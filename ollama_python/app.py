# ELAW AI — entry point: page setup, theme, login gate and page navigation.
#
# Run locally (two terminals, see AUTH_ARCHITECTURE.md):
#   uvicorn auth_api.main:app --port 8000 --no-access-log   # login / register / admin API
#   streamlit run app.py                                     # chat + admin dashboard
#
# Chat settings (OLLAMA_HOST, DEFAULT_MODEL, ...) are documented in views/chat.py.
# Auth settings (DATABASE_URL, SMTP_*, ...) are documented in .env.example.

import streamlit as st

import streamlit_auth
import ui_theme

APP_NAME = "ELAW AI"

st.set_page_config(
    page_title=f"{APP_NAME} · Free Open-Source AI",
    page_icon="✦",
    layout="centered",
    initial_sidebar_state="auto",
)

ui_theme.apply_theme()

# ---------------- LOGIN GATE + PAGES ----------------
user = streamlit_auth.current_user()  # checked against the database on every run
if user is None:
    streamlit_auth.login_gate()
    st.stop()

pages = [st.Page("views/chat.py", title="Chat", icon=":material/chat:", default=True)]
if streamlit_auth.is_admin(user):  # admins only; the admin page re-checks on the server too
    pages.append(st.Page("views/admin.py", title="Admin", icon=":material/admin_panel_settings:",
                         url_path="admin"))
st.navigation(pages, position="hidden").run()
