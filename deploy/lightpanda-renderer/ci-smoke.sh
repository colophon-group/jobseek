#!/usr/bin/env bash
# Production-shaped legacy bootstrap, cold rollback, and packet-path smoke.
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
LEGACY_ID="sha-${SOURCE_COMMIT}-ci-r1a1"
CANDIDATE_ID="sha-${SOURCE_COMMIT}-ci-r1a2"
LEGACY_RELEASE="$ROOT/releases/$LEGACY_ID"
CANDIDATE_RELEASE="$ROOT/releases/$CANDIDATE_ID"
NETWORK=jobseek-lightpanda-renderer
EGRESS_NETWORK=jobseek-lightpanda-egress
CONTAINER=jobseek-lightpanda-renderer
PROTECTED=(deploy-murmur-1 deploy-cloudflared-1)
CRAWLER_NAMESPACE=jobseek-lightpanda-crawler-peer
work=""
stage=""
root_stage=""
protected_ids=()
topology_created=0
host_bootstrapped=0
last_phase=preflight

phase() {
  last_phase="$1"
  printf 'ci-smoke phase: %s\n' "$last_phase"
}

delete_test_policy() {
  local binary parent target chain
  while read -r binary parent target; do
    while sudo "$binary" --wait 30 -C "$parent" -j "$target" >/dev/null 2>&1; do
      sudo "$binary" --wait 30 -D "$parent" -j "$target"
    done
  done <<'EOF'
iptables DOCKER-USER JSLP4-FWD
iptables INPUT JSLP4-HOST
iptables OUTPUT JSLP4-OUTPUT
ip6tables FORWARD JSLP6-FWD
ip6tables INPUT JSLP6-HOST
ip6tables OUTPUT JSLP6-OUTPUT
EOF
  for binary in iptables ip6tables; do
    if [[ "$binary" == iptables ]]; then
      chains=(JSLP4-FWD JSLP4-EGRESS JSLP4-INGRESS JSLP4-HOST JSLP4-OUTPUT)
    else
      chains=(JSLP6-FWD JSLP6-HOST JSLP6-OUTPUT)
    fi
    for chain in "${chains[@]}"; do
      sudo "$binary" --wait 30 -F "$chain" >/dev/null 2>&1 || :
      sudo "$binary" --wait 30 -X "$chain" >/dev/null 2>&1 || :
    done
  done
}

cleanup() {
  exit_status=$?
  trap - EXIT HUP INT TERM
  if [[ "$exit_status" -ne 0 ]]; then
    printf 'ci-smoke failed after phase: %s (status %s)\n' \
      "$last_phase" "$exit_status" >&2 || :
  fi
  docker rm --force "$CONTAINER" >/dev/null 2>&1 || :
  if (( host_bootstrapped )); then
    sudo systemctl disable --now jobseek-lightpanda-network.service >/dev/null 2>&1 || :
    delete_test_policy || exit 1
  fi
  for network_name in "$EGRESS_NETWORK" "$NETWORK"; do
    docker network rm "$network_name" >/dev/null 2>&1 || :
  done
  for index in "${!protected_ids[@]}"; do
    protected_id="${protected_ids[$index]}"
    protected_name="${PROTECTED[$index]}"
    [[ "$(docker inspect --format '{{.Id}}' "$protected_name" 2>/dev/null || :)" == "$protected_id" ]] || exit 1
    docker rm --force "$protected_id" >/dev/null
  done
  sudo rm -rf -- "$ROOT" /var/lib/jobseek-lightpanda-network
  sudo rm -f -- \
    /usr/local/libexec/jobseek-lightpanda-network-policy \
    /etc/jobseek-lightpanda-network/inventory.json \
    /etc/systemd/system/jobseek-lightpanda-network.service \
    /etc/sudoers.d/jobseek-lightpanda-network \
    /run/lock/jobseek-lightpanda-network.lock
  sudo rmdir /etc/jobseek-lightpanda-network 2>/dev/null || :
  sudo systemctl daemon-reload >/dev/null 2>&1 || :
  [[ -z "$stage" ]] || sudo rm -rf -- "$stage"
  [[ -z "$root_stage" ]] || sudo rm -rf -- "$root_stage"
  if (( topology_created )); then
    sudo ip netns delete "$CRAWLER_NAMESPACE" 2>/dev/null || :
    sudo ip link delete enp7s0 2>/dev/null || :
  fi
  [[ -z "$work" ]] || rm -rf -- "$work"
  exit "$exit_status"
}
trap cleanup EXIT

phase preflight
if sudo test -e "$ROOT" || sudo test -L "$ROOT"; then
  exit 1
