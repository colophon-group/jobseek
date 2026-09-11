#!/usr/bin/env bash
# Install one renderer generation as deploy; failures leave the renderer cold.
set -euo pipefail
set +x
umask 077

STAGE="${1:-}"
SOURCE_COMMIT="${2:-}"
IMAGE_REF="${3:-}"
RELEASE_ID="${4:-}"
ROOT=/home/deploy/.local/share/jobseek-lightpanda
RELEASE_ROOT="$ROOT/releases"
GENERATION="$RELEASE_ROOT/$RELEASE_ID"
ACTIVE="$ROOT/active"
PROJECT=jobseek-lightpanda
SERVICE=renderer
CONTAINER=jobseek-lightpanda-renderer
NETWORK=jobseek-lightpanda-renderer
EGRESS_NETWORK=jobseek-lightpanda-egress
POLICY=/usr/local/libexec/jobseek-lightpanda-network-policy
HOST_LOCK=/run/lock/jobseek-lightpanda-network.lock
CI_FAILURE_MODE="${JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE:-}"
ci_rollback_smoke=0

trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$STAGE" =~ ^/tmp/jobseek-lightpanda-renderer\.r[0-9]+a[0-9]+\.[A-Za-z0-9]+$ ]] || exit 2
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 2
if [[ -n "$CI_FAILURE_MODE" ]]; then
  [[ "$CI_FAILURE_MODE" == success || "$CI_FAILURE_MODE" == after-candidate || \
    "$CI_FAILURE_MODE" == after-candidate-remove-ambiguous || \
    "$CI_FAILURE_MODE" == after-active-switch || \
    "$CI_FAILURE_MODE" == before-compose || \
    "$CI_FAILURE_MODE" == during-compose || \
    "$CI_FAILURE_MODE" == crash-after-predecessor-stop || \
    "$CI_FAILURE_MODE" == crash-after-compose-create || \
    "$CI_FAILURE_MODE" == crash-after-candidate ]] || exit 2
  [[ "${CI:-}" == true && "${GITHUB_ACTIONS:-}" == true ]] || exit 2
  [[ "$IMAGE_REF" == jobseek-lightpanda-renderer:pr ]] || exit 2
  [[ "$RELEASE_ID" =~ ^sha-${SOURCE_COMMIT}-ci-r[0-9]+a[0-9]+$ ]] || exit 2
  ci_rollback_smoke=1
else
  [[ "$IMAGE_REF" =~ ^ghcr\.io/colophon-group/jobseek-lightpanda-renderer@sha256:[0-9a-f]{64}$ ]] || exit 2
  [[ "$RELEASE_ID" =~ ^sha-${SOURCE_COMMIT}-r[0-9]+a[0-9]+$ ]] || exit 2
fi
[[ "$(id -u)" -ne 0 && "$(id -un)" == deploy ]] || exit 2
for command in base64 docker flock ip openssl python3 readlink sha256sum sudo; do
  command -v "$command" >/dev/null || {
    echo "required renderer deployment command is absent" >&2
    exit 1
  }
done

if (( ! ci_rollback_smoke )); then
  IFS= read -r username_b64
  IFS= read -r token_b64
  [[ -n "$username_b64" && -n "$token_b64" ]] || exit 2
  GHCR_PULL_USERNAME="$(printf '%s' "$username_b64" | base64 --decode)"
  GHCR_PULL_TOKEN="$(printf '%s' "$token_b64" | base64 --decode)"
  unset username_b64 token_b64
  [[ "$GHCR_PULL_USERNAME" =~ ^[A-Za-z0-9_-]+$ && -n "$GHCR_PULL_TOKEN" ]] || exit 2
fi

for relative in compose.yml inventory.json verify.py validate_pki.py lock.sh pins.env release.env pki/ca.pem pki/server.pem pki/server-key.pem pki/client.pem; do
  [[ -f "$STAGE/$relative" && ! -L "$STAGE/$relative" ]] || exit 2
  [[ "$(stat -c '%s' "$STAGE/$relative")" -le 131072 ]] || exit 2
