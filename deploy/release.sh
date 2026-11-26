#!/usr/bin/env bash
# Deploy the latest origin/main to the live stack, with image-tagged rollback on failure.
set -euo pipefail

BASE="${MOLIPLY_BASE:-/opt/moliply}"
REPO="${MOLIPLY_REPO:-https://github.com/MuadibUsul/moliply.git}"
KEEP="${MOLIPLY_KEEP_RELEASES:-5}"
ENV_FILE="$BASE/.env"

[ -d "$BASE" ] || { echo "missing $BASE" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
REL="$BASE/releases/$STAMP"
PREV="$(readlink -f "$BASE/current" 2>/dev/null || true)"
PREV_TAG="$(cat "$BASE/.current_tag" 2>/dev/null || true)"

compose_for() {
  local dir="$1"
  local tag="$2"
  MOLIPLY_ENV_FILE="$ENV_FILE" MOLIPLY_IMAGE_TAG="$tag" \
    docker compose -f "$dir/deploy/docker-compose.server.yml" --env-file "$ENV_FILE" "${@:3}"
}

rollback() {
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    local rb_tag="${PREV_TAG:-latest}"
    echo "==> rolling back to $PREV (image tag: $rb_tag)" >&2
    ln -sfn "$PREV" "$BASE/current"
    compose_for "$PREV" "$rb_tag" up -d --no-build --remove-orphans || true
  fi
}

echo "==> staging $REL"
mkdir -p "$BASE/releases"
git clone -q --depth 1 --branch main "$REPO" "$REL"
rm -rf "$REL/.git"

echo "==> building tagged images ($STAMP) from the staged tree"
compose_for "$REL" "$STAMP" build

echo "==> ensuring db and redis are running"
compose_for "$REL" "$STAMP" up -d --no-build db redis

echo "==> running alembic upgrade head BEFORE switching traffic"
migrated=0
for _ in $(seq 1 15); do
  if compose_for "$REL" "$STAMP" run --rm --no-deps api alembic upgrade head; then
    migrated=1
    break
  fi
  sleep 3
done
if [ "$migrated" -ne 1 ]; then
  echo "!! alembic upgrade failed; aborting before cutover" >&2
  exit 1
fi

echo "==> switching current -> $STAMP"
ln -sfn "$REL" "$BASE/current"

echo "==> starting services ($STAMP)"
compose_for "$REL" "$STAMP" up -d --no-build --remove-orphans

echo "==> waiting for API readiness (/readyz)"
healthy=0
for _ in $(seq 1 30); do
  if compose_for "$REL" "$STAMP" exec -T api python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 3
done

if [ "$healthy" -ne 1 ]; then
  echo "!! readiness check failed" >&2
  compose_for "$REL" "$STAMP" logs --tail 60 api >&2 || true
  rollback
  exit 1
fi

printf '%s\n' "$STAMP" > "$BASE/.current_tag"

# Sync vendored CCE console to the host systemd tree when co-located on this box.
CCE_HOST_DIR="${CCE_HOST_DIR:-/home/deploy/cce/server}"
if [ -d "$CCE_HOST_DIR" ] && [ -w "$CCE_HOST_DIR" ]; then
  echo "==> syncing cce_console -> $CCE_HOST_DIR"
  for sub in api core db util; do
    if [ -d "$REL/recharge-gateway-backend/cce_console/$sub" ]; then
      mkdir -p "$CCE_HOST_DIR/$sub"
      cp -a "$REL/recharge-gateway-backend/cce_console/$sub/." "$CCE_HOST_DIR/$sub/"
    fi
  done
  if systemctl --user is-active cce-api.service >/dev/null 2>&1; then
    echo "==> restarting user unit cce-api.service"
    systemctl --user restart cce-api.service || echo "WARN: failed to restart user cce-api.service" >&2
  elif command -v sudo >/dev/null 2>&1 && sudo -n -u deploy XDG_RUNTIME_DIR="/run/user/$(id -u deploy 2>/dev/null || echo 1000)" systemctl --user is-active cce-api.service >/dev/null 2>&1; then
    echo "==> restarting deploy user unit cce-api.service"
    sudo -n -u deploy XDG_RUNTIME_DIR="/run/user/$(id -u deploy)" systemctl --user restart cce-api.service || echo "WARN: failed to restart deploy cce-api.service" >&2
  fi
fi

echo "==> pruning old releases (keeping $KEEP)"
ls -1dt "$BASE"/releases/*/ 2>/dev/null | tail -n "+$((KEEP + 1))" | xargs -r rm -rf

echo "done: $(readlink -f "$BASE/current") (tag: $STAMP)"
