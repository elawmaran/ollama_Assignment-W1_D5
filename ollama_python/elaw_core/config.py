"""Settings, read from environment variables (see .env.example). No secrets in code."""

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _load_dotenv():
    """Load a local .env file for development (never committed). Real env wins."""
    path = BASE_DIR / ".env"
    if os.getenv("ELAW_SKIP_DOTENV") or not path.exists():  # tests stay hermetic
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass
class Settings:
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    public_url: str = field(default_factory=lambda: os.getenv(
        "PUBLIC_URL", "http://localhost:8501").rstrip("/"))
    # Where the chat app lives. Same origin as PUBLIC_URL in production; in local
    # development the auth service (:8000) and Streamlit (:8501) are separate.
    app_url: str = field(default_factory=lambda: os.getenv(
        "APP_URL", os.getenv("PUBLIC_URL", "http://localhost:8501")).rstrip("/"))
    database_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'elaw.db'}"))

    # Sessions
    session_cookie: str = "elaw_session"
    csrf_cookie: str = "elaw_csrf"
    session_days: int = field(default_factory=lambda: _int("SESSION_DAYS", 7))
    secure_cookies: bool = field(default_factory=lambda: _bool("SECURE_COOKIES", False))
    trust_proxy: bool = field(default_factory=lambda: _bool("TRUST_PROXY", False))
    allowed_origins: list = field(default_factory=lambda: [
        o.strip().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()])

    # Tokens
    verify_token_minutes: int = field(default_factory=lambda: _int("VERIFY_TOKEN_MINUTES", 30))
    reset_token_minutes: int = field(default_factory=lambda: _int("RESET_TOKEN_MINUTES", 30))

    # Brute-force protection
    max_failed_logins: int = field(default_factory=lambda: _int("MAX_FAILED_LOGINS", 5))
    lockout_minutes: int = field(default_factory=lambda: _int("LOCKOUT_MINUTES", 15))

    # Email (SMTP). EMAIL_PROVIDER: smtp | console (console only allowed in development)
    email_provider: str = field(default_factory=lambda: os.getenv("EMAIL_PROVIDER", "console"))
    smtp_host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_username: str = field(default_factory=lambda: os.getenv("SMTP_USERNAME", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""), repr=False)
    smtp_from_email: str = field(default_factory=lambda: os.getenv("SMTP_FROM_EMAIL", ""))
    smtp_from_name: str = field(default_factory=lambda: os.getenv("SMTP_FROM_NAME", "ELAW AI"))
    smtp_use_ssl: bool = field(default_factory=lambda: _bool("SMTP_USE_SSL", False))
    email_daily_limit: int = field(default_factory=lambda: _int("EMAIL_DAILY_LIMIT", 400))

    # Cost: "free" blocks every paid AI provider (OpenAI etc.). Default and recommended.
    cost_mode: str = field(default_factory=lambda: os.getenv("COST_MODE", "free").lower())

    # Ollama (shared by Chat and Prompt Builder; one CPU, so answers queue)
    max_concurrent: int = field(default_factory=lambda: _int("MAX_CONCURRENT", 1))

    # Prompt Builder. All its limits live here; change them with env vars.
    prompt_builder_model: str = field(default_factory=lambda: os.getenv(
        "PROMPT_BUILDER_MODEL", os.getenv("DEFAULT_MODEL", "qwen3:4b-instruct")))
    guest_daily_limit: int = field(default_factory=lambda: _int("GUEST_DAILY_LIMIT", 5))
    free_daily_limit: int = field(default_factory=lambda: _int("FREE_DAILY_LIMIT", 20))
    admin_daily_limit: int = field(default_factory=lambda: _int("ADMIN_DAILY_LIMIT", 200))
    pb_per_minute: int = field(default_factory=lambda: _int("PROMPT_BUILDER_PER_MINUTE", 4))
    pb_max_input_chars: int = field(default_factory=lambda: _int("PROMPT_BUILDER_MAX_INPUT", 1000))
    pb_max_output_tokens: int = field(default_factory=lambda: _int("PROMPT_BUILDER_MAX_TOKENS", 900))
    pb_timeout_s: int = field(default_factory=lambda: _int("PROMPT_BUILDER_TIMEOUT", 150))
    pb_queue_wait_s: int = field(default_factory=lambda: _int("PROMPT_BUILDER_QUEUE_WAIT", 90))

    @property
    def is_production(self):
        return self.app_env == "production"


settings = Settings()


def reload_settings():
    """Re-read the environment (used by tests)."""
    global settings
    settings = Settings()
    return settings
