from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "deploy/backups/web-postgresql/operations.py"


def load_operations() -> ModuleType:
    spec = importlib.util.spec_from_file_location("web_postgresql_operations", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def bypass_deployment_lock(monkeypatch: pytest.MonkeyPatch, operations: ModuleType) -> None:
    monkeypatch.setattr(operations, "deployment_identity_lock", lambda: nullcontext(41))


def failed_backup_status(**updates: object) -> dict[str, object]:
    status: dict[str, object] = {
        "schema_version": 1,
        "service": "web-postgresql",
        "attempt_at": "1970-01-01T00:16:40+00:00",
        "attempt_unix": 1_000,
        "success": False,
        "finished_at": "1970-01-01T00:16:41+00:00",
        "duration_seconds": 1.25,
        "error": "pg_dump exited 1: connection refused",
    }
    status.update(updates)
    return status


def test_identity_requires_exact_deployed_revision_and_every_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    deployed_sha = "a" * 40
    deployed_path = tmp_path / "deployed-sha"
    deployed_path.write_text(deployed_sha + "\n", encoding="utf-8")
    artifacts = {
        name: tmp_path / name
        for name in (
            "data_backup",
            "operations",
            "retirement_migration",
            "restore_drill",
            "service",
            "timer",
        )
    }
    for name, path in artifacts.items():
        path.write_text(f"{name}\n", encoding="utf-8")
    expected = operations.ExpectedIdentity(
        deploy_sha=deployed_sha,
        artifact_sha256={name: operations.sha256_file(path) for name, path in artifacts.items()},
    )
    monkeypatch.setattr(operations, "DEPLOYED_SHA_PATH", deployed_path)
    monkeypatch.setattr(operations, "ARTIFACT_PATHS", artifacts)
    monkeypatch.setattr(operations, "require_root_regular_file", lambda *_args, **_kwargs: None)

    actual = operations.validate_identity(expected)

    assert actual == {
        str(artifacts[name]): expected.artifact_sha256[name] for name in sorted(artifacts)
    }
    artifacts["restore_drill"].write_text("tampered\n", encoding="utf-8")
    with pytest.raises(operations.OperationError, match="restore_drill"):
        operations.validate_identity(expected)


def test_reset_failed_unit_resets_only_a_genuinely_failed_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    checked: list[list[str]] = []
    reset: list[list[str]] = []
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda argv, **_kwargs: (
            checked.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )
    monkeypatch.setattr(
        operations,
        "run_checked",
        lambda argv, **_kwargs: reset.append(argv) or "",
    )

    operations.reset_failed_unit_if_needed(operations.BACKUP_UNIT)

    assert checked == [["systemctl", "is-failed", "--quiet", operations.BACKUP_UNIT]]
    assert reset == [["systemctl", "reset-failed", operations.BACKUP_UNIT]]


def test_reset_failed_unit_accepts_a_loaded_nonfailed_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    checked: list[list[str]] = []
    followups: list[list[str]] = []
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda argv, **_kwargs: (
            checked.append(argv) or subprocess.CompletedProcess(argv, 1, "", "")
        ),
    )

    def fake_run_checked(argv: list[str], **_kwargs: object) -> str:
        followups.append(argv)
        return "loaded\n"

    monkeypatch.setattr(operations, "run_checked", fake_run_checked)

    operations.reset_failed_unit_if_needed(operations.BACKUP_UNIT)

    assert checked == [["systemctl", "is-failed", "--quiet", operations.BACKUP_UNIT]]
    assert followups == [
        [
            "systemctl",
            "show",
            operations.BACKUP_UNIT,
            "--property=LoadState",
            "--value",
        ]
    ]


@pytest.mark.parametrize(
    ("returncode", "load_state"),
    ((4, None), (1, "not-found\n")),
)
def test_reset_failed_unit_rejects_unknown_or_unavailable_state(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    load_state: str | None,
) -> None:
    operations = load_operations()
    followups: list[list[str]] = []
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            returncode,
            "",
            "password=must-not-appear postgresql://must-not-appear",
        ),
    )

    def fake_run_checked(argv: list[str], **_kwargs: object) -> str:
        followups.append(argv)
        assert load_state is not None
        return load_state

    monkeypatch.setattr(operations, "run_checked", fake_run_checked)

    with pytest.raises(operations.OperationError, match="state is unavailable") as raised:
        operations.reset_failed_unit_if_needed(operations.BACKUP_UNIT)

    assert "must-not-appear" not in str(raised.value)
    if returncode == 4:
        assert followups == []
    else:
        assert followups == [
            [
                "systemctl",
                "show",
                operations.BACKUP_UNIT,
                "--property=LoadState",
                "--value",
            ]
        ]


