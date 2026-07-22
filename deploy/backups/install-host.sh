#!/usr/bin/env bash
# Install repo-owned backup code on an already credentialed Hetzner host.
set -euo pipefail

usage() {
  echo "Usage: $0 [--start-timer|--disable-timer] <postgresql|typesense>" >&2
}

TIMER_ACTION=preserve
if [[ "${1:-}" == "--start-timer" ]]; then
  TIMER_ACTION=start
  shift
elif [[ "${1:-}" == "--disable-timer" ]]; then
  TIMER_ACTION=disable
  shift
fi
SERVICE="${1:-}"
if [[ "$SERVICE" != "postgresql" && "$SERVICE" != "typesense" ]]; then
  usage
  exit 2
fi

LOCK_TIMEOUT_S="${JOBSEEK_BACKUP_DEPLOY_LOCK_TIMEOUT_S:-60}"
exec 9>"/run/jobseek-data-backup-${SERVICE}.lock"
if ! flock -w "$LOCK_TIMEOUT_S" 9; then
  echo "Backup is active; could not acquire deployment lock within ${LOCK_TIMEOUT_S}s" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
install -d -m 0700 /etc/jobseek-backup
install -d -m 0700 /var/lib/jobseek-backup/status
install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/jobseek-data-backup.py" \
  /usr/local/sbin/jobseek-data-backup

if [[ "$SERVICE" == "postgresql" ]]; then
  test -s /etc/jobseek-backup/postgresql/pgbackrest.conf
  test -s /etc/jobseek-backup/postgresql/repository.env
  test -s /etc/jobseek-backup/postgresql/storage-box.cifs
  if ! command -v mount.cifs >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cifs-utils
  fi
  docker build \
    --pull=false \
    --tag jobseek-postgres:16-pgbackrest \
    "$REPO_ROOT/deploy/backups/postgresql"
  install -o root -g root -m 0755 \
    "$REPO_ROOT/deploy/backups/postgresql/migrate-container.sh" \
    /usr/local/sbin/jobseek-postgresql-enable-pgbackrest
  install -o root -g root -m 0755 \
    "$REPO_ROOT/deploy/backups/postgresql/smoke-repository.sh" \
    /usr/local/sbin/jobseek-postgresql-smoke-pgbackrest
  install -o root -g root -m 0755 \
    "$REPO_ROOT/deploy/backups/postgresql/mount-repository.sh" \
    /usr/local/sbin/jobseek-postgresql-mount-backup-repository
  install -o root -g root -m 0755 \
    "$REPO_ROOT/deploy/backups/postgresql/restore-drill.sh" \
    /usr/local/sbin/jobseek-postgresql-restore-drill
  install -o root -g root -m 0644 \
    "$REPO_ROOT/deploy/systemd/jobseek-postgresql-backup-repository.service" \
    /etc/systemd/system/jobseek-postgresql-backup-repository.service
else
  test -s /etc/jobseek-backup/typesense.env
  test -s /etc/jobseek-backup/typesense/id_ed25519
  if ! command -v restic >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends restic
  fi
  install -d -m 0700 /var/lib/jobseek-backup/typesense/staging
fi

install -o root -g root -m 0644 \
  "$REPO_ROOT/deploy/systemd/jobseek-${SERVICE}-backup.service" \
  "/etc/systemd/system/jobseek-${SERVICE}-backup.service"
install -o root -g root -m 0644 \
  "$REPO_ROOT/deploy/systemd/jobseek-${SERVICE}-backup.timer" \
  "/etc/systemd/system/jobseek-${SERVICE}-backup.timer"
systemctl daemon-reload
if [[ "$SERVICE" == "postgresql" ]]; then
  systemd-analyze verify /etc/systemd/system/jobseek-postgresql-backup-repository.service
  /usr/local/sbin/jobseek-postgresql-mount-backup-repository
  systemctl enable --now jobseek-postgresql-backup-repository.service
  systemctl is-active --quiet jobseek-postgresql-backup-repository.service
fi
systemd-analyze verify "/etc/systemd/system/jobseek-${SERVICE}-backup.service"
systemd-analyze verify "/etc/systemd/system/jobseek-${SERVICE}-backup.timer"
if [[ -n "${JOBSEEK_BACKUP_DEPLOY_SHA:-}" ]]; then
  [[ "$JOBSEEK_BACKUP_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]
  printf '%s\n' "$JOBSEEK_BACKUP_DEPLOY_SHA" >/var/lib/jobseek-backup/deployed-sha.tmp
  chmod 0644 /var/lib/jobseek-backup/deployed-sha.tmp
  mv /var/lib/jobseek-backup/deployed-sha.tmp /var/lib/jobseek-backup/deployed-sha
fi
flock -u 9

if [[ "$TIMER_ACTION" == "start" ]]; then
  systemctl enable --now "jobseek-${SERVICE}-backup.timer"
elif [[ "$TIMER_ACTION" == "disable" ]]; then
  systemctl stop "jobseek-${SERVICE}-backup.timer" >/dev/null 2>&1 || true
  systemctl disable "jobseek-${SERVICE}-backup.timer" >/dev/null 2>&1 || true
fi
echo "Installed ${SERVICE} backup automation; timer_action=${TIMER_ACTION}"
