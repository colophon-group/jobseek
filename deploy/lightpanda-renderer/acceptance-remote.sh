#!/usr/bin/env bash
# shellcheck disable=SC2029 # Interpolated remote values are validated first.
set -euo pipefail
set +x
umask 077
: "${TARGET_HOST:?Murmur host is required}"
: "${SSH_PRIVATE_KEY:?SSH key is required}"
: "${SSH_KNOWN_HOSTS:?Murmur host keys are required}"
: "${RUNNER_TEMP:?runner temp is required}"
ACCEPTANCE_COMMIT="${1:-}"
PHASE="${2:-}"
RENDERER_SOURCE_COMMIT="${3:-}"
RENDERER_IMAGE_REF="${4:-}"
[[ "$ACCEPTANCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$PHASE" == host-policy-reboot || "$PHASE" == renderer-restarts ]]
if [[ "$PHASE" == host-policy-reboot ]]; then
  [[ -z "$RENDERER_SOURCE_COMMIT" && -z "$RENDERER_IMAGE_REF" ]]
else
  [[ "$RENDERER_SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]
  [[ "$RENDERER_IMAGE_REF" =~ ^ghcr\.io/colophon-group/jobseek-lightpanda-renderer@sha256:[0-9a-f]{64}$ ]]
fi
[[ "$TARGET_HOST" =~ ^[A-Za-z0-9.-]+$ ]]
[[ "$(git rev-parse HEAD)" == "$ACCEPTANCE_COMMIT" ]]
[[ "${GITHUB_RUN_ID:-}" =~ ^[1-9][0-9]*$ && "${GITHUB_RUN_ATTEMPT:-}" =~ ^[1-9][0-9]*$ ]]
owner="r${GITHUB_RUN_ID}a${GITHUB_RUN_ATTEMPT}"
root="$(mktemp -d "$RUNNER_TEMP/lightpanda-acceptance.XXXXXX")"
report="$RUNNER_TEMP/lightpanda-host-acceptance.json"
host_stage=""
crawler_stage=""
cleanup_armed=0
[[ "$PHASE" == host-policy-reboot ]] || cleanup_armed=1
printf '{"schema_version":1,"status":"aborted","phase":"%s","acceptance_source_commit":"%s"}\n' \
  "$PHASE" "$ACCEPTANCE_COMMIT" >"$report"
key="$root/id"
known="$root/known"
printf '%s\n' "$SSH_PRIVATE_KEY" >"$key"
printf '%s\n' "$SSH_KNOWN_HOSTS" >"$known"
chmod 0600 "$key" "$known"
ssh-keygen -y -f "$key" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$known" >/dev/null
ssh_common=(-F /dev/null -i "$key" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known" \
  -o GlobalKnownHostsFile=/dev/null -o PasswordAuthentication=no \
  -o KbdInteractiveAuthentication=no -o ConnectTimeout=10 \
  -o ServerAliveInterval=10 -o ServerAliveCountMax=2 -o LogLevel=ERROR)
artifact_root=deploy/lightpanda-renderer
policy_sha="$(sha256sum "$artifact_root/network-policy.py" | awk '{print $1}')"
inventory_sha="$(sha256sum "$artifact_root/inventory.json" | awk '{print $1}')"
unit_sha="$(sha256sum "$artifact_root/jobseek-lightpanda-network.service" | awk '{print $1}')"
[[ "$policy_sha$inventory_sha$unit_sha" =~ ^[0-9a-f]{192}$ ]]
stage_host() {
  host_stage="$(ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
    "umask 077; mktemp -d /tmp/jobseek-lightpanda-acceptance.XXXXXX")"
  [[ "$host_stage" =~ ^/tmp/jobseek-lightpanda-acceptance\.[A-Za-z0-9]+$ ]]
  tar -czf "$root/host.tar.gz" -C "$artifact_root" acceptance-host.py verify.py
  timeout --foreground --kill-after=15s 60s ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
    "tar -xzf - -C '$host_stage'; chmod 0600 '$host_stage/acceptance-host.py' '$host_stage/verify.py'" \
    <"$root/host.tar.gz"
}
remove_host_stage() {
  [[ -z "$host_stage" ]] && return 0
  [[ "$host_stage" =~ ^/tmp/jobseek-lightpanda-acceptance\.[A-Za-z0-9]+$ ]]
  ssh "${ssh_common[@]}" "root@$TARGET_HOST" "rm -rf -- '$host_stage'"
  host_stage=""
}
snapshot() {
  local mode="$1" output="$2"
  local source_args=()
  [[ "$mode" == empty ]] || source_args=(
    --renderer-source "$RENDERER_SOURCE_COMMIT" --renderer-image-ref "$RENDERER_IMAGE_REF"
  )
  timeout --foreground --kill-after=15s 150s ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
    "flock --shared --wait 60 /run/lock/jobseek-lightpanda-network.lock python3 '$host_stage/acceptance-host.py' snapshot --mode '$mode' --verifier '$host_stage/verify.py' --policy-sha256 '$policy_sha' --inventory-sha256 '$inventory_sha' --unit-sha256 '$unit_sha' ${source_args[*]}" \
    >"$output"
}
cold_snapshot() {
  local output="$1" deadline=$((SECONDS + 120))
  until snapshot cold "$output"; do
    (( SECONDS < deadline )) || return 1
    sleep 5
  done
}
traffic_counters() {
  timeout --foreground --kill-after=15s 60s ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
    "flock --shared --wait 60 /run/lock/jobseek-lightpanda-network.lock python3 '$host_stage/acceptance-host.py' traffic-counters"
}
boot_id() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["boot_id"])' "$1"
}
reboot_host() {
  local previous="$1" observed="" status
  set +e
  timeout --foreground --kill-after=15s 90s ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
    "flock --exclusive --wait 60 /run/lock/jobseek-lightpanda-network.lock systemctl reboot --no-wall"
  status=$?
  set -e
  [[ "$status" -eq 0 || "$status" -eq 124 || "$status" -eq 255 ]]
  for _attempt in {1..36}; do
    observed="$(ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
      "cat /proc/sys/kernel/random/boot_id" 2>/dev/null || :)"
    [[ "$observed" =~ ^[0-9a-f-]{36}$ && "$observed" != "$previous" ]] && return 0
    sleep 5
  done
  return 1
}
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if (( cleanup_armed )); then
    timeout --foreground --kill-after=15s 120s ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
      "flock --exclusive --wait 60 /run/lock/jobseek-lightpanda-network.lock /usr/local/libexec/jobseek-lightpanda-network-policy quarantine" \
      >/dev/null 2>&1 || status=1
    timeout --foreground --kill-after=15s 120s ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
      "flock --exclusive --wait 60 /run/lock/jobseek-lightpanda-network.lock systemctl restart docker.service" \
      >/dev/null 2>&1 || status=1
    [[ -n "$host_stage" ]] || stage_host >/dev/null 2>&1 || status=1
    if [[ -n "$host_stage" ]]; then
      cold_snapshot "$root/cleanup-cold.json" >/dev/null 2>&1 || status=1
    else
      status=1
    fi
  fi
  if [[ -n "$crawler_stage" && "$crawler_stage" =~ ^/tmp/jobseek-lightpanda-crawler-acceptance\.${owner}\.[A-Za-z0-9]+$ ]]; then
    ssh "${crawler_ssh[@]}" "deploy@$CRAWLER_HOST" "rm -rf -- '$crawler_stage'" >/dev/null 2>&1 || status=1
  fi
  remove_host_stage >/dev/null 2>&1 || status=1
  rm -rf -- "$root" || status=1
  exit "$status"
}
trap cleanup EXIT HUP INT TERM
stage_host
if [[ "$PHASE" == host-policy-reboot ]]; then
  before="$root/before.json"
  after="$root/after.json"
  snapshot empty "$before"
  remove_host_stage
  reboot_host "$(boot_id "$before")"
  stage_host
  snapshot empty "$after"
  for name in before after; do
    timeout --foreground --kill-after=15s 60s ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
      "install -o root -g root -m 0600 /dev/stdin '$host_stage/$name.json'" \
      <"$root/$name.json"
  done
  ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
    "python3 '$host_stage/acceptance-host.py' install-receipt --before '$host_stage/before.json' --after '$host_stage/after.json' --acceptance-source-commit '$ACCEPTANCE_COMMIT'" \
    >"$report"
  exit 0