fi
for name in "$CONTAINER" "$NETWORK" "$EGRESS_NETWORK" "${PROTECTED[@]}"; do
  if docker inspect "$name" >/dev/null 2>&1 || docker network inspect "$name" >/dev/null 2>&1; then
    echo "CI Docker identity already exists: $name" >&2
    exit 1
  fi
done

phase identity-and-topology
if ! id -u deploy >/dev/null 2>&1; then
  sudo useradd --create-home --shell /bin/bash deploy
fi
docker_group="$(stat -c '%G' /var/run/docker.sock)"
[[ "$docker_group" != UNKNOWN ]] || exit 1
sudo usermod --append --groups "$docker_group" deploy
[[ "$(sudo -u deploy id -gn)" == deploy ]] || exit 1
sudo install -d -o deploy -g deploy -m 0700 "$ROOT" "$ROOT/releases"
sudo ip netns add "$CRAWLER_NAMESPACE"
sudo ip link add enp7s0 type veth peer name eth0 netns "$CRAWLER_NAMESPACE"
topology_created=1
sudo ip address add 10.0.0.5/32 dev enp7s0
sudo ip link set enp7s0 up
sudo ip -n "$CRAWLER_NAMESPACE" address add 10.0.0.4/32 dev eth0
sudo ip -n "$CRAWLER_NAMESPACE" address add 10.0.0.6/32 dev eth0
sudo ip -n "$CRAWLER_NAMESPACE" link set lo up
sudo ip -n "$CRAWLER_NAMESPACE" link set eth0 up
sudo ip -n "$CRAWLER_NAMESPACE" route add 10.0.0.5/32 dev eth0
sudo ip route add 10.0.0.4/32 dev enp7s0
sudo ip route add 10.0.0.6/32 dev enp7s0

phase pki
work="$(mktemp -d)"
openssl req -new -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
  -pkeyopt ec_param_enc:named_curve -nodes -sha256 -days 1095 \
  -config deploy/lightpanda-renderer/testdata/ca.cnf \
  -keyout "$work/ca-key.pem" -out "$work/ca.pem" >/dev/null 2>&1
for leaf in server client; do
  openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
    -pkeyopt ec_param_enc:named_curve -nodes -subj "/CN=$leaf" \
    -keyout "$work/$leaf-key.pem" -out "$work/$leaf.csr" >/dev/null 2>&1
  openssl x509 -req -in "$work/$leaf.csr" -CA "$work/ca.pem" \
    -CAkey "$work/ca-key.pem" -CAcreateserial -days 180 -sha256 \
    -extfile deploy/lightpanda-renderer/testdata/leaf.cnf \
    -extensions "$leaf" -out "$work/$leaf.pem" >/dev/null 2>&1
done
python3 deploy/lightpanda-renderer/validate_pki.py \
  --ca "$work/ca.pem" --server "$work/server.pem" \
  --server-key "$work/server-key.pem" --client "$work/client.pem" \
  --output "$work/pins.env"

install_release() {
  local release="$1" release_id="$2" compose_source="$3" verifier_source="$4"
  sudo install -d -o deploy -g deploy -m 0711 "$release" "$release/pki"
  sudo install -o deploy -g deploy -m 0644 "$compose_source" "$release/compose.yml"
  sudo install -o deploy -g deploy -m 0644 \
    deploy/lightpanda-renderer/inventory.json "$release/inventory.json"
  sudo install -o deploy -g deploy -m 0555 "$verifier_source" "$release/verify.py"
  sudo install -o deploy -g deploy -m 0555 \
    deploy/lightpanda-renderer/validate_pki.py "$release/validate_pki.py"
  sudo install -m 0444 "$work/ca.pem" "$release/pki/ca.pem"
  sudo install -m 0444 "$work/server.pem" "$release/pki/server.pem"
  sudo install -o 10001 -g 10001 -m 0400 \
    "$work/server-key.pem" "$release/pki/server-key.pem"
  {
    printf 'RENDERER_IMAGE_REF=%s\n' "$IMAGE"
    printf 'RENDERER_RELEASE_DIR=%s\n' "$release"
    printf 'SOURCE_COMMIT=%s\n' "$SOURCE_COMMIT"
    printf 'RELEASE_ID=%s\n' "$release_id"
    cat "$work/pins.env"
  } >"$work/release.env"
  sudo install -o deploy -g deploy -m 0600 "$work/release.env" "$release/release.env"
}

