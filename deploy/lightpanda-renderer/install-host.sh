#!/usr/bin/env bash
# Run a single-service, exact-ID rollback transaction as the Murmur deploy user.
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
CI_FAILURE_MODE="${JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE:-}"
ci_rollback_smoke=0

trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$STAGE" =~ ^/tmp/jobseek-lightpanda-renderer\.r[0-9]+a[0-9]+\.[A-Za-z0-9]+$ ]] || exit 2
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 2
if [[ -n "$CI_FAILURE_MODE" ]]; then
  [[ "$CI_FAILURE_MODE" == after-candidate || "$CI_FAILURE_MODE" == after-active-switch ]] || exit 2
  [[ "${CI:-}" == true && "${GITHUB_ACTIONS:-}" == true ]] || exit 2
  [[ "$IMAGE_REF" == jobseek-lightpanda-renderer:pr ]] || exit 2
  [[ "$RELEASE_ID" =~ ^sha-${SOURCE_COMMIT}-ci-r[0-9]+a[0-9]+$ ]] || exit 2
  ci_rollback_smoke=1
else
  [[ "$IMAGE_REF" =~ ^ghcr\.io/colophon-group/jobseek-lightpanda-renderer@sha256:[0-9a-f]{64}$ ]] || exit 2
  [[ "$RELEASE_ID" =~ ^sha-${SOURCE_COMMIT}-r[0-9]+a[0-9]+$ ]] || exit 2
fi
[[ "$(id -u)" -ne 0 && "$(id -un)" == deploy ]] || exit 2
for command in base64 docker flock ip openssl python3 readlink sha256sum ss; do
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

for relative in compose.yml inventory.json verify.py validate_pki.py pins.env release.env pki/ca.pem pki/server.pem pki/server-key.pem pki/client.pem; do
  [[ -f "$STAGE/$relative" && ! -L "$STAGE/$relative" ]] || exit 2
  [[ "$(stat -c '%s' "$STAGE/$relative")" -le 131072 ]] || exit 2
done

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
[[ -f "$ROOT/renderer.lock" && ! -L "$ROOT/renderer.lock" ]] || {
  [[ ! -e "$ROOT/renderer.lock" && ! -L "$ROOT/renderer.lock" ]] || exit 1
  install -m 0600 /dev/null "$ROOT/renderer.lock"
}
[[ "$(stat -c '%U:%G:%a' "$ROOT/renderer.lock")" == deploy:deploy:600 ]] || exit 1
exec 9>"$ROOT/renderer.lock"
flock -w 900 9 || {
  echo "renderer deployment lock is busy" >&2
  exit 1
}
python3 "$STAGE/verify.py" snapshot-protected "$protected_before"
renderer_exists=0
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  renderer_exists=1
fi
inventory_args=(inventory "$STAGE/inventory.json")
if (( renderer_exists )); then
  inventory_args+=(--renderer-exists)
fi
if (( ci_rollback_smoke )); then
  python3 "$STAGE/verify.py" inventory-file "$STAGE/inventory.json"
else
  python3 "$STAGE/verify.py" "${inventory_args[@]}"
