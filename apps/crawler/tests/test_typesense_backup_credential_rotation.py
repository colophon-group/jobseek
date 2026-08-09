from __future__ import annotations

import os
import platform
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ROTATION_LIBRARY = ROOT / "deploy/backups/typesense/credential-rotation.sh"
OLD_KEY = "old-typesense-backup-key-test-only"
NEW_KEY = "new-typesense-backup-key-test-only"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@dataclass
class RotationHarness:
    root: Path
    live_env: Path
    candidate_key: Path
    status_file: Path
    timer_enabled: Path
    timer_active: Path
    service_active: Path
    actions: Path
    smoke_started: Path
    post_commit_started: Path
    harness: Path
    environment: dict[str, str]

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        environment = self.environment | overrides
        return subprocess.run(
            ["bash", str(self.harness)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=20,
        )

    def live_key(self) -> str:
        line = next(
            line
            for line in self.live_env.read_text(encoding="utf-8").splitlines()
            if line.startswith("TYPESENSE_API_KEY=")
        )
        return line.split("=", 1)[1]

    def action_lines(self) -> list[str]:
        return self.actions.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def rotation_harness(tmp_path: Path) -> RotationHarness:
    root = tmp_path / "root"
    credential_dir = root / "etc/jobseek-backup"
    status_dir = root / "var/lib/jobseek-backup/status"
    state_dir = root / "state"
    bin_dir = root / "bin"
    for directory in (credential_dir, status_dir, state_dir, bin_dir):
        directory.mkdir(parents=True, mode=0o700)

    live_env = credential_dir / "typesense.env"
    live_env.write_text(
        "RESTIC_PASSWORD_FILE=/root-only/restic-password\n"
        "RESTIC_REPOSITORY=sftp:backup.invalid:/typesense\n"
        "RESTIC_SFTP_COMMAND='ssh -i /root-only/id_ed25519'\n"
        f"TYPESENSE_API_KEY={OLD_KEY}\n",
        encoding="utf-8",
    )
    live_env.chmod(0o600)
    candidate_key = root / "candidate-key"
    candidate_key.write_text(NEW_KEY, encoding="utf-8")
    candidate_key.chmod(0o600)

    timer_enabled = state_dir / "timer-enabled"
    timer_active = state_dir / "timer-active"
    service_active = state_dir / "service-active"
    lock_state = state_dir / "lock-state"
    actions = state_dir / "actions"
    smoke_started = state_dir / "smoke-started"
    post_commit_started = state_dir / "post-commit-started"
    timer_enabled.write_text("enabled\n", encoding="utf-8")
    timer_active.write_text("active\n", encoding="utf-8")
    service_active.write_text("inactive\n", encoding="utf-8")
    lock_state.write_text("locked\n", encoding="utf-8")
    actions.write_text("", encoding="utf-8")

    fake_systemctl = bin_dir / "systemctl"
    _write_executable(
        fake_systemctl,
        r"""#!/usr/bin/env bash
set -euo pipefail
command=$1
shift
printf 'systemctl:%s:%s\n' "$command" "$*" >>"$FAKE_ACTIONS"
case "$command" in
  is-enabled)
    state="$(cat "$FAKE_TIMER_ENABLED")"
    printf '%s\n' "$state"
    [[ "$state" == enabled ]]
    ;;
  is-active)
    if [[ "$1" == *.timer ]]; then
      state="$(cat "$FAKE_TIMER_ACTIVE")"
    else
      state="$(cat "$FAKE_SERVICE_ACTIVE")"
    fi
    printf '%s\n' "$state"
    [[ "$state" == active ]]
    ;;
  disable)
    printf 'disabled\n' >"$FAKE_TIMER_ENABLED"
    printf 'inactive\n' >"$FAKE_TIMER_ACTIVE"
    ;;
  stop)
    if [[ "$1" == *.timer ]]; then
      printf 'inactive\n' >"$FAKE_TIMER_ACTIVE"
    else
      printf 'inactive\n' >"$FAKE_SERVICE_ACTIVE"
    fi
    ;;
  enable)
    printf 'enabled\n' >"$FAKE_TIMER_ENABLED"
    ;;
  start)
    if [[ "$1" == *.timer ]]; then
      printf 'active\n' >"$FAKE_TIMER_ACTIVE"
    else
      printf 'active\n' >"$FAKE_SERVICE_ACTIVE"
    fi
    ;;
  reset-failed)
    printf 'inactive\n' >"$FAKE_SERVICE_ACTIVE"
    ;;
  *)
    exit 64
    ;;
esac
""",
    )

    fake_flock = bin_dir / "flock"
    _write_executable(
        fake_flock,
        r"""#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  -u)
    printf 'unlock\n' >>"$FAKE_ACTIONS"
    printf 'unlocked\n' >"$FAKE_LOCK_STATE"
    ;;
  -w)
    printf 'reacquire\n' >>"$FAKE_ACTIONS"
    if [[ "${FAKE_REACQUIRE_FAIL:-0}" == 1 ]] &&
      [[ "$(cat "$FAKE_LOCK_STATE")" == unlocked ]]; then
      exit 1
    fi
    printf 'locked\n' >"$FAKE_LOCK_STATE"
    ;;
  *)
    exit 64
    ;;
esac
""",
    )

    fake_authorize = bin_dir / "authorize"
    _write_executable(
        fake_authorize,
        r"""#!/usr/bin/env bash
set -euo pipefail
printf 'authorize\n' >>"$FAKE_ACTIONS"
test -s "$TYPESENSE_ROTATION_KEY_FILE"
[[ "${FAKE_AUTH_FAIL:-0}" != 1 ]]
""",
    )

    fake_backup = bin_dir / "backup"
    _write_executable(
        fake_backup,
        r"""#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == typesense ]]
printf 'backup\n' >>"$FAKE_ACTIONS"
[[ "$TYPESENSE_API_KEY" == "$FAKE_EXPECTED_KEY" ]]
if [[ "${FAKE_SMOKE_WAIT:-0}" == 1 ]]; then
  : >"$FAKE_SMOKE_STARTED"
  while :; do sleep 1; done
fi
now="$(date +%s)"
mkdir -p "$BACKUP_STATUS_DIR"
if [[ "${FAKE_SMOKE_FAIL:-0}" == 1 ]]; then
  printf '{"service":"typesense","success":false,"attempt_unix":%s,"last_success_unix":0}\n' \
    "$now" >"$BACKUP_STATUS_DIR/typesense.json"
  exit 1
fi
printf '{"service":"typesense","success":true,"attempt_unix":%s,"last_success_unix":%s}\n' \
  "$now" "$now" >"$BACKUP_STATUS_DIR/typesense.json"
""",
    )

    harness = root / "run-rotation.sh"
    _write_executable(
        harness,
        r"""#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1090
source "$ROTATION_LIBRARY"
typesense_rotation_test_mode=1
typesense_rotation_live_env="$LIVE_ENV"
typesense_rotation_status_file="$STATUS_FILE"
typesense_rotation_backup_command="$FAKE_BACKUP"
typesense_rotation_systemctl_command="$FAKE_SYSTEMCTL"
typesense_rotation_flock_command="$FAKE_FLOCK"
typesense_rotation_auth_probe_command="$FAKE_AUTHORIZE"
typesense_rotation_force_restore_failure="${FAKE_RESTORE_FAIL:-0}"
exec 9>"$SERVICE_LOCK"

cleanup_harness() {
  local status=$?
  local cleanup_failed=0
  if [[ "$status" -ne 0 ]] &&
    ! typesense_rotation_rollback 1 9; then
    cleanup_failed=1
  fi
  typesense_rotation_discard
  if [[ "$cleanup_failed" -ne 0 ]]; then
    exit 1
  fi
  exit "$status"
}
trap cleanup_harness EXIT
trap 'trap - HUP INT TERM; echo "interrupted" >&2; exit 1' HUP INT TERM

typesense_rotation_prepare
typesense_rotation_smoke_and_commit 1 9
if [[ "${FAKE_FAIL_AFTER_COMMIT:-0}" == 1 ]]; then
  echo "post-commit gate failed" >&2
  false
fi
if [[ "${FAKE_WAIT_AFTER_COMMIT:-0}" == 1 ]]; then
  : >"$FAKE_POST_COMMIT_STARTED"
  while :; do sleep 1; done
fi
printf 'marker\n' >>"$FAKE_ACTIONS"
typesense_rotation_finalize
""",
    )

    environment = os.environ.copy()
    environment.update(
        {
            "ROTATION_LIBRARY": str(ROTATION_LIBRARY),
            "LIVE_ENV": str(live_env),
            "STATUS_FILE": str(status_dir / "typesense.json"),
            "JOBSEEK_TYPESENSE_BACKUP_KEY_FILE": str(candidate_key),
            "FAKE_SYSTEMCTL": str(fake_systemctl),
            "FAKE_FLOCK": str(fake_flock),
            "FAKE_AUTHORIZE": str(fake_authorize),
            "FAKE_BACKUP": str(fake_backup),
            "FAKE_TIMER_ENABLED": str(timer_enabled),
            "FAKE_TIMER_ACTIVE": str(timer_active),
            "FAKE_SERVICE_ACTIVE": str(service_active),
            "FAKE_LOCK_STATE": str(lock_state),
            "FAKE_ACTIONS": str(actions),
            "FAKE_SMOKE_STARTED": str(smoke_started),
            "FAKE_POST_COMMIT_STARTED": str(post_commit_started),
            "FAKE_EXPECTED_KEY": NEW_KEY,
            "SERVICE_LOCK": str(state_dir / "service.lock"),
        }
    )
    return RotationHarness(
        root=root,
        live_env=live_env,
        candidate_key=candidate_key,
        status_file=status_dir / "typesense.json",
        timer_enabled=timer_enabled,
        timer_active=timer_active,
        service_active=service_active,
        actions=actions,
        smoke_started=smoke_started,
        post_commit_started=post_commit_started,
        harness=harness,
        environment=environment,
    )


def _assert_secret_safe(result: subprocess.CompletedProcess[str]) -> None:
    combined = result.stdout + result.stderr
    assert OLD_KEY not in combined
    assert NEW_KEY not in combined


def _assert_timer_safe(harness: RotationHarness) -> None:
    assert harness.timer_enabled.read_text(encoding="utf-8").strip() == "disabled"
    assert harness.timer_active.read_text(encoding="utf-8").strip() == "inactive"
    assert harness.service_active.read_text(encoding="utf-8").strip() != "active"


def test_unchanged_key_authorizes_without_smoke_or_timer_mutation(
    rotation_harness: RotationHarness,
) -> None:
    rotation_harness.candidate_key.write_text(OLD_KEY, encoding="utf-8")
    rotation_harness.candidate_key.chmod(0o600)
    result = rotation_harness.run(FAKE_EXPECTED_KEY=OLD_KEY)

    assert result.returncode == 0, result.stderr
    assert rotation_harness.live_key() == OLD_KEY
    assert rotation_harness.timer_enabled.read_text(encoding="utf-8").strip() == "enabled"
    assert rotation_harness.timer_active.read_text(encoding="utf-8").strip() == "active"
    assert rotation_harness.action_lines() == ["authorize", "marker"]
    _assert_secret_safe(result)


def test_changed_key_smokes_candidate_before_atomic_commit_and_marker(
    rotation_harness: RotationHarness,
) -> None:
    result = rotation_harness.run()

    assert result.returncode == 0, result.stderr
    assert rotation_harness.live_key() == NEW_KEY
    assert rotation_harness.status_file.is_file()
    assert rotation_harness.timer_enabled.read_text(encoding="utf-8").strip() == "enabled"
    assert rotation_harness.timer_active.read_text(encoding="utf-8").strip() == "active"
    actions = rotation_harness.action_lines()
    disable = next(
        index for index, action in enumerate(actions) if action.startswith("systemctl:disable:")
    )
    stop = actions.index("systemctl:stop:jobseek-typesense-backup.service")
    assert actions.index("authorize") < disable < stop < actions.index("unlock")
    assert actions.index("unlock") < actions.index("backup")
    assert actions.index("backup") < actions.index("reacquire") < actions.index("marker")
    assert OLD_KEY not in "\n".join(actions)
    assert NEW_KEY not in "\n".join(actions)
    assert not list(rotation_harness.live_env.parent.glob(".typesense-candidate.*"))
    assert not list(rotation_harness.live_env.parent.glob(".typesense-env.rollback.*"))
    _assert_secret_safe(result)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"FAKE_AUTH_FAIL": "1"}, ""),
        ({"FAKE_SMOKE_FAIL": "1"}, "backup smoke failed"),
    ],
)
def test_authorization_and_smoke_failures_preserve_prior_key(
    rotation_harness: RotationHarness,
    overrides: dict[str, str],
    error: str,
) -> None:
    result = rotation_harness.run(**overrides)

    assert result.returncode != 0
    assert rotation_harness.live_key() == OLD_KEY
    if "FAKE_SMOKE_FAIL" in overrides:
        _assert_timer_safe(rotation_harness)
    else:
        assert rotation_harness.timer_enabled.read_text(encoding="utf-8").strip() == "enabled"
        assert rotation_harness.timer_active.read_text(encoding="utf-8").strip() == "active"
    if error:
        assert error in result.stderr
    _assert_secret_safe(result)


