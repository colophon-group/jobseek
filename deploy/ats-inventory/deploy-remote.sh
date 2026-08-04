#!/usr/bin/env bash
# Copy and install ATS inventory artifacts over host-key-pinned OpenSSH.
set -euo pipefail
umask 077

DEPLOY_SHA="${1:-}"
EXPECTED_TAG="${2:-}"
[[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "$EXPECTED_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
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
  timeout --foreground --signal=TERM --kill-after=30s 15m \
    ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
    "install -d -o root -g root -m 0755 /opt/jobseek-ats-inventory && \
      tar --extract --gzip --file - --directory /opt/jobseek-ats-inventory \
        --no-same-owner --no-same-permissions"

# The host surface and application image deploy independently. Wait for the
# exact reviewed crawler release before quiescing or replacing the live runner.
timeout --foreground --signal=TERM --kill-after=30s 90m \
  ssh "${ssh_options[@]}" "root@$TARGET_HOST" "bash -s -- '$EXPECTED_TAG'" <<'REMOTE'
set -euo pipefail
expected_tag="$1"
for ((attempt = 1; attempt <= 180; attempt++)); do
  deployed_tag="$(sed -n 's/^CRAWLER_IMAGE_TAG=//p' /home/deploy/.env | tail -n1)"
  [[ "$deployed_tag" != "$expected_tag" ]] || break
  sleep 30
done
[[ "$(sed -n 's/^CRAWLER_IMAGE_TAG=//p' /home/deploy/.env | tail -n1)" == "$expected_tag" ]]
REMOTE

{
  printf '%s\n' "$DEPLOY_SHA"
  printf '%s' "$ATS_GITHUB_APP_ID" | base64 | tr -d '\n'; printf '\n'
  printf '%s' "$ATS_GITHUB_APP_INSTALLATION_ID" | base64 | tr -d '\n'; printf '\n'
  printf '%s' "$ATS_GITHUB_APP_PRIVATE_KEY" | base64 | tr -d '\n'; printf '\n'
} >"$payload"
timeout --foreground --signal=TERM --kill-after=30s 20m \
  ssh "${ssh_options[@]}" "root@$TARGET_HOST" \
  "bash /opt/jobseek-ats-inventory/deploy/ats-inventory/install-host-from-stdin.sh" \
  <"$payload"

timeout --foreground --signal=TERM --kill-after=30s 4h \
  ssh "${ssh_options[@]}" "root@$TARGET_HOST" "bash -s" <<'REMOTE'
set -euo pipefail
systemctl is-enabled --quiet jobseek-ats-inventory.timer
systemctl is-active --quiet jobseek-ats-inventory.timer
/usr/local/sbin/jobseek-ats-inventory-control status
if [[ -e /etc/jobseek-ats-inventory/writes-disabled ]]; then
  systemctl reset-failed jobseek-ats-inventory.service || true
  systemctl start jobseek-ats-inventory.service
  systemctl is-failed --quiet jobseek-ats-inventory.service && exit 1
  python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("/var/lib/jobseek-ats-inventory/status/current.json").read_text())
assert payload["last_attempt_success"] == 1
assert payload["effective_mode"] == "report"
assert payload["report"]["data_only"] is True
PY
fi
REMOTE