fi
: "${CRAWLER_HOST:?crawler host is required}"
: "${CRAWLER_SSH_KNOWN_HOSTS:?crawler host keys are required}"
[[ "$CRAWLER_HOST" =~ ^[A-Za-z0-9.-]+$ ]]
crawler_known="$root/crawler-known"
printf '%s\n' "$CRAWLER_SSH_KNOWN_HOSTS" >"$crawler_known"
chmod 0600 "$crawler_known"
ssh-keygen -F "$CRAWLER_HOST" -f "$crawler_known" >/dev/null
crawler_ssh=(-F /dev/null -i "$key" -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$crawler_known" \
  -o GlobalKnownHostsFile=/dev/null -o PasswordAuthentication=no \
  -o KbdInteractiveAuthentication=no -o ConnectTimeout=10 -o LogLevel=ERROR)
initial="$root/initial.json"
after_probe="$root/after-probe.json"
after_docker="$root/after-docker.json"
after_reboot="$root/after-reboot.json"
probes="$root/probes.json"
crawler="$root/crawler.json"
traffic_before="$root/traffic-before.json"
traffic_after="$root/traffic-after.json"
snapshot running "$initial"
crawler_stage="$(ssh "${crawler_ssh[@]}" "deploy@$CRAWLER_HOST" \
  "umask 077; mktemp -d '/tmp/jobseek-lightpanda-crawler-acceptance.${owner}.XXXXXX'")"
[[ "$crawler_stage" =~ ^/tmp/jobseek-lightpanda-crawler-acceptance\.${owner}\.[A-Za-z0-9]+$ ]]
tar -czf "$root/crawler.tar.gz" -C "$artifact_root" acceptance-crawler.sh acceptance-client.py
ssh "${crawler_ssh[@]}" "deploy@$CRAWLER_HOST" \
  "tar -xzf - -C '$crawler_stage'; chmod 0500 '$crawler_stage/acceptance-crawler.sh'; chmod 0444 '$crawler_stage/acceptance-client.py'" \
  <"$root/crawler.tar.gz"
traffic_counters >"$traffic_before"
timeout --foreground --kill-after=30s 3m ssh "${crawler_ssh[@]}" "deploy@$CRAWLER_HOST" \
  "bash '$crawler_stage/acceptance-crawler.sh' '$crawler_stage' '$owner'" >"$crawler"
traffic_counters >"$traffic_after"
ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
  "flock --shared --wait 60 /run/lock/jobseek-lightpanda-network.lock python3 '$host_stage/acceptance-host.py' negative-probes --mode running --verifier '$host_stage/verify.py' --policy-sha256 '$policy_sha' --inventory-sha256 '$inventory_sha' --unit-sha256 '$unit_sha' --renderer-source '$RENDERER_SOURCE_COMMIT' --renderer-image-ref '$RENDERER_IMAGE_REF'" \
  >"$probes"
snapshot running "$after_probe"
ssh "${ssh_common[@]}" "root@$TARGET_HOST" \
  "flock --exclusive --wait 60 /run/lock/jobseek-lightpanda-network.lock systemctl restart docker.service"
cold_snapshot "$after_docker"
remove_host_stage
reboot_host "$(boot_id "$after_docker")"
stage_host
cold_snapshot "$after_reboot"
python3 "$artifact_root/acceptance-host.py" report \
  --initial "$initial" --after-probe "$after_probe" \
  --after-docker "$after_docker" --after-reboot "$after_reboot" \
  --crawler "$crawler" --probes "$probes" \
  --traffic-before "$traffic_before" --traffic-after "$traffic_after" \
  --acceptance-source-commit "$ACCEPTANCE_COMMIT" \
  --renderer-source-commit "$RENDERER_SOURCE_COMMIT" \
  --renderer-image-ref "$RENDERER_IMAGE_REF" >"$report"
cleanup_armed=0
