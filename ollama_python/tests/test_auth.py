from datetime import timedelta

from sqlalchemy import select, update

from conftest import PASSWORD, H, login, make_user, register, token_from, verify_last
from elaw_core.db import session_scope, utcnow
from elaw_core.models import (
    EmailVerificationToken, LoginEvent, PasswordResetToken, Session, Status, User,
)


# ---------------- REGISTRATION ----------------
def test_register_success_creates_pending_user_and_sends_email(client):
    r = register(client, email="  Alice@Example.COM ")
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "alice@example.com"  # normalized
    assert body["user"]["status"] == "PENDING" and body["user"]["email_verified"] is False
    assert "password" not in str(body).lower().replace("check your email", "")
    assert len(client.outbox.outbox) == 1
    with session_scope() as db:
        user = db.scalar(select(User))
        assert user.password_hash.startswith("$argon2id$")
        assert PASSWORD not in user.password_hash
        tok = db.scalar(select(EmailVerificationToken))
        raw = token_from(client.outbox.outbox[0])
        assert tok.token_hash != raw and len(tok.token_hash) == 64  # stored hashed only


def test_register_duplicate_email(client):
    register(client)
    r = register(client, email="ALICE@example.com")
    assert r.status_code == 409


def test_register_invalid_email(client):
    r = register(client, email="not-an-email")
    assert r.status_code == 400 and "email" in r.json()["detail"].lower()


def test_register_weak_password(client):
    for pw in ("short1", "allletterslong", "password123"):
        r = register(client, password=pw)
        assert r.status_code == 400, pw


def test_register_password_mismatch(client):
    r = client.post("/api/auth/register", headers=H(client), json={
        "name": "A", "email": "a@example.com", "password": PASSWORD, "confirm_password": "x"})
    assert r.status_code == 400


# ---------------- EMAIL VERIFICATION ----------------
def test_verify_valid_token_activates(client):
    register(client)
    assert verify_last(client).status_code == 200
    with session_scope() as db:
        u = db.scalar(select(User))
        assert u.email_verified and u.status == Status.ACTIVE


