from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
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


def test_identity_requires_exact_deployed_revision_and_every_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operations = load_operations()
    deployed_sha = "a" * 40
    deployed_path = tmp_path / "deployed-sha"
    deployed_path.write_text(deployed_sha + "\n", encoding="utf-8")
    artifacts = {
        name: tmp_path / name
        for name in ("data_backup", "operations", "restore_drill", "service", "timer")
    }
    for name, path in artifacts.items():
        path.write_text(f"{name}\n", encoding="utf-8")
    expected = operations.ExpectedIdentity(
        deploy_sha=deployed_sha,
        artifact_sha256={name: operations.sha256_file(path) for name, path in artifacts.items()},
    )
    monkeypatch.setattr(operations, "DEPLOYED_SHA_PATH", deployed_path)
    monkeypatch.setattr(operations, "ARTIFACT_PATHS", artifacts)

    actual = operations.validate_identity(expected)

    assert actual == {
        str(artifacts[name]): expected.artifact_sha256[name] for name in sorted(artifacts)
    }
    artifacts["restore_drill"].write_text("tampered\n", encoding="utf-8")
    with pytest.raises(operations.OperationError, match="restore_drill"):
        operations.validate_identity(expected)


def test_enable_timer_requires_disabled_and_inactive_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    expected = operations.ExpectedIdentity("a" * 40, {})
    commands: list[list[str]] = []
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


def test_enable_timer_rolls_back_partial_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    expected = operations.ExpectedIdentity("a" * 40, {})
    states = iter((("disabled", "inactive"), ("enabled", "active")))
    rollback_commands: list[list[str]] = []
    monkeypatch.setattr(
        operations,
        "validate_activation_evidence",
        lambda _expected: {"table_count": 1, "row_count": 2},
    )
    monkeypatch.setattr(operations, "timer_state", lambda: next(states))
    monkeypatch.setattr(operations, "command_succeeds", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(operations, "validate_identity", lambda _expected: {})

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
    expected = operations.ExpectedIdentity("a" * 40, {})
    states = iter((("disabled", "inactive"), ("enabled", "active")))
    rollback_commands: list[list[str]] = []
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
        "run_checked",
        lambda argv, **_kwargs: (
            "2030-01-01 00:30:00 UTC\n" if "NextElapseUSecRealtime" in " ".join(argv) else ""
        ),
    )
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda argv, **_kwargs: (
            rollback_commands.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        ),
    )

    operations.enable_timer(expected)

    assert rollback_commands == []


def test_enable_timer_rolls_back_post_enable_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = load_operations()
    expected = operations.ExpectedIdentity("a" * 40, {})
    states = iter((("disabled", "inactive"), ("enabled", "active")))
    rollback_commands: list[list[str]] = []
    monkeypatch.setattr(
        operations,
        "validate_activation_evidence",
        lambda _expected: {"table_count": 1, "row_count": 2},
    )
    monkeypatch.setattr(operations, "timer_state", lambda: next(states))
    monkeypatch.setattr(operations, "command_succeeds", lambda *_args, **_kwargs: False)
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
