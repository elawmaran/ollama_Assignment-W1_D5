"""Streamlit side of auth: reads the HTTP-only session cookie set by the auth
service and checks it against the database on every script run. Nothing about
the session lives in the browser except that opaque cookie."""

import html

import streamlit as st

from elaw_core import admin as admin_svc
from elaw_core import auth, config
from elaw_core.db import session_scope


def auth_url(path):
    """Auth pages live on the auth service (same origin in production)."""
    return f"{config.settings.public_url}{path}"


def current_user():
    """The logged-in ACTIVE, verified user, or None. Re-checked every run, so a
    suspension, logout or role change takes effect on the user's next click."""
    raw = st.context.cookies.get(config.settings.session_cookie)
    if not raw:
        return None
    try:
        with session_scope() as db:
            user = auth.current_user(db, raw)
    except Exception:  # database unreachable: fail closed
        return None
    return user


def require_user():
    user = current_user()
    if user is None:
        login_gate()
        st.stop()
    return user


def is_admin(user):
    return admin_svc.is_admin(user)


def require_admin():
    """Server-side check at the top of every admin page."""
    user = require_user()
    if not is_admin(user):
        st.error("You don't have access to this page.")
        st.stop()
    return user


def client_ip():
    if config.settings.trust_proxy:
        forwarded = st.context.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[-1].strip()[:64]
    return (st.context.ip_address or "")[:64] or None


def _link(href, label, cls):
    return f'<a class="{cls}" href="{html.escape(href)}" target="_self">{label}</a>'


def login_gate():
    st.markdown(f"""
        <div class="hero">
            <div class="orb">✦</div>
            <h1>ELAW AI.<br><span class="serif">Free and open.</span></h1>
            <p class="lead">Open-source AI on my own server. Create a free account to start chatting.</p>
            <div class="gate-actions">
                {_link(auth_url('/login'), 'Log in', 'gate-btn primary')}
                {_link(auth_url('/register'), 'Create free account', 'gate-btn')}
            </div>
            <div class="notice">
                <span>🔒</span>
                <span><b>Why an account?</b> It keeps this free server fair for everyone.
                Email + password only: no SMS, no paid sign-in service.</span>
            </div>
        </div>
    """, unsafe_allow_html=True)


def user_card(user):
    """Sidebar block: who is logged in, admin link, log out."""
    links = []
    if is_admin(user):
        links.append(("admin", "Admin dashboard"))
    st.markdown(f"""
        <div class="user-card">
            <div class="user-name">{html.escape(user.name)}</div>
            <div class="user-email">{html.escape(user.email)}</div>
        </div>
    """, unsafe_allow_html=True)
    for target, label in links:
        st.page_link(f"views/{target}.py", label=label, icon=":material/admin_panel_settings:")
    st.markdown(_link(auth_url("/logout"), "Log out", "side-link"), unsafe_allow_html=True)
