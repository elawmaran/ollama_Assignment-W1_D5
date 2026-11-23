"""Admin service: customer management, monitoring and audit logging.

Every function takes the acting admin and checks permissions itself, so the
rules hold no matter which frontend (Streamlit pages or the JSON API) calls it.
Outputs are plain dicts of safe metadata: never hashes, tokens or secrets.
"""

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import case, func, or_, select

from elaw_core import auth
from elaw_core.db import as_utc, utcnow
from elaw_core.models import (
    ADMIN_ROLES, AdminAuditLog, LoginEvent, Role, Session, Status, UsageEvent, User,
)
from elaw_core.usage import PLAN_LIMITS

ONLINE_MINUTES = 5
MAX_PAGE_SIZE = 100


class PermissionDenied(Exception):
    pass


class NotFound(Exception):
    pass


# ---------------- PERMISSIONS ----------------
def is_admin(user):
    return user is not None and user.role in ADMIN_ROLES and user.status == Status.ACTIVE


def require_admin(user):
    if not is_admin(user):
        raise PermissionDenied("Admin access required.")


def _require_can_manage(actor, target, super_only=False):
    require_admin(actor)
    if actor.id == target.id:
        raise PermissionDenied("You can't change your own account here.")
    if super_only and actor.role != Role.SUPER_ADMIN:
        raise PermissionDenied("Only a super admin can do this.")
    if target.role == Role.SUPER_ADMIN:
        raise PermissionDenied("Super admin accounts can only be changed from the server CLI.")
    if target.role == Role.ADMIN and actor.role != Role.SUPER_ADMIN:
        raise PermissionDenied("Only a super admin can manage admins.")


def permissions(actor):
    """What the UI may offer. The service functions still enforce every rule."""
    sup = actor is not None and actor.role == Role.SUPER_ADMIN
    return {"suspend": True, "activate": True, "force_logout": True, "resend": True,
            "reset": True, "disable": sup, "restore": sup, "change_role": sup}


def audit(db, actor, action, target_id=None, ip=None, description=""):
    db.add(AdminAuditLog(admin_user_id=actor.id if actor else None, action=action,
                         target_user_id=target_id, ip_address=ip, description=description[:2000]))


def _get_user(db, user_id):
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("Customer not found.")
    return user


# ---------------- SERIALIZERS (safe fields only) ----------------
def _iso(dt):
    dt = as_utc(dt)
    return dt.isoformat() if dt else None


def user_summary(u, login_count=0, last_ip=None, credits=0, ai_requests=0):
    return {
        "id": u.id, "name": u.name, "email": u.email, "email_verified": u.email_verified,
        "status": u.status.value, "role": u.role.value, "plan": u.plan.value,
        "created_at": _iso(u.created_at), "updated_at": _iso(u.updated_at),
        "last_login_at": _iso(u.last_login_at), "last_activity_at": _iso(u.last_activity_at),
        "login_count": login_count or 0, "last_login_ip": last_ip,
        "credits_used": credits or 0, "ai_requests": ai_requests or 0,
    }


def _event(e):
    return {"id": e.id, "created_at": _iso(e.created_at), "user_id": e.user_id, "email": e.email,
            "login_success": e.login_success, "ip_address": e.ip_address,
            "device_info": e.device_info, "user_agent": e.user_agent,
            "failure_reason": e.failure_reason}


def _audit_row(a, admin_email=None, target_email=None):
    return {"id": a.id, "created_at": _iso(a.created_at), "admin_user_id": a.admin_user_id,
            "admin_email": admin_email, "action": a.action, "target_user_id": a.target_user_id,
            "target_email": target_email, "ip_address": a.ip_address,
            "description": a.description}


def _page(page, page_size):
    page = max(1, int(page or 1))
    page_size = min(MAX_PAGE_SIZE, max(1, int(page_size or 25)))
    return page, page_size


def _day_start(d):
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


