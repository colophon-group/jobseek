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
source "$DEPLOY_DIR/deploy_helpers.sh"
IMAGE_TAG="${CRAWLER_IMAGE_TAG:-latest}"
DEPLOY_MIN_FREE_KB="${DEPLOY_MIN_FREE_KB:-5242880}" # 5 GiB hard floor.
DEPLOY_PRUNE_FREE_KB="${DEPLOY_PRUNE_FREE_KB:-10485760}" # Prune cache below 10 GiB.
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$DEPLOY_DIR")}"
export COMPOSE_PROJECT_NAME
ALLOY_IMAGE="grafana/alloy:v1.18.0@sha256:491b0578c04983fd54fe99b587b6fab4404dc46d0dc16677bd6b00cc1140b308"
ALLOY_STATE_ACTIVATION_REQUIRED=0

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
  [[ "$health" == "none" || "$health" == "healthy" ]] || return 1
  if [[ "$service" == "alloy" ]]; then
    curl --fail --silent --show-error --max-time 2 \
      http://127.0.0.1:12346/-/ready >/dev/null
  fi
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

normalize_alloy_state_volume() {
  local volume_name="$1"

  # The long-running collector is explicit root with every capability dropped.
  # Make it the volume owner so it can write its WAL/cursors without relying on
  # CAP_DAC_OVERRIDE. The helper is pinned, networkless, and exits immediately.
  docker run --rm --network none --user 0:0 \
    -v "${volume_name}:/data-alloy" \
    --entrypoint sh "$ALLOY_IMAGE" \
    -c 'chown -R 0:0 /data-alloy && chmod 0700 /data-alloy'
}

prepare_alloy_state_volume() {
  local volume_name="${COMPOSE_PROJECT_NAME}_alloy-data"
  local marker="/data-alloy/.jobseek-persistent-state"
  local alloy_container state state_volume staging

  docker volume create "$volume_name" >/dev/null
  alloy_container="$(docker compose ps -aq alloy 2>/dev/null || true)"
  state=""
  state_volume=""
  if [[ -n "$alloy_container" ]]; then
    state="$(docker inspect -f '{{.State.Status}}' "$alloy_container" 2>/dev/null || true)"
    state_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data-alloy"}}{{.Name}}{{end}}{{end}}' "$alloy_container" 2>/dev/null || true)"
  fi

  # Fast path for every deploy after the migration. The marker lives in the
  # named volume, so force-recreating Alloy below cannot erase it or the
  # Docker-source positions stored beside it.
  if docker run --rm --network none --user 0:0 \
    -v "${volume_name}:/data-alloy" \
    --entrypoint sh "$ALLOY_IMAGE" \
    -c "test -f '${marker}'"; then
    echo "Alloy state volume already initialized: ${volume_name}" >&2
    if [[ "$state" != "running" || "$state_volume" != "$volume_name" ]]; then
      # A prior deploy may have prepared the volume and failed before the
      # changed service spec became active. Recreate immediately on retry.
      ALLOY_STATE_ACTIVATION_REQUIRED=1
    fi
    normalize_alloy_state_volume "$volume_name"
    return 0
  fi

  if [[ -n "$alloy_container" ]]; then
    # Stop cleanly so positions.yml and the remote-write WAL are consistent,
    # then stage the disposable container-layer state on the host before
    # writing anything into the new named volume. This ordering prevents the
    # first persistent-state rollout from causing one final historical replay.
    if [[ "$state" == "running" ]]; then
      docker stop --time=30 "$alloy_container" >/dev/null
    fi

    staging="$(mktemp -d "${DEPLOY_DIR}/.alloy-state.XXXXXX")"
    if ! docker cp "${alloy_container}:/data-alloy/." "$staging/"; then
      rm -rf "$staging"
      echo "ERROR: failed to stage current Alloy state" >&2
      return 1
    fi
    if ! docker run --rm --network none --user 0:0 \
      -v "${staging}:/source:ro" \
      -v "${volume_name}:/data-alloy" \
      --entrypoint sh "$ALLOY_IMAGE" \
      -c 'tar -C /source -cf - . | tar -C /data-alloy -xpf -'; then
      rm -rf "$staging"
      echo "ERROR: failed to seed persistent Alloy state" >&2
      return 1
    fi
    rm -rf "$staging"
    echo "Migrated Alloy state from ${alloy_container} into ${volume_name}" >&2
    ALLOY_STATE_ACTIVATION_REQUIRED=1
  else
    echo "No existing Alloy container; initializing an empty state volume" >&2
  fi

  docker run --rm --network none --user 0:0 \
    -v "${volume_name}:/data-alloy" \
    --entrypoint sh "$ALLOY_IMAGE" \
    -c "touch '${marker}'"
  normalize_alloy_state_volume "$volume_name"
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

running_compose_oneoff_containers() {
  docker ps \
    --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
    --filter "label=com.docker.compose.oneoff=True" \
    --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Label "com.docker.compose.service"}}\t{{.Command}}'
}

