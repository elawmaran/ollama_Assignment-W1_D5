"""ELAW AI auth service (FastAPI).

Serves the auth pages (/login, /register, /verify-email, /forgot-password,
/reset-password, /logout) and the JSON APIs (/api/auth/*, /api/admin/*).
Sessions are opaque random tokens in an HTTP-only cookie; the database keeps
only their SHA-256 hash. Run:  uvicorn auth_api.main:app --no-access-log
"""

import logging
import secrets
import time
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from elaw_core import admin as admin_svc
from elaw_core import auth, config
from elaw_core.db import get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("elaw.http")
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")

app = FastAPI(title="ELAW AI Auth", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/auth-assets", StaticFiles(directory=HERE / "static"), name="auth-assets")

if config.settings.allowed_origins:
    app.add_middleware(CORSMiddleware, allow_origins=config.settings.allowed_origins,
                       allow_credentials=True, allow_methods=["GET", "POST", "PATCH"],
                       allow_headers=["Content-Type", "X-CSRF-Token"])

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    started = time.perf_counter()
    s = config.settings
    # Reject cross-site writes outright (CSRF defence in depth, next to the token).
    if request.method in UNSAFE:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in _allowed_origins(request):
            return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)

    response = await call_next(request)

    h = response.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "no-referrer"  # tokens in URLs must never leak via Referer
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    h["Content-Security-Policy"] = ("default-src 'self'; style-src 'self'; script-src 'self'; "
                                    "img-src 'self' data:; form-action 'self'; "
                                    "frame-ancestors 'none'; base-uri 'none'")
    h["Cache-Control"] = "no-store"
    if s.secure_cookies:
        h["Strict-Transport-Security"] = "max-age=31536000"
    if not request.cookies.get(s.csrf_cookie):
        _set_csrf_cookie(response, getattr(request.state, "csrf", None) or secrets.token_urlsafe(24))
    # Path only, never the query string: it can contain verification/reset tokens.
    log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code,
             (time.perf_counter() - started) * 1000)
    return response


def _allowed_origins(request):
    own = f"{request.url.scheme}://{request.url.netloc}"
    s = config.settings
    return {own, s.public_url, s.app_url, *s.allowed_origins}


def _set_csrf_cookie(response, value):
    response.set_cookie(config.settings.csrf_cookie, value, httponly=False, samesite="strict",
                        secure=config.settings.secure_cookies, path="/")


def csrf_token(request: Request):
    """Double-submit token: cookie value echoed in a form field or X-CSRF-Token header."""
    token = request.cookies.get(config.settings.csrf_cookie)
    if not token:
        token = getattr(request.state, "csrf", None) or secrets.token_urlsafe(24)
        request.state.csrf = token
    return token


async def require_csrf(request: Request):
    cookie = request.cookies.get(config.settings.csrf_cookie)
    sent = request.headers.get("x-csrf-token")
    if sent is None and "form" in request.headers.get("content-type", ""):
        sent = (await request.form()).get("csrf_token")
    if not cookie or not sent or not secrets.compare_digest(cookie, str(sent)):
        raise HTTPException(403, "Invalid or missing CSRF token. Reload the page and try again.")


def client_info(request: Request):
    ip = request.client.host if request.client else None
    if config.settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:  # Caddy appends the real client address last
            ip = forwarded.split(",")[-1].strip()
    return auth.ClientInfo(ip=(ip or "")[:64] or None,
                           user_agent=request.headers.get("user-agent", "")[:300])


def _set_session(response, raw):
    s = config.settings
    response.set_cookie(s.session_cookie, raw, httponly=True, secure=s.secure_cookies,
                        samesite="lax", max_age=s.session_days * 86400, path="/")


def _clear_session(response):
    response.delete_cookie(config.settings.session_cookie, path="/")


def optional_user(request: Request, db=Depends(get_db)):
    return auth.current_user(db, request.cookies.get(config.settings.session_cookie))


