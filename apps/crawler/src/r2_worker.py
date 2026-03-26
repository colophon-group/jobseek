"""Background R2 upload worker.

Drains pending R2 uploads from the ``description_pending`` and
``r2_pending_meta`` columns on ``job_posting``.

Architecture: producer–consumer with an async buffer.

    Producer : fetches batches from DB, fills the buffer
    Consumers: N concurrent uploaders (one per R2 connection)

The buffer size caps RAM usage.  The consumer count caps concurrent
R2 connections (and thus CPU/network load).  When the buffer is full
the producer blocks, providing natural backpressure on the DB side.
When the buffer is empty the consumers idle, waiting for work.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from time import monotonic

import asyncpg
import structlog

from src.config import settings
from src.core.description_store import (
    get_description_html,
    upload_description,
    upload_posting,
)
from src.metrics import r2_drain_errors, r2_drain_total, r2_pending_gauge

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_FETCH_PENDING = """
SELECT id, description_pending, r2_pending_meta, description_r2_hash
FROM job_posting
WHERE description_pending IS NOT NULL
   OR r2_pending_meta IS NOT NULL
ORDER BY description_r2_hash NULLS FIRST
LIMIT $1
FOR UPDATE SKIP LOCKED
"""

_COMPLETE_R2_UPLOAD = """
UPDATE job_posting
SET description_pending = NULL,
    r2_pending_meta = NULL,
    description_r2_hash = $2,
    technology_ids = COALESCE($3, technology_ids),
    to_be_enriched = CASE
        WHEN description_r2_hash IS DISTINCT FROM $2 THEN true
        ELSE to_be_enriched
    END
WHERE id = $1::uuid
"""

_INCREMENT_RETRY = """
UPDATE job_posting
SET r2_pending_meta = jsonb_set(
    r2_pending_meta,
    '{retry_count}',
    to_jsonb(COALESCE((r2_pending_meta->>'retry_count')::int, 0) + 1)
)
WHERE id = $1::uuid
"""

_ABANDON_PENDING = """
UPDATE job_posting
SET description_pending = NULL,
    r2_pending_meta = NULL
WHERE id = $1::uuid
"""

_RESET_SCRAPE = """
UPDATE job_posting
SET next_scrape_at = now()
WHERE id = $1::uuid
"""

_COUNT_PENDING = """
SELECT count(*) FROM job_posting
WHERE description_pending IS NOT NULL
   OR r2_pending_meta IS NOT NULL