def test_failed_backup_emits_only_fresh_aggregate_evidence_and_preserves_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operations = load_operations()
    expected = operations.ExpectedIdentity("a" * 40, {})
    status_path = tmp_path / "web-postgresql.json"
    original_failure = operations.OperationError("systemctl operation failed")
    failure_state_checks: list[str] = []
    monkeypatch.setattr(operations, "BACKUP_STATUS_PATH", status_path)
    monkeypatch.setattr(operations, "EVIDENCE_PATH", tmp_path / "activation.json")
    monkeypatch.setattr(operations, "validate_host_readiness", lambda _expected: None)
    monkeypatch.setattr(operations, "validate_identity", lambda _expected: {})
    monkeypatch.setattr(operations, "timer_state", lambda: ("disabled", "inactive"))
    monkeypatch.setattr(
        operations,
        "reset_failed_unit_if_needed",
        lambda unit: failure_state_checks.append(unit),
    )
    monkeypatch.setattr(operations.time, "time", lambda: 1_000)

    def fail_start(argv: list[str], **_kwargs: object) -> str:
        if argv == ["systemctl", "start", operations.BACKUP_UNIT]:
            status_path.write_text(
                json.dumps(
                    failed_backup_status(
                        row_count=99,
                        database_url="postgresql://must-not-appear",
                        environment={"PASSWORD": "must-not-appear"},
                    )
                ),
                encoding="utf-8",
            )
            raise original_failure
        return ""

    monkeypatch.setattr(operations, "run_checked", fail_start)

    with pytest.raises(operations.OperationError) as raised:
        operations.run_backup_locked(expected)

    assert raised.value is original_failure
    assert failure_state_checks == [operations.BACKUP_UNIT]
    prefix = "Backup failure evidence: "
    diagnostic_line = capsys.readouterr().err.strip()
    assert diagnostic_line.startswith(prefix)
    assert json.loads(diagnostic_line.removeprefix(prefix)) == {
        "attempt_at": "1970-01-01T00:16:40+00:00",
        "duration_seconds": 1.25,
        "error": "pg_dump exited 1: connection refused",
    }
    assert "row_count" not in diagnostic_line
    assert "database_url" not in diagnostic_line
    assert "environment" not in diagnostic_line


def test_backup_failure_diagnostic_redacts_uris_and_credentials() -> None:
    operations = load_operations()
    diagnostic = operations.backup_failure_diagnostic(
        failed_backup_status(
            error=(
                "pg_dump postgresql://db-user:db-password@db.example/jobs failed; "
                "password=plain-secret token:opaque-secret api_key api-secret "
                "Authorization: Bearer bearer-secret "
                "https://user:web-password@example.test/path?token=query-secret"
            )
        ),
        started=1_000,
    )

    assert len(diagnostic) <= operations.BACKUP_FAILURE_DIAGNOSTIC_LIMIT
    assert "db-user" not in diagnostic
    assert "db-password" not in diagnostic
    assert "plain-secret" not in diagnostic
    assert "opaque-secret" not in diagnostic
    assert "api-secret" not in diagnostic
    assert "bearer-secret" not in diagnostic
    assert "web-password" not in diagnostic
    assert "query-secret" not in diagnostic
    assert "<redacted-uri>" in diagnostic
    assert "password=<redacted>" in diagnostic
    assert "token=<redacted>" in diagnostic
    assert "api_key=<redacted>" in diagnostic
    assert "authorization=<redacted>" in diagnostic


@pytest.mark.parametrize(
    "status_content",
    (None, "{", json.dumps(failed_backup_status(attempt_unix=999))),
)
def test_failed_backup_without_fresh_valid_evidence_preserves_original_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    status_content: str | None,
) -> None:
    operations = load_operations()
    expected = operations.ExpectedIdentity("a" * 40, {})
    status_path = tmp_path / "web-postgresql.json"
    original_failure = operations.OperationError("systemctl operation failed")
    if status_content is not None:
        status_path.write_text(status_content, encoding="utf-8")
    monkeypatch.setattr(operations, "BACKUP_STATUS_PATH", status_path)
    monkeypatch.setattr(operations, "EVIDENCE_PATH", tmp_path / "activation.json")
    monkeypatch.setattr(operations, "validate_host_readiness", lambda _expected: None)
    monkeypatch.setattr(operations, "validate_identity", lambda _expected: {})
    monkeypatch.setattr(operations, "timer_state", lambda: ("disabled", "inactive"))
    monkeypatch.setattr(operations, "reset_failed_unit_if_needed", lambda _unit: None)
    monkeypatch.setattr(operations.time, "time", lambda: 1_000)
    monkeypatch.setattr(
        operations,
        "run_checked",
        lambda argv, **_kwargs: (
            (_ for _ in ()).throw(original_failure)
            if argv == ["systemctl", "start", operations.BACKUP_UNIT]
            else ""
        ),
    )

    with pytest.raises(operations.OperationError) as raised:
        operations.run_backup_locked(expected)

    assert raised.value is original_failure
    assert capsys.readouterr().err == ""


