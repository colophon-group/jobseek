"""Behavioral coverage for the cross-process labeller DB budget lock."""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.labeller.cli import LabellerDatabaseLockError, _database_process_lock

CRAWLER_ROOT = Path(__file__).resolve().parents[1]

_LOCK_WORKER = """
import sys
import time
from pathlib import Path

from src.labeller.cli import _database_process_lock

worker = sys.argv[1]
events = Path(sys.argv[2])
with _database_process_lock("sample"):
    with events.open("a", encoding="utf-8") as handle:
        handle.write(f"start {worker}\\n")
        handle.flush()
    time.sleep(0.15)
    with events.open("a", encoding="utf-8") as handle:
        handle.write(f"end {worker}\\n")
        handle.flush()
"""


def test_database_process_lock_serializes_multiple_labeller_children(tmp_path: Path) -> None:
    lock_path = tmp_path / "labeller-postgresql.lock"
    events_path = tmp_path / "events.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(CRAWLER_ROOT),
        "CRAWLER_DB_ROLE": "labeller",
        "JOBSEEK_LABELLER_DB_LOCK_FILE": str(lock_path),
        "JOBSEEK_LABELLER_DB_LOCK_TIMEOUT_SECONDS": "5",
    }
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _LOCK_WORKER, str(index), str(events_path)],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(3)
    ]

    failures: list[str] = []
    for child in children:
        stdout, stderr = child.communicate(timeout=10)
        if child.returncode != 0:
            failures.append(f"rc={child.returncode} stdout={stdout!r} stderr={stderr!r}")
    assert failures == []

    active: set[str] = set()
    maximum_active = 0
    starts = 0
    for event in events_path.read_text(encoding="utf-8").splitlines():
        action, worker = event.split()
        if action == "start":
            starts += 1
            active.add(worker)
            maximum_active = max(maximum_active, len(active))
        else:
            assert action == "end"
            assert worker in active
            active.remove(worker)

    assert starts == 3
    assert maximum_active == 1
    assert active == set()


def test_labeller_database_command_fails_closed_without_shared_lock(monkeypatch) -> None:
    monkeypatch.setenv("CRAWLER_DB_ROLE", "labeller")
    monkeypatch.delenv("JOBSEEK_LABELLER_DB_LOCK_FILE", raising=False)

    with (
        pytest.raises(LabellerDatabaseLockError, match="DB_LOCK_FILE is required"),
        _database_process_lock("prepare-pre-llm"),
    ):
        pass


def test_database_process_lock_wait_is_bounded(tmp_path: Path) -> None:
    lock_path = tmp_path / "labeller-postgresql.lock"
    events_path = tmp_path / "events.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(CRAWLER_ROOT),
        "CRAWLER_DB_ROLE": "labeller",
        "JOBSEEK_LABELLER_DB_LOCK_FILE": str(lock_path),
        "JOBSEEK_LABELLER_DB_LOCK_TIMEOUT_SECONDS": "0.1",
    }

    with lock_path.open("a+", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        result = subprocess.run(
            [sys.executable, "-c", _LOCK_WORKER, "contender", str(events_path)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    assert result.returncode != 0
    assert "timed out waiting for another labeller database process" in result.stderr
    assert not events_path.exists()


def test_non_database_command_does_not_contend_for_database_lock(monkeypatch) -> None:
    monkeypatch.setenv("CRAWLER_DB_ROLE", "labeller")
    monkeypatch.delenv("JOBSEEK_LABELLER_DB_LOCK_FILE", raising=False)

    with _database_process_lock("validate"):
        pass
