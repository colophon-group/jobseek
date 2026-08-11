#!/usr/bin/env bash
# Install explicit journal budgets and retire exact obsolete scheduler units.
set -euo pipefail

ROLE="${1:-}"
case "$ROLE" in
  crawler|postgresql|typesense) ;;
  *)
    echo "Usage: $0 <crawler|postgresql|typesense>" >&2
    exit 2
    ;;
esac

[[ "$(id -u)" -eq 0 ]] || {
  echo "ERROR: install-host.sh must run as root" >&2
  exit 1
}
for command in flock install journalctl python3 systemctl systemd-analyze; do
  command -v "$command" >/dev/null || {
    echo "ERROR: required command is unavailable: $command" >&2
    exit 1
  }
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_ROOT=/var/lib/jobseek-host-hygiene
ROLLBACK_ROOT="${STATE_ROOT}/rollback"
RETIRED_ROOT="${STATE_ROOT}/retired-units"
POLICY_DIR=/etc/systemd/journald.conf.d
POLICY_PATH="${POLICY_DIR}/60-jobseek-retention.conf"
VERIFIER=/usr/local/sbin/jobseek-host-hygiene
DEPLOY_SHA="${JOBSEEK_HOST_HYGIENE_DEPLOY_SHA:-manual}"
LOCK_TIMEOUT_S="${JOBSEEK_HOST_HYGIENE_LOCK_TIMEOUT_S:-120}"
CANONICAL_TIMER=jobseek-crawler-reconciliation.timer
RETIRED_UNITS=(
  jobseek-reconciliation-typesense-catchup.service
  jobseek-reconciliation-typesense-catchup.timer
)
ROLLBACK_ARMED=0
ROLLBACK_PATH=""

[[ "$DEPLOY_SHA" == manual || "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ERROR: deployment SHA must be a full lowercase Git revision" >&2
  exit 1
}
[[ "$LOCK_TIMEOUT_S" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: deployment lock timeout must be a positive integer" >&2
  exit 1
}

install -d -o root -g root -m 0700 "$STATE_ROOT" "$ROLLBACK_ROOT" "$RETIRED_ROOT"
exec 9>"${STATE_ROOT}/deploy.lock"
flock -w "$LOCK_TIMEOUT_S" 9 || {
  echo "ERROR: another host-hygiene deployment holds the lock" >&2
  exit 1
}

snapshot_previous() {
  local path stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ).$$"
  path="${ROLLBACK_ROOT}/${stamp}"
  install -d -o root -g root -m 0700 "$path"
  if [[ -f "$POLICY_PATH" ]]; then
    cp --archive "$POLICY_PATH" "$path/policy.conf"
  else
    : >"$path/policy.absent"
  fi
  if [[ -f "$VERIFIER" ]]; then
    cp --archive "$VERIFIER" "$path/jobseek-host-hygiene"
  else
    : >"$path/verifier.absent"
  fi
  printf '%s\n' "$path"
}

restore_previous() {
  local path="$1"
  set +e
  if [[ -f "$path/policy.conf" ]]; then
    rm -f "$POLICY_PATH"
    cp --archive "$path/policy.conf" "$POLICY_PATH"
  else
    rm -f "$POLICY_PATH"
  fi
  if [[ -f "$path/jobseek-host-hygiene" ]]; then
    rm -f "$VERIFIER"
    cp --archive "$path/jobseek-host-hygiene" "$VERIFIER"
  else
    rm -f "$VERIFIER"
  fi
  systemctl restart systemd-journald.service >/dev/null 2>&1 || true
}

rollback_on_exit() {
  local status=$?
  trap - EXIT
  if [[ "$ROLLBACK_ARMED" -eq 1 && "$status" -ne 0 ]]; then
    restore_previous "$ROLLBACK_PATH"
  fi
  exit "$status"
}

install_journal_policy() {
  local source="${REPO_ROOT}/deploy/host-hygiene/journald/${ROLE}.conf"
  local changed=1
  install -d -o root -g root -m 0755 "$POLICY_DIR" /var/log/journal
  if [[ -f "$POLICY_PATH" ]] && cmp --silent "$source" "$POLICY_PATH"; then
    changed=0
  fi
  install -o root -g root -m 0644 "$source" "$POLICY_PATH"
  install -o root -g root -m 0755 \
    "${REPO_ROOT}/scripts/jobseek-host-hygiene.py" "$VERIFIER"
  if [[ "$changed" -eq 1 ]]; then
    systemctl restart systemd-journald.service
  fi
  systemctl is-active --quiet systemd-journald.service
  journalctl --verify --quiet
  "$VERIFIER" verify-journal --role "$ROLE"
}

canonical_timer_is_healthy() {
  systemctl is-enabled --quiet "$CANONICAL_TIMER" &&
    systemctl is-active --quiet "$CANONICAL_TIMER"
}

archive_exact_unit_file() {
  local destination source unit="$1"
  source="/etc/systemd/system/${unit}"
  if [[ -e "$source" || -L "$source" ]]; then
    destination="${RETIRED_ROOT}/${DEPLOY_SHA}"
    install -d -o root -g root -m 0700 "$destination"
    if [[ ! -e "${destination}/${unit}" && ! -L "${destination}/${unit}" ]]; then
      cp --archive --no-dereference "$source" "${destination}/${unit}"
    fi
    rm -f -- "$source"
  fi
}

retire_obsolete_reconciliation() {
  local unit
  canonical_timer_is_healthy || {
    echo "ERROR: canonical reconciliation timer is not enabled and active" >&2
    return 1
  }

  for unit in "${RETIRED_UNITS[@]}"; do
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
    archive_exact_unit_file "$unit"
    ln -s /dev/null "/etc/systemd/system/${unit}"
  done
  systemctl daemon-reload
  systemctl reset-failed "${RETIRED_UNITS[@]}" >/dev/null 2>&1 || true

  canonical_timer_is_healthy || {
    echo "ERROR: canonical reconciliation timer changed during retirement" >&2
    return 1
  }
  for unit in "${RETIRED_UNITS[@]}"; do
    if ! "$VERIFIER" verify-retired-unit --unit "$unit"; then
      echo "ERROR: retired reconciliation unit is not safely masked: $unit" >&2
      return 1
    fi
  done
}

ROLLBACK_PATH="$(snapshot_previous)"
ROLLBACK_ARMED=1
trap rollback_on_exit EXIT
install_journal_policy
if [[ "$ROLE" == crawler ]]; then
  retire_obsolete_reconciliation
fi

printf '%s\n' "$DEPLOY_SHA" >"${STATE_ROOT}/deployed-sha.tmp"
chmod 0600 "${STATE_ROOT}/deployed-sha.tmp"
mv "${STATE_ROOT}/deployed-sha.tmp" "${STATE_ROOT}/deployed-sha"
ROLLBACK_ARMED=0
trap - EXIT
echo "Installed host hygiene baseline; role=${ROLE} revision=${DEPLOY_SHA}"
