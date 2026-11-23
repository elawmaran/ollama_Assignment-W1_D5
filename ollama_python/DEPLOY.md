# ELAW AI: live deployment

**Live at https://ai.elawmaran.tech** (main domain since 2026-11-23; https://llm.elawmaran.tech serves the same app until it is retired) on the Hostinger KVM 2 VPS (`srv2091969.hstgr.cloud`,
201.18.199.87; 2 vCPU, 7.8 GB RAM, 4 GB swap).

The server also runs the **travel-crm** Docker stack (`/opt/travel-crm`). Its Caddy, Ollama and Docker network are shared with ELAW AI, so never run
`docker compose down` on travel-crm. Since 2026-11-23 the Travel CRM is no longer served publicly
(its containers and database volume are kept). ELAW AI plugs into that stack instead of running
its own web server or Ollama:

```
Internet ──► travel-crm-caddy-1 (ports 80/443, automatic HTTPS)
               ├─ http://<IP>         ──► redirect to https://ai.elawmaran.tech
               ├─ qa.elawmaran.tech   ──► qa-site (DNS now points to GitHub Pages)
               └─ ai.elawmaran.tech + llm.elawmaran.tech (same routing)
                    ├─ /login /register /verify-email /forgot-password /reset-password
                    │  /resend-verification /logout /auth-assets/* /api/*  ──► elaw-auth:8000 (FastAPI)
                    └─ everything else (chat, /admin)                       ──► elaw-ai:8501 (Streamlit)
                                   both ──► elaw-db (PostgreSQL 16, not exposed to the internet)
                                   elaw-ai ──► travel-crm-ollama-1 (shared Ollama, http://ollama:11434)
                                                 qwen3:4b-instruct → ELAW AI, always loaded
                                                 qwen2.5:3b        → used by travel-crm
```

| Piece | Where |
|---|---|
| Code | `/opt/elaw-ai` (clone of this repo) |
| Secrets | `/opt/elaw-ai/ollama_python/.env` (chmod 600, never committed) |
| Containers | `elaw-ai` (768 MB), `elaw-auth` (256 MB), `elaw-db` (256 MB), all on network `travel-crm_default`, via `docker-compose.yml` |
| Database data | Docker volume `elaw_elaw-db-data` |
| HTTPS / routing | `ai.` + `llm.` blocks in `/opt/travel-crm/Caddyfile` (see `deploy/Caddyfile.elaw`) |
| DNS | A records `ai` and `llm` → 201.18.199.87 |

Auth design: `AUTH_ARCHITECTURE.md`. Admin dashboard: `ADMIN_ARCHITECTURE.md`.

## First-time setup of login + admin (one time)

```bash
ssh root@201.18.199.87
free -h                                   # need ~400 MB free for elaw-auth + elaw-db
cd /opt/elaw-ai && git pull && cd ollama_python

# 1. Secrets
cp .env.example .env && chmod 600 .env
#    edit .env: POSTGRES_PASSWORD=<long random>, EMAIL_PROVIDER=smtp, SMTP_* (Gmail App Password)
#    tip: openssl rand -base64 32   for POSTGRES_PASSWORD

# 2. Replace the old single container with the compose stack
docker rm -f elaw-ai
docker compose up -d --build              # elaw-db → elaw-auth (runs migrations) → elaw-ai
docker compose ps

# 3. Route auth paths in Caddy (zero downtime, see "Caddy notes" below)
cp /opt/travel-crm/Caddyfile /opt/travel-crm/Caddyfile.bak-$(date +%F)
#    add the ELAW blocks from deploy/Caddyfile.elaw, edit IN PLACE (keep the inode)
docker cp /opt/travel-crm/Caddyfile travel-crm-caddy-1:/tmp/Caddyfile.new
docker exec travel-crm-caddy-1 caddy validate --config /tmp/Caddyfile.new --adapter caddyfile
docker exec -w /etc/caddy travel-crm-caddy-1 caddy reload --config /tmp/Caddyfile.new --adapter caddyfile

# 4. Create your super admin (password is asked, never echoed)
docker exec -it elaw-auth python -m elaw_core.cli create-admin --email <you> --name "<Your Name>"
```

Then open https://ai.elawmaran.tech, log in, and use **Admin dashboard** in the sidebar.

## Update the live app after pushing to GitHub

```bash
ssh root@201.18.199.87
cd /opt/elaw-ai && git pull && cd ollama_python
docker compose up -d --build              # migrations run automatically in elaw-auth
```

Database backup: `docker exec elaw-db pg_dump -U elaw elaw | gzip > /root/elaw-$(date +%F).sql.gz`

## Rollback

```bash
cd /opt/elaw-ai && git log --oneline -5           # pick the last good commit
git checkout <good-commit> -- ollama_python && cd ollama_python
docker compose up -d --build                      # rebuild that version
# Database changes are additive (new nullable columns). To undo one anyway:
docker exec elaw-auth alembic downgrade <previous-revision>   # e.g. 0001
```
Prompt Builder only (keep everything else): set `GUEST_DAILY_LIMIT=0` and `FREE_DAILY_LIMIT=0`
in `.env`, then `docker compose up -d elaw-ai`. Every build is then refused politely.