# ---------------- CUSTOMERS ----------------
def list_users(db, actor, q=None, status=None, verified=None, role=None, registered_from=None,
               registered_to=None, sort="created_at", order="desc", page=1, page_size=25):
    require_admin(actor)
    page, page_size = _page(page, page_size)

    login_count = (select(func.count(LoginEvent.id))
                   .where(LoginEvent.user_id == User.id, LoginEvent.login_success.is_(True))
                   .correlate(User).scalar_subquery())
    last_ip = (select(LoginEvent.ip_address)
               .where(LoginEvent.user_id == User.id, LoginEvent.login_success.is_(True))
               .order_by(LoginEvent.created_at.desc()).limit(1)
               .correlate(User).scalar_subquery())
    credits = (select(func.coalesce(func.sum(UsageEvent.credits_used), 0))
               .where(UsageEvent.user_id == User.id).correlate(User).scalar_subquery())
    requests = (select(func.count(UsageEvent.id))
                .where(UsageEvent.user_id == User.id).correlate(User).scalar_subquery())

    filters = []
    if q:
        like = f"%{q.strip().lower()[:100]}%"
        filters.append(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
    if status:
        filters.append(User.status == Status(status))
    if verified is not None:
        filters.append(User.email_verified.is_(bool(verified)))
    if role:
        filters.append(User.role == Role(role))
    if registered_from:
        filters.append(User.created_at >= _day_start(registered_from))
    if registered_to:
        filters.append(User.created_at < _day_start(registered_to) + timedelta(days=1))

    sort_col = {"created_at": User.created_at, "last_login_at": User.last_login_at,
                "name": User.name, "email": User.email}.get(sort, User.created_at)
    sort_expr = sort_col.asc() if order == "asc" else sort_col.desc()

    total = db.scalar(select(func.count(User.id)).where(*filters))
    rows = db.execute(
        select(User, login_count, last_ip, credits, requests).where(*filters)
        .order_by(sort_expr.nulls_last(), User.id.desc())
        .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [user_summary(u, lc, ip, cr, rq) for u, lc, ip, cr, rq in rows],
            "total": total, "page": page, "page_size": page_size}


def user_detail(db, actor, user_id):
    require_admin(actor)
    u = _get_user(db, user_id)
    now = utcnow()

    ok = db.scalar(select(func.count(LoginEvent.id)).where(
        LoginEvent.user_id == u.id, LoginEvent.login_success.is_(True))) or 0
    failed = db.scalar(select(func.count(LoginEvent.id)).where(
        LoginEvent.user_id == u.id, LoginEvent.login_success.is_(False))) or 0
    events = db.scalars(select(LoginEvent).where(LoginEvent.user_id == u.id)
                        .order_by(LoginEvent.created_at.desc()).limit(20)).all()
    last_ok = next((e for e in events if e.login_success), None)

    sessions = db.scalars(select(Session).where(Session.user_id == u.id)
                          .order_by(Session.created_at.desc()).limit(10)).all()

    def session_state(s):
        if s.revoked_at:
            return "revoked"
        return "expired" if as_utc(s.expires_at) <= now else "active"

    active_sessions = sum(1 for s in sessions if session_state(s) == "active")

    by_provider = db.execute(
        select(UsageEvent.provider, func.count(UsageEvent.id),
               func.coalesce(func.sum(UsageEvent.credits_used), 0),
               func.coalesce(func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens), 0),
               func.coalesce(func.sum(UsageEvent.estimated_cost), 0.0))
        .where(UsageEvent.user_id == u.id).group_by(UsageEvent.provider)).all()
    provider_stats = {p: {"requests": n, "credits": c, "tokens": t, "cost": cost}
                      for p, n, c, t, cost in by_provider}
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    credits_month = db.scalar(select(func.coalesce(func.sum(UsageEvent.credits_used), 0)).where(
        UsageEvent.user_id == u.id, UsageEvent.created_at >= month_start)) or 0
    daily = _daily(db, select(func.date(UsageEvent.created_at), func.count(UsageEvent.id))
                   .where(UsageEvent.user_id == u.id,
                          UsageEvent.created_at >= now - timedelta(days=30))
                   .group_by(func.date(UsageEvent.created_at)))

    return {
        "profile": user_summary(u, ok, last_ok.ip_address if last_ok else None,
                                sum(v["credits"] for v in provider_stats.values()),
                                sum(v["requests"] for v in provider_stats.values())),
        "authentication": {
            "successful_logins": ok, "failed_logins": failed,
            "last_successful_login": _iso(last_ok.created_at) if last_ok else None,
            "online": bool(u.last_activity_at and
                           now - as_utc(u.last_activity_at) < timedelta(minutes=ONLINE_MINUTES)
                           and active_sessions),
            "active_sessions": active_sessions,
            "sessions": [{"id": s.id, "created_at": _iso(s.created_at),
                          "last_seen_at": _iso(s.last_seen_at), "expires_at": _iso(s.expires_at),
                          "ip_address": s.ip_address,
                          "device_info": auth.security.device_info(s.user_agent),
                          "state": session_state(s)} for s in sessions],
            "recent_login_events": [_event(e) for e in events],
        },
        "usage": {
            "ai_requests": sum(v["requests"] for v in provider_stats.values()),
            "credits_used": sum(v["credits"] for v in provider_stats.values()),
            "credits_this_month": credits_month,
            "ollama_requests": provider_stats.get("ollama", {}).get("requests", 0),
            "openai_requests": provider_stats.get("openai", {}).get("requests", 0),
            "estimated_cost_usd": round(sum(v["cost"] for v in provider_stats.values()), 4),
            "by_provider": provider_stats,
            "daily_requests": daily,
        },
        "subscription": {"plan": u.plan.value, **PLAN_LIMITS[u.plan.value]},
    }