"""

_SENTINEL = None  # signals consumers to stop


# ---------------------------------------------------------------------------
# Consumer: upload one item to R2
# ---------------------------------------------------------------------------


async def _upload_one(
    pool: asyncpg.Pool,
    row: asyncpg.Record,
) -> bool:
    """Upload one pending item to R2 and update DB.  Returns True on success."""
    posting_id = str(row["id"])
    description = row["description_pending"]
    meta_raw = row["r2_pending_meta"]

    if meta_raw is None:
        async with pool.acquire() as conn:
            await conn.execute(_ABANDON_PENDING, posting_id)
        return True

    meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    locale = meta.get("locale", "en")
    extras = meta.get("extras", {})
    tech_ids = meta.get("tech_ids")
    localizations = meta.get("localizations")
    source = meta.get("source", "monitor")
    retry_count = meta.get("retry_count", 0)
    new_hash = meta.get("new_hash")

    try:
        if description:
            await upload_posting(posting_id, locale, description, extras)

            if localizations and isinstance(localizations, dict):
                for loc_locale, loc_html in localizations.items():
                    if loc_locale != locale and loc_html:
                        await upload_description(posting_id, loc_locale, loc_html)
        else:
            # Meta-only change: fetch existing description from R2
            existing_html = await get_description_html(posting_id, locale)
            if existing_html:
                await upload_posting(posting_id, locale, existing_html, extras)
            else:
                log.warning("r2_worker.no_existing_html", posting_id=posting_id)
                async with pool.acquire() as conn:
                    await conn.execute(_ABANDON_PENDING, posting_id)
                return True

        async with pool.acquire() as conn:
            await conn.execute(_COMPLETE_R2_UPLOAD, posting_id, new_hash, tech_ids)
        r2_drain_total.labels(status="success").inc()
        return True

    except Exception:
        log.warning("r2_worker.upload_error", posting_id=posting_id, retry=retry_count)
        r2_drain_errors.inc()

        try:
            async with pool.acquire() as conn:
                if retry_count + 1 >= settings.r2_drain_max_retries:
                    log.error(
                        "r2_worker.max_retries",
                        posting_id=posting_id,
                        source=source,
                    )
                    await conn.execute(_ABANDON_PENDING, posting_id)
                    if source == "scrape":
                        await conn.execute(_RESET_SCRAPE, posting_id)
                    r2_drain_total.labels(status="abandoned").inc()
                else:
                    await conn.execute(_INCREMENT_RETRY, posting_id)
                    r2_drain_total.labels(status="retried").inc()
        except Exception:
            log.warning("r2_worker.db_error_on_retry", posting_id=posting_id)

        return False


# ---------------------------------------------------------------------------
# Consumer loop
# ---------------------------------------------------------------------------


async def _consumer(
    pool: asyncpg.Pool,
    buffer: asyncio.Queue,
    consumer_id: int,
) -> int:
    """Pull items from buffer and upload to R2.  Returns count of successes."""
    drained = 0
    while True:
        row = await buffer.get()
        if row is _SENTINEL:
            buffer.task_done()
            break
        try:
            if await _upload_one(pool, row):
                drained += 1
        except Exception:
            log.exception("r2_worker.consumer_error", consumer=consumer_id)
        finally:
            buffer.task_done()
    return drained


# ---------------------------------------------------------------------------
# Producer loop
# ---------------------------------------------------------------------------


async def _producer(
    pool: asyncpg.Pool,
    buffer: asyncio.Queue,
    shutdown_event: asyncio.Event,
    num_consumers: int,
) -> None:
    """Fetch pending rows from DB in batches and feed the buffer."""
    batch_size = settings.r2_drain_batch_size
    idle_interval = 1.0
    max_interval = 10.0
    current_interval = idle_interval

    while not shutdown_event.is_set():
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_FETCH_PENDING, batch_size)

            if not rows:
                current_interval = min(current_interval * 2, max_interval)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        shutdown_event.wait(), timeout=current_interval
                    )
                continue

            current_interval = idle_interval

            for row in rows:
                if shutdown_event.is_set():
                    break
                await buffer.put(row)  # blocks if buffer full

            with contextlib.suppress(Exception):
                count = await pool.fetchval(_COUNT_PENDING)
                r2_pending_gauge.set(count)

        except (asyncpg.PostgresError, OSError) as exc:
            log.warning("r2_worker.producer_error", error=str(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)

    # Signal all consumers to stop
    for _ in range(num_consumers):
        await buffer.put(_SENTINEL)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_r2_drain_loop(
    pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
) -> None:
    """Run the producer–consumer R2 drain pipeline.

    ``r2_max_connections`` controls the number of concurrent consumers
    (one R2 upload per consumer).  ``r2_drain_batch_size`` controls how
    many rows the producer fetches per DB query.  Buffer size is
    ``2 * r2_max_connections`` to keep consumers saturated.
    """
    num_consumers = settings.r2_max_connections
    buffer_size = num_consumers * 2
    buffer: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)

    log.info(
        "r2_worker.starting",
        consumers=num_consumers,
        buffer_size=buffer_size,
        batch_size=settings.r2_drain_batch_size,
    )

    consumers = [
        asyncio.create_task(_consumer(pool, buffer, i))
        for i in range(num_consumers)
    ]
    producer = asyncio.create_task(
        _producer(pool, buffer, shutdown_event, num_consumers)
    )

    # Wait for producer to finish (shutdown signalled)
    await producer

    # Wait for consumers to drain remaining buffer items
    await asyncio.gather(*consumers)

    total = sum(c.result() for c in consumers if not c.cancelled())
    log.info("r2_worker.stopped", total_drained=total)


async def drain_remaining(pool: asyncpg.Pool) -> int:
    """Drain pending uploads during graceful shutdown.

    Runs a mini producer–consumer pipeline with a deadline.
    """
    timeout = settings.r2_drain_shutdown_timeout
    deadline = monotonic() + timeout
    num_consumers = min(settings.r2_max_connections, 10)
    buffer: asyncio.Queue = asyncio.Queue(maxsize=num_consumers * 2)

    log.info("r2_worker.shutdown_drain_start", timeout_s=timeout, consumers=num_consumers)

    shutdown = asyncio.Event()

    async def _timed_producer():
        batch_size = settings.r2_drain_batch_size
        while monotonic() < deadline:
            try:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(_FETCH_PENDING, batch_size)
                if not rows:
                    break
                for row in rows:
                    if monotonic() >= deadline:
                        break
                    await buffer.put(row)
            except Exception:
                log.warning("r2_worker.shutdown_drain_error", exc_info=True)
                break
        for _ in range(num_consumers):
            await buffer.put(_SENTINEL)

    consumers = [
        asyncio.create_task(_consumer(pool, buffer, i))
        for i in range(num_consumers)
    ]
    await _timed_producer()
    await asyncio.gather(*consumers)

    drained = sum(c.result() for c in consumers if not c.cancelled())

    with contextlib.suppress(Exception):
        remaining = await pool.fetchval(_COUNT_PENDING)
        log.info(
            "r2_worker.shutdown_drain_done",
            drained=drained,
            remaining=remaining,
        )

    return drained