fi
[[ -z "$(ss -H -ltn 'sport = :9443')" ]] || {
  echo "host port 9443 is unexpectedly published" >&2
  exit 1
}

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
  [[ -n "$previous_generation" ]] || exit 1
  previous_container_id="$(docker inspect --format '{{.Id}}' "$CONTAINER")"
  [[ "$previous_container_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
  python3 "$previous_generation/verify.py" running \
    "$previous_generation/release.env" --expected-id "$previous_container_id" >/dev/null
elif [[ -n "$previous_generation" ]]; then
  exit 1
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
trap cleanup_auth EXIT
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
  fi
  cleanup_auth
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
install -m 0600 "$STAGE/pki/server-key.pem" "$GENERATION/pki/server-key.pem"
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
  -ceu 'chmod 0400 /server-key.pem && chown 10001:10001 /server-key.pem'
[[ "$(stat -c '%u:%g:%a' "$GENERATION/pki/server-key.pem")" == 10001:10001:400 ]] || exit 1
[[ "$(stat -c '%a' "$GENERATION/pki/ca.pem")" == 444 ]] || exit 1
[[ "$(stat -c '%a' "$GENERATION/pki/server.pem")" == 444 ]] || exit 1
rm -f -- "$GENERATION/pki/client.pem"
[[ ! -e "$GENERATION/pki/client.pem" ]] || exit 1

python3 "$GENERATION/verify.py" compose \
  "$GENERATION/compose.yml" "$GENERATION/release.env" "$GENERATION/inventory.json"
network_before_id=""
if docker network inspect "$NETWORK" >/dev/null 2>&1; then
  network_before_id="$(docker network inspect --format '{{.Id}}' "$NETWORK")"
  [[ "$network_before_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
fi

candidate_container_id=""
candidate_network_id=""
rollback_armed=1
rollback() {
  status=$?
  trap - EXIT HUP INT TERM
  if (( rollback_armed )); then
    active_candidate="$ROOT/.active.$RELEASE_ID"
    if [[ -e "$active_candidate" || -L "$active_candidate" ]]; then
      [[ -L "$active_candidate" && "$(readlink -f "$active_candidate")" == "$GENERATION" ]] || exit 1
      rm -- "$active_candidate"
    fi
    if (( active_existed )); then
      active_restore="$ROOT/.active.rollback.$RELEASE_ID"
      ln -s "$previous_generation" "$active_restore"
      mv -Tf "$active_restore" "$ACTIVE"
      [[ "$(readlink -f "$ACTIVE")" == "$previous_generation" ]] || exit 1
    elif [[ -L "$ACTIVE" && "$(readlink -f "$ACTIVE")" == "$GENERATION" ]]; then
      rm -- "$ACTIVE"
    elif [[ -e "$ACTIVE" || -L "$ACTIVE" ]]; then
      exit 1
    fi
    fsync_directories "$ROOT"
    if [[ -z "$candidate_container_id" ]]; then
      mapfile -t discovered_candidates < <(
        docker ps --all --quiet \
          --filter "label=com.docker.compose.project=$PROJECT" \
          --filter "label=com.docker.compose.service=$SERVICE" \
          --filter "label=org.jobseek.lightpanda.release=$RELEASE_ID" \
          --filter "label=org.jobseek.lightpanda.image-ref=$IMAGE_REF"
      )
      [[ "${#discovered_candidates[@]}" -le 1 ]] || exit 1
      if [[ "${#discovered_candidates[@]}" -eq 1 ]]; then
        candidate_container_id="${discovered_candidates[0]}"
        [[ "$candidate_container_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
        [[ -z "$previous_container_id" || "$candidate_container_id" != "$previous_container_id" ]] || exit 1
        [[ "$(docker inspect --format '{{.Config.Image}}' "$candidate_container_id")" == "$IMAGE_REF" ]] || exit 1
      fi
    fi
    if [[ -n "$candidate_container_id" ]] && \
       [[ "$(docker inspect --format '{{.Id}}' "$CONTAINER" 2>/dev/null || :)" == "$candidate_container_id" ]]; then
      docker rm --force "$candidate_container_id" >/dev/null
    fi
    if [[ -n "$previous_generation" ]]; then
      docker compose --project-name "$PROJECT" \
        --env-file "$previous_generation/release.env" \
        --file "$previous_generation/compose.yml" \
        up --detach --no-deps "$SERVICE"
      python3 "$previous_generation/verify.py" running "$previous_generation/release.env" >/dev/null
    elif [[ -z "$network_before_id" ]]; then
      if [[ -z "$candidate_network_id" ]] && docker network inspect "$NETWORK" >/dev/null 2>&1; then
        candidate_network_id="$(docker network inspect --format '{{.Id}}' "$NETWORK")"
      fi
      if [[ -n "$candidate_network_id" ]]; then
        [[ "$candidate_network_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
        python3 "$STAGE/verify.py" cleanup-network \
          "$candidate_network_id" "$STAGE/inventory.json"
        docker network rm "$candidate_network_id" >/dev/null
      fi
    fi
    python3 "$STAGE/verify.py" assert-protected "$protected_before"
    rm -rf -- "$GENERATION"
  fi
  cleanup_auth
  exit "$status"
}
generation_cleanup_armed=0
trap rollback EXIT

docker compose --project-name "$PROJECT" \
  --env-file "$GENERATION/release.env" \
  --file "$GENERATION/compose.yml" \
  up --detach --no-deps "$SERVICE"
candidate_container_id="$(docker inspect --format '{{.Id}}' "$CONTAINER")"
candidate_network_id="$(docker network inspect --format '{{.Id}}' "$NETWORK")"
[[ "$candidate_container_id" =~ ^[0-9a-f]{64}$ && "$candidate_network_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
[[ -z "$previous_container_id" || "$candidate_container_id" != "$previous_container_id" ]] || exit 1

first_verified_id="$(python3 "$GENERATION/verify.py" running "$GENERATION/release.env")"
[[ "$first_verified_id" == "$candidate_container_id" ]] || exit 1
if [[ "$CI_FAILURE_MODE" == after-candidate ]]; then
  echo "CI rollback smoke: forcing failure after candidate creation" >&2
  exit 96
fi
started_before_restart="$(docker inspect --format '{{.State.StartedAt}}' "$candidate_container_id")"
docker restart --time 30 "$candidate_container_id" >/dev/null
[[ "$(docker inspect --format '{{.Id}}' "$CONTAINER")" == "$candidate_container_id" ]] || exit 1
started_after_restart="$(docker inspect --format '{{.State.StartedAt}}' "$candidate_container_id")"
[[ "$started_after_restart" != "$started_before_restart" ]] || exit 1
sleep 20
python3 "$GENERATION/verify.py" running "$GENERATION/release.env" \
  --expected-id "$candidate_container_id" >/dev/null
[[ "$(docker inspect --format '{{.RestartCount}}' "$candidate_container_id")" == 0 ]] || exit 1
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
[[ -z "$(ss -H -ltn 'sport = :9443')" ]] || exit 1
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
