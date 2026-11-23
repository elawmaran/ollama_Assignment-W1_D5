"""Registration, email verification, login, sessions and password reset."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update

from elaw_core import config, security
from elaw_core.db import as_utc, utcnow
from elaw_core.email import EmailError, email_service
from elaw_core.models import (
    EmailVerificationToken, LoginEvent, PasswordResetToken, Session, Status, User,
)

log = logging.getLogger("elaw.auth")


class AuthError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ClientInfo:
    ip: str | None = None
    user_agent: str | None = None


MESSAGES = {
    "invalid_login": "Incorrect email or password.",
    "unverified": "Please verify your email first. We can send you a new link.",
    "suspended": "Your account is suspended. Please contact support.",
    "disabled": "This account has been disabled.",
    "locked": "Too many failed attempts. Please wait a few minutes and try again.",
    "rate_limited": "Too many requests. Please wait a moment and try again.",
    "invalid_token": "This link is invalid. Please request a new one.",
    "expired_token": "This link has expired. Please request a new one.",
    "used_token": "This link has already been used.",
}


def _err(code):
    return AuthError(code, MESSAGES[code])


# ---------------- TOKENS ----------------
def _create_token(db, model, user, minutes):
    # Only one live token per user: older unused ones stop working.
    db.execute(update(model).where(model.user_id == user.id, model.used_at.is_(None))
               .values(used_at=utcnow()))
    raw, digest = security.new_token()
    db.add(model(user_id=user.id, token_hash=digest,
                 expires_at=utcnow() + timedelta(minutes=minutes)))
    db.flush()
    return raw


def _consume_token(db, model, raw, consume=True):
    if not raw or len(raw) > 200:
        raise _err("invalid_token")
    token = db.scalar(select(model).where(model.token_hash == security.hash_token(raw)))
    if token is None:
        raise _err("invalid_token")
    if token.used_at is not None:
        raise _err("used_token")
    if as_utc(token.expires_at) <= utcnow():
        raise _err("expired_token")
    if consume:
        token.used_at = utcnow()
    return token


def send_verification_email(db, user):
    s = config.settings
    raw = _create_token(db, EmailVerificationToken, user, s.verify_token_minutes)
    link = f"{s.public_url}/verify-email?token={raw}"
    try:
        email_service.send_verification(user.email, user.name, link, s.verify_token_minutes)
        return True
    except EmailError:
        return False


def send_reset_email(db, user):
    s = config.settings
    raw = _create_token(db, PasswordResetToken, user, s.reset_token_minutes)
    link = f"{s.public_url}/reset-password?token={raw}"
    try:
        email_service.send_password_reset(user.email, user.name, link, s.reset_token_minutes)
        return True
    except EmailError:
        return False


# ---------------- REGISTRATION ----------------
def register(db, name, email, password, confirm, client=None):
    client = client or ClientInfo()
    if client.ip and not security.allow("register_ip", client.ip):
        raise _err("rate_limited")
    email = security.normalize_email(email)
    name = (name or "").strip()
    problem = (security.validate_name(name) or security.validate_email(email)
               or security.validate_password(password, email))
    if problem:
        raise AuthError("invalid_input", problem)
    if password != confirm:
        raise AuthError("invalid_input", "Passwords don't match.")
    if db.scalar(select(User.id).where(User.email == email)):
        raise AuthError("duplicate_email", "An account with this email already exists. "
                        "Try logging in or resetting your password.")
    user = User(name=name, email=email, password_hash=security.hash_password(password),
                email_verified=False, status=Status.PENDING)
    db.add(user)
    db.flush()
    sent = send_verification_email(db, user)
    return user, sent


def verify_email(db, raw, client=None):
    client = client or ClientInfo()
    if client.ip and not security.allow("verify_ip", client.ip):
        raise _err("rate_limited")
    token = _consume_token(db, EmailVerificationToken, raw)
    user = db.get(User, token.user_id)
    user.email_verified = True
    if user.status == Status.PENDING:
        user.status = Status.ACTIVE
    return user


def resend_verification(db, email, client=None):
    """Always looks the same to the caller, so it can't be used to find accounts."""
    client = client or ClientInfo()
    email = security.normalize_email(email)
    if client.ip and not security.allow("resend_ip", client.ip):
        raise _err("rate_limited")
    if not security.allow("resend_email", email):
        raise _err("rate_limited")
    user = db.scalar(select(User).where(User.email == email))
    if user and not user.email_verified and user.status in (Status.PENDING, Status.ACTIVE):
        send_verification_email(db, user)


# ---------------- LOGIN / SESSIONS ----------------
def _record_login(db, email, user, success, client, reason=None):
    db.add(LoginEvent(user_id=user.id if user else None, email=email[:254],
                      login_success=success, ip_address=client.ip,
                      user_agent=(client.user_agent or "")[:300] or None,
                      device_info=security.device_info(client.user_agent),
                      failure_reason=reason))


def _fail(db, email, user, client, reason, code):
    """Save the failed attempt even though the caller's transaction will roll back."""
    _record_login(db, email, user, False, client, reason)
    db.commit()
    return _err(code)