def require_user(user=Depends(optional_user)):
    if user is None:
        raise HTTPException(401, "Not logged in.")
    return user


def require_admin(user=Depends(require_user)):
    if not admin_svc.is_admin(user):
        raise HTTPException(403, "Admin access required.")
    return user


STATUS_FOR = {"rate_limited": 429, "locked": 429, "invalid_login": 401, "unverified": 403,
              "suspended": 403, "disabled": 403, "duplicate_email": 409}


def _api_error(error: auth.AuthError):
    return JSONResponse({"detail": error.message, "code": error.code},
                        status_code=STATUS_FOR.get(error.code, 400))


def _me(user):
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role.value,
            "status": user.status.value, "email_verified": user.email_verified,
            "plan": user.plan.value}


# ======================= JSON AUTH API =======================
class RegisterIn(BaseModel):
    name: str = Field(max_length=100)
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    confirm_password: str = Field(max_length=128)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class EmailIn(BaseModel):
    email: str = Field(max_length=254)


class TokenIn(BaseModel):
    token: str = Field(max_length=200)


class ResetIn(TokenIn):
    password: str = Field(max_length=128)
    confirm_password: str = Field(max_length=128)


GENERIC_SENT = "If an account matches, we've sent an email. Check your inbox and spam folder."


@app.get("/api/auth/csrf")
def api_csrf(request: Request):
    token = csrf_token(request)
    response = JSONResponse({"csrf_token": token})
    _set_csrf_cookie(response, token)
    return response


@app.post("/api/auth/register", dependencies=[Depends(require_csrf)], status_code=201)
def api_register(body: RegisterIn, request: Request, db=Depends(get_db)):
    try:
        user, sent = auth.register(db, body.name, body.email, body.password,
                                   body.confirm_password, client_info(request))
    except auth.AuthError as e:
        return _api_error(e)
    return JSONResponse({"detail": "Registration successful. Check your email to verify your "
                         "account.", "verification_email_sent": sent, "user": _me(user)},
                        status_code=201)


@app.post("/api/auth/login", dependencies=[Depends(require_csrf)])
def api_login(body: LoginIn, request: Request, db=Depends(get_db)):
    try:
        user, raw = auth.login(db, body.email, body.password, client_info(request))
    except auth.AuthError as e:
        return _api_error(e)
    response = JSONResponse({"user": _me(user)})
    _set_session(response, raw)
    return response


@app.post("/api/auth/logout", dependencies=[Depends(require_csrf)])
def api_logout(request: Request, db=Depends(get_db)):
    auth.logout(db, request.cookies.get(config.settings.session_cookie))
    response = JSONResponse({"detail": "Logged out."})
    _clear_session(response)
    return response


@app.get("/api/auth/me")
def api_me(user=Depends(require_user)):
    return {"user": _me(user)}


@app.post("/api/auth/verify-email", dependencies=[Depends(require_csrf)])
def api_verify(body: TokenIn, request: Request, db=Depends(get_db)):
    try:
        auth.verify_email(db, body.token, client_info(request))
    except auth.AuthError as e:
        return _api_error(e)
    return {"detail": "Email verified. You can log in now."}


@app.post("/api/auth/resend-verification", dependencies=[Depends(require_csrf)])
def api_resend(body: EmailIn, request: Request, db=Depends(get_db)):
    try:
        auth.resend_verification(db, body.email, client_info(request))
    except auth.AuthError as e:
        return _api_error(e)
    return {"detail": GENERIC_SENT}


@app.post("/api/auth/forgot-password", dependencies=[Depends(require_csrf)])
def api_forgot(body: EmailIn, request: Request, db=Depends(get_db)):
    try:
        auth.forgot_password(db, body.email, client_info(request))
    except auth.AuthError as e:
        return _api_error(e)
    return {"detail": GENERIC_SENT}


