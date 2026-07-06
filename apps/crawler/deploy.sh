#!/usr/bin/env bash
# Deploy crawler containers on Hetzner (worker machine).
# Postgres runs on a separate dedicated machine.
# Called by CI with env vars set from GitHub secrets.
set -euo pipefail

# ── Validate required env vars ─────────────────────────────────────────
required_vars=(
  OWNER
  DATABASE_URL_UNPOOLED
  LOCAL_DATABASE_URL
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_ENDPOINT_URL
  R2_DOMAIN_URL
  R2_BUCKET
  GRAFANA_PROM_URL
  GRAFANA_PROM_USERNAME
  GRAFANA_PROM_PASSWORD
  GRAFANA_LOKI_URL
  GRAFANA_LOKI_USERNAME
  GRAFANA_LOKI_PASSWORD
  TYPESENSE_HOST
  TYPESENSE_PORT
  TYPESENSE_PROTOCOL
  TYPESENSE_ADMIN_KEY
  # Murmur shim secret. Without this, the shim's compose env
  # substitution `${MURMUR_TOKEN}` resolves to empty on a full-stack
  # redeploy and the shim accepts every request as anonymous. The
  # H4 deploy workflow (deploy-murmur-shim.yml) sets this transiently
  # via SSH `env:` block, but its `up -d murmur-shim` does not write
  # to /home/deploy/.env — so a subsequent crawler full-stack redeploy
  # would silently lose the token if we didn't pin it here.
  # Required since H3 (#2775) added the murmur-shim service.
  MURMUR_TOKEN
)

missing=()
for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    missing+=("$var")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: Missing required env vars: ${missing[*]}" >&2
  exit 1
fi

DEPLOY_DIR="/home/deploy"
ENV_FILE="$DEPLOY_DIR/.env"
ROLLBACK_ENV_FILE="$DEPLOY_DIR/.env.rollback"
IMAGE_TAG="${CRAWLER_IMAGE_TAG:-latest}"
DEPLOY_MIN_FREE_KB="${DEPLOY_MIN_FREE_KB:-5242880}" # 5 GiB hard floor.
DEPLOY_PRUNE_FREE_KB="${DEPLOY_PRUNE_FREE_KB:-10485760}" # Prune cache below 10 GiB.

rollback_deploy() {
  local exit_code=$?
  trap - ERR
  echo "Deploy failed — restoring crawler containers on previous image" >&2

  if [[ -f "$ROLLBACK_ENV_FILE" ]]; then
    mv "$ROLLBACK_ENV_FILE" "$ENV_FILE" || true
    chmod 600 "$ENV_FILE" || true
  fi

  cd "$DEPLOY_DIR" || exit "$exit_code"
  docker compose up -d --remove-orphans 2>/dev/null || true
  exit "$exit_code"
}

compose_service_ready() {
  local service="$1"
  local container_id state health

  container_id="$(docker compose ps -q "$service" 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    return 1
  fi

  state="$(docker inspect -f '{{.State.Status}}' "$container_id" 2>/dev/null || true)"
  if [[ "$state" != "running" ]]; then
    return 1
  fi

  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || true)"
  [[ "$health" == "none" || "$health" == "healthy" ]]
}

