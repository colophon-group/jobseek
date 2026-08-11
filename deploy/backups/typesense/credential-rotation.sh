#!/usr/bin/env bash
# Fail-closed Typesense backup credential rotation helpers.
#
# This file is sourced by deploy/backups/install-host.sh.  The caller must hold
# both the host-wide deployment lock and the Typesense service-data lock.  The
# service lock may be released only while the timer/service are proven quiet so
# a candidate credential can run a real backup smoke without changing the live
# environment file.

typesense_rotation_changed=0
typesense_rotation_pending=0
typesense_rotation_candidate_root=""
typesense_rotation_candidate_env=""
typesense_rotation_previous_env=""
typesense_rotation_commit_started=0
typesense_rotation_commit_complete=0
typesense_rotation_service_lock_held=1
typesense_rotation_lock_reacquire_failed=0
typesense_rotation_timer_quiesced=0
typesense_rotation_timer_was_enabled=0
typesense_rotation_timer_was_active=0
typesense_rotation_timer_enabled_state=""
typesense_rotation_timer_active_state=""
typesense_rotation_service_active_state=""

typesense_rotation_test_mode=0
typesense_rotation_force_restore_failure=0
typesense_rotation_live_env=/etc/jobseek-backup/typesense.env
typesense_rotation_status_file=/var/lib/jobseek-backup/status/typesense.json
typesense_rotation_backup_command=/usr/local/sbin/jobseek-data-backup
typesense_rotation_systemctl_command=systemctl
typesense_rotation_flock_command=flock
typesense_rotation_auth_probe_command=""

typesense_rotation_validate_file() {
  local path=$1
  TYPESENSE_ROTATION_VALIDATE_PATH="$path" \
    TYPESENSE_ROTATION_TEST_MODE="$typesense_rotation_test_mode" \
    python3 - <<'PY'
import os
import stat
from pathlib import Path

path = Path(os.environ["TYPESENSE_ROTATION_VALIDATE_PATH"])
try:
    metadata = path.lstat()
except OSError as exc:
    raise SystemExit(f"ERROR: cannot inspect Typesense credential file: {exc}") from exc
if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
    raise SystemExit("ERROR: Typesense credential path must be a regular non-symlink file")
if stat.S_IMODE(metadata.st_mode) != 0o600:
    raise SystemExit("ERROR: Typesense credential file must have mode 0600")
if os.environ["TYPESENSE_ROTATION_TEST_MODE"] != "1" and (
    metadata.st_uid != 0 or metadata.st_gid != 0
):
    raise SystemExit("ERROR: Typesense credential file must be owned by root:root")
PY
}

typesense_rotation_remove_stale_files() {
  local live_directory
  local stale
  local stale_name
  live_directory="$(dirname "$typesense_rotation_live_env")"
  shopt -s nullglob
  for stale in "$live_directory"/.typesense-candidate.*; do
    stale_name="${stale##*/}"
    [[ "$stale_name" =~ ^\.typesense-candidate\.[A-Za-z0-9]{6}$ ]]
    [[ -d "$stale" && ! -L "$stale" ]]
    if [[ "$typesense_rotation_test_mode" != 1 ]]; then
      [[ "$(stat -c '%U:%G:%a' "$stale")" == root:root:700 ]]
    fi
    rm -rf -- "$stale"
    [[ ! -e "$stale" && ! -L "$stale" ]]
  done
  for stale in "$live_directory"/.typesense-env.rollback.*; do
    stale_name="${stale##*/}"
    [[ "$stale_name" =~ ^\.typesense-env\.rollback\.[A-Za-z0-9]{6}$ ]]
    typesense_rotation_validate_file "$stale"
    rm -f -- "$stale"
    [[ ! -e "$stale" && ! -L "$stale" ]]
  done
  shopt -u nullglob
}

typesense_rotation_authorize_candidate() {
  if [[ "$typesense_rotation_test_mode" == 1 && \
    -n "$typesense_rotation_auth_probe_command" ]]; then
    TYPESENSE_ROTATION_KEY_FILE="$JOBSEEK_TYPESENSE_BACKUP_KEY_FILE" \
      "$typesense_rotation_auth_probe_command"
    return
  fi

  JOBSEEK_TYPESENSE_BACKUP_KEY_FILE="$JOBSEEK_TYPESENSE_BACKUP_KEY_FILE" \
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
except urllib.error.URLError as exc:
    raise SystemExit("ERROR: Typesense backup credential authorization probe failed") from exc
if response.status != 200 or not isinstance(payload, dict):
    raise SystemExit("ERROR: Typesense backup credential authorization probe failed")
PY
}

