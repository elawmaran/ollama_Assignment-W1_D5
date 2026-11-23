# ELAW AI: authentication architecture

Email + password sign-in with email verification. No SMS, no OTP on every login, no paid
auth provider, and the AI is never used for auth. The only outside service is the SMTP
mailbox you configure, and it only sends verification and password-reset emails.

## Components

```
Browser ──► Caddy (ai.elawmaran.tech; llm.elawmaran.tech too until retired; HTTPS)
             ├─ /login /register /verify-email /resend-verification
             │  /forgot-password /reset-password /logout /auth-assets/*  ─┐
             ├─ /api/auth/*  /api/admin/*                                 ├─► elaw-auth (FastAPI, :8000)
             └─ everything else (chat, /admin, websockets)  ─► elaw-ai (Streamlit, :8501)
                                                                │
                      both use elaw_core (one service layer) ───┴─► elaw-db (PostgreSQL 16)
elaw-auth ──► SMTP mailbox (verification + reset emails only)
elaw-ai   ──► AI gateway (elaw_core/usage.py) ──► Ollama   [OpenAI route exists, blocked by COST_MODE=free]
```

| Path | Role |
|---|---|
| `elaw_core/models.py` | Tables (SQLAlchemy) |
| `elaw_core/auth.py` | Register, verify, login, sessions, reset |
| `elaw_core/admin.py` | Admin service with permission checks and audit log |
| `elaw_core/security.py` | Argon2id, tokens, validation, rate limiter |
| `elaw_core/email.py` | `EmailService` → `SMTPEmailProvider` / `ConsoleEmailProvider` (dev) / your future provider |
| `elaw_core/usage.py` | Model router, no-cost guard, usage tracker, credits, plans |
| `elaw_core/cli.py` | Creates admins from the server shell |
| `auth_api/main.py` | FastAPI app: HTML auth pages + JSON APIs |
| `streamlit_auth.py` | Streamlit reads the session cookie and checks it in the DB on every run |
| `views/chat.py`, `views/admin.py` | Chat page and admin dashboard |
| `migrations/` | Alembic migrations |

## Flows

**Registration** (`/register`, `POST /api/auth/register`)
1. Validate name, email and password strength. Normalize the email (trim + lowercase).
2. Reject a duplicate email.
3. Hash the password with Argon2id. Create the user as `PENDING` with `email_verified=false`.
4. Generate a 256-bit random token and store only its SHA-256 hash. It expires in 30 minutes.
5. Email `https://<domain>/verify-email?token=…`.

**Email verification** (`GET /verify-email?token=`, `POST /api/auth/verify-email`)
1. Look up the token by its hash. Reject it if unknown, used or expired.
2. Mark it used, set `email_verified=true`, and change `PENDING` to `ACTIVE`.
3. Redirect to `/login?notice=verified`.

Asking for a new link invalidates every older link. "Resend verification email" is
rate-limited and gives the same answer whether or not the email exists.

**Login** (`/login`, `POST /api/auth/login`)
1. Rate-limit by IP.
2. Lock out after 5 wrong passwords in 15 minutes for that email. While locked, even the
   right password is refused.
3. Verify the Argon2id hash. An unknown email costs the same time as a real one.
4. Only after the password is correct, check the account: disabled, suspended, then unverified.
5. Create a session: a random token stored as a hash. Set it in the `elaw_session` cookie
   (HttpOnly, Secure, SameSite=Lax, 7 days).
6. Record a `login_events` row for every attempt, successful or not.

**Session checks**
- Every Streamlit run and every API call looks the session up in the DB.
- It must be unrevoked and unexpired, and the user must be `ACTIVE` and verified. So a
  suspension, a forced logout or a role change takes effect on the user's next click.
- `POST /api/auth/refresh` swaps the token for a new one and extends the session.
- `POST /logout` and `POST /api/auth/logout` revoke the session.

**Forgot / reset password** (`/forgot-password`, `/reset-password?token=`)
- Same token rules as verification: random, stored hashed, 30 minutes, single use.
- The forgot form gives the same answer for unknown emails, and is rate-limited by email
  and by IP.
- A successful reset logs the user out everywhere. Because the user proved they own the
  address, it also marks the email verified.

**UI states covered:** registration successful, verification email sent, email verified,
invalid, expired or used verification link, reset email sent, invalid, expired or used
reset link, invalid login, account suspended, account disabled, email not verified (with a
resend link), too many attempts.

## Database schema

| Table | Key columns |
|---|---|
| `users` | id, name, email (unique), password_hash, email_verified, status (PENDING/ACTIVE/SUSPENDED/DISABLED), role (USER/ADMIN/SUPER_ADMIN), plan (FREE…ENTERPRISE), created_at, updated_at, last_login_at, last_activity_at |
| `email_verification_tokens` | id, user_id → users (cascade), token_hash (unique), expires_at, used_at, created_at |
| `password_reset_tokens` | same as above |
| `sessions` | id, user_id → users (cascade), token_hash (unique), ip_address, user_agent, expires_at, revoked_at, last_seen_at, created_at |
| `login_events` | id, user_id → users (set null), email, login_success, ip_address, user_agent, device_info, failure_reason, created_at |
| `admin_audit_logs` | id, admin_user_id, action, target_user_id (both → users, set null), ip_address, description, created_at |
| `usage_events` | id, user_id, request_id (unique), model, provider, feature, input_tokens, output_tokens, estimated_cost, credits_used, created_at |

