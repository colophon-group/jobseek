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
FILES=(
  /usr/local/sbin/jobseek-crawler-reconciliation
  /etc/systemd/system/jobseek-crawler-reconciliation.service
  /etc/systemd/system/jobseek-crawler-reconciliation.timer
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

mkdir -p "$STATE_ROOT"
chmod 0700 "$STATE_ROOT"
ROLLBACK="$(mktemp -d "${STATE_ROOT}/rollback.XXXXXX")"
for path in "${FILES[@]}"; do
  if [[ -e "$path" ]]; then
    cp --archive "$path" "$ROLLBACK/"
  fi
done

restore_previous() {
  local path name
  systemctl disable --now jobseek-crawler-reconciliation.timer >/dev/null 2>&1 || true
  for path in "${FILES[@]}"; do
    name="${path##*/}"
    if [[ -e "$ROLLBACK/$name" ]]; then
      install -o root -g root -m "$(stat -c '%a' "$ROLLBACK/$name")" \
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

if [[ -n "$DEPLOY_SHA" ]]; then
  [[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || {
    echo "ERROR: invalid deployment SHA" >&2
    exit 1
  }
  printf '%s\n' "$DEPLOY_SHA" >"${STATE_ROOT}/deployed-sha.tmp"
  chmod 0644 "${STATE_ROOT}/deployed-sha.tmp"
  mv "${STATE_ROOT}/deployed-sha.tmp" "${STATE_ROOT}/deployed-sha"
fi

ROLLBACK_ARMED=0
