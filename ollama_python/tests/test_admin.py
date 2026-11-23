import json

import pytest
from sqlalchemy import select

from conftest import H, login, make_admin, make_user
from elaw_core import admin as admin_svc
from elaw_core import usage
from elaw_core.db import session_scope
from elaw_core.models import AdminAuditLog, Role, Status, User

SECRET_KEYS = ("password_hash", "token_hash", "salt", "smtp_password")

ADMIN_GETS = ["/api/admin/overview", "/api/admin/users", "/api/admin/users/1",
              "/api/admin/login-events", "/api/admin/audit-logs"]


def admin_login(client, role="SUPER_ADMIN", email="admin@example.com"):
    email, pw = make_admin(email, role)
    assert login(client, email, pw).status_code == 200


def uid(email):
    with session_scope() as db:
        return db.scalar(select(User.id).where(User.email == email))


def assert_no_secrets(payload, raw_tokens=()):
    text = json.dumps(payload)
    for key in SECRET_KEYS:
        assert key not in text
    assert "$argon2" not in text
    for raw in raw_tokens:
        assert raw not in text


def test_anonymous_cannot_access_admin_api(client):
    for url in ADMIN_GETS:
        assert client.get(url).status_code == 401, url


def test_customer_cannot_access_admin_api(client):
    make_user(client)
    login(client)
    for url in ADMIN_GETS:
        assert client.get(url).status_code == 403, url
    r = client.patch("/api/admin/users/1/status", headers=H(client),
                     json={"status": "SUSPENDED", "confirm": True})
    assert r.status_code == 403
    r = client.post("/api/admin/users/1/force-logout", headers=H(client))
    assert r.status_code == 403


def test_admin_login_and_customer_list(client):
    for i in range(3):
        make_user(client, f"user{i}@example.com", f"User {i}")
    admin_login(client)
    r = client.get("/api/admin/users", params={"page_size": 2, "sort": "created_at"})
    body = r.json()
    assert r.status_code == 200 and body["total"] == 4 and len(body["items"]) == 2
    assert_no_secrets(body)
    r = client.get("/api/admin/users", params={"q": "user1"}).json()
    assert [u["email"] for u in r["items"]] == ["user1@example.com"]
    r = client.get("/api/admin/users", params={"role": "USER", "verified": True}).json()
    assert r["total"] == 3
    r = client.get("/api/admin/users", params={"page_size": 1000})
    assert r.status_code == 422  # server-side page size cap


def test_customer_detail_is_safe(client):
    make_user(client)
    login(client)
    session_raw = client.cookies.get("elaw_session")
    client.cookies.clear()
    client.csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    with session_scope() as db:
        usage.record_usage(db, uid("alice@example.com"), "qwen3:4b-instruct", "ollama", 100, 400)
    admin_login(client)
    r = client.get(f"/api/admin/users/{uid('alice@example.com')}")
    d = r.json()
    assert r.status_code == 200
    assert d["profile"]["email"] == "alice@example.com"
    assert d["authentication"]["successful_logins"] == 1
    assert d["usage"]["ollama_requests"] == 1 and d["usage"]["estimated_cost_usd"] == 0
    assert d["subscription"]["plan"] == "FREE"
    assert_no_secrets(d, [session_raw])
    assert client.get("/api/admin/users/9999").status_code == 404


def test_suspend_activate_force_logout_and_audit(client):
    make_user(client)
    login(client)
    victim_cookie = client.cookies.get("elaw_session")
    client.cookies.clear()
    client.csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    admin_login(client)
    target = uid("alice@example.com")

    r = client.patch(f"/api/admin/users/{target}/status", headers=H(client),
                     json={"status": "SUSPENDED"})
    assert r.status_code == 400  # confirmation required for dangerous actions
    r = client.patch(f"/api/admin/users/{target}/status", headers=H(client),
                     json={"status": "SUSPENDED", "reason": "spam", "confirm": True})
    assert r.status_code == 200 and r.json()["status"] == "SUSPENDED"

    with session_scope() as db:  # suspension revoked the customer's session
        from elaw_core import auth
        assert auth.current_user(db, victim_cookie) is None

    r = client.patch(f"/api/admin/users/{target}/status", headers=H(client),
                     json={"status": "ACTIVE", "confirm": True})
    assert r.json()["status"] == "ACTIVE"
    r = client.post(f"/api/admin/users/{target}/force-logout", headers=H(client))
    assert r.status_code == 200

    logs = client.get("/api/admin/audit-logs").json()["items"]
    actions = [a["action"] for a in logs]
    assert {"USER_SUSPENDED", "USER_ACTIVATED", "SESSION_REVOKED"} <= set(actions)
    assert all(a["admin_email"] == "admin@example.com" for a in logs)
    assert any("spam" in a["description"] for a in logs)