@app.post("/api/auth/reset-password", dependencies=[Depends(require_csrf)])
def api_reset(body: ResetIn, request: Request, db=Depends(get_db)):
    try:
        auth.reset_password(db, body.token, body.password, body.confirm_password,
                            client_info(request))
    except auth.AuthError as e:
        return _api_error(e)
    return {"detail": "Password updated. Please log in with your new password."}


@app.post("/api/auth/refresh", dependencies=[Depends(require_csrf)])
def api_refresh(request: Request, db=Depends(get_db)):
    user, raw = auth.refresh_session(db, request.cookies.get(config.settings.session_cookie),
                                     client_info(request))
    if user is None:
        raise HTTPException(401, "Not logged in.")
    response = JSONResponse({"user": _me(user)})
    _set_session(response, raw)
    return response


# ======================= ADMIN API =======================
def _admin_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except admin_svc.PermissionDenied as e:
        raise HTTPException(403, str(e))
    except admin_svc.NotFound as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/admin/overview")
def api_admin_overview(actor=Depends(require_admin), db=Depends(get_db)):
    return admin_svc.overview(db, actor)


@app.get("/api/admin/users")
def api_admin_users(
    actor=Depends(require_admin), db=Depends(get_db),
    q: str | None = Query(None, max_length=100),
    status: Literal["PENDING", "ACTIVE", "SUSPENDED", "DISABLED"] | None = None,
    verified: bool | None = None,
    role: Literal["USER", "ADMIN", "SUPER_ADMIN"] | None = None,
    registered_from: date | None = None, registered_to: date | None = None,
    sort: Literal["created_at", "last_login_at", "name", "email"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
):
    return admin_svc.list_users(db, actor, q, status, verified, role, registered_from,
                                registered_to, sort, order, page, page_size)


@app.get("/api/admin/users/{user_id}")
def api_admin_user(user_id: int, actor=Depends(require_admin), db=Depends(get_db)):
    return _admin_call(admin_svc.user_detail, db, actor, user_id)


class StatusIn(BaseModel):
    status: Literal["SUSPENDED", "ACTIVE", "DISABLED", "RESTORED"]
    reason: str = Field("", max_length=500)
    confirm: bool = False


class RoleIn(BaseModel):
    role: Literal["USER", "ADMIN"]
    confirm: bool = False


def _need_confirm(body):
    if not body.confirm:
        raise HTTPException(400, "This is a sensitive action. Resend with \"confirm\": true.")


@app.patch("/api/admin/users/{user_id}/status", dependencies=[Depends(require_csrf)])
def api_admin_status(user_id: int, body: StatusIn, request: Request,
                     actor=Depends(require_admin), db=Depends(get_db)):
    _need_confirm(body)
    return _admin_call(admin_svc.set_status, db, actor, user_id, body.status, body.reason,
                       client_info(request).ip)


@app.patch("/api/admin/users/{user_id}/role", dependencies=[Depends(require_csrf)])
def api_admin_role(user_id: int, body: RoleIn, request: Request,
                   actor=Depends(require_admin), db=Depends(get_db)):
    _need_confirm(body)
    return _admin_call(admin_svc.set_role, db, actor, user_id, body.role,
                       client_info(request).ip)


@app.post("/api/admin/users/{user_id}/resend-verification", dependencies=[Depends(require_csrf)])
def api_admin_resend(user_id: int, request: Request, actor=Depends(require_admin),
                     db=Depends(get_db)):
    sent = _admin_call(admin_svc.resend_verification, db, actor, user_id, client_info(request).ip)
    return {"sent": sent}


@app.post("/api/admin/users/{user_id}/password-reset", dependencies=[Depends(require_csrf)])
def api_admin_reset(user_id: int, request: Request, actor=Depends(require_admin),
                    db=Depends(get_db)):
    sent = _admin_call(admin_svc.trigger_password_reset, db, actor, user_id,
                       client_info(request).ip)
    return {"sent": sent}


@app.post("/api/admin/users/{user_id}/force-logout", dependencies=[Depends(require_csrf)])
def api_admin_force_logout(user_id: int, request: Request, actor=Depends(require_admin),
                           db=Depends(get_db)):
    count = _admin_call(admin_svc.force_logout, db, actor, user_id, client_info(request).ip)
    return {"revoked_sessions": count}


@app.get("/api/admin/login-events")
def api_admin_login_events(
    actor=Depends(require_admin), db=Depends(get_db), success: bool | None = None,
    user_id: int | None = None, email: str | None = Query(None, max_length=100),
    ip: str | None = Query(None, max_length=64), date_from: date | None = None,
    date_to: date | None = None, page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    return admin_svc.login_events(db, actor, success, user_id, email, ip, date_from, date_to,
                                  page, page_size)


@app.get("/api/admin/audit-logs")
def api_admin_audit(actor=Depends(require_admin), db=Depends(get_db),
                    action: str | None = Query(None, max_length=50),
                    target_user_id: int | None = None, page: int = Query(1, ge=1),
                    page_size: int = Query(50, ge=1, le=100)):
    return admin_svc.audit_logs(db, actor, action, target_user_id, page, page_size)


@app.get("/api/health")
def health():
    return {"ok": True}


# ======================= HTML PAGES =======================
NOTICES = {  # fixed texts only: nothing from the URL is ever echoed into the page
    "verified": ("success", "Email verified. You can log in now."),
    "registered": ("success", "Registration successful. We've sent a verification link to "
                   "your email. It expires in 30 minutes."),
    "reset_done": ("success", "Password updated. Please log in with your new password."),
    "logged_out": ("info", "You've been logged out."),
    "sent": ("info", GENERIC_SENT),
    "session_expired": ("info", "Please log in to continue."),
}


def page(request, template, status_code=200, **ctx):
    notice = NOTICES.get(request.query_params.get("notice", ""))
    return templates.TemplateResponse(
        request, template, {"csrf": csrf_token(request), "notice": notice, **ctx},
        status_code=status_code)


def _safe_next(value):
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value[:200]
    return "/"


def _redirect(url):
    return RedirectResponse(url, status_code=303)


def _to_app(path):
    """Into the chat app (a fixed, configured origin, so no open redirect)."""
    return _redirect(f"{config.settings.app_url}{_safe_next(path)}")


@app.get("/")
def root():
    return _to_app("/")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", user=Depends(optional_user)):
    if user is not None:
        return _to_app(next)
    return page(request, "login.html", next=_safe_next(next))


@app.post("/login", dependencies=[Depends(require_csrf)])
async def login_submit(request: Request, db=Depends(get_db)):
    form = await request.form()
    email, nxt = str(form.get("email", ""))[:254], _safe_next(str(form.get("next", "/")))
    try:
        _, raw = auth.login(db, email, str(form.get("password", ""))[:128], client_info(request))
    except auth.AuthError as e:
        return page(request, "login.html", 401 if e.code == "invalid_login" else 400,
                    error=e.message, error_code=e.code, email=email, next=nxt)
    response = _to_app(nxt)
    _set_session(response, raw)
    return response


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return page(request, "register.html")


@app.post("/register", dependencies=[Depends(require_csrf)])
async def register_submit(request: Request, db=Depends(get_db)):
    form = await request.form()
    name, email = str(form.get("name", ""))[:100], str(form.get("email", ""))[:254]
    try:
        _, sent = auth.register(db, name, email, str(form.get("password", ""))[:128],
                                str(form.get("confirm_password", ""))[:128], client_info(request))
    except auth.AuthError as e:
        return page(request, "register.html", 400, error=e.message, name=name, email=email)
    if not sent:
        return page(request, "message.html", title="Account created",
                    kind="info", text="Your account was created, but we couldn't send the "
                    "verification email right now. Please use “Resend verification email” "
                    "in a few minutes.", link=("/resend-verification", "Resend verification email"))
    return _redirect("/login?notice=registered")


@app.get("/verify-email", response_class=HTMLResponse)
def verify_page(request: Request, token: str = Query("", max_length=200), db=Depends(get_db)):
    try:
        auth.verify_email(db, token, client_info(request))
    except auth.AuthError as e:
        titles = {"expired_token": "Link expired", "used_token": "Link already used"}
        return page(request, "message.html", 400, title=titles.get(e.code, "Invalid link"),
                    kind="error", text=e.message,
                    link=("/resend-verification", "Resend verification email"))
    return _redirect("/login?notice=verified")


@app.get("/resend-verification", response_class=HTMLResponse)
def resend_page(request: Request):
    return page(request, "email_form.html", title="Resend verification email",
                action="/resend-verification", button="Send new link",
                intro="Enter your email and we'll send a new verification link.")


@app.post("/resend-verification", dependencies=[Depends(require_csrf)])
async def resend_submit(request: Request, db=Depends(get_db)):
    form = await request.form()
    try:
        auth.resend_verification(db, str(form.get("email", ""))[:254], client_info(request))
    except auth.AuthError as e:
        return page(request, "email_form.html", 429, title="Resend verification email",
                    action="/resend-verification", button="Send new link", error=e.message)
    return page(request, "message.html", title="Verification email sent", kind="success",
                text=GENERIC_SENT, link=("/login", "Back to log in"))


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_page(request: Request):
    return page(request, "email_form.html", title="Forgot your password?",
                action="/forgot-password", button="Send reset link",
                intro="Enter your email and we'll send you a link to choose a new password.")


@app.post("/forgot-password", dependencies=[Depends(require_csrf)])
async def forgot_submit(request: Request, db=Depends(get_db)):
    form = await request.form()
    try:
        auth.forgot_password(db, str(form.get("email", ""))[:254], client_info(request))
    except auth.AuthError as e:
        return page(request, "email_form.html", 429, title="Forgot your password?",
                    action="/forgot-password", button="Send reset link", error=e.message)
    return page(request, "message.html", title="Password reset email sent", kind="success",
                text=GENERIC_SENT, link=("/login", "Back to log in"))


@app.get("/reset-password", response_class=HTMLResponse)
def reset_page(request: Request, token: str = Query("", max_length=200), db=Depends(get_db)):
    try:
        auth.check_reset_token(db, token)
    except auth.AuthError as e:
        titles = {"expired_token": "Reset link expired", "used_token": "Reset link already used"}
        return page(request, "message.html", 400, title=titles.get(e.code, "Invalid reset link"),
                    kind="error", text=e.message, link=("/forgot-password", "Request a new link"))
    return page(request, "reset.html", token=token)


@app.post("/reset-password", dependencies=[Depends(require_csrf)])
async def reset_submit(request: Request, db=Depends(get_db)):
    form = await request.form()
    token = str(form.get("token", ""))[:200]
    try:
        auth.reset_password(db, token, str(form.get("password", ""))[:128],
                            str(form.get("confirm_password", ""))[:128], client_info(request))
    except auth.AuthError as e:
        if e.code == "invalid_input":
            return page(request, "reset.html", 400, token=token, error=e.message)
        return page(request, "message.html", 400, title="Reset link problem", kind="error",
                    text=e.message, link=("/forgot-password", "Request a new link"))
    response = _redirect("/login?notice=reset_done")
    _clear_session(response)
    return response


@app.get("/logout", response_class=HTMLResponse)
def logout_page(request: Request):
    return page(request, "logout.html")


@app.post("/logout", dependencies=[Depends(require_csrf)])
def logout_submit(request: Request, db=Depends(get_db)):
    auth.logout(db, request.cookies.get(config.settings.session_cookie))
    response = _redirect("/login?notice=logged_out")
    _clear_session(response)
    return response
