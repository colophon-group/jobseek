"""R2 drain worker — producer-consumer pipeline for uploading descriptions to R2.

Producer claims rows atomically (``r2_uploaded = false`` → ``NULL``) and feeds
them into an asyncio.Queue buffer. Consumers pop from the buffer, PUT to R2,
and mark ``r2_uploaded = true``. On failure, rows return to ``false`` with a
durable, exponentially increasing retry timestamp.

Three-state ``r2_uploaded``:
- ``false``: pending upload, eligible at ``r2_next_attempt_at``
- ``NULL``: claimed by producer, in-flight
- ``true``: uploaded to R2

The ``NULL`` state is reset to ``false`` at startup and periodically by a
reaper sweep. Without this, a consumer crash between the producer's
``false → NULL`` flip and the consumer's ``NULL → true``/``NULL → false``
update leaves the row permanently invisible (issue #3168). OOM kills,
SIGKILL, segfaults and host reboots are the common triggers.

Tuning knobs (env vars via config):
- ``DRAIN_PRODUCERS``: number of producer coroutines (default 2)
- ``DRAIN_CONSUMERS``: number of consumer coroutines (default 30)
- ``DRAIN_BUFFER_SIZE``: asyncio.Queue maxsize (default 200)
- ``DRAIN_RETRY_BASE_SECONDS``: first durable retry ceiling (default 5)
- ``DRAIN_RETRY_MAX_SECONDS``: durable retry ceiling cap (default 900)
- ``DRAIN_REAPER_INTERVAL``: seconds between background reaper sweeps
  (default 300 = 5 minutes)
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import random
import time
from datetime import timedelta

import asyncpg
import httpx
import structlog

from src.config import settings
from src.core.description_store import put_description
from src.metrics import (
    r2_retry_delay,
    r2_retry_scheduled_total,
    r2_upload_duration,
    r2_uploaded_total,
)

log = structlog.get_logger()

_FETCH_BATCH = 50
_DEFAULT_REAPER_INTERVAL = 300  # 5 minutes
# Rows stuck in r2_uploaded=NULL for longer than this are considered
# orphaned. A healthy consumer claim-to-completion round-trip is well
# under a minute (R2 PUT latency ~100-500ms plus queue wait); 10 minutes
# is a generous floor that avoids reaping in-flight claims on a slow
# consumer while still recovering reasonably fast after a real crash.
_REAP_STALE_AFTER_SECONDS = 600  # 10 minutes


def _retry_delay_seconds(failure_count: int) -> float:
    """Return an equal-jitter durable delay for one description.

    ``failure_count`` is one-based. Equal jitter keeps a useful minimum
    cooldown (unlike full jitter, which can approach zero) while spreading
    many rows after a provider incident.
    """
    base = settings.drain_retry_base_seconds
    maximum = settings.drain_retry_max_seconds
    if base <= 0 or maximum < base:
        raise ValueError(
            "DRAIN_RETRY_BASE_SECONDS must be positive and no greater than DRAIN_RETRY_MAX_SECONDS"
        )
    cap_exponent = math.ceil(math.log2(maximum / base))
    exponent = min(max(0, failure_count - 1), cap_exponent)
    ceiling = min(maximum, base * (2**exponent))
    return random.uniform(ceiling / 2, ceiling)


def _failure_reason(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return "http_5xx" if 500 <= status <= 599 else "http_other"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "transport"
    if isinstance(exc, asyncpg.PostgresError):
        return "database"
    return "other"


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
                    "WITH candidates AS MATERIALIZED ("
                    "  SELECT posting_id, locale FROM descriptions "
                    "  WHERE r2_uploaded = false AND r2_next_attempt_at <= now() "
                    "  ORDER BY r2_next_attempt_at, posting_id, locale "
                    "  FOR UPDATE SKIP LOCKED "
                    "  LIMIT $1"
                    ") "
                    "UPDATE descriptions AS d "
                    "SET r2_uploaded = NULL, updated_at = now() "
                    "FROM candidates AS c "
                    "WHERE d.posting_id = c.posting_id AND d.locale = c.locale "
                    "RETURNING d.posting_id, d.locale, d.html, d.hash, "
                    "d.r2_upload_failures",
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
    """Pop from buffer, PUT to R2, and durably record success or retry."""
    while not shutdown_event.is_set():
        try:
            row = await asyncio.wait_for(buffer.get(), timeout=1.0)
        except TimeoutError:
            continue

        t0 = time.monotonic()
        try:
            await put_description(str(row["posting_id"]), row["locale"], row["html"])

            marked_current = await local_pool.fetchval(
                "UPDATE descriptions SET r2_uploaded = true, "
                "r2_upload_failures = 0, "
                "r2_next_attempt_at = '-infinity'::timestamptz "
                "WHERE posting_id = $1 AND locale = $2 AND hash = $3 "
                "RETURNING true",
                row["posting_id"],
                row["locale"],
                row["hash"],
            )

            stats["total_time"] += time.monotonic() - t0
            r2_upload_duration.observe(time.monotonic() - t0)
            if marked_current:
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
                r2_uploaded_total.labels(status="succeeded").inc()
                # Lifecycle anchor: mirror the error path's per-row event so
                # an operator can confirm the description reached R2 (#3192).
                drain_log.info(
                    "r2_drain.uploaded",
                    posting_id=str(row["posting_id"]),
                    locale=row["locale"],
                )
            else:
                # A newer version replaced the claimed row while this PUT was
                # in flight. Do not mark the old hash current; the newer row
                # remains pending and will overwrite the R2 object.
                r2_uploaded_total.labels(status="superseded").inc()
                drain_log.info(
                    "r2_drain.upload_superseded",
                    posting_id=str(row["posting_id"]),
                    locale=row["locale"],
                    hash=row["hash"],
                )

        except Exception as exc:
            failure_count = int(row.get("r2_upload_failures", 0)) + 1
            retry_in = _retry_delay_seconds(failure_count)
            retry_scheduled = False
            try:
                retry_scheduled = bool(
                    await local_pool.fetchval(
                        "UPDATE descriptions SET r2_uploaded = false, "
                        "r2_upload_failures = $3, "
                        "r2_next_attempt_at = now() + $4::interval "
                        "WHERE posting_id = $1 AND locale = $2 AND hash = $5 "
                        "RETURNING true",
                        row["posting_id"],
                        row["locale"],
                        failure_count,
                        timedelta(seconds=retry_in),
                        row["hash"],
                    )
                )
            except Exception:
                # The row stays NULL and the orphan reaper remains the safety
                # net. Surface this separately instead of claiming the retry
                # was durably scheduled.
                drain_log.warning(
                    "r2_drain.retry_schedule_error",
                    posting_id=str(row["posting_id"]),
                    locale=row["locale"],
                    exc_info=True,
                )

            drain_log.warning(
                "r2_drain.consumer_error",
                posting_id=str(row["posting_id"]),
                locale=row["locale"],
                error_type=type(exc).__name__,
                http_status=(
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                ),
                failure_count=failure_count,
                retry_in_s=round(retry_in, 2),
                retry_scheduled=retry_scheduled,
                exc_info=True,
            )
            stats["errors"] += 1
            r2_uploaded_total.labels(status="failed").inc()
            if retry_scheduled:
                r2_retry_scheduled_total.labels(reason=_failure_reason(exc)).inc()
                r2_retry_delay.observe(retry_in)
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
