#!/usr/bin/env bash
# Transactionally install the crawler-host ATS inventory timer and credentials.
set -euo pipefail
umask 077

[[ "$(id -u)" -eq 0 ]] || { echo "ERROR: install-host.sh requires root" >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_ROOT=/var/lib/jobseek-ats-inventory
CONFIG_ROOT=/etc/jobseek-ats-inventory
DEPLOY_SHA="${JOBSEEK_ATS_INVENTORY_DEPLOY_SHA:-}"
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
)
TIMER_WAS_ENABLED=0
TIMER_WAS_ACTIVE=0
SERVICE_WAS_ACTIVE=0
ROLLBACK_ARMED=1

[[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "ERROR: deployment SHA is invalid" >&2; exit 1; }
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
install -d -o root -g deploy -m 0750 "$STATE_ROOT"
install -d -o deploy -g deploy -m 0770 "$STATE_ROOT/cache"
install -d -o deploy -g deploy -m 0770 "$STATE_ROOT/status"
install -d -o root -g deploy -m 0750 "$CONFIG_ROOT"
ROLLBACK="$(mktemp -d "$STATE_ROOT/rollback.XXXXXX")"
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
  systemctl daemon-reload
  (( TIMER_WAS_ENABLED == 0 )) || systemctl enable jobseek-ats-inventory.timer >/dev/null 2>&1 || true
  (( TIMER_WAS_ACTIVE == 0 )) || systemctl start jobseek-ats-inventory.timer >/dev/null 2>&1 || true
  (( SERVICE_WAS_ACTIVE == 0 )) || systemctl start --no-block jobseek-ats-inventory.service >/dev/null 2>&1 || true
}

cleanup() {
  status=$?
  trap - EXIT
  if (( status != 0 && ROLLBACK_ARMED )); then restore_previous; fi
  rm -rf -- "$ROLLBACK"
  exit "$status"
}
trap cleanup EXIT

systemctl stop jobseek-ats-inventory.timer >/dev/null 2>&1 || true
systemctl stop jobseek-ats-inventory.service >/dev/null 2>&1 || true

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

if [[ ! -e "$CONFIG_ROOT/config.env" ]]; then
  temporary="$(mktemp "$CONFIG_ROOT/config.env.XXXXXX")"
  printf 'ATS_INVENTORY_MODE=report\nATS_INVENTORY_ROLLOUT_CAP=1\n' >"$temporary"
  install -o root -g deploy -m 0640 "$temporary" "$CONFIG_ROOT/config.env"
  rm -f "$temporary"
  install -o root -g deploy -m 0640 /dev/null "$CONFIG_ROOT/writes-disabled"
fi

printf '%s\n' "$DEPLOY_SHA" >"$STATE_ROOT/deployed-sha.tmp"
install -o root -g deploy -m 0640 "$STATE_ROOT/deployed-sha.tmp" "$STATE_ROOT/deployed-sha"
rm -f "$STATE_ROOT/deployed-sha.tmp"
printf '%s\n' "$WRAPPER_SHA256" >"$STATE_ROOT/wrapper-sha256.tmp"
install -o root -g deploy -m 0640 "$STATE_ROOT/wrapper-sha256.tmp" "$STATE_ROOT/wrapper-sha256"
rm -f "$STATE_ROOT/wrapper-sha256.tmp"

systemctl daemon-reload
systemd-analyze verify \
  /etc/systemd/system/jobseek-ats-inventory.service \
  /etc/systemd/system/jobseek-ats-inventory.timer
systemctl enable --now jobseek-ats-inventory.timer
systemctl is-active --quiet jobseek-ats-inventory.timer
runuser -u deploy -- test -r "$STATE_ROOT/deployed-sha"
runuser -u deploy -- test -r "$STATE_ROOT/wrapper-sha256"
ROLLBACK_ARMED=0
