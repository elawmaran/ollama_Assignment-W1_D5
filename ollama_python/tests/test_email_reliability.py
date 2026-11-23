"""Email delivery: retries, honest feedback, and links that survive a failed resend."""
import smtplib

import pytest
from sqlalchemy import select

from conftest import H, make_admin, login, register, token_from, verify_last
from elaw_core import auth, config
from elaw_core import email as email_mod
from elaw_core.db import session_scope
from elaw_core.models import EmailVerificationToken, User


class FlakyProvider:
    """Raises the given SMTP errors in order, then delivers like MemoryEmailProvider."""

    def __init__(self, errors=()):
        self.errors = list(errors)
        self.attempts = 0
        self.outbox = []

    def send(self, to, subject, text, html_body):
        self.attempts += 1
        if self.errors:
            error = self.errors.pop(0)
            transient, code = email_mod._classify(error)
            raise email_mod.EmailError(type(error).__name__, transient=transient, code=code)
        self.outbox.append({"to": to, "subject": subject, "text": text})


@pytest.fixture()
def flaky(env, monkeypatch):
    monkeypatch.setattr(email_mod.time, "sleep", lambda s: None)  # no real waiting in tests
    email_mod.email_service._count_day = None

    def use(*errors):
        provider = FlakyProvider(errors)
        monkeypatch.setattr(email_mod.email_service, "_provider", provider)
        return provider
    return use


TRANSIENT = smtplib.SMTPServerDisconnected("dropped")
TRY_LATER = smtplib.SMTPResponseException(451, b"try again later")
BAD_LOGIN = smtplib.SMTPAuthenticationError(535, b"auth failed")
REJECTED = smtplib.SMTPRecipientsRefused({"x@example.com": (550, b"no such user")})


def test_classification():
    assert email_mod._classify(TRANSIENT) == (True, None)
    assert email_mod._classify(TRY_LATER) == (True, 451)
    assert email_mod._classify(TimeoutError()) == (True, None)
    assert email_mod._classify(BAD_LOGIN) == (False, 535)
    assert email_mod._classify(REJECTED) == (False, 550)


def test_transient_errors_are_retried_then_delivered(flaky, client):
    provider = flaky(TRANSIENT, TRY_LATER)
    r = register(client)
    assert r.status_code == 201 and r.json()["verification_email_sent"] is True
    assert provider.attempts == 3 and len(provider.outbox) == 1


def test_permanent_error_is_not_retried_and_reported(flaky, client):
    provider = flaky(BAD_LOGIN)
    r = register(client)
    body = r.json()
    assert r.status_code == 201 and body["verification_email_sent"] is False
    assert "couldn't send" in body["detail"]
    assert provider.attempts == 1
    with session_scope() as db:  # no orphan, undelivered token left behind
        assert db.scalar(select(EmailVerificationToken)) is None


def test_gives_up_after_max_attempts(flaky, client):
    provider = flaky(TRANSIENT, TRANSIENT, TRANSIENT, TRANSIENT)
    assert register(client).json()["verification_email_sent"] is False
    assert provider.attempts == email_mod.MAX_ATTEMPTS


def test_failed_resend_keeps_the_previous_link_working(flaky, client):
    provider = flaky()
    register(client)
    first = token_from(provider.outbox[-1])
    flaky(BAD_LOGIN)  # the mail server breaks before the resend
    r = client.post("/api/auth/resend-verification", headers=H(client),
                    json={"email": "alice@example.com"})
    assert r.status_code == 503 and r.json()["code"] == "email_failed"
    r = client.post("/api/auth/verify-email", headers=H(client), json={"token": first})
    assert r.status_code == 200  # the link they already had still works


def test_successful_resend_retires_the_old_link(flaky, client):
    provider = flaky()
    register(client)
    first = token_from(provider.outbox[-1])
    r = client.post("/api/auth/resend-verification", headers=H(client),
                    json={"email": "alice@example.com"})
    assert r.status_code == 200
    r = client.post("/api/auth/verify-email", headers=H(client), json={"token": first})
    assert r.json()["code"] == "used_token"
    assert client.post("/api/auth/verify-email", headers=H(client),
                       json={"token": token_from(provider.outbox[-1])}).status_code == 200


def test_unknown_email_still_gets_the_generic_answer_during_outage(flaky, client):
    flaky(BAD_LOGIN)
    for url in ("/api/auth/resend-verification", "/api/auth/forgot-password"):
        r = client.post(url, headers=H(client), json={"email": "ghost@example.com"})
        assert r.status_code == 200, url  # no account discovery


def test_forgot_password_reports_failure_for_real_account(flaky, client):
    flaky()
    register(client)
    flaky(REJECTED)
    r = client.post("/api/auth/forgot-password", headers=H(client),
                    json={"email": "alice@example.com"})
    assert r.status_code == 503 and r.json()["code"] == "email_failed"


def test_html_resend_page_shows_failure(flaky, client):
    flaky()
    register(client)
    flaky(BAD_LOGIN)
    r = client.post("/resend-verification", data={"email": "alice@example.com",
                                                   "csrf_token": client.csrf})
    assert r.status_code == 503 and "couldn" in r.text and "sent an email" not in r.text


def test_failures_do_not_use_up_the_daily_cap(flaky, client, monkeypatch):
    monkeypatch.setenv("EMAIL_DAILY_LIMIT", "1")
    config.reload_settings()
    flaky(BAD_LOGIN)
    register(client, email="a@example.com")       # fails, slot given back
    provider = flaky()
    assert register(client, email="b@example.com").json()["verification_email_sent"] is True
    assert register(client, email="c@example.com").json()["verification_email_sent"] is False
    assert len(provider.outbox) == 1               # cap of 1 delivered email respected


def test_admin_resend_reports_failure(flaky, client):
    flaky()
    register(client, "bob@example.com", "Bob")
    email, pw = make_admin()
    login(client, email, pw)
    with session_scope() as db:
        bob = db.scalar(select(User.id).where(User.email == "bob@example.com"))
    flaky(BAD_LOGIN)
    r = client.post(f"/api/admin/users/{bob}/resend-verification", headers=H(client))
    assert r.status_code == 502 and r.json() == {"sent": False}
    logs = client.get("/api/admin/audit-logs").json()["items"]
    assert logs[0]["action"] == "VERIFICATION_EMAIL_RESENT" and logs[0]["description"] == "send failed"


def test_logs_mask_the_address(flaky, client, caplog):
    flaky(BAD_LOGIN)
    with caplog.at_level("ERROR"):
        register(client, email="secretperson@example.com")
    text = caplog.text
    assert "s***@example.com" in text and "secretperson@" not in text
    assert "token=" not in text  # never the one-time link
