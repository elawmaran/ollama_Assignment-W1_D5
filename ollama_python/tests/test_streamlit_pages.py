"""Render the real Streamlit pages: login gate, chat for customers, admin for admins."""
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

import streamlit_auth
from conftest import ROOT, make_admin
from elaw_core.db import session_scope
from elaw_core.models import Role, Status, User


def _user(email, role=Role.USER):
    with session_scope() as db:
        u = User(name=email.split("@")[0], email=email, password_hash="x", email_verified=True,
                 status=Status.ACTIVE, role=role)
        db.add(u)
    return u


def _page_links(at):
    return [c.proto.label for c in at.sidebar.children.values() if type(c).__name__ == "PageLink"]


def _text(at):
    return " ".join(m.value for m in at.markdown)


def test_logged_out_sees_login_gate_only(env, monkeypatch):
    monkeypatch.setattr(streamlit_auth, "current_user", lambda: None)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert "/login" in _text(at) and "/register" in _text(at)
    assert len(at.chat_input) == 0  # no chat without a session


def test_customer_gets_chat_but_not_admin(env, monkeypatch):
    customer = _user("cust@example.com")
    monkeypatch.setattr(streamlit_auth, "current_user", lambda: customer)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert len(at.chat_input) == 1 and "cust@example.com" in _text(at)
    assert "Admin dashboard" not in _page_links(at)
    admin_page = AppTest.from_file(str(ROOT / "views" / "admin.py"), default_timeout=30).run()
    assert any("don't have access" in e.value for e in admin_page.error)
    assert len(admin_page.metric) == 0


def test_admin_dashboard_sections_render(env, monkeypatch):
    make_admin()
    with session_scope() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
    _user("someone@example.com")
    monkeypatch.setattr(streamlit_auth, "current_user", lambda: admin)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    assert "Admin dashboard" in _page_links(at)
    at.switch_page("views/admin.py").run()
    assert not at.exception, at.exception
    assert any(m.label == "Total customers" and m.value == "2" for m in at.metric)
    for section in ["Customers", "Login events", "Security events", "Audit log", "AI usage",
                    "Subscriptions", "Settings"]:
        at.sidebar.radio[0].set_value(section).run()
        assert not at.exception, (section, at.exception)
    with session_scope() as db:
        uid = db.scalar(select(User.id).where(User.email == "someone@example.com"))
    at.query_params["user"] = str(uid)
    at.run()
    assert not at.exception, at.exception
    assert any(t.value == "someone" for t in at.title)
    assert "argon2" not in str(at).lower()
