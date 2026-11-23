#!/usr/bin/env bash
# Restore an ELAW AI database backup (.sql.gz) into the running stack.
# WARNING: replaces ALL current ELAW data (users, sessions, usage) with the backup's.
#
#   deploy/restore-db.sh ~/elaw-backups/elaw-db-YYYYMMDD-HHMM.sql.gz [compose-file]
#   compose-file defaults to docker-compose.standalone.yml (new server).
set -euo pipefail
FILE="${1:?usage: deploy/restore-db.sh <backup.sql.gz> [compose-file]}"
COMPOSE="${2:-docker-compose.standalone.yml}"
dc() { docker compose -f "$COMPOSE" "$@"; }

gunzip -t "$FILE"
echo "This REPLACES all ELAW data in the '$COMPOSE' stack with: $FILE"
read -r -p "Type RESTORE to continue: " answer
[ "$answer" = "RESTORE" ] || { echo "Cancelled."; exit 1; }

dc stop elaw-ai elaw-auth
dc up -d --wait elaw-db
dc exec -T elaw-db psql -U elaw -d postgres -v ON_ERROR_STOP=1 -q \
   -c "DROP DATABASE IF EXISTS elaw WITH (FORCE)" -c "CREATE DATABASE elaw OWNER elaw"
gunzip -c "$FILE" | dc exec -T elaw-db psql -U elaw -d elaw -v ON_ERROR_STOP=1 -q
dc up -d elaw-auth elaw-ai
echo "Restored. Users in database: $(dc exec -T elaw-db psql -U elaw -d elaw -tAc 'select count(*) from users')"