phase protected-snapshot
for service in murmur cloudflared; do
  name="deploy-${service}-1"
  protected_id="$(docker run --detach --name "$name" \
    --label com.docker.compose.project=deploy \
    --label "com.docker.compose.service=$service" \
    --restart no --entrypoint /bin/sh "$IMAGE" -ceu 'while :; do sleep 60; done')"
  protected_ids+=("$protected_id")
  docker stop --time 2 "$protected_id" >/dev/null
done
python3 deploy/lightpanda-renderer/verify.py snapshot-protected "$work/protected-before.json"

phase exact-legacy-start
install_release "$LEGACY_RELEASE" "$LEGACY_ID" \
  deploy/lightpanda-renderer/testdata/legacy-compose.yml \
  deploy/lightpanda-renderer/testdata/legacy-verify.py
sudo -u deploy docker compose --project-name jobseek-lightpanda \
  --env-file "$LEGACY_RELEASE/release.env" --file "$LEGACY_RELEASE/compose.yml" \
  up --detach --no-deps renderer
legacy_container_id="$(docker inspect --format '{{.Id}}' "$CONTAINER")"
sudo -u deploy python3 "$LEGACY_RELEASE/verify.py" running \
  "$LEGACY_RELEASE/release.env" --expected-id "$legacy_container_id" >/dev/null
