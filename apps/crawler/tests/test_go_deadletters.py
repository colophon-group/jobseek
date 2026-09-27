from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from src import deadletters

ROOT = Path(__file__).resolve().parents[1]


def test_python_oracle_is_current():
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "go/typesense-exporter/testdata/generate_deadletter_fixture.py"),
            "--check",
        ],
        check=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


async def test_go_classifier_decodes_complete_entries(monkeypatch):
    expected = json.loads(
        (ROOT / "go/typesense-exporter/testdata/deadletter_fixture.json").read_text()
    )["expected"]
    process = Mock(returncode=0)
    process.communicate = AsyncMock(return_value=(json.dumps(expected).encode(), None))
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    entries = await deadletters.classify_deadletters(Mock())
    assert [entry.to_dict() for entry in entries] == expected["entries"]
    assert deadletters.lifecycle_counts(entries) == expected["counts"]
    launch.assert_awaited_once_with(
        "go-typesense-exporter", "--inspect-deadletters", stdout=asyncio.subprocess.PIPE
    )
    process.terminate.assert_not_called()


async def test_go_classifier_failure_has_no_python_fallback(monkeypatch):
    process = Mock(returncode=1)
    process.communicate = AsyncMock(return_value=(b"", None))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    db = AsyncMock()
    with pytest.raises(RuntimeError, match="Go deadletter inspection failed"):
        await deadletters.classify_deadletters(db)
    db.fetch.assert_not_awaited()


async def test_go_classifier_cancellation_reaps_child(monkeypatch):
    process = Mock(returncode=None)
    started = asyncio.Event()

    calls = 0

    async def communicate():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await asyncio.Event().wait()
        process.returncode = -15
        return b"", None

    process.communicate = AsyncMock(side_effect=communicate)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(deadletters.classify_deadletters(Mock()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process.terminate.assert_called_once()
    assert process.communicate.await_count == 2


async def test_inspect_cli_execs_before_python_pools(monkeypatch):
    from src import cli

    monkeypatch.setattr(sys, "argv", ["crawler", "deadletters", "inspect"])

    class ExecComplete(Exception):
        pass

    def execute(binary, args):
        assert (binary, args) == (
            "go-typesense-exporter",
            ["go-typesense-exporter", "--inspect-deadletters"],
        )
        raise ExecComplete

    monkeypatch.setattr(cli.os, "execvp", execute)
    with pytest.raises(ExecComplete):
        await cli.run()
