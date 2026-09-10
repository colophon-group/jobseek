#!/usr/bin/env bash
# Native ARM64 start/inspect and rollback smoke with disposable test-only PKI.
set -euo pipefail
set +x
umask 077

trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

IMAGE="${1:-}"
SOURCE_COMMIT="${2:-}"
[[ "$IMAGE" == jobseek-lightpanda-renderer:pr ]] || exit 2
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "${CI:-}" == true && "${GITHUB_ACTIONS:-}" == true ]] || exit 2

ROOT=/home/deploy/.local/share/jobseek-lightpanda
PREVIOUS_RELEASE_ID="sha-${SOURCE_COMMIT}-ci-r1a1"
CANDIDATE_RELEASE_ID="sha-${SOURCE_COMMIT}-ci-r1a2"
FIRST_INSTALL_RELEASE_ID="sha-${SOURCE_COMMIT}-ci-r1a3"
PREVIOUS_RELEASE="$ROOT/releases/$PREVIOUS_RELEASE_ID"
CANDIDATE_RELEASE="$ROOT/releases/$CANDIDATE_RELEASE_ID"
NETWORK=jobseek-lightpanda-renderer
CONTAINER=jobseek-lightpanda-renderer
PROTECTED=(deploy-murmur-1 deploy-cloudflared-1)
work=""
stage=""
protected_ids=()
last_phase=preflight

phase() {
  last_phase="$1"
  printf 'ci-smoke phase: %s\n' "$last_phase"
}

phase "$last_phase"
if sudo test -e "$ROOT" || sudo test -L "$ROOT"; then
  exit 1
fi
for name in "$CONTAINER" "$NETWORK" "${PROTECTED[@]}"; do
  if docker inspect "$name" >/dev/null 2>&1 || docker network inspect "$name" >/dev/null 2>&1; then
    echo "CI Docker identity already exists: $name" >&2
    exit 1
  fi
done

cleanup() {
  exit_status=$?
  trap - EXIT HUP INT TERM
  if [[ "$exit_status" -ne 0 ]]; then
    printf 'ci-smoke failed after phase: %s (status %s)\n' \
      "$last_phase" "$exit_status" >&2 || :
  fi
  if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")"
    service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER")"
    [[ "$project" == jobseek-lightpanda && "$service" == renderer ]] || exit 1
    docker rm --force "$CONTAINER" >/dev/null
  fi
  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    network_id="$(docker network inspect --format '{{.Id}}' "$NETWORK")"
    network_project="$(docker network inspect --format '{{index .Labels "com.docker.compose.project"}}' "$network_id")"
    network_role="$(docker network inspect --format '{{index .Labels "com.docker.compose.network"}}' "$network_id")"
    [[ "$network_project" == jobseek-lightpanda && "$network_role" == renderer ]] || exit 1
    docker network rm "$network_id" >/dev/null
  fi
  for index in "${!protected_ids[@]}"; do
    protected_id="${protected_ids[$index]}"
    protected_name="${PROTECTED[$index]}"
    [[ "$(docker inspect --format '{{.Id}}' "$protected_name" 2>/dev/null || :)" == "$protected_id" ]] || exit 1
    docker rm --force "$protected_id" >/dev/null
  done
  sudo rm -rf -- "$ROOT"
  if [[ -n "$stage" ]]; then
    sudo rm -rf -- "$stage"
  fi
  if [[ -n "$work" ]]; then
    rm -rf -- "$work"
  fi
  exit "$exit_status"
}
trap cleanup EXIT

phase identity-setup
if ! id -u deploy >/dev/null 2>&1; then
  sudo useradd --create-home --shell /bin/bash deploy
fi
docker_group="$(stat -c '%G' /var/run/docker.sock)"
[[ "$docker_group" != UNKNOWN ]] || exit 1
sudo usermod --append --groups "$docker_group" deploy
sudo -u deploy id -nG | tr ' ' '\n' | grep -Fx "$docker_group" >/dev/null
sudo install -d -o deploy -g deploy -m 0700 "$ROOT" "$ROOT/releases"
sudo install -o deploy -g deploy -m 0500 \
  deploy/lightpanda-renderer/lock.sh "$ROOT/lock-race-helper.sh"

