#!/usr/bin/env bash
# Deploy the upstream service behind Caddy at <domain>/upstream/*.
# Idempotent: safe to re-run for upgrades. Never touches the store stack.
set -euo pipefail

BASE="${MOLIPLY_BASE:-/home/deploy/moliply}"
UP_DIR="$BASE/upstream"
DOMAIN="${MOLIPLY_DOMAIN:-moliply.shop}"
CADDYFILE="${MOLIPLY_CADDYFILE:-/opt/caddy/Caddyfile}"
CADDY_CONTAINER="${MOLIPLY_CADDY_CONTAINER:-caddy-caddy-1}"
UPSTREAM_EDGE_PORT="${UPSTREAM_EDGE_PORT:-18099}"
RELEASE="$(date +%Y%m%d-%H%M%S)"

[ -d "$BASE" ] || { echo "missing $BASE" >&2; exit 1; }

mkdir -p "$UP_DIR/releases"
REL="$UP_DIR/releases/$RELEASE"
mkdir -p "$REL"
git clone -q --depth 1 "${UPSTREAM_REPO:-https://github.com/MuadibUsul/moliply.git}" "$REL"
rm -rf "$REL/.git"
# current must point at the service directory, not the repo root.
mv "$REL/upstream-service" "$UP_DIR/pkg.$RELEASE"
rm -rf "$REL"
ln -sfn "pkg.$RELEASE" "$UP_DIR/current"
echo "staged $UP_DIR/tmp.$RELEASE"

# Preserve keys across deploys.
if [ ! -f "$UP_DIR/.env" ]; then
  {
    echo "FULFILL_MODE=manual"
    echo "LOG_LEVEL=INFO"
    echo "UPSTREAM_KEY=$(openssl rand -hex 16)"
    echo "UPSTREAM_SECRET=$(openssl rand -hex 32)"
    echo "UPSTREAM_ADMIN_TOKEN=$(openssl rand -hex 12)"
  } > "$UP_DIR/.env"
  chmod 600 "$UP_DIR/.env"
  echo "generated new credentials in $UP_DIR/.env"
fi

cd "$UP_DIR"
docker compose -f current/docker-compose.server.yml --env-file .env build --no-cache upstream >/dev/null
docker compose -f current/docker-compose.server.yml --env-file .env up -d
echo "upstream container started on 127.0.0.1:18099"

# Caddy: insert a path handler INSIDE the existing domain block (same pattern
# the /cce route uses). Caddy runs in a container, so it reaches the host port.
if grep -q "# moliply-upstream" "$CADDYFILE"; then
  echo "caddy block already present"
else
  sudo cp "$CADDYFILE" "$CADDYFILE.bak.$RELEASE"
  sudo python3 - "$CADDYFILE" "$DOMAIN" "$UPSTREAM_EDGE_PORT" <<'PYEOF'
import sys
path, domain, port = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8", newline="").read()
marker = f"{domain} {{"
start = text.index(marker)
# insert right before the site's final `handle {` block
handle = text.index("    handle {", start)
block = (
    "    # moliply-upstream
"
    "    handle_path /upstream/* {
"
    f"        reverse_proxy 172.18.0.1:{port}
"
    "    }
"
)
open(path, "w", encoding="utf-8", newline="").write(text[:handle] + block + text[handle:])
print("inserted /upstream handler")
PYEOF
  docker exec "$CADDY_CONTAINER" caddy validate --config /etc/caddy/Caddyfile
  docker exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile
  echo "caddy reloaded"
fi

echo "done. base URL: https://$DOMAIN/upstream"
