#!/usr/bin/env bash
# Remove only the demo fixtures created by seed.py, after backing up the DB.
# Real inventory and real orders are never matched by these predicates.
set -euo pipefail

BASE="${MOLIPLY_BASE:-/opt/moliply}"
ENV_FILE="$BASE/.env"
DB_CONTAINER="${MOLIPLY_DB_CONTAINER:-moliply-db-1}"
DB_USER="${MOLIPLY_DB_USER:-moliply}"
DB_NAME="${MOLIPLY_DB_NAME:-moliply}"
ALLOW_EMPTY=0
CONFIRMED=0

for arg in "$@"; do
  case "$arg" in
    --yes) CONFIRMED=1 ;;
    --allow-empty) ALLOW_EMPTY=1 ;;
    *) echo "Usage: $0 --yes [--allow-empty]" >&2; exit 1 ;;
  esac
done

if [ "$CONFIRMED" -ne 1 ]; then
  echo "Usage: $0 --yes [--allow-empty]" >&2
  echo "This deletes demo codes/accounts/coupons and sets SEED_DEMO_INVENTORY=false." >&2
  exit 1
fi

count_db() {
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc "$1" 2>/dev/null | tr -d '[:space:]'
}

real_codes="$(count_db "SELECT count(*) FROM activation_codes WHERE status='UNUSED' AND note <> '演示库存';")"
real_accounts="$(count_db "SELECT count(*) FROM account_inventory WHERE status='AVAILABLE' AND label NOT LIKE '%@moliply.test%';")"

if [ "${real_codes:-0}" -eq 0 ] && [ "${real_accounts:-0}" -eq 0 ] && [ "$ALLOW_EMPTY" -ne 1 ]; then
  echo "No real inventory detected. Import real stock first, or pass --allow-empty intentionally." >&2
  exit 1
fi

echo "Creating a backup before purging demo fixtures..."
MOLIPLY_BASE="$BASE" "$BASE/backup-db.sh"

docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DELETE FROM activation_codes
 WHERE note = '演示库存'
   AND status = 'UNUSED'
   AND order_item_id IS NULL;

DELETE FROM account_inventory
 WHERE label LIKE '%@moliply.test%'
   AND status = 'AVAILABLE'
   AND order_item_id IS NULL;

DELETE FROM coupons
 WHERE code IN ('MOLI10', 'WELCOME5')
   AND used_count = 0;

DO $$
BEGIN
  IF (SELECT count(*) FROM orders) = 0 THEN
    UPDATE products SET sold_count = 0;
  END IF;
END $$;
COMMIT;
SQL

cp "$ENV_FILE" "$ENV_FILE.bak-demo-purge"
sed -i 's/^SEED_DEMO_INVENTORY=.*/SEED_DEMO_INVENTORY=false/' "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "Demo fixtures removed; SEED_DEMO_INVENTORY=false written to .env."
echo "Remaining real codes: ${real_codes:-0}; real accounts: ${real_accounts:-0}"
echo "Run $BASE/preflight.sh to verify launch readiness."
