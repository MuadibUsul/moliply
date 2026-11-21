#!/usr/bin/env bash
# Verified PostgreSQL restore script for MOLIPLY disaster recovery and restore drills.
# Usage:
#   ./deploy/restore-db.sh /opt/moliply/backups/db/moliply-YYYYMMDD-HHMMSS.dump --yes
#   ./deploy/restore-db.sh /path/to/backup.dump --target-db moliply_drill --yes
set -euo pipefail

DB_CONTAINER="${MOLIPLY_DB_CONTAINER:-moliply-db-1}"
DB_USER="${MOLIPLY_DB_USER:-moliply}"
TARGET_DB="${MOLIPLY_DB_NAME:-moliply}"
CONFIRMED=0
DUMP_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --yes) CONFIRMED=1 ;;
    --target-db) shift; TARGET_DB="${1:?--target-db requires a database name}" ;;
    --help|-h)
      echo "Usage: $0 <dump-file> [--target-db <db>] --yes"
      exit 0
      ;;
    *)
      if [ -z "$DUMP_FILE" ]; then DUMP_FILE="$1"; else echo "Unknown arg: $1" >&2; exit 1; fi
      ;;
  esac
  shift
done

if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
  echo "ERROR: dump file missing: ${DUMP_FILE:-<none>}" >&2
  exit 1
fi
if [ "$CONFIRMED" -ne 1 ]; then
  echo "Refusing destructive restore into '$TARGET_DB' without --yes." >&2
  exit 1
fi

TMP_PLAIN=""
cleanup() { [ -n "$TMP_PLAIN" ] && rm -f "$TMP_PLAIN"; }
trap cleanup EXIT

SOURCE="$DUMP_FILE"
if [[ "$DUMP_FILE" == *.gpg ]]; then
  TMP_PLAIN="$(mktemp)"
  chmod 600 "$TMP_PLAIN"
  gpg --batch --yes -d -o "$TMP_PLAIN" "$DUMP_FILE"
  SOURCE="$TMP_PLAIN"
fi

echo "==> verifying archive integrity"
docker exec -i "$DB_CONTAINER" pg_restore --list < "$SOURCE" >/dev/null

echo "==> ensuring target database '$TARGET_DB' exists"
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -tc \
  "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DB'" | grep -q 1 \
  || docker exec "$DB_CONTAINER" createdb -U "$DB_USER" "$TARGET_DB"

echo "==> restoring into '$TARGET_DB'"
docker exec -i "$DB_CONTAINER" pg_restore \
  -U "$DB_USER" -d "$TARGET_DB" --clean --if-exists --no-owner --no-privileges < "$SOURCE"

ORDERS="$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$TARGET_DB" -tAc "SELECT count(*) FROM orders;" | tr -d '[:space:]')"
CODES="$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$TARGET_DB" -tAc "SELECT count(*) FROM activation_codes;" | tr -d '[:space:]')"
echo "[$(date -Is)] restore ok into '$TARGET_DB' (orders=${ORDERS:-0}, activation_codes=${CODES:-0})"
