#!/usr/bin/env bash
# Daily PostgreSQL + .env backup for the MOLIPLY deployment.
# Only touches the moliply DB container and its own backup directory.
set -euo pipefail

BASE="${MOLIPLY_BASE:-/opt/moliply}"
DB_CONTAINER="${MOLIPLY_DB_CONTAINER:-moliply-db-1}"
DB_USER="${MOLIPLY_DB_USER:-moliply}"
DB_NAME="${MOLIPLY_DB_NAME:-moliply}"
BACKUP_DIR="$BASE/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/env"
chmod 700 "$BACKUP_DIR" "$BACKUP_DIR/db" "$BACKUP_DIR/env"

OUT="$BACKUP_DIR/db/moliply-$STAMP.dump"

docker exec "$DB_CONTAINER" pg_dump \
  -U "$DB_USER" -d "$DB_NAME" \
  -Fc -Z 9 --no-owner --no-privileges > "$OUT"

if ! docker exec -i "$DB_CONTAINER" pg_restore --list < "$OUT" >/dev/null 2>&1; then
  rm -f "$OUT"
  echo "[$(date -Is)] ERROR: backup verification failed" >&2
  exit 1
fi

chmod 600 "$OUT"

# Keep a local copy of .env so a restore also has the matching AES key.
# Copy this directory off-server; local backups do not survive server loss.
if [ -f "$BASE/.env" ]; then
  cp "$BASE/.env" "$BACKUP_DIR/env/env-$STAMP"
  chmod 600 "$BACKUP_DIR/env/env-$STAMP"
fi

find "$BACKUP_DIR/db" -type f -name 'moliply-*.dump' -mtime +14 -delete
find "$BACKUP_DIR/env" -type f -name 'env-*' -mtime +14 -delete

SIZE="$(du -h "$OUT" | cut -f1)"
echo "[$(date -Is)] backup ok: $OUT ($SIZE)"
