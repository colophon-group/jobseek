#!/usr/bin/env bash
# Install repo-owned backup code on an already credentialed Hetzner host.
set -euo pipefail

usage() {
  echo "Usage: $0 [--start-timer|--disable-timer] <postgresql|typesense|web-postgresql>" >&2
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
if [[ "$SERVICE" != "postgresql" && "$SERVICE" != "typesense" && "$SERVICE" != "web-postgresql" ]]; then
  usage
  exit 2
fi

LOCK_TIMEOUT_S="${JOBSEEK_BACKUP_DEPLOY_LOCK_TIMEOUT_S:-60}"
exec 8>/run/jobseek-backup-deployment.lock
if ! flock -w "$LOCK_TIMEOUT_S" 8; then
  echo "Another backup deployment or protected operation is active" >&2
  exit 1
fi
exec 9>"/run/jobseek-data-backup-${SERVICE}.lock"
if ! flock -w "$LOCK_TIMEOUT_S" 9; then
  echo "Backup is active; could not acquire service data lock within ${LOCK_TIMEOUT_S}s" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
typesense_key_changed=0
typesense_previous_env=""
typesense_rotation_pending=0
web_candidate_root=""
web_configuration_changed=0
web_timer_was_enabled=0
web_timer_was_active=0
web_timer_quiesced=0
web_timer_enabled_state=""
web_timer_active_state=""
web_candidate_commit_started=0
web_candidate_commit_complete=0

rollback_typesense_credential() {
  if [[ "$typesense_rotation_pending" -ne 1 || -z "$typesense_previous_env" ]]; then
    return
  fi
  flock -w "$LOCK_TIMEOUT_S" 9 || return
  install -o root -g root -m 0600 \
    "$typesense_previous_env" /etc/jobseek-backup/typesense.env
  flock -u 9
}

read_web_timer_state() {
  web_timer_enabled_state=""
  web_timer_active_state=""
  if web_timer_enabled_state="$(systemctl is-enabled jobseek-web-postgresql-backup.timer 2>/dev/null)"; then
    :
  elif [[ "$web_timer_enabled_state" != disabled ]]; then
    echo "ERROR: exact web backup timer enabled state is unavailable" >&2
    return 1
  fi
  if web_timer_active_state="$(systemctl is-active jobseek-web-postgresql-backup.timer 2>/dev/null)"; then
    :
  elif [[ "$web_timer_active_state" != inactive ]]; then
    echo "ERROR: exact web backup timer active state is unavailable" >&2
    return 1
  fi
  [[ "$web_timer_enabled_state" =~ ^(enabled|disabled)$ ]]
  [[ "$web_timer_active_state" =~ ^(active|inactive)$ ]]
}

web_timer_is_disabled_inactive() {
  read_web_timer_state && \
    [[ "$web_timer_enabled_state" == disabled && "$web_timer_active_state" == inactive ]]
}

disable_web_timer_fail_safe() {
  local failed=0
  if ! systemctl disable --now jobseek-web-postgresql-backup.timer; then
    failed=1
  fi
  if ! systemctl stop jobseek-web-postgresql-backup.service; then
    failed=1
  fi
  if ! web_timer_is_disabled_inactive; then
    failed=1
  fi
  if [[ "$failed" -ne 0 ]]; then
    echo "ERROR: web backup timer disabled/inactive rollback state could not be proven" >&2
    return 1
  fi
}

restore_web_timer_state() {
  local failed=0
  local expected_enabled=disabled
  local expected_active=inactive
  if [[ "$web_timer_quiesced" -ne 1 ]]; then
    return
  fi
  if ! systemctl reset-failed jobseek-web-postgresql-backup.service; then
    failed=1
  fi
  if [[ "$web_timer_was_enabled" -eq 1 ]]; then
    expected_enabled=enabled
    if ! systemctl enable jobseek-web-postgresql-backup.timer; then
      failed=1
    fi
  fi
  if [[ "$web_timer_was_active" -eq 1 ]]; then
    expected_active=active
    if ! systemctl start jobseek-web-postgresql-backup.timer; then
      failed=1
    fi
  fi
  if ! read_web_timer_state || \
    [[ "$web_timer_enabled_state" != "$expected_enabled" || \
      "$web_timer_active_state" != "$expected_active" ]]; then
    failed=1
  fi
  if [[ "$failed" -ne 0 ]]; then
    echo "ERROR: prior web backup timer state could not be restored" >&2
    if ! disable_web_timer_fail_safe; then
      echo "ERROR: web backup timer rollback failed hard" >&2
    fi
    return 1
  fi
  web_timer_quiesced=0
}

commit_web_candidate() {
  web_candidate_commit_started=1
  mv "$web_candidate_root/web-postgresql.env" \
    /etc/jobseek-backup/web-postgresql.env
  mv "$web_candidate_root/web-database-url" \
    /etc/jobseek-backup/web-postgresql.database-url
  test "$(stat -c '%U:%G:%a' /etc/jobseek-backup/web-postgresql.env)" = root:root:600
  test "$(stat -c '%U:%G:%a' /etc/jobseek-backup/web-postgresql.database-url)" = root:root:600
  web_candidate_commit_complete=1
}

cleanup() {
  local status=$?
  local cleanup_failed=0
  if [[ "$status" -ne 0 ]]; then
    rollback_typesense_credential || true
    if [[ "$web_candidate_commit_started" -eq 1 && \
      "$web_candidate_commit_complete" -ne 1 ]]; then
      echo "ERROR: web candidate commit is incomplete; timer will remain disabled" >&2
      if ! disable_web_timer_fail_safe; then
        cleanup_failed=1
      fi
      web_timer_quiesced=0
    elif ! restore_web_timer_state; then
      cleanup_failed=1
    fi
  fi
  if [[ -n "$typesense_previous_env" ]]; then
    rm -f -- "$typesense_previous_env"
  fi
  if [[ -n "$web_candidate_root" ]]; then
    rm -rf -- "$web_candidate_root"
  fi
  if [[ "$cleanup_failed" -ne 0 ]]; then
    exit 1
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
  python3 "$REPO_ROOT/deploy/backups/postgresql/configure-retention.py" \
    /etc/jobseek-backup/postgresql/pgbackrest.conf
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
  install -o root -g root -m 0755 \
    "$REPO_ROOT/deploy/backups/postgresql/emergency-headroom.sh" \
    /usr/local/sbin/jobseek-postgresql-emergency-headroom
  install -o root -g root -m 0644 \
    "$REPO_ROOT/deploy/systemd/jobseek-postgresql-backup-repository.service" \
    /etc/systemd/system/jobseek-postgresql-backup-repository.service
  install -o root -g root -m 0644 \
    "$REPO_ROOT/deploy/systemd/jobseek-postgresql-emergency-headroom.service" \
    /etc/systemd/system/jobseek-postgresql-emergency-headroom.service
elif [[ "$SERVICE" == "typesense" ]]; then
  test -s /etc/jobseek-backup/typesense.env
  test -s /etc/jobseek-backup/typesense/id_ed25519
  : "${JOBSEEK_TYPESENSE_BACKUP_KEY_FILE:?JOBSEEK_TYPESENSE_BACKUP_KEY_FILE is required}"
  test ! -L "$JOBSEEK_TYPESENSE_BACKUP_KEY_FILE"
  test "$(stat -c '%U:%G:%a' "$JOBSEEK_TYPESENSE_BACKUP_KEY_FILE")" = root:root:600
  export JOBSEEK_TYPESENSE_BACKUP_KEY_FILE
  python3 - <<'PY'
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

request = urllib.request.Request(
    "http://127.0.0.1:8108/stats.json",
    headers={
        "X-TYPESENSE-API-KEY": Path(
            os.environ["JOBSEEK_TYPESENSE_BACKUP_KEY_FILE"]
        ).read_text(encoding="utf-8")
    },
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
key = Path(os.environ["JOBSEEK_TYPESENSE_BACKUP_KEY_FILE"]).read_text(encoding="utf-8")
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
else
  test -s /etc/jobseek-backup/typesense.env
  test -s /etc/jobseek-backup/typesense/id_ed25519
  shopt -s nullglob
  stale_web_candidates=(/etc/jobseek-backup/.web-postgresql-candidate.*)
  shopt -u nullglob
  for stale_candidate in "${stale_web_candidates[@]}"; do
    stale_name="${stale_candidate##*/}"
    [[ "$stale_name" =~ ^\.web-postgresql-candidate\.[A-Za-z0-9]{6}$ ]]
    [[ -d "$stale_candidate" && ! -L "$stale_candidate" ]]
    [[ "$(stat -c '%U:%G:%a' "$stale_candidate")" == root:root:700 ]]
    rm -rf -- "$stale_candidate"
    [[ ! -e "$stale_candidate" && ! -L "$stale_candidate" ]]
  done
  : "${JOBSEEK_WEB_DATABASE_URL_FILE:?JOBSEEK_WEB_DATABASE_URL_FILE is required}"
  test ! -L "$JOBSEEK_WEB_DATABASE_URL_FILE"
  test "$(stat -c '%U:%G:%a' "$JOBSEEK_WEB_DATABASE_URL_FILE")" = root:root:600
  web_candidate_root="$(mktemp -d /etc/jobseek-backup/.web-postgresql-candidate.XXXXXX)"
  chown root:root "$web_candidate_root"
  chmod 0700 "$web_candidate_root"
  test ! -L /etc/jobseek-backup/web-postgresql.env
  test ! -L /etc/jobseek-backup/web-postgresql.database-url
  if [[ -e /etc/jobseek-backup/web-postgresql.env ]]; then
    test "$(stat -c '%U:%G:%a' /etc/jobseek-backup/web-postgresql.env)" = root:root:600
  fi
  if [[ -e /etc/jobseek-backup/web-postgresql.database-url ]]; then
    test "$(stat -c '%U:%G:%a' /etc/jobseek-backup/web-postgresql.database-url)" = root:root:600
  fi
  export JOBSEEK_WEB_DATABASE_URL_FILE web_candidate_root
  web_configuration_changed="$(python3 - <<'PY'
import os
from pathlib import Path

source = Path("/etc/jobseek-backup/typesense.env")
live_environment = Path("/etc/jobseek-backup/web-postgresql.env")
live_credential = Path("/etc/jobseek-backup/web-postgresql.database-url")
candidate_root = Path(os.environ["web_candidate_root"])
candidate_environment = candidate_root / "web-postgresql.env"
candidate_credential = candidate_root / "web-database-url"
allowed = {"RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE", "RESTIC_SFTP_COMMAND"}
selected = {}
for line in source.read_text(encoding="utf-8").splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key = line.split("=", 1)[0]
    if key in allowed:
        if key in selected:
            raise SystemExit(f"ERROR: duplicate {key} in Typesense backup environment")
        selected[key] = line
missing = sorted(allowed - set(selected))
if missing:
    raise SystemExit(f"ERROR: missing Restic settings: {', '.join(missing)}")

url = Path(os.environ["JOBSEEK_WEB_DATABASE_URL_FILE"]).read_text(encoding="utf-8")
if not url.startswith(("postgres://", "postgresql://")) or any(
    character in url for character in "\r\n"
):
    raise SystemExit("ERROR: web database URL must be one PostgreSQL URI")
canonical_url = url + "\n"
canonical_environment = "\n".join(selected[key] for key in sorted(selected)) + "\n"
current_url = live_credential.read_text(encoding="utf-8") if live_credential.exists() else None
current_environment = (
    live_environment.read_text(encoding="utf-8") if live_environment.exists() else None
)

def atomic_write(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(value)
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)

atomic_write(candidate_environment, canonical_environment)
atomic_write(candidate_credential, canonical_url)
print(
    0
    if current_url == canonical_url and current_environment == canonical_environment
    else 1
)
PY
  )"
  install -o root -g root -m 0755 \
    "$REPO_ROOT/scripts/jobseek-data-backup.py" \
    /usr/local/sbin/jobseek-data-backup
  if ! command -v restic >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends restic
  fi
  docker pull \
    postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193
  install -o root -g root -m 0755 \
    "$REPO_ROOT/deploy/backups/web-postgresql/restore-drill.sh" \
    /usr/local/sbin/jobseek-web-postgresql-restore-drill
  install -o root -g root -m 0755 \
    "$REPO_ROOT/deploy/backups/web-postgresql/operations.py" \
    /usr/local/sbin/jobseek-web-postgresql-operations
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
  systemd-analyze verify /etc/systemd/system/jobseek-postgresql-emergency-headroom.service
  /usr/local/sbin/jobseek-postgresql-mount-backup-repository
  systemctl enable --now jobseek-postgresql-backup-repository.service
  systemctl is-active --quiet jobseek-postgresql-backup-repository.service
  systemctl enable jobseek-postgresql-emergency-headroom.service
  systemctl restart jobseek-postgresql-emergency-headroom.service
  systemctl is-active --quiet jobseek-postgresql-emergency-headroom.service
fi
systemd-analyze verify "/etc/systemd/system/jobseek-${SERVICE}-backup.service"
systemd-analyze verify "/etc/systemd/system/jobseek-${SERVICE}-backup.timer"

if [[ "$SERVICE" == "typesense" && "$typesense_key_changed" -eq 1 ]]; then
  flock -u 9
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
  if ! flock -w "$LOCK_TIMEOUT_S" 9; then
    echo "ERROR: could not reacquire Typesense service data lock after smoke" >&2
    exit 1
  fi
elif [[ "$SERVICE" == "web-postgresql" ]]; then
  read_web_timer_state
  if [[ "$web_timer_enabled_state" == enabled ]]; then
    web_timer_was_enabled=1
  fi
  if [[ "$web_timer_active_state" == active ]]; then
    web_timer_was_active=1
  fi
  if [[ "$web_configuration_changed" -eq 1 ]] && \
    [[ "$web_timer_was_enabled" -eq 1 || "$web_timer_was_active" -eq 1 ]]; then
    web_timer_quiesced=1
    if ! disable_web_timer_fail_safe; then
      exit 1
    fi
    flock -u 9
    smoke_started="$(date +%s)"
    (
      set -a
      # shellcheck disable=SC1091
      source "$web_candidate_root/web-postgresql.env"
      set +a
      unset WEB_DATABASE_URL
      export CREDENTIALS_DIRECTORY="$web_candidate_root"
      export BACKUP_STATUS_DIR=/var/lib/jobseek-backup/status
      export WEB_POSTGRES_STAGING_ROOT=/run/jobseek-backup/web-postgresql
      export WEB_POSTGRES_IMAGE=postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193
      /usr/local/sbin/jobseek-data-backup web-postgresql
    )
    BACKUP_SMOKE_STARTED="$smoke_started" python3 - <<'PY'
import json
import os
from pathlib import Path

status = json.loads(
    Path("/var/lib/jobseek-backup/status/web-postgresql.json").read_text(encoding="utf-8")
)
started = int(os.environ["BACKUP_SMOKE_STARTED"])
if (
    status.get("service") != "web-postgresql"
    or status.get("success") is not True
    or int(status.get("attempt_unix") or 0) < started
    or int(status.get("last_success_unix") or 0) < started
):
    raise SystemExit("ERROR: web database credential-change backup smoke did not succeed")
PY
    if ! flock -w "$LOCK_TIMEOUT_S" 9; then
      echo "ERROR: could not reacquire web PostgreSQL service data lock after smoke" >&2
      exit 1
    fi
  fi
  if [[ "$web_configuration_changed" -eq 1 ]]; then
    commit_web_candidate
    if ! restore_web_timer_state; then
      exit 1
    fi
  fi
fi

if [[ "$TIMER_ACTION" == "start" ]]; then
  systemctl enable --now "jobseek-${SERVICE}-backup.timer"
elif [[ "$TIMER_ACTION" == "disable" ]]; then
  systemctl stop "jobseek-${SERVICE}-backup.timer" >/dev/null 2>&1 || true
  systemctl disable "jobseek-${SERVICE}-backup.timer" >/dev/null 2>&1 || true
fi
if [[ "$TIMER_ACTION" != "disable" ]] && \
  systemctl is-enabled --quiet "jobseek-${SERVICE}-backup.timer"; then
  systemctl is-active --quiet "jobseek-${SERVICE}-backup.timer"
  if systemctl is-failed --quiet "jobseek-${SERVICE}-backup.service"; then
    echo "ERROR: jobseek-${SERVICE}-backup.service is failed" >&2
    exit 1
  fi
fi
if [[ "$SERVICE" == "typesense" ]] && \
  systemctl is-enabled --quiet jobseek-typesense-backup.timer; then
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
  printf '%s\n' "$JOBSEEK_BACKUP_DEPLOY_SHA" \
    >"/var/lib/jobseek-backup/${SERVICE}-deployed-sha.tmp"
  chown root:root "/var/lib/jobseek-backup/${SERVICE}-deployed-sha.tmp"
  chmod 0644 "/var/lib/jobseek-backup/${SERVICE}-deployed-sha.tmp"
  mv "/var/lib/jobseek-backup/${SERVICE}-deployed-sha.tmp" \
    "/var/lib/jobseek-backup/${SERVICE}-deployed-sha"
  printf '%s\n' "$JOBSEEK_BACKUP_DEPLOY_SHA" >/var/lib/jobseek-backup/deployed-sha.tmp
  chmod 0644 /var/lib/jobseek-backup/deployed-sha.tmp
  mv /var/lib/jobseek-backup/deployed-sha.tmp /var/lib/jobseek-backup/deployed-sha
fi
typesense_rotation_pending=0
echo "Installed ${SERVICE} backup automation; timer_action=${TIMER_ACTION}"
