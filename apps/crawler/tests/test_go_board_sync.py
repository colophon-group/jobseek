"""Go receives only committed board effects and propagates failure to sync."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.redis_queue import MonitorSchedule
from src.runtime.go_board_sync import publish_board_queues
from src.sync import BoardSyncEffects, apply_board_redis_effects


async def test_committed_effects_are_sent_to_go_with_configured_delays(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "redis_url", "redis://localhost:12345/2")
    monkeypatch.setattr(settings, "throttle_delay_default", 3.5)
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(None, None))
    effects = BoardSyncEffects(
        schedules=(
            MonitorSchedule("example.test", "board", 1234.5, {"metadata": "{}"}, True, False),
        ),
        orphan_monitors=(("retired.test", "old"),),
    )
    with patch(
        "src.runtime.go_board_sync.asyncio.create_subprocess_exec", new_callable=AsyncMock
    ) as execute:
        execute.return_value = process
        await apply_board_redis_effects(effects)
    assert execute.await_args.args == ("go-typesense-exporter", "--sync-board-queues")
    assert execute.await_args.kwargs["env"]["REDIS_URL"] == "redis://localhost:12345/2"
    assert execute.await_args.kwargs["env"]["THROTTLE_DELAY_DEFAULT"] == "3.5"
    assert json.loads(process.communicate.await_args.args[0]) == {
        "schedules": [
            {
                "domain": "example.test",
                "board_id": "board",
                "next_check_at": 1234.5,
                "config": {"metadata": "{}"},
                "browser": True,
                "first_time": False,
            }
        ],
        "orphans": [["retired.test", "old"]],
    }


@pytest.mark.parametrize("status", [1, 130])
async def test_queue_publication_failure_aborts_sync(status):
    process = MagicMock(returncode=status)
    process.communicate = AsyncMock(return_value=(None, None))
    with (
        patch("src.runtime.go_board_sync.asyncio.create_subprocess_exec", return_value=process),
        pytest.raises(RuntimeError, match="publication failed"),
    ):
        await publish_board_queues({"schedules": [], "orphans": []})


async def test_missing_queue_publisher_never_falls_back_to_python():
    with (
        patch(
            "src.runtime.go_board_sync.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ),
        pytest.raises(RuntimeError, match="could not start"),
    ):
        await publish_board_queues({})


async def test_cancelled_queue_publisher_is_terminated_and_reaped():
    started = asyncio.Event()
    process = MagicMock(returncode=None)

    async def communicate(payload=None):
        if payload is not None:
            started.set()
            await asyncio.Future()
        return None, None

    process.communicate = AsyncMock(side_effect=communicate)
    with patch("src.runtime.go_board_sync.asyncio.create_subprocess_exec", return_value=process):
        task = asyncio.create_task(publish_board_queues({}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    process.terminate.assert_called_once()
    assert process.communicate.await_count == 2


def test_board_sync_redis_oracle_matches_python():
    crawler = Path(__file__).resolve().parents[1]
    script = crawler / "go/typesense-exporter/testdata/generate_board_sync_fixture.py"
    subprocess.run([sys.executable, str(script), "--check"], cwd=crawler, check=True)