done
read -r EXPECTED_POLICY_SHA256 EXPECTED_INVENTORY_SHA256 < <(
  python3 "$STAGE/verify.py" policy-digests "$STAGE/release.env"
)
[[ "$EXPECTED_POLICY_SHA256" =~ ^[0-9a-f]{64}$ ]] || exit 2
[[ "$EXPECTED_INVENTORY_SHA256" =~ ^[0-9a-f]{64}$ ]] || exit 2

# Recompute every public pin after transport. This also repeats the full
# signature, profile, validity, SAN/EKU, key-format, and key-match checks.
remote_pins="$(mktemp "$STAGE/pins.remote.XXXXXX")"
python3 "$STAGE/validate_pki.py" \
  --ca "$STAGE/pki/ca.pem" \
  --server "$STAGE/pki/server.pem" \
  --server-key "$STAGE/pki/server-key.pem" \
  --client "$STAGE/pki/client.pem" \
  --output "$remote_pins"
cmp --silent "$STAGE/pins.env" "$remote_pins" || {
  echo "renderer public pins changed during transport" >&2
  exit 1
}
rm -f -- "$remote_pins"

protected_before="$STAGE/protected-before.json"
if [[ -e "$ROOT" || -L "$ROOT" ]]; then
  [[ -d "$ROOT" && ! -L "$ROOT" ]] || exit 1
else
  install -d -m 0700 "$ROOT"
fi
[[ "$(stat -c '%U:%G:%a' "$ROOT")" == deploy:deploy:700 ]] || exit 1
install -d -m 0700 "$RELEASE_ROOT"
# shellcheck source=deploy/lightpanda-renderer/lock.sh
source "$STAGE/lock.sh"
[[ -f "$HOST_LOCK" && ! -L "$HOST_LOCK" ]] || exit 1
[[ "$(stat -c '%U:%G:%a:%h' "$HOST_LOCK")" == root:deploy:640:1 ]] || exit 1
exec 7<"$HOST_LOCK"
[[ "$(stat -Lc '%d:%i' /proc/$$/fd/7)" == "$(stat -Lc '%d:%i' "$HOST_LOCK")" ]] || exit 1
flock -w 900 7 || { echo "host policy lock is busy" >&2; exit 1; }
[[ "$(stat -Lc '%d:%i' /proc/$$/fd/7)" == "$(stat -Lc '%d:%i' "$HOST_LOCK")" ]] || exit 1
acquire_renderer_lock "$ROOT/renderer.lock" 900 || {
  echo "renderer deployment lock is busy" >&2
  exit 1
}
python3 "$STAGE/verify.py" snapshot-protected "$protected_before"
stable_empty_egress() {
  local endpoint_count
  for _scan in 1 2; do
    if docker network inspect "$EGRESS_NETWORK" >/dev/null 2>&1; then
      endpoint_count="$(docker network inspect --format '{{len .Containers}}' "$EGRESS_NETWORK")" || return 1
      [[ "$endpoint_count" == 0 ]] || return 1
    fi
  done
}
stable_empty_renderer_networks() {
  local endpoint_count network_name
  for _scan in 1 2; do
    for network_name in "$NETWORK" "$EGRESS_NETWORK"; do
      if docker network inspect "$network_name" >/dev/null 2>&1; then
        endpoint_count="$(docker network inspect --format '{{len .Containers}}' "$network_name")" || return 1
        [[ "$endpoint_count" == 0 ]] || return 1
      fi
    done
  done
}
early_containment_armed=1
early_containment() {
  status=$?
  trap - EXIT HUP INT TERM
  if (( early_containment_armed )) && [[ "$status" -ne 0 ]]; then
    sudo -n "$POLICY" quarantine || status=1
    stable_empty_egress || status=1
    python3 "$STAGE/verify.py" assert-protected "$protected_before" || status=1
  fi
  exit "$status"
}
trap early_containment EXIT
# Static inventory validation is safe before retry ownership is known. The
# live-host/network preflight runs only after an exact owned predecessor has
# been quarantined and removed, so a Compose-created candidate with no live
# endpoint can be recovered without weakening impostor handling.
python3 "$STAGE/verify.py" inventory-file "$STAGE/inventory.json"
renderer_exists=0
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  renderer_exists=1
fi
previous_generation=""
previous_container_id=""
active_existed=0
if [[ -e "$ACTIVE" || -L "$ACTIVE" ]]; then
  [[ -L "$ACTIVE" ]] || exit 1
  active_existed=1
  previous_generation="$(readlink -f "$ACTIVE")"
  if (( ci_rollback_smoke )); then
    [[ "$previous_generation" =~ ^${RELEASE_ROOT}/sha-[0-9a-f]{40}-ci-r[0-9]+a[0-9]+$ ]] || exit 1
  else
    [[ "$previous_generation" =~ ^${RELEASE_ROOT}/sha-[0-9a-f]{40}-r[0-9]+a[0-9]+$ ]] || exit 1
  fi
  [[ -d "$previous_generation" && -f "$previous_generation/compose.yml" && -f "$previous_generation/release.env" ]] || exit 1
