# ELAW AI: live deployment

**Live at https://llm.elawmaran.tech** on the Hostinger KVM 2 VPS (`srv2091969.hstgr.cloud`,
201.18.199.87; 2 vCPU, 7.8 GB RAM, 4 GB swap).

The server also runs the **travel-crm** Docker stack (`/opt/travel-crm`), which serves
`ai.elawmaran.tech` and `qa.elawmaran.tech`. ELAW AI plugs into that stack instead of running
its own web server or Ollama:

```
Internet ──► travel-crm-caddy-1 (ports 80/443, automatic HTTPS)
               ├─ ai.elawmaran.tech   ──► travel-crm frontend
               ├─ qa.elawmaran.tech   ──► qa-site
               └─ llm.elawmaran.tech  ──► elaw-ai:8501   (this app, Docker container)
                                            │
                                            ▼
                                travel-crm-ollama-1 (shared Ollama 0.6.8, http://ollama:11434)
                                  qwen3:4b-instruct  → ELAW AI, always loaded (normal + Deep thinking)
                                  qwen2.5:3b         → used by travel-crm
```

| Piece | Where |
|---|---|
| Code | `/opt/elaw-ai` (clone of this repo) |
| Container | `elaw-ai`, network `travel-crm_default`, memory limit 768 MB, restarts automatically |
| HTTPS / routing | `llm.elawmaran.tech` block in `/opt/travel-crm/Caddyfile` |
| DNS | A record `llm` → 201.18.199.87 |

## Update the live app after pushing to GitHub

```bash
ssh root@201.18.199.87
cd /opt/elaw-ai && git pull
cd ollama_python && docker build -t elaw-ai:latest .
docker rm -f elaw-ai
docker run -d --name elaw-ai --restart unless-stopped --network travel-crm_default \
  -e OLLAMA_HOST=http://ollama:11434 \
  -e DEFAULT_MODEL=qwen3:4b-instruct \
  -e ALLOWED_MODELS=qwen3:4b-instruct -e MAX_CONCURRENT=1 \
  --memory 768m elaw-ai:latest
```

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

## Useful commands

```bash
docker logs -f elaw-ai                          # app logs
docker exec travel-crm-ollama-1 ollama ps       # which models are loaded
free -h                                         # memory
curl -s -o /dev/null -w "%{http_code}\n" https://llm.elawmaran.tech/_stcore/health
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
  `llm.elawmaran.tech` block there too, or a redeploy will drop it.

## Speed and memory

Measured 2026-11-21: a **cold** model load takes ~75 s on this server, while a **loaded** model
starts answering in ~0.2 s. So ELAW AI's model is kept in memory permanently (`ollama ps` shows
`Forever`). qwen3:4b-instruct (3.6 GB) and travel-crm's qwen2.5:3b (2.4 GB) fit together.
Don't add a second ELAW model: loading it pushes the others out and causes the slow reloads.
A bigger model (e.g. 8B) would run at about half the speed on 2 vCPUs.
