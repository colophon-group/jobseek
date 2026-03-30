"""R2 drain worker — producer-consumer pipeline for uploading descriptions to R2.

Producer claims rows atomically (``r2_uploaded = false`` → ``NULL``) and feeds
them into an asyncio.Queue buffer. Consumers pop from the buffer, PUT to R2,
and mark ``r2_uploaded = true``. On failure, rows revert to ``false``.

Three-state ``r2_uploaded``:
- ``false``: pending upload (workers write this)
- ``NULL``: claimed by producer, in-flight
- ``true``: uploaded to R2

Tuning knobs (env vars via config):
- ``DRAIN_PRODUCERS``: number of producer coroutines (default 1)
- ``DRAIN_CONSUMERS``: number of consumer coroutines (default 30)
- ``DRAIN_BUFFER_SIZE``: asyncio.Queue maxsize (default 200)
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import asyncpg
import structlog

from src.config import settings
from src.core.description_store import put_description
from src.metrics import r2_upload_duration, r2_uploaded_total

log = structlog.get_logger()

_FETCH_BATCH = 50


async def _producer(
    producer_id: int,
    local_pool: asyncpg.Pool,
    buffer: asyncio.Queue,
    shutdown_event: asyncio.Event,
    drain_log: structlog.stdlib.BoundLogger,
) -> None:
    """Claim pending rows (false → NULL) and feed into buffer."""
    plog = drain_log.bind(producer=producer_id)
    while not shutdown_event.is_set():
        try:
            # Atomically claim a batch: false → NULL
            async with local_pool.acquire() as conn:
                rows = await conn.fetch(
                    "UPDATE descriptions SET r2_uploaded = NULL "
                    "WHERE (posting_id, locale) IN ("
                    "  SELECT posting_id, locale FROM descriptions "
                    "  WHERE r2_uploaded = false "
                    "  LIMIT $1"
                    ") RETURNING posting_id, locale, html, hash",
                    _FETCH_BATCH,
                )
        except Exception:
            plog.warning("r2_drain.producer_fetch_error", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=2.0)
            continue

        if not rows:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=2.0)
            continue

        for row in rows:
            if shutdown_event.is_set():
                return
            await buffer.put(row)


async def _consumer(
    consumer_id: int,
    local_pool: asyncpg.Pool,
    buffer: asyncio.Queue,
    shutdown_event: asyncio.Event,
    drain_log: structlog.stdlib.BoundLogger,
    stats: dict,
) -> None:
    """Pop from buffer, PUT to R2, mark true. On failure revert to false."""
    while not shutdown_event.is_set():
        try:
            row = await asyncio.wait_for(buffer.get(), timeout=1.0)
        except TimeoutError:
            continue

        t0 = time.monotonic()
        try:
            await put_description(str(row["posting_id"]), row["locale"], row["html"])

            await local_pool.execute(
                "UPDATE descriptions SET r2_uploaded = true WHERE posting_id = $1 AND locale = $2",
                row["posting_id"],
                row["locale"],
            )

            await local_pool.execute(
                "UPDATE job_posting SET description_r2_hash = $2, "
                "to_be_enriched = true, "
                "updated_at = CASE WHEN description_r2_hash IS DISTINCT FROM $2 "
                "THEN now() ELSE updated_at END "
                "WHERE id = $1",
                row["posting_id"],
                row["hash"],
            )

            stats["uploaded"] += 1
            stats["total_time"] += time.monotonic() - t0
            r2_uploaded_total.labels(status="succeeded").inc()
            r2_upload_duration.observe(time.monotonic() - t0)

        except Exception:
            drain_log.warning(
                "r2_drain.consumer_error",
                posting_id=str(row["posting_id"]),
                exc_info=True,
            )
            stats["errors"] += 1
            r2_uploaded_total.labels(status="failed").inc()
            # Revert to pending so it gets retried
            with contextlib.suppress(Exception):
                await local_pool.execute(
                    "UPDATE descriptions SET r2_uploaded = false "
                    "WHERE posting_id = $1 AND locale = $2",
                    row["posting_id"],
                    row["locale"],
                )
        finally:
            buffer.task_done()


async def r2_drain_loop(
    local_pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
    consumer_name: str = "drain-0",
) -> None:
    """R2 drain: producer-consumer pipeline with atomic row claiming."""
    producers = getattr(settings, "drain_producers", 1)
    consumers = getattr(settings, "drain_consumers", 30)
    buf_size = getattr(settings, "drain_buffer_size", 200)

    drain_log = log.bind(name=consumer_name)
    drain_log.info(
        "r2_drain.started",
        producers=producers,
        consumers=consumers,
        buffer_size=buf_size,
    )

    buffer: asyncio.Queue = asyncio.Queue(maxsize=buf_size)
    stats = {"uploaded": 0, "errors": 0, "total_time": 0.0}
    last_report = time.monotonic()
    last_uploaded = 0

    async def _reporter() -> None:
        nonlocal last_report, last_uploaded
        while not shutdown_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=10.0)
            now = time.monotonic()
            interval = now - last_report
            uploaded_delta = stats["uploaded"] - last_uploaded
            rate = uploaded_delta / interval if interval > 0 else 0
            avg_latency = stats["total_time"] / stats["uploaded"] if stats["uploaded"] > 0 else 0
            drain_log.info(
                "r2_drain.stats",
                uploaded=stats["uploaded"],
                errors=stats["errors"],
                rate=round(rate, 1),
                avg_latency_ms=round(avg_latency * 1000, 1),
                buffer=buffer.qsize(),
                buffer_pct=round(buffer.qsize() / buf_size * 100, 1) if buf_size else 0,
            )
            last_report = now
            last_uploaded = stats["uploaded"]

    async with asyncio.TaskGroup() as tg:
        for i in range(producers):
            tg.create_task(_producer(i, local_pool, buffer, shutdown_event, drain_log))
        for i in range(consumers):
            tg.create_task(_consumer(i, local_pool, buffer, shutdown_event, drain_log, stats))
        tg.create_task(_reporter())

    drain_log.info(
        "r2_drain.stopped",
        total_uploaded=stats["uploaded"],
        total_errors=stats["errors"],
    )