def test_root_artifact_boundary_rejects_nonroot_or_writable_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()

    monkeypatch.setattr(
        Path,
        "lstat",
        lambda _path: type(
            "Metadata",
            (),
            {"st_mode": operations.stat.S_IFREG | 0o775, "st_uid": 0, "st_gid": 0},
        )(),
    )

    with pytest.raises(operations.OperationError, match="unsafe"):
        operations.require_root_regular_file(Path("/installed/helper"), mode=0o755)


def test_loaded_unit_rejects_drop_ins_or_daemon_reload_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    expected_path = Path("/etc/systemd/system/jobseek-web-postgresql-backup.service")
    monkeypatch.setattr(
        operations,
        "run_checked",
        lambda *_args, **_kwargs: (
            f"FragmentPath={expected_path}\n"
            "DropInPaths=/etc/systemd/system/jobseek-web-postgresql-backup.service.d/override.conf\n"
            "NeedDaemonReload=yes\n"
        ),
    )

    with pytest.raises(operations.OperationError, match="does not match reviewed artifact"):
        operations.validate_loaded_unit(operations.BACKUP_UNIT, expected_path)


def test_readiness_requires_the_exact_stopped_helper_image_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    exact = json.dumps(
        [
            {
                "Config": {
                    "Image": operations.RESTORE_IMAGE,
                    "Labels": {operations.HELPER_IMAGE_LEASE_LABEL: "web-postgresql"},
                    "Entrypoint": ["/bin/true"],
                },
                "State": {"Running": False},
                "HostConfig": {
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "Tmpfs": operations.HELPER_IMAGE_LEASE_TMPFS,
                },
                "Mounts": [],
            }
        ]
    )
    monkeypatch.setattr(
        operations,
        "run_checked",
        lambda argv, **_kwargs: exact if argv[:3] == ["docker", "container", "inspect"] else "",
    )
    operations.validate_helper_image_lease()

    wrong = exact.replace(operations.RESTORE_IMAGE, "postgres:17-alpine")
    monkeypatch.setattr(
        operations,
        "run_checked",
        lambda argv, **_kwargs: wrong if argv[:3] == ["docker", "container", "inspect"] else "",
    )
    with pytest.raises(operations.OperationError, match="pinned digest"):
        operations.validate_helper_image_lease()


def test_enable_timer_requires_disabled_and_inactive_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    bypass_deployment_lock(monkeypatch, operations)
    expected = operations.ExpectedIdentity("a" * 40, {})
    commands: list[list[str]] = []
    monkeypatch.setattr(operations, "validate_host_readiness", lambda _expected: None)
    monkeypatch.setattr(operations, "validate_activation_evidence", lambda _expected: {})
    monkeypatch.setattr(operations, "timer_state", lambda: ("enabled", "active"))
    monkeypatch.setattr(
        operations,
        "run_checked",
        lambda argv, **_kwargs: commands.append(argv) or "",
    )

    with pytest.raises(operations.OperationError, match="disabled and inactive"):
        operations.enable_timer(expected)

    assert commands == []


def test_bound_backup_rejects_deployment_or_live_status_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    expected = operations.ExpectedIdentity("a" * 40, {})
    artifact_hashes = {"/installed/operations": "b" * 64}
    status = {
        "archive_bytes": 10,
        "archive_sha256": "c" * 64,
        "attempt_at": "2026-08-03T12:00:00+00:00",
        "attempt_unix": 900,
        "duration_seconds": 1.0,
        "finished_at": "2026-08-03T12:00:01+00:00",
        "last_success_unix": 900,
        "repository_snapshot_id": "abcdef01",
        "row_count": 2,
        "service": "web-postgresql",
        "success": True,
        "table_count": 1,
    }
    evidence_path = tmp_path / "activation.json"
    status_path = tmp_path / "backup.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(operations, "EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(operations, "BACKUP_STATUS_PATH", status_path)
    monkeypatch.setattr(operations, "validate_identity", lambda _expected: artifact_hashes)
    monkeypatch.setattr(operations.time, "time", lambda: 1_000)

    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "deployed_sha": "different",
                "artifact_sha256": artifact_hashes,
                "backup": status,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(operations.OperationError, match="another deployment"):
        operations.load_bound_backup(expected)

    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "deployed_sha": expected.deploy_sha,
                "artifact_sha256": artifact_hashes,
                "backup": status,
            }
        ),
        encoding="utf-8",
    )
    status_path.write_text(json.dumps({**status, "row_count": 3}), encoding="utf-8")
    with pytest.raises(operations.OperationError, match="no longer matches"):
        operations.load_bound_backup(expected)