ensure_no_running_compose_oneoffs() {
  local rows

  rows="$(running_compose_oneoff_containers)"
  if [[ -z "$rows" ]]; then
    echo "No running Docker Compose one-off containers detected for project ${COMPOSE_PROJECT_NAME}" >&2
    return 0
  fi

  cat >&2 <<EOF
ERROR: running Docker Compose one-off containers detected for project ${COMPOSE_PROJECT_NAME}.
Deploy is refusing to overlap a production one-off job because it may keep
running older crawler code while this deploy restarts services and reseeds
Redis-backed schedules.

Container ID\tName\tImage\tStatus\tCompose service\tCommand
${rows}

Wait for the one-off job to finish, or intentionally stop it after confirming
with the operator who started it, then rerun the deploy.
EOF
  return 1
}

running_typesense_maintenance_containers() {
  docker ps \
    --filter 'name=^/crawler-(backfill|refresh)-typesense-' \
    --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Command}}'
}

ensure_no_running_typesense_maintenance() {
  local rows

  rows="$(running_typesense_maintenance_containers)"
  if [[ -z "$rows" ]]; then
    echo "No running Typesense maintenance containers detected" >&2
    return 0
  fi

  cat >&2 <<EOF
ERROR: running Typesense maintenance containers detected.
Deploy is refusing to overlap a full backfill or count refresh because its
inline crawler sync also refreshes Typesense and could publish partial counts.

Container ID\tName\tImage\tStatus\tCommand
${rows}

Wait for the maintenance job to finish, then rerun the deploy.
EOF
  return 1
}

# Fail before touching services if an operator one-off is still running.
# Example: `docker compose run --rm worker-1 uv run --no-sync crawler ...`
# receives the Compose label `com.docker.compose.oneoff=True` and otherwise
# survives the named-service stop/recreate sequence below.
ensure_no_running_compose_oneoffs
ensure_no_running_typesense_maintenance

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

# Activate persistent Alloy state before any later deploy step can fail and
# run the rollback against the new Compose service spec. On the first rollout
# this migrates the live cursor and immediately recreates Alloy on the volume;
# later deploys take the no-restart fast path here.
prepare_alloy_state_volume
if (( ALLOY_STATE_ACTIVATION_REQUIRED )); then
  docker compose up -d --force-recreate alloy
fi

ensure_deploy_disk_headroom

pull_deploy_images

docker compose up -d redis

# ── Quiesce every local-Postgres writer before schema cutover ──────
# Migrations may introduce a database/runtime protocol (for example the
# shared-writer/exclusive-exporter CDC barrier). Stop both sides before
# Alembic so no old process can write or advance a cursor in the interval
# between the schema change and the new containers starting. `--timeout 60`
# matches the app's 30s bounded drain with headroom before Docker sends
# SIGKILL. Redis and Alloy remain available throughout.
docker compose stop --timeout 60 worker-1 worker-2 worker-3 browser-1 exporter drain

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
