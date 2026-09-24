#!/usr/bin/env bash
# Exact cold cutover/rollback wrapper for the fixed Lightpanda B0 cohort.
set -euo pipefail

DEPLOY_DIR=/home/deploy
ENV_FILE="$DEPLOY_DIR/.env"
OVERLAY="$DEPLOY_DIR/lightpanda-b0-enabled.override.yml"
RECEIPT="$DEPLOY_DIR/.lightpanda-b0-active-v1"
LOCK=/run/lock/jobseek-crawler-mutation.lock

usage() {
  echo "usage: $0 activate|rollback|recover-pending c1|c2|c3" >&2
  exit 2
}

validate_fixed_b0_identity() {
  [[ "$LIGHTPANDA_B0_SERVICE_HOST" == 10.0.0.5 &&
    "$LIGHTPANDA_B0_QUEUE_NAMESPACE" == production-b0 &&
    "$LIGHTPANDA_B0_SHARD_ID" == lightpanda-b0 &&
    "$LIGHTPANDA_B0_ROUTING_EPOCH" =~ ^[1-9][0-9]{0,12}$ ]] || {
    echo "ERROR: B0 service and route identity must match the reviewed production tuple" >&2
    return 1
  }
  ((LIGHTPANDA_B0_ROUTING_EPOCH <= 9999999999999)) || {
    echo "ERROR: B0 routing epoch exceeds the reviewed canonical bound" >&2
    return 1
  }
}

