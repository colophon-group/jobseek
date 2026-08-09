#!/usr/bin/env bash
# Transactionally install the crawler-host reconciliation timer surface.
set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || {
  echo "ERROR: install-host.sh must run as root" >&2
  exit 1
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_ROOT=/var/lib/jobseek-reconciliation
DEPLOY_SHA="${JOBSEEK_RECONCILIATION_DEPLOY_SHA:-}"
WRAPPER_SHA256="$(sha256sum "$REPO_ROOT/deploy/reconciliation/run.sh" | awk '{print $1}')"
FILES=(
  /usr/local/sbin/jobseek-reconciliation-state
  /usr/local/sbin/jobseek-crawler-reconciliation
  /etc/systemd/system/jobseek-crawler-reconciliation.service
  /etc/systemd/system/jobseek-crawler-reconciliation.timer
  /var/lib/jobseek-reconciliation/deployed-sha
  /var/lib/jobseek-reconciliation/wrapper-sha256
)
ROLLBACK_ARMED=1
TIMER_WAS_ENABLED=0
TIMER_WAS_ACTIVE=0

if systemctl is-enabled --quiet jobseek-crawler-reconciliation.timer 2>/dev/null; then
  TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet jobseek-crawler-reconciliation.timer 2>/dev/null; then
  TIMER_WAS_ACTIVE=1
fi

install -d -o root -g deploy -m 0750 "$STATE_ROOT"
ROLLBACK="$(mktemp -d "${STATE_ROOT}/rollback.XXXXXX")"
for path in "${FILES[@]}"; do
  if [[ -e "$path" ]]; then
    cp --archive "$path" "$ROLLBACK/"
  fi
done

restore_previous() {
  local group name path
  systemctl disable --now jobseek-crawler-reconciliation.timer >/dev/null 2>&1 || true
  for path in "${FILES[@]}"; do
    name="${path##*/}"
    if [[ -e "$ROLLBACK/$name" ]]; then
      group=root
      if [[ "$path" == "$STATE_ROOT/"* ]]; then
        group=deploy
      fi
      install -o root -g "$group" -m "$(stat -c '%a' "$ROLLBACK/$name")" \
        "$ROLLBACK/$name" "$path"
    else
      rm -f "$path"
    fi
  done
  systemctl daemon-reload
  if (( TIMER_WAS_ENABLED )); then
    systemctl enable jobseek-crawler-reconciliation.timer >/dev/null 2>&1 || true
  fi
  if (( TIMER_WAS_ACTIVE )); then
    systemctl start jobseek-crawler-reconciliation.timer >/dev/null 2>&1 || true
  fi
}

cleanup() {
  status=$?
  trap - EXIT
  if (( status != 0 && ROLLBACK_ARMED )); then
    restore_previous
  fi
  rm -rf "$ROLLBACK"
  exit "$status"
}
trap cleanup EXIT

install -o root -g root -m 0755 \
  "$REPO_ROOT/deploy/reconciliation/state.py" \
  /usr/local/sbin/jobseek-reconciliation-state
install -o root -g root -m 0755 \
  "$REPO_ROOT/deploy/reconciliation/run.sh" \
  /usr/local/sbin/jobseek-crawler-reconciliation
install -o root -g root -m 0644 \
  "$REPO_ROOT/deploy/systemd/jobseek-crawler-reconciliation.service" \
  /etc/systemd/system/jobseek-crawler-reconciliation.service
install -o root -g root -m 0644 \
  "$REPO_ROOT/deploy/systemd/jobseek-crawler-reconciliation.timer" \
  /etc/systemd/system/jobseek-crawler-reconciliation.timer

systemctl daemon-reload
systemd-analyze verify \
  /etc/systemd/system/jobseek-crawler-reconciliation.service \
  /etc/systemd/system/jobseek-crawler-reconciliation.timer
systemctl enable --now jobseek-crawler-reconciliation.timer
systemctl is-active --quiet jobseek-crawler-reconciliation.timer

[[ -n "$DEPLOY_SHA" ]] || {
  echo "ERROR: reconciliation deployment SHA is required" >&2
  exit 1
}
[[ "$WRAPPER_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "ERROR: reconciliation wrapper digest is invalid" >&2
  exit 1
}
/usr/local/sbin/jobseek-reconciliation-state install \
  --revision "$DEPLOY_SHA" \
  --wrapper-sha256 "$WRAPPER_SHA256"
runuser -u deploy -- /usr/local/sbin/jobseek-reconciliation-state \
  check \
  --expected-revision "$DEPLOY_SHA" \
  --expected-wrapper-sha256 "$WRAPPER_SHA256"

ROLLBACK_ARMED=0