typesense_rotation_prepare() {
  local live_directory
  : "${JOBSEEK_TYPESENSE_BACKUP_KEY_FILE:?JOBSEEK_TYPESENSE_BACKUP_KEY_FILE is required}"
  typesense_rotation_validate_file "$typesense_rotation_live_env"
  typesense_rotation_validate_file "$JOBSEEK_TYPESENSE_BACKUP_KEY_FILE"
  typesense_rotation_remove_stale_files

  live_directory="$(dirname "$typesense_rotation_live_env")"
  typesense_rotation_candidate_root="$(mktemp -d \
    "$live_directory/.typesense-candidate.XXXXXX")"
  chmod 0700 "$typesense_rotation_candidate_root"
  if [[ "$typesense_rotation_test_mode" != 1 ]]; then
    chown root:root "$typesense_rotation_candidate_root"
  fi
  typesense_rotation_candidate_env="$typesense_rotation_candidate_root/typesense.env"
  typesense_rotation_previous_env="$(mktemp \
    "$live_directory/.typesense-env.rollback.XXXXXX")"
  cp -p "$typesense_rotation_live_env" "$typesense_rotation_previous_env"
  chmod 0600 "$typesense_rotation_previous_env"
  if [[ "$typesense_rotation_test_mode" != 1 ]]; then
    chown root:root "$typesense_rotation_previous_env"
  fi
  typesense_rotation_validate_file "$typesense_rotation_previous_env"

  export JOBSEEK_TYPESENSE_BACKUP_KEY_FILE
  typesense_rotation_changed="$(
    TYPESENSE_ROTATION_LIVE_ENV="$typesense_rotation_live_env" \
      TYPESENSE_ROTATION_CANDIDATE_ENV="$typesense_rotation_candidate_env" \
      TYPESENSE_ROTATION_TEST_MODE="$typesense_rotation_test_mode" \
      python3 - <<'PY'
import os
from pathlib import Path

live = Path(os.environ["TYPESENSE_ROTATION_LIVE_ENV"])
candidate = Path(os.environ["TYPESENSE_ROTATION_CANDIDATE_ENV"])
key = Path(os.environ["JOBSEEK_TYPESENSE_BACKUP_KEY_FILE"]).read_text(encoding="utf-8")
if not key or any(character.isspace() for character in key):
    raise SystemExit("ERROR: Typesense backup key must be a non-empty single token")
lines = live.read_text(encoding="utf-8").splitlines()
matches = [
    index for index, line in enumerate(lines) if line.startswith("TYPESENSE_API_KEY=")
]
if len(matches) != 1:
    raise SystemExit("ERROR: Typesense backup environment must contain one API key")
current_key = lines[matches[0]].split("=", 1)[1]
lines[matches[0]] = f"TYPESENSE_API_KEY={key}"
descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    stream.write("\n".join(lines) + "\n")
if os.environ["TYPESENSE_ROTATION_TEST_MODE"] != "1":
    os.chown(candidate, 0, 0)
os.chmod(candidate, 0o600)
print(0 if current_key == key else 1)
PY
  )"
  [[ "$typesense_rotation_changed" =~ ^[01]$ ]]
  typesense_rotation_validate_file "$typesense_rotation_candidate_env"
  typesense_rotation_authorize_candidate
  if [[ "$typesense_rotation_changed" -eq 1 ]]; then
    # Arm cleanup before any timer or live-environment mutation.
    typesense_rotation_pending=1
  fi
}

