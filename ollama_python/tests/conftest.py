import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ELAW_SKIP_DOTENV"] = "1"  # never read the developer's .env during tests


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh database (built by the real Alembic migration) and an in-memory mailbox."""
    monkeypatch.setenv("APP_ENV", "test")
    # Default SQLite; set TEST_DATABASE_URL to run the same tests on PostgreSQL.
    pg = os.getenv("TEST_DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", pg or f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("APP_URL", "http://testserver")
    monkeypatch.setenv("COST_MODE", "free")
    from elaw_core import config, db, security
    from elaw_core import email as email_mod

    config.reload_settings()
    db.reset_engine()
    if pg:  # start every test from an empty schema
        from sqlalchemy import text
        with db.get_engine().begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        db.reset_engine()
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    outbox = email_mod.MemoryEmailProvider()
    monkeypatch.setattr(email_mod.email_service, "_provider", outbox)
    security.limiter.reset()
    yield outbox
    db.reset_engine()


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from auth_api.main import app

    with TestClient(app) as c:
        c.outbox = env
        c.csrf = c.get("/api/auth/csrf").json()["csrf_token"]
        yield c


def token_from(mail):
    return re.search(r"token=([A-Za-z0-9_-]+)", mail["text"]).group(1)


PASSWORD = "Correct-horse-42"


def register(client, email="alice@example.com", name="Alice", password=PASSWORD):
    return client.post("/api/auth/register", headers={"X-CSRF-Token": client.csrf}, json={
        "name": name, "email": email, "password": password, "confirm_password": password})


def verify_last(client):
    token = token_from(client.outbox.outbox[-1])
    return client.post("/api/auth/verify-email", headers={"X-CSRF-Token": client.csrf},
                       json={"token": token})


def login(client, email="alice@example.com", password=PASSWORD):
    return client.post("/api/auth/login", headers={"X-CSRF-Token": client.csrf},
                       json={"email": email, "password": password})


def make_user(client, email="alice@example.com", name="Alice"):
    assert register(client, email, name).status_code == 201
    assert verify_last(client).status_code == 200
    return email


def make_admin(email="admin@example.com", role="SUPER_ADMIN"):
    os.environ["ELAW_ADMIN_PASSWORD"] = "Admin-pass-2026"
    try:
        from elaw_core import cli
        cli.create_admin(email, "Admin", role)
    finally:
        os.environ.pop("ELAW_ADMIN_PASSWORD")
    return email, "Admin-pass-2026"


def H(client):
    return {"X-CSRF-Token": client.csrf}