def test_post_commit_failure_atomically_restores_prior_key_and_leaves_timer_safe(
    rotation_harness: RotationHarness,
) -> None:
    result = rotation_harness.run(FAKE_FAIL_AFTER_COMMIT="1")

    assert result.returncode != 0
    assert rotation_harness.live_key() == OLD_KEY
    _assert_timer_safe(rotation_harness)
    assert "post-commit gate failed" in result.stderr
    _assert_secret_safe(result)


def test_lock_reacquire_failure_keeps_prior_key_and_reports_hard_rollback(
    rotation_harness: RotationHarness,
) -> None:
    result = rotation_harness.run(FAKE_REACQUIRE_FAIL="1")

    assert result.returncode != 0
    assert rotation_harness.live_key() == OLD_KEY
    _assert_timer_safe(rotation_harness)
    assert "could not reacquire Typesense service data lock after smoke" in result.stderr
    assert "credential rollback failed hard" in result.stderr
    _assert_secret_safe(result)


def test_rollback_failure_is_a_primary_hard_error_and_timer_stays_safe(
    rotation_harness: RotationHarness,
) -> None:
    result = rotation_harness.run(
        FAKE_FAIL_AFTER_COMMIT="1",
        FAKE_RESTORE_FAIL="1",
    )

    assert result.returncode != 0
    assert rotation_harness.live_key() == NEW_KEY
    _assert_timer_safe(rotation_harness)
    assert "credential rollback failed hard" in result.stderr
    _assert_secret_safe(result)


