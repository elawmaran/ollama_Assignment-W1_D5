# ELAW AI: admin dashboard architecture

## Where it lives

- **UI:** Streamlit page `views/admin.py`, served at `/admin`. Sections are picked in the
  sidebar and kept in the URL:
  - `/admin?section=Customers`
  - `/admin?user=<id>` opens a customer's detail page
- **API:** FastAPI `/api/admin/*` in `auth_api/main.py`.
- **Rules:** both call `elaw_core/admin.py`. That one service layer holds every permission
  check, so neither frontend can skip one.

## Three layers of protection

1. **Navigation:** `app.py` only registers the `/admin` page for ADMIN and SUPER_ADMIN users.
   For anyone else the page doesn't exist.
2. **Page:** `streamlit_auth.require_admin()` re-checks the session and role in the DB at
   the top of the admin page, on every run.
3. **Service:** every function in `elaw_core/admin.py` calls `require_admin(actor)` and,
   for actions, `_require_can_manage`. The API routes also depend on `require_admin`.
   Responses are 401 when logged out and 403 for customers.

Hiding the menu is never the only protection. Tests show customers get 403 on every admin
endpoint, and that the service refuses customers even when called directly.

## Roles and permissions

| Action | ADMIN | SUPER_ADMIN |
|---|---|---|
| View overview, customers, details, login events, audit log, usage | ✅ | ✅ |
| Suspend / activate a USER | ✅ | ✅ |
| Force logout, resend verification, send password reset (USER) | ✅ | ✅ |
| Do the above to an ADMIN | ❌ | ✅ |
| Disable / restore an account | ❌ | ✅ |
| Change role USER ↔ ADMIN | ❌ | ✅ |
| Create SUPER_ADMIN, or act on a SUPER_ADMIN | ❌ (CLI only) | ❌ (CLI only) |
| Act on yourself | ❌ | ❌ |

Changing a role or suspending or disabling an account revokes that user's sessions, so the
change applies immediately.

**Never possible for any admin:** viewing passwords or hashes, viewing raw or hashed
verification or reset tokens, viewing session tokens or secrets, or impersonating users.

## Status lifecycle

```
register ─► PENDING ──verify email──► ACTIVE ◄──activate── SUSPENDED
                                        │  ▲                   ▲
                                        │  └──restore── DISABLED
                                        └──suspend─────────────┘   (disable: super admin, from any state)
```

## Dangerous actions

Suspend, activate, disable, restore, force logout and role change all open a confirmation
dialog. It shows a warning and needs a ticked "I understand" box. Suspend and disable also
ask for a reason, which goes into the audit log. The API equivalent requires
`"confirm": true` in the request body.

## Audit log (`admin_audit_logs`)

Every action writes one row in the same transaction as the change. It records the admin's
ID, the action, the target customer's ID, the IP address, a description (old → new, plus
the reason) and the time.

Actions: `USER_SUSPENDED`, `USER_ACTIVATED`, `USER_DISABLED`, `USER_RESTORED`,
`ROLE_CHANGED`, `SESSION_REVOKED`, `VERIFICATION_EMAIL_RESENT`, `PASSWORD_RESET_TRIGGERED`.
The app has no way to edit or delete these rows.

## Login monitoring (`login_events`)

Every login attempt is recorded, whatever the outcome. Each row has the email, the user
(when known), success or failure, the IP address, the user agent, a short device summary
("Chrome on macOS") and a failure reason. The reasons are: `wrong_password`,
`unknown_email`, `locked`, `rate_limited`, `unverified`, `suspended` and `disabled`.

- **Login events page:** filter by result, email, IP and date range.
- **Security events page:** shows only the failures.

## Dashboard sections

- **Overview:**
  - Cards: total, active, new (7 days), online now, active sessions, verified,
    unverified, login attempts and failed logins (24h), AI requests, credits and cost.
  - Charts (last 30 days): registrations, daily active users, logins (success vs failed)
    and AI requests per day.
- **Customers:**
  - Search by name or email. Filter by status, verified, role and registration date
    range. Sort by registration date or last login.
  - Server-side pagination: 25 per page, and the API is capped at 100.
  - Columns: ID, name, email, verified, status, role, registered, last login, logins,
    last login IP, plan, AI requests, credits.
  - Selecting a row opens that customer.
- **Customer detail:**
  - Profile.
  - Authentication: login counts, sessions with their state, recent events, devices
    and IPs.
  - Usage: requests, credits, Ollama vs OpenAI, estimated cost, this month against the
    plan limit, a daily chart.
  - Subscription.
  - Action buttons, depending on your permissions.
- **AI usage:** totals, a daily chart and a per-model table.
- **Subscriptions:** the FREE, PERSONAL, PRO, BUSINESS and ENTERPRISE structure. No
  payments are processed.
- **Settings:** a read-only view of the safe settings. Secrets show only as configured yes/no.

## Usage and credit system (separate from auth)

`views/chat.py` → `usage.route(model)` (the no-cost guard) → Ollama → `usage.record_usage(...)`

- Each record stores user_id, request_id, model, provider, feature (`chat` or
  `deep_thinking`), input and output tokens, estimated cost and credits.
- 1 credit = 1,000 tokens. Ollama costs $0.
- `COST_MODE=free` makes the router refuse any paid provider (OpenAI and similar) before
  a request is sent.
- Plan limits are shown but not enforced yet.

## Admin setup

Super admins are created only from the server shell, never from the web:

```bash
docker exec -it elaw-auth python -m elaw_core.cli create-admin --email you@example.com --name "You"
# promote/demote later:
docker exec -it elaw-auth python -m elaw_core.cli promote --email someone@example.com --role ADMIN
```

The command asks for the password without echoing it. You then log in at `/login` and
open `/admin`, or use "Admin dashboard" in the sidebar.

## Privacy

The app stores only a name, an email and the security metadata listed above. Chat text is
not stored, only token counts. Admins see safe metadata only, every action they take is
audited, and the API and pages follow least privilege as described above.