def _daily(db, stmt):
    return [{"day": str(d), "count": n} for d, n in db.execute(stmt).all()]


# ---------------- ACTIONS ----------------
_STATUS_ACTIONS = {
    # target status: (audit action, allowed current statuses, super admin only)
    "SUSPENDED": ("USER_SUSPENDED", {Status.ACTIVE, Status.PENDING}, False),
    "ACTIVE": ("USER_ACTIVATED", {Status.SUSPENDED}, False),
    "DISABLED": ("USER_DISABLED", {Status.ACTIVE, Status.PENDING, Status.SUSPENDED}, True),
    "RESTORED": ("USER_RESTORED", {Status.DISABLED}, True),
}


def set_status(db, actor, user_id, new_status, reason="", ip=None):
    """new_status: SUSPENDED | ACTIVE (activate a suspended user) | DISABLED | RESTORED."""
    if new_status not in _STATUS_ACTIONS:
        raise ValueError("Unknown status")
    target = _get_user(db, user_id)
    action, allowed_from, super_only = _STATUS_ACTIONS[new_status]
    _require_can_manage(actor, target, super_only=super_only)
    if target.status not in allowed_from:
        raise ValueError(f"Can't change a {target.status.value} account this way.")
    old = target.status
    if new_status in ("ACTIVE", "RESTORED"):
        target.status = Status.ACTIVE if target.email_verified else Status.PENDING
    else:
        target.status = Status(new_status)
        auth.revoke_all_sessions(db, target.id)
    audit(db, actor, action, target.id, ip,
          f"{old.value} → {target.status.value}" + (f". Reason: {reason.strip()}" if reason else ""))
    return user_summary(target)


def set_role(db, actor, user_id, new_role, ip=None):
    target = _get_user(db, user_id)
    _require_can_manage(actor, target, super_only=True)
    role = Role(new_role)
    if role == Role.SUPER_ADMIN:
        raise PermissionDenied("Super admins can only be created from the server CLI.")
    old = target.role
    target.role = role
    auth.revoke_all_sessions(db, target.id)  # new permissions on next login
    audit(db, actor, "ROLE_CHANGED", target.id, ip, f"{old.value} → {role.value}")
    return user_summary(target)


