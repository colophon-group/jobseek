"""R2 drain worker — producer-consumer pipeline for uploading descriptions to R2.

Producer claims rows atomically (``r2_uploaded = false`` → ``NULL``) and feeds
them into an asyncio.Queue buffer. Consumers pop from the buffer, PUT to R2,
and mark ``r2_uploaded = true``. On failure, rows revert to ``false``.

Three-state ``r2_uploaded``:
- ``false``: pending upload (workers write this)
- ``NULL``: claimed by producer, in-flight
- ``true``: uploaded to R2

The ``NULL`` state is reset to ``false`` at startup and periodically by a
reaper sweep. Without this, a consumer crash between the producer's
``false → NULL`` flip and the consumer's ``NULL → true``/``NULL → false``
update leaves the row permanently invisible (issue #3168). OOM kills,
SIGKILL, segfaults and host reboots are the common triggers.

Tuning knobs (env vars via config):
- ``DRAIN_PRODUCERS``: number of producer coroutines (default 1)
- ``DRAIN_CONSUMERS``: number of consumer coroutines (default 30)
- ``DRAIN_BUFFER_SIZE``: asyncio.Queue maxsize (default 200)
- ``DRAIN_REAPER_INTERVAL``: seconds between background reaper sweeps
  (default 300 = 5 minutes)
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import timedelta

import asyncpg
import structlog

from src.config import settings
from src.core.description_store import put_description
from src.metrics import r2_upload_duration, r2_uploaded_total

log = structlog.get_logger()

_FETCH_BATCH = 50
_DEFAULT_REAPER_INTERVAL = 300  # 5 minutes
# Rows stuck in r2_uploaded=NULL for longer than this are considered
# orphaned. A healthy consumer claim-to-completion round-trip is well
# under a minute (R2 PUT latency ~100-500ms plus queue wait); 10 minutes
# is a generous floor that avoids reaping in-flight claims on a slow
# consumer while still recovering reasonably fast after a real crash.
_REAP_STALE_AFTER_SECONDS = 600  # 10 minutes


async def _reap_orphaned_claims(
    local_pool: asyncpg.Pool,
    drain_log: structlog.stdlib.BoundLogger,
    *,
    stale_after_seconds: int | None = None,
) -> int:
    """Revert orphaned in-flight claims (``r2_uploaded IS NULL``) to pending.

    Issue #3168: the 3-state scheme (``false → NULL → true``) has no
    reaper. If a consumer dies between the producer's claim
    (``false → NULL``) and either the success update (``NULL → true``)
    or the failure revert (``NULL → false``), the row stays ``NULL``
    forever — the producer query filters on ``r2_uploaded = false``, so
    the row is never re-claimed. The description never reaches R2 and
    every read-through (job detail page) silently 404s.

    Causes seen in production: OOM kill during consumer ``put_description``,
    SIGKILL on container restart, segfault inside the boto3 SSL stack,
    host reboot. None of these run the ``except`` block in ``_consumer``.

    Stale-age filter (``stale_after_seconds``) avoids racing with an
    in-flight consumer that has a row claimed but is still uploading.
    When ``None`` (the startup case), no age filter is applied — at
    startup any NULL row predates this process and must be a crashed
    leftover (no consumer is alive to upload it). When the background
    sweep runs, the default ``_REAP_STALE_AFTER_SECONDS`` ensures we
    only touch claims older than the realistic upload window.

    Returns the number of rows reaped.
    """
    where_clause = "r2_uploaded IS NULL"
    params: tuple = ()
    if stale_after_seconds is not None:
        where_clause += " AND updated_at < now() - $1::interval"
        params = (timedelta(seconds=stale_after_seconds),)

    try:
        async with local_pool.acquire() as conn:
            count = await conn.fetchval(
                "WITH reaped AS ("
                "  UPDATE descriptions SET r2_uploaded = false, updated_at = now() "
                f"  WHERE {where_clause} "
                "  RETURNING posting_id"
                ") SELECT count(*) FROM reaped",
                *params,
            )
    except Exception:
        drain_log.warning("r2_drain.reaper_error", exc_info=True)
        return 0

    reaped = int(count or 0)
    if reaped:
        drain_log.info(
            "r2_drain.reaped_orphans",
            count=reaped,
            stale_after_seconds=stale_after_seconds,
        )
    return reaped


async def _reaper_loop(
    local_pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
    drain_log: structlog.stdlib.BoundLogger,
    interval: float,
) -> None:
    """Periodically reap orphaned claims until shutdown.

    Runs every ``interval`` seconds with the stale-age filter, so it
    only touches NULL rows older than ``_REAP_STALE_AFTER_SECONDS``.
    Sleeps first, since startup already runs a one-shot unfiltered
    reap before workers start. The sweep is the safety net for crashes
    that happen *after* startup; a consumer OOM 10 minutes into the
    run would otherwise wait until the next process restart to recover.
    """
    while not shutdown_event.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
        if shutdown_event.is_set():
            break
        await _reap_orphaned_claims(
            local_pool, drain_log, stale_after_seconds=_REAP_STALE_AFTER_SECONDS
        )


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
            # Atomically claim a batch: false → NULL. The claim also
            # stamps updated_at = now() so the periodic reaper can
            # distinguish recently claimed in-flight rows from genuine
            # crash-orphans (#3168).
            async with local_pool.acquire() as conn:
                rows = await conn.fetch(
                    "UPDATE descriptions SET r2_uploaded = NULL, updated_at = now() "
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
            # Lifecycle anchor: mirror the error path's per-row event so an
            # operator with only the posting_id can confirm "yes, the
            # description for this row reached R2" instead of having to
            # diff the r2_drain.stats aggregate (#3192). r2_drain throughput
            # is bounded by new/touched postings per cycle (not by every
            # scrape claim) so per-row info is sustainable.
            drain_log.info(
                "r2_drain.uploaded",
                posting_id=str(row["posting_id"]),
                locale=row["locale"],
            )

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
    reaper_interval = float(getattr(settings, "drain_reaper_interval", _DEFAULT_REAPER_INTERVAL))

    drain_log = log.bind(name=consumer_name)
    drain_log.info(
        "r2_drain.started",
        producers=producers,
        consumers=consumers,
        buffer_size=buf_size,
        reaper_interval=reaper_interval,
    )

    # Issue #3168: reap orphaned claims (r2_uploaded IS NULL) at startup,
    # BEFORE producers begin claiming new rows. Any row left NULL by a
    # previous crash is reset to false so the producer can re-claim it.
    # This MUST happen before producers run — otherwise the producers
    # might already have claimed fresh rows (false → NULL) by the time
    # the reaper runs, and the reaper's blanket UPDATE would revert
    # those in-flight rows too.
    await _reap_orphaned_claims(local_pool, drain_log)

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
        # Periodic reaper: catch crashes that happen *after* startup
        # (the startup reap covers crashes before this process began).
        # Issue #3168.
        tg.create_task(_reaper_loop(local_pool, shutdown_event, drain_log, reaper_interval))

    drain_log.info(
        "r2_drain.stopped",
        total_uploaded=stats["uploaded"],
        total_errors=stats["errors"],
    )