# Race the exact production lock helper from an absent lock file. Both
# processes must serialize through one inode and never overlap the critical
# section.
phase lock-race
lock_source="$ROOT/lock-race-helper.sh"
lock_race="$ROOT/lock-race-test"
lock_barrier="$ROOT/lock-race-start"
lock_critical="$ROOT/lock-race-critical"
first_lock_ready="$ROOT/lock-race-ready-1"
second_lock_ready="$ROOT/lock-race-ready-2"
lock_race_worker() {
  local ready_path="$1"
  local worker_label="$2"
  sudo -u deploy bash -c '
    set -euo pipefail
    trap '\''worker_status=$?; printf "ci-smoke lock worker %s failed at line %s (status %s)\n" "$6" "$LINENO" "$worker_status" >&2 || :; exit "$worker_status"'\'' ERR
    source "$1"
    touch "$5"
    while [[ ! -e "$2" ]]; do sleep 0.01; done
    acquire_renderer_lock "$3" 10
    mkdir "$4"
    sleep 0.2
    rmdir "$4"
  ' bash "$lock_source" "$lock_barrier" "$lock_race" "$lock_critical" \
    "$ready_path" "$worker_label"
}
lock_race_worker "$first_lock_ready" first &
first_lock_pid=$!
lock_race_worker "$second_lock_ready" second &
second_lock_pid=$!
for ((attempt = 0; attempt < 1000; attempt++)); do
  if sudo -u deploy test -e "$first_lock_ready" && \
    sudo -u deploy test -e "$second_lock_ready"; then
    break
  fi
  kill -0 "$first_lock_pid" "$second_lock_pid" 2>/dev/null || break
  sleep 0.01
done
if ! sudo -u deploy test -e "$first_lock_ready" || \
  ! sudo -u deploy test -e "$second_lock_ready"; then
  kill "$first_lock_pid" "$second_lock_pid" 2>/dev/null || :
  first_lock_status=0
  second_lock_status=0
  wait "$first_lock_pid" 2>/dev/null || first_lock_status=$?
  wait "$second_lock_pid" 2>/dev/null || second_lock_status=$?
  printf 'ci-smoke lock readiness failed: first=%s second=%s\n' \
    "$first_lock_status" "$second_lock_status" >&2 || :
  exit 1
fi
sudo -u deploy touch "$lock_barrier"
first_lock_status=0
second_lock_status=0
wait "$first_lock_pid" || first_lock_status=$?
wait "$second_lock_pid" || second_lock_status=$?
if [[ "$first_lock_status" -ne 0 || "$second_lock_status" -ne 0 ]]; then
  printf 'ci-smoke lock race failed: first=%s second=%s\n' \
    "$first_lock_status" "$second_lock_status" >&2 || :
  exit 1
fi
sudo -u deploy rm -- \
  "$lock_barrier" "$first_lock_ready" "$second_lock_ready" "$lock_race"

phase pki
work="$(mktemp -d)"
openssl req -new -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
  -pkeyopt ec_param_enc:named_curve -nodes -sha256 -days 1095 \
  -config deploy/lightpanda-renderer/testdata/ca.cnf \
  -keyout "$work/ca-key.pem" -out "$work/ca.pem" >/dev/null 2>&1
for leaf in server client; do
  openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
    -pkeyopt ec_param_enc:named_curve -nodes \
    -subj "/CN=$leaf" -keyout "$work/$leaf-key.pem" -out "$work/$leaf.csr" \
    >/dev/null 2>&1
  openssl x509 -req -in "$work/$leaf.csr" -CA "$work/ca.pem" \
    -CAkey "$work/ca-key.pem" -CAcreateserial -days 180 -sha256 \
    -extfile deploy/lightpanda-renderer/testdata/leaf.cnf \
    -extensions "$leaf" -out "$work/$leaf.pem" \
    >/dev/null 2>&1
done
python3 deploy/lightpanda-renderer/validate_pki.py \
  --ca "$work/ca.pem" --server "$work/server.pem" \
  --server-key "$work/server-key.pem" --client "$work/client.pem" \
  --output "$work/pins.env"

