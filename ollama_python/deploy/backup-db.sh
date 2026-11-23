#!/usr/bin/env bash
# Nightly ELAW AI database backup (runs on the server from /etc/cron.d/elaw-backup).
# Writes a compressed, verified dump and keeps the last KEEP_DAYS days of them.
#   Manual run:   /opt/elaw-ai/ollama_python/deploy/backup-db.sh
#   Copy to Mac:  deploy/pull-backups.sh   (run on the Mac)
set -euo pipefail
DIR="${BACKUP_DIR:-/root/elaw-backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
CONTAINER="${DB_CONTAINER:-elaw-db}"

umask 077
mkdir -p "$DIR"
FILE="$DIR/elaw-db-$(date -u +%Y%m%d-%H%M).sql.gz"
trap 'rm -f "$FILE.tmp"' EXIT

docker exec "$CONTAINER" pg_dump -U elaw elaw | gzip > "$FILE.tmp"   # pipefail: a failed dump stops here
gunzip -t "$FILE.tmp"                                                  # archive is readable
gunzip -c "$FILE.tmp" | grep -q "^CREATE TABLE public.users"           # and holds the data
mv "$FILE.tmp" "$FILE"

# Retention: delete only this script's own dumps, older than KEEP_DAYS.
find "$DIR" -maxdepth 1 -type f -name 'elaw-db-*.sql.gz' -mtime +"$KEEP_DAYS" -print -delete

echo "$(date -u +%FT%TZ) backup ok: $FILE ($(stat -c %s "$FILE") bytes, $(ls "$DIR"/elaw-db-*.sql.gz | wc -l) kept)"
