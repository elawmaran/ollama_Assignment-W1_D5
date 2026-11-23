# Moving ELAW AI to a new server

Use this if the current VPS ends, or to host ELAW AI anywhere else. It takes about an hour.
Everything except your passwords and data is in this GitHub repo. Your data is in the
database backups (`~/elaw-backups/` on your Mac).

## What you need
- A Linux server (Ubuntu 22.04/24.04) with **at least 2 vCPU, 8 GB RAM and 20 GB disk**.
  The AI model alone uses about 3.6 GB of RAM.
- SSH access to it as root (or a sudo user).
- Your newest database backup: `~/elaw-backups/elaw-db-*.sql.gz` (run
  `deploy/pull-backups.sh` first if the old server still works).
- Access to your domain's DNS, and to Zoho Mail (for a new app password).

## Steps

### 1. Point the domain at the new server
At your DNS provider, set the **A record** for `ai` (ai.elawmaran.tech) to the new server's IP.
Do this first: HTTPS certificates are issued automatically once DNS points to the server.

### 2. Install Docker on the new server
```bash
ssh root@NEW_SERVER_IP
curl -fsSL https://get.docker.com | sh
```

### 3. Get the code
```bash
git clone https://github.com/elawmaran/ollama_Assignment-W1_D5.git /opt/elaw-ai
cd /opt/elaw-ai/ollama_python
```

### 4. Create the settings file
```bash
cp .env.example .env && chmod 600 .env
nano .env
```
Set at least these:

| Setting | Value |
|---|---|
| `DOMAIN` | `ai.elawmaran.tech` |
| `POSTGRES_PASSWORD` | a new long random value: run `openssl rand -hex 24` |
| `EMAIL_PROVIDER` | `smtp` |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.zoho.in` / `465` |
| `SMTP_USERNAME` / `SMTP_FROM_EMAIL` | `elawmaran@zohomail.in` |
| `SMTP_PASSWORD` | a **new Zoho app password** (accounts.zoho.in → Security → App Passwords) |

Everything else can stay as it is.

### 5. Start everything
```bash
docker compose -f docker-compose.standalone.yml up -d --build
docker compose -f docker-compose.standalone.yml exec ollama ollama pull qwen3:4b-instruct
```
The second command downloads the AI model (2.5 GB). It is the same model as before
(`qwen3:4b-instruct`, ID `0edcdef34593`).

### 6. Restore your data
Copy the backup from your Mac to the server, then restore it:
```bash
# on your Mac
scp ~/elaw-backups/elaw-db-YYYYMMDD-HHMM.sql.gz root@NEW_SERVER_IP:/root/
# on the server
cd /opt/elaw-ai/ollama_python
deploy/restore-db.sh /root/elaw-db-YYYYMMDD-HHMM.sql.gz
```
Type `RESTORE` when asked. All accounts, including your admin login, come back with their
old passwords. Users will need to log in again.

### 7. Turn on nightly backups
```bash
echo '30 3 * * * root /opt/elaw-ai/ollama_python/deploy/backup-db.sh >> /var/log/elaw-backup.log 2>&1' \
  > /etc/cron.d/elaw-backup
/opt/elaw-ai/ollama_python/deploy/backup-db.sh       # first backup now
```
On your Mac, edit `SERVER=` at the top of `deploy/pull-backups.sh` to the new IP.

### 8. Check it
- Open https://ai.elawmaran.tech. The padlock should show a valid certificate (this can take
  a minute after DNS updates).
- Log in with your admin account and open **Admin dashboard → Settings**. "Email sending"
  should say ✅ Configured.
- Send a chat message, and try the Prompt Builder.
- Register a test account with another email and check the verification email arrives.

## Good to know
- **HTTPS** is automatic: Caddy gets and renews Let's Encrypt certificates.
- **Speed** depends on the CPU. On 2 vCPU, answers come at about 9 words per second.
- **Updates later:** `git pull`, then `docker compose -f docker-compose.standalone.yml up -d --build`.
- **The Travel CRM** isn't part of this setup. Its code is in the private
  `travel-agency-crm` repo, and its data backup is in `~/elaw-backups/travel-crm-db-*.sql.gz`.
