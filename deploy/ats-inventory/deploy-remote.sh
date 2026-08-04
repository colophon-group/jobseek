#!/usr/bin/env bash
# Copy and install ATS inventory artifacts over host-key-pinned OpenSSH.
set -euo pipefail
umask 077

DEPLOY_SHA="${1:-}"
EXPECTED_TAG="${2:-}"
EXPECTED_REVISION="${3:-}"
[[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 2
if [[ "$EXPECTED_TAG" == current || "$EXPECTED_REVISION" == current ]]; then
  [[ "$EXPECTED_TAG" == current && "$EXPECTED_REVISION" == current ]] || exit 2
else
  [[ "$EXPECTED_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][a-zA-Z0-9.]+)?$ ]] || exit 2
  [[ "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] || exit 2
fi
: "${TARGET_HOST:?TARGET_HOST is required}"
: "${SSH_PRIVATE_KEY:?SSH_PRIVATE_KEY is required}"
: "${SSH_KNOWN_HOSTS:?SSH_KNOWN_HOSTS is required}"
: "${ATS_GITHUB_APP_ID:?ATS_GITHUB_APP_ID is required}"
: "${ATS_GITHUB_APP_INSTALLATION_ID:?ATS_GITHUB_APP_INSTALLATION_ID is required}"
: "${ATS_GITHUB_APP_PRIVATE_KEY:?ATS_GITHUB_APP_PRIVATE_KEY is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
[[ "$TARGET_HOST" =~ ^[a-zA-Z0-9.-]+$ ]] || exit 2

ssh_root="$(mktemp -d "$RUNNER_TEMP/jobseek-ats-inventory-ssh.XXXXXX")"
payload="$(mktemp "$RUNNER_TEMP/jobseek-ats-inventory-payload.XXXXXX")"
cleanup() { rm -rf -- "$ssh_root"; rm -f -- "$payload"; }
trap cleanup EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" >"$ssh_root/id"
printf '%s\n' "$SSH_KNOWN_HOSTS" >"$ssh_root/known_hosts"
chmod 0600 "$ssh_root/id" "$ssh_root/known_hosts" "$payload"
ssh-keygen -y -f "$ssh_root/id" >/dev/null
ssh-keygen -F "$TARGET_HOST" -f "$ssh_root/known_hosts" >/dev/null
ssh_options=(
  -F /dev/null
  -i "$ssh_root/id"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$ssh_root/known_hosts"
  -o GlobalKnownHostsFile=/dev/null
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
  -o LogLevel=ERROR
)

tar --create --gzip --file - \
  deploy/ats-inventory \
  deploy/systemd/jobseek-ats-inventory.service \
  deploy/systemd/jobseek-ats-inventory.timer |
  timeout --foreground --signal=TERM --kill-after=30s 5m \
    ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
    "install -d -o root -g root -m 0755 /opt/jobseek-ats-inventory && \
      tar --extract --gzip --file - --directory /opt/jobseek-ats-inventory \
        --no-same-owner --no-same-permissions"

# The host surface and application image deploy independently. Wait for the
# exact reviewed crawler release to pass health checks and commit. The crawler
# mutation lock prevents observing its marker while a deploy or maintenance
# operation is active; the installer repeats this gate before any mutation.
resolved_release="$(timeout --foreground --signal=TERM --kill-after=30s 60m \
  ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
  "bash -s -- '$EXPECTED_TAG' '$EXPECTED_REVISION'" <<'REMOTE'
set -euo pipefail
expected_tag="$1"
expected_revision="$2"
marker=/home/deploy/.crawler-deploy-success.env
lock=/run/lock/jobseek-crawler-mutation.lock
open_shared_lock() {
  if [[ ! -e "$lock" ]]; then
    (umask 077; set -o noclobber; : >"$lock") 2>/dev/null || true
  fi
  [[ -f "$lock" && ! -L "$lock" ]] || return 1
  chown deploy:deploy "$lock"
  chmod 0600 "$lock"
  [[ "$(stat -c '%U:%G:%a' "$lock")" == deploy:deploy:600 ]] || return 1
  exec 8<"$lock"
}
read_exact() {
  local key="$1"
  mapfile -t matches < <(sed -n "s/^${key}=//p" "$marker" 2>/dev/null)
  [[ ${#matches[@]} -eq 1 ]] || return 1
  printf '%s' "${matches[0]}"
}
for ((attempt = 1; attempt <= 120; attempt++)); do
  open_shared_lock
  if flock -w 30 8; then
    deployed_tag="$(read_exact CRAWLER_IMAGE_TAG || true)"
    deployed_revision="$(read_exact JOBSEEK_DEPLOY_REVISION || true)"
    flock -u 8
    if [[ "$deployed_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][a-zA-Z0-9.]+)?$ && \
          "$deployed_revision" =~ ^[0-9a-f]{40}$ && \
          ( "$expected_tag" == current || \
            ( "$deployed_tag" == "$expected_tag" && "$deployed_revision" == "$expected_revision" ) ) ]]; then
      printf '%s %s\n' "$deployed_tag" "$deployed_revision"
      exit 0
    fi
  fi
  sleep 30
done
echo "ERROR: exact committed crawler release did not become available" >&2
exit 1
REMOTE
)"
read -r resolved_tag resolved_revision extra <<<"$resolved_release"
[[ -z "${extra:-}" ]] || exit 1
[[ "$resolved_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][a-zA-Z0-9.]+)?$ ]] || exit 1
[[ "$resolved_revision" =~ ^[0-9a-f]{40}$ ]] || exit 1

{
  printf '%s\n' "$DEPLOY_SHA"
  printf '%s\n' "$resolved_tag"
  printf '%s\n' "$resolved_revision"
  printf '%s' "$ATS_GITHUB_APP_ID" | base64 | tr -d '\n'; printf '\n'
  printf '%s' "$ATS_GITHUB_APP_INSTALLATION_ID" | base64 | tr -d '\n'; printf '\n'
  printf '%s' "$ATS_GITHUB_APP_PRIVATE_KEY" | base64 | tr -d '\n'; printf '\n'
} >"$payload"
timeout --foreground --signal=TERM --kill-after=30s 250m \
  ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
  "bash /opt/jobseek-ats-inventory/deploy/ats-inventory/install-host-from-stdin.sh" \
  <"$payload"

timeout --foreground --signal=TERM --kill-after=30s 5m \
  ssh "${ssh_options[@]}" "root@$TARGET_HOST" "bash -s -- '$DEPLOY_SHA'" <<'REMOTE'
set -euo pipefail
expected_revision="$1"
systemctl is-enabled --quiet jobseek-ats-inventory.timer
systemctl is-active --quiet jobseek-ats-inventory.timer
/usr/local/sbin/jobseek-ats-inventory-control status
[[ "$(tr -d '\n' </var/lib/jobseek-ats-inventory/deployed-sha)" == "$expected_revision" ]]
python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("/var/lib/jobseek-ats-inventory/status/current.json").read_text())
assert payload["last_attempt_success"] == 1
assert payload["effective_mode"] == "report"
assert payload["report"]["data_only"] is True
PY
REMOTE
