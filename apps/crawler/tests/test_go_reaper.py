from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from src.workers import go_reaper

ROOT = Path(__file__).resolve().parents[1]


def test_reaper_script_and_python_oracle_are_current():
    directory = ROOT / "go/typesense-exporter"
    assert (directory / "lease_reaper.lua").read_bytes() == (
        ROOT / "src/lua/reap_expired.lua"
    ).read_bytes()
    subprocess.run(
        [sys.executable, str(directory / "testdata/generate_reaper_fixture.py"), "--check"],
        check=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


@pytest.fixture
def metrics(monkeypatch):
    result = {}
    for name in (
        "inflight_reaped_total",
        "inflight_depth",
        "inflight_deadletter_depth",
        "monitor_deadletter_lifecycle_depth",
    ):
        result[name] = Mock()
        monkeypatch.setattr(go_reaper, name, result[name])
    return result


def test_metric_names_and_failure_retention(metrics):
    log = Mock()
    go_reaper.publish_reaper_event(
        {
            "event": "reaper.sweep",
            "wtype": "browser",
            "failed": False,
            "result": {"reenqueued": 2, "dead_lettered": 1, "missing_config": 0},
            "inflight": 8,
            "deadletters": 3,
        },
        log,
    )
    metrics["inflight_reaped_total"].labels.assert_any_call(wtype="browser", outcome="reenqueued")
    assert metrics["inflight_reaped_total"].labels.return_value.inc.call_count == 2
    metrics["inflight_depth"].labels.return_value.set.assert_called_once_with(8)
    metrics["inflight_deadletter_depth"].labels.return_value.set.assert_called_once_with(3)
    go_reaper.publish_reaper_event(
        {"event": "reaper.sweep", "wtype": "browser", "failed": True}, log
    )
    assert metrics["inflight_depth"].labels.return_value.set.call_count == 1
    go_reaper.publish_reaper_event(
        {
            "event": "reaper.lifecycle",
            "failed": False,
            "counts": {
                wtype: {lifecycle: 0 for lifecycle in go_reaper.LIFECYCLES}
                for wtype in ("simple", "browser")
            },
        },
        log,
    )
    assert metrics["monitor_deadletter_lifecycle_depth"].labels.return_value.set.call_count == 8
    go_reaper.publish_reaper_event({"event": "reaper.lifecycle", "failed": True}, log)
    assert metrics["monitor_deadletter_lifecycle_depth"].labels.return_value.set.call_count == 8


@pytest.mark.parametrize(
    "event",
    [
        {"event": "unknown", "failed": False},
        {"event": "reaper.sweep", "wtype": "bad", "failed": True},
        {"event": "reaper.sweep", "wtype": "simple", "failed": False, "result": {"reenqueued": -1}},
        {"event": "reaper.lifecycle", "failed": "false"},
    ],
)
def test_invalid_child_protocol_fails(event):
    with pytest.raises(RuntimeError):
        go_reaper.publish_reaper_event(event, Mock())


@pytest.fixture
async def child(monkeypatch):
    process = Mock(returncode=None)
    process.stdout = asyncio.StreamReader()
    started = asyncio.Event()

    async def launch(*args, **kwargs):
        assert args == ("go-typesense-exporter", "--reap-leases")
        assert kwargs["env"]["REAPER_BATCH_SIZE"] == str(go_reaper.settings.reaper_batch_size)
        started.set()
        return process

    async def communicate():
        process.returncode = -15
        return b"", None

    process.communicate = AsyncMock(side_effect=communicate)
    process.wait = AsyncMock(return_value=0)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=launch))
    return process, started


async def test_child_shutdown_reaps_and_drains(child, metrics):
    process, started = child
    shutdown = asyncio.Event()
    task = asyncio.create_task(go_reaper.run_go_reaper(shutdown, browser=False))
    await started.wait()
    process.stdout.feed_data(
        json.dumps({"event": "reaper.sweep", "wtype": "simple", "failed": True}).encode() + b"\n"
    )
    shutdown.set()
    await task
    process.terminate.assert_called_once()
    process.communicate.assert_awaited_once()


async def test_child_cancellation_reaps(child):
    process, started = child
    task = asyncio.create_task(go_reaper.run_go_reaper(asyncio.Event(), browser=True))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process.terminate.assert_called_once()
    process.communicate.assert_awaited_once()


async def test_unexpected_child_exit_surfaces_to_pipeline(child):
    process, _ = child
    process.returncode = 0
    process.stdout.feed_eof()
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        await go_reaper.run_go_reaper(asyncio.Event(), browser=False)
    process.wait.assert_awaited_once()
    process.terminate.assert_not_called()


async def test_unresponsive_child_is_killed(child):
    process, started = child
    process.communicate = AsyncMock(side_effect=[TimeoutError(), (b"", None)])
    shutdown = asyncio.Event()
    task = asyncio.create_task(go_reaper.run_go_reaper(shutdown, browser=False))
    await started.wait()
    shutdown.set()
    await task
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.communicate.await_count == 2


async def test_stopped_pipeline_does_not_launch(monkeypatch):
    launch = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    shutdown = asyncio.Event()
    shutdown.set()
    await go_reaper.run_go_reaper(shutdown, browser=False)
    launch.assert_not_awaited()