def test_role_rules(client):
    make_user(client)
    target = uid("alice@example.com")
    admin_login(client, role="ADMIN")
    r = client.patch(f"/api/admin/users/{target}/role", headers=H(client),
                     json={"role": "ADMIN", "confirm": True})
    assert r.status_code == 403  # only super admins change roles
    r = client.patch(f"/api/admin/users/{target}/status", headers=H(client),
                     json={"status": "DISABLED", "confirm": True})
    assert r.status_code == 403  # disabling is super-admin only
    me = uid("admin@example.com")
    r = client.patch(f"/api/admin/users/{me}/status", headers=H(client),
                     json={"status": "SUSPENDED", "confirm": True})
    assert r.status_code == 403  # can't act on yourself


def test_super_admin_role_change_and_no_super_promotion(client):
    make_user(client)
    target = uid("alice@example.com")
    admin_login(client)
    r = client.patch(f"/api/admin/users/{target}/role", headers=H(client),
                     json={"role": "ADMIN", "confirm": True})
    assert r.status_code == 200 and r.json()["role"] == "ADMIN"
    r = client.patch(f"/api/admin/users/{target}/role", headers=H(client),
                     json={"role": "SUPER_ADMIN", "confirm": True})
    assert r.status_code == 422  # not allowed from the web at all
    with session_scope() as db:
        a = db.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "ROLE_CHANGED"))
        assert a.target_user_id == target and "USER → ADMIN" in a.description


def test_admin_resend_and_reset_emails_dont_leak_tokens(client):
    from conftest import register
    register(client, "bob@example.com", "Bob")
    admin_login(client)
    bob = uid("bob@example.com")
    r = client.post(f"/api/admin/users/{bob}/resend-verification", headers=H(client))
    assert r.status_code == 200 and r.json() == {"sent": True}
    r = client.post(f"/api/admin/users/{bob}/password-reset", headers=H(client))
    assert r.json() == {"sent": True}
    from conftest import token_from
    raw = [token_from(m) for m in client.outbox.outbox]
    for url in ADMIN_GETS[:2] + [f"/api/admin/users/{bob}", "/api/admin/audit-logs"]:
        assert_no_secrets(client.get(url).json(), raw)


def test_login_events_monitoring(client):
    make_user(client)
    login(client, password="Wrong-password-1")
    login(client)
    client.cookies.clear()
    client.csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    admin_login(client)
    r = client.get("/api/admin/login-events", params={"success": False}).json()
    assert r["total"] == 1 and r["items"][0]["failure_reason"] == "wrong_password"
    r = client.get("/api/admin/login-events", params={"email": "alice"}).json()
    assert r["total"] == 2
    assert_no_secrets(r)


def test_overview_cards(client):
    make_user(client)
    admin_login(client)
    c = client.get("/api/admin/overview").json()["cards"]
    assert c["total_customers"] == 2 and c["verified_customers"] == 2
    assert c["estimated_cost_usd"] == 0


def test_service_layer_enforces_permissions_without_http(env):
    """The Streamlit admin pages call the service directly: rules must hold there too."""
    with session_scope() as db:
        customer = User(name="C", email="c@example.com", password_hash="x", email_verified=True,
                        status=Status.ACTIVE, role=Role.USER)
        other = User(name="D", email="d@example.com", password_hash="x", email_verified=True,
                     status=Status.ACTIVE, role=Role.USER)
        db.add_all([customer, other])
        db.flush()
        with pytest.raises(admin_svc.PermissionDenied):
            admin_svc.list_users(db, customer)
        with pytest.raises(admin_svc.PermissionDenied):
            admin_svc.set_status(db, customer, other.id, "SUSPENDED")


def test_no_cost_mode_blocks_paid_providers(env):
    assert usage.route("qwen3:4b-instruct") == "ollama"
    with pytest.raises(usage.PaidProviderBlocked):
        usage.route("gpt-4o-mini")
    assert usage.estimate_cost("ollama", 10_000, 10_000) == 0
