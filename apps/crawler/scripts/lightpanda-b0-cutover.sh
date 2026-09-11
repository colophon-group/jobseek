#!/usr/bin/env bash
# Exact cold cutover/rollback wrapper for the fixed Lightpanda B0 cohort.
set -euo pipefail

DEPLOY_DIR=/home/deploy
ENV_FILE="$DEPLOY_DIR/.env"
OVERLAY="$DEPLOY_DIR/lightpanda-b0-enabled.override.yml"
RECEIPT="$DEPLOY_DIR/.lightpanda-b0-active-v1"
LOCK=/run/lock/jobseek-crawler-mutation.lock

usage() {
  echo "usage: $0 activate|rollback|recover-pending c1|c4" >&2
  exit 2
}

validate_fixed_b0_identity() {
  [[ "$LIGHTPANDA_B0_SERVICE_HOST" == 10.0.0.5 &&
    "$LIGHTPANDA_B0_QUEUE_NAMESPACE" == production-b0 &&
    "$LIGHTPANDA_B0_SHARD_ID" == lightpanda-b0 &&
    "$LIGHTPANDA_B0_ROUTING_EPOCH" == 1 ]] || {
    echo "ERROR: B0 service and route identity must match the reviewed production tuple" >&2
    return 1
  }
}

[[ $# == 2 ]] || usage
OPERATION=$1
COHORT=$2
[[ "$OPERATION" == activate || "$OPERATION" == rollback || "$OPERATION" == recover-pending ]] || usage
[[ "$COHORT" == c1 || "$COHORT" == c4 ]] || usage
[[ "$(id -un)" == deploy ]] || {
  echo "ERROR: B0 cutover must run as the deploy user" >&2
  exit 1
}
[[ -d "$DEPLOY_DIR" && ! -L "$DEPLOY_DIR" && -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "ERROR: active crawler deployment or environment is unsafe" >&2
  exit 1
}
[[ -f "$OVERLAY" && ! -L "$OVERLAY" ]] || {
  echo "ERROR: reviewed B0 activation overlay is unavailable" >&2
  exit 1
}

exec 9>"$LOCK"
flock -n 9 || {
  echo "ERROR: crawler mutation lock is held by deploy or maintenance" >&2
  exit 1
}

cd "$DEPLOY_DIR"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${LIGHTPANDA_B0_QUEUE_NAMESPACE:?required}"
: "${LIGHTPANDA_B0_SHARD_ID:?required}"
: "${LIGHTPANDA_B0_ROUTING_EPOCH:?required}"
: "${LIGHTPANDA_B0_SERVICE_HOST:?required}"
: "${CRAWLER_IMAGE_REF:?required}"
: "${JOBSEEK_DEPLOY_REVISION:?required}"
validate_fixed_b0_identity
[[ "$JOBSEEK_DEPLOY_REVISION" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ERROR: JOBSEEK_DEPLOY_REVISION must be a full lowercase Git commit SHA" >&2
  exit 1
}
[[ "$CRAWLER_IMAGE_REF" =~ ^ghcr\.io/[^/]+/jobseek-crawler@sha256:[0-9a-f]{64}$ ]] || {
  echo "ERROR: CRAWLER_IMAGE_REF must be an immutable crawler digest" >&2
  exit 1
}
export LIGHTPANDA_B0_PRODUCER_COHORT="$COHORT"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$DEPLOY_DIR")}"
export COMPOSE_PROJECT_NAME

compose_enabled=(docker compose -f docker-compose.yml -f lightpanda-b0-enabled.override.yml)
compose_base=(docker compose -f docker-compose.yml)
mutation_services=(worker-1 worker-2 worker-3 browser-1 drain lightpanda-claimant lightpanda-executor)
activation_failure_containment_armed=0
recovery_failure_containment_armed=0

bounded() {
  timeout --foreground --signal=TERM --kill-after=5s "$@"
}

contain_activation_failure() {
  local status=$?
  trap - EXIT
  if ((status != 0 && (activation_failure_containment_armed == 1 || recovery_failure_containment_armed == 1))); then
    echo "ERROR: B0 mutation failed after cold attestation; containing the crawler lane" >&2
    bounded 90s "${compose_enabled[@]}" stop --timeout 60 "${mutation_services[@]}" || true
    # A daemon/API failure can make graceful stop return nonzero after only a
    # subset was stopped. Follow with an idempotent hard stop and retain the
    # pending receipt so ordinary deploy remains fail-closed.
    bounded 30s "${compose_enabled[@]}" kill "${mutation_services[@]}" || true
    if ! terminate_running_oneoffs; then
      echo "CRITICAL: B0 containment could not terminate Compose one-offs; the receipt remains" >&2
    fi
    if ! attest_cold_host; then
      echo "CRITICAL: activation containment could not attest a cold crawler host; the pending receipt remains" >&2
    fi
  fi
  exit "$status"
}

trap contain_activation_failure EXIT

running_oneoffs() {
  bounded 15s docker ps \
    --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
    --filter "label=com.docker.compose.oneoff=True" \
    --format '{{.ID}} {{.Names}} {{.Label "com.docker.compose.service"}}'
}

terminate_running_oneoffs() {
  local container_ids_output container_id
  if ! container_ids_output="$(bounded 15s docker ps -q \
    --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
    --filter "label=com.docker.compose.oneoff=True")"; then
    echo "ERROR: failed to enumerate running Compose one-offs for containment" >&2
    return 1
  fi
  while IFS= read -r container_id; do
    [[ -z "$container_id" ]] && continue
    [[ "$container_id" =~ ^[0-9a-f]{12,64}$ ]] || {
      echo "ERROR: Docker returned an invalid one-off container ID" >&2
      return 1
    }
    bounded 15s docker kill "$container_id" >/dev/null || return 1
  done <<<"$container_ids_output"
}

attest_cold_host() {
  local rows service container_ids_output container_id running
  if ! rows="$(running_oneoffs)"; then
    echo "ERROR: failed to enumerate Compose one-off containers" >&2
    return 1
  fi
  if [[ -n "$rows" ]]; then
    echo "ERROR: existing Compose one-off containers prevent cold B0 mutation:" >&2
    echo "$rows" >&2
    return 1
  fi
  for service in "${mutation_services[@]}"; do
    if ! container_ids_output="$(bounded 15s "${compose_enabled[@]}" ps -aq "$service" 2>/dev/null)"; then
      echo "ERROR: failed to enumerate containers for service: $service" >&2
      return 1
    fi
    while IFS= read -r container_id; do
      [[ -z "$container_id" ]] && continue
      if ! running="$(bounded 15s docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null)"; then
        echo "ERROR: failed to inspect container for service $service: $container_id" >&2
        return 1
      fi
      if [[ "$running" != false ]]; then
        echo "ERROR: service container is running or has invalid state: $service $container_id" >&2
        return 1
      fi
    done <<<"$container_ids_output"
  done
}

wait_healthy() {
  local compose_kind=$1
  shift
  local service container_id state attempt
  for service in "$@"; do
    for attempt in $(seq 1 48); do
      if [[ "$compose_kind" == enabled ]]; then
        container_id="$(bounded 15s "${compose_enabled[@]}" ps -q "$service" 2>/dev/null || true)"
      else
        container_id="$(bounded 15s "${compose_base[@]}" ps -q "$service" 2>/dev/null || true)"
      fi
      state=""
      if [[ -n "$container_id" ]]; then
        state="$(bounded 15s docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
      fi
      if [[ "$state" == healthy || "$state" == running ]]; then
        break
      fi
      if ((attempt == 48)); then
        echo "ERROR: service did not become healthy: $service ($state)" >&2
        return 1
      fi
      sleep 5
    done
  done
}

extract_digest() {
  local output=$1 digest
  digest="$(sed -n 's/.*"digest": "\([0-9a-f]\{64\}\)".*/\1/p' <<<"$output")"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: operator plan did not return one canonical digest" >&2
    return 1
  }
  echo "$digest"
}

fsync_path() {
  local kind=$1 path=$2
  python3 - "$kind" "$path" <<'PY'
import os
import sys

kind, path = sys.argv[1:]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
if kind == "directory":
    flags |= getattr(os, "O_DIRECTORY", 0)
elif kind != "file":
    raise SystemExit("invalid fsync path kind")
descriptor = os.open(path, flags)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

write_receipt() {
  local digest=$1 state=$2 temp compose_digest
  compose_digest="$(bounded 30s "${compose_enabled[@]}" config | sha256sum | awk '{print $1}')"
  temp="$(mktemp "$DEPLOY_DIR/.lightpanda-b0-active-v1.tmp.XXXXXX")"
  trap 'rm -f -- "${temp:-}"' RETURN
  umask 077
  printf '%s\n' \
    'schema=jobseek.lightpanda-b0-active/v1' \
    "state=$state" \
    "cohort=$COHORT" \
    "namespace=$LIGHTPANDA_B0_QUEUE_NAMESPACE" \
    "shard_id=$LIGHTPANDA_B0_SHARD_ID" \
    "routing_epoch=$LIGHTPANDA_B0_ROUTING_EPOCH" \
    "plan_digest=$digest" \
    "compose_digest=$compose_digest" \
    "crawler_image_ref=$CRAWLER_IMAGE_REF" \
    "deploy_revision=$JOBSEEK_DEPLOY_REVISION" \
    "activated_at_epoch=$(date +%s)" >"$temp"
  chmod 600 "$temp"
  fsync_path file "$temp"
  mv -f -- "$temp" "$RECEIPT"
  fsync_path directory "$DEPLOY_DIR"
  trap - RETURN
  [[ -f "$RECEIPT" && ! -L "$RECEIPT" && "$(stat -c '%a' "$RECEIPT")" == 600 ]] || {
    echo "ERROR: B0 activation receipt publication failed" >&2
    return 1
  }
}

attest_receipt() {
  local expected_state=$1 compose_digest
  [[ "$expected_state" == active || "$expected_state" == pending ]] || return 1
  if ! compose_digest="$(bounded 30s "${compose_enabled[@]}" config | sha256sum | awk '{print $1}')"; then
    echo "ERROR: current B0 Compose digest could not be computed" >&2
    return 1
  fi
  python3 - \
    "$RECEIPT" "$COHORT" "$LIGHTPANDA_B0_QUEUE_NAMESPACE" \
    "$LIGHTPANDA_B0_SHARD_ID" "$LIGHTPANDA_B0_ROUTING_EPOCH" \
    "$CRAWLER_IMAGE_REF" "$JOBSEEK_DEPLOY_REVISION" "$compose_digest" \
    "$expected_state" <<'PY'
import os
import re
import stat
import sys

path, cohort, namespace, shard_id, routing_epoch, image_ref, revision, compose_digest, state = (
    sys.argv[1:]
)
expected_keys = {
    "schema",
    "state",
    "cohort",
    "namespace",
    "shard_id",
    "routing_epoch",
    "plan_digest",
    "compose_digest",
    "crawler_image_ref",
    "deploy_revision",
    "activated_at_epoch",
}


def reject() -> None:
    print("ERROR: B0 activation receipt is incomplete, unsafe, or drifted", file=sys.stderr)
    raise SystemExit(1)


try:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        payload = os.read(descriptor, 4097)
    finally:
        os.close(descriptor)
except OSError:
    reject()
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or metadata.st_uid != os.geteuid()
    or len(payload) > 4096
):
    reject()
try:
    text = payload.decode("ascii")
except UnicodeDecodeError:
    reject()
if not text.endswith("\n") or "\r" in text or "\x00" in text:
    reject()
lines = text[:-1].split("\n")
if len(lines) != len(expected_keys) or any(line.count("=") != 1 for line in lines):
    reject()
pairs = [line.split("=", 1) for line in lines]
values = dict(pairs)
if len(values) != len(pairs) or set(values) != expected_keys:
    reject()
expected_values = {
    "schema": "jobseek.lightpanda-b0-active/v1",
    "state": state,
    "cohort": cohort,
    "namespace": namespace,
    "shard_id": shard_id,
    "routing_epoch": routing_epoch,
    "compose_digest": compose_digest,
    "crawler_image_ref": image_ref,
    "deploy_revision": revision,
}
if any(values[key] != value for key, value in expected_values.items()):
    reject()
if (
    re.fullmatch(r"[0-9a-f]{64}", values["plan_digest"]) is None
    or re.fullmatch(r"[0-9a-f]{64}", values["compose_digest"]) is None
    or re.fullmatch(r"[1-9][0-9]*", values["activated_at_epoch"]) is None
    or re.fullmatch(r"[0-9]+", values["routing_epoch"]) is None
    or re.fullmatch(r"[0-9a-f]{40}", values["deploy_revision"]) is None
    or re.fullmatch(
        r"ghcr\.io/[^/]+/jobseek-crawler@sha256:[0-9a-f]{64}",
        values["crawler_image_ref"],
    )
    is None
):
    reject()
PY
}

if [[ "$OPERATION" == activate ]]; then
  if [[ -e "$RECEIPT" || -L "$RECEIPT" ]]; then
    [[ -f "$RECEIPT" && ! -L "$RECEIPT" && "$(stat -c '%a' "$RECEIPT")" == 600 ]] || {
      echo "ERROR: existing B0 activation receipt is unsafe" >&2
      exit 1
    }
  fi
  bounded 30s "${compose_enabled[@]}" config -q
  bounded 90s "${compose_enabled[@]}" stop --timeout 60 "${mutation_services[@]}"
  attest_cold_host
  activation_failure_containment_armed=1
  plan_output="$(bounded 90s "${compose_enabled[@]}" run --rm --no-deps worker-1 \
    uv run --no-sync lightpanda-b0-activation plan --operation activate --cohort "$COHORT")"
  plan_digest="$(extract_digest "$plan_output")"
  # Publish a fail-closed pending receipt before the first per-task Lua turn;
  # an apply can be partially complete only if a later authoritative race is
  # rejected, and ordinary deploy must not restart Python into that state.
  write_receipt "$plan_digest" pending
  bounded 90s "${compose_enabled[@]}" run --rm --no-deps worker-1 \
    uv run --no-sync lightpanda-b0-activation activate --cohort "$COHORT" \
    --apply --expect-digest "$plan_digest"
  bounded 90s "${compose_enabled[@]}" up -d --force-recreate \
    worker-1 worker-2 worker-3 browser-1 drain lightpanda-executor lightpanda-claimant
  wait_healthy enabled worker-1 worker-2 worker-3 browser-1 drain lightpanda-executor lightpanda-claimant
  write_receipt "$plan_digest" active
  activation_failure_containment_armed=0
  echo "Go Lightpanda B0 ${COHORT} active; receipt: $RECEIPT"
  exit 0
fi

receipt_state=active
[[ "$OPERATION" == recover-pending ]] && receipt_state=pending
attest_receipt "$receipt_state"
recovery_failure_containment_armed=1
bounded 30s "${compose_enabled[@]}" config -q
bounded 90s "${compose_enabled[@]}" stop --timeout 60 "${mutation_services[@]}"
attest_cold_host
bounded 90s "${compose_enabled[@]}" run --rm --no-deps \
  -e LIGHTPANDA_B0_PRODUCER_MODE=off worker-1 \
  uv run --no-sync lightpanda-b0-activation settle-rollback --cohort "$COHORT"
plan_output="$(bounded 90s "${compose_enabled[@]}" run --rm --no-deps \
  -e LIGHTPANDA_B0_PRODUCER_MODE=off worker-1 \
  uv run --no-sync lightpanda-b0-activation plan --operation rollback --cohort "$COHORT")"
plan_digest="$(extract_digest "$plan_output")"
bounded 90s "${compose_enabled[@]}" run --rm --no-deps \
  -e LIGHTPANDA_B0_PRODUCER_MODE=off worker-1 \
  uv run --no-sync lightpanda-b0-activation rollback --cohort "$COHORT" \
  --apply --expect-digest "$plan_digest"
bounded 90s "${compose_base[@]}" up -d --force-recreate \
  worker-1 worker-2 worker-3 browser-1 drain lightpanda-claimant
wait_healthy base worker-1 worker-2 worker-3 browser-1 drain lightpanda-claimant
rm -f -- "$RECEIPT"
[[ ! -e "$RECEIPT" && ! -L "$RECEIPT" ]] || {
  echo "ERROR: B0 receipt removal failed after healthy Python rollback" >&2
  exit 1
}
recovery_failure_containment_armed=0
echo "Go Lightpanda B0 ${COHORT} rolled back to Python"
