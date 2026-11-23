#!/usr/bin/env bash
# Run on your Mac: copies the server's nightly database backups to ~/elaw-backups.
# Safe to run any time; files already copied are skipped. Needs your SSH key.
set -euo pipefail
SERVER="${SERVER:-root@201.18.199.87}"
DEST="${DEST:-$HOME/elaw-backups}"

mkdir -p "$DEST" && chmod 700 "$DEST"
rsync -a --ignore-existing "$SERVER:/root/elaw-backups/" "$DEST/"
chmod 600 "$DEST"/*.gz 2>/dev/null || true
echo "Backups in $DEST (newest first):"
ls -lt "$DEST" | grep '\.sql\.gz' | head -5 | awk '{print "  " $6, $7, $8, $NF}'