fi
if (( renderer_exists )); then
  existing_id="$(docker container inspect --format '{{.Id}}' "$CONTAINER")"
  existing_name="$(docker container inspect --format '{{.Name}}' "$existing_id")"
  existing_project="$(docker container inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$existing_id")"
  existing_service="$(docker container inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$existing_id")"
  existing_mode="$(docker container inspect --format '{{index .Config.Labels "org.jobseek.lightpanda.mode"}}' "$existing_id")"
  existing_release_id="$(docker container inspect --format '{{index .Config.Labels "org.jobseek.lightpanda.release"}}' "$existing_id")"
  existing_image_ref="$(docker container inspect --format '{{index .Config.Labels "org.jobseek.lightpanda.image-ref"}}' "$existing_id")"
  existing_source="$(docker container inspect --format '{{index .Config.Labels "org.jobseek.lightpanda.source-commit"}}' "$existing_id")"
  [[ "$existing_source" =~ ^[0-9a-f]{40}$ ]] || exit 1
  [[ "$existing_id" =~ ^[0-9a-f]{64}$ \
    && "$existing_name" == "/$CONTAINER" \
    && "$existing_project" == "$PROJECT" \
    && "$existing_service" == "$SERVICE" \
    && "$existing_mode" == dormant-controlled-egress \
    && "$existing_release_id" =~ ^sha-${existing_source}-(ci-)?r[0-9]+a[0-9]+$ ]] || exit 1
  if (( ci_rollback_smoke )); then
    [[ "$existing_image_ref" == jobseek-lightpanda-renderer:pr \
      && "$existing_release_id" =~ ^sha-${existing_source}-ci-r[0-9]+a[0-9]+$ ]] || exit 1
  else
    [[ "$existing_image_ref" =~ ^ghcr\.io/colophon-group/jobseek-lightpanda-renderer@sha256:[0-9a-f]{64}$ \
      && "$existing_release_id" =~ ^sha-${existing_source}-r[0-9]+a[0-9]+$ ]] || exit 1
  fi
  existing_generation="$RELEASE_ROOT/$existing_release_id"
  [[ -d "$existing_generation" && ! -L "$existing_generation" \
    && "$(readlink -f "$existing_generation")" == "$existing_generation" \
    && "$(stat -c '%U:%G:%a' "$existing_generation")" == deploy:deploy:711 ]] || exit 1
  for artifact in verify.py release.env inventory.json compose.yml pki/ca.pem pki/server.pem pki/server-key.pem; do
    [[ -f "$existing_generation/$artifact" && ! -L "$existing_generation/$artifact" ]] || exit 1
  done
  [[ "$(stat -c '%U:%G:%a:%h' "$existing_generation/verify.py")" == deploy:deploy:555:1 \
    && "$(stat -c '%U:%G:%a:%h' "$existing_generation/release.env")" == deploy:deploy:600:1 \
    && "$(stat -c '%U:%G:%a:%h' "$existing_generation/inventory.json")" == deploy:deploy:644:1 \
    && "$(stat -c '%U:%G:%a:%h' "$existing_generation/compose.yml")" == deploy:deploy:644:1 \
    && "$(stat -c '%U:%G:%a:%h' "$existing_generation/pki/ca.pem")" == deploy:deploy:444:1 \
    && "$(stat -c '%U:%G:%a:%h' "$existing_generation/pki/server.pem")" == deploy:deploy:444:1 \
    && "$(stat -c '%u:%g:%a:%h' "$existing_generation/pki/server-key.pem")" == 10001:10001:400:1 ]] || exit 1
  existing_running="$(docker container inspect --format '{{json .State.Running}}' "$existing_id")"
  existing_status="$(docker container inspect --format '{{.State.Status}}' "$existing_id")"
  if [[ "$existing_running" == true ]]; then
    python3 "$existing_generation/verify.py" running \
      "$existing_generation/release.env" --expected-id "$existing_id" >/dev/null
    sudo -n "$POLICY" verify-running-ready \
      "$EXPECTED_POLICY_SHA256" "$EXPECTED_INVENTORY_SHA256" >/dev/null
  elif [[ "$existing_running" == false && "$existing_status" == created ]]; then
    python3 "$existing_generation/verify.py" owned-created \
      "$existing_generation/release.env" --expected-id "$existing_id" >/dev/null
  elif [[ "$existing_running" == false ]]; then
    python3 "$existing_generation/verify.py" owned \
      "$existing_generation/release.env" --expected-id "$existing_id" >/dev/null
  else
    exit 1
  fi
  docker stop --time 30 "$existing_id" >/dev/null 2>&1 || :
  [[ "$(docker container inspect --format '{{json .State.Running}}' "$existing_id")" == false ]] || exit 1
  if [[ "$CI_FAILURE_MODE" == crash-after-predecessor-stop ]]; then
    echo "CI retry smoke: killing deploy after exact predecessor stop" >&2
    kill -KILL "$$"
  fi
  sudo -n "$POLICY" quarantine >/dev/null
  for network_name in "$NETWORK" "$EGRESS_NETWORK"; do
    docker network disconnect --force "$network_name" "$existing_id" >/dev/null 2>&1 || :
  done
  stable_empty_renderer_networks
  docker rm --force "$existing_id" >/dev/null 2>&1 || :
  if docker container inspect "$existing_id" >/dev/null 2>&1; then
    docker rm --force "$existing_id" >/dev/null 2>&1 || :
  fi
  ! docker container inspect "$existing_id" >/dev/null 2>&1 || exit 1
  ! docker container inspect "$CONTAINER" >/dev/null 2>&1 || exit 1
  stable_empty_renderer_networks
  sudo -n "$POLICY" verify-ready \
    "$EXPECTED_POLICY_SHA256" "$EXPECTED_INVENTORY_SHA256" >/dev/null
