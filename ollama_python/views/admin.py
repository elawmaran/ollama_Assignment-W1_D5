"""ELAW AI admin dashboard (Streamlit page at /admin).

Security: the page itself re-checks the role on the server, and every read or
action goes through elaw_core.admin, which checks permissions again. Only safe
metadata is shown: never password hashes, raw tokens or secrets.
"""

import html
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
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
st.markdown(f"<style>{(Path(__file__).parent.parent / 'admin.css').read_text()}</style>",
            unsafe_allow_html=True)

SERIES_1, SERIES_2 = "#2a78d6", "#eb6834"  # categorical slots 1–2 (validated palette)
try:
    TZ = ZoneInfo(st.context.timezone or "UTC")
except Exception:
    TZ = timezone.utc

STATUS_BADGE = {"ACTIVE": ("Active", "b-green"), "PENDING": ("Pending", "b-amber"),
                "SUSPENDED": ("Suspended", "b-red"), "DISABLED": ("Disabled", "b-gray")}
STATUS_LABEL = {"ACTIVE": "🟢 Active", "PENDING": "🟡 Pending", "SUSPENDED": "🔴 Suspended",
                "DISABLED": "⚫ Disabled"}
ROLE_LABEL = {"USER": "Customer", "ADMIN": "Admin", "SUPER_ADMIN": "Super admin"}

# (key, label, material icon, group)
NAV = [
    ("overview", "Overview", "dashboard", "Dashboard"),
    ("customers", "All customers", "group", "Customers"),
    ("active", "Active", "check_circle", "Customers"),
    ("pending", "Pending", "hourglass_top", "Customers"),
    ("suspended", "Suspended", "block", "Customers"),
    ("logins", "Login events", "login", "Security"),
    ("security", "Failed logins", "gpp_maybe", "Security"),
    ("audit", "Audit log", "history", "Security"),
    ("usage", "AI usage", "monitoring", "Usage"),
    ("plans", "Subscriptions", "workspace_premium", "Usage"),
    ("settings", "Settings", "settings", "System"),
]
NAV_KEYS = [n[0] for n in NAV]


# ---------------- HELPERS ----------------
def esc(value):
    return html.escape(str(value))


def local(ts):
    return datetime.fromisoformat(ts).astimezone(TZ) if ts else None


def fmt(ts):
    dt = local(ts)
    return dt.strftime("%d %b %Y, %H:%M") if dt else "—"


def ago(ts):
    if not ts:
        return "never"
    seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    for limit, div, unit in ((60, 1, "s"), (3600, 60, "min"), (86400, 3600, "h"), (2592000, 86400, "d")):
        if seconds < limit:
            return "just now" if unit == "s" else f"{int(seconds // div)} {unit} ago"
    return fmt(ts).split(",")[0]


def initials(name):
    return "".join(w[0] for w in str(name).split()[:2]).upper() or "?"


def badge(text, cls):
    return f'<span class="badge {cls}">{esc(text)}</span>'


def run(fn, *args, **kwargs):
    """Call the admin service in its own transaction; show permission errors nicely."""
    try:
        with session_scope() as db:
            return fn(db, actor, *args, **kwargs), None
    except (svc.PermissionDenied, svc.NotFound, ValueError) as error:
        return None, str(error)


def header(title, subtitle=""):
    st.markdown(f'<div class="adm-head"><div><h1>{esc(title)}</h1>'
                f'{f"<p>{esc(subtitle)}</p>" if subtitle else ""}</div></div>',
                unsafe_allow_html=True)


def section_label(text):
    st.markdown(f'<div class="adm-section">{esc(text)}</div>', unsafe_allow_html=True)


def kpi_cards(cards):
    """cards: (label, value, icon, hint, tone) with tone in '', good, warn, info."""
    st.markdown('<div class="kpi-grid">' + "".join(
        f'<div class="kpi {tone}"><div class="kpi-icon">{icon}</div><div>'
        f'<div class="kpi-label">{esc(label)}</div><div class="kpi-value">{esc(value)}</div>'
        f'{f"<div class=kpi-hint>{esc(hint)}</div>" if hint else ""}</div></div>'
        for label, value, icon, hint, tone in cards) + "</div>", unsafe_allow_html=True)


