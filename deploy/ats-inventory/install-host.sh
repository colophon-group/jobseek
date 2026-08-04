#!/usr/bin/env bash
# Transactionally install and accept the crawler-host ATS inventory timer.
set -euo pipefail
umask 077

[[ "$(id -u)" -eq 0 ]] || { echo "ERROR: install-host.sh requires root" >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_ROOT=/var/lib/jobseek-ats-inventory
CONFIG_ROOT=/etc/jobseek-ats-inventory
DEPLOY_SUCCESS=/home/deploy/.crawler-deploy-success.env
CRAWLER_LOCK=/run/lock/jobseek-crawler-mutation.lock
ACCEPTANCE_PIN="$STATE_ROOT/acceptance-crawler.env"
DEPLOY_SHA="${JOBSEEK_ATS_INVENTORY_DEPLOY_SHA:-}"
EXPECTED_CRAWLER_TAG="${JOBSEEK_EXPECTED_CRAWLER_IMAGE_TAG:-}"
EXPECTED_CRAWLER_REVISION="${JOBSEEK_EXPECTED_CRAWLER_DEPLOY_REVISION:-}"
APP_ID_FILE="${JOBSEEK_GITHUB_APP_ID_FILE:-}"
INSTALLATION_ID_FILE="${JOBSEEK_GITHUB_APP_INSTALLATION_ID_FILE:-}"
PRIVATE_KEY_FILE="${JOBSEEK_GITHUB_APP_PRIVATE_KEY_FILE:-}"
WRAPPER_SHA256="$(sha256sum "$REPO_ROOT/deploy/ats-inventory/run.sh" | awk '{print $1}')"
FILES=(
  /usr/local/sbin/jobseek-ats-inventory
  /usr/local/sbin/jobseek-ats-inventory-control
  /usr/local/sbin/jobseek-ats-inventory-bounded-tee
  /usr/local/sbin/jobseek-ats-inventory-github-token
  /usr/local/sbin/jobseek-ats-inventory-status
  /etc/systemd/system/jobseek-ats-inventory.service
  /etc/systemd/system/jobseek-ats-inventory.timer
  /etc/jobseek-ats-inventory/config.env
  /etc/jobseek-ats-inventory/writes-disabled
  /etc/jobseek-ats-inventory/github-app-id
  /etc/jobseek-ats-inventory/github-app-installation-id
  /etc/jobseek-ats-inventory/github-app-private-key
  /var/lib/jobseek-ats-inventory/deployed-sha
  /var/lib/jobseek-ats-inventory/wrapper-sha256
  /var/lib/jobseek-ats-inventory/acceptance-crawler.env
)
TIMER_WAS_ENABLED=0
TIMER_WAS_ACTIVE=0
SERVICE_WAS_ACTIVE=0
STATE_ROOT_WAS_PRESENT=0
CONFIG_ROOT_WAS_PRESENT=0
CONFIG_WAS_PRESENT=0
WRITE_GATE_WAS_PRESENT=0
ROLLBACK_ARMED=1

[[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "ERROR: deployment SHA is invalid" >&2; exit 1; }
[[ "$EXPECTED_CRAWLER_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][a-zA-Z0-9.]+)?$ ]] || {
  echo "ERROR: expected crawler image tag is invalid" >&2
  exit 1
}
[[ "$EXPECTED_CRAWLER_REVISION" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ERROR: expected crawler deployment revision is invalid" >&2
  exit 1
}
[[ "$WRAPPER_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "ERROR: wrapper digest is invalid" >&2; exit 1; }
for path in "$APP_ID_FILE" "$INSTALLATION_ID_FILE" "$PRIVATE_KEY_FILE"; do
  [[ -n "$path" && -f "$path" && ! -L "$path" ]] || {
    echo "ERROR: candidate GitHub App credential is unavailable" >&2
    exit 1
  }
done
app_id="$(tr -d '\n' <"$APP_ID_FILE")"
installation_id="$(tr -d '\n' <"$INSTALLATION_ID_FILE")"
[[ "$app_id" =~ ^[1-9][0-9]{0,19}$ ]] || { echo "ERROR: GitHub App ID is invalid" >&2; exit 1; }
[[ "$installation_id" =~ ^[1-9][0-9]{0,19}$ ]] || {
  echo "ERROR: GitHub App installation ID is invalid" >&2
  exit 1
}
grep -q 'PRIVATE KEY-----' "$PRIVATE_KEY_FILE" || {
  echo "ERROR: GitHub App private key is not PEM encoded" >&2
  exit 1
}
unset app_id installation_id

systemctl is-enabled --quiet jobseek-ats-inventory.timer 2>/dev/null && TIMER_WAS_ENABLED=1 || true
systemctl is-active --quiet jobseek-ats-inventory.timer 2>/dev/null && TIMER_WAS_ACTIVE=1 || true
systemctl is-active --quiet jobseek-ats-inventory.service 2>/dev/null && SERVICE_WAS_ACTIVE=1 || true
[[ ! -d "$STATE_ROOT" ]] || STATE_ROOT_WAS_PRESENT=1
[[ ! -d "$CONFIG_ROOT" ]] || CONFIG_ROOT_WAS_PRESENT=1
[[ ! -e "$CONFIG_ROOT/config.env" ]] || CONFIG_WAS_PRESENT=1
[[ ! -e "$CONFIG_ROOT/writes-disabled" ]] || WRITE_GATE_WAS_PRESENT=1
ROLLBACK="$(mktemp -d /run/jobseek-ats-inventory-rollback.XXXXXX)"
for path in "${FILES[@]}"; do
  [[ ! -e "$path" ]] || cp --archive "$path" "$ROLLBACK/"
done

restore_previous() {
  local group mode name path
  systemctl disable --now jobseek-ats-inventory.timer >/dev/null 2>&1 || true
  systemctl stop jobseek-ats-inventory.service >/dev/null 2>&1 || true
  for path in "${FILES[@]}"; do
    name="${path##*/}"
    if [[ -e "$ROLLBACK/$name" ]]; then
      group=root
      [[ "$path" == "$STATE_ROOT/"* ]] && group=deploy
      [[ "$path" == "$CONFIG_ROOT/config.env" ]] && group=deploy
      [[ "$path" == "$CONFIG_ROOT/writes-disabled" ]] && group=deploy
      mode="$(stat -c '%a' "$ROLLBACK/$name")"
      install -o root -g "$group" -m "$mode" "$ROLLBACK/$name" "$path"
    else
      rm -f "$path"
    fi
  done
  (( CONFIG_ROOT_WAS_PRESENT != 0 )) || rm -rf -- "$CONFIG_ROOT"
  (( STATE_ROOT_WAS_PRESENT != 0 )) || rm -rf -- "$STATE_ROOT"
  systemctl daemon-reload
  (( TIMER_WAS_ENABLED == 0 )) || systemctl enable jobseek-ats-inventory.timer >/dev/null 2>&1 || true
  (( TIMER_WAS_ACTIVE == 0 )) || systemctl start jobseek-ats-inventory.timer >/dev/null 2>&1 || true
  (( SERVICE_WAS_ACTIVE == 0 )) || systemctl start --no-block jobseek-ats-inventory.service >/dev/null 2>&1 || true
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if (( status != 0 && ROLLBACK_ARMED )); then restore_previous; fi
  rm -rf -- "$ROLLBACK"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

read_exact_release() {
  local key="$1"
  mapfile -t matches < <(sed -n "s/^${key}=//p" "$DEPLOY_SUCCESS" 2>/dev/null)
  [[ ${#matches[@]} -eq 1 && -n "${matches[0]}" ]] || {
    echo "ERROR: ${key} is not unique in the committed crawler marker" >&2
    return 1
  }
  printf '%s' "${matches[0]}"
}

stop_unit_if_present() {
  local load_state unit="$1"
  if ! load_state="$(systemctl show --property=LoadState --value "$unit")"; then
    echo "ERROR: could not resolve ${unit} before replacement" >&2
    return 1
  fi
  [[ "$load_state" != not-found ]] || return 0
  systemctl stop "$unit"
  if systemctl is-active --quiet "$unit"; then
    echo "ERROR: ${unit} remained active after stop" >&2
    return 1
  fi
}

# Serialize the exact committed-release check and the short host-surface
# replacement with crawler deploys. The long report-only acceptance is pinned
# below, so the mutation lock can be released before network work begins.
exec 8>"$CRAWLER_LOCK"
flock -w 7200 8 || { echo "ERROR: timed out waiting for crawler mutation lock" >&2; exit 1; }
[[ -f "$DEPLOY_SUCCESS" && ! -L "$DEPLOY_SUCCESS" ]] || {
  echo "ERROR: committed crawler deployment marker is unavailable" >&2
  exit 1
}
[[ "$(read_exact_release CRAWLER_IMAGE_TAG)" == "$EXPECTED_CRAWLER_TAG" ]] || {
  echo "ERROR: committed crawler image does not match this ATS deployment" >&2
  exit 1
}
[[ "$(read_exact_release JOBSEEK_DEPLOY_REVISION)" == "$EXPECTED_CRAWLER_REVISION" ]] || {
  echo "ERROR: committed crawler revision does not match this ATS deployment" >&2
  exit 1
}

stop_unit_if_present jobseek-ats-inventory.timer
stop_unit_if_present jobseek-ats-inventory.service

install -d -o root -g deploy -m 0750 "$STATE_ROOT"
install -d -o deploy -g deploy -m 0770 "$STATE_ROOT/cache"
install -d -o deploy -g deploy -m 0770 "$STATE_ROOT/status"
install -d -o root -g deploy -m 0750 "$CONFIG_ROOT"
install -o root -g root -m 0755 "$REPO_ROOT/deploy/ats-inventory/run.sh" \
  /usr/local/sbin/jobseek-ats-inventory
install -o root -g root -m 0755 "$REPO_ROOT/deploy/ats-inventory/control.sh" \
  /usr/local/sbin/jobseek-ats-inventory-control
install -o root -g root -m 0755 "$REPO_ROOT/deploy/ats-inventory/bounded-tee.py" \
  /usr/local/sbin/jobseek-ats-inventory-bounded-tee
install -o root -g root -m 0755 "$REPO_ROOT/deploy/ats-inventory/github-app-token.py" \
  /usr/local/sbin/jobseek-ats-inventory-github-token
install -o root -g root -m 0755 "$REPO_ROOT/deploy/ats-inventory/status.py" \
  /usr/local/sbin/jobseek-ats-inventory-status
install -o root -g root -m 0644 "$REPO_ROOT/deploy/systemd/jobseek-ats-inventory.service" \
  /etc/systemd/system/jobseek-ats-inventory.service
install -o root -g root -m 0644 "$REPO_ROOT/deploy/systemd/jobseek-ats-inventory.timer" \
  /etc/systemd/system/jobseek-ats-inventory.timer
install -o root -g root -m 0600 "$APP_ID_FILE" "$CONFIG_ROOT/github-app-id"
install -o root -g root -m 0600 "$INSTALLATION_ID_FILE" "$CONFIG_ROOT/github-app-installation-id"
install -o root -g root -m 0600 "$PRIVATE_KEY_FILE" "$CONFIG_ROOT/github-app-private-key"

if (( CONFIG_WAS_PRESENT == 0 )); then
  temporary="$(mktemp "$CONFIG_ROOT/config.env.XXXXXX")"
  printf 'ATS_INVENTORY_MODE=report\nATS_INVENTORY_ROLLOUT_CAP=1\n' >"$temporary"
  install -o root -g deploy -m 0640 "$temporary" "$CONFIG_ROOT/config.env"
  rm -f "$temporary"
fi

printf '%s\n' "$DEPLOY_SHA" >"$STATE_ROOT/deployed-sha.tmp"
install -o root -g deploy -m 0640 "$STATE_ROOT/deployed-sha.tmp" "$STATE_ROOT/deployed-sha"
rm -f "$STATE_ROOT/deployed-sha.tmp"
printf '%s\n' "$WRAPPER_SHA256" >"$STATE_ROOT/wrapper-sha256.tmp"
install -o root -g deploy -m 0640 "$STATE_ROOT/wrapper-sha256.tmp" "$STATE_ROOT/wrapper-sha256"
rm -f "$STATE_ROOT/wrapper-sha256.tmp"

# Every install is accepted in report mode while rollback is still armed. The
# root-owned pin keeps this run on the release verified under the crawler lock,
# even if a later crawler deploy begins while the data refresh is running.
acceptance_temporary="$(mktemp "$STATE_ROOT/acceptance-crawler.XXXXXX")"
printf 'CRAWLER_IMAGE_TAG=%s\nJOBSEEK_DEPLOY_REVISION=%s\n' \
  "$EXPECTED_CRAWLER_TAG" "$EXPECTED_CRAWLER_REVISION" >"$acceptance_temporary"
chown root:deploy "$acceptance_temporary"
chmod 0640 "$acceptance_temporary"
mv "$acceptance_temporary" "$ACCEPTANCE_PIN"
install -o root -g deploy -m 0640 /dev/null "$CONFIG_ROOT/writes-disabled"
flock -u 8

systemctl daemon-reload
systemd-analyze verify \
  /etc/systemd/system/jobseek-ats-inventory.service \
  /etc/systemd/system/jobseek-ats-inventory.timer
systemctl enable jobseek-ats-inventory.timer
runuser -u deploy -- test -r "$STATE_ROOT/deployed-sha"
runuser -u deploy -- test -r "$STATE_ROOT/wrapper-sha256"
runuser -u deploy -- test -r "$ACCEPTANCE_PIN"
acceptance_started="$(date +%s)"
systemctl reset-failed jobseek-ats-inventory.service || true
systemctl start jobseek-ats-inventory.service
if systemctl is-failed --quiet jobseek-ats-inventory.service; then
  echo "ERROR: ATS inventory acceptance service failed" >&2
  exit 1
fi
python3 - "$acceptance_started" "$DEPLOY_SHA" "$WRAPPER_SHA256" <<'PY'
import json
import sys
from pathlib import Path

started = int(sys.argv[1])
expected_revision = sys.argv[2]
expected_wrapper = sys.argv[3]
state = Path("/var/lib/jobseek-ats-inventory")
payload = json.loads((state / "status/current.json").read_text())
assert payload["last_attempt_started_unixtime"] >= started
assert payload["last_attempt_success"] == 1
assert payload["effective_mode"] == "report"
assert payload["report"]["data_only"] is True
assert (state / "deployed-sha").read_text().strip() == expected_revision
assert (state / "wrapper-sha256").read_text().strip() == expected_wrapper
PY

rm -f "$ACCEPTANCE_PIN"
if (( CONFIG_WAS_PRESENT != 0 && WRITE_GATE_WAS_PRESENT == 0 )); then
  rm -f "$CONFIG_ROOT/writes-disabled"
fi
systemctl start jobseek-ats-inventory.timer
systemctl is-enabled --quiet jobseek-ats-inventory.timer
systemctl is-active --quiet jobseek-ats-inventory.timer
ROLLBACK_ARMED=0