def force_logout(db, actor, user_id, ip=None):
    target = _get_user(db, user_id)
    _require_can_manage(actor, target)
    count = auth.revoke_all_sessions(db, target.id)
    audit(db, actor, "SESSION_REVOKED", target.id, ip, f"{count} session(s) revoked")
    return count


def resend_verification(db, actor, user_id, ip=None):
    target = _get_user(db, user_id)
    _require_can_manage(actor, target)
    if target.email_verified:
        raise ValueError("This email is already verified.")
    sent = auth.send_verification_email(db, target)
    audit(db, actor, "VERIFICATION_EMAIL_RESENT", target.id, ip,
          "sent" if sent else "send failed")
    return sent


def trigger_password_reset(db, actor, user_id, ip=None):
    target = _get_user(db, user_id)
    _require_can_manage(actor, target)
    if target.status in (Status.SUSPENDED, Status.DISABLED):
        raise ValueError("Activate the account before sending a reset link.")
    sent = auth.send_reset_email(db, target)
    audit(db, actor, "PASSWORD_RESET_TRIGGERED", target.id, ip,
          "sent" if sent else "send failed")
    return sent


# ---------------- MONITORING ----------------
def login_events(db, actor, success=None, user_id=None, email=None, ip=None,
                 date_from=None, date_to=None, page=1, page_size=50):
    require_admin(actor)
    page, page_size = _page(page, page_size)
    f = []
    if success is not None:
        f.append(LoginEvent.login_success.is_(bool(success)))
    if user_id:
        f.append(LoginEvent.user_id == int(user_id))
    if email:
        f.append(LoginEvent.email.like(f"%{email.strip().lower()[:100]}%"))
    if ip:
        f.append(LoginEvent.ip_address == ip.strip()[:64])
    if date_from:
        f.append(LoginEvent.created_at >= _day_start(date_from))
    if date_to:
        f.append(LoginEvent.created_at < _day_start(date_to) + timedelta(days=1))
    total = db.scalar(select(func.count(LoginEvent.id)).where(*f))
    rows = db.scalars(select(LoginEvent).where(*f).order_by(LoginEvent.created_at.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_event(e) for e in rows], "total": total, "page": page,
            "page_size": page_size}


def audit_logs(db, actor, action=None, target_user_id=None, page=1, page_size=50):
    require_admin(actor)
    page, page_size = _page(page, page_size)
    admin_u, target_u = User.__table__.alias("a"), User.__table__.alias("t")
    f = []
    if action:
        f.append(AdminAuditLog.action == action)
    if target_user_id:
        f.append(AdminAuditLog.target_user_id == int(target_user_id))
    total = db.scalar(select(func.count(AdminAuditLog.id)).where(*f))
    rows = db.execute(
        select(AdminAuditLog, admin_u.c.email, target_u.c.email)
        .outerjoin(admin_u, admin_u.c.id == AdminAuditLog.admin_user_id)
        .outerjoin(target_u, target_u.c.id == AdminAuditLog.target_user_id)
        .where(*f).order_by(AdminAuditLog.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_audit_row(a, ae, te) for a, ae, te in rows], "total": total,
            "page": page, "page_size": page_size}


