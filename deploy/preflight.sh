#!/usr/bin/env bash
# Read-only launch readiness check. Never prints secret values.
set -uo pipefail

BASE="${MOLIPLY_BASE:-/opt/moliply}"
ENV_FILE="$BASE/.env"
DB_CONTAINER="${MOLIPLY_DB_CONTAINER:-moliply-db-1}"
DB_USER="${MOLIPLY_DB_USER:-moliply}"
DB_NAME="${MOLIPLY_DB_NAME:-moliply}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1; }
pass=0; warn=0; fail=0
ok()   { pass=$((pass+1)); echo "PASS  $1"; }
warnf(){ warn=$((warn+1)); echo "WARN  $1"; }
bad()  { fail=$((fail+1)); echo "FAIL  $1"; }

count_db() {
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc "$1" 2>/dev/null | tr -d '[:space:]'
}

# Admin settings saved in the database override .env. Read them through the
# API container so this check remains accurate for UI-configured keys.
API_CONTAINER="${MOLIPLY_API_CONTAINER:-moliply-api-1}"
RUNTIME_JSON=""
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$API_CONTAINER"; then
  RUNTIME_JSON="$(docker exec "$API_CONTAINER" python -c 'import json; from app.services.runtime_settings import public_settings; print(json.dumps(public_settings()))' 2>/dev/null || true)"
fi
runtime_value() {
  printf '%s' "$RUNTIME_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null || true
}
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
RUNTIME_PAYMENT_MODE="$(runtime_value 'd["values"].get("payment_mode","")')"
RUNTIME_MOCK="$(lower "$(runtime_value 'd["values"].get("mock_payment_enabled","")')")"
RUNTIME_PAYMENT_READY="$(lower "$(runtime_value 'd.get("payment_ready",False)')")"
RUNTIME_CRYPTO_ENABLED="$(lower "$(runtime_value 'd["values"].get("crypto_enabled","")')")"
RUNTIME_CRYPTO_READY="$(lower "$(runtime_value 'd.get("crypto_ready",False)')")"
RUNTIME_UPSTREAM_READY="$(lower "$(runtime_value 'd.get("upstream_ready",False)')")"
RUNTIME_SUPPORT_READY="$(lower "$(runtime_value 'd.get("support_ready",False)')")"
RUNTIME_FEISHU_READY="$(lower "$(runtime_value 'd.get("feishu_ready",False)')")"
RUNTIME_SELF_VCC="$(lower "$(runtime_value 'd["values"].get("self_vcc_enabled","")')")"
RUNTIME_SELF_DRY="$(lower "$(runtime_value 'd["values"].get("self_vcc_dry_run","")')")"
RUNTIME_SMTP="$(runtime_value 'd["values"].get("smtp_host","")')"

STORE_URL="$(get PUBLIC_STORE_URL)"
PAYMENT_MODE="${RUNTIME_PAYMENT_MODE:-$(get PAYMENT_MODE)}"
MOCK="${RUNTIME_MOCK:-$(get MOCK_PAYMENT_ENABLED)}"
MOCK="$(lower "$MOCK")"
UPSTREAM_MODE="$(get UPSTREAM_MODE)"
UPSTREAM_BASE="$(get UPSTREAM_API_BASE)"
UPSTREAM_KEY="$(get UPSTREAM_API_KEY)"
SELF_VCC="${RUNTIME_SELF_VCC:-$(get SELF_VCC_ENABLED)}"
SELF_DRY="${RUNTIME_SELF_DRY:-$(get SELF_VCC_DRY_RUN)}"
SEED_INV="$(get SEED_DEMO_INVENTORY)"
ADMIN_PASSWORD="$(get ADMIN_PASSWORD)"
MASTER_KEY="$(get MASTER_AES_KEY)"
SMTP_HOST="${RUNTIME_SMTP:-$(get SMTP_HOST)}"
CONTACT="$(get NEXT_PUBLIC_CONTACT_WECHAT)"
CRYPTO_ENABLED="${RUNTIME_CRYPTO_ENABLED:-$(get CRYPTO_ENABLED)}"
NOWPAYMENTS_API_KEY="$(get NOWPAYMENTS_API_KEY)"
NOWPAYMENTS_IPN_SECRET="$(get NOWPAYMENTS_IPN_SECRET)"
LTZF_ID="$(get LTZF_MCH_ID)"
LTZF_KEY="$(get LTZF_KEY)"
XH_ID="$(get XUNHUPAY_APP_ID)"
XH_KEY="$(get XUNHUPAY_APP_SECRET)"