wait_for_core_services() {
  local services=(redis worker-1 worker-2 worker-3 browser-1 exporter drain alloy)
  local deadline=$((SECONDS + 180))
  local missing=()

  while (( SECONDS < deadline )); do
    missing=()
    for service in "${services[@]}"; do
      if ! compose_service_ready "$service"; then
        missing+=("$service")
      fi
    done

    if [[ ${#missing[@]} -eq 0 ]]; then
      return 0
    fi

    echo "Waiting for services to become ready: ${missing[*]}" >&2
    sleep 5
  done

  echo "ERROR: services did not become ready: ${missing[*]}" >&2
  docker compose ps >&2 || true
  return 1
}

deploy_disk_free_kb() {
  df -Pk "$DEPLOY_DIR" | awk 'NR == 2 {print $4}'
}

ensure_deploy_disk_headroom() {
  local free_kb

  free_kb="$(deploy_disk_free_kb)"
  if (( free_kb < DEPLOY_PRUNE_FREE_KB )); then
    echo "Low deploy disk headroom (${free_kb} KiB available); pruning Docker builder cache" >&2
    # The crawler host should run pulled images, not depend on local build
    # cache. Keep containers, images, and volumes intact.
    docker builder prune -af >/dev/null || true
    free_kb="$(deploy_disk_free_kb)"
  fi

  if (( free_kb < DEPLOY_MIN_FREE_KB )); then
    echo "ERROR: insufficient deploy disk headroom (${free_kb} KiB available; need ${DEPLOY_MIN_FREE_KB} KiB)" >&2
    df -h "$DEPLOY_DIR" >&2 || true
    docker system df >&2 || true
    return 1
  fi

  echo "Deploy disk headroom OK: ${free_kb} KiB available" >&2
}

# ── Stop any manually-started containers that conflict with compose ──
# `indexnow` was retired in #2821 (companies left the index); the rm is
# kept here to clean up boxes that still have a manually-started one.
legacy_containers=(redis worker-1 worker-2 worker-3 browser-1 exporter drain indexnow alloy)
docker stop --time=60 "${legacy_containers[@]}" 2>/dev/null || true
docker rm "${legacy_containers[@]}" 2>/dev/null || true

# ── Write env file ──────────────────────────────────────────────────
# Proxy vars are expanded with ``:-`` defaults so missing provider
# secrets don't break the deploy — PROXY_PROVIDER=none disables the
# proxy layer even when the URL envs are empty.
rm -f "$ROLLBACK_ENV_FILE"
if [[ -f "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "$ROLLBACK_ENV_FILE"
  chmod 600 "$ROLLBACK_ENV_FILE"
fi

cat > "$ENV_FILE" <<EOF
OWNER=${OWNER}
CRAWLER_IMAGE_TAG=${IMAGE_TAG}
DATABASE_URL=${DATABASE_URL_UNPOOLED}
LOCAL_DATABASE_URL=${LOCAL_DATABASE_URL}
R2_ACCESS_KEY_ID=${R2_ACCESS_KEY_ID}
R2_SECRET_ACCESS_KEY=${R2_SECRET_ACCESS_KEY}
R2_ENDPOINT_URL=${R2_ENDPOINT_URL}
R2_DOMAIN_URL=${R2_DOMAIN_URL}
R2_BUCKET=${R2_BUCKET}
GRAFANA_PROM_URL=${GRAFANA_PROM_URL}
GRAFANA_PROM_USERNAME=${GRAFANA_PROM_USERNAME}
GRAFANA_PROM_PASSWORD=${GRAFANA_PROM_PASSWORD}
GRAFANA_LOKI_URL=${GRAFANA_LOKI_URL}
GRAFANA_LOKI_USERNAME=${GRAFANA_LOKI_USERNAME}
GRAFANA_LOKI_PASSWORD=${GRAFANA_LOKI_PASSWORD}
TYPESENSE_HOST=${TYPESENSE_HOST}
TYPESENSE_PORT=${TYPESENSE_PORT}
TYPESENSE_PROTOCOL=${TYPESENSE_PROTOCOL}
TYPESENSE_ADMIN_KEY=${TYPESENSE_ADMIN_KEY}
PROXY_PROVIDER=${PROXY_PROVIDER:-none}
WEBSHARE_PROXY_URL=${WEBSHARE_PROXY_URL:-}
DECODO_PROXY_URL=${DECODO_PROXY_URL:-}
MURMUR_TOKEN=${MURMUR_TOKEN}
EOF

# Lock down the env file — it contains proxy + DB + R2 creds. Default
# umask on some images is 0022, which would leave this world-readable.
chmod 600 "$ENV_FILE"

# ── Pull images and preflight while the old stack is still serving ────
cd "$DEPLOY_DIR"
trap rollback_deploy ERR

ensure_deploy_disk_headroom

docker compose pull

docker compose up -d redis

# ── Run Alembic migrations on local Postgres ─────────────────────────
docker run --rm --env-file "$ENV_FILE" --network host \
  "ghcr.io/${OWNER}/jobseek-crawler:${IMAGE_TAG}" \
  uv run --no-sync alembic -c src/migrations/alembic.ini upgrade head

# ── Patch Typesense schema (idempotent — adds new fields if missing) ─
# Must run BEFORE `crawler sync`, otherwise the next sync would upsert
# docs containing fields that the live schema doesn't know about.
docker run --rm --env-file "$ENV_FILE" --network host \
  "ghcr.io/${OWNER}/jobseek-crawler:${IMAGE_TAG}" \
  uv run --no-sync crawler setup-typesense

# ── Quiesce processors before reseeding Redis-backed schedules ───────
# Keep Redis and alloy up, but stop processors so deploy-time
# `crawler sync` does not race with live workers claiming work while we
# reseed board monitors. `--timeout 60` matches the app's 30s bounded
# drain with headroom before Docker sends SIGKILL.
docker compose stop --timeout 60 worker-1 worker-2 worker-3 browser-1 exporter drain

# ── Sync board config from CSV → local Postgres + Redis + Typesense ──
docker run --rm --env-file "$ENV_FILE" --network host \
  "ghcr.io/${OWNER}/jobseek-crawler:${IMAGE_TAG}" \
  uv run --no-sync crawler sync

# ── Start the full stack on the freshly seeded Redis state ───────────
docker compose up -d --remove-orphans

# Force-recreate alloy so it picks up any alloy.river bind-mount changes.
# Compose's plain ``up -d`` does not recreate a service when only the
# bind-mounted file's content changed — the service spec is unchanged
# from compose's perspective. Without this step, alloy keeps serving
# the previous config indefinitely (the container's bind-mount is
# pinned to the old inode rsync replaced). One extra ~2s alloy restart
# per deploy is well worth not having silent observability drift.
docker compose up -d --force-recreate alloy

# Gate success on the core crawler services actually running. The
# murmur shim is intentionally excluded while Murmur remains
# backburnered; a shim issue should not fail the crawler deploy.
wait_for_core_services

# ── Cleanup ──────────────────────────────────────────────────────────
trap - ERR
rm -f "$ROLLBACK_ENV_FILE"
docker image prune -f
echo "Deploy complete: $(docker compose ps --format '{{.Name}}' | tr '\n' ' ')"