typesense_rotation_read_service_state() {
  local allow_unloaded=${1:-0}
  typesense_rotation_timer_enabled_state=""
  typesense_rotation_timer_active_state=""
  typesense_rotation_service_active_state=""
  if typesense_rotation_timer_enabled_state="$(
    "$typesense_rotation_systemctl_command" is-enabled \
      jobseek-typesense-backup.timer 2>/dev/null
  )"; then
    :
  elif [[ "$typesense_rotation_timer_enabled_state" != disabled ]]; then
    echo "ERROR: exact Typesense backup timer enabled state is unavailable" >&2
    return 1
  fi
  if typesense_rotation_timer_active_state="$(
    "$typesense_rotation_systemctl_command" is-active \
      jobseek-typesense-backup.timer 2>/dev/null
  )"; then
    :
  elif [[ "$typesense_rotation_timer_active_state" != inactive ]]; then
    echo "ERROR: exact Typesense backup timer active state is unavailable" >&2
    return 1
  fi
  if typesense_rotation_service_active_state="$(
    "$typesense_rotation_systemctl_command" is-active \
      jobseek-typesense-backup.service 2>/dev/null
  )"; then
    :
  elif [[ "$allow_unloaded" == 1 && \
    "$typesense_rotation_service_active_state" == unknown ]]; then
    :
  elif [[ ! "$typesense_rotation_service_active_state" =~ ^(inactive|failed)$ ]]; then
    echo "ERROR: exact Typesense backup service active state is unavailable" >&2
    return 1
  fi
  [[ "$typesense_rotation_timer_enabled_state" =~ ^(enabled|disabled)$ ]]
  [[ "$typesense_rotation_timer_active_state" =~ ^(active|inactive)$ ]]
  if [[ "$allow_unloaded" == 1 ]]; then
    [[ "$typesense_rotation_service_active_state" =~ ^(active|inactive|failed|unknown)$ ]]
  else
    [[ "$typesense_rotation_service_active_state" =~ ^(active|inactive|failed)$ ]]
  fi
}

typesense_rotation_is_disabled_inactive() {
  typesense_rotation_read_service_state && \
    [[ "$typesense_rotation_timer_enabled_state" == disabled ]] && \
    [[ "$typesense_rotation_timer_active_state" == inactive ]] && \
    [[ "$typesense_rotation_service_active_state" != active ]]
}

typesense_rotation_disable_fail_safe() {
  local failed=0
  if ! "$typesense_rotation_systemctl_command" disable --now \
    jobseek-typesense-backup.timer; then
    failed=1
  fi
  if ! "$typesense_rotation_systemctl_command" stop \
    jobseek-typesense-backup.service; then
    failed=1
  fi
  if ! typesense_rotation_is_disabled_inactive; then
    failed=1
  fi
  if [[ "$failed" -ne 0 ]]; then
    echo "ERROR: Typesense backup timer disabled/inactive safety state could not be proven" >&2
    return 1
  fi
}

typesense_rotation_restore_timer_state() {
  local failed=0
  local expected_enabled=disabled
  local expected_active=inactive
  local service_failed_state
  if [[ "$typesense_rotation_timer_quiesced" -ne 1 ]]; then
    return
  fi
  # Successful oneshots can be garbage-collected immediately. Accept only the
  # explicit non-failed states that systemd reports for an inactive or unloaded
  # unit; any other failure to inspect the state remains fatal.
  if service_failed_state="$(
    "$typesense_rotation_systemctl_command" is-failed \
      jobseek-typesense-backup.service 2>/dev/null
  )"; then
    if [[ "$service_failed_state" != failed ]]; then
      failed=1
    fi
    if ! "$typesense_rotation_systemctl_command" reset-failed \
      jobseek-typesense-backup.service; then
      failed=1
    fi
  elif [[ ! "$service_failed_state" =~ ^(inactive|unknown)$ ]]; then
    failed=1
  fi
  if [[ "$typesense_rotation_timer_was_enabled" -eq 1 ]]; then
    expected_enabled=enabled
    if ! "$typesense_rotation_systemctl_command" enable \
      jobseek-typesense-backup.timer; then
      failed=1
    fi
  fi
  if [[ "$typesense_rotation_timer_was_active" -eq 1 ]]; then
    expected_active=active
    if ! "$typesense_rotation_systemctl_command" start \
      jobseek-typesense-backup.timer; then
      failed=1
    fi
  fi
  if ! typesense_rotation_read_service_state 1 || \
    [[ "$typesense_rotation_timer_enabled_state" != "$expected_enabled" ]] || \
    [[ "$typesense_rotation_timer_active_state" != "$expected_active" ]] || \
    [[ ! "$typesense_rotation_service_active_state" =~ ^(inactive|unknown)$ ]]; then
    failed=1
  fi
  if [[ "$failed" -ne 0 ]]; then
    echo "ERROR: prior Typesense backup timer state could not be restored" >&2
    if ! typesense_rotation_disable_fail_safe; then
      echo "ERROR: Typesense backup timer rollback failed hard" >&2
    fi
    return 1
  fi
  typesense_rotation_timer_quiesced=0
}

