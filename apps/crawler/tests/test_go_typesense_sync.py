"""The post-commit Typesense stage runs in Go and propagates failure/cancellation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sync import CompanyTypesenseSyncError, sync_typesense


@pytest.mark.parametrize("status", [0, 1, 130])
async def test_postcommit_go_sync_preserves_exit_status(status: int) -> None:
    process = MagicMock(returncode=status)
    process.wait = AsyncMock(return_value=status)
    with patch("src.sync.asyncio.create_subprocess_exec", new_callable=AsyncMock) as execute:
        execute.return_value = process
        if status:
            with pytest.raises(CompanyTypesenseSyncError):
                await sync_typesense(AsyncMock(), MagicMock())
        else:
            await sync_typesense(AsyncMock(), MagicMock())
    execute.assert_awaited_once_with("go-typesense-exporter", "--sync-taxonomies")


async def test_missing_go_sync_binary_fails_closed() -> None:
    with (
        patch("src.sync.asyncio.create_subprocess_exec", side_effect=FileNotFoundError),
        pytest.raises(CompanyTypesenseSyncError, match="could not start"),
    ):
        await sync_typesense(AsyncMock(), MagicMock())


async def test_cancelled_go_sync_child_is_terminated_and_reaped() -> None:
    started = asyncio.Event()
    ended = asyncio.Event()
    process = MagicMock(returncode=None)

    async def wait() -> int:
        started.set()
        await ended.wait()
        return 130

    process.wait = AsyncMock(side_effect=wait)
    process.terminate.side_effect = ended.set
    with patch("src.sync.asyncio.create_subprocess_exec", new_callable=AsyncMock) as execute:
        execute.return_value = process
        task = asyncio.create_task(sync_typesense(AsyncMock(), MagicMock()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    process.terminate.assert_called_once()
    assert process.wait.await_count == 2
    process.kill.assert_not_called()


def test_go_sync_fixture_matches_retained_python_producer() -> None:
    import subprocess
    import sys
    from pathlib import Path

    crawler = Path(__file__).resolve().parents[1]
    script = crawler / "go/typesense-exporter/testdata/generate_sync_fixture.py"
    subprocess.run([sys.executable, str(script), "--check"], cwd=crawler, check=True)