There are indexes on every foreign key, users.status, users.role, users.created_at,
users.last_login_at, login_events (email, created_at), login_events.ip_address,
usage_events (user_id, created_at), and every created_at used for charts. The migration
is `migrations/versions/0001_auth_admin_usage_tables.py`.

## Security model

- **Passwords:** Argon2id (19 MiB, t=2, p=1, OWASP's minimum profile, chosen because RAM
  on the server is tight). Hashes are upgraded automatically on login. Passwords need 10+
  characters with a letter and a number, and common passwords are blocked.
- **Tokens and sessions:** `secrets.token_urlsafe(32)`, stored only as SHA-256. Short-lived
  and single-use.
- **Cookies:** the session cookie is HttpOnly, Secure (production) and SameSite=Lax.
- **CSRF:** every POST/PATCH needs a double-submit token, either as the `csrf_token` form
  field or the `X-CSRF-Token` header, matching the `elaw_csrf` cookie. On top of that, a
  request whose Origin header names another site is rejected. A `null` Origin, which some
  browsers and privacy tools send, falls back to the token check.
- **CORS:** off by default. Only `ALLOWED_ORIGINS` are allowed.
- **Headers:** strict CSP (no inline scripts), `X-Frame-Options: DENY`,
  `Referrer-Policy: same-origin` (so tokens in URLs never leak to other sites), nosniff, HSTS, and
  `Cache-Control: no-store`.
- **Rate limits:** login 20 per 5 minutes per IP, plus the per-email lockout. Register 5
  per hour per IP. Forgot password and resend verification 3 per 15 minutes per email and
  10 per hour per IP. Reset 10 per 15 minutes per IP.
- **Logging:** uvicorn runs with `--no-access-log`. The app logs method, path and status
  only, never query strings, passwords, tokens or SMTP credentials. SMTP errors log only
  the error type.
- **Injection and XSS:** SQLAlchemy uses bound parameters everywhere. Jinja2 auto-escapes
  output. Streamlit HTML snippets escape user values. Notices come from a fixed list and
  never echo the URL. The redirect after login only goes to the configured `APP_URL` with
  a relative path.
- **API responses:** they use explicit safe fields. `password_hash`, token hashes and raw
  tokens never appear, and tests check this.

## SMTP configuration and cost

The app itself has no auth costs. Email goes through whichever free mailbox you configure:

| Provider (free tier) | Settings | Limit to know about |
|---|---|---|
| **Gmail** (recommended to start) | `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME`/`SMTP_FROM_EMAIL`=your Gmail, `SMTP_PASSWORD`=a 16-character **App Password** (needs 2-Step Verification) | About 500 emails a day. Bulk sending can get the account paused. |
| Brevo free | `smtp-relay.brevo.com:587`, SMTP key | About 300 emails a day on the free plan |
| Zoho Mail free | `smtp.zoho.com:587` or `smtp.zoho.in:587` | Free plan limits apply, and it needs your own domain |

`EMAIL_DAILY_LIMIT` (default 400) is a built-in cap that keeps you under the free limit.
After that, emails are refused for the rest of the day instead of risking the mailbox.
Each user gets at most about 2 emails at sign-up and one per reset request, and both are
rate-limited, so a free mailbox covers thousands of users a month. Check your provider's
current terms, because they can change.

## Environment variables

See `.env.example` for the full list. The ones that matter in production:
- `APP_ENV=production`
- `PUBLIC_URL` and `APP_URL`, both `https://ai.elawmaran.tech`. When they are equal, links between
  the auth pages and the app are relative, so any domain pointing at the server works.
- `SECURE_COOKIES=true` and `TRUST_PROXY=true`
- `DATABASE_URL`, built by compose from `POSTGRES_PASSWORD`
- `EMAIL_PROVIDER=smtp` and the `SMTP_*` settings
- `COST_MODE=free`

## Local development

```bash
cd ollama_python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # defaults: SQLite + console email
alembic upgrade head
python -m elaw_core.cli create-admin --email you@example.com --name "You"
uvicorn auth_api.main:app --port 8000 --no-access-log     # terminal 1
streamlit run app.py                                        # terminal 2
```

Open http://localhost:8000/register. Verification and reset links are printed in terminal 1.
Then use the chat at http://localhost:8501 and the admin dashboard at http://localhost:8501/admin.

**Tests:** run `pytest tests -q` for SQLite. To run against PostgreSQL, set
`TEST_DATABASE_URL=postgresql+psycopg://…` first. The tests drop and recreate the schema.

## Production deployment

See `DEPLOY.md`.