## Settings (environment variables)

| Variable | Live value | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://ollama:11434` | travel-crm's Ollama on the Docker network |
| `DEFAULT_MODEL` | `qwen3:4b-instruct` | first words in ~2–5 s, ~11 tok/s |
| `THINKING_MODEL` | *(unset)* | Deep thinking uses the same model via `<reasoning>` tags, so no second model competes for RAM |
| `KEEP_ALIVE` | `-1` (default) | model stays in memory forever; `warm.py` loads it when the container starts |
| `MAX_ANSWER_TOKENS` | `700` (default) | caps answer length so CPU replies stay quick |
| `ALLOWED_MODELS` | `qwen3:4b-instruct` | models visitors can pick (hides travel-crm's model) |
| `MAX_CONCURRENT` | `1` | one answer at a time; others see "You're next in line…" |
| `COST_MODE` | `free` | paid AI providers are refused; AI cost stays $0 |
| Prompt Builder limits | `GUEST_DAILY_LIMIT=5`, `FREE_DAILY_LIMIT=20` (defaults) | see `PROMPT_BUILDER.md` |
| Auth settings | in `.env` | `POSTGRES_PASSWORD`, `SMTP_*`, etc.; see `.env.example` and `AUTH_ARCHITECTURE.md` |

## Useful commands

```bash
docker logs -f elaw-ai                          # chat app logs
docker logs -f elaw-auth                        # auth logs (paths only, never tokens)
docker exec travel-crm-ollama-1 ollama ps       # which models are loaded
free -h                                         # memory
curl -s -o /dev/null -w "%{http_code}\n" https://ai.elawmaran.tech/_stcore/health
```

## Caddy notes (important)

- The Caddyfile is bind-mounted **read-only** into `travel-crm-caddy-1`. If the file on disk is
  ever replaced (new inode), the running container keeps reading the old copy until it is
  restarted. On 2026-11-20 the disk copy was missing the `qa.elawmaran.tech` block; it now
  matches the live config (travel-crm + qa + llm). Backups: `/opt/travel-crm/Caddyfile.bak-*`.
- To apply Caddyfile changes without downtime:

  ```bash
  docker cp /opt/travel-crm/Caddyfile travel-crm-caddy-1:/tmp/Caddyfile.new
  docker exec travel-crm-caddy-1 caddy validate --config /tmp/Caddyfile.new --adapter caddyfile
  docker exec -w /etc/caddy travel-crm-caddy-1 caddy reload --config /tmp/Caddyfile.new --adapter caddyfile
  ```
- If travel-crm's Caddyfile lives in another repo you redeploy from, add the
  ELAW blocks there too (see `deploy/Caddyfile.elaw`), or a redeploy will drop them.

## Speed and memory

Measured 2026-11-21: a **cold** model load takes ~75 s on this server, while a **loaded** model
starts answering in ~0.2 s. So ELAW AI's model is kept in memory permanently (`ollama ps` shows
`Forever`). qwen3:4b-instruct (3.6 GB) and travel-crm's qwen2.5:3b (2.4 GB) fit together.
Don't add a second ELAW model: loading it pushes the others out and causes the slow reloads.
A bigger model (e.g. 8B) would run at about half the speed on 2 vCPUs.

## Domain move: llm. → ai. (2026-11-23)

- Routing: one shared `(elaw_app)` Caddy snippet imported by both `ai.` and `llm.`. Plain-IP HTTP
  redirects to `https://ai.elawmaran.tech`.
- Links between the auth pages and the app are relative when `PUBLIC_URL == APP_URL`, so both
  domains work at once. Emails use `PUBLIC_URL` (now ai.).
- Logins are per domain, so users sign in once on ai.
- Backups taken before the move are in `/root/backup-ai-migration-20261123-1202/` (both
  databases, the Caddyfile and both .env files). The CRM database is also copied to the Mac at
  `~/elaw-backups/`.

**Roll back to the Travel CRM on ai.:**
```bash
B=/root/backup-ai-migration-20261123-1202
cat $B/Caddyfile > /opt/travel-crm/Caddyfile          # in place: keeps the inode
docker cp /opt/travel-crm/Caddyfile travel-crm-caddy-1:/tmp/Caddyfile.new
docker exec travel-crm-caddy-1 caddy validate --config /tmp/Caddyfile.new --adapter caddyfile
docker exec -w /etc/caddy travel-crm-caddy-1 caddy reload --config /tmp/Caddyfile.new --adapter caddyfile
```

**Retire llm. later:** replace `llm.elawmaran.tech { import elaw_app }` with
`llm.elawmaran.tech { redir https://ai.elawmaran.tech{uri} 301 }`, then reload as above. Old email
links and bookmarks keep working through the redirect. Keep the redirect for a few months before
removing the block and the DNS record.