def test_restore_binds_exact_retirement_artifact_and_convergence_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    migration_sha256 = "d" * 64
    expected = operations.ExpectedIdentity("a" * 40, {"retirement_migration": migration_sha256})
    backup = {
        "archive_sha256": "c" * 64,
        "row_count": 12,
        "table_count": 17,
    }
    evidence: dict[str, object] = {"backup": backup}
    restore_status = {
        "archive_sha256": backup["archive_sha256"],
        "deploy_sha": expected.deploy_sha,
        "duration_seconds": 3,
        "finished_at": "1970-01-01T00:16:40Z",
        "retirement_convergence_applied": True,
        "retirement_created_at": 1_785_760_800_000,
        "retirement_ledger_count": 76,
        "retirement_migration_sha256": migration_sha256,
        "row_count": backup["row_count"],
        "saved_job_digest": "e" * 32,
        "saved_job_rows": 4,
        "service": "web-postgresql-restore",
        "started_at": "1970-01-01T00:16:40Z",
        "success": True,
        "table_count": backup["table_count"],
    }
    observed_restore: dict[str, object] = {}
    writes: list[dict[str, object]] = []

    def load_bound(_expected: object, *, clear_restore: bool = False) -> tuple[object, object]:
        if clear_restore:
            evidence.pop("restore", None)
        return evidence, backup

    def run_drill(_resources: object, **kwargs: object) -> None:
        observed_restore.update(kwargs)

    monkeypatch.setattr(operations, "validate_host_readiness", lambda _expected: None)
    monkeypatch.setattr(operations, "load_bound_backup", load_bound)
    monkeypatch.setattr(operations, "timer_state", lambda: ("disabled", "inactive"))
    monkeypatch.setattr(operations, "service_data_lock", lambda: nullcontext(42))
    monkeypatch.setattr(operations, "reconcile_stale_restore_resources", lambda: None)
    monkeypatch.setattr(operations, "run_restore_drill", run_drill)
    monkeypatch.setattr(operations, "read_json", lambda _path: restore_status)
    monkeypatch.setattr(operations, "atomic_json", lambda _path, value: writes.append(value))
    monkeypatch.setattr(operations.time, "time", lambda: 1_000)

    operations.run_restore_locked(expected, deployment_lock_fd=43)

    assert observed_restore == {
        "deploy_sha": expected.deploy_sha,
        "service_lock_fd": 42,
        "deployment_lock_fd": 43,
    }
    assert evidence["restore"] == {
        field: restore_status[field] for field in operations.RESTORE_FIELDS
    }
    assert writes[-1] is evidence

    restore_status["retirement_ledger_count"] = 79
    with pytest.raises(operations.OperationError, match="does not match the bound backup"):
        operations.run_restore_locked(expected, deployment_lock_fd=43)

    restore_status["retirement_convergence_applied"] = False
    operations.run_restore_locked(expected, deployment_lock_fd=43)
    assert evidence["restore"] == {
        field: restore_status[field] for field in operations.RESTORE_FIELDS
    }

    restore_status["retirement_migration_sha256"] = "f" * 64
    with pytest.raises(operations.OperationError, match="does not match the bound backup"):
        operations.run_restore_locked(expected, deployment_lock_fd=43)


