"""Streamlit side of auth: reads the HTTP-only session cookie set by the auth
service and checks it against the database on every script run. Nothing about
the session lives in the browser except that opaque cookie."""

import html

import streamlit as st

from elaw_core import admin as admin_svc
from elaw_core import auth, config
from elaw_core.db import session_scope


def auth_url(path):
    """Auth pages: relative on a single domain (production), else PUBLIC_URL."""
    s = config.settings
    return path if s.same_origin else f"{s.public_url}{path}"


def app_url(path):
    """Pages of the chat app: relative on a single domain (production), else APP_URL."""
    s = config.settings
    return path if s.same_origin else f"{s.app_url}{path}"


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


def prompt_builder_promo(note=""):
    """Highlighted card linking to Prompt Builder (chat home + welcome page). Plain HTML,
    so it lays out cleanly on every screen size."""
    extra = f" <b>{html.escape(note)}</b>" if note else ""
    st.markdown(f"""
        <div class="pb-promo">
            <div class="pb-promo-icon">auto_fix_high</div>
            <div class="pb-promo-body">
                <div class="pb-promo-title">Prompt Builder <span class="pb-new">✨ New</span></div>
                <div class="pb-promo-text">Tell us what you want in your own words. ELAW AI turns it
                into an AI-ready prompt.{extra}</div>
            </div>
            <a class="pb-promo-btn" href="{html.escape(app_url('/prompt-builder'))}" target="_self">
                <span class="mi">arrow_forward</span>Build a prompt</a>
        </div>
    """, unsafe_allow_html=True)


def logout_button():
    """Always-visible "Log out" pill in the top-right corner, next to the theme toggle."""
    st.markdown(f'<a class="top-logout" href="{html.escape(auth_url("/logout"))}" target="_self" '
                'title="Log out" aria-label="Log out"><span class="mi">logout</span>'
                '<span class="lbl">Log out</span></a>', unsafe_allow_html=True)


def login_gate():
    st.markdown("""
        <div class="hero">
            <div class="orb">✦</div>
            <h1>ELAW AI.<br><span class="serif">Free and open.</span></h1>
            <p class="lead">Open-source AI on my own server. Create a free account to start chatting.</p>
        </div>
    """, unsafe_allow_html=True)
    prompt_builder_promo("No account needed.")
    st.markdown(f"""
        <div class="hero" style="animation:none">
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


def user_card(user, admin_link=True):
    """Sidebar block: who is logged in, admin link, log out."""
    initials = "".join(w[0] for w in user.name.split()[:2]).upper() or "?"
    st.markdown(f"""
        <div class="user-card">
            <div class="user-avatar">{html.escape(initials)}</div>
            <div class="user-meta">
                <div class="user-name">{html.escape(user.name)}</div>
                <div class="user-email">{html.escape(user.email)}</div>
            </div>
        </div>
    """, unsafe_allow_html=True)
    if admin_link and is_admin(user):
        st.page_link("views/admin.py", label="Admin dashboard",
                     icon=":material/admin_panel_settings:")
    st.markdown(f'<a class="side-logout" href="{html.escape(auth_url("/logout"))}" target="_self">'
                '<span class="mi">logout</span>Log out</a>', unsafe_allow_html=True)
