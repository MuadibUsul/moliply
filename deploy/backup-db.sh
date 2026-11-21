#!/usr/bin/env bash
# Daily PostgreSQL + CCE SQLite + .env backup for the MOLIPLY deployment.
# Supports optional GPG encryption (BACKUP_GPG_RECIPIENT) and off-site rsync (BACKUP_RSYNC_TARGET).
set -euo pipefail

BASE="${MOLIPLY_BASE:-/opt/moliply}"
DB_CONTAINER="${MOLIPLY_DB_CONTAINER:-moliply-db-1}"
API_CONTAINER="${MOLIPLY_API_CONTAINER:-moliply-api-1}"
DB_USER="${MOLIPLY_DB_USER:-moliply}"
DB_NAME="${MOLIPLY_DB_NAME:-moliply}"
BACKUP_DIR="$BASE/backups"
KEEP_DAYS="${MOLIPLY_BACKUP_KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/env" "$BACKUP_DIR/cce"
chmod 700 "$BACKUP_DIR" "$BACKUP_DIR/db" "$BACKUP_DIR/env" "$BACKUP_DIR/cce"

OUT="$BACKUP_DIR/db/moliply-$STAMP.dump"

docker exec "$DB_CONTAINER" pg_dump \
  -U "$DB_USER" -d "$DB_NAME" \
  -Fc -Z 9 --no-owner --no-privileges > "$OUT"

if ! docker exec -i "$DB_CONTAINER" pg_restore --list < "$OUT" >/dev/null 2>&1; then
  rm -f "$OUT"
  echo "[$(date -Is)] ERROR: PostgreSQL backup verification failed" >&2
  exit 1
fi
chmod 600 "$OUT"

# Also snapshot CCE SQLite consistently if the API container has /data/cce.sqlite3.
CCE_OUT="$BACKUP_DIR/cce/cce-$STAMP.sqlite3"
if docker ps --format '{{.Names}}' | grep -qx "$API_CONTAINER"; then
  if docker exec "$API_CONTAINER" test -f /data/cce.sqlite3 2>/dev/null; then
    docker exec "$API_CONTAINER" python -c \
      "import sqlite3; src=sqlite3.connect('/data/cce.sqlite3'); dst=sqlite3.connect('/tmp/cce-bak.sqlite3'); src.backup(dst); dst.close(); src.close()" \
      && docker cp "$API_CONTAINER:/tmp/cce-bak.sqlite3" "$CCE_OUT" \
      && docker exec "$API_CONTAINER" rm -f /tmp/cce-bak.sqlite3 \
      && chmod 600 "$CCE_OUT" || rm -f "$CCE_OUT"
  fi
fi

# Keep a copy of .env so a restore also has the matching AES key.
ENV_OUT=""
if [ -f "$BASE/.env" ]; then
  ENV_OUT="$BACKUP_DIR/env/env-$STAMP"
  cp "$BASE/.env" "$ENV_OUT"
  chmod 600 "$ENV_OUT"
fi

# Optional GPG encryption at rest when BACKUP_GPG_RECIPIENT is configured.
if [ -n "${BACKUP_GPG_RECIPIENT:-}" ] && command -v gpg >/dev/null 2>&1; then
  for f in "$OUT" "$CCE_OUT" "$ENV_OUT"; do
    if [ -n "$f" ] && [ -f "$f" ]; then
      gpg --batch --yes --trust-model always -e -r "$BACKUP_GPG_RECIPIENT" -o "$f.gpg" "$f"
      chmod 600 "$f.gpg"
      rm -f "$f"
    fi
  done
  OUT="$OUT.gpg"
fi

# Optional off-site replication (recommended for disaster recovery).
if [ -n "${BACKUP_RSYNC_TARGET:-}" ] && command -v rsync >/dev/null 2>&1; then
  rsync -az --chmod=F600 "$BACKUP_DIR/" "$BACKUP_RSYNC_TARGET"
fi

find "$BACKUP_DIR/db" -type f -name 'moliply-*.dump*' -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR/cce" -type f -name 'cce-*.sqlite3*' -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR/env" -type f -name 'env-*' -mtime "+$KEEP_DAYS" -delete

SIZE="$(du -h "$OUT" | cut -f1)"
echo "[$(date -Is)] backup ok: $OUT ($SIZE)"