[[ $# == 2 ]] || usage
OPERATION=$1
COHORT=$2
[[ "$OPERATION" == activate || "$OPERATION" == rollback || "$OPERATION" == recover-pending ]] || usage
[[ "$COHORT" == c1 || "$COHORT" == c2 || "$COHORT" == c3 ]] || usage
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
# The deploy .env escapes JSON for Compose. Sourcing it through Bash removes
# those escapes, so let Compose read this one list directly from its .env file.
unset WEBSHARE_PROXY_URLS

: "${LIGHTPANDA_B0_QUEUE_NAMESPACE:?required}"
: "${LIGHTPANDA_B0_SHARD_ID:?required}"
: "${LIGHTPANDA_B0_SERVICE_HOST:?required}"
: "${CRAWLER_IMAGE_REF:?required}"
: "${JOBSEEK_DEPLOY_REVISION:?required}"
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
mutation_services=(worker-1 worker-2 worker-3 browser-1 drain lightpanda-producer-socket-init lightpanda-executor-socket-init lightpanda-producer lightpanda-claimant lightpanda-executor)
restart_candidate_services=(worker-1 worker-2 worker-3 browser-1 drain lightpanda-producer lightpanda-executor lightpanda-claimant)
route_environment=(
  -e "LIGHTPANDA_B0_QUEUE_NAMESPACE=$LIGHTPANDA_B0_QUEUE_NAMESPACE"
  -e "LIGHTPANDA_B0_SHARD_ID=$LIGHTPANDA_B0_SHARD_ID"
  -e LIGHTPANDA_B0_ROUTING_EPOCH
)
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

resolved_candidate_container() {
  local service=$1 container_id labels
  container_id="$(bounded 15s "${compose_enabled[@]}" ps -q "$service")" || return 1
  [[ "$container_id" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: candidate service does not resolve to one exact container: $service" >&2
    return 1
  }
  labels="$(bounded 15s docker inspect -f \
    '{{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}}' \
    "$container_id")" || return 1
  [[ "$labels" == "$COMPOSE_PROJECT_NAME $service" ]] || {
    echo "ERROR: candidate container identity is not exact: $service" >&2
    return 1
  }
  echo "$container_id"
}

expected_restart_policy() {
  if [[ "$1" == lightpanda-producer ]]; then
    echo "on-failure:5"
  else
    echo "unless-stopped"
  fi
}

expected_restart_inspection() {
  if [[ "$1" == lightpanda-producer ]]; then
    echo "on-failure:5"
  else
    echo "unless-stopped:0"
  fi
}

verify_pending_restart_disabled() {
  local service container_id actual
  for service in "${restart_candidate_services[@]}"; do
    container_id="$(resolved_candidate_container "$service")" || return 1
    actual="$(bounded 15s docker inspect -f \
      '{{.HostConfig.RestartPolicy.Name}}:{{.HostConfig.RestartPolicy.MaximumRetryCount}}' \
      "$container_id")" || return 1
    [[ "$actual" == "no:0" ]] || {
      echo "ERROR: pending candidate can restart before active receipt: $service" >&2
      return 1
    }
  done
}

arm_and_verify_active_restart_policies() {
  local service container_id current_id policy expected actual running
  for service in "${restart_candidate_services[@]}"; do
    container_id="$(resolved_candidate_container "$service")" || return 1
    policy="$(expected_restart_policy "$service")"
    expected="$(expected_restart_inspection "$service")"
    bounded 15s docker update --restart "$policy" "$container_id" >/dev/null || return 1
    actual="$(bounded 15s docker inspect -f \
      '{{.HostConfig.RestartPolicy.Name}}:{{.HostConfig.RestartPolicy.MaximumRetryCount}}' \
      "$container_id")" || return 1
    [[ "$actual" == "$expected" ]] || {
      echo "ERROR: active candidate restart policy is not exact: $service" >&2
      return 1
    }
    current_id="$(resolved_candidate_container "$service")" || return 1
    [[ "$current_id" == "$container_id" ]] || {
      echo "ERROR: active candidate container changed while arming restart: $service" >&2
      return 1
    }
    running="$(bounded 15s docker inspect -f '{{.State.Running}}' "$current_id")" || return 1
    [[ "$running" == true ]] || {
      echo "ERROR: active candidate stopped while arming restart: $service" >&2
      return 1
    }
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

extract_rollback_digest() {
  local output=$1 digest
  digest="$(sed -n 's/.*"rollback_plan_digest": "\([0-9a-f]\{64\}\)".*/\1/p' <<<"$output")"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: rollback plan did not return one commit digest" >&2
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

load_receipt_identity() {
  python3 - "$RECEIPT" "$COHORT" "$LIGHTPANDA_B0_QUEUE_NAMESPACE" \
    "$LIGHTPANDA_B0_SHARD_ID" "$CRAWLER_IMAGE_REF" "$JOBSEEK_DEPLOY_REVISION" <<'PY'
import os
import re
import stat
import sys

path, cohort, namespace, shard_id, image_ref, revision = sys.argv[1:]

def reject() -> None:
    print("ERROR: B0 activation receipt identity is incomplete or unsafe", file=sys.stderr)
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
    or metadata.st_nlink != 1
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
if any(line.count("=") != 1 for line in lines):
    reject()
pairs = [line.split("=", 1) for line in lines]
values = dict(pairs)
state = values.get("state")
expected_keys = {
    "schema", "state", "cohort", "namespace", "shard_id", "routing_epoch",
    "plan_digest", "compose_digest", "crawler_image_ref", "deploy_revision",
    "activated_at_epoch",
}
if state in {"rollback-pending", "rollback-cleared"}:
    expected_keys.update({
        "source_receipt_state", "source_receipt_sha256", "retirement_routing_epoch",
    })
if len(values) != len(pairs) or set(values) != expected_keys:
    reject()
epoch_text = values["routing_epoch"]
if (
    values["schema"] != "jobseek.lightpanda-b0-active/v1"
    or state not in {"active", "pending", "rollback-pending", "rollback-cleared"}
    or values["cohort"] != cohort
    or values["namespace"] != namespace
    or values["shard_id"] != shard_id
    or values["crawler_image_ref"] != image_ref
    or values["deploy_revision"] != revision
    or re.fullmatch(r"[1-9][0-9]{0,12}", epoch_text) is None
    or int(epoch_text) > 9_999_999_999_999
    or re.fullmatch(r"[0-9a-f]{64}", values["plan_digest"]) is None
    or re.fullmatch(r"[0-9a-f]{64}", values["compose_digest"]) is None
    or re.fullmatch(r"[1-9][0-9]*", values["activated_at_epoch"]) is None
):
    reject()
retirement = values.get("retirement_routing_epoch", "-")
source_state = values.get("source_receipt_state", "-")
source_sha = values.get("source_receipt_sha256", "-")
if state in {"rollback-pending", "rollback-cleared"}:
    retirement_valid = retirement == "unreserved" or (
        re.fullmatch(r"[1-9][0-9]{0,12}", retirement) is not None
        and int(retirement) <= 9_999_999_999_999
        and int(retirement) > int(epoch_text)
    )
    if (
        source_state not in {"active", "pending"}
        or re.fullmatch(r"[0-9a-f]{64}", source_sha) is None
        or not retirement_valid
        or (state == "rollback-cleared" and retirement == "unreserved")
    ):
        reject()
print(state, epoch_text, retirement, source_state, source_sha, values["plan_digest"], sep="\t")
PY
}

reserve_routing_epoch() {
  local output
  output="$(bounded 30s "${compose_base[@]}" run --rm --no-deps worker-1 \
    uv run --no-sync lightpanda-b0-activation reserve-epoch)" || return 1
  python3 - "$output" <<'PY'
import json
import sys

try:
    document = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    raise SystemExit("invalid routing epoch allocator reply")
if set(document) != {"routing_epoch"}:
    raise SystemExit("invalid routing epoch allocator reply")
epoch = document["routing_epoch"]
if isinstance(epoch, bool) or not isinstance(epoch, int) or not 1 <= epoch <= 9_999_999_999_999:
    raise SystemExit("invalid routing epoch allocator reply")
print(epoch)
PY
}

attest_routing_epoch() {
  local output
  output="$(bounded 30s "${compose_base[@]}" run --rm --no-deps \
    "${route_environment[@]}" worker-1 \
    uv run --no-sync lightpanda-b0-activation attest-epoch)" || return 1
  python3 - "$output" "$LIGHTPANDA_B0_ROUTING_EPOCH" <<'PY'
import json
import sys

try:
    document = json.loads(sys.argv[1])
    expected = int(sys.argv[2])
except (IndexError, json.JSONDecodeError, ValueError):
    raise SystemExit("invalid routing epoch attestation reply")
if set(document) != {"routing_epoch", "current"}:
    raise SystemExit("invalid routing epoch attestation reply")
epoch = document["routing_epoch"]
if (
    isinstance(epoch, bool)
    or not isinstance(epoch, int)
    or epoch != expected
    or document["current"] is not True
):
    raise SystemExit("invalid routing epoch attestation reply")
PY
}

write_receipt() {
  local digest=$1 state=$2 source_receipt_sha256=${3:-} retirement_routing_epoch=${4:-} source_receipt_state=${5:-} temp compose_digest
  [[ "$state" == active || "$state" == pending || "$state" == rollback-pending || "$state" == rollback-cleared ]] || {
    echo "ERROR: invalid B0 receipt state" >&2
    return 1
  }
  if [[ "$state" == rollback-pending || "$state" == rollback-cleared ]]; then
    [[ "$source_receipt_sha256" =~ ^[0-9a-f]{64}$ &&
      ("$source_receipt_state" == active || "$source_receipt_state" == pending) ]] || {
      echo "ERROR: rollback receipt requires the exact source receipt identity" >&2
      return 1
    }
    if [[ "$retirement_routing_epoch" != unreserved ]]; then
      if [[ ! "$retirement_routing_epoch" =~ ^[1-9][0-9]{0,12}$ ]] ||
        ((retirement_routing_epoch > 9999999999999)) ||
        ((retirement_routing_epoch <= LIGHTPANDA_B0_ROUTING_EPOCH)); then
        echo "ERROR: rollback retirement epoch must be canonical and newer than its source" >&2
        return 1
      fi
    fi
    [[ "$state" != rollback-cleared || "$retirement_routing_epoch" != unreserved ]] || {
      echo "ERROR: rollback-cleared receipt requires a reserved retirement epoch" >&2
      return 1
    }
  elif [[ -n "$source_receipt_sha256" || -n "$retirement_routing_epoch" || -n "$source_receipt_state" ]]; then
    echo "ERROR: active and pending receipts cannot carry rollback identity" >&2
    return 1
  fi
  compose_digest="$(bounded 30s "${compose_enabled[@]}" config | sha256sum | awk '{print $1}')"
  temp="$(mktemp "$DEPLOY_DIR/.lightpanda-b0-active-v1.tmp.XXXXXX")"
  trap 'rm -f -- "${temp:-}"' RETURN
  umask 077
  receipt_lines=(
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
    "activated_at_epoch=$(date +%s)"
  )
  if [[ "$state" == rollback-pending || "$state" == rollback-cleared ]]; then
    receipt_lines+=(
      "source_receipt_state=$source_receipt_state"
      "source_receipt_sha256=$source_receipt_sha256"
      "retirement_routing_epoch=$retirement_routing_epoch"
    )
  fi
  printf '%s\n' "${receipt_lines[@]}" >"$temp"
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
  local expected_state=$1 compose_digest attestation
  [[ "$expected_state" == active || "$expected_state" == pending || "$expected_state" == rollback-pending || "$expected_state" == rollback-cleared ]] || return 1
  if ! compose_digest="$(bounded 30s "${compose_enabled[@]}" config | sha256sum | awk '{print $1}')"; then
    echo "ERROR: current B0 Compose digest could not be computed" >&2
    return 1
  fi
  attestation="$(python3 - \
    "$RECEIPT" "$COHORT" "$LIGHTPANDA_B0_QUEUE_NAMESPACE" \
    "$LIGHTPANDA_B0_SHARD_ID" "$LIGHTPANDA_B0_ROUTING_EPOCH" \
    "$CRAWLER_IMAGE_REF" "$JOBSEEK_DEPLOY_REVISION" "$compose_digest" \
    "$expected_state" <<'PY'
import hashlib
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
if state in {"rollback-pending", "rollback-cleared"}:
    expected_keys.update({
        "source_receipt_state", "source_receipt_sha256", "retirement_routing_epoch",
    })

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
    or re.fullmatch(r"[1-9][0-9]{0,12}", values["routing_epoch"]) is None
    or int(values["routing_epoch"]) > 9_999_999_999_999
    or re.fullmatch(r"[0-9a-f]{40}", values["deploy_revision"]) is None
    or re.fullmatch(
        r"ghcr\.io/[^/]+/jobseek-crawler@sha256:[0-9a-f]{64}",
        values["crawler_image_ref"],
    )
    is None
):
    reject()
retirement = values.get("retirement_routing_epoch", "-")
source_state = values.get("source_receipt_state", "-")
source_sha = values.get("source_receipt_sha256", "-")
if state in {"rollback-pending", "rollback-cleared"}:
    retirement_valid = retirement == "unreserved" or (
        re.fullmatch(r"[1-9][0-9]{0,12}", retirement) is not None
        and int(retirement) <= 9_999_999_999_999
        and int(retirement) > int(values["routing_epoch"])
    )
    if (
        source_state not in {"active", "pending"}
        or re.fullmatch(r"[0-9a-f]{64}", source_sha) is None
        or not retirement_valid
        or (state == "rollback-cleared" and retirement == "unreserved")
    ):
        reject()
print(
    hashlib.sha256(payload).hexdigest(),
    values["plan_digest"],
    source_sha,
    retirement,
    source_state,
    sep="\t",
)
PY
  )" || return 1
  IFS=$'\t' read -r ATTESTED_RECEIPT_SHA256 ATTESTED_PLAN_DIGEST ATTESTED_SOURCE_RECEIPT_SHA256 ATTESTED_RETIREMENT_ROUTING_EPOCH ATTESTED_SOURCE_RECEIPT_STATE <<<"$attestation"
  [[ "$ATTESTED_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ &&
    "$ATTESTED_PLAN_DIGEST" =~ ^[0-9a-f]{64}$ ]] || return 1
}

clear_producer_activation_sentinel() {
  bounded 30s "${compose_enabled[@]}" run --rm --no-deps \
    -e "LIGHTPANDA_B0_ROLLBACK_PLAN_DIGEST=$rollback_plan_digest" \
    -e "LIGHTPANDA_B0_SOURCE_RECEIPT_SHA256=$source_receipt_sha256" \
    --entrypoint /usr/local/bin/lightpanda-b0-supervisor \
    lightpanda-producer producer --clear-activation-sentinel
}

check_producer_activation_sentinel_clearable() {
  bounded 30s "${compose_enabled[@]}" run --rm --no-deps \
    --entrypoint /usr/local/bin/lightpanda-b0-supervisor \
    lightpanda-producer producer --check-activation-sentinel-clearable
}

check_producer_activation_sentinel_absent() {
  bounded 30s "${compose_enabled[@]}" run --rm --no-deps \
    --entrypoint /usr/local/bin/lightpanda-b0-supervisor \
    lightpanda-producer producer --check-activation-sentinel-absent
}

persist_redis_rdb() {
  local reply attempt
  for attempt in $(seq 1 40); do
    reply=""
    if reply="$(bounded 120s "${compose_base[@]}" exec -T redis redis-cli --raw SAVE)"; then
      [[ "$reply" == OK ]] && return 0
    fi
    if [[ "$reply" == *"Background save already in progress"* && "$attempt" -lt 40 ]]; then
      sleep 2
      continue
    fi
    echo "ERROR: synchronous Redis RDB persistence failed" >&2
    return 1
  done
}

RECEIPT_STATE=""
RECEIPT_RETIREMENT_ROUTING_EPOCH="-"
RECEIPT_SOURCE_STATE="-"
RECEIPT_SOURCE_SHA256="-"
RECEIPT_PLAN_DIGEST="-"
if [[ -e "$RECEIPT" || -L "$RECEIPT" ]]; then
  IFS=$'\t' read -r RECEIPT_STATE LIGHTPANDA_B0_ROUTING_EPOCH \
    RECEIPT_RETIREMENT_ROUTING_EPOCH RECEIPT_SOURCE_STATE \
    RECEIPT_SOURCE_SHA256 RECEIPT_PLAN_DIGEST \
    <<<"$(load_receipt_identity)"
elif [[ "$OPERATION" == activate ]]; then
  # nextval is non-transactional: this incarnation is durably burned under
  # the host mutation lock before any producer, sentinel, or Redis mutation.
  LIGHTPANDA_B0_ROUTING_EPOCH="$(reserve_routing_epoch)"
else
  echo "ERROR: rollback requires an attested activation receipt" >&2
  exit 1
fi
export LIGHTPANDA_B0_ROUTING_EPOCH
validate_fixed_b0_identity

if [[ "$OPERATION" == activate ]]; then
  attest_routing_epoch
  if [[ -n "$RECEIPT_STATE" ]]; then
    [[ "$RECEIPT_STATE" == active ]] || {
      echo "ERROR: incomplete B0 activation must use recover-pending" >&2
      exit 1
    }
    attest_receipt active
  fi
  bounded 30s "${compose_enabled[@]}" config -q
  bounded 90s "${compose_enabled[@]}" stop --timeout 60 "${mutation_services[@]}"
  attest_cold_host
  activation_failure_containment_armed=1
  # Start only the non-claiming authority. Its startup can read Redis but does
  # not initialize or mutate the B0 queue; prepare below is read-only too.
  bounded 30s "${compose_enabled[@]}" run --rm --no-deps lightpanda-producer-socket-init
  bounded 30s "${compose_enabled[@]}" run --rm --no-deps lightpanda-executor-socket-init
  bounded 30s "${compose_enabled[@]}" up -d --no-deps --force-recreate lightpanda-producer
  wait_healthy enabled lightpanda-producer
  plan_output="$(bounded 90s "${compose_enabled[@]}" run --rm --no-deps \
    "${route_environment[@]}" worker-1 \
    uv run --no-sync lightpanda-b0-activation plan --operation activate --cohort "$COHORT")"
  plan_digest="$(extract_digest "$plan_output")"
  # Publish a fail-closed pending receipt before the first per-task Lua turn;
  # an apply can be partially complete only if a later authoritative race is
  # rejected, and ordinary deploy must not restart Python into that state.
  write_receipt "$plan_digest" pending
  bounded 90s "${compose_enabled[@]}" run --rm --no-deps \
    "${route_environment[@]}" worker-1 \
    uv run --no-sync lightpanda-b0-activation activate --cohort "$COHORT" \
    --apply --expect-digest "$plan_digest"
  # The pending receipt remains the recovery authority until every transfer,
  # audit, and owner/route mutation is synchronously present in Redis' RDB.
  # A failed or lost SAVE reply therefore contains the lane and retries only
  # through recover-pending; no mutation service is enabled on uncertainty.
  # Redis SAVE blocks the server longer than the producer's authority probe.
  # Stop that read-only service while Redis persists the completed transfer.
  bounded 30s "${compose_enabled[@]}" stop --timeout 15 lightpanda-producer
  persist_redis_rdb
  bounded 30s "${compose_enabled[@]}" up -d --no-deps --force-recreate lightpanda-producer
  wait_healthy enabled lightpanda-producer
  bounded 90s "${compose_enabled[@]}" up -d --force-recreate \
    worker-1 worker-2 worker-3 browser-1 drain lightpanda-executor lightpanda-claimant
  wait_healthy enabled worker-1 worker-2 worker-3 browser-1 drain lightpanda-producer lightpanda-executor lightpanda-claimant
  verify_pending_restart_disabled
  write_receipt "$plan_digest" active
  arm_and_verify_active_restart_policies
  activation_failure_containment_armed=0
  echo "Go Lightpanda B0 ${COHORT} active; receipt: $RECEIPT"
  exit 0
fi

requested_source_state=active
[[ "$OPERATION" == recover-pending ]] && requested_source_state=pending
case "$RECEIPT_STATE" in
  active|pending)
    [[ "$RECEIPT_STATE" == "$requested_source_state" ]] || {
      echo "ERROR: rollback command does not match the source receipt state" >&2
      exit 1
    }
    attest_receipt "$RECEIPT_STATE"
    [[ "$ATTESTED_PLAN_DIGEST" == "$RECEIPT_PLAN_DIGEST" &&
      "$ATTESTED_SOURCE_RECEIPT_STATE" == "$RECEIPT_SOURCE_STATE" &&
      "$ATTESTED_SOURCE_RECEIPT_SHA256" == "$RECEIPT_SOURCE_SHA256" &&
      "$ATTESTED_RETIREMENT_ROUTING_EPOCH" == "$RECEIPT_RETIREMENT_ROUTING_EPOCH" ]] || {
      echo "ERROR: rollback receipt changed while it was being attested" >&2
      exit 1
    }
    source_receipt_state=$RECEIPT_STATE
    source_receipt_sha256=$ATTESTED_RECEIPT_SHA256
    source_plan_digest=$ATTESTED_PLAN_DIGEST
    retirement_routing_epoch=unreserved
    ;;
  rollback-pending|rollback-cleared)
    [[ "$RECEIPT_SOURCE_STATE" == "$requested_source_state" ]] || {
      echo "ERROR: rollback recovery command does not match the source receipt state" >&2
      exit 1
    }
    attest_receipt "$RECEIPT_STATE"
    [[ "$ATTESTED_PLAN_DIGEST" == "$RECEIPT_PLAN_DIGEST" &&
      "$ATTESTED_SOURCE_RECEIPT_STATE" == "$RECEIPT_SOURCE_STATE" &&
      "$ATTESTED_SOURCE_RECEIPT_SHA256" == "$RECEIPT_SOURCE_SHA256" &&
      "$ATTESTED_RETIREMENT_ROUTING_EPOCH" == "$RECEIPT_RETIREMENT_ROUTING_EPOCH" ]] || {
      echo "ERROR: rollback recovery receipt changed while it was being attested" >&2
      exit 1
    }
    source_receipt_state=$ATTESTED_SOURCE_RECEIPT_STATE
    source_receipt_sha256=$ATTESTED_SOURCE_RECEIPT_SHA256
    source_plan_digest=$ATTESTED_PLAN_DIGEST
    retirement_routing_epoch=$ATTESTED_RETIREMENT_ROUTING_EPOCH
    ;;
  *)
    echo "ERROR: rollback requires an active, pending, or recovery receipt" >&2
    exit 1
    ;;