typesense_rotation_validate_fresh_status() {
  local smoke_started=$1
  BACKUP_SMOKE_STARTED="$smoke_started" \
    TYPESENSE_ROTATION_STATUS_FILE="$typesense_rotation_status_file" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

status = json.loads(
    Path(os.environ["TYPESENSE_ROTATION_STATUS_FILE"]).read_text(encoding="utf-8")
)
started = int(os.environ["BACKUP_SMOKE_STARTED"])
if (
    status.get("service") != "typesense"
    or status.get("success") is not True
    or int(status.get("attempt_unix") or 0) < started
    or int(status.get("last_success_unix") or 0) < started
):
    raise SystemExit("ERROR: Typesense backup smoke did not succeed")
PY
}

typesense_rotation_repair_failed_backup() {
  local lock_timeout=$1
  local service_lock_fd=$2
  local smoke_started
  if [[ "$typesense_rotation_changed" -ne 0 || \
    "$typesense_rotation_pending" -ne 0 ]]; then
    echo "ERROR: Typesense backup repair requires an unchanged credential" >&2
    return 1
  fi

  typesense_rotation_read_service_state
  if [[ "$typesense_rotation_timer_enabled_state" == enabled ]]; then
    typesense_rotation_timer_was_enabled=1
  fi
  if [[ "$typesense_rotation_timer_active_state" == active ]]; then
    typesense_rotation_timer_was_active=1
  fi

  # Arm the existing rollback trap before changing timer or lock state. With
  # an unchanged credential, rollback only has to prove a disabled timer and
  # regain the service lock; it still retains the prior environment snapshot.
  typesense_rotation_pending=1
  typesense_rotation_timer_quiesced=1
  typesense_rotation_disable_fail_safe

  typesense_rotation_service_lock_held=0
  if ! "$typesense_rotation_flock_command" -u "$service_lock_fd"; then
    typesense_rotation_service_lock_held=1
    echo "ERROR: could not release Typesense service data lock for repair" >&2
    return 1
  fi

  smoke_started="$(date +%s)"
  if ! "$typesense_rotation_systemctl_command" reset-failed \
    jobseek-typesense-backup.service; then
    echo "ERROR: could not reset the failed Typesense backup unit" >&2
    return 1
  fi
  if ! "$typesense_rotation_systemctl_command" start \
    jobseek-typesense-backup.service; then
    echo "ERROR: repaired Typesense backup smoke failed" >&2
    return 1
  fi
  typesense_rotation_validate_fresh_status "$smoke_started"

  if ! "$typesense_rotation_flock_command" -w "$lock_timeout" \
    "$service_lock_fd"; then
    typesense_rotation_lock_reacquire_failed=1
    echo "ERROR: could not reacquire Typesense service data lock after repair" >&2
    return 1
  fi
  typesense_rotation_service_lock_held=1
  typesense_rotation_restore_timer_state
  typesense_rotation_pending=0
}

typesense_rotation_smoke_and_commit() {
  local lock_timeout=$1
  local service_lock_fd=$2
  local smoke_started
  if [[ "$typesense_rotation_changed" -ne 1 ]]; then
    return
  fi

  typesense_rotation_read_service_state
  if [[ "$typesense_rotation_timer_enabled_state" == enabled ]]; then
    typesense_rotation_timer_was_enabled=1
  fi
  if [[ "$typesense_rotation_timer_active_state" == active ]]; then
    typesense_rotation_timer_was_active=1
  fi
  typesense_rotation_timer_quiesced=1
  typesense_rotation_disable_fail_safe

  # Mark unlocked first.  If interrupted in this two-command window cleanup
  # safely attempts a reacquire even if the inherited open file description is
  # still locked by this process.
  typesense_rotation_service_lock_held=0
  if ! "$typesense_rotation_flock_command" -u "$service_lock_fd"; then
    typesense_rotation_service_lock_held=1
    echo "ERROR: could not release Typesense service data lock for smoke" >&2
    return 1
  fi

  smoke_started="$(date +%s)"
  if ! (
    local backup_status_dir
    backup_status_dir="$(dirname "$typesense_rotation_status_file")"
    set -a
    # shellcheck disable=SC1090
    source "$typesense_rotation_candidate_env"
    set +a
    export BACKUP_STATUS_DIR="$backup_status_dir"
    "$typesense_rotation_backup_command" typesense
  ); then
    echo "ERROR: Typesense credential-change backup smoke failed" >&2
    return 1
  fi
  typesense_rotation_validate_fresh_status "$smoke_started"

  if ! "$typesense_rotation_flock_command" -w "$lock_timeout" \
    "$service_lock_fd"; then
    typesense_rotation_lock_reacquire_failed=1
    echo "ERROR: could not reacquire Typesense service data lock after smoke" >&2
    return 1
  fi
  typesense_rotation_service_lock_held=1

  typesense_rotation_commit_started=1
  mv -f "$typesense_rotation_candidate_env" "$typesense_rotation_live_env"
  typesense_rotation_candidate_env=""
  typesense_rotation_validate_file "$typesense_rotation_live_env"
  typesense_rotation_commit_complete=1
  typesense_rotation_restore_timer_state
}