sudo -u deploy ln -s "$LEGACY_RELEASE" "$ROOT/active"
legacy_network_id="$(docker network inspect --format '{{.Id}}' "$NETWORK")"
legacy_network_options="$(docker network inspect --format '{{json .Options}}' "$NETWORK")"
[[ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' deploy-murmur-1)" == no ]]
[[ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' deploy-cloudflared-1)" == no ]]

phase one-time-host-bootstrap
root_stage="$(sudo mktemp -d '/tmp/jobseek-lightpanda-bootstrap.r999999a1.XXXXXX')"
for artifact in bootstrap-host.sh network-policy.py inventory.json verify.py \
  jobseek-lightpanda-network.service jobseek-lightpanda-network.sudoers; do
  sudo install -o root -g root -m 0700 \
    "deploy/lightpanda-renderer/$artifact" "$root_stage/$artifact"
done
sudo bash "$root_stage/bootstrap-host.sh" "$root_stage" "$SOURCE_COMMIT"
host_bootstrapped=1
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  exit 1
fi
[[ "$(sudo -u deploy readlink -f "$ROOT/active")" == "$LEGACY_RELEASE" ]]
sudo -u deploy test -d "$LEGACY_RELEASE"
[[ "$(docker network inspect --format '{{.Id}}' "$NETWORK")" == "$legacy_network_id" ]]
[[ "$(docker network inspect --format '{{json .Options}}' "$NETWORK")" == "$legacy_network_options" ]]
egress_network_id="$(docker network inspect --format '{{.Id}}' "$EGRESS_NETWORK")"
[[ "$(docker network inspect --format '{{len .Containers}}' "$EGRESS_NETWORK")" == 0 ]]
sudo /usr/local/libexec/jobseek-lightpanda-network-policy verify-ready >/dev/null
python3 deploy/lightpanda-renderer/verify.py assert-protected "$work/protected-before.json"

phase partial-bootstrap-replay
sudo iptables --wait 30 -D DOCKER-USER -j JSLP4-FWD
sudo bash "$root_stage/bootstrap-host.sh" "$root_stage" "$SOURCE_COMMIT"
[[ "$(docker network inspect --format '{{.Id}}' "$NETWORK")" == "$legacy_network_id" ]]
[[ "$(docker network inspect --format '{{.Id}}' "$EGRESS_NETWORK")" == "$egress_network_id" ]]
[[ "$(sudo -u deploy readlink -f "$ROOT/active")" == "$LEGACY_RELEASE" ]]
sudo /usr/local/libexec/jobseek-lightpanda-network-policy verify-ready >/dev/null
python3 deploy/lightpanda-renderer/verify.py assert-protected "$work/protected-before.json"

phase candidate-stage
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
  printf 'RELEASE_ID=%s\n' "$CANDIDATE_ID"
  cat "$work/pins.env"
} >"$work/candidate-release.env"
sudo install -o deploy -g deploy -m 0600 \
  "$work/candidate-release.env" "$stage/release.env"

run_expected_failure() {
  local mode="$1" expected="$2" status=0
  set +e
  sudo -u deploy env CI=true GITHUB_ACTIONS=true \
    "JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE=$mode" \
    bash "$stage/install-host.sh" \
      "$stage" "$SOURCE_COMMIT" "$IMAGE" "$CANDIDATE_ID"
  status=$?
  set -e
  [[ "$status" -eq "$expected" ]] || {
    echo "CI rollback smoke returned $status instead of $expected" >&2
    exit 1
  }
  sudo -u deploy test ! -e "$CANDIDATE_RELEASE"
  [[ "$(sudo -u deploy readlink -f "$ROOT/active")" == "$LEGACY_RELEASE" ]]
  if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    exit 1
  fi
  [[ "$(docker network inspect --format '{{len .Containers}}' "$EGRESS_NETWORK")" == 0 ]]
  sudo /usr/local/libexec/jobseek-lightpanda-network-policy verify-ready >/dev/null
  python3 deploy/lightpanda-renderer/verify.py assert-protected "$work/protected-before.json"
}

phase ambiguous-remove-rollback
run_expected_failure after-candidate-remove-ambiguous 96

phase active-switch-cold-rollback
run_expected_failure after-active-switch 97

phase replacement-success
sudo -u deploy env CI=true GITHUB_ACTIONS=true \
  JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE=success \
  bash "$stage/install-host.sh" \
    "$stage" "$SOURCE_COMMIT" "$IMAGE" "$CANDIDATE_ID"
[[ "$(sudo -u deploy readlink -f "$ROOT/active")" == "$CANDIDATE_RELEASE" ]]
candidate_container_id="$(sudo -u deploy python3 "$CANDIDATE_RELEASE/verify.py" running \
  "$CANDIDATE_RELEASE/release.env")"
[[ "$candidate_container_id" =~ ^[0-9a-f]{64}$ ]]
[[ "$(docker network inspect --format '{{.Id}}' "$NETWORK")" == "$legacy_network_id" ]]
[[ "$(docker network inspect --format '{{.Id}}' "$EGRESS_NETWORK")" == "$egress_network_id" ]]
sudo /usr/local/libexec/jobseek-lightpanda-network-policy verify-running-ready >/dev/null

phase firewall-paths
sudo iptables --wait 30 -C JSLP4-EGRESS -d 185.12.64.1/32 -p udp -m udp --dport 53 -j ACCEPT
sudo iptables --wait 30 -C JSLP4-EGRESS -d 185.12.64.2/32 -p tcp -m tcp --dport 53 -j ACCEPT
sudo iptables --wait 30 -C JSLP4-EGRESS -p tcp -m tcp --dport 443 -j ACCEPT
sudo iptables --wait 30 -C JSLP4-EGRESS -d 10.0.0.0/8 -j DROP
[[ "$(sudo iptables --wait 30 -S JSLP4-EGRESS | tail -n 1)" == "-A JSLP4-EGRESS -j DROP" ]]
[[ "$(sudo ip6tables --wait 30 -S JSLP6-FWD | tail -n 1)" == "-A JSLP6-FWD -j DROP" ]]
[[ "$(sudo iptables --wait 30 -S DOCKER-USER | sed -n '1p')" == "-N DOCKER-USER" ]]
[[ "$(sudo iptables --wait 30 -S DOCKER-USER | sed -n '2p')" == "-A DOCKER-USER -j JSLP4-FWD" ]]

phase private-mtls-ingress
sudo ip netns exec "$CRAWLER_NAMESPACE" python3 - "$work" <<'PY'
import socket
import ssl
import sys

credential_root = sys.argv[1]
blocked = socket.socket()
blocked.settimeout(2)
blocked.bind(("10.0.0.6", 0))
try:
    blocked.connect(("10.0.0.5", 9443))
except OSError:
    pass
else:
    raise SystemExit("non-crawler peer reached the private renderer publication")
finally:
    blocked.close()

context = ssl.create_default_context(cafile=f"{credential_root}/ca.pem")
context.minimum_version = ssl.TLSVersion.TLSv1_3
context.maximum_version = ssl.TLSVersion.TLSv1_3
context.load_cert_chain(
    certfile=f"{credential_root}/client.pem",
    keyfile=f"{credential_root}/client-key.pem",
)
context.set_alpn_protocols(["jobseek-lightpanda-b0/1"])
raw = socket.socket()
raw.settimeout(5)
raw.bind(("10.0.0.4", 0))
raw.connect(("10.0.0.5", 9443))
with context.wrap_socket(raw, server_hostname="10.0.0.5") as connection:
    first = connection.recv(1)
    if not first:
        raise SystemExit("renderer did not return its hello frame")
PY

phase final-attestation
python3 deploy/lightpanda-renderer/verify.py assert-protected "$work/protected-before.json"
test "$(docker image inspect --format '{{.Architecture}}' "$IMAGE")" = arm64
test "$(docker image inspect --format '{{index .Config.Labels "org.jobseek.lightpanda.source-commit"}}' "$IMAGE")" = "$SOURCE_COMMIT"
phase complete