def test_verify_invalid_token(client):
    r = client.post("/api/auth/verify-email", headers=H(client), json={"token": "nope"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_token"


def test_verify_expired_token(client):
    register(client)
    with session_scope() as db:
        db.execute(update(EmailVerificationToken).values(expires_at=utcnow() - timedelta(minutes=1)))
    r = verify_last(client)
    assert r.json()["code"] == "expired_token"


def test_verify_reused_token(client):
    register(client)
    assert verify_last(client).status_code == 200
    r = verify_last(client)
    assert r.json()["code"] == "used_token"


def test_verify_link_page_redirects_to_login(client):
    register(client)
    token = token_from(client.outbox.outbox[-1])
    r = client.get(f"/verify-email?token={token}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?notice=verified"
    r = client.get(f"/verify-email?token={token}")
    assert "already been used" in r.text


def test_resend_verification_invalidates_old_link(client):
    register(client)
    old = token_from(client.outbox.outbox[-1])
    r = client.post("/api/auth/resend-verification", headers=H(client),
                    json={"email": "alice@example.com"})
    assert r.status_code == 200 and len(client.outbox.outbox) == 2
    r = client.post("/api/auth/verify-email", headers=H(client), json={"token": old})
    assert r.json()["code"] == "used_token"
    assert verify_last(client).status_code == 200


def test_resend_verification_unknown_email_looks_the_same(client):
    r = client.post("/api/auth/resend-verification", headers=H(client),
                    json={"email": "ghost@example.com"})
    assert r.status_code == 200 and client.outbox.outbox == []


def test_resend_verification_rate_limited(client):
    register(client)
    codes = [client.post("/api/auth/resend-verification", headers=H(client),
                         json={"email": "alice@example.com"}).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


# ---------------- LOGIN ----------------
def test_login_success_sets_httponly_cookie_and_records_event(client):
    make_user(client)
    r = login(client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "elaw_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    raw = r.cookies.get("elaw_session")
    assert raw not in r.text  # token only in the cookie, never in the body
    assert client.get("/api/auth/me").json()["user"]["email"] == "alice@example.com"
    with session_scope() as db:
        s = db.scalar(select(Session))
        assert s.token_hash != raw and len(s.token_hash) == 64
        e = db.scalar(select(LoginEvent).where(LoginEvent.login_success.is_(True)))
        assert e.email == "alice@example.com" and e.device_info


def test_login_wrong_password(client):
    make_user(client)
    r = login(client, password="Wrong-password-1")
    assert r.status_code == 401 and r.json()["code"] == "invalid_login"
    with session_scope() as db:
        e = db.scalar(select(LoginEvent))
        assert e.login_success is False and e.failure_reason == "wrong_password"


def test_login_unknown_email_same_message(client):
    make_user(client)
    a = login(client, password="Wrong-password-1").json()["detail"]
    b = login(client, email="nobody@example.com").json()["detail"]
    assert a == b


def test_login_unverified(client):
    register(client)
    r = login(client)
    assert r.status_code == 403 and r.json()["code"] == "unverified"


def test_login_suspended_and_disabled(client):
    make_user(client)
    for status, code in ((Status.SUSPENDED, "suspended"), (Status.DISABLED, "disabled")):
        with session_scope() as db:
            db.execute(update(User).values(status=status))
        r = login(client)
        assert r.status_code == 403 and r.json()["code"] == code


def test_session_expiration(client):
    make_user(client)
    login(client)
    with session_scope() as db:
        db.execute(update(Session).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert client.get("/api/auth/me").status_code == 401


def test_logout_revokes_session(client):
    make_user(client)
    login(client)
    raw = client.cookies.get("elaw_session")
    assert client.post("/api/auth/logout", headers=H(client)).status_code == 200
    client.cookies.set("elaw_session", raw)  # even if the old cookie is replayed
    assert client.get("/api/auth/me").status_code == 401


def test_refresh_rotates_session(client):
    make_user(client)
    login(client)
    old = client.cookies.get("elaw_session")
    r = client.post("/api/auth/refresh", headers=H(client))
    new = r.cookies.get("elaw_session")
    assert r.status_code == 200 and new and new != old
    client.cookies.set("elaw_session", old)
    assert client.get("/api/auth/me").status_code == 401


def test_brute_force_lockout(client):
    make_user(client)
    for _ in range(5):
        assert login(client, password="Wrong-password-1").status_code == 401
    r = login(client)  # even the right password is refused while locked
    assert r.status_code == 429 and r.json()["code"] == "locked"


def test_login_ip_rate_limit(client):
    codes = [login(client, email=f"u{i}@example.com").status_code for i in range(21)]
    assert codes[-1] == 429


# ---------------- PASSWORD RESET ----------------
def _forgot(client, email="alice@example.com"):
    return client.post("/api/auth/forgot-password", headers=H(client), json={"email": email})


def test_password_reset_valid_token(client):
    make_user(client)
    login(client)
    assert _forgot(client).status_code == 200
    token = token_from(client.outbox.outbox[-1])
    r = client.post("/api/auth/reset-password", headers=H(client), json={
        "token": token, "password": "Brand-new-pass-7", "confirm_password": "Brand-new-pass-7"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 401  # all sessions revoked
    assert login(client).status_code == 401
    assert login(client, password="Brand-new-pass-7").status_code == 200
    with session_scope() as db:
        assert db.scalar(select(PasswordResetToken)).used_at is not None


def test_password_reset_expired_token(client):
    make_user(client)
    _forgot(client)
    with session_scope() as db:
        db.execute(update(PasswordResetToken).values(expires_at=utcnow() - timedelta(minutes=1)))
    token = token_from(client.outbox.outbox[-1])
    r = client.post("/api/auth/reset-password", headers=H(client), json={
        "token": token, "password": "Brand-new-pass-7", "confirm_password": "Brand-new-pass-7"})
    assert r.json()["code"] == "expired_token"
    assert "expired" in client.get(f"/reset-password?token={token}").text.lower()


def test_password_reset_reused_token(client):
    make_user(client)
    _forgot(client)
    token = token_from(client.outbox.outbox[-1])
    body = {"token": token, "password": "Brand-new-pass-7", "confirm_password": "Brand-new-pass-7"}
    assert client.post("/api/auth/reset-password", headers=H(client), json=body).status_code == 200
    r = client.post("/api/auth/reset-password", headers=H(client), json=body)
    assert r.json()["code"] == "used_token"


def test_forgot_password_unknown_email_and_rate_limit(client):
    make_user(client)
    assert _forgot(client, "ghost@example.com").status_code == 200
    codes = [_forgot(client).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


# ---------------- HTML PAGES + CSRF ----------------
def test_html_login_flow_and_csrf(client):
    make_user(client)
    page = client.get("/login")
    assert page.status_code == 200 and 'name="csrf_token"' in page.text
    r = client.post("/login", data={"email": "alice@example.com", "password": PASSWORD},
                    follow_redirects=False)
    assert r.status_code == 403  # missing CSRF token
    r = client.post("/login", data={"email": "alice@example.com", "password": PASSWORD,
                                    "csrf_token": client.csrf, "next": "//evil.com"},
                    follow_redirects=False)
    assert r.status_code == 303  # open redirect blocked: always our own app
    assert r.headers["location"] == "/"


def test_cross_site_origin_blocked(client):
    r = client.post("/api/auth/login", headers={**H(client), "Origin": "https://evil.example"},
                    json={"email": "a@example.com", "password": "x"})
    assert r.status_code == 403


def test_null_origin_form_post_uses_csrf_token(client):
    """Browsers can send 'Origin: null' on same-site forms: the token decides, not a 403."""
    make_user(client)
    r = client.post("/login", headers={"Origin": "null"}, follow_redirects=False,
                    data={"email": "alice@example.com", "password": PASSWORD,
                          "csrf_token": client.csrf})
    assert r.status_code == 303
    r = client.post("/login", headers={"Origin": "null"},
                    data={"email": "alice@example.com", "password": PASSWORD})
    assert r.status_code == 403  # still blocked without the CSRF token


def test_security_headers(client):
    h = client.get("/login").headers
    assert h["x-frame-options"] == "DENY" and h["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_html_escapes_user_input(client):
    r = client.post("/register", data={"name": "<script>x</script>", "email": "<b>@x",
                                       "password": "a", "confirm_password": "a",
                                       "csrf_token": client.csrf})
    assert "<script>x</script>" not in r.text and "&lt;b&gt;" in r.text


def test_redirect_and_links_follow_the_domain_setup(client, monkeypatch):
    """One domain (production): relative, so ai. and llm. both work. Split dev setup: absolute."""
    import streamlit_auth
    from elaw_core import config
    assert config.settings.same_origin
    assert streamlit_auth.auth_url("/login") == "/login" and streamlit_auth.app_url("/x") == "/x"
    monkeypatch.setenv("PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setenv("APP_URL", "http://localhost:8501")
    config.reload_settings()
    make_user(client)
    r = client.post("/login", data={"email": "alice@example.com", "password": PASSWORD,
                                    "csrf_token": client.csrf, "next": "/admin"},
                    follow_redirects=False)
    assert r.headers["location"] == "http://localhost:8501/admin"
    assert streamlit_auth.auth_url("/login") == "http://localhost:8000/login"