fi
previous_container_id=""
if (( ! ci_rollback_smoke )); then
  python3 "$STAGE/verify.py" inventory "$STAGE/inventory.json"
fi

docker_config=""
cleanup_auth() {
  if [[ -n "$docker_config" ]]; then
    rm -rf -- "$docker_config"
  fi
}
fsync_directories() {
  python3 - "$@" <<'PY'
import os
import sys

for path in sys.argv[1:]:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY
}
fsync_files() {
  python3 - "$@" <<'PY'
import os
import stat
import sys

for path in sys.argv[1:]:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(f"generation artifact is not regular: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY
}
pre_generation_failure() {
  status=$?
  trap - EXIT HUP INT TERM
  cleanup_auth
  if [[ "$status" -ne 0 ]]; then
    sudo -n "$POLICY" quarantine || status=1
    stable_empty_egress || status=1
    python3 "$STAGE/verify.py" assert-protected "$protected_before" || status=1
  fi
  exit "$status"
}
trap pre_generation_failure EXIT
if (( ci_rollback_smoke )); then
  python3 "$STAGE/verify.py" image "$IMAGE_REF" "$SOURCE_COMMIT" --ci-release-id "$RELEASE_ID"
else
  docker_config="$(mktemp -d /tmp/jobseek-lightpanda-renderer-auth.XXXXXX)"
  printf '%s' "$GHCR_PULL_TOKEN" | DOCKER_CONFIG="$docker_config" docker login ghcr.io \
    --username "$GHCR_PULL_USERNAME" --password-stdin >/dev/null
  unset GHCR_PULL_TOKEN GHCR_PULL_USERNAME
  DOCKER_CONFIG="$docker_config" docker pull "$IMAGE_REF" >/dev/null
  DOCKER_CONFIG="$docker_config" docker logout ghcr.io >/dev/null
  python3 "$STAGE/verify.py" image "$IMAGE_REF" "$SOURCE_COMMIT"
fi

python3 "$STAGE/verify.py" assert-protected "$protected_before"
[[ ! -e "$GENERATION" && ! -L "$GENERATION" ]] || {
  echo "renderer release generation already exists" >&2
  exit 1
}

mkdir -m 0711 "$GENERATION"
generation_cleanup_armed=1
cleanup_generation_before_start() {
  status=$?
  trap - EXIT HUP INT TERM
  if (( generation_cleanup_armed )); then
    rm -rf -- "$GENERATION"
    fsync_directories "$RELEASE_ROOT" || status=1
  fi
  cleanup_auth
  if [[ "$status" -ne 0 ]]; then
    sudo -n "$POLICY" quarantine || status=1
    stable_empty_egress || status=1
    python3 "$STAGE/verify.py" assert-protected "$protected_before" || status=1
  fi
  exit "$status"
}
trap cleanup_generation_before_start EXIT
mkdir -m 0711 "$GENERATION/pki"
install -m 0644 "$STAGE/compose.yml" "$GENERATION/compose.yml"
install -m 0644 "$STAGE/inventory.json" "$GENERATION/inventory.json"
install -m 0555 "$STAGE/verify.py" "$GENERATION/verify.py"
install -m 0555 "$STAGE/validate_pki.py" "$GENERATION/validate_pki.py"
install -m 0444 "$STAGE/pki/ca.pem" "$GENERATION/pki/ca.pem"
install -m 0444 "$STAGE/pki/server.pem" "$GENERATION/pki/server.pem"
install -m 0400 "$STAGE/pki/server-key.pem" "$GENERATION/pki/server-key.pem"
install -m 0600 "$STAGE/pki/client.pem" "$GENERATION/pki/client.pem"
install -m 0600 "$STAGE/release.env" "$GENERATION/release.env"
install -m 0444 "$STAGE/pins.env" "$GENERATION/pins.env"

python3 "$GENERATION/validate_pki.py" \
  --ca "$GENERATION/pki/ca.pem" \
  --server "$GENERATION/pki/server.pem" \
  --server-key "$GENERATION/pki/server-key.pem" \
  --client "$GENERATION/pki/client.pem" \
  --output "$GENERATION/pins.revalidated.env"
cmp --silent "$GENERATION/pins.env" "$GENERATION/pins.revalidated.env" || exit 1
rm -f -- "$GENERATION/pins.revalidated.env"

# The service runs as uid/gid 10001. Grant ownership of only its exact private
# key with an immutable, already-pulled image and an isolated one-shot helper.
# Keep a verified descriptor open while deploy still owns the key so its final
# contents and ownership metadata can be fsynced after the helper chowns it;
# reopening the 0400 service-owned path as deploy would be impossible.
exec 10<"$GENERATION/pki/server-key.pem"
[[ "$(stat -Lc '%d:%i' /proc/$$/fd/10)" == \
  "$(stat -Lc '%d:%i' "$GENERATION/pki/server-key.pem")" ]] || exit 1
docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --cap-add CHOWN \
  --security-opt no-new-privileges:true \
  --user 0:0 \
  --entrypoint /bin/sh \
  --mount "type=bind,source=$GENERATION/pki/server-key.pem,target=/server-key.pem" \
  "$IMAGE_REF" \
  -ceu 'chown 10001:10001 /server-key.pem'
[[ "$(stat -c '%u:%g:%a' "$GENERATION/pki/server-key.pem")" == 10001:10001:400 ]] || exit 1
[[ "$(stat -Lc '%d:%i' /proc/$$/fd/10)" == \
  "$(stat -Lc '%d:%i' "$GENERATION/pki/server-key.pem")" ]] || exit 1
python3 - 10 <<'PY'
import os
import sys

os.fsync(int(sys.argv[1]))
PY
exec 10<&-
[[ "$(stat -c '%a' "$GENERATION/pki/ca.pem")" == 444 ]] || exit 1
[[ "$(stat -c '%a' "$GENERATION/pki/server.pem")" == 444 ]] || exit 1
rm -f -- "$GENERATION/pki/client.pem"
[[ ! -e "$GENERATION/pki/client.pem" ]] || exit 1
fsync_files \
  "$GENERATION/compose.yml" \
  "$GENERATION/inventory.json" \
  "$GENERATION/verify.py" \
  "$GENERATION/validate_pki.py" \
  "$GENERATION/pki/ca.pem" \
  "$GENERATION/pki/server.pem" \
  "$GENERATION/release.env" \
  "$GENERATION/pins.env"
fsync_directories "$GENERATION/pki" "$GENERATION" "$RELEASE_ROOT"

python3 "$GENERATION/verify.py" compose \
  "$GENERATION/compose.yml" "$GENERATION/release.env" "$GENERATION/inventory.json"
candidate_container_id=""
rollback_armed=1

attest_routed_network_empty() {
  local endpoint_count
  for _scan in 1 2; do
    if docker network inspect "$EGRESS_NETWORK" >/dev/null 2>&1; then
      endpoint_count="$(docker network inspect --format '{{len .Containers}}' "$EGRESS_NETWORK")" || return 1
      [[ "$endpoint_count" == 0 ]] || return 1
    fi
  done
}

drain_routed_endpoints() {
  local empty_scans=0 endpoint_id network_output
  for _scan in 1 2 3 4 5; do
    docker stop --time 30 "$CONTAINER" >/dev/null 2>&1 || :
    if ! docker network inspect "$EGRESS_NETWORK" >/dev/null 2>&1; then
      empty_scans=$((empty_scans + 1))
      (( empty_scans >= 2 )) && return 0
      continue
    fi
    network_output="$(docker network inspect --format '{{range $id, $_ := .Containers}}{{$id}}{{println}}{{end}}' "$EGRESS_NETWORK")" || return 1
    mapfile -t routed_ids <<<"$network_output"
    if [[ -z "$network_output" ]]; then
      empty_scans=$((empty_scans + 1))
      (( empty_scans >= 2 )) && return 0
      continue
    fi
    empty_scans=0
    for endpoint_id in "${routed_ids[@]}"; do
      [[ "$endpoint_id" =~ ^[0-9a-f]{64}$ ]] || return 1
      docker stop --time 30 "$endpoint_id" >/dev/null 2>&1 || :
      docker network disconnect --force "$EGRESS_NETWORK" "$endpoint_id" >/dev/null 2>&1 || :
    done
  done
  return 1
}

remove_candidate_and_attest_empty() {
  local discovered_id
  if [[ -z "$candidate_container_id" ]]; then
    mapfile -t discovered_candidates < <(
      docker ps --all --quiet \
        --filter "label=com.docker.compose.project=$PROJECT" \
        --filter "label=com.docker.compose.service=$SERVICE" \
        --filter "label=org.jobseek.lightpanda.release=$RELEASE_ID" \
        --filter "label=org.jobseek.lightpanda.image-ref=$IMAGE_REF"
    )
    [[ "${#discovered_candidates[@]}" -le 1 ]] || return 1
    if [[ "${#discovered_candidates[@]}" -eq 1 ]]; then
      candidate_container_id="${discovered_candidates[0]}"
    fi
  fi
  if [[ -n "$candidate_container_id" ]]; then
    [[ "$candidate_container_id" =~ ^[0-9a-f]{64}$ ]] || return 1
    [[ -z "$previous_container_id" || "$candidate_container_id" != "$previous_container_id" ]] || return 1
    [[ "$(docker container inspect --format '{{.Config.Image}}' "$candidate_container_id" 2>/dev/null || :)" == "$IMAGE_REF" ]] || return 1
    docker stop --time 30 "$candidate_container_id" >/dev/null 2>&1 || :
  fi
  drain_routed_endpoints || return 1
  if [[ -n "$candidate_container_id" ]]; then
    if [[ "$CI_FAILURE_MODE" == after-candidate-remove-ambiguous ]]; then
      # Model a transport error reported after Docker already performed the
      # removal. Rollback decides from re-inspected final state, not status.
      docker rm --force "$candidate_container_id" >/dev/null
      ambiguous_remove_status=75
      [[ "$ambiguous_remove_status" -ne 0 ]] || return 1
    else
      docker rm --force "$candidate_container_id" >/dev/null 2>&1 || :
    fi
    ! docker container inspect "$candidate_container_id" >/dev/null 2>&1 || return 1
  fi
  drain_routed_endpoints || return 1
  discovered_id="$(docker container inspect --format '{{.Id}}' "$CONTAINER" 2>/dev/null || :)"
  [[ -z "$discovered_id" ]] || return 1
}

rollback() {
  status=$?
  trap - EXIT HUP INT TERM
  if (( rollback_armed )); then
    rollback_status=0
    remove_candidate_and_attest_empty || rollback_status=1
    active_candidate="$ROOT/.active.$RELEASE_ID"
    if [[ -e "$active_candidate" || -L "$active_candidate" ]]; then
      if [[ -L "$active_candidate" && "$(readlink -f "$active_candidate")" == "$GENERATION" ]]; then
        rm -- "$active_candidate" || rollback_status=1
      else
        rollback_status=1
      fi
    fi
    if (( active_existed )); then
      active_restore="$ROOT/.active.rollback.$RELEASE_ID"
      if [[ -e "$active_restore" || -L "$active_restore" ]]; then
        rollback_status=1
      else
        ln -s "$previous_generation" "$active_restore" || rollback_status=1
        mv -Tf "$active_restore" "$ACTIVE" || rollback_status=1
      fi
      [[ -L "$ACTIVE" && "$(readlink -f "$ACTIVE")" == "$previous_generation" ]] || rollback_status=1
    elif [[ -L "$ACTIVE" && "$(readlink -f "$ACTIVE")" == "$GENERATION" ]]; then
      rm -- "$ACTIVE" || rollback_status=1
    elif [[ -e "$ACTIVE" || -L "$ACTIVE" ]]; then
      rollback_status=1
    fi
    fsync_directories "$ROOT" || rollback_status=1
    attest_routed_network_empty || rollback_status=1
    sudo -n "$POLICY" verify-ready \
      "$EXPECTED_POLICY_SHA256" "$EXPECTED_INVENTORY_SHA256" >/dev/null || rollback_status=1
    python3 "$STAGE/verify.py" assert-protected "$protected_before" || rollback_status=1
    if [[ "$rollback_status" -eq 0 ]]; then
      rm -rf -- "$GENERATION" || rollback_status=1
      fsync_directories "$RELEASE_ROOT" || rollback_status=1
    fi
    (( rollback_status == 0 )) || status=1
  fi
  cleanup_auth
  exit "$status"
}
generation_cleanup_armed=0
trap rollback EXIT

sudo -n "$POLICY" verify-ready \
  "$EXPECTED_POLICY_SHA256" "$EXPECTED_INVENTORY_SHA256" >/dev/null
if [[ "$CI_FAILURE_MODE" == before-compose ]]; then
  echo "CI rollback smoke: forcing failure before Compose" >&2
  exit 94
fi
if [[ "$CI_FAILURE_MODE" == crash-after-compose-create ]]; then
  docker compose --project-name "$PROJECT" \
    --env-file "$GENERATION/release.env" \
    --file "$GENERATION/compose.yml" \
    create --no-deps "$SERVICE"
  candidate_container_id="$(docker container inspect --format '{{.Id}}' "$CONTAINER")"
  [[ "$candidate_container_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
  python3 "$GENERATION/verify.py" owned-created \
    "$GENERATION/release.env" --expected-id "$candidate_container_id" >/dev/null
  echo "CI retry smoke: killing deploy after Compose create and before start" >&2
  kill -KILL "$$"
fi
docker compose --project-name "$PROJECT" \
  --env-file "$GENERATION/release.env" \
  --file "$GENERATION/compose.yml" \
  up --detach --no-deps "$SERVICE"
if [[ "$CI_FAILURE_MODE" == during-compose ]]; then
  echo "CI rollback smoke: forcing ambiguous failure after Compose start" >&2
  exit 95
fi
candidate_container_id="$(docker container inspect --format '{{.Id}}' "$CONTAINER")"
[[ "$candidate_container_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
[[ -z "$previous_container_id" || "$candidate_container_id" != "$previous_container_id" ]] || exit 1

first_verified_id="$(python3 "$GENERATION/verify.py" running "$GENERATION/release.env")"
[[ "$first_verified_id" == "$candidate_container_id" ]] || exit 1
sudo -n "$POLICY" verify-running-ready \
  "$EXPECTED_POLICY_SHA256" "$EXPECTED_INVENTORY_SHA256" >/dev/null
if [[ "$CI_FAILURE_MODE" == crash-after-candidate ]]; then
  echo "CI retry smoke: killing deploy after candidate start" >&2
  kill -KILL "$$"
fi
if [[ "$CI_FAILURE_MODE" == after-candidate || \
  "$CI_FAILURE_MODE" == after-candidate-remove-ambiguous ]]; then
  echo "CI rollback smoke: forcing failure after candidate creation" >&2
  exit 96
fi
started_before_restart="$(docker container inspect --format '{{.State.StartedAt}}' "$candidate_container_id")"
docker restart --time 30 "$candidate_container_id" >/dev/null
[[ "$(docker container inspect --format '{{.Id}}' "$CONTAINER")" == "$candidate_container_id" ]] || exit 1
started_after_restart="$(docker container inspect --format '{{.State.StartedAt}}' "$candidate_container_id")"
[[ "$started_after_restart" != "$started_before_restart" ]] || exit 1
sleep 20
python3 "$GENERATION/verify.py" running "$GENERATION/release.env" \
  --expected-id "$candidate_container_id" >/dev/null
sudo -n "$POLICY" verify-running-ready \
  "$EXPECTED_POLICY_SHA256" "$EXPECTED_INVENTORY_SHA256" >/dev/null
[[ "$(docker container inspect --format '{{.RestartCount}}' "$candidate_container_id")" == 0 ]] || exit 1
memory_current="$(docker exec "$candidate_container_id" cat /sys/fs/cgroup/memory.current)"
[[ "$memory_current" =~ ^[0-9]+$ && "$memory_current" -le 134217728 ]] || {
  echo "idle renderer exceeds the reviewed 128 MiB idle ceiling" >&2
  exit 1
}
docker exec "$candidate_container_id" /bin/sh -ceu '
  for comm in /proc/[0-9]*/comm; do
    IFS= read -r process_name <"$comm"
    [ "$process_name" != lightpanda ] || exit 1
  done
'
python3 "$STAGE/verify.py" assert-protected "$protected_before"

active_candidate="$ROOT/.active.$RELEASE_ID"
ln -s "$GENERATION" "$active_candidate"
mv -Tf "$active_candidate" "$ACTIVE"
[[ "$(readlink -f "$ACTIVE")" == "$GENERATION" ]] || exit 1
fsync_directories "$ROOT" "$GENERATION"
python3 "$STAGE/verify.py" assert-protected "$protected_before"

if [[ "$CI_FAILURE_MODE" == after-active-switch ]]; then
  echo "CI rollback smoke: forcing failure after active pointer switch" >&2
  exit 97
fi

rollback_armed=0
trap cleanup_auth EXIT
echo "dormant renderer release committed: $RELEASE_ID"
