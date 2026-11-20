# Deploying ELAW AI on Hostinger KVM 2 → llm.elawmaran.tech

Server: `srv2091969.hstgr.cloud` (Hostinger KVM 2: 2 vCPU, 8 GB RAM, 100 GB NVMe).
No extra cost: everything runs on the VPS you already pay for, with open-source software.

**Model: `qwen3:8b`.** `qwen3:14b` needs ~10–12 GB RAM and won't fit in 8 GB.
On 2 CPU cores expect roughly 2–4 tokens/sec. If that's too slow, `qwen3:4b` is about twice as fast.

## 1. DNS (hPanel)

hPanel → **Domains → elawmaran.tech → DNS / Nameservers** → add:

| Type | Name | Points to | TTL |
|---|---|---|---|
| A | `llm` | your VPS IP (hPanel → VPS → Overview) | 3600 |

## 2. Log in and prepare the server

hPanel → VPS → **Browser terminal**, or from your Mac:

```bash
ssh root@srv2091969.hstgr.cloud
```

Then:

```bash
apt update && apt upgrade -y
apt install -y python3-venv git nginx ufw

# 4 GB swap: a safety net so the 8 GB server never crashes on memory spikes
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Firewall: SSH + web only (Ollama's port 11434 stays private)
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

If hPanel → VPS → **Firewall** has rules enabled, allow 22, 80 and 443 there too.

## 3. Install Ollama and the model

```bash
curl -fsSL https://ollama.com/install.sh | sh

# Only keep one model in memory, and keep it loaded between chats
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Service]
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_KEEP_ALIVE=30m
EOF
systemctl daemon-reload && systemctl restart ollama

ollama pull qwen3:8b
```

## 4. Install the app

```bash
git clone https://github.com/elawmaran/ollama_Assignment-W1_D5.git /opt/elaw-ai
cd /opt/elaw-ai/ollama_python
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## 5. Run it as a service (auto-restarts, survives reboots)

```bash
cat > /etc/systemd/system/elaw-ai.service <<'EOF'
[Unit]
Description=ELAW AI (Streamlit)
After=network.target ollama.service

[Service]
WorkingDirectory=/opt/elaw-ai/ollama_python
Environment=DEFAULT_MODEL=qwen3:8b
Environment=MAX_CONCURRENT=1
ExecStart=/opt/elaw-ai/ollama_python/.venv/bin/streamlit run app.py --server.address 127.0.0.1 --server.port 8501
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now elaw-ai
```

## 6. Nginx + free HTTPS

```bash
cat > /etc/nginx/sites-available/elaw-ai <<'EOF'
server {
    server_name llm.elawmaran.tech;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;     # Streamlit needs websockets
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 600s;                    # slow CPU answers
    }
}
EOF
ln -s /etc/nginx/sites-available/elaw-ai /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

apt install -y certbot python3-certbot-nginx
certbot --nginx -d llm.elawmaran.tech
```

Open https://llm.elawmaran.tech.

## Keeping it stable

- **One answer at a time** (`MAX_CONCURRENT=1`). Other visitors see "You're next in line…" instead of the server overloading.
- **Model stays loaded** for 30 minutes after each chat, so replies start faster.
- **Inputs are capped** at 4,000 characters, and only the last 20 messages are sent to the model.
- **Update:** `cd /opt/elaw-ai && git pull && systemctl restart elaw-ai`
- **Logs:** `journalctl -u elaw-ai -f` and `journalctl -u ollama -f`
- **Memory check:** `free -h` (the model should use ~6 GB while answering)
