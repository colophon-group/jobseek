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
typesense_key_changed=0
typesense_previous_env=""
typesense_rotation_pending=0

rollback_typesense_credential() {
  if [[ "$typesense_rotation_pending" -ne 1 || -z "$typesense_previous_env" ]]; then
    return
  fi
  flock -w "$LOCK_TIMEOUT_S" 9 || return
  install -o root -g root -m 0600 \
    "$typesense_previous_env" /etc/jobseek-backup/typesense.env
  flock -u 9
}

cleanup() {
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    rollback_typesense_credential || true
  fi
  if [[ -n "$typesense_previous_env" ]]; then
    rm -f -- "$typesense_previous_env"
  fi
  exit "$status"
}
trap cleanup EXIT

install -d -m 0700 /etc/jobseek-backup
install -d -m 0700 /var/lib/jobseek-backup/status

if [[ "$SERVICE" == "postgresql" ]]; then
  install -o root -g root -m 0755 \
    "$REPO_ROOT/scripts/jobseek-data-backup.py" \
    /usr/local/sbin/jobseek-data-backup
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
  : "${JOBSEEK_TYPESENSE_BACKUP_KEY:?JOBSEEK_TYPESENSE_BACKUP_KEY is required}"
  export JOBSEEK_TYPESENSE_BACKUP_KEY
  python3 - <<'PY'
import json
import os
import urllib.error
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:8108/stats.json",
    headers={"X-TYPESENSE-API-KEY": os.environ["JOBSEEK_TYPESENSE_BACKUP_KEY"]},
)
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
except urllib.error.HTTPError as exc:
    raise SystemExit(
        f"ERROR: Typesense backup credential authorization returned HTTP {exc.code}"
    ) from exc
if response.status != 200 or not isinstance(payload, dict):
    raise SystemExit("ERROR: Typesense backup credential authorization probe failed")
PY
  install -o root -g root -m 0755 \
    "$REPO_ROOT/scripts/jobseek-data-backup.py" \
    /usr/local/sbin/jobseek-data-backup
  typesense_previous_env="$(mktemp /run/jobseek-typesense-backup-env.rollback.XXXXXX)"
  chmod 0600 "$typesense_previous_env"
  cp --preserve=mode,ownership \
    /etc/jobseek-backup/typesense.env "$typesense_previous_env"
  typesense_key_changed="$(python3 - <<'PY'
import os
from pathlib import Path

path = Path("/etc/jobseek-backup/typesense.env")
key = os.environ["JOBSEEK_TYPESENSE_BACKUP_KEY"]
if not key or any(character.isspace() for character in key):
    raise SystemExit("ERROR: Typesense backup key must be a non-empty single token")
lines = path.read_text(encoding="utf-8").splitlines()
matches = [
    index for index, line in enumerate(lines) if line.startswith("TYPESENSE_API_KEY=")
]
if len(matches) != 1:
    raise SystemExit("ERROR: Typesense backup environment must contain one API key")
current_key = lines[matches[0]].split("=", 1)[1]
if current_key == key:
    print(0)
    raise SystemExit(0)
lines[matches[0]] = f"TYPESENSE_API_KEY={key}"
temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    stream.write("\n".join(lines) + "\n")
os.chown(temporary, 0, 0)
os.chmod(temporary, 0o600)
os.replace(temporary, path)
print(1)
PY
  )"
  if [[ "$typesense_key_changed" -eq 1 ]]; then
    typesense_rotation_pending=1
  fi
  if ! command -v restic >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends restic
  fi
  install -d -m 0700 /var/lib/jobseek-backup/typesense/staging
  install -o root -g root -m 0755 \
    "$REPO_ROOT/deploy/backups/typesense/restore-drill.sh" \
    /usr/local/sbin/jobseek-typesense-restore-drill
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
flock -u 9

if [[ "$SERVICE" == "typesense" && "$typesense_key_changed" -eq 1 ]]; then
  smoke_started="$(date +%s)"
  systemctl reset-failed jobseek-typesense-backup.service
  systemctl start jobseek-typesense-backup.service
  BACKUP_SMOKE_STARTED="$smoke_started" python3 - <<'PY'
import json
import os
from pathlib import Path

status = json.loads(
    Path("/var/lib/jobseek-backup/status/typesense.json").read_text(encoding="utf-8")
)
started = int(os.environ["BACKUP_SMOKE_STARTED"])
if (
    status.get("success") is not True
    or int(status.get("attempt_unix") or 0) < started
    or int(status.get("last_success_unix") or 0) < started
):
    raise SystemExit("ERROR: Typesense credential-change backup smoke did not succeed")
PY
fi

if [[ "$TIMER_ACTION" == "start" ]]; then
  systemctl enable --now "jobseek-${SERVICE}-backup.timer"
elif [[ "$TIMER_ACTION" == "disable" ]]; then
  systemctl stop "jobseek-${SERVICE}-backup.timer" >/dev/null 2>&1 || true
  systemctl disable "jobseek-${SERVICE}-backup.timer" >/dev/null 2>&1 || true
fi
if [[ "$TIMER_ACTION" != "disable" ]]; then
  systemctl is-enabled --quiet "jobseek-${SERVICE}-backup.timer"
  systemctl is-active --quiet "jobseek-${SERVICE}-backup.timer"
  if systemctl is-failed --quiet "jobseek-${SERVICE}-backup.service"; then
    echo "ERROR: jobseek-${SERVICE}-backup.service is failed" >&2
    exit 1
  fi
fi
if [[ "$SERVICE" == "typesense" ]]; then
  python3 - <<'PY'
import json
import time
from pathlib import Path

status = json.loads(
    Path("/var/lib/jobseek-backup/status/typesense.json").read_text(encoding="utf-8")
)
age = time.time() - int(status.get("last_success_unix") or 0)
if status.get("success") is not True or age < 0 or age > 36 * 60 * 60:
    raise SystemExit("ERROR: Typesense backup evidence is failed or stale")
PY
fi
if [[ -n "${JOBSEEK_BACKUP_DEPLOY_SHA:-}" ]]; then
  [[ "$JOBSEEK_BACKUP_DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]
  printf '%s\n' "$JOBSEEK_BACKUP_DEPLOY_SHA" >/var/lib/jobseek-backup/deployed-sha.tmp
  chmod 0644 /var/lib/jobseek-backup/deployed-sha.tmp
  mv /var/lib/jobseek-backup/deployed-sha.tmp /var/lib/jobseek-backup/deployed-sha
fi
typesense_rotation_pending=0
echo "Installed ${SERVICE} backup automation; timer_action=${TIMER_ACTION}"