esac
[[ "$source_receipt_state" == active || "$source_receipt_state" == pending ]] || {
  echo "ERROR: source receipt state is invalid" >&2
  exit 1
}
[[ "$source_receipt_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "ERROR: source receipt SHA-256 is invalid" >&2
  exit 1
}
source_routing_epoch=$LIGHTPANDA_B0_ROUTING_EPOCH
recovery_failure_containment_armed=1
bounded 30s "${compose_enabled[@]}" config -q
bounded 90s "${compose_enabled[@]}" stop --timeout 60 "${mutation_services[@]}"
attest_cold_host

if [[ "$RECEIPT_STATE" == active || "$RECEIPT_STATE" == pending ]]; then
  # Persist recovery intent before touching the non-transactional allocator.
  # A crash after nextval but before the following bound receipt deliberately
  # burns that ambiguous value; retry reserves another strictly newer epoch.
  write_receipt "$source_plan_digest" rollback-pending "$source_receipt_sha256" \
    unreserved "$source_receipt_state"
  RECEIPT_STATE=rollback-pending
fi
if [[ "$retirement_routing_epoch" == unreserved ]]; then
  retirement_routing_epoch="$(reserve_routing_epoch)"
  if [[ ! "$retirement_routing_epoch" =~ ^[1-9][0-9]{0,12}$ ]] ||
    ((retirement_routing_epoch <= source_routing_epoch)); then
    echo "ERROR: allocator did not retire the source routing epoch" >&2
    exit 1
  fi
  write_receipt "$source_plan_digest" rollback-pending "$source_receipt_sha256" \
    "$retirement_routing_epoch" "$source_receipt_state"
fi

# The bound recovery receipt may be reused only while R is still the exact
# PostgreSQL high-water. Restore E immediately afterward for fence/Redis cleanup.
LIGHTPANDA_B0_ROUTING_EPOCH=$retirement_routing_epoch
export LIGHTPANDA_B0_ROUTING_EPOCH
validate_fixed_b0_identity
attest_routing_epoch
LIGHTPANDA_B0_ROUTING_EPOCH=$source_routing_epoch
export LIGHTPANDA_B0_ROUTING_EPOCH
validate_fixed_b0_identity

if [[ "$RECEIPT_STATE" == rollback-cleared ]]; then
  rollback_plan_digest=$source_plan_digest
  check_producer_activation_sentinel_absent
else
  receipt_state=$source_receipt_state
  # This uses the Go-owned exact marker bytes and metadata. It is read-only and
  # must pass before the first Redis G/empty -> T rollback commit.
  check_producer_activation_sentinel_clearable
  bounded 90s "${compose_enabled[@]}" run --rm --no-deps \
    -e LIGHTPANDA_B0_PRODUCER_MODE=off "${route_environment[@]}" worker-1 \
    uv run --no-sync lightpanda-b0-activation settle-rollback --cohort "$COHORT" \
    --receipt-state "$receipt_state" --source-receipt-sha256 "$source_receipt_sha256"
  plan_output="$(bounded 90s "${compose_enabled[@]}" run --rm --no-deps \
    -e LIGHTPANDA_B0_PRODUCER_MODE=off "${route_environment[@]}" worker-1 \
    uv run --no-sync lightpanda-b0-activation plan --operation rollback --cohort "$COHORT" \
    --receipt-state "$receipt_state" --source-receipt-sha256 "$source_receipt_sha256")"
  plan_digest="$(extract_digest "$plan_output")"
  rollback_plan_digest="$(extract_rollback_digest "$plan_output")"
  bounded 90s "${compose_enabled[@]}" run --rm --no-deps \
    -e LIGHTPANDA_B0_PRODUCER_MODE=off "${route_environment[@]}" worker-1 \
    uv run --no-sync lightpanda-b0-activation rollback --cohort "$COHORT" \
    --receipt-state "$receipt_state" --source-receipt-sha256 "$source_receipt_sha256" \
    --apply --expect-digest "$plan_digest"
  # Persist the exact rollback tombstone before changing either recovery
  # attestation. A lost SAVE reply leaves rollback-pending and the producer
  # sentinel intact, so retry can safely re-drive the idempotent rollback.
  persist_redis_rdb
  clear_producer_activation_sentinel
  # Publish only after the sentinel removal is durable. T still exists, so a
  # crash on either side of this receipt write has one exact recovery path.
  write_receipt "$rollback_plan_digest" rollback-cleared "$source_receipt_sha256" \
    "$retirement_routing_epoch" "$source_receipt_state"
fi
bounded 90s "${compose_enabled[@]}" run --rm --no-deps \
  -e LIGHTPANDA_B0_PRODUCER_MODE=off "${route_environment[@]}" worker-1 \
  uv run --no-sync lightpanda-b0-activation clear-rollback-tombstone --cohort "$COHORT" \
  --rollback-plan-digest "$rollback_plan_digest" \
  --source-receipt-sha256 "$source_receipt_sha256" --allow-absent
persist_redis_rdb

# Python may restart only after R (not the retired source E) is re-attested as
# the exact database high-water. A stale Go transaction at E is ordered before
# R or rejected by the migration trigger's shared advisory lock.
LIGHTPANDA_B0_ROUTING_EPOCH=$retirement_routing_epoch
export LIGHTPANDA_B0_ROUTING_EPOCH
validate_fixed_b0_identity
attest_routing_epoch
bounded 90s "${compose_base[@]}" up -d --force-recreate \
  worker-1 worker-2 worker-3 browser-1 drain lightpanda-claimant
wait_healthy base worker-1 worker-2 worker-3 browser-1 drain lightpanda-claimant
rm -f -- "$RECEIPT"
fsync_path directory "$DEPLOY_DIR"
[[ ! -e "$RECEIPT" && ! -L "$RECEIPT" ]] || {
  echo "ERROR: B0 receipt removal failed after healthy Python rollback" >&2
  exit 1
}
recovery_failure_containment_armed=0
echo "Go Lightpanda B0 ${COHORT} rolled back to Python at retired epoch ${retirement_routing_epoch}"