typesense_rotation_atomic_restore() {
  if [[ "$typesense_rotation_test_mode" == 1 && \
    "$typesense_rotation_force_restore_failure" == 1 ]]; then
    return 1
  fi
  TYPESENSE_ROTATION_PREVIOUS_ENV="$typesense_rotation_previous_env" \
    TYPESENSE_ROTATION_LIVE_ENV="$typesense_rotation_live_env" \
    TYPESENSE_ROTATION_TEST_MODE="$typesense_rotation_test_mode" \
    python3 - <<'PY'
import os
from pathlib import Path

previous = Path(os.environ["TYPESENSE_ROTATION_PREVIOUS_ENV"])
live = Path(os.environ["TYPESENSE_ROTATION_LIVE_ENV"])
temporary = live.with_name(f".{live.name}.rollback-restore.{os.getpid()}.tmp")
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(previous.read_bytes())
    if os.environ["TYPESENSE_ROTATION_TEST_MODE"] != "1":
        os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o600)
    os.replace(temporary, live)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
}

typesense_rotation_rollback() {
  local lock_timeout=$1
  local service_lock_fd=$2
  local failed=0
  if [[ "$typesense_rotation_pending" -ne 1 ]]; then
    return
  fi

  # A failed transaction never restores an enabled timer automatically.  The
  # old credential is restored under the service lock and the operator gets an
  # explicit hard failure with the timer proven safe.
  if ! typesense_rotation_disable_fail_safe; then
    failed=1
  fi
  typesense_rotation_timer_quiesced=0

  if [[ "$typesense_rotation_service_lock_held" -ne 1 ]]; then
    if "$typesense_rotation_flock_command" -w "$lock_timeout" \
      "$service_lock_fd"; then
      typesense_rotation_service_lock_held=1
    else
      typesense_rotation_lock_reacquire_failed=1
      echo "ERROR: Typesense credential rollback could not reacquire service data lock" >&2
      failed=1
    fi
  fi

  if [[ "$typesense_rotation_service_lock_held" -eq 1 ]]; then
    if [[ "$typesense_rotation_commit_started" -eq 1 && \
      "$typesense_rotation_commit_complete" -ne 1 ]]; then
      echo "ERROR: Typesense credential commit was interrupted; restoring prior value" >&2
    fi
    if [[ -z "$typesense_rotation_previous_env" || \
      ! -f "$typesense_rotation_previous_env" ]]; then
      echo "ERROR: prior Typesense credential snapshot is unavailable" >&2
      failed=1
    elif ! cmp -s "$typesense_rotation_previous_env" \
      "$typesense_rotation_live_env"; then
      if ! typesense_rotation_atomic_restore; then
        failed=1
      elif ! typesense_rotation_validate_file "$typesense_rotation_live_env" || \
        ! cmp -s "$typesense_rotation_previous_env" \
          "$typesense_rotation_live_env"; then
        failed=1
      fi
    fi
  fi

  if [[ "$failed" -ne 0 ]]; then
    if [[ "$typesense_rotation_lock_reacquire_failed" -eq 1 ]]; then
      echo "ERROR: Typesense credential rollback did not regain its service lock" >&2
    fi
    echo "ERROR: Typesense credential rollback failed hard; timer remains disabled" >&2
    return 1
  fi
  typesense_rotation_pending=0
}

typesense_rotation_discard() {
  if [[ -n "$typesense_rotation_candidate_root" ]]; then
    rm -rf -- "$typesense_rotation_candidate_root"
    typesense_rotation_candidate_root=""
    typesense_rotation_candidate_env=""
  fi
  if [[ -n "$typesense_rotation_previous_env" ]]; then
    rm -f -- "$typesense_rotation_previous_env"
    typesense_rotation_previous_env=""
  fi
}

typesense_rotation_finalize() {
  if [[ "$typesense_rotation_changed" -eq 1 ]]; then
    [[ "$typesense_rotation_commit_complete" -eq 1 ]]
    [[ "$typesense_rotation_pending" -eq 1 ]]
    typesense_rotation_pending=0
  fi
  typesense_rotation_discard
}
