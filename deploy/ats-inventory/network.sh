#!/usr/bin/env bash
# Enforce and prove the data-only ATS runner's dedicated egress boundary.
set -euo pipefail
umask 077

[[ "$(id -u)" -eq 0 ]] || { echo "ERROR: network helper requires root" >&2; exit 1; }
ACTION="${1:-ensure}"
NETWORK=jobseek-ats-inventory-egress
BRIDGE=br-jobseek-ats
EGRESS_CHAIN=JOBSEEK-ATS-EGRESS
INPUT_CHAIN=JOBSEEK-ATS-INPUT
STATE_ROOT=/var/lib/jobseek-ats-inventory
DEPLOY_SUCCESS=/home/deploy/.crawler-deploy-success.env
ENV_FILE=/home/deploy/.env
CRAWLER_LOCK=/run/lock/jobseek-crawler-mutation.lock
PROBE=/usr/local/libexec/jobseek-ats-inventory-network-probe
PROBE_CONTAINER=jobseek-ats-inventory-network-probe
ATTESTATION=/run/lock/jobseek-ats-inventory-network.verified
ENDPOINTS=""
ATTESTATION_TEMP=""
IPT=(iptables --wait 30)

cleanup() {
  docker rm -f "$PROBE_CONTAINER" >/dev/null 2>&1 || true
  [[ -z "$ENDPOINTS" ]] || rm -f -- "$ENDPOINTS"
  [[ -z "$ATTESTATION_TEMP" ]] || rm -f -- "$ATTESTATION_TEMP"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

for command in date docker flock iptables mktemp python3 runuser stat; do
  command -v "$command" >/dev/null || {
    echo "ERROR: required network command ${command} is unavailable" >&2
    exit 1
  }
done

open_shared_crawler_lock() {
  if [[ ! -e "$CRAWLER_LOCK" ]]; then
    runuser -u deploy -- python3 -c \
      'import os,sys; fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); os.close(fd)' \
      "$CRAWLER_LOCK" 2>/dev/null || true
  fi
  [[ -f "$CRAWLER_LOCK" && ! -L "$CRAWLER_LOCK" ]] || return 1
  chown deploy:deploy "$CRAWLER_LOCK"
  chmod 0600 "$CRAWLER_LOCK"
  [[ "$(stat -c '%U:%G:%a' "$CRAWLER_LOCK")" == deploy:deploy:600 ]] || return 1
  exec 8<"$CRAWLER_LOCK"
}

remove_hook() {
  local parent="$1" interface="$2" target="$3"
  while "${IPT[@]}" -C "$parent" -i "$interface" -j "$target" 2>/dev/null; do
    "${IPT[@]}" -D "$parent" -i "$interface" -j "$target"
  done
}

remove_firewall() {
  remove_hook DOCKER-USER "$BRIDGE" "$EGRESS_CHAIN"
  remove_hook INPUT "$BRIDGE" "$INPUT_CHAIN"
  if "${IPT[@]}" -n -L "$EGRESS_CHAIN" >/dev/null 2>&1; then
    "${IPT[@]}" -F "$EGRESS_CHAIN"
    "${IPT[@]}" -X "$EGRESS_CHAIN"
  fi
  if "${IPT[@]}" -n -L "$INPUT_CHAIN" >/dev/null 2>&1; then
    "${IPT[@]}" -F "$INPUT_CHAIN"
    "${IPT[@]}" -X "$INPUT_CHAIN"
  fi
}

teardown() {
  rm -f "$ATTESTATION"
  remove_firewall
  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    [[ "$(docker network inspect --format '{{len .Containers}}' "$NETWORK")" == 0 ]] || {
      echo "ERROR: refusing to remove an in-use ATS inventory network" >&2
      exit 1
    }
    docker network rm "$NETWORK" >/dev/null
  fi
}

if [[ "$ACTION" == teardown ]]; then
  teardown
  exit 0
fi
[[ "$ACTION" == ensure ]] || { echo "ERROR: usage: $0 <ensure|teardown>" >&2; exit 2; }
rm -f "$ATTESTATION"
[[ -x "$PROBE" ]] || { echo "ERROR: ATS network probe is unavailable" >&2; exit 1; }
[[ -r "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "ERROR: crawler environment is unavailable for boundary verification" >&2
  exit 1
}
[[ "$(docker info --format '{{.FirewallBackend.Driver}}')" == iptables ]] || {
  echo "ERROR: ATS inventory boundary requires Docker's iptables firewall backend" >&2
  exit 1
}

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  docker network create \
    --driver bridge \
    --opt "com.docker.network.bridge.name=$BRIDGE" \
    --opt com.docker.network.bridge.enable_icc=false \
    --label jobseek.maintenance.operation=ats-inventory \
    --label jobseek.maintenance.issue=6190 \
    "$NETWORK" >/dev/null
fi
docker rm -f "$PROBE_CONTAINER" >/dev/null 2>&1 || true
[[ "$(docker network inspect --format '{{.Driver}}' "$NETWORK")" == bridge ]]
[[ "$(docker network inspect --format '{{.EnableIPv6}}' "$NETWORK")" == false ]]
[[ "$(docker network inspect --format '{{.Internal}}' "$NETWORK")" == false ]]
[[ "$(docker network inspect --format '{{index .Options "com.docker.network.bridge.name"}}' "$NETWORK")" == "$BRIDGE" ]]
[[ "$(docker network inspect --format '{{index .Options "com.docker.network.bridge.enable_icc"}}' "$NETWORK")" == false ]]
[[ "$(docker network inspect --format '{{index .Labels "jobseek.maintenance.operation"}}' "$NETWORK")" == ats-inventory ]]
[[ "$(docker network inspect --format '{{len .Containers}}' "$NETWORK")" == 0 ]] || {
  echo "ERROR: ATS inventory network has an unexpected attached container" >&2
  exit 1
}
gateway="$(docker network inspect --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' "$NETWORK")"
[[ "$gateway" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "ERROR: ATS inventory bridge has no IPv4 gateway" >&2
  exit 1
}

ENDPOINTS="$(mktemp /run/lock/jobseek-ats-inventory-network.XXXXXX)"
chmod 0600 "$ENDPOINTS"
open_shared_crawler_lock
flock -w 300 8 || {
  echo "ERROR: timed out waiting for crawler state before network verification" >&2
  exit 1
}
"$PROBE" build --env-file "$ENV_FILE" --gateway "$gateway" --output "$ENDPOINTS"
release_file="$DEPLOY_SUCCESS"
[[ ! -e "$STATE_ROOT/acceptance-crawler.env" ]] || \
  release_file="$STATE_ROOT/acceptance-crawler.env"
mapfile -t tags < <(sed -n 's/^CRAWLER_IMAGE_TAG=//p' "$release_file" 2>/dev/null)
[[ ${#tags[@]} -eq 1 && "${tags[0]}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][a-zA-Z0-9.]+)?$ ]] || {
  echo "ERROR: committed crawler image is unavailable for network verification" >&2
  exit 1
}
flock -u 8

remove_firewall
"${IPT[@]}" -N "$EGRESS_CHAIN"
"${IPT[@]}" -N "$INPUT_CHAIN"
for cidr in \
  0.0.0.0/8 \
  10.0.0.0/8 \
  100.64.0.0/10 \
  127.0.0.0/8 \
  169.254.0.0/16 \
  172.16.0.0/12 \
  192.0.0.0/24 \
  192.0.2.0/24 \
  192.168.0.0/16 \
  198.18.0.0/15 \
  198.51.100.0/24 \
  203.0.113.0/24 \
  224.0.0.0/4 \
  240.0.0.0/4; do
  "${IPT[@]}" -A "$EGRESS_CHAIN" -d "$cidr" -j REJECT
done
"${IPT[@]}" -A "$EGRESS_CHAIN" -p tcp --dport 443 -j ACCEPT
"${IPT[@]}" -A "$EGRESS_CHAIN" -p udp --dport 53 -j ACCEPT
"${IPT[@]}" -A "$EGRESS_CHAIN" -p tcp --dport 53 -j ACCEPT
"${IPT[@]}" -A "$EGRESS_CHAIN" -j REJECT
"${IPT[@]}" -A "$INPUT_CHAIN" -j REJECT
"${IPT[@]}" -I DOCKER-USER 1 -i "$BRIDGE" -j "$EGRESS_CHAIN"
"${IPT[@]}" -I INPUT 1 -i "$BRIDGE" -j "$INPUT_CHAIN"

image="ghcr.io/colophon-group/jobseek-crawler:${tags[0]}"
docker image inspect "$image" >/dev/null
docker run --rm \
  --name "$PROBE_CONTAINER" \
  --network "$NETWORK" \
  --dns 1.1.1.1 \
  --dns 1.0.0.1 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --memory 96m \
  --cpus 0.25 \
  --pids-limit 32 \
  --mount "type=bind,src=$PROBE,dst=/run/probe/network-probe.py,readonly" \
  --mount "type=bind,src=$ENDPOINTS,dst=/run/probe/endpoints.json,readonly" \
  "$image" \
  /app/.venv/bin/python /run/probe/network-probe.py verify /run/probe/endpoints.json

network_id="$(docker network inspect --format '{{.Id}}' "$NETWORK")"
[[ "$network_id" =~ ^[0-9a-f]{64}$ ]] || {
  echo "ERROR: ATS inventory network ID is invalid" >&2
  exit 1
}
ATTESTATION_TEMP="$(mktemp /run/lock/jobseek-ats-inventory-network-verified.XXXXXX)"
printf 'NETWORK_ID=%s\nVERIFIED_AT=%s\n' "$network_id" "$(date +%s)" \
  >"$ATTESTATION_TEMP"
chown root:deploy "$ATTESTATION_TEMP"
chmod 0640 "$ATTESTATION_TEMP"
mv "$ATTESTATION_TEMP" "$ATTESTATION"
ATTESTATION_TEMP=""
