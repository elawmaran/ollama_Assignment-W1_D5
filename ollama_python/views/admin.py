"""ELAW AI admin dashboard (Streamlit page at /admin).

Security: the page itself re-checks the role on the server, and every read or
action goes through elaw_core.admin, which checks permissions again. Only safe
metadata is shown: never password hashes, raw tokens or secrets.
"""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import streamlit_auth
from elaw_core import admin as svc
from elaw_core import config, usage
from elaw_core.db import session_scope

st.set_page_config(page_title="ELAW AI · Admin", page_icon="✦", layout="wide")
actor = streamlit_auth.require_admin()
perms = svc.permissions(actor)
IP = streamlit_auth.client_ip()

SERIES_1, SERIES_2 = "#2a78d6", "#eb6834"  # categorical slots 1–2 (validated palette)
STATUSES = ["PENDING", "ACTIVE", "SUSPENDED", "DISABLED"]
ROLES = ["USER", "ADMIN", "SUPER_ADMIN"]
SECTIONS = ["Overview", "Customers", "Login events", "Security events", "Audit log",
            "AI usage", "Subscriptions", "Settings"]


def fmt(ts):
    if not ts:
        return "—"
    return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M UTC")


def run(fn, *args, **kwargs):
    """Call the admin service in its own transaction; show permission errors nicely."""
    try:
        with session_scope() as db:
            return fn(db, actor, *args, **kwargs), None
    except (svc.PermissionDenied, svc.NotFound, ValueError) as error:
        return None, str(error)


