#!/usr/bin/env bash
# Deploy the latest origin/main to the live stack, with rollback on failure.
#
# Staging mirrors the layout deploy/README.md prescribes: each run lands in its
# own releases/<timestamp> directory and `current` is repointed only after the
# build succeeds, so a broken commit never replaces a working tree.
set -euo pipefail

BASE="${MOLIPLY_BASE:-/home/deploy/moliply}"
HEALTH_URL="${MOLIPLY_HEALTH_URL:-http://127.0.0.1:18080/healthz}"
REPO="${MOLIPLY_REPO:-https://github.com/MuadibUsul/moliply.git}"
KEEP="${MOLIPLY_KEEP_RELEASES:-5}"

[ -d "$BASE" ] || { echo "missing $BASE" >&2; exit 1; }
[ -f "$BASE/.env" ] || { echo "missing $BASE/.env" >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
REL="$BASE/releases/$STAMP"
PREV="$(readlink -f "$BASE/current" 2>/dev/null || true)"

echo "==> staging $REL"
mkdir -p "$BASE/releases"
git clone -q --depth 1 --branch main "$REPO" "$REL"
rm -rf "$REL/.git"

echo "==> building from the staged tree"
cd "$REL"
docker compose -f "$REL/docker-compose.server.yml" --env-file "$BASE/.env" build

echo "==> switching current -> $STAMP"
ln -sfn "$REL" "$BASE/current"

echo "==> starting"
docker compose -f "$BASE/current/docker-compose.server.yml" --env-file "$BASE/.env" up -d --remove-orphans

# The API runs migrations on boot, and with STRICT_MIGRATIONS=1 a failure is
# fatal. Running `upgrade head` here first makes a bad revision fail the deploy
# before the containers are considered live, instead of at first request.
echo "==> migrating to the current alembic head"
migrated=0
for _ in $(seq 1 20); do
  if docker compose -f "$BASE/current/docker-compose.server.yml" --env-file "$BASE/.env" \
      exec -T api alembic upgrade head; then
    migrated=1
    break
  fi
  sleep 3
done
if [ "$migrated" -ne 1 ]; then
  echo "!! alembic upgrade failed" >&2
  docker compose -f "$BASE/current/docker-compose.server.yml" --env-file "$BASE/.env" logs --tail 60 api >&2 || true
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    echo "==> rolling back to $PREV" >&2
    ln -sfn "$PREV" "$BASE/current"
    cd "$BASE/current"
    docker compose -f "$BASE/current/docker-compose.server.yml" --env-file "$BASE/.env" up -d --remove-orphans
  fi
  exit 1
fi

echo "==> waiting for health at $HEALTH_URL"
healthy=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 3
done

if [ "$healthy" -ne 1 ]; then
  echo "!! health check failed" >&2
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    echo "==> rolling back to $PREV" >&2
    ln -sfn "$PREV" "$BASE/current"
    cd "$BASE/current"
    docker compose -f "$BASE/current/docker-compose.server.yml" --env-file "$BASE/.env" up -d --remove-orphans
  fi
  exit 1
fi

echo "==> pruning old releases (keeping $KEEP)"
ls -1dt "$BASE"/releases/*/ 2>/dev/null | tail -n "+$((KEEP + 1))" | xargs -r rm -rf

echo "done: $(readlink -f "$BASE/current")"
