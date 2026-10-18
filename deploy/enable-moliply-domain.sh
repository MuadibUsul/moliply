#!/usr/bin/env bash
# Enable the formal MOLIPLY domain once DNS points at the server.
# Safe by design: validates the Caddyfile and restores the backup on failure.
set -euo pipefail

DOMAIN="${MOLIPLY_DOMAIN:-moliply.shop}"
SERVER_IP="${MOLIPLY_SERVER_IP:?Set MOLIPLY_SERVER_IP to the public IP of this server}"
BASE="${MOLIPLY_BASE:-/opt/moliply}"
CADDYFILE="${MOLIPLY_CADDYFILE:-/opt/caddy/Caddyfile}"
CADDY_CONTAINER="${MOLIPLY_CADDY_CONTAINER:-caddy-caddy-1}"
BACKUP_DIR="$BASE/caddy-backups"

mkdir -p "$BACKUP_DIR"

ips="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u || true)"
if ! grep -qx "$SERVER_IP" <<<"$ips"; then
  echo "[$(date -Is)] DNS for $DOMAIN is not ready: ${ips:-none}; expected $SERVER_IP"
  exit 2
fi

if grep -Eq '^moliply\.shop[[:space:]]*\{' "$CADDYFILE"; then
  echo "[$(date -Is)] $DOMAIN is already configured in Caddy"
  exit 0
fi

ts="$(date +%Y%m%d-%H%M%S)"
backup="$BACKUP_DIR/Caddyfile.pre-moliply-$ts"
cp "$CADDYFILE" "$backup"

python3 - <<'PY'
from pathlib import Path

path = Path("/opt/caddy/Caddyfile")
text = path.read_text(encoding="utf-8")
block = """
# >>> MOLIPLY DOMAIN (managed by deploy)
moliply.shop {
    encode zstd gzip
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy strict-origin-when-cross-origin
    }
    reverse_proxy moliply-web:3000
}
# <<< MOLIPLY DOMAIN
"""
path.write_text(text.rstrip() + "\n" + block, encoding="utf-8")
print("Caddyfile: added moliply.shop")
PY

if ! docker exec "$CADDY_CONTAINER" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  cp "$backup" "$CADDYFILE"
  echo "[$(date -Is)] Caddy validation failed; restored $backup"
  exit 1
fi

docker exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
echo "[$(date -Is)] Caddy reloaded for $DOMAIN; waiting for certificate..."

for _ in $(seq 1 30); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" 2>/dev/null || true)"
  if [ "$code" = "200" ]; then
    echo "[$(date -Is)] https://$DOMAIN is live with a valid certificate"
    exit 0
  fi
  sleep 2
done

echo "[$(date -Is)] configuration reloaded; certificate pending. Check: docker logs caddy-caddy-1 --tail=80"
exit 0