def _recent_failures(db, email):
    s = config.settings
    since = utcnow() - timedelta(minutes=s.lockout_minutes)
    last_ok = db.scalar(select(func.max(LoginEvent.created_at)).where(
        LoginEvent.email == email, LoginEvent.login_success.is_(True)))
    if last_ok is not None and as_utc(last_ok) > since:
        since = as_utc(last_ok)
    return db.scalar(select(func.count(LoginEvent.id)).where(
        LoginEvent.email == email, LoginEvent.login_success.is_(False),
        LoginEvent.failure_reason.in_(("wrong_password", "unknown_email")),
        LoginEvent.created_at > since)) or 0


def create_session(db, user, client):
    raw, digest = security.new_token()
    db.add(Session(user_id=user.id, token_hash=digest, ip_address=client.ip,
                   user_agent=(client.user_agent or "")[:300] or None,
                   expires_at=utcnow() + timedelta(days=config.settings.session_days)))
    db.flush()
    return raw


def login(db, email, password, client=None):
    """Return (user, raw_session_token) or raise AuthError."""
    client = client or ClientInfo()
    email = security.normalize_email(email)[:254]
    if client.ip and not security.allow("login_ip", client.ip):
        raise _fail(db, email, None, client, "rate_limited", "rate_limited")
    if _recent_failures(db, email) >= config.settings.max_failed_logins:
        security.burn_password_check(password)
        raise _fail(db, email, None, client, "locked", "locked")

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        security.burn_password_check(password)
        raise _fail(db, email, None, client, "unknown_email", "invalid_login")
    if not security.verify_password(user.password_hash, password):
        raise _fail(db, email, user, client, "wrong_password", "invalid_login")
    # Password is right: now it is safe to say why the account can't log in.
    if user.status == Status.DISABLED:
        raise _fail(db, email, user, client, "disabled", "disabled")
    if user.status == Status.SUSPENDED:
        raise _fail(db, email, user, client, "suspended", "suspended")
    if not user.email_verified:
        raise _fail(db, email, user, client, "unverified", "unverified")

    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
    if user.status == Status.PENDING:
        user.status = Status.ACTIVE
    now = utcnow()
    user.last_login_at = user.last_activity_at = now
    _record_login(db, email, user, True, client)
    return user, create_session(db, user, client)


def get_session(db, raw):
    if not raw or len(raw) > 200:
        return None
    session = db.scalar(select(Session).where(Session.token_hash == security.hash_token(raw)))
    if session is None or session.revoked_at is not None:
        return None
    if as_utc(session.expires_at) <= utcnow():
        return None
    return session


def current_user(db, raw, touch=True):
    """Return the logged-in ACTIVE user for a session token, or None."""
    session = get_session(db, raw)
    if session is None:
        return None
    user = db.get(User, session.user_id)
    if user is None or user.status != Status.ACTIVE or not user.email_verified:
        return None
    if touch:
        now = utcnow()
        if now - as_utc(session.last_seen_at) > timedelta(seconds=60):
            session.last_seen_at = user.last_activity_at = now
    return user


def logout(db, raw):
    session = get_session(db, raw)
    if session is not None:
        session.revoked_at = utcnow()


def refresh_session(db, raw, client=None):
    """Rotate the session token and extend its lifetime."""
    client = client or ClientInfo()
    user = current_user(db, raw, touch=False)
    if user is None:
        return None, None
    get_session(db, raw).revoked_at = utcnow()
    return user, create_session(db, user, client)


def revoke_all_sessions(db, user_id):
    result = db.execute(update(Session).where(Session.user_id == user_id,
                                              Session.revoked_at.is_(None))
                        .values(revoked_at=utcnow()))
    return result.rowcount or 0


# ---------------- PASSWORD RESET ----------------
def forgot_password(db, email, client=None):
    """Always looks the same to the caller, so it can't be used to find accounts."""
    client = client or ClientInfo()
    email = security.normalize_email(email)
    if client.ip and not security.allow("forgot_ip", client.ip):
        raise _err("rate_limited")
    if not security.allow("forgot_email", email):
        raise _err("rate_limited")
    user = db.scalar(select(User).where(User.email == email))
    if user and user.status in (Status.ACTIVE, Status.PENDING):
        send_reset_email(db, user)


def check_reset_token(db, raw):
    _consume_token(db, PasswordResetToken, raw, consume=False)


def reset_password(db, raw, password, confirm, client=None):
    client = client or ClientInfo()
    if client.ip and not security.allow("reset_ip", client.ip):
        raise _err("rate_limited")
    token = _consume_token(db, PasswordResetToken, raw, consume=False)
    user = db.get(User, token.user_id)
    problem = security.validate_password(password, user.email)
    if problem:
        raise AuthError("invalid_input", problem)
    if password != confirm:
        raise AuthError("invalid_input", "Passwords don't match.")
    if user.status in (Status.SUSPENDED, Status.DISABLED):
        raise _err(user.status.value.lower())
    token.used_at = utcnow()
    user.password_hash = security.hash_password(password)
    # Following the emailed link proves the user owns the address.
    user.email_verified = True
    if user.status == Status.PENDING:
        user.status = Status.ACTIVE
    revoke_all_sessions(db, user.id)  # log out everywhere after a reset
    return user
