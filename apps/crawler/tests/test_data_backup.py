from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "jobseek-data-backup.py"
SPEC = importlib.util.spec_from_file_location("jobseek_data_backup", SCRIPT_PATH)
assert SPEC and SPEC.loader
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def test_execute_with_status_preserves_last_success_on_failure(tmp_path: Path) -> None:
    previous = {
        "last_success_at": "2026-07-21T01:00:00+00:00",
        "last_success_unix": 1_774_000_000,
    }
    (tmp_path / "postgresql.json").write_text(json.dumps(previous), encoding="utf-8")

    def fail() -> dict[str, object]:
        raise backup.BackupError("token=should-not-leak")

    with pytest.raises(backup.BackupError):
        backup.execute_with_status("postgresql", fail, status_dir=tmp_path)

    record = json.loads((tmp_path / "postgresql.json").read_text(encoding="utf-8"))
    assert record["success"] is False
    assert record["last_success_unix"] == previous["last_success_unix"]
    assert "should-not-leak" not in record["error"]
    assert "<redacted>" in record["error"]
    assert 'jobseek_backup_last_attempt_success{service="postgresql"} 0' in (
        tmp_path / "postgresql.prom"
    ).read_text(encoding="utf-8")


def test_execute_with_status_records_a_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    instants = iter(
        (
            datetime(2026, 7, 22, 1, 0, tzinfo=UTC),
            datetime(2026, 7, 22, 1, 2, 3, tzinfo=UTC),
        )
    )
    monkeypatch.setattr(backup, "utc_now", lambda: next(instants))

    record = backup.execute_with_status(
        "postgresql",
        lambda: {"backup_label": "20260722-010000F"},
        status_dir=tmp_path,
    )

    assert record["success"] is True
    assert record["duration_seconds"] == 123
    assert record["last_success_at"] == "2026-07-22T01:02:03+00:00"
    assert record["backup_label"] == "20260722-010000F"
    assert 'jobseek_backup_last_attempt_success{service="postgresql"} 1' in (
        tmp_path / "postgresql.prom"
    ).read_text(encoding="utf-8")


def test_postgres_auto_uses_full_on_sunday(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    info = [
        {
            "backup": [
                {
                    "label": "20260726-010000F",
                    "type": "full",
                    "info": {"size": 1234, "repository": {"size": 567}},
                    "timestamp": {"stop": 1_774_555_555},
                }
            ]
        }
    ]

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return completed("true\n")
        if "info" in argv:
            return completed(json.dumps(info))
        return completed()

    monkeypatch.setattr(backup, "run_checked", fake_run)
    monkeypatch.setattr(backup, "utc_now", lambda: datetime(2026, 7, 26, tzinfo=UTC))

    result = backup.postgres_backup("auto")

    assert result["backup_type"] == "full"
    assert result["backup_database_bytes"] == 1234
    assert result["backup_repository_bytes"] == 567
    assert any("--type=full" in command for command in commands)
    assert sum("check" in command for command in commands) == 2


def test_typesense_requires_root_only_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESENSE_API_KEY", raising=False)
    with pytest.raises(backup.BackupError, match="TYPESENSE_API_KEY is missing"):
        backup.typesense_backup()


def test_restic_command_injects_the_restricted_sftp_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESTIC_SFTP_COMMAND", "ssh -i /root-only/key -p 23")
    assert backup._restic_command("check") == [
        "restic",
        "-o",
        "sftp.command=ssh -i /root-only/key -p 23",
        "check",
    ]


def test_typesense_backup_snapshots_uploads_validates_and_cleans(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    staging_parent = tmp_path / "typesense"
    monkeypatch.setenv("TYPESENSE_API_KEY", "test-key")
    monkeypatch.setenv("RESTIC_REPOSITORY", "sftp:relative-repository")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", "/root-only/password")
    monkeypatch.setenv("RESTIC_SFTP_COMMAND", "ssh -i /root-only/key -p 23")
    monkeypatch.setenv("TYPESENSE_SNAPSHOT_HOST_ROOT", str(staging_parent))
    monkeypatch.setattr(backup, "_snapshot_request", lambda *_: None)
    monkeypatch.setattr(
        backup,
        "utc_now",
        lambda: datetime(2026, 7, 22, 2, 0, tzinfo=UTC),
    )

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return completed("true\n")
        if argv[:2] == ["docker", "cp"]:
            destination = Path(argv[-1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "state.bin").write_bytes(b"consistent-snapshot")
        if "snapshots" in argv:
            return completed(
                json.dumps(
                    [
                        {
                            "id": "1234567890abcdef",
                            "short_id": "12345678",
                            "time": "2026-07-22T02:00:05Z",
                        }
                    ]
                )
            )
        return completed()

    monkeypatch.setattr(backup, "run_checked", fake_run)

    result = backup.typesense_backup()

    assert result["snapshot_bytes"] == len(b"consistent-snapshot")
    assert result["repository_snapshot_id"] == "12345678"
    assert not (staging_parent / "staging" / "20260722T020000Z").exists()
    assert any("backup" in command for command in commands)
    assert any("forget" in command and "--prune" in command for command in commands)
    assert any("check" in command for command in commands)


def test_redact_removes_common_secret_shapes() -> None:
    value = backup.redact("api_key=abc password: def Authorization: Bearer ghi")
    assert "abc" not in value
    assert "def" not in value
    assert "ghi" not in value
