"""Pass committed local board effects to Go; queue mutation belongs to Go."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os

from src.config import settings


async def publish_board_queues(effects: dict) -> None:
    payload = json.dumps(effects, allow_nan=False).encode()
    if len(payload) > 128 << 20:
        raise RuntimeError("Go board sync input exceeds limit")
    try:
        process = await asyncio.create_subprocess_exec(
            "go-typesense-exporter",
            "--sync-board-queues",
            stdin=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "REDIS_URL": settings.redis_url,
                "THROTTLE_DELAY_DEFAULT": str(settings.throttle_delay_default),
                "THROTTLE_DELAY_ATS": str(settings.throttle_delay_ats),
            },
        )
    except OSError:
        raise RuntimeError("Go board queue publisher could not start") from None
    try:
        await asyncio.wait_for(process.communicate(payload), timeout=330)
    except (asyncio.CancelledError, TimeoutError):
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.communicate(), timeout=10)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.communicate()
        raise
    if process.returncode != 0:
        raise RuntimeError("Go board queue publication failed")
