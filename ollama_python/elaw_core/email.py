"""Email delivery behind one interface.

EmailService
    ├── SMTPEmailProvider     any SMTP mailbox (Gmail app password, Brevo free, Zoho...)
    ├── ConsoleEmailProvider  development only: prints the email instead of sending
    └── (FutureEmailProvider) add a class with .send(to, subject, text, html)

No paid OTP/SMS providers. Credentials come only from environment variables.
"""

import html
import logging
import smtplib
import ssl
import threading
import time
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr

from elaw_core import config

log = logging.getLogger("elaw.email")


class EmailError(Exception):
    def __init__(self, message, transient=False, code=None):
        super().__init__(message)
        self.transient = transient  # worth retrying (timeouts, 4xx "try again later")
        self.code = code            # SMTP reply code, if the server gave one


# Retries for temporary problems only; wrong password or rejected address won't fix itself.
MAX_ATTEMPTS = 3
BACKOFF_S = (2, 5)  # wait before attempt 2, attempt 3


def mask(address):
    """a***@gmail.com: enough for support, not the full address in logs."""
    local, _, domain = (address or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def _classify(error):
    """Return (transient, smtp_code) for an exception raised while sending."""
    code = getattr(error, "smtp_code", None)
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        codes = [c for c, _ in error.recipients.values()]
        code = codes[0] if codes else None
        return bool(code and 400 <= code < 500), code
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return False, code
    if isinstance(error, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                          TimeoutError, ConnectionError)):
        return True, code
    if isinstance(error, smtplib.SMTPResponseException):
        return 400 <= error.smtp_code < 500, code
    return isinstance(error, OSError), code


class SMTPEmailProvider:
    def __init__(self, s):
        missing = [k for k in ("smtp_host", "smtp_username", "smtp_password", "smtp_from_email")
                   if not getattr(s, k)]
        if missing:
            raise EmailError("SMTP is not configured: missing " +
                             ", ".join(m.upper() for m in missing))
        self.s = s

    def send(self, to, subject, text, html_body):
        s = self.s
        msg = EmailMessage()
        msg["From"] = formataddr((s.smtp_from_name, s.smtp_from_email))
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        msg.add_alternative(html_body, subtype="html")
        context = ssl.create_default_context()
        try:
            if s.smtp_use_ssl or s.smtp_port == 465:
                with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context, timeout=15) as smtp:
                    smtp.login(s.smtp_username, s.smtp_password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                    smtp.starttls(context=context)
                    smtp.login(s.smtp_username, s.smtp_password)
                    smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as error:
            transient, code = _classify(error)
            # Never the credentials or the message body (it contains the one-time link).
            raise EmailError(f"{type(error).__name__}", transient=transient, code=code) from error


class ConsoleEmailProvider:
    """Development only. Prints links to the terminal so you can click them."""

    def send(self, to, subject, text, html_body):
        print(f"\n===== DEV EMAIL to {to} =====\nSubject: {subject}\n\n{text}\n"
              "=============================\n", flush=True)


class MemoryEmailProvider:
    """Tests: keeps sent emails in a list."""

    def __init__(self):
        self.outbox = []

    def send(self, to, subject, text, html_body):
        self.outbox.append({"to": to, "subject": subject, "text": text, "html": html_body})


class EmailService:
    def __init__(self, provider=None):
        self._provider = provider
        self._count_day = None
        self._count = 0
        self._lock = threading.Lock()

    @property
    def provider(self):
        if self._provider is None:
            s = config.settings
            if s.email_provider == "smtp":
                self._provider = SMTPEmailProvider(s)
            elif s.email_provider == "console" and not s.is_production:
                self._provider = ConsoleEmailProvider()
            else:
                raise EmailError("EMAIL_PROVIDER must be 'smtp' in production")
        return self._provider

    def _reserve(self):
        """Hold one slot of the daily cap (stays under the free mailbox's limit)."""
        with self._lock:
            today = date.today()
            if self._count_day != today:
                self._count_day, self._count = today, 0
            if self._count >= config.settings.email_daily_limit:
                return False
            self._count += 1
            return True

    def _release(self):
        """A failed email gives its slot back: only delivered emails count."""
        with self._lock:
            self._count = max(0, self._count - 1)

    def send(self, to, subject, text, html_body, purpose="email"):
        """Send with retries for temporary failures. Raises EmailError if it can't be sent."""
        if not self._reserve():
            log.error("email not sent: daily cap of %s reached (purpose=%s to=%s)",
                      config.settings.email_daily_limit, purpose, mask(to))
            raise EmailError("Daily email limit reached")
        try:
            provider = self.provider
        except EmailError as error:
            self._release()
            log.error("email not sent: %s (purpose=%s to=%s)", error, purpose, mask(to))
            raise
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                provider.send(to, subject, text, html_body)
                log.info("email sent (purpose=%s to=%s attempt=%s)", purpose, mask(to), attempt)
                return
            except EmailError as error:
                retry = error.transient and attempt < MAX_ATTEMPTS
                log.log(logging.WARNING if retry else logging.ERROR,
                        "email send failed (purpose=%s to=%s attempt=%s/%s error=%s code=%s%s)",
                        purpose, mask(to), attempt, MAX_ATTEMPTS, error, error.code,
                        ", retrying" if retry else "")
                if not retry:
                    self._release()
                    raise
                time.sleep(BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)])

    # ---- templates ----
    def _render(self, name, intro, button, link, minutes):
        safe_name = html.escape(name)
        text = (f"Hi {name},\n\n{intro}\n\n{link}\n\n"
                f"This link expires in {minutes} minutes and can be used once.\n"
                "If you didn't ask for this, you can ignore this email.\n\n— ELAW AI")
        html_body = f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:480px;margin:auto;color:#1d1d1f">
<h2 style="color:#7c3aed">ELAW AI</h2><p>Hi {safe_name},</p><p>{html.escape(intro)}</p>
<p><a href="{html.escape(link)}" style="display:inline-block;background:#7c3aed;color:#fff;padding:12px 20px;border-radius:10px;text-decoration:none">{html.escape(button)}</a></p>
<p style="color:#6e6e73;font-size:13px">This link expires in {minutes} minutes and can be used once.
If you didn't ask for this, you can ignore this email.</p></div>"""
        return text, html_body

    def send_verification(self, to, name, link, minutes):
        text, body = self._render(name, "Please confirm your email address to start using ELAW AI.",
                                  "Verify my email", link, minutes)
        self.send(to, "Verify your ELAW AI email", text, body, purpose="verification")

    def send_password_reset(self, to, name, link, minutes):
        text, body = self._render(name, "Someone (hopefully you) asked to reset your ELAW AI password.",
                                  "Choose a new password", link, minutes)
        self.send(to, "Reset your ELAW AI password", text, body, purpose="password_reset")


email_service = EmailService()