def daily_frame(rows, value="count", days=30):
    """Fill missing days with zeros so gaps read as 'none', not 'skip'."""
    today = date.today()
    index = [str(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    df = pd.DataFrame(rows or [], columns=["day", *([value] if isinstance(value, str) else value)])
    return df.set_index("day").reindex(index, fill_value=0).rename_axis("day")


# ---------------- SIDEBAR NAV ----------------
with st.sidebar:
    st.markdown('<div class="brand"><div class="brand-logo">✦</div>'
                '<div class="brand-name">ELAW <em>Admin</em></div></div>', unsafe_allow_html=True)
    streamlit_auth.user_card(actor)
    st.page_link("views/chat.py", label="Back to chat", icon=":material/chat:")
    st.caption(f"Signed in as **{actor.role.value}**")
    qp_section = st.query_params.get("section")
    section = st.radio("Section", SECTIONS, label_visibility="collapsed",
                       index=SECTIONS.index(qp_section) if qp_section in SECTIONS else 0)
    if section != qp_section:
        st.query_params["section"] = section
        st.query_params.pop("user", None)


# ---------------- CUSTOMER DETAIL ----------------
@st.dialog("Confirm action")
def confirm_dialog(label, warning, fn, *args, needs_reason=False):
    st.warning(warning, icon=":material/warning:")
    reason = st.text_input("Reason (saved in the audit log)", max_chars=500) if needs_reason else ""
    sure = st.checkbox("I understand, go ahead")
    if st.button(label, type="primary", disabled=not sure):
        extra = {"reason": reason} if needs_reason else {}
        result, error = run(fn, *args, ip=IP, **extra)
        st.session_state.flash = ("error", error) if error else ("success", f"{label}: done.")
        st.rerun()


def show_flash():
    flash = st.session_state.pop("flash", None)
    if flash:
        (st.error if flash[0] == "error" else st.success)(flash[1])


def customer_detail(user_id):
    d, error = run(svc.user_detail, user_id)
    if error:
        st.error(error)
        return
    p, a, u, sub = d["profile"], d["authentication"], d["usage"], d["subscription"]
    if st.button("← All customers", key="back"):
        st.query_params.pop("user", None)
        st.rerun()
    st.title(p["name"])
    st.caption(f"Customer #{p['id']} · {p['email']}")
    show_flash()

    # ----- actions -----
    is_self = p["id"] == actor.id
    target_is_admin = p["role"] in ("ADMIN", "SUPER_ADMIN")
    can_touch = not is_self and p["role"] != "SUPER_ADMIN" and (
        not target_is_admin or actor.role.value == "SUPER_ADMIN")
    if can_touch:
        cols = st.columns(6)
        status = p["status"]
        if status in ("ACTIVE", "PENDING") and cols[0].button("Suspend", icon=":material/block:"):
            confirm_dialog("Suspend customer", "They will be logged out everywhere and can't log "
                           "in until reactivated.", svc.set_status, user_id, "SUSPENDED",
                           needs_reason=True)
        if status == "SUSPENDED" and cols[0].button("Activate", icon=":material/check_circle:"):
            confirm_dialog("Activate customer", "They will be able to log in again.",
                           svc.set_status, user_id, "ACTIVE")
        if perms["disable"] and status != "DISABLED" and cols[1].button(
                "Disable", icon=":material/person_off:"):
            confirm_dialog("Disable account", "The account is switched off (data kept). Only a "
                           "super admin can restore it.", svc.set_status, user_id, "DISABLED",
                           needs_reason=True)
        if perms["restore"] and status == "DISABLED" and cols[1].button(
                "Restore", icon=":material/restore:"):
            confirm_dialog("Restore account", "The account becomes usable again.",
                           svc.set_status, user_id, "RESTORED")
        if cols[2].button("Force logout", icon=":material/logout:"):
            confirm_dialog("Force logout", "All of this customer's sessions are revoked.",
                           svc.force_logout, user_id)
        if not p["email_verified"] and cols[3].button("Resend verification",
                                                       icon=":material/mail:"):
            _, err = run(svc.resend_verification, user_id, ip=IP)
            st.session_state.flash = ("error", err) if err else ("success", "Verification email sent.")
            st.rerun()
        if status in ("ACTIVE", "PENDING") and cols[4].button("Send password reset",
                                                              icon=":material/lock_reset:"):
            _, err = run(svc.trigger_password_reset, user_id, ip=IP)
            st.session_state.flash = ("error", err) if err else ("success", "Reset email sent.")
            st.rerun()
        if perms["change_role"]:
            new_role = "ADMIN" if p["role"] == "USER" else "USER"
            if cols[5].button(f"Make {new_role.lower()}", icon=":material/manage_accounts:"):
                confirm_dialog(f"Change role to {new_role}",
                               "Admins can see all customers and act on them. The user is "
                               "logged out so the new role applies on next login.",
                               svc.set_role, user_id, new_role)
    elif is_self:
        st.info("This is your own account. Manage it from the server CLI.")
    else:
        st.info("You don't have permission to change this account.")

    t1, t2, t3, t4 = st.tabs(["Profile", "Authentication", "Usage", "Subscription"])
    with t1:
        st.dataframe(pd.DataFrame({
            "Field": ["Customer ID", "Name", "Email", "Email verified", "Status", "Role",
                      "Created", "Updated", "Last login", "Last activity"],
            "Value": [str(p["id"]), p["name"], p["email"], "Yes" if p["email_verified"] else "No",
                      p["status"], p["role"], fmt(p["created_at"]), fmt(p["updated_at"]),
                      fmt(p["last_login_at"]), fmt(p["last_activity_at"])]}),
            hide_index=True, width="stretch")
    with t2:
        c = st.columns(4)
        c[0].metric("Successful logins", a["successful_logins"])
        c[1].metric("Failed logins", a["failed_logins"])
        c[2].metric("Active sessions", a["active_sessions"])
        c[3].metric("Online now", "Yes" if a["online"] else "No")
        st.caption(f"Last successful login: {fmt(a['last_successful_login'])}")
        st.subheader("Sessions")
        st.dataframe(pd.DataFrame([{**s, "created_at": fmt(s["created_at"]),
                                    "last_seen_at": fmt(s["last_seen_at"]),
                                    "expires_at": fmt(s["expires_at"])} for s in a["sessions"]]),
                     hide_index=True, width="stretch")
        st.subheader("Recent login events")
        st.dataframe(_events_frame(a["recent_login_events"]), hide_index=True, width="stretch")
    with t3:
        c = st.columns(4)
        c[0].metric("AI requests", u["ai_requests"])
        c[1].metric("Credits used", u["credits_used"])
        c[2].metric("Ollama / OpenAI", f"{u['ollama_requests']} / {u['openai_requests']}")
        c[3].metric("Estimated cost", f"${u['estimated_cost_usd']:.2f}")
        limit = sub["monthly_credits"]
        st.caption(f"This month: {u['credits_this_month']} credits"
                   + (f" of {limit} ({sub['plan']} plan)" if limit else " (unlimited plan)"))
        st.markdown("**AI requests per day (last 30 days)**")
        st.bar_chart(daily_frame(u["daily_requests"]), y="count", color=SERIES_1,
                     x_label="Day", y_label="Requests", height=220)
    with t4:
        st.metric("Current plan", sub["plan"])
        st.caption("Plans are prepared for the future. No payments are processed; "
                   "every customer is on FREE.")


def _events_frame(events):
    return pd.DataFrame([{
        "Time": fmt(e["created_at"]), "Customer": e["user_id"], "Email": e["email"],
        "Result": "✅ Success" if e["login_success"] else "❌ Failed",
        "IP address": e["ip_address"], "Device": e["device_info"],
        "Failure reason": e["failure_reason"] or ""} for e in events],
        columns=["Time", "Customer", "Email", "Result", "IP address", "Device", "Failure reason"])


# ---------------- SECTIONS ----------------
def page_controls(total, key, page_size):
    pages = max(1, -(-total // page_size))
    c1, c2 = st.columns([1, 4])
    page = c1.number_input("Page", 1, pages, 1, key=f"{key}_page")
    c2.caption(f"{total} result(s) · page {page} of {pages}")
    return page


def overview():
    st.title("Overview")
    data, error = run(svc.overview)
    if error:
        st.error(error)
        return
    c = data["cards"]
    rows = [
        [("Total customers", c["total_customers"]), ("Active customers", c["active_customers"]),
         ("New (7 days)", c["new_customers_7d"]), ("Online now", c["online_now"]),
         ("Active sessions", c["active_sessions"])],
        [("Verified", c["verified_customers"]), ("Unverified", c["unverified_customers"]),
         ("Login attempts (24h)", c["login_attempts_24h"]),
         ("Failed logins (24h)", c["failed_logins_24h"]), ("AI requests", c["ai_requests"])],
    ]
    for row in rows:
        for col, (label, value) in zip(st.columns(len(row)), row):
            col.metric(label, value)
    st.caption(f"AI credits consumed: {c['credits_consumed']} · estimated AI cost "
               f"${c['estimated_cost_usd']:.2f} (no-cost mode: {config.settings.cost_mode == 'free'})")

    left, right = st.columns(2)
    with left:
        st.markdown("**New registrations per day**")
        st.bar_chart(daily_frame(data["registrations_daily"]), y="count", color=SERIES_1,
                     x_label="Day", y_label="Registrations", height=240)
        st.markdown("**Daily active users** (users with a session that day)")
        st.bar_chart(daily_frame(data["sessions_daily"]), y="count", color=SERIES_1,
                     x_label="Day", y_label="Users", height=240)
    with right:
        st.markdown("**Login activity per day**")
        st.bar_chart(daily_frame(data["logins_daily"], ["success", "failed"]),
                     y=["success", "failed"], color=[SERIES_1, SERIES_2],
                     x_label="Day", y_label="Logins", height=240)
        st.markdown("**AI requests per day**")
        st.bar_chart(daily_frame(data["ai_usage_daily"]), y="count", color=SERIES_1,
                     x_label="Day", y_label="Requests", height=240)


def customers(preset_status=None):
    st.title("Customers")
    f = st.columns([3, 2, 2, 2, 3])
    q = f[0].text_input("Search name or email", max_chars=100)
    views = ["All", *STATUSES]
    status = f[1].selectbox("Status", views, index=views.index(preset_status or "All"))
    verified = f[2].selectbox("Email", ["All", "Verified", "Unverified"])
    role = f[3].selectbox("Role", ["All", *ROLES])
    dates = f[4].date_input("Registered between", value=(), max_value=date.today())
    s = st.columns([2, 2, 6])
    sort = s[0].selectbox("Sort by", ["created_at", "last_login_at"],
                          format_func={"created_at": "Registration date",
                                       "last_login_at": "Last login"}.get)
    order = s[1].selectbox("Order", ["desc", "asc"],
                           format_func={"desc": "Newest first", "asc": "Oldest first"}.get)
    page_size = 25
    page = st.session_state.get("cust_page", 1)
    kwargs = dict(q=q or None, status=None if status == "All" else status,
                  verified={"All": None, "Verified": True, "Unverified": False}[verified],
                  role=None if role == "All" else role,
                  registered_from=dates[0] if len(dates) > 0 else None,
                  registered_to=dates[1] if len(dates) > 1 else None,
                  sort=sort, order=order, page_size=page_size)
    data, error = run(svc.list_users, page=page, **kwargs)
    if error:
        st.error(error)
        return
    if data["total"] and page > -(-data["total"] // page_size):  # filters shrank the result
        st.session_state.cust_page = 1
        data, _ = run(svc.list_users, page=1, **kwargs)
    table = pd.DataFrame([{
        "ID": u["id"], "Name": u["name"], "Email": u["email"],
        "Verified": "✅" if u["email_verified"] else "—", "Status": u["status"],
        "Role": u["role"], "Registered": fmt(u["created_at"]), "Last login": fmt(u["last_login_at"]),
        "Logins": u["login_count"], "Last login IP": u["last_login_ip"] or "—",
        "Plan": u["plan"], "AI requests": u["ai_requests"], "Credits": u["credits_used"],
    } for u in data["items"]])
    st.caption("Select a row to open the customer.")
    picked = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row", key="cust_table")
    page_controls(data["total"], "cust", page_size)
    rows = picked.selection.rows if picked else []
    if rows:
        st.query_params["user"] = str(int(table.iloc[rows[0]]["ID"]))
        st.rerun()


def login_events(only_failed=False):
    st.title("Security events" if only_failed else "Login events")
    if only_failed:
        st.caption("Failed and blocked logins: wrong passwords, lockouts, rate limits, "
                   "suspended or unverified accounts.")
    f = st.columns([2, 3, 2, 3])
    result = "Failed" if only_failed else f[0].selectbox("Result", ["All", "Successful", "Failed"])
    email = f[1].text_input("Customer email contains", max_chars=100)
    ip = f[2].text_input("IP address", max_chars=64)
    dates = f[3].date_input("Date range", value=(), max_value=date.today())
    page = st.session_state.get("ev_page", 1)
    data, error = run(svc.login_events,
                      success={"All": None, "Successful": True, "Failed": False}[result],
                      email=email or None, ip=ip or None,
                      date_from=dates[0] if len(dates) > 0 else None,
                      date_to=dates[1] if len(dates) > 1 else None, page=page, page_size=50)
    if error:
        st.error(error)
        return
    st.dataframe(_events_frame(data["items"]), hide_index=True, width="stretch")
    page_controls(data["total"], "ev", 50)


def audit_log():
    st.title("Audit log")
    st.caption("Every admin action is recorded here. Entries can't be edited or deleted from the app.")
    actions = ["All", "USER_SUSPENDED", "USER_ACTIVATED", "USER_DISABLED", "USER_RESTORED",
               "ROLE_CHANGED", "SESSION_REVOKED", "VERIFICATION_EMAIL_RESENT",
               "PASSWORD_RESET_TRIGGERED"]
    action = st.selectbox("Action", actions)
    page = st.session_state.get("audit_page", 1)
    data, error = run(svc.audit_logs, action=None if action == "All" else action, page=page)
    if error:
        st.error(error)
        return
    st.dataframe(pd.DataFrame([{
        "Time": fmt(a["created_at"]), "Admin": a["admin_email"], "Action": a["action"],
        "Customer": a["target_email"] or a["target_user_id"], "IP address": a["ip_address"],
        "Details": a["description"]} for a in data["items"]],
        columns=["Time", "Admin", "Action", "Customer", "IP address", "Details"]),
        hide_index=True, width="stretch")
    page_controls(data["total"], "audit", 50)


def ai_usage():
    st.title("AI usage")
    data, error = run(svc.overview)
    if error:
        st.error(error)
        return
    c = data["cards"]
    cols = st.columns(3)
    cols[0].metric("AI requests", c["ai_requests"])
    cols[1].metric("Credits consumed", c["credits_consumed"])
    cols[2].metric("Estimated cost", f"${c['estimated_cost_usd']:.2f}")
    st.markdown("**AI requests per day**")
    st.bar_chart(daily_frame(data["ai_usage_daily"]), y="count", color=SERIES_1,
                 x_label="Day", y_label="Requests", height=260)
    st.markdown("**Model usage**")
    st.dataframe(pd.DataFrame(data["model_usage"], columns=["model", "provider", "requests",
                                                            "credits"]),
                 hide_index=True, width="stretch")
    st.caption(f"1 credit = {usage.TOKENS_PER_CREDIT} tokens (prompt + answer). "
               "Per-customer usage is on each customer's page.")


def subscriptions():
    st.title("Subscriptions")
    st.info("Plans are prepared for later. No payment processing exists, and everyone is "
            "on FREE.", icon=":material/info:")
    st.dataframe(pd.DataFrame([{"Plan": k, "Monthly credits": v["monthly_credits"] or "Unlimited",
                                "Price": "Free" if v["price_usd"] == 0 else "Not set"}
                               for k, v in usage.PLAN_LIMITS.items()]),
                 hide_index=True, width="stretch")


def settings_page():
    st.title("Settings")
    s = config.settings
    st.caption("Read-only view of safe settings. Secrets are never shown.")
    st.dataframe(pd.DataFrame({"Setting": [
        "Environment", "Public URL", "Database", "Email provider", "SMTP host", "SMTP configured",
        "Daily email cap", "Verification link lifetime", "Reset link lifetime", "Session length",
        "Lockout after failed logins", "Cost mode"], "Value": [
        s.app_env, s.public_url, s.database_url.split(":", 1)[0], s.email_provider,
        s.smtp_host or "—", "Yes" if s.smtp_password else "No", str(s.email_daily_limit),
        f"{s.verify_token_minutes} min", f"{s.reset_token_minutes} min", f"{s.session_days} days",
        f"{s.max_failed_logins} in {s.lockout_minutes} min",
        "free (paid AI providers blocked)" if s.cost_mode == "free" else s.cost_mode]}),
        hide_index=True, width="stretch")


selected_user = st.query_params.get("user")
if selected_user and selected_user.isdigit():
    customer_detail(int(selected_user))
elif section == "Overview":
    overview()
elif section == "Customers":
    customers()
elif section == "Login events":
    login_events()
elif section == "Security events":
    login_events(only_failed=True)
elif section == "Audit log":
    audit_log()
elif section == "AI usage":
    ai_usage()
elif section == "Subscriptions":
    subscriptions()
else:
    settings_page()
