"""Tests for the Codex daily-routine status handoff."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "codex-routine-status.py"
SPEC = importlib.util.spec_from_file_location("codex_routine_status", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)


def test_begin_preserves_success_and_records_running_attempt(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"last_success_unixtime": 50}), encoding="utf-8")

    record = status.begin(path, now=100)

    assert record == {
        "schema_version": 1,
        "last_attempt_unixtime": 100,
        "last_success_unixtime": 50,
        "last_attempt_success": 0,
        "run_in_progress": 1,
        "last_result": "running",
    }
    assert json.loads(path.read_text(encoding="utf-8")) == record
    assert path.stat().st_mode & 0o777 == 0o600


def test_successful_finish_advances_last_success(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    status.begin(path, now=100)

    record = status.finish(path, "success", now=120)

    assert record["last_attempt_unixtime"] == 100
    assert record["last_success_unixtime"] == 120
    assert record["last_attempt_success"] == 1
    assert record["run_in_progress"] == 0
    assert record["last_result"] == "success"


def test_failed_finish_preserves_last_success(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps(
            {
                "last_attempt_unixtime": 100,
                "last_success_unixtime": 80,
                "last_attempt_success": 0,
            }
        ),
        encoding="utf-8",
    )

    record = status.finish(path, "exit-code", now=120)

    assert record["last_attempt_unixtime"] == 100
    assert record["last_success_unixtime"] == 80
    assert record["last_attempt_success"] == 0
    assert record["run_in_progress"] == 0
    assert record["last_result"] == "exit-code"


def test_finish_rejects_unknown_result_without_mutating_file(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text('{"last_success_unixtime": 80}\n', encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(status.RoutineStatusError, match="unrecognized"):
        status.finish(path, "not-a-systemd-result", now=120)

    assert path.read_bytes() == before


def test_invalid_existing_status_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(status.RoutineStatusError, match="cannot read valid"):
        status.begin(path, now=100)