def empty(icon, text):
    st.markdown(f'<div class="empty"><b>{icon}</b>{esc(text)}</div>', unsafe_allow_html=True)


def daily(rows, days, value="count"):
    """One row per day (missing days = 0), as a real date column for the chart."""
    cols = [value] if isinstance(value, str) else list(value)
    today = date.today()
    idx = [str(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    df = pd.DataFrame(rows or [], columns=["day", *cols]).set_index("day")
    df = df.reindex(idx, fill_value=0).reset_index()
    df["day"] = pd.to_datetime(df["day"])
    return df


def bar_chart(df, series, labels, colors, empty_text, icon="bar_chart"):
    """Thin bars, whole-number axis, readable dates, hover tooltip; friendly empty state."""
    if float(df[series].to_numpy().sum()) == 0:
        empty(icon, empty_text)
        return
    long = df.melt("day", value_vars=series, var_name="series", value_name="value")
    long["series"] = long["series"].map(dict(zip(series, labels)))
    peak = int(df[series].sum(axis=1).max())
    chart = alt.Chart(long).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("yearmonthdate(day):T", title=None,
                axis=alt.Axis(format="%d %b", labelAngle=0, tickCount=6, grid=False)),
        y=alt.Y("sum(value):Q", title=None,
                axis=alt.Axis(format="d", tickMinStep=1, tickCount=min(peak, 4) or 1),
                scale=alt.Scale(domainMin=0, nice=True)),
        color=alt.Color("series:N", scale=alt.Scale(domain=labels, range=colors),
                        legend=alt.Legend(orient="top", title=None) if len(series) > 1 else None),
        tooltip=[alt.Tooltip("yearmonthdate(day):T", title="Day", format="%a %d %b"),
                 alt.Tooltip("series:N", title=""), alt.Tooltip("sum(value):Q", title="Count")],
    ).properties(height=210)
    st.altair_chart(chart, use_container_width=True)


def go(key, **params):
    st.query_params.clear()
    st.query_params["section"] = key
    for k, v in params.items():
        st.query_params[k] = str(v)


def show_flash():
    flash = st.session_state.pop("flash", None)
    if flash:
        (st.error if flash[0] == "error" else st.success)(flash[1])


# ---------------- SIDEBAR ----------------
current = st.query_params.get("section", "overview")
if current not in NAV_KEYS:
    current = "overview"

with st.sidebar:
    st.markdown('<div class="brand"><div class="brand-logo">✦</div>'
                '<div class="brand-name">ELAW <em>Admin</em></div></div>', unsafe_allow_html=True)
    group = None
    for key, label, icon, grp in NAV:
        if grp != group:
            st.markdown(f'<div class="nav-group">{esc(grp)}</div>', unsafe_allow_html=True)
            group = grp
        active = key == current and "user" not in st.query_params
        if st.button(label, key=f"nav_{key}", icon=f":material/{icon}:", width="stretch",
                     type="primary" if active else "secondary"):
            go(key)
            st.rerun()
    st.divider()
    st.page_link("views/chat.py", label="Back to chat", icon=":material/arrow_back:")
    streamlit_auth.user_card(actor, admin_link=False)
    st.caption(f"Role: {ROLE_LABEL[actor.role.value]}")


# ---------------- CONFIRM DIALOG ----------------
@st.dialog("Please confirm")
def confirm_dialog(label, warning, fn, *args, needs_reason=False):
    st.warning(warning, icon=":material/warning:")
    reason = st.text_input("Reason (saved in the audit log)", max_chars=500) if needs_reason else ""
    sure = st.checkbox("I understand, go ahead")
    if st.button(label, type="primary", disabled=not sure, width="stretch"):
        extra = {"reason": reason} if needs_reason else {}
        _, error = run(fn, *args, ip=IP, **extra)
        st.session_state.flash = ("error", error) if error else ("success", f"{label}: done.")
        st.rerun()


# ---------------- PAGES ----------------
def overview():
    hour = datetime.now(TZ).hour
    hello = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    head, pick = st.columns([3, 1], vertical_alignment="bottom")
    with head:
        header(f"{hello}, {actor.name.split()[0]}",
               f"Here's what's happening on ELAW AI · {datetime.now(TZ):%A %d %B}")
    with pick:
        days = st.segmented_control("Period", [7, 14, 30], default=14, key="ov_days",
                                    format_func=lambda d: f"{d} days",
                                    label_visibility="collapsed") or 14
    data, error = run(svc.overview, days=days)
    if error:
        st.error(error)
        return
    c = data["cards"]

    section_label("Customers")
    kpi_cards([
        ("Total customers", c["total_customers"], "group", f"{c['verified_customers']} verified", ""),
        ("Active", c["active_customers"], "check_circle",
         f"{c['pending_customers']} pending · {c['suspended_customers']} suspended", "good"),
        ("New this week", c["new_customers_7d"], "person_add", "registered in the last 7 days", "info"),
        ("Online now", c["online_now"], "wifi", f"{c['active_sessions']} active sessions", "good"),
    ])
    section_label("Security & usage (last 24 hours)")
    kpi_cards([
        ("Login attempts", c["login_attempts_24h"], "login", "successful and failed", ""),
        ("Failed logins", c["failed_logins_24h"], "gpp_maybe",
         "worth a look" if c["failed_logins_24h"] else "all clear",
         "warn" if c["failed_logins_24h"] else "good"),
        ("Unverified emails", c["unverified_customers"], "mark_email_unread",
         "haven't clicked their link", "warn" if c["unverified_customers"] else ""),
        ("AI requests", c["ai_requests"], "auto_awesome",
         f"{c['credits_consumed']} credits · ${c['estimated_cost_usd']:.2f} cost", "info"),
    ])

    section_label(f"Trends · last {days} days")
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown('<div class="panel-title">New registrations</div>'
                    '<div class="panel-sub">Accounts created per day</div>', unsafe_allow_html=True)
        bar_chart(daily(data["registrations_daily"], days), ["count"], ["Registrations"],
                  [SERIES_1], "No new registrations in this period yet", "person_add")
    with right, st.container(border=True):
        st.markdown('<div class="panel-title">Login activity</div>'
                    '<div class="panel-sub">Successful and failed logins per day</div>',
                    unsafe_allow_html=True)
        bar_chart(daily(data["logins_daily"], days, ["success", "failed"]),
                  ["success", "failed"], ["Successful", "Failed"], [SERIES_1, SERIES_2],
                  "No logins in this period yet", "login")
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown('<div class="panel-title">Daily active users</div>'
                    '<div class="panel-sub">Customers who used the site each day</div>',
                    unsafe_allow_html=True)
        bar_chart(daily(data["sessions_daily"], days), ["count"], ["Active users"], [SERIES_1],
                  "No activity in this period yet", "group")
    with right, st.container(border=True):
        st.markdown('<div class="panel-title">AI requests</div>'
                    '<div class="panel-sub">Chat answers generated per day</div>',
                    unsafe_allow_html=True)
        bar_chart(daily(data["ai_usage_daily"], days), ["count"], ["AI requests"], [SERIES_1],
                  "No chats yet. Usage appears here after the first answer", "auto_awesome")

    section_label("Recent")
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown('<div class="panel-title">Newest customers</div>', unsafe_allow_html=True)
        users, _ = run(svc.list_users, page_size=5)
        if users and users["items"]:
            st.markdown('<div class="rows">' + "".join(
                f'<div class="row"><div class="avatar">{esc(initials(u["name"]))}</div>'
                f'<div class="row-main"><div class="row-title">{esc(u["name"])}</div>'
                f'<div class="row-sub">{esc(u["email"])}</div></div>'
                f'{badge(*STATUS_BADGE[u["status"]])}'
                f'<div class="row-time">{esc(ago(u["created_at"]))}</div></div>'
                for u in users["items"]) + "</div>", unsafe_allow_html=True)
            if st.button("View all customers", icon=":material/arrow_forward:", key="ov_all"):
                go("customers")
                st.rerun()
        else:
            empty("group", "No customers yet")
    with right, st.container(border=True):
        st.markdown('<div class="panel-title">Latest login activity</div>', unsafe_allow_html=True)
        events, _ = run(svc.login_events, page_size=6)
        if events and events["items"]:
            st.markdown('<div class="rows">' + "".join(
                f'<div class="row"><div class="avatar">{"✓" if e["login_success"] else "!"}</div>'
                f'<div class="row-main"><div class="row-title">{esc(e["email"])}</div>'
                f'<div class="row-sub">{esc(e["device_info"] or "Unknown device")}'
                f'{" · " + esc(e["ip_address"]) if e["ip_address"] else ""}</div></div>'
                f'{badge("Success", "b-green") if e["login_success"] else badge(failure_text(e["failure_reason"]), "b-red")}'
                f'<div class="row-time">{esc(ago(e["created_at"]))}</div></div>'
                for e in events["items"]) + "</div>", unsafe_allow_html=True)
            if st.button("View all login events", icon=":material/arrow_forward:", key="ov_ev"):
                go("logins")
                st.rerun()
        else:
            empty("login", "No logins yet")


FAILURE_TEXT = {"wrong_password": "Wrong password", "unknown_email": "Unknown email",
                "locked": "Locked out", "rate_limited": "Too many tries",
                "unverified": "Not verified", "suspended": "Suspended", "disabled": "Disabled"}


def failure_text(reason):
    return FAILURE_TEXT.get(reason or "", "Failed")


def customers(preset_status=None):
    titles = {None: ("All customers", "Everyone who has signed up"),
              "ACTIVE": ("Active customers", "Verified and able to use ELAW AI"),
              "PENDING": ("Pending customers", "Signed up but haven't verified their email"),
              "SUSPENDED": ("Suspended customers", "Blocked by an admin")}
    header(*titles[preset_status])
    with st.container(border=True):
        f = st.columns([3, 2, 2, 2, 2])
        q = f[0].text_input("Search", placeholder="Name or email", max_chars=100,
                            key=f"c_q_{preset_status}")
        if preset_status:
            status = preset_status
            f[1].text_input("Status", value=STATUS_LABEL[preset_status], disabled=True)
        else:
            picked_status = f[1].selectbox("Status", ["Any", *STATUS_LABEL],
                                           format_func=lambda v: STATUS_LABEL.get(v, "Any"))
            status = None if picked_status == "Any" else picked_status
        verified = f[2].selectbox("Email", ["Any", "Verified", "Unverified"])
        role = f[3].selectbox("Role", ["Any", "USER", "ADMIN", "SUPER_ADMIN"],
                              format_func=lambda v: ROLE_LABEL.get(v, "Any"))
        sort = f[4].selectbox("Sort by", ["Newest", "Oldest", "Recently logged in"])
        dates = st.date_input("Registered between (optional)", value=(), max_value=date.today())

    page_size = 25
    key = f"cust_page_{preset_status}"
    page = st.session_state.get(key, 1)
    kwargs = dict(q=q or None, status=status,
                  verified={"Any": None, "Verified": True, "Unverified": False}[verified],
                  role=None if role == "Any" else role,
                  registered_from=dates[0] if len(dates) > 0 else None,
                  registered_to=dates[1] if len(dates) > 1 else None,
                  sort="last_login_at" if sort == "Recently logged in" else "created_at",
                  order="asc" if sort == "Oldest" else "desc", page_size=page_size)
    data, error = run(svc.list_users, page=page, **kwargs)
    if error:
        st.error(error)
        return
    if data["total"] and page > -(-data["total"] // page_size):
        st.session_state[key] = page = 1
        data, _ = run(svc.list_users, page=1, **kwargs)
    if not data["items"]:
        empty("person_search", "No customers match these filters")
        return

    table = pd.DataFrame([{
        "ID": u["id"], "Name": u["name"], "Email": u["email"],
        "Status": STATUS_LABEL[u["status"]],
        "Role": ROLE_LABEL[u["role"]], "Joined": local(u["created_at"]),
        "Last login": local(u["last_login_at"]), "Logins": u["login_count"],
        "AI requests": u["ai_requests"],
    } for u in data["items"]])
    st.caption("👆 Click a row to open the customer.")
    picked = st.dataframe(
        table, hide_index=True, width="stretch", on_select="rerun", height=fit(len(table)),
        selection_mode="single-row", key=f"cust_table_{preset_status}",
        column_config={
            "ID": st.column_config.NumberColumn(width="small"),
            "Joined": st.column_config.DatetimeColumn(format="D MMM YYYY"),
            "Last login": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
            "Logins": st.column_config.NumberColumn(width="small"),
            "AI requests": st.column_config.NumberColumn(width="small"),
        })
    pager(data["total"], key, page_size)
    rows = picked.selection.rows if picked else []
    if rows:
        go(current, user=int(table.iloc[rows[0]]["ID"]))
        st.rerun()


def fit(rows):
    """Table height that shows every row on the page (no inner scrollbar)."""
    return 38 + 35 * max(rows, 1)


def pager(total, key, page_size):
    pages = max(1, -(-total // page_size))
    page = st.session_state.get(key, 1)
    if pages == 1:
        st.caption(f"{total} in total")
        return
    bar = st.container(horizontal=True, vertical_alignment="center", gap="small")
    with bar:
        if st.button("Previous", icon=":material/chevron_left:", disabled=page <= 1,
                     key=f"{key}_prev"):
            st.session_state[key] = page - 1
            st.rerun()
        st.caption(f"Page {page} of {pages} · {total} in total")
        if st.button("Next", icon=":material/chevron_right:", icon_position="right",
                     disabled=page >= pages, key=f"{key}_next"):
            st.session_state[key] = page + 1
            st.rerun()


def customer_detail(user_id):
    d, error = run(svc.user_detail, user_id)
    if error:
        st.error(error)
        return
    p, a, u, sub = d["profile"], d["authentication"], d["usage"], d["subscription"]
    if st.button("Back to customers", icon=":material/arrow_back:", key="back"):
        go(current)
        st.rerun()
    online = badge("Online now", "b-green") if a["online"] else badge(
        f"Last seen {ago(p['last_activity_at'])}", "b-gray")
    st.markdown(f"""
        <div class="profile">
            <div class="avatar">{esc(initials(p["name"]))}</div>
            <div>
                <h2>{esc(p["name"])}</h2>
                <div class="sub">{esc(p["email"])} · Customer #{p["id"]} · joined {esc(fmt(p["created_at"]).split(",")[0])}</div>
                <div class="badges">{badge(*STATUS_BADGE[p["status"]])}
                    {badge(ROLE_LABEL[p["role"]], "b-violet")}
                    {badge("Email verified", "b-blue") if p["email_verified"] else badge("Email not verified", "b-amber")}
                    {online}</div>
            </div>
        </div>""", unsafe_allow_html=True)
    show_flash()
    actions(p)

    t1, t2, t3, t4 = st.tabs([":material/person: Profile", ":material/shield: Security",
                              ":material/auto_awesome: Usage", ":material/workspace_premium: Plan"])
    with t1:
        c = st.columns(3)
        c[0].metric("Last login", ago(p["last_login_at"]))
        c[1].metric("Total logins", a["successful_logins"])
        c[2].metric("Plan", p["plan"].title())
        st.dataframe(pd.DataFrame({
            "Field": ["Customer ID", "Name", "Email", "Email verified", "Status", "Role",
                      "Joined", "Last updated", "Last login", "Last activity"],
            "Value": [str(p["id"]), p["name"], p["email"], "Yes" if p["email_verified"] else "No",
                      STATUS_LABEL[p["status"]], ROLE_LABEL[p["role"]], fmt(p["created_at"]),
                      fmt(p["updated_at"]), fmt(p["last_login_at"]), fmt(p["last_activity_at"])]}),
            hide_index=True, width="stretch")
    with t2:
        c = st.columns(4)
        c[0].metric("Successful logins", a["successful_logins"])
        c[1].metric("Failed logins", a["failed_logins"])
        c[2].metric("Active sessions", a["active_sessions"])
        c[3].metric("Last login", ago(a["last_successful_login"]))
        st.markdown("**Devices & sessions**")
        if a["sessions"]:
            st.dataframe(pd.DataFrame([{
                "Device": s["device_info"], "IP address": s["ip_address"],
                "State": {"active": "🟢 Active", "revoked": "⚫ Logged out",
                          "expired": "⚪ Expired"}[s["state"]],
                "Started": local(s["created_at"]), "Last seen": local(s["last_seen_at"])}
                for s in a["sessions"]]), hide_index=True, width="stretch",
                column_config={"Started": st.column_config.DatetimeColumn(format="D MMM, HH:mm"),
                               "Last seen": st.column_config.DatetimeColumn(format="D MMM, HH:mm")})
        else:
            st.caption("No sessions yet.")
        st.markdown("**Recent login attempts**")
        events_table(a["recent_login_events"], show_email=False)
    with t3:
        c = st.columns(4)
        c[0].metric("AI requests", u["ai_requests"])
        c[1].metric("Credits used", u["credits_used"])
        c[2].metric("This month", u["credits_this_month"])
        c[3].metric("Cost", f"${u['estimated_cost_usd']:.2f}")
        limit = sub["monthly_credits"]
        if limit:
            st.progress(min(1.0, u["credits_this_month"] / limit),
                        text=f"{u['credits_this_month']} of {limit} monthly credits ({sub['plan'].title()} plan)")
        st.caption(f"Local Ollama requests: {u['ollama_requests']} · OpenAI requests: "
                   f"{u['openai_requests']} (blocked in no-cost mode)")
        with st.container(border=True):
            st.markdown('<div class="panel-title">AI requests per day</div>', unsafe_allow_html=True)
            bar_chart(daily(u["daily_requests"], 30), ["count"], ["AI requests"], [SERIES_1],
                      "This customer hasn't chatted yet", "auto_awesome")
    with t4:
        st.metric("Current plan", sub["plan"].title())
        st.info("Plans are ready for later. No payments are processed; everyone is on Free.",
                icon=":material/info:")


def actions(p):
    user_id, status = p["id"], p["status"]
    is_self = user_id == actor.id
    target_is_admin = p["role"] in ("ADMIN", "SUPER_ADMIN")
    if is_self:
        st.info("This is your own account. Change it from the server command line.",
                icon=":material/info:")
        return
    if p["role"] == "SUPER_ADMIN" or (target_is_admin and actor.role.value != "SUPER_ADMIN"):
        st.info("You don't have permission to change this account.", icon=":material/lock:")
        return
    buttons = []
    if status in ("ACTIVE", "PENDING"):
        buttons.append(("Suspend", "block", lambda: confirm_dialog(
            "Suspend customer", "They are logged out everywhere and can't log in until you "
            "activate them again.", svc.set_status, user_id, "SUSPENDED", needs_reason=True)))
    if status == "SUSPENDED":
        buttons.append(("Activate", "check_circle", lambda: confirm_dialog(
            "Activate customer", "They will be able to log in again.",
            svc.set_status, user_id, "ACTIVE")))
    if perms["disable"] and status != "DISABLED":
        buttons.append(("Disable", "person_off", lambda: confirm_dialog(
            "Disable account", "The account is switched off (its data is kept). Only a super "
            "admin can restore it.", svc.set_status, user_id, "DISABLED", needs_reason=True)))
    if perms["restore"] and status == "DISABLED":
        buttons.append(("Restore", "restore", lambda: confirm_dialog(
            "Restore account", "The account becomes usable again.",
            svc.set_status, user_id, "RESTORED")))
    buttons.append(("Log out everywhere", "logout", lambda: confirm_dialog(
        "Log out everywhere", "All of this customer's sessions end now.",
        svc.force_logout, user_id)))
    if not p["email_verified"]:
        buttons.append(("Resend verification", "mail", lambda: instant(
            svc.resend_verification, user_id, "Verification email sent.")))
    if status in ("ACTIVE", "PENDING"):
        buttons.append(("Send password reset", "lock_reset", lambda: instant(
            svc.trigger_password_reset, user_id, "Password reset email sent.")))
    if perms["change_role"]:
        new_role = "ADMIN" if p["role"] == "USER" else "USER"
        buttons.append((f"Make {ROLE_LABEL[new_role].lower()}", "manage_accounts",
                        lambda: confirm_dialog(
                            f"Change role to {ROLE_LABEL[new_role]}",
                            "Admins can see every customer and act on them. The person is "
                            "logged out so the new role applies on their next login.",
                            svc.set_role, user_id, new_role)))
    row = st.container(horizontal=True, gap="small")
    for label, icon, handler in buttons:
        with row:
            if st.button(label, icon=f":material/{icon}:", key=f"act_{label}"):
                handler()


def instant(fn, user_id, ok_text):
    _, err = run(fn, user_id, ip=IP)
    st.session_state.flash = ("error", err) if err else ("success", ok_text)
    st.rerun()


def events_table(events, show_email=True):
    if not events:
        empty("login", "No login attempts found")
        return
    df = pd.DataFrame([{
        "Time": local(e["created_at"]), "Email": e["email"],
        "Result": "✅ Success" if e["login_success"] else f"❌ {failure_text(e['failure_reason'])}",
        "Device": e["device_info"] or "", "IP address": e["ip_address"] or ""} for e in events])
    if not show_email:
        df = df.drop(columns=["Email"])
    st.dataframe(df, hide_index=True, width="stretch", height=fit(len(df)),
                 column_config={"Time": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm:ss")})


def login_events(only_failed=False):
    header("Failed logins" if only_failed else "Login events",
           "Wrong passwords, lockouts and blocked accounts: useful for spotting attacks"
           if only_failed else "Every login attempt, successful or not")
    with st.container(border=True):
        f = st.columns([2, 3, 2, 3])
        result = "Failed" if only_failed else f[0].selectbox("Result", ["All", "Successful", "Failed"])
        if only_failed:
            f[0].text_input("Result", value="❌ Failed only", disabled=True)
        email = f[1].text_input("Email", placeholder="Search by email", max_chars=100)
        ip = f[2].text_input("IP address", placeholder="e.g. 203.0.113.5", max_chars=64)
        dates = f[3].date_input("Date range", value=(), max_value=date.today())
    key = f"ev_page_{only_failed}"
    page = st.session_state.get(key, 1)
    data, error = run(svc.login_events,
                      success={"All": None, "Successful": True, "Failed": False}[result],
                      email=email or None, ip=ip or None,
                      date_from=dates[0] if len(dates) > 0 else None,
                      date_to=dates[1] if len(dates) > 1 else None, page=page, page_size=50)
    if error:
        st.error(error)
        return
    events_table(data["items"])
    if data["items"]:
        pager(data["total"], key, 50)


AUDIT_TEXT = {"USER_SUSPENDED": "Suspended", "USER_ACTIVATED": "Activated",
              "USER_DISABLED": "Disabled", "USER_RESTORED": "Restored",
              "ROLE_CHANGED": "Role changed", "SESSION_REVOKED": "Logged out everywhere",
              "VERIFICATION_EMAIL_RESENT": "Verification email resent",
              "PASSWORD_RESET_TRIGGERED": "Password reset sent"}


def audit_log():
    header("Audit log", "Every admin action is recorded here and can't be edited")
    action = st.selectbox("Action", ["All", *AUDIT_TEXT], format_func=lambda a: AUDIT_TEXT.get(a, "All actions"))
    page = st.session_state.get("audit_page", 1)
    data, error = run(svc.audit_logs, action=None if action == "All" else action, page=page)
    if error:
        st.error(error)
        return
    if not data["items"]:
        empty("history", "No admin actions yet")
        return
    st.dataframe(pd.DataFrame([{
        "Time": local(a["created_at"]), "Admin": a["admin_email"],
        "Action": AUDIT_TEXT.get(a["action"], a["action"]),
        "Customer": a["target_email"] or a["target_user_id"], "Details": a["description"],
        "IP address": a["ip_address"]} for a in data["items"]]),
        hide_index=True, width="stretch",
        column_config={"Time": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm")})
    pager(data["total"], "audit_page", 50)


def ai_usage():
    header("AI usage", "How much the AI is used. Local models keep the cost at $0")
    data, error = run(svc.overview, days=30)
    if error:
        st.error(error)
        return
    c = data["cards"]
    kpi_cards([
        ("AI requests", c["ai_requests"], "auto_awesome", "all time", "info"),
        ("Credits used", c["credits_consumed"], "toll", f"1 credit = {usage.TOKENS_PER_CREDIT} tokens", ""),
        ("Estimated cost", f"${c['estimated_cost_usd']:.2f}", "savings",
         "no-cost mode is on" if config.settings.cost_mode == "free" else "", "good"),
    ])
    st.write("")
    with st.container(border=True):
        st.markdown('<div class="panel-title">AI requests per day</div>'
                    '<div class="panel-sub">Last 30 days</div>', unsafe_allow_html=True)
        bar_chart(daily(data["ai_usage_daily"], 30), ["count"], ["AI requests"], [SERIES_1],
                  "No chats yet. Usage appears here after the first answer", "auto_awesome")
    with st.container(border=True):
        st.markdown('<div class="panel-title">By feature</div>', unsafe_allow_html=True)
        names = {"chat": "Chat", "deep_thinking": "Chat (deep thinking)",
                 "prompt_builder": "Prompt Builder"}
        if data["feature_usage"]:
            st.dataframe(pd.DataFrame([{"Feature": names.get(f["feature"], f["feature"]),
                                        "Requests": f["requests"], "Credits": f["credits"]}
                                       for f in data["feature_usage"]]),
                         hide_index=True, width="stretch")
        else:
            empty("apps", "No usage yet")
    with st.container(border=True):
        st.markdown('<div class="panel-title">By model</div>', unsafe_allow_html=True)
        if data["model_usage"]:
            st.dataframe(pd.DataFrame([{"Model": m["model"], "Provider": m["provider"].title(),
                                        "Requests": m["requests"], "Credits": m["credits"]}
                                       for m in data["model_usage"]]),
                         hide_index=True, width="stretch")
        else:
            empty("memory", "No model usage yet")


def subscriptions():
    header("Subscriptions", "Plans are ready for later. Everyone is on Free today")
    plans = usage.PLAN_LIMITS
    cols = st.columns(len(plans))
    for col, (name, info) in zip(cols, plans.items()):
        with col, st.container(border=True):
            st.markdown(f"**{name.title()}**")
            st.markdown(f"### {'Free' if info['price_usd'] == 0 else '—'}")
            credits = info["monthly_credits"]
            st.caption(f"{credits:,} credits / month" if credits else "Unlimited credits")
    st.info("No payment processing is set up. Customers can't be charged.", icon=":material/info:")


def settings_page():
    header("Settings", "Read-only. Secrets are never shown here")
    s = config.settings
    rows = [
        ("Site address", s.public_url), ("Environment", s.app_env.title()),
        ("Database", {"postgresql": "PostgreSQL", "sqlite": "SQLite"}.get(
            s.database_url.split(":", 1)[0].split("+")[0], "Other")),
        ("Email sending", "✅ Configured" if s.smtp_password and s.email_provider == "smtp"
         else "⚠️ Not configured: verification emails can't be sent"),
        ("Email server", s.smtp_host or "—"), ("Daily email limit", f"{s.email_daily_limit} emails"),
        ("Verification link valid for", f"{s.verify_token_minutes} minutes"),
        ("Reset link valid for", f"{s.reset_token_minutes} minutes"),
        ("Stay logged in for", f"{s.session_days} days"),
        ("Lockout", f"after {s.max_failed_logins} wrong passwords, for {s.lockout_minutes} minutes"),
        ("Cost mode", "✅ Free: paid AI providers are blocked" if s.cost_mode == "free" else s.cost_mode),
    ]
    with st.container(border=True):
        for label, value in rows:
            a, b = st.columns([1, 2])
            a.markdown(f"<span style='color:var(--muted)'>{esc(label)}</span>", unsafe_allow_html=True)
            b.markdown(esc(value))


# ---------------- ROUTER ----------------
selected_user = st.query_params.get("user")
if selected_user and selected_user.isdigit():
    customer_detail(int(selected_user))
elif current == "overview":
    overview()
elif current in ("customers", "active", "pending", "suspended"):
    customers({"customers": None, "active": "ACTIVE", "pending": "PENDING",
               "suspended": "SUSPENDED"}[current])
elif current == "logins":
    login_events()
elif current == "security":
    login_events(only_failed=True)
elif current == "audit":
    audit_log()
elif current == "usage":
    ai_usage()
elif current == "plans":
    subscriptions()
else:
    settings_page()