[[ "$STORE_URL" == https://* ]] && ok "PUBLIC_STORE_URL uses HTTPS" || bad "PUBLIC_STORE_URL must be HTTPS"
[[ "$PAYMENT_MODE" == "live" ]] && ok "PAYMENT_MODE=live" || bad "PAYMENT_MODE is not live (currently ${PAYMENT_MODE:-unset})"
[[ "$MOCK" == "false" ]] && ok "MOCK_PAYMENT_ENABLED=false" || bad "mock payment must be disabled before launch"

if [ "$RUNTIME_PAYMENT_READY" = "true" ] || { { [ -n "$LTZF_ID" ] && [ -n "$LTZF_KEY" ]; } || { [ -n "$XH_ID" ] && [ -n "$XH_KEY" ]; }; }; then
  ok "payment merchant keys configured"
else
  bad "no payment merchant keys configured (Lanttu or Xunhupay)"
fi

[[ -n "$ADMIN_PASSWORD" && "$ADMIN_PASSWORD" != "change-me" && ${#ADMIN_PASSWORD} -ge 12 ]] \
  && ok "admin password is non-default and >=12 chars" \
  || bad "admin password is default/too short"
[[ -n "$MASTER_KEY" ]] && ok "MASTER_AES_KEY configured" || bad "MASTER_AES_KEY missing"
[[ "$SEED_INV" == "false" ]] && ok "SEED_DEMO_INVENTORY=false" || bad "SEED_DEMO_INVENTORY must be false before launch"

demo_codes="$(count_db "SELECT count(*) FROM activation_codes WHERE note='演示库存';")"
demo_accounts="$(count_db "SELECT count(*) FROM account_inventory WHERE label LIKE '%@moliply.test%';")"
real_codes="$(count_db "SELECT count(*) FROM activation_codes WHERE status='UNUSED' AND note <> '演示库存';")"
real_accounts="$(count_db "SELECT count(*) FROM account_inventory WHERE status='AVAILABLE' AND label NOT LIKE '%@moliply.test%';")"
orders="$(count_db "SELECT count(*) FROM orders;")"

[[ "${demo_codes:-0}" -eq 0 ]] && ok "no demo activation codes left" || bad "demo activation codes remain: ${demo_codes}"
[[ "${demo_accounts:-0}" -eq 0 ]] && ok "no demo account credentials left" || bad "demo account credentials remain: ${demo_accounts}"

if [ "${real_codes:-0}" -gt 0 ] || [ "${real_accounts:-0}" -gt 0 ]; then
  ok "real inventory present (codes=${real_codes:-0}, accounts=${real_accounts:-0})"
else
  bad "no real inventory imported"
fi

if [ "${real_accounts:-0}" -eq 0 ] && [ "${real_codes:-0}" -gt 0 ]; then
  if { [ "$UPSTREAM_MODE" = "http" ] && [ -n "$UPSTREAM_BASE" ] && [ -n "$UPSTREAM_KEY" ]; } \
     || { [ "$SELF_VCC" = "true" ] && [ "$SELF_DRY" = "false" ]; }; then
    ok "a real recharge channel is configured for code inventory"
  else
    bad "real codes exist but neither real upstream API nor live self-VCC is configured"
  fi
fi

if [ -n "$SMTP_HOST" ]; then ok "SMTP delivery email configured"; else warnf "SMTP is not configured (order page delivery still works)"; fi

if [ "$RUNTIME_SUPPORT_READY" = "true" ] || { [ -n "$CONTACT" ] && [ "$CONTACT" != "MOLIPLY-SUPPORT" ]; }; then
  ok "customer service contact configured"
else
  bad "customer service contact is still a placeholder; set it in the admin settings"
fi

if [ "$CRYPTO_ENABLED" = "true" ]; then
  if [ "$RUNTIME_CRYPTO_READY" = "true" ] || { [ -n "$NOWPAYMENTS_API_KEY" ] && [ -n "$NOWPAYMENTS_IPN_SECRET" ]; }; then
    ok "crypto checkout enabled and NOWPayments keys configured"
  else
    bad "CRYPTO_ENABLED=true but NOWPayments API/IPN secrets are missing"
  fi
else
  warnf "crypto checkout is disabled (USDT/USDC/BTC/ETH not offered)"
fi
if [ "$RUNTIME_FEISHU_READY" = "true" ]; then
  ok "Feishu notifications configured"
else
  warnf "Feishu notifications are not configured"
fi

latest_backup="$(find "$BASE/backups/db" -type f -name '*.dump' -mmin -1440 2>/dev/null | head -n1 || true)"
if [ -n "$latest_backup" ]; then ok "database backup in the last 24h"; else bad "no database backup in the last 24h"; fi

if curl -fsS --max-time 10 "${STORE_URL%/}/healthz" >/dev/null 2>&1; then
  ok "public /healthz reachable"
elif curl -fsS --max-time 10 "http://127.0.0.1:18088/healthz" >/dev/null 2>&1; then
  warnf "public /healthz not reachable from this host (DNS cache/ingress); origin is healthy"
else
  bad "public /healthz and local origin are not reachable"
fi

echo
echo "Orders: ${orders:-0} | Real codes: ${real_codes:-0} | Real accounts: ${real_accounts:-0} | Demo codes: ${demo_codes:-0} | Demo accounts: ${demo_accounts:-0}"
echo "Result: ${pass} pass, ${warn} warn, ${fail} fail"
[ "$fail" -eq 0 ]