install_release() {
  release="$1"
  release_id="$2"
  sudo install -d -o deploy -g deploy -m 0711 "$release" "$release/pki"
  sudo install -o deploy -g deploy -m 0644 \
    deploy/lightpanda-renderer/compose.yml "$release/compose.yml"
  sudo install -o deploy -g deploy -m 0644 \
    deploy/lightpanda-renderer/inventory.json "$release/inventory.json"
  sudo install -o deploy -g deploy -m 0555 \
    deploy/lightpanda-renderer/verify.py "$release/verify.py"
  sudo install -o deploy -g deploy -m 0555 \
    deploy/lightpanda-renderer/validate_pki.py "$release/validate_pki.py"
  sudo install -m 0444 "$work/ca.pem" "$release/pki/ca.pem"
  sudo install -m 0444 "$work/server.pem" "$release/pki/server.pem"
  sudo install -o 10001 -g 10001 -m 0400 \
    "$work/server-key.pem" "$release/pki/server-key.pem"
  sudo install -o deploy -g deploy -m 0444 "$work/pins.env" "$release/pins.env"
  {
    printf 'RENDERER_IMAGE_REF=%s\n' "$IMAGE"
    printf 'RENDERER_RELEASE_DIR=%s\n' "$release"
    printf 'SOURCE_COMMIT=%s\n' "$SOURCE_COMMIT"
    printf 'RELEASE_ID=%s\n' "$release_id"
    cat "$work/pins.env"
  } >"$work/release.env"
  sudo install -o deploy -g deploy -m 0600 "$work/release.env" "$release/release.env"
}

phase baseline-release
install_release "$PREVIOUS_RELEASE" "$PREVIOUS_RELEASE_ID"
sudo -u deploy python3 "$PREVIOUS_RELEASE/verify.py" compose \
  "$PREVIOUS_RELEASE/compose.yml" "$PREVIOUS_RELEASE/release.env" \
  "$PREVIOUS_RELEASE/inventory.json"