def test_signal_during_candidate_smoke_preserves_prior_key_and_disables_timer(
    rotation_harness: RotationHarness,
) -> None:
    environment = rotation_harness.environment | {"FAKE_SMOKE_WAIT": "1"}
    process = subprocess.Popen(
        ["bash", str(rotation_harness.harness)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while not rotation_harness.smoke_started.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert rotation_harness.smoke_started.exists(), "candidate smoke did not start"
    ps_arguments = (
        ["ps", "-ax", "-o", "pid=", "-o", "ppid=", "-o", "command="]
        if platform.system() == "Darwin"
        else ["ps", "-eo", "pid=,ppid=,args="]
    )
    process_rows = subprocess.run(
        ps_arguments,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    parsed_rows = [line.strip().split(maxsplit=2) for line in process_rows]
    process_tree = {
        int(parts[0]): (int(parts[1]), parts[2] if len(parts) == 3 else "")
        for parts in parsed_rows
        if len(parts) >= 2
    }
    descendants = {process.pid}
    while True:
        discovered = {pid for pid, (parent, _) in process_tree.items() if parent in descendants}
        if discovered <= descendants:
            break
        descendants.update(discovered)
    rotation_processes = "\n".join(
        process_tree[pid][1] for pid in descendants if pid in process_tree
    )
    assert process.pid in process_tree
    assert len(descendants) > 1
    assert OLD_KEY not in rotation_processes
    assert NEW_KEY not in rotation_processes
    os.killpg(process.pid, signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode != 0
    assert rotation_harness.live_key() == OLD_KEY
    _assert_timer_safe(rotation_harness)
    _assert_secret_safe(
        subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    )


def test_signal_after_atomic_commit_restores_prior_key_and_disables_timer(
    rotation_harness: RotationHarness,
) -> None:
    environment = rotation_harness.environment | {"FAKE_WAIT_AFTER_COMMIT": "1"}
    process = subprocess.Popen(
        ["bash", str(rotation_harness.harness)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while not rotation_harness.post_commit_started.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert rotation_harness.post_commit_started.exists(), "candidate commit did not finish"
    assert rotation_harness.live_key() == NEW_KEY
    os.killpg(process.pid, signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode != 0
    assert rotation_harness.live_key() == OLD_KEY
    _assert_timer_safe(rotation_harness)
    _assert_secret_safe(
        subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    )