def test_enable_timer_rolls_back_partial_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    bypass_deployment_lock(monkeypatch, operations)
    expected = operations.ExpectedIdentity("a" * 40, {})
    states = iter((("disabled", "inactive"), ("disabled", "active")))
    rollback_commands: list[list[str]] = []
    monkeypatch.setattr(operations, "validate_host_readiness", lambda _expected: None)
    monkeypatch.setattr(
        operations,
        "validate_activation_evidence",
        lambda _expected: {"table_count": 1, "row_count": 2},
    )
    monkeypatch.setattr(operations, "timer_state", lambda: next(states))
    monkeypatch.setattr(operations, "command_succeeds", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(operations, "validate_identity", lambda _expected: {})
    monkeypatch.setattr(operations, "reset_failed_unit_if_needed", lambda _unit: None)

    def fake_run_checked(argv: list[str], **_kwargs: object) -> str:
        if "NextElapseUSecRealtime" in " ".join(argv):
            raise operations.OperationError("enabled timer has no next run")
        return ""

    def fake_subprocess_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        rollback_commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(operations, "run_checked", fake_run_checked)
    monkeypatch.setattr(operations.subprocess, "run", fake_subprocess_run)

    with pytest.raises(operations.OperationError, match="no next run"):
        operations.enable_timer(expected)

    assert rollback_commands == [["systemctl", "disable", "--now", operations.TIMER_UNIT]]


def test_enable_timer_disarms_rollback_only_after_postconditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    bypass_deployment_lock(monkeypatch, operations)
    expected = operations.ExpectedIdentity("a" * 40, {})
    states = iter((("disabled", "inactive"), ("disabled", "active"), ("enabled", "active")))
    activation_commands: list[list[str]] = []
    rollback_commands: list[list[str]] = []
    failure_state_checks: list[str] = []
    monkeypatch.setattr(operations, "validate_host_readiness", lambda _expected: None)
    monkeypatch.setattr(
        operations,
        "validate_activation_evidence",
        lambda _expected: {"table_count": 1, "row_count": 2},
    )
    monkeypatch.setattr(operations, "timer_state", lambda: next(states))
    monkeypatch.setattr(operations, "command_succeeds", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(operations, "validate_identity", lambda _expected: {})
    monkeypatch.setattr(
        operations,
        "reset_failed_unit_if_needed",
        lambda unit: failure_state_checks.append(unit),
    )

    def fake_run_checked(argv: list[str], **_kwargs: object) -> str:
        activation_commands.append(argv)
        if "NextElapseUSecRealtime" in " ".join(argv):
            return "2030-01-01 00:30:00 UTC\n"
        return ""

    monkeypatch.setattr(operations, "run_checked", fake_run_checked)
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda argv, **_kwargs: (
            rollback_commands.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )

    operations.enable_timer(expected)

    assert rollback_commands == []
    assert failure_state_checks == [operations.BACKUP_UNIT]
    assert activation_commands == [
        ["systemctl", "start", operations.TIMER_UNIT],
        [
            "systemctl",
            "show",
            operations.TIMER_UNIT,
            "--property=NextElapseUSecRealtime",
            "--value",
        ],
        ["systemctl", "enable", operations.TIMER_UNIT],
    ]


def test_enable_timer_rolls_back_post_enable_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    bypass_deployment_lock(monkeypatch, operations)
    expected = operations.ExpectedIdentity("a" * 40, {})
    states = iter((("disabled", "inactive"), ("disabled", "active")))
    rollback_commands: list[list[str]] = []
    monkeypatch.setattr(operations, "validate_host_readiness", lambda _expected: None)
    monkeypatch.setattr(
        operations,
        "validate_activation_evidence",
        lambda _expected: {"table_count": 1, "row_count": 2},
    )
    monkeypatch.setattr(operations, "timer_state", lambda: next(states))
    monkeypatch.setattr(operations, "command_succeeds", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(operations, "reset_failed_unit_if_needed", lambda _unit: None)
    monkeypatch.setattr(
        operations,
        "run_checked",
        lambda argv, **_kwargs: (
            "2030-01-01 00:30:00 UTC\n" if "NextElapseUSecRealtime" in " ".join(argv) else ""
        ),
    )
    monkeypatch.setattr(
        operations,
        "validate_identity",
        lambda _expected: (_ for _ in ()).throw(
            operations.OperationError("installed backup revision changed")
        ),
    )
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda argv, **_kwargs: (
            rollback_commands.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )

    with pytest.raises(operations.OperationError, match="revision changed"):
        operations.enable_timer(expected)

    assert rollback_commands == [["systemctl", "disable", "--now", operations.TIMER_UNIT]]


def test_enable_timer_requires_current_host_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    bypass_deployment_lock(monkeypatch, operations)
    expected = operations.ExpectedIdentity("a" * 40, {})
    evidence_checked = False

    def fail_readiness(_expected: object) -> None:
        raise operations.OperationError("root-only credential mode drifted")

    def check_evidence(_expected: object) -> dict[str, object]:
        nonlocal evidence_checked
        evidence_checked = True
        return {}

    monkeypatch.setattr(operations, "validate_host_readiness", fail_readiness)
    monkeypatch.setattr(operations, "validate_activation_evidence", check_evidence)

    with pytest.raises(operations.OperationError, match="credential mode drifted"):
        operations.enable_timer(expected)

    assert evidence_checked is False


def test_restore_timeout_terminates_process_group_and_runs_exact_reconciliation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    resources = operations.RestoreResources(
        operation_id="a" * 32,
        container="jobseek-web-postgresql-restore-" + "a" * 32,
        network="jobseek-web-postgresql-restore-" + "a" * 32 + "-network",
        operation_root=tmp_path / ("operation-" + "a" * 32),
    )
    observed_env: dict[str, str] = {}
    observed_pass_fds: tuple[int, ...] = ()
    signals: list[int] = []
    reconciled: list[object] = []

    class TimedOutProcess:
        pid = 1234
        returncode: int | None = None
        communicate_calls = 0

        def communicate(self, *, timeout: int | None = None) -> tuple[str, str]:
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired(["restore-drill"], timeout)
            self.returncode = -15
            return "", ""

        def poll(self) -> int | None:
            return self.returncode

    process = TimedOutProcess()

    def fake_popen(_argv: list[str], **kwargs: object) -> TimedOutProcess:
        nonlocal observed_pass_fds
        observed_env.update(kwargs["env"])  # type: ignore[arg-type]
        observed_pass_fds = kwargs["pass_fds"]  # type: ignore[assignment]
        assert kwargs["start_new_session"] is True
        return process

    monkeypatch.setattr(operations.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        operations,
        "process_group_absent",
        lambda _pid: process.returncode is not None,
    )
    monkeypatch.setattr(operations.os, "killpg", lambda _pid, sig: signals.append(sig))
    monkeypatch.setattr(
        operations,
        "reconcile_restore_resources",
        lambda value: reconciled.append(value),
    )

    with pytest.raises(operations.OperationError, match="timed out"):
        operations.run_restore_drill(
            resources,
            deploy_sha="f" * 40,
            service_lock_fd=42,
            deployment_lock_fd=43,
            timeout=1,
        )

    assert signals == [operations.signal.SIGTERM]
    assert reconciled == [resources]
    assert observed_env["WEB_POSTGRES_RESTORE_OPERATION_ID"] == resources.operation_id
    assert observed_env["WEB_POSTGRES_RESTORE_CONTAINER"] == resources.container
    assert observed_env["WEB_POSTGRES_RESTORE_NETWORK"] == resources.network
    assert observed_env["WEB_POSTGRES_RESTORE_OPERATION_ROOT"] == str(resources.operation_root)
    assert observed_env["WEB_POSTGRES_RESTORE_DEPLOY_SHA"] == "f" * 40
    assert observed_env["WEB_POSTGRES_RESTORE_LOCK_FD"] == "42"
    assert observed_env["WEB_POSTGRES_RESTORE_DEPLOYMENT_LOCK_FD"] == "43"
    assert observed_pass_fds == (42, 43)


def test_restore_does_not_reconcile_until_process_group_death_is_proven(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    resources = operations.RestoreResources(
        operation_id="c" * 32,
        container="jobseek-web-postgresql-restore-" + "c" * 32,
        network="jobseek-web-postgresql-restore-" + "c" * 32 + "-network",
        operation_root=tmp_path / ("operation-" + "c" * 32),
    )
    reconciled = False

    class UnkillableProcess:
        pid = 4321
        returncode = None

        def communicate(self, *, timeout: int | None = None) -> tuple[str, str]:
            raise subprocess.TimeoutExpired(["restore-drill"], timeout)

        def poll(self) -> None:
            return None

        def wait(self, *, timeout: float | None = None) -> None:
            raise subprocess.TimeoutExpired(["restore-drill"], timeout)

    def observe_reconcile(_resources: object) -> None:
        nonlocal reconciled
        reconciled = True

    monkeypatch.setattr(
        operations.subprocess,
        "Popen",
        lambda _argv, **_kwargs: UnkillableProcess(),
    )
    monkeypatch.setattr(operations, "process_group_absent", lambda _pid: False)
    monkeypatch.setattr(
        operations.os,
        "killpg",
        lambda _pid, _signal: (_ for _ in ()).throw(PermissionError("denied")),
    )
    monkeypatch.setattr(operations, "reconcile_restore_resources", observe_reconcile)

    with pytest.raises(operations.OperationError, match="liveness cannot be excluded"):
        operations.run_restore_drill(resources, deploy_sha="f" * 40, timeout=1)

    assert reconciled is False


def test_restore_termination_is_retried_after_handler_setup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    resources = operations.RestoreResources(
        operation_id="e" * 32,
        container="jobseek-web-postgresql-restore-" + "e" * 32,
        network="jobseek-web-postgresql-restore-" + "e" * 32 + "-network",
        operation_root=tmp_path / ("operation-" + "e" * 32),
    )
    terminations: list[int] = []
    reconciled: list[object] = []

    class LiveProcess:
        pid = 6789
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

    process = LiveProcess()

    def terminate(value: LiveProcess) -> None:
        terminations.append(value.pid)
        value.returncode = -operations.signal.SIGTERM

    monkeypatch.setattr(operations.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        operations.signal,
        "signal",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("handler setup failed")),
    )
    monkeypatch.setattr(
        operations,
        "process_group_absent",
        lambda _pid: process.returncode is not None,
    )
    monkeypatch.setattr(operations, "terminate_restore_process", terminate)
    monkeypatch.setattr(
        operations,
        "reconcile_restore_resources",
        lambda value: reconciled.append(value),
    )

    with pytest.raises(operations.OperationError, match="interrupted"):
        operations.run_restore_drill(resources, deploy_sha="f" * 40)

    assert terminations == [process.pid]
    assert reconciled == [resources]


def test_outer_restore_reconciliation_removes_files_and_rejects_residual_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    operation_root = tmp_path / "operation"
    operation_root.mkdir()
    (operation_root / "decrypted.dump").write_text("sensitive", encoding="utf-8")
    resources = operations.RestoreResources(
        operation_id="b" * 32,
        container="jobseek-web-postgresql-restore-" + "b" * 32,
        network="jobseek-web-postgresql-restore-" + "b" * 32 + "-network",
        operation_root=operation_root,
    )

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ["docker", "container", "ls"]:
            return subprocess.CompletedProcess(argv, 0, resources.container + "\n", "")
        if argv[:3] == ["docker", "network", "ls"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "remove failed")

    monkeypatch.setattr(operations.subprocess, "run", fake_run)

    with pytest.raises(operations.OperationError, match="cleanup could not be proven"):
        operations.reconcile_restore_resources(resources)

    assert not operation_root.exists()


def test_restore_cleanup_rejects_failed_docker_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, "", "transient error"),
    )

    with pytest.raises(operations.OperationError, match="inventory is unavailable"):
        operations.docker_resource_absent("container", "test-restore")


def test_next_restore_reconciles_only_service_labeled_stale_resources_under_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    runtime_root = tmp_path / "drills"
    stale_root = runtime_root / ("operation-" + "d" * 32)
    stale_root.mkdir(parents=True)
    (stale_root / "decrypted.dump").write_text("sensitive", encoding="utf-8")
    inventories = {
        "container": iter((["stale-container-id"], [])),
        "network": iter((["stale-network-id"], [])),
    }
    removals: list[list[str]] = []
    monkeypatch.setattr(operations, "RESTORE_RUNTIME_ROOT", runtime_root)
    assert (
        "with service_data_lock() as service_lock_fd:\n"
        "        reconcile_stale_restore_resources()\n"
        "        run_restore_drill(\n"
        "            new_restore_resources(),\n"
        "            deploy_sha=expected.deploy_sha,\n"
        "            service_lock_fd=service_lock_fd,\n"
        "            deployment_lock_fd=deployment_lock_fd,\n"
        "        )" in MODULE_PATH.read_text(encoding="utf-8")
    )
    monkeypatch.setattr(
        operations,
        "listed_restore_resources",
        lambda kind: next(inventories[kind]),
    )
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda argv, **_kwargs: (
            removals.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )

    operations.reconcile_stale_restore_resources()

    assert removals == [
        ["docker", "rm", "--force", "stale-container-id"],
        ["docker", "network", "rm", "stale-network-id"],
    ]
    assert not stale_root.exists()


def test_restore_lock_exception_never_explicitly_unlocks_inherited_open_description(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    flock_operations: list[int] = []
    monkeypatch.setattr(operations, "RESTORE_LOCK_PATH", tmp_path / "restore.lock")
    monkeypatch.setattr(
        operations.fcntl,
        "flock",
        lambda _handle, operation: flock_operations.append(operation),
    )

    with (
        pytest.raises(RuntimeError, match="simulated parent failure"),
        operations.service_data_lock() as inherited_fd,
    ):
        assert inherited_fd >= 0
        raise RuntimeError("simulated parent failure")

    assert flock_operations == [operations.fcntl.LOCK_EX | operations.fcntl.LOCK_NB]
    assert "LOCK_UN" not in MODULE_PATH.read_text(encoding="utf-8")


def test_restore_wrapper_holds_and_passes_deployment_identity_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    expected = operations.ExpectedIdentity("a" * 40, {})
    lock_is_held = False
    observed_fd: int | None = None

    @contextmanager
    def fake_deployment_lock() -> object:
        nonlocal lock_is_held
        lock_is_held = True
        try:
            yield 84
        finally:
            lock_is_held = False

    def fake_run_restore_locked(_expected: object, *, deployment_lock_fd: int) -> None:
        nonlocal observed_fd
        assert lock_is_held
        observed_fd = deployment_lock_fd

    monkeypatch.setattr(operations, "deployment_identity_lock", fake_deployment_lock)
    monkeypatch.setattr(operations, "run_restore_locked", fake_run_restore_locked)

    operations.run_restore(expected)

    assert observed_fd == 84
    assert lock_is_held is False


def test_deployment_lock_reuses_and_validates_inherited_open_description(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    lock_path = tmp_path / "deployment.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    flock_operations: list[int] = []
    real_fstat = operations.os.fstat

    def fake_fstat(value: int) -> object:
        metadata = real_fstat(value)
        return type(
            "Metadata",
            (),
            {
                "st_mode": metadata.st_mode,
                "st_uid": 0,
                "st_gid": 0,
            },
        )()

    try:
        monkeypatch.setattr(operations, "DEPLOYMENT_LOCK_PATH", lock_path)
        monkeypatch.setenv(operations.DEPLOYMENT_LOCK_FD_ENV, str(descriptor))
        monkeypatch.setattr(operations.os, "readlink", lambda _path: str(lock_path))
        monkeypatch.setattr(operations.os, "fstat", fake_fstat)
        monkeypatch.setattr(
            operations.fcntl,
            "flock",
            lambda _fd, operation: flock_operations.append(operation),
        )

        with operations.deployment_identity_lock() as inherited_fd:
            assert inherited_fd == descriptor

        assert flock_operations == [operations.fcntl.LOCK_EX | operations.fcntl.LOCK_NB]
    finally:
        os.close(descriptor)


def test_inherited_deployment_lock_rejects_invalid_descriptor_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    lock_path = tmp_path / "deployment.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        monkeypatch.setattr(operations, "DEPLOYMENT_LOCK_PATH", lock_path)
        monkeypatch.setenv(operations.DEPLOYMENT_LOCK_FD_ENV, "not-a-descriptor")
        with (
            pytest.raises(operations.OperationError, match="descriptor is invalid"),
            operations.deployment_identity_lock(),
        ):
            pass

        monkeypatch.setenv(operations.DEPLOYMENT_LOCK_FD_ENV, str(descriptor))
        monkeypatch.setattr(operations.os, "readlink", lambda _path: "/run/wrong.lock")
        with (
            pytest.raises(operations.OperationError, match="boundary is unsafe"),
            operations.deployment_identity_lock(),
        ):
            pass

        monkeypatch.setattr(operations.os, "readlink", lambda _path: str(lock_path))
        monkeypatch.setattr(
            operations.os,
            "fstat",
            lambda _fd: type(
                "Metadata",
                (),
                {"st_mode": operations.stat.S_IFREG | 0o666, "st_uid": 0, "st_gid": 0},
            )(),
        )
        with (
            pytest.raises(operations.OperationError, match="boundary is unsafe"),
            operations.deployment_identity_lock(),
        ):
            pass

        monkeypatch.setattr(
            operations.os,
            "fstat",
            lambda _fd: type(
                "Metadata",
                (),
                {"st_mode": operations.stat.S_IFREG | 0o600, "st_uid": 0, "st_gid": 0},
            )(),
        )
        monkeypatch.setattr(
            operations.fcntl,
            "flock",
            lambda *_args: (_ for _ in ()).throw(BlockingIOError()),
        )
        with (
            pytest.raises(operations.OperationError, match="is not held"),
            operations.deployment_identity_lock(),
        ):
            pass
    finally:
        os.close(descriptor)


def test_shell_deployment_lock_descriptor_survives_exec(tmp_path: Path) -> None:
    if not Path("/proc/self/fd").exists():
        pytest.skip("Linux /proc descriptor verification is required")
    lock_path = tmp_path / "deployment.lock"
    completed = subprocess.run(
        [
            "bash",
            "-ceu",
            """
            umask 077
            exec 8>"$1"
            flock -n 8
            exec bash -ceu '
              test "$(readlink /proc/$$/fd/8)" = "$1"
              flock -n 8
            ' inherited "$1"
            """,
            "parent",
            str(lock_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert lock_path.stat().st_mode & 0o777 == 0o600
