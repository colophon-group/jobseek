#!/usr/bin/env bash
# Deploy crawler containers on Hetzner (worker machine).
# Postgres runs on a separate dedicated machine.
# Called by CI with env vars set from GitHub secrets.
set -euo pipefail

# Serialize deploys with host-scheduled data maintenance. The database-level
# reconciler lock prevents duplicate jobs, while this host lock also closes
# the race where a new timer starts after deploy preflight but before the old
# writer containers are quiesced.
exec 9>/run/lock/jobseek-crawler-mutation.lock
flock -w 7200 9 || {
  echo "ERROR: timed out waiting for the crawler mutation lock" >&2
  exit 1
}

# ── Validate required env vars ─────────────────────────────────────────
required_vars=(
  OWNER
  CRAWLER_IMAGE_TAG
  CRAWLER_IMAGE_REF
  BROWSER_IMAGE_REF
  JOBSEEK_DEPLOY_REVISION
  JOBSEEK_RECONCILIATION_WRAPPER_SHA256
  WEB_DATABASE_URL
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
  TYPESENSE_OPERATIONS_KEY
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
INCOMING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$DEPLOY_DIR/.env"
ROLLBACK_ENV_FILE="$DEPLOY_DIR/.env.rollback"
ROLLBACK_SPEC_ARCHIVE="$DEPLOY_DIR/.deploy-spec.rollback.tar"
ROLLBACK_POOL_OVERRIDE="$DEPLOY_DIR/.crawler-rollback-pool-budget.override.yml"
ROLLBACK_POOL_OVERRIDE_SOURCE="$INCOMING_DIR/rollback-pool-budget.override.yml"
ACTIVE_COMPOSE_SNAPSHOT="$DEPLOY_DIR/.crawler-active-docker-compose.yml"
ACTIVE_COMPOSE_SNAPSHOT_SHA256="$DEPLOY_DIR/.crawler-active-docker-compose.sha256"
ACTIVE_ENV_SNAPSHOT="$DEPLOY_DIR/.crawler-active.env"
ACTIVE_ENV_SNAPSHOT_SHA256="$DEPLOY_DIR/.crawler-active-env.sha256"
DEPLOY_SUCCESS_FILE="$DEPLOY_DIR/.crawler-deploy-success.env"
ROLLBACK_SUCCESS_FILE="$DEPLOY_DIR/.crawler-deploy-success.rollback"
ENV_FILE_WAS_PRESENT=0
ROLLBACK_ARMED=0
ROLLBACK_RUNNING=0
DEPLOY_SPEC_FILES=(
  deploy.sh
  deploy_helpers.sh
  docker-compose.yml
  alloy.river
  scripts/postgresql-operational-preflight.py
)
if [[ "$INCOMING_DIR" == "$DEPLOY_DIR" ]]; then
  echo "ERROR: deploy artifacts must be staged outside the active deploy directory" >&2
  exit 1
fi
for spec in "${DEPLOY_SPEC_FILES[@]}"; do
  [[ -f "$INCOMING_DIR/$spec" ]] || {
    echo "ERROR: staged deploy artifact is unavailable: ${spec}" >&2
    exit 1
  }
done
[[ -f "$ROLLBACK_POOL_OVERRIDE_SOURCE" && ! -L "$ROLLBACK_POOL_OVERRIDE_SOURCE" ]] || {
  echo "ERROR: staged rollback pool-budget override is unavailable or unsafe" >&2
  exit 1
}
# Staged path is intentionally dynamic; the workflow verifies and supplies it.
# shellcheck disable=SC1091
source "$INCOMING_DIR/deploy_helpers.sh"
IMAGE_TAG="$CRAWLER_IMAGE_TAG"
REDIS_IMAGE="redis:8-alpine@sha256:978f0e01593e65eed801f2402944efcd936d43b5027e4908a7897baf88ed6241"
SHIM_IMAGE_REF="${SHIM_IMAGE_REF:-}"
DEPLOY_MIN_FREE_KB="${DEPLOY_MIN_FREE_KB:-5242880}" # 5 GiB hard floor.
DEPLOY_PRUNE_FREE_KB="${DEPLOY_PRUNE_FREE_KB:-10485760}" # Prune cache below 10 GiB.
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$DEPLOY_DIR")}"
export COMPOSE_PROJECT_NAME
MAINTENANCE_OPERATION=crawler-deploy
MAINTENANCE_ISSUE=3409
MAINTENANCE_BUDGET_SECONDS=1800
MAINTENANCE_MARKER_NAME=""
if [[ ! "$JOBSEEK_DEPLOY_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: JOBSEEK_DEPLOY_REVISION must be a full lowercase Git commit SHA" >&2
  exit 1
fi
if [[ ! "$OWNER" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "ERROR: OWNER must be a lowercase GitHub owner" >&2
  exit 1
fi
if [[ ! "$IMAGE_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-build\.[1-9][0-9]*\.g[0-9a-f]{7,12})?$ ]]; then
  echo "ERROR: CRAWLER_IMAGE_TAG must be a versioned release/build tag" >&2
  exit 1
fi
if [[ ! "$CRAWLER_IMAGE_REF" =~ ^ghcr\.io/${OWNER}/jobseek-crawler@sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: CRAWLER_IMAGE_REF must be an immutable crawler digest" >&2
  exit 1
fi
if [[ ! "$BROWSER_IMAGE_REF" =~ ^ghcr\.io/${OWNER}/jobseek-crawler-browser@sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: BROWSER_IMAGE_REF must be an immutable crawler-browser digest" >&2
  exit 1
fi
if [[ ! "$JOBSEEK_RECONCILIATION_WRAPPER_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: JOBSEEK_RECONCILIATION_WRAPPER_SHA256 must be a lowercase SHA-256" >&2
  exit 1
fi
MAINTENANCE_PROVENANCE_LABELS=(
  --label "com.docker.compose.project=${COMPOSE_PROJECT_NAME}"
  --label com.docker.compose.container-number=1
  --label com.docker.compose.oneoff=True
  --label "jobseek.maintenance.operation=${MAINTENANCE_OPERATION}"
  --label "jobseek.maintenance.issue=${MAINTENANCE_ISSUE}"
  --label "jobseek.maintenance.revision=${JOBSEEK_DEPLOY_REVISION}"
  --label "jobseek.maintenance.budget-seconds=${MAINTENANCE_BUDGET_SECONDS}"
)
ALLOY_IMAGE="grafana/alloy:v1.18.1@sha256:0f4434c92b3e6cdac38bb129b344e1790c246f7b6e2eaffcc16a5fa363240e33"
ALLOY_STATE_ACTIVATION_REQUIRED=0

stop_maintenance_window() {
  if [[ -z "$MAINTENANCE_MARKER_NAME" ]]; then
    return 0
  fi
  docker stop --time=1 "$MAINTENANCE_MARKER_NAME" >/dev/null 2>&1 || true
  docker rm -f "$MAINTENANCE_MARKER_NAME" >/dev/null 2>&1 || true
  MAINTENANCE_MARKER_NAME=""
}

start_maintenance_window() {
  local marker_image

  MAINTENANCE_MARKER_NAME="jobseek-maintenance-window-crawler-deploy-${JOBSEEK_DEPLOY_REVISION:0:12}"
  marker_image="$(docker inspect --format '{{.Image}}' "${COMPOSE_PROJECT_NAME}-redis-1" 2>/dev/null || true)"
  if [[ ! "$marker_image" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    docker pull "$REDIS_IMAGE" >/dev/null
    marker_image="$(docker image inspect --format '{{.Id}}' "$REDIS_IMAGE")"
  fi
  [[ "$marker_image" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: a local Redis image is required for the maintenance marker" >&2
    return 1
  }

  docker run --detach --rm \
    --name "$MAINTENANCE_MARKER_NAME" \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --memory 16m \
    --cpus 0.05 \
    --pids-limit 16 \
    "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
    --label com.docker.compose.service=maintenance-window \
    "$marker_image" \
    /bin/sh -c \
    "trap 'exit 0' TERM INT; sleep 28800" >/dev/null
}

verify_active_snapshot_file() {
  local snapshot="$1"
  local digest_file="$2"
  local label="$3"
  local expected actual

  [[ -f "$snapshot" && ! -L "$snapshot" ]] || {
    echo "ERROR: crawler-confirmed active ${label} snapshot is unavailable or unsafe" >&2
    return 1
  }
  [[ -f "$digest_file" && ! -L "$digest_file" ]] || {
    echo "ERROR: crawler-confirmed active ${label} digest is unavailable or unsafe" >&2
    return 1
  }
  expected="$(tr -d '[:space:]' <"$digest_file")"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: crawler-confirmed active ${label} digest is invalid" >&2
    return 1
  }
  actual="$(sha256sum "$snapshot" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "ERROR: crawler-confirmed active ${label} snapshot failed verification" >&2
    return 1
  }
}

verify_active_deploy_snapshot() {
  verify_active_snapshot_file \
    "$ACTIVE_COMPOSE_SNAPSHOT" "$ACTIVE_COMPOSE_SNAPSHOT_SHA256" Compose
  verify_active_snapshot_file \
    "$ACTIVE_ENV_SNAPSHOT" "$ACTIVE_ENV_SNAPSHOT_SHA256" environment
  [[ "$(stat -c '%a' "$ACTIVE_ENV_SNAPSHOT")" == 600 ]] || {
    echo "ERROR: crawler-confirmed active environment snapshot permissions are unsafe" >&2
    return 1
  }
}

publish_active_deploy_snapshot() {
  local compose_temporary compose_digest_temporary
  local env_temporary env_digest_temporary

  compose_temporary="$(mktemp "${DEPLOY_DIR}/.crawler-active-compose.XXXXXX")"
  compose_digest_temporary="$(mktemp "${DEPLOY_DIR}/.crawler-active-compose-digest.XXXXXX")"
  env_temporary="$(mktemp "${DEPLOY_DIR}/.crawler-active-env.XXXXXX")"
  env_digest_temporary="$(mktemp "${DEPLOY_DIR}/.crawler-active-env-digest.XXXXXX")"
  install -m 0644 "$DEPLOY_DIR/docker-compose.yml" "$compose_temporary"
  install -m 0600 "$ENV_FILE" "$env_temporary"
  sha256sum "$compose_temporary" | awk '{print $1}' >"$compose_digest_temporary"
  sha256sum "$env_temporary" | awk '{print $1}' >"$env_digest_temporary"
  chmod 0644 "$compose_digest_temporary" "$env_digest_temporary"
  mv "$compose_temporary" "$ACTIVE_COMPOSE_SNAPSHOT"
  mv "$compose_digest_temporary" "$ACTIVE_COMPOSE_SNAPSHOT_SHA256"
  mv "$env_temporary" "$ACTIVE_ENV_SNAPSHOT"
  mv "$env_digest_temporary" "$ACTIVE_ENV_SNAPSHOT_SHA256"
}

snapshot_active_deploy_specs() {
  local snapshot_dir spec temporary

  for spec in "${DEPLOY_SPEC_FILES[@]}"; do
    [[ -f "$DEPLOY_DIR/$spec" ]] || {
      echo "ERROR: active deploy artifact is unavailable: ${spec}" >&2
      return 1
    }
  done

  snapshot_dir="$(mktemp -d "${DEPLOY_DIR}/.deploy-spec.snapshot.XXXXXX")"
  for spec in "${DEPLOY_SPEC_FILES[@]}"; do
    if ! install -D -p "$DEPLOY_DIR/$spec" "$snapshot_dir/$spec"; then
      rm -rf "$snapshot_dir"
      return 1
    fi
  done

  # The independently scheduled shim rollout can already have replaced the
  # live Compose file. Only the verified, crawler-confirmed snapshot is valid
  # rollback evidence; the first rollout must pre-seed it explicitly.
  if ! install -m 0644 "$ACTIVE_COMPOSE_SNAPSHOT" "$snapshot_dir/docker-compose.yml"; then
    rm -rf "$snapshot_dir"
    return 1
  fi

  temporary="$(mktemp "${DEPLOY_DIR}/.deploy-spec.rollback.XXXXXX.tar")"
  if ! tar -C "$snapshot_dir" -cpf "$temporary" "${DEPLOY_SPEC_FILES[@]}"; then
    rm -rf "$snapshot_dir"
    rm -f "$temporary"
    return 1
  fi
  rm -rf "$snapshot_dir"
  chmod 600 "$temporary"
  mv "$temporary" "$ROLLBACK_SPEC_ARCHIVE"
}

activate_staged_deploy_specs() {
  local mode spec

  for spec in "${DEPLOY_SPEC_FILES[@]}"; do
    case "$spec" in
      deploy.sh | deploy_helpers.sh | scripts/*.py) mode=0755 ;;
      *) mode=0644 ;;
    esac
    install -D -m "$mode" "$INCOMING_DIR/$spec" "$DEPLOY_DIR/$spec"
  done
  install -m 0644 "$ROLLBACK_POOL_OVERRIDE_SOURCE" "$ROLLBACK_POOL_OVERRIDE"
}

restore_previous_deploy_specs() {
  [[ -f "$ROLLBACK_SPEC_ARCHIVE" && ! -L "$ROLLBACK_SPEC_ARCHIVE" ]] || {
    echo "ERROR: rollback deployment archive is unavailable or unsafe" >&2
    return 1
  }
  tar -C "$DEPLOY_DIR" -xpf "$ROLLBACK_SPEC_ARCHIVE"
}

reconciliation_wrapper_is_compatible() {
  local actual_wrapper_sha256

  [[ -x /usr/local/sbin/jobseek-reconciliation-state ]] || return 1
  [[ -r /usr/local/sbin/jobseek-crawler-reconciliation ]] || return 1
  actual_wrapper_sha256="$({
    sha256sum /usr/local/sbin/jobseek-crawler-reconciliation
  } | awk '{print $1}')"
  [[ "$actual_wrapper_sha256" == "$JOBSEEK_RECONCILIATION_WRAPPER_SHA256" ]] || return 1
  /usr/local/sbin/jobseek-reconciliation-state check \
    --expected-wrapper-sha256 "$JOBSEEK_RECONCILIATION_WRAPPER_SHA256" \
    >/dev/null 2>&1 || return 1
  systemctl is-enabled --quiet jobseek-crawler-reconciliation.timer || return 1
  systemctl is-active --quiet jobseek-crawler-reconciliation.timer || return 1
}

ensure_reconciliation_wrapper_compatible() {
  local deadline=$((SECONDS + ${RECONCILIATION_COMPAT_WAIT_SECONDS:-1200}))

  while (( SECONDS < deadline )); do
    if reconciliation_wrapper_is_compatible; then
      echo "Installed reconciliation wrapper matches the required contract" >&2
      return 0
    fi
    echo "Waiting for the compatible reconciliation host wrapper" >&2
    sleep 10
  done

  echo "ERROR: compatible reconciliation host wrapper was not installed before timeout" >&2
  return 1
}

configure_rollback_compose_contract() {
  local compose_file_count compose_file_value expected

  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
    echo "ERROR: restored rollback environment is unavailable or unsafe" >&2
    return 1
  }
  [[ -f "$DEPLOY_DIR/docker-compose.yml" && ! -L "$DEPLOY_DIR/docker-compose.yml" ]] || {
    echo "ERROR: restored rollback Compose file is unavailable or unsafe" >&2
    return 1
  }
  [[ -f "$ROLLBACK_POOL_OVERRIDE" && ! -L "$ROLLBACK_POOL_OVERRIDE" ]] || {
    echo "ERROR: rollback pool-budget override is unavailable or unsafe" >&2
    return 1
  }

  expected="COMPOSE_FILE=$DEPLOY_DIR/docker-compose.yml:$ROLLBACK_POOL_OVERRIDE"
  compose_file_count="$(grep -Ec '^COMPOSE_FILE=' "$ENV_FILE" || true)"
  compose_file_value="$(grep -E '^COMPOSE_FILE=' "$ENV_FILE" || true)"
  if ((compose_file_count == 0)); then
    printf '\n%s\n' "$expected" >>"$ENV_FILE" || return 1
  elif ((compose_file_count != 1)) || [[ "$compose_file_value" != "$expected" ]]; then
    echo "ERROR: restored rollback environment has an unexpected Compose contract" >&2
    return 1
  fi
  chmod 600 "$ENV_FILE"
}

rollback_compose() {
  # Compose gives process variables precedence over the restored env file.
  # Run rollback in a clean environment so the failed release tag and every
  # other current SSH input cannot override the previous deployment contract.
  env -i \
    "PATH=${PATH:-/usr/local/bin:/usr/bin:/bin}" \
    "HOME=${HOME:-$DEPLOY_DIR}" \
    "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME" \
    docker compose \
    --env-file "$ENV_FILE" \
    -f "$DEPLOY_DIR/docker-compose.yml" \
    -f "$ROLLBACK_POOL_OVERRIDE" \
    "$@"
}

rollback_compose_service_ready() {
  local service="$1"
  local container_id state health

  container_id="$(rollback_compose ps -q "$service" 2>/dev/null)" || return 1
  [[ -n "$container_id" && "$(wc -w <<<"$container_id")" -eq 1 ]] || return 1
  state="$(docker inspect -f '{{.State.Status}}' "$container_id" 2>/dev/null)" || return 1
  [[ "$state" == "running" ]] || return 1
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null)" || return 1
  [[ "$health" == "none" || "$health" == "healthy" ]] || return 1
  if [[ "$service" == "alloy" ]]; then
    curl --fail --silent --show-error --max-time 2 \
      http://127.0.0.1:12346/-/ready >/dev/null
  fi
}

wait_for_rollback_core_services() {
  local services=(redis worker-1 worker-2 worker-3 browser-1 exporter drain alloy)
  local deadline=$((SECONDS + ${ROLLBACK_HEALTH_TIMEOUT_SECONDS:-180}))
  local service
  local missing=()

  while ((SECONDS < deadline)); do
    missing=()
    for service in "${services[@]}"; do
      if ! rollback_compose_service_ready "$service"; then
        missing+=("$service")
      fi
    done
    if ((${#missing[@]} == 0)); then
      return 0
    fi
    echo "Waiting for rollback services to become ready: ${missing[*]}" >&2
    sleep 5
  done

  echo "ERROR: rollback services did not become ready: ${missing[*]}" >&2
  rollback_compose ps >&2 || true
  return 1
}

rollback_deploy() {
  local exit_code="${1:-1}"
  local command_status=0
  local rollback_status=0
  local quiesce_complete=0
  local env_restore_complete=0
  local spec_restore_complete=0
  local success_marker_restore_complete=0
  local bounded_contract_persisted=0
  local rollback_stack_started=0

  trap - ERR EXIT HUP INT TERM
  if (( ! ROLLBACK_ARMED || ROLLBACK_RUNNING )); then
    exit "$exit_code"
  fi
  ROLLBACK_RUNNING=1
  set +e
  echo "Deploy failed — restoring crawler containers on previous image" >&2

  cd "$DEPLOY_DIR"
  command_status=$?
  if ((command_status != 0)); then
    rollback_status=$command_status
  else
    # Stop every local-PostgreSQL crawler owner before restoring the archived
    # spec. Starting old and new generations together would violate the same
    # budget that this rollback is responsible for preserving.
    docker compose \
      --env-file "$ENV_FILE" \
      -f "$DEPLOY_DIR/docker-compose.yml" \
      stop --timeout 60 worker-1 worker-2 worker-3 browser-1 exporter drain
    command_status=$?
    if ((command_status == 0)); then
      quiesce_complete=1
    fi
  fi
  if ((command_status != 0 && rollback_status == 0)); then
    rollback_status=$command_status
  fi
  if ((ENV_FILE_WAS_PRESENT)); then
    if [[ -f "$ROLLBACK_ENV_FILE" && ! -L "$ROLLBACK_ENV_FILE" ]]; then
      mv "$ROLLBACK_ENV_FILE" "$ENV_FILE"
      command_status=$?
      if ((command_status == 0)); then
        chmod 600 "$ENV_FILE"
        command_status=$?
      fi
    else
      echo "ERROR: rollback environment snapshot is unavailable or unsafe" >&2
      command_status=1
    fi
  else
    rm -f "$ENV_FILE"
    command_status=$?
  fi
  if ((command_status == 0)); then
    env_restore_complete=1
  fi
  if ((command_status != 0 && rollback_status == 0)); then
    rollback_status=$command_status
  fi

  if [[ -f "$ROLLBACK_SUCCESS_FILE" && ! -L "$ROLLBACK_SUCCESS_FILE" ]]; then
    mv "$ROLLBACK_SUCCESS_FILE" "$DEPLOY_SUCCESS_FILE"
    command_status=$?
    if ((command_status == 0)); then
      chmod 0644 "$DEPLOY_SUCCESS_FILE"
      command_status=$?
    fi
  else
    echo "ERROR: crawler rollback success marker is unavailable or unsafe" >&2
    command_status=1
  fi
  if ((command_status == 0)); then
    success_marker_restore_complete=1
  elif ((rollback_status == 0)); then
    rollback_status=$command_status
  fi

  restore_previous_deploy_specs
  command_status=$?
  if ((command_status == 0)); then
    spec_restore_complete=1
  fi
  if ((command_status != 0 && rollback_status == 0)); then
    rollback_status=$command_status
  fi

  # Persist the bounded old-stack contract whenever both authoritative
  # rollback inputs were restored, even if quiescing returned nonzero. A
  # partial stop must prevent restart, but it must not leave later operator
  # recovery pointed at the unbounded pre-budget Compose contract.
  if ((env_restore_complete && spec_restore_complete)); then
    configure_rollback_compose_contract
    command_status=$?
    if ((command_status == 0)); then
      bounded_contract_persisted=1
    elif ((rollback_status == 0)); then
      rollback_status=$command_status
    fi
  fi
  if (( ! quiesce_complete && bounded_contract_persisted )); then
    echo "ERROR: rollback quiesce was incomplete; bounded old-stack contract persisted but restart skipped" >&2
  fi
  if ((quiesce_complete && env_restore_complete && spec_restore_complete && success_marker_restore_complete && bounded_contract_persisted)); then
    rollback_compose up -d --remove-orphans
    command_status=$?
    if ((command_status == 0)); then
      rollback_stack_started=1
    elif ((rollback_status == 0)); then
      rollback_status=$command_status
    fi
  fi
  if ((rollback_stack_started)); then
    wait_for_rollback_core_services
    command_status=$?
    if ((command_status != 0 && rollback_status == 0)); then
      rollback_status=$command_status
    fi
  fi

  if ((rollback_status == 0)); then
    publish_active_deploy_snapshot
    command_status=$?
    if ((command_status != 0)); then
      echo "ERROR: crawler rollback could not republish its restored contract" >&2
      rollback_status=$command_status
    fi
  else
    echo "ERROR: retaining the last verified crawler snapshot after rollback failure" >&2
  fi

  stop_maintenance_window
  ROLLBACK_ARMED=0
  if ((rollback_status != 0)); then
    echo "ERROR: crawler rollback failed with status ${rollback_status}" >&2
    exit "$rollback_status"
  fi
  echo "Crawler rollback restored the previous image with bounded PostgreSQL pools" >&2
  exit "$exit_code"
}

arm_deploy_rollback() {
  ROLLBACK_ARMED=1
  trap 'rollback_deploy $?' ERR
  trap 'rollback_deploy $?' EXIT
  trap 'rollback_deploy 129' HUP
  trap 'rollback_deploy 130' INT
  trap 'rollback_deploy 143' TERM
}

disarm_deploy_rollback() {
  ROLLBACK_ARMED=0
  trap - ERR EXIT HUP INT TERM
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
    "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
    --label com.docker.compose.service=deploy-alloy-state \
    -v "${volume_name}:/data-alloy" \
    --entrypoint sh "$ALLOY_IMAGE" \
    -c 'chown -R 0:0 /data-alloy && chmod 0700 /data-alloy'
}

prepare_alloy_state_volume() {
  local volume_name="${COMPOSE_PROJECT_NAME}_alloy-data"
  local marker="/data-alloy/.jobseek-persistent-state"
  local alloy_container marker_status state state_volume staging

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
  marker_status="$(docker run --rm --network none --user 0:0 \
    "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
    --label com.docker.compose.service=deploy-alloy-state \
    -v "${volume_name}:/data-alloy" \
    --entrypoint sh "$ALLOY_IMAGE" \
    -c "if test -f '${marker}'; then echo present; else echo missing; fi")"
  if [[ "$marker_status" == "present" ]]; then
    echo "Alloy state volume already initialized: ${volume_name}" >&2
    if [[ "$state" != "running" || "$state_volume" != "$volume_name" ]]; then
      # A prior deploy may have prepared the volume and failed before the
      # changed service spec became active. Recreate immediately on retry.
      ALLOY_STATE_ACTIVATION_REQUIRED=1
    fi
    normalize_alloy_state_volume "$volume_name"
    return 0
  fi
  if [[ "$marker_status" != "missing" ]]; then
    echo "ERROR: unexpected Alloy state marker probe result" >&2
    return 1
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
      "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
      --label com.docker.compose.service=deploy-alloy-state \
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
    "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
    --label com.docker.compose.service=deploy-alloy-state \
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

resolve_shim_image_ref() {
  local candidate="$SHIM_IMAGE_REF"
  local existing_container configured_ref persisted_ref
  local -a persisted_refs=()

  # A coupled Murmur/crawler rollout passes the attested same-revision digest
  # explicitly. Never replace it with live state: a prior crawler attempt may
  # have failed and rolled the host back to the previous shim release.
  if [[ -n "$candidate" ]]; then
    if [[ ! "$candidate" =~ ^ghcr\.io/${OWNER}/jobseek-murmur-shim@sha256:[0-9a-f]{64}$ ]]; then
      echo "ERROR: SHIM_IMAGE_REF must be an immutable jobseek-murmur-shim digest" >&2
      return 1
    fi
    export SHIM_IMAGE_REF
    return 0
  fi

  # Crawler-only revisions do not build a same-head shim. Resolve their shim
  # from the live environment only when the running container agrees exactly;
  # accepting either source alone would allow drift to become the next release.
  if [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]]; then
    mapfile -t persisted_refs < <(sed -n 's/^SHIM_IMAGE_REF=//p' "$ENV_FILE")
    if (( ${#persisted_refs[@]} != 1 )); then
      echo "ERROR: active deploy environment must contain exactly one SHIM_IMAGE_REF value" >&2
      return 1
    fi
    persisted_ref="${persisted_refs[0]}"
  else
    echo "ERROR: active deploy environment is unavailable for SHIM_IMAGE_REF resolution" >&2
    return 1
  fi

  existing_container="${COMPOSE_PROJECT_NAME}-murmur-shim-1"
  configured_ref="$(
    docker inspect "$existing_container" --format '{{.Config.Image}}' 2>/dev/null || true
  )"
  if [[ ! "$persisted_ref" =~ ^ghcr\.io/${OWNER}/jobseek-murmur-shim@sha256:[0-9a-f]{64}$ ]] ||
    [[ "$configured_ref" != "$persisted_ref" ]]
  then
    echo "ERROR: live environment and Murmur container do not attest one immutable image" >&2
    return 1
  fi
  SHIM_IMAGE_REF="$persisted_ref"
  export SHIM_IMAGE_REF
}

read_exact_shim_ref() {
  local file="$1"
  local label="$2"
  local -a refs=()

  if [[ ! -f "$file" || -L "$file" ]]; then
    echo "ERROR: ${label} is not a regular non-symlink file" >&2
    return 1
  fi
  mapfile -t refs < <(sed -n 's/^SHIM_IMAGE_REF=//p' "$file")
  if (( ${#refs[@]} != 1 )); then
    echo "ERROR: ${label} must contain exactly one SHIM_IMAGE_REF value" >&2
    return 1
  fi
  if [[ ! "${refs[0]}" =~ ^ghcr\.io/${OWNER}/jobseek-murmur-shim@sha256:[0-9a-f]{64}$ ]]; then
    echo "ERROR: ${label} contains a malformed SHIM_IMAGE_REF value" >&2
    return 1
  fi
  printf '%s\n' "${refs[0]}"
}

verify_shim_deploy_contract() {
  local success_file="$1"
  local live_ref success_ref container_id container_ref

  live_ref="$(read_exact_shim_ref "$ENV_FILE" "active deploy environment")"
  success_ref="$(read_exact_shim_ref "$success_file" "crawler success marker")"
  container_id="$(docker compose ps -aq murmur-shim 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    echo "ERROR: Murmur deploy contract found no live container" >&2
    return 1
  fi
  container_ref="$(docker inspect "$container_id" --format '{{.Config.Image}}')"
  if [[ "$live_ref" != "$SHIM_IMAGE_REF" ||
    "$container_ref" != "$SHIM_IMAGE_REF" ||
    "$success_ref" != "$SHIM_IMAGE_REF" ]]
  then
    echo "ERROR: Murmur live environment, container, and success marker disagree" >&2
    return 1
  fi
}

verify_compose_service_image() {
  local service="$1"
  local expected_ref="$2"
  local container_id actual_ref

  container_id="$(docker compose ps -aq "$service" 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    echo "ERROR: image identity check found no container for ${service}" >&2
    return 1
  fi
  actual_ref="$(docker inspect "$container_id" --format '{{.Config.Image}}')"
  if [[ "$actual_ref" != "$expected_ref" ]]; then
    echo "ERROR: ${service} image identity does not match the deployment manifest" >&2
    return 1
  fi
}

verify_deployed_image_identity() {
  local service

  verify_compose_service_image redis "$REDIS_IMAGE"
  for service in worker-1 worker-2 worker-3 exporter drain murmur-shim-runtime-init; do
    verify_compose_service_image "$service" "$CRAWLER_IMAGE_REF"
  done
  verify_compose_service_image browser-1 "$BROWSER_IMAGE_REF"
  verify_compose_service_image murmur-shim "$SHIM_IMAGE_REF"
  verify_compose_service_image alloy "$ALLOY_IMAGE"
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
ensure_reconciliation_wrapper_compatible
python3 "$INCOMING_DIR/scripts/postgresql-operational-preflight.py"
verify_active_deploy_snapshot

# Snapshot the complete active deployment contract before replacing any file
# or credential. Rollback restores the old Compose spec and old env together,
# so an old image never starts with the new credential semantics.
rm -f "$ROLLBACK_ENV_FILE" "$ROLLBACK_SPEC_ARCHIVE"
if [[ -f "$DEPLOY_SUCCESS_FILE" && ! -L "$DEPLOY_SUCCESS_FILE" ]]; then
  install -m 0644 "$DEPLOY_SUCCESS_FILE" "$ROLLBACK_SUCCESS_FILE"
else
  echo "ERROR: crawler success marker is unavailable or unsafe" >&2
  exit 1
fi
snapshot_active_deploy_specs
ENV_FILE_WAS_PRESENT=1
install -m 0600 "$ACTIVE_ENV_SNAPSHOT" "$ROLLBACK_ENV_FILE"
arm_deploy_rollback
activate_staged_deploy_specs
resolve_shim_image_ref

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

cat > "$ENV_FILE" <<EOF
OWNER=${OWNER}
CRAWLER_IMAGE_TAG=${IMAGE_TAG}
CRAWLER_IMAGE_REF=${CRAWLER_IMAGE_REF}
BROWSER_IMAGE_REF=${BROWSER_IMAGE_REF}
SHIM_IMAGE_REF=${SHIM_IMAGE_REF}
JOBSEEK_DEPLOY_REVISION=${JOBSEEK_DEPLOY_REVISION}
WEB_DATABASE_URL=${WEB_DATABASE_URL}
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
TYPESENSE_OPERATIONS_KEY=${TYPESENSE_OPERATIONS_KEY}
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
start_maintenance_window

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
# shared-writer/oldest-writer-floor CDC boundary). Stop both sides before
# Alembic so no old process can write or advance a cursor in the interval
# between the schema change and the new containers starting. `--timeout 60`
# matches the app's 30s bounded drain with headroom before Docker sends
# SIGKILL. Redis and Alloy remain available throughout.
docker compose stop --timeout 60 worker-1 worker-2 worker-3 browser-1 exporter drain

# ── Run Alembic migrations on local Postgres ─────────────────────────
docker run --rm \
  -e LOCAL_DATABASE_URL \
  -e CRAWLER_DB_ROLE=deploy-migrate \
  --network host \
  "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
  --label com.docker.compose.service=deploy-migrate \
  "$CRAWLER_IMAGE_REF" \
  uv run --no-sync alembic -c src/migrations/alembic.ini upgrade head

# ── Patch Typesense schema (idempotent — adds new fields if missing) ─
# Must run BEFORE `crawler sync`, otherwise the next sync would upsert
# docs containing fields that the live schema doesn't know about.
docker run --rm \
  -e TYPESENSE_HOST \
  -e TYPESENSE_PORT \
  -e TYPESENSE_PROTOCOL \
  -e TYPESENSE_OPERATIONS_KEY \
  --network host \
  "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
  --label com.docker.compose.service=deploy-setup-typesense \
  "$CRAWLER_IMAGE_REF" \
  uv run --no-sync crawler setup-typesense

# ── Sync board config from CSV → local Postgres + Redis + Typesense ──
docker run --rm \
  -e LOCAL_DATABASE_URL \
  -e WEB_DATABASE_URL \
  -e CRAWLER_DB_ROLE=deploy-sync \
  -e CRAWLER_DB_POOL_MIN=0 \
  -e CRAWLER_DB_POOL_MAX=4 \
  -e TYPESENSE_HOST \
  -e TYPESENSE_PORT \
  -e TYPESENSE_PROTOCOL \
  -e TYPESENSE_OPERATIONS_KEY \
  --network host \
  "${MAINTENANCE_PROVENANCE_LABELS[@]}" \
  --label com.docker.compose.service=deploy-sync \
  "$CRAWLER_IMAGE_REF" \
  uv run --no-sync crawler sync

# ── Start the full stack on the freshly seeded Redis state ───────────
# Coupled rollout marker (2026-08-04): this comment-only deploy contract
# change intentionally triggers the same-revision Murmur workflow and keeps
# the crawler workflow behind its Murmur safety wait for this one rollout.
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
reconciliation_wrapper_is_compatible
verify_deployed_image_identity
stop_maintenance_window

# ── Cleanup ──────────────────────────────────────────────────────────
deploy_success_temporary="$(mktemp "${DEPLOY_DIR}/.crawler-deploy-success.XXXXXX")"
printf '%s\n' \
  "CRAWLER_IMAGE_TAG=$IMAGE_TAG" \
  "CRAWLER_IMAGE_REF=$CRAWLER_IMAGE_REF" \
  "BROWSER_IMAGE_REF=$BROWSER_IMAGE_REF" \
  "REDIS_IMAGE_REF=$REDIS_IMAGE" \
  "SHIM_IMAGE_REF=$SHIM_IMAGE_REF" \
  "ALLOY_IMAGE_REF=$ALLOY_IMAGE" \
  "JOBSEEK_DEPLOY_REVISION=$JOBSEEK_DEPLOY_REVISION" \
  >"$deploy_success_temporary"
chmod 0644 "$deploy_success_temporary"
verify_shim_deploy_contract "$deploy_success_temporary"
publish_active_deploy_snapshot
# Publish and verify the exact committed release after all health gates pass
# while rollback is still armed. Consumers must use this atomic marker rather
# than the earlier .env write, which is intentionally part of the rollback
# window.
mv "$deploy_success_temporary" "$DEPLOY_SUCCESS_FILE"
verify_shim_deploy_contract "$DEPLOY_SUCCESS_FILE"
disarm_deploy_rollback
rm -f "$ROLLBACK_ENV_FILE" "$ROLLBACK_SPEC_ARCHIVE" "$ROLLBACK_SUCCESS_FILE" || true
docker image prune -f || true
echo "Deploy complete: $(docker compose ps --format '{{.Name}}' | tr '\n' ' ')"