for service in murmur cloudflared; do
  name="deploy-${service}-1"
  protected_id="$(docker run --detach \
    --name "$name" \
    --label com.docker.compose.project=deploy \
    --label "com.docker.compose.service=$service" \
    --restart unless-stopped \
    --entrypoint /bin/sh "$IMAGE" -ceu 'while :; do sleep 60; done')"
  [[ "$protected_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
  protected_ids+=("$protected_id")
  docker stop --time 2 "$protected_id" >/dev/null
done
phase protected-snapshot
python3 deploy/lightpanda-renderer/verify.py snapshot-protected "$work/protected-before.json"

phase baseline-start
sudo -u deploy docker compose --project-name jobseek-lightpanda \
  --env-file "$PREVIOUS_RELEASE/release.env" --file "$PREVIOUS_RELEASE/compose.yml" \
  up --detach --no-deps renderer
previous_container_id="$(sudo -u deploy python3 "$PREVIOUS_RELEASE/verify.py" running "$PREVIOUS_RELEASE/release.env")"
previous_network_id="$(docker network inspect --format '{{.Id}}' "$NETWORK")"
[[ "$previous_container_id" =~ ^[0-9a-f]{64}$ && "$previous_network_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
phase isolation-negative
previous_bridge="br-${previous_network_id:0:12}"
sudo ip address add 172.30.94.1/29 dev "$previous_bridge"
set +e
sudo -u deploy python3 "$PREVIOUS_RELEASE/verify.py" \
  running "$PREVIOUS_RELEASE/release.env" >/dev/null 2>&1
routable_bridge_status=$?
set -e
sudo ip address del 172.30.94.1/29 dev "$previous_bridge"
[[ "$routable_bridge_status" -ne 0 ]] || {
  echo "renderer verifier accepted a routable host bridge address" >&2
  exit 1
}
sudo -u deploy python3 "$PREVIOUS_RELEASE/verify.py" \
  running "$PREVIOUS_RELEASE/release.env" \
  --expected-id "$previous_container_id" >/dev/null
sudo -u deploy ln -s "$PREVIOUS_RELEASE" "$ROOT/active"

stage="$(sudo -u deploy mktemp -d '/tmp/jobseek-lightpanda-renderer.r999999a1.XXXXXX')"
sudo -u deploy install -d -m 0700 "$stage/pki"
for artifact in compose.yml inventory.json verify.py validate_pki.py lock.sh install-host.sh; do
  sudo install -o deploy -g deploy -m 0600 \
    "deploy/lightpanda-renderer/$artifact" "$stage/$artifact"
done
for file in ca.pem server.pem server-key.pem client.pem; do
  sudo install -o deploy -g deploy -m 0600 "$work/$file" "$stage/pki/$file"
done
sudo install -o deploy -g deploy -m 0600 "$work/pins.env" "$stage/pins.env"
{
  printf 'RENDERER_IMAGE_REF=%s\n' "$IMAGE"
  printf 'RENDERER_RELEASE_DIR=%s\n' "$CANDIDATE_RELEASE"
  printf 'SOURCE_COMMIT=%s\n' "$SOURCE_COMMIT"
  printf 'RELEASE_ID=%s\n' "$CANDIDATE_RELEASE_ID"
  cat "$work/pins.env"
} >"$work/candidate-release.env"
sudo install -o deploy -g deploy -m 0600 \
  "$work/candidate-release.env" "$stage/release.env"

phase replacement-rollback
set +e
sudo -u deploy env \
  CI=true \
  GITHUB_ACTIONS=true \
  JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE=after-active-switch \
  bash "$stage/install-host.sh" \
    "$stage" "$SOURCE_COMMIT" "$IMAGE" "$CANDIDATE_RELEASE_ID"
install_status=$?
set -e
[[ "$install_status" -eq 97 ]] || {
  echo "CI rollback smoke returned $install_status instead of 97" >&2
  exit 1
}

if sudo -u deploy test -e "$CANDIDATE_RELEASE" || sudo -u deploy test -L "$CANDIDATE_RELEASE"; then
  exit 1
fi
[[ "$(sudo -u deploy readlink -f "$ROOT/active")" == "$PREVIOUS_RELEASE" ]] || exit 1
restored_container_id="$(sudo -u deploy python3 "$PREVIOUS_RELEASE/verify.py" running "$PREVIOUS_RELEASE/release.env")"
[[ "$restored_container_id" =~ ^[0-9a-f]{64}$ ]] || exit 1
[[ "$(docker network inspect --format '{{.Id}}' "$NETWORK")" == "$previous_network_id" ]] || exit 1
mapfile -t leaked_candidates < <(
  docker ps --all --quiet \
    --filter label=com.docker.compose.project=jobseek-lightpanda \
    --filter label=com.docker.compose.service=renderer \
    --filter "label=org.jobseek.lightpanda.release=$CANDIDATE_RELEASE_ID"
)
[[ "${#leaked_candidates[@]}" -eq 0 ]] || exit 1
python3 deploy/lightpanda-renderer/verify.py assert-protected "$work/protected-before.json"

# Exercise the first-install rollback too: no active pointer or pre-existing
# network may survive after a candidate is created and deliberately rejected.
phase first-install-rollback
docker rm --force "$restored_container_id" >/dev/null
sudo -u deploy rm -- "$ROOT/active"
python3 deploy/lightpanda-renderer/verify.py cleanup-network \
  "$previous_network_id" deploy/lightpanda-renderer/inventory.json
docker network rm "$previous_network_id" >/dev/null
FIRST_INSTALL_RELEASE="$ROOT/releases/$FIRST_INSTALL_RELEASE_ID"
{
  printf 'RENDERER_IMAGE_REF=%s\n' "$IMAGE"
  printf 'RENDERER_RELEASE_DIR=%s\n' "$FIRST_INSTALL_RELEASE"
  printf 'SOURCE_COMMIT=%s\n' "$SOURCE_COMMIT"
  printf 'RELEASE_ID=%s\n' "$FIRST_INSTALL_RELEASE_ID"
  cat "$work/pins.env"
} >"$work/first-install-release.env"
sudo install -o deploy -g deploy -m 0600 \
  "$work/first-install-release.env" "$stage/release.env"
set +e
sudo -u deploy env \
  CI=true \
  GITHUB_ACTIONS=true \
  JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE=after-candidate \
  bash "$stage/install-host.sh" \
    "$stage" "$SOURCE_COMMIT" "$IMAGE" "$FIRST_INSTALL_RELEASE_ID"
first_install_status=$?
set -e
[[ "$first_install_status" -eq 96 ]] || exit 1
if sudo -u deploy test -e "$FIRST_INSTALL_RELEASE" || sudo -u deploy test -L "$FIRST_INSTALL_RELEASE"; then
  exit 1
fi
if sudo -u deploy test -e "$ROOT/active" || sudo -u deploy test -L "$ROOT/active"; then
  exit 1
fi
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  exit 1
fi
if docker network inspect "$NETWORK" >/dev/null 2>&1; then
  exit 1
fi
python3 deploy/lightpanda-renderer/verify.py assert-protected "$work/protected-before.json"
phase final-attestation
test "$(docker image inspect --format '{{.Architecture}}' "$IMAGE")" = arm64
test "$(docker image inspect --format '{{index .Config.Labels "org.jobseek.lightpanda.source-commit"}}' "$IMAGE")" = "$SOURCE_COMMIT"
phase complete
