"""Supervise the Go lease-recovery process and expose its existing metrics.

Scheduling, queue recovery and lifecycle classification belong to Go. This
adapter only transports configuration and metrics until the worker host is Go.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from typing import Any

import structlog

from src.config import settings
from src.metrics import (
    inflight_deadletter_depth,
    inflight_depth,
    inflight_reaped_total,
    monitor_deadletter_lifecycle_depth,
)

log = structlog.get_logger()
LIFECYCLES = ("actionable", "retired", "superseded", "unresolved")
OUTCOMES = ("reenqueued", "dead_lettered", "missing_config")


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise RuntimeError("Invalid Go reaper metric count")
    return value


def publish_reaper_event(event: dict, reaper_log: Any) -> None:
    """Retain metric labels and last good gauges across observation failures."""
    kind = event.get("event")
    if type(event.get("failed")) is not bool:
        raise RuntimeError("Invalid Go reaper event status")
    if kind == "reaper.sweep":
        wtype = event.get("wtype")
        if wtype not in {"simple", "browser"}:
            raise RuntimeError("Invalid Go reaper worker type")
        if event["failed"]:
            reaper_log.warning("pipeline.reaper.error", wtype=wtype)
            return
        result = {key: _count(event["result"][key]) for key in OUTCOMES}
        for outcome, value in result.items():
            if value:
                inflight_reaped_total.labels(wtype=wtype, outcome=outcome).inc(value)
        if any(result.values()):
            reaper_log.info("pipeline.reaper.swept", wtype=wtype, **result)
        if "inflight" in event and "deadletters" in event:
            inflight_depth.labels(wtype=wtype).set(_count(event["inflight"]))
            inflight_deadletter_depth.labels(wtype=wtype).set(_count(event["deadletters"]))
    elif kind == "reaper.lifecycle":
        if event["failed"]:
            reaper_log.warning("pipeline.deadletters.classification_failed")
            return
        counts = event["counts"]
        validated = {
            (wtype, lifecycle): _count(counts[wtype][lifecycle])
            for wtype in ("simple", "browser")
            for lifecycle in LIFECYCLES
        }
        for (wtype, lifecycle), value in validated.items():
            monitor_deadletter_lifecycle_depth.labels(wtype=wtype, lifecycle=lifecycle).set(value)
    else:
        raise RuntimeError("Unknown Go reaper event")


async def run_go_reaper(shutdown_event: asyncio.Event, *, browser: bool) -> None:
    if shutdown_event.is_set():
        return
    reaper_log = log.bind(component="reaper", browser=browser)
    process = await asyncio.create_subprocess_exec(
        "go-typesense-exporter",
        "--reap-leases",
        stdout=asyncio.subprocess.PIPE,
        env={
            **os.environ,
            "REAPER_INTERVAL_SECONDS": str(settings.reaper_interval_seconds),
            "REAPER_BATCH_SIZE": str(settings.reaper_batch_size),
            "REAPER_MAX_STRIKES": str(settings.reaper_max_strikes),
        },
    )
    reaper_log.info("pipeline.reaper.started", engine="go")

    async def consume() -> None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            event = json.loads(line)
            if not isinstance(event, dict):
                raise RuntimeError("Invalid Go reaper event")
            publish_reaper_event(event, reaper_log)

    reader = asyncio.create_task(consume(), name="go-reaper-metrics")
    stopped = asyncio.create_task(shutdown_event.wait(), name="go-reaper-shutdown")
    try:
        done, _ = await asyncio.wait({reader, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if reader in done:
            await reader
            if not shutdown_event.is_set():
                # The pipeline already treats an early reaper-task exit as a
                # worker failure and drains/restarts the instance.
                raise RuntimeError("Go lease reaper exited unexpectedly")
    finally:
        reader.cancel()
        stopped.cancel()
        await asyncio.gather(reader, stopped, return_exceptions=True)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.communicate(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.communicate()
        else:
            await process.wait()
        reaper_log.info("pipeline.reaper.stopped", engine="go")