def overview(db, actor, days=30):
    require_admin(actor)
    now = utcnow()
    since = now - timedelta(days=days)
    week = now - timedelta(days=7)
    day = now - timedelta(days=1)

    counts = db.execute(select(
        func.count(User.id),
        func.sum(case((User.status == Status.ACTIVE, 1), else_=0)),
        func.sum(case((User.created_at >= week, 1), else_=0)),
        func.sum(case((User.email_verified.is_(True), 1), else_=0)),
        func.sum(case((User.status == Status.SUSPENDED, 1), else_=0)),
        func.sum(case((User.status == Status.PENDING, 1), else_=0)),
    )).one()
    active_sessions = db.scalar(select(func.count(Session.id)).where(
        Session.revoked_at.is_(None), Session.expires_at > now)) or 0
    online = db.scalar(select(func.count(func.distinct(Session.user_id))).where(
        Session.revoked_at.is_(None), Session.expires_at > now,
        Session.last_seen_at >= now - timedelta(minutes=ONLINE_MINUTES))) or 0
    logins_24h = db.scalar(select(func.count(LoginEvent.id)).where(
        LoginEvent.created_at >= day)) or 0
    failed_24h = db.scalar(select(func.count(LoginEvent.id)).where(
        LoginEvent.created_at >= day, LoginEvent.login_success.is_(False))) or 0
    ai_requests, credits, cost = db.execute(select(
        func.count(UsageEvent.id), func.coalesce(func.sum(UsageEvent.credits_used), 0),
        func.coalesce(func.sum(UsageEvent.estimated_cost), 0.0))).one()

    d_login = func.date(LoginEvent.created_at)
    login_daily = db.execute(
        select(d_login, func.sum(case((LoginEvent.login_success.is_(True), 1), else_=0)),
               func.sum(case((LoginEvent.login_success.is_(False), 1), else_=0)))
        .where(LoginEvent.created_at >= since).group_by(d_login).order_by(d_login)).all()
    d_usage = func.date(UsageEvent.created_at)
    d_session = func.date(Session.last_seen_at)
    return {
        "cards": {
            "total_customers": counts[0] or 0, "active_customers": counts[1] or 0,
            "new_customers_7d": counts[2] or 0, "verified_customers": counts[3] or 0,
            "unverified_customers": (counts[0] or 0) - (counts[3] or 0),
            "suspended_customers": counts[4] or 0, "pending_customers": counts[5] or 0,
            "active_sessions": active_sessions, "online_now": online,
            "login_attempts_24h": logins_24h, "failed_logins_24h": failed_24h,
            "ai_requests": ai_requests or 0, "credits_consumed": credits or 0,
            "estimated_cost_usd": round(cost or 0.0, 4),
        },
        "registrations_daily": _daily(db, select(func.date(User.created_at), func.count(User.id))
                                      .where(User.created_at >= since)
                                      .group_by(func.date(User.created_at))
                                      .order_by(func.date(User.created_at))),
        "active_users_daily": _daily(db, select(d_usage, func.count(func.distinct(UsageEvent.user_id)))
                                     .where(UsageEvent.created_at >= since)
                                     .group_by(d_usage).order_by(d_usage)),
        "sessions_daily": _daily(db, select(d_session, func.count(func.distinct(Session.user_id)))
                                 .where(Session.last_seen_at >= since)
                                 .group_by(d_session).order_by(d_session)),
        "logins_daily": [{"day": str(d), "success": s or 0, "failed": f or 0}
                         for d, s, f in login_daily],
        "ai_usage_daily": _daily(db, select(d_usage, func.count(UsageEvent.id))
                                 .where(UsageEvent.created_at >= since)
                                 .group_by(d_usage).order_by(d_usage)),
        "feature_usage": [{"feature": f, "requests": n, "credits": c}
                          for f, n, c in db.execute(
                              select(UsageEvent.feature, func.count(UsageEvent.id),
                                     func.coalesce(func.sum(UsageEvent.credits_used), 0))
                              .group_by(UsageEvent.feature)).all()],
        "model_usage": [{"model": m, "provider": p, "requests": n, "credits": c}
                        for m, p, n, c in db.execute(
                            select(UsageEvent.model, UsageEvent.provider,
                                   func.count(UsageEvent.id),
                                   func.coalesce(func.sum(UsageEvent.credits_used), 0))
                            .group_by(UsageEvent.model, UsageEvent.provider)).all()],
    }
