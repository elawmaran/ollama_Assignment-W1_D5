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
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr

from elaw_core import config

log = logging.getLogger("elaw.email")


class EmailError(Exception):
    pass


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
                with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context, timeout=20) as smtp:
                    smtp.login(s.smtp_username, s.smtp_password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
                    smtp.starttls(context=context)
                    smtp.login(s.smtp_username, s.smtp_password)
                    smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as error:
            # Log the error type only: never the credentials or the message body (it has tokens).
            log.error("SMTP send failed: %s", type(error).__name__)
            raise EmailError("Could not send email") from error


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

    def _within_daily_cap(self):
        """Stay under the free mailbox's daily sending limit (keeps it free, avoids bans)."""
        with self._lock:
            today = date.today()
            if self._count_day != today:
                self._count_day, self._count = today, 0
            if self._count >= config.settings.email_daily_limit:
                return False
            self._count += 1
            return True

    def send(self, to, subject, text, html_body):
        if not self._within_daily_cap():
            log.warning("Daily email cap reached; email not sent")
            raise EmailError("Daily email limit reached")
        self.provider.send(to, subject, text, html_body)

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
        self.send(to, "Verify your ELAW AI email", text, body)

    def send_password_reset(self, to, name, link, minutes):
        text, body = self._render(name, "Someone (hopefully you) asked to reset your ELAW AI password.",
                                  "Choose a new password", link, minutes)
        self.send(to, "Reset your ELAW AI password", text, body)


email_service = EmailService()
