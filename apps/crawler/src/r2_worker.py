"""Background R2 upload worker.

Drains pending R2 uploads from the ``description_pending`` and
``r2_pending_meta`` columns on ``job_posting``.

Architecture: three-stage async pipeline.

    Producer : fetches DB rows + prefetches R2 state in parallel
    Consumers: N concurrent uploaders (PUT only, GETs already done)
    DB writer: batches acknowledgements from consumers

The producer prefetches existing R2 content so consumers only do
writes.  The DB writer batches completed uploads to reduce pool
contention.  Buffer sizes cap RAM usage.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from time import monotonic

import asyncpg
import structlog

from src.config import settings
from src.core.description_store import (
    _compute_reverse_diff,
    _extras_diff,
    _put_object,
    get_object,
    upload_description,
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
  AND r2_pending_meta->>'new_hash' = $4
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

_SENTINEL = None


# ---------------------------------------------------------------------------
# R2 key helpers
# ---------------------------------------------------------------------------


def _r2_prefix(posting_id: str, locale: str) -> tuple[str, str]:
    prefix = f"job/{posting_id}"
    return f"{prefix}/{locale}/latest.html", f"{prefix}/{locale}/history.json"


# ---------------------------------------------------------------------------
# Stage 1: Producer — DB fetch + R2 prefetch
# ---------------------------------------------------------------------------


async def _prefetch_r2_state(row) -> dict:
    """Fetch existing R2 content for a pending row (concurrent with others)."""
    meta_raw = row["r2_pending_meta"]
    if meta_raw is None:
        return {"row": row, "_r2_html": None, "_r2_history": None}

    meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    locale = meta.get("locale", "en")
    posting_id = str(row["id"])

    latest_key, history_key = _r2_prefix(posting_id, locale)
    try:
        r2_html, r2_history = await asyncio.gather(
            get_object(latest_key),
            get_object(history_key),
        )
    except Exception:
        r2_html, r2_history = None, None

    return {"row": row, "_r2_html": r2_html, "_r2_history": r2_history}


async def _producer(
    pool: asyncpg.Pool,
    buffer: asyncio.Queue,
    shutdown_event: asyncio.Event,
    num_consumers: int,
) -> None:
    """Fetch pending rows from DB, prefetch R2 state, feed the buffer."""
    batch_size = settings.r2_drain_batch_size
    idle_interval = 1.0
    max_interval = 10.0
    current_interval = idle_interval

    try:
        while not shutdown_event.is_set():
            try:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(_FETCH_PENDING, batch_size)

                if not rows:
                    current_interval = min(current_interval * 2, max_interval)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(shutdown_event.wait(), timeout=current_interval)
                    continue

                current_interval = idle_interval

                # Prefetch R2 state and enqueue concurrently — each item
                # enters the buffer as soon as its GETs complete, so
                # consumers start uploading before the whole batch is fetched.
                async def _prefetch_and_enqueue(r):
                    item = await _prefetch_r2_state(r)
                    while not shutdown_event.is_set():
                        try:
                            buffer.put_nowait(item)
                            return
                        except asyncio.QueueFull:
                            await asyncio.sleep(0.1)

                await asyncio.gather(
                    *[_prefetch_and_enqueue(row) for row in rows]
                )

                with contextlib.suppress(Exception):
                    count = await pool.fetchval(_COUNT_PENDING)
                    r2_pending_gauge.set(count)

            except (asyncpg.PostgresError, OSError) as exc:
                log.warning("r2_worker.producer_error", error=str(exc))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
    finally:
        for _ in range(num_consumers):
            await buffer.put(_SENTINEL)


# ---------------------------------------------------------------------------
# Stage 2: Consumer — R2 PUTs only (no GETs, no DB)
# ---------------------------------------------------------------------------

# Result types for the done_queue
_OK = "ok"  # (type, posting_id, new_hash, tech_ids, hash_str)
_RETRY = "retry"  # (type, posting_id)
_ABANDON = "abandon"  # (type, posting_id, source)


async def _upload_one(item: dict) -> tuple:
    """Upload one item to R2 using prefetched state.

    Returns a result tuple for the DB writer. No DB access here.
    """
    row = item["row"]
    r2_html = item["_r2_html"]
    r2_history_raw = item["_r2_history"]

    posting_id = str(row["id"])
    description = row["description_pending"]
    meta_raw = row["r2_pending_meta"]

    if meta_raw is None:
        return (_ABANDON, posting_id, "monitor")

    meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    locale = meta.get("locale", "en")
    extras = meta.get("extras", {})
    tech_ids = meta.get("tech_ids")
    localizations = meta.get("localizations")
    source = meta.get("source", "monitor")
    retry_count = meta.get("retry_count", 0)
    new_hash = meta.get("new_hash")

    try:
        html = description
        if not html:
            html = r2_html
            if not html:
                log.warning("r2_worker.no_existing_html", posting_id=posting_id)
                return (_ABANDON, posting_id, source)

        latest_key, history_key = _r2_prefix(posting_id, locale)
        history = json.loads(r2_history_raw) if r2_history_raw else {"versions": []}
        existing_extras = history.get("current_extras", {})

        if "metadata" not in extras and "metadata" in existing_extras:
            extras = {**extras, "metadata": existing_extras["metadata"]}

        is_first = r2_html is None
        desc_changed = not is_first and r2_html != html

        if is_first:
            history = {"versions": [], "current_extras": extras}
        else:
            extras_diff = _extras_diff(existing_extras, extras)
            if not desc_changed and not extras_diff:
                hash_str = str(new_hash) if new_hash is not None else ""
                return (_OK, posting_id, new_hash, tech_ids, hash_str)

            entry: dict = {"timestamp": datetime.now(UTC).isoformat()}
            if desc_changed:
                entry["diff"] = _compute_reverse_diff(html, r2_html)
            if extras_diff:
                entry["extras"] = extras_diff
            history["versions"].insert(0, entry)
            history["current_extras"] = extras

        # PUTs only — the hot path
        puts = [_put_object(history_key, json.dumps(history), "application/json")]
        if is_first or desc_changed:
            puts.append(_put_object(latest_key, html))
        await asyncio.gather(*puts)

        if description and localizations and isinstance(localizations, dict):
            loc_puts = [
                upload_description(posting_id, loc_locale, loc_html)
                for loc_locale, loc_html in localizations.items()
                if loc_locale != locale and loc_html
            ]
            if loc_puts:
                await asyncio.gather(*loc_puts)

        hash_str = str(new_hash) if new_hash is not None else ""
        return (_OK, posting_id, new_hash, tech_ids, hash_str)

    except Exception:
        log.warning("r2_worker.upload_error", posting_id=posting_id, retry=retry_count)
        r2_drain_errors.inc()

        if retry_count + 1 >= settings.r2_drain_max_retries:
            log.error("r2_worker.max_retries", posting_id=posting_id, source=source)
            return (_ABANDON, posting_id, source)
        return (_RETRY, posting_id)


async def _consumer(
    buffer: asyncio.Queue,
    done_queue: asyncio.Queue,
    consumer_id: int,
) -> int:
    """Pull items from buffer, upload to R2, put result in done_queue."""
    uploaded = 0
    while True:
        item = await buffer.get()
        if item is _SENTINEL:
            buffer.task_done()
            break
        try:
            result = await _upload_one(item)
            await done_queue.put(result)
            if result[0] == _OK:
                uploaded += 1
        except Exception:
            log.exception("r2_worker.consumer_error", consumer=consumer_id)
        finally:
            buffer.task_done()
    return uploaded


# ---------------------------------------------------------------------------
# Stage 3: DB writer — batched acknowledgements
# ---------------------------------------------------------------------------

_DB_WRITER_SENTINEL = object()


async def _db_writer(
    pool: asyncpg.Pool,
    done_queue: asyncio.Queue,
) -> int:
    """Batch DB writes from consumer results."""
    written = 0
    while True:
        # Collect a batch (wait for first, then drain without blocking)
        first = await done_queue.get()
        if first is _DB_WRITER_SENTINEL:
            done_queue.task_done()
            break

        batch = [first]
        done_queue.task_done()
        # Drain up to 49 more without blocking
        for _ in range(49):
            try:
                item = done_queue.get_nowait()
                if item is _DB_WRITER_SENTINEL:
                    done_queue.task_done()
                    # Process batch then stop
                    batch.append(None)  # marker
                    break
                batch.append(item)
                done_queue.task_done()
            except asyncio.QueueEmpty:
                break

        has_sentinel = None in batch
        batch = [b for b in batch if b is not None]

        if batch:
            try:
                async with pool.acquire() as conn:
                    for result in batch:
                        rtype = result[0]
                        if rtype == _OK:
                            _, pid, new_hash, tech_ids, hash_str = result
                            res = await conn.execute(
                                _COMPLETE_R2_UPLOAD, pid, new_hash, tech_ids, hash_str
                            )
                            if res == "UPDATE 0":
                                log.info("r2_worker.stale_upload", posting_id=pid)
                            else:
                                r2_drain_total.labels(status="success").inc()
                                written += 1
                        elif rtype == _RETRY:
                            _, pid = result
                            await conn.execute(_INCREMENT_RETRY, pid)
                            r2_drain_total.labels(status="retried").inc()
                        elif rtype == _ABANDON:
                            _, pid, source = result
                            await conn.execute(_ABANDON_PENDING, pid)
                            if source == "scrape":
                                await conn.execute(_RESET_SCRAPE, pid)
                            r2_drain_total.labels(status="abandoned").inc()
            except Exception:
                log.exception("r2_worker.db_writer_error", batch_size=len(batch))

        if has_sentinel:
            break

    return written


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_r2_drain_loop(
    pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
) -> None:
    """Run the three-stage R2 drain pipeline."""
    num_consumers = settings.r2_max_connections
    buffer_size = num_consumers * 2
    buffer: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
    done_queue: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)

    log.info(
        "r2_worker.starting",
        consumers=num_consumers,
        buffer_size=buffer_size,
        batch_size=settings.r2_drain_batch_size,
    )

    consumers = [
        asyncio.create_task(_consumer(buffer, done_queue, i)) for i in range(num_consumers)
    ]
    writer = asyncio.create_task(_db_writer(pool, done_queue))
    producer = asyncio.create_task(_producer(pool, buffer, shutdown_event, num_consumers))

    try:
        await producer
    except Exception:
        log.exception("r2_worker.producer_crashed")
    finally:
        # Wait for consumers to finish
        await asyncio.gather(*consumers, return_exceptions=True)
        # Signal DB writer to flush and stop
        await done_queue.put(_DB_WRITER_SENTINEL)
        await writer

    total = sum(
        c.result() for c in consumers if c.done() and not c.cancelled() and c.exception() is None
    )
    db_written = writer.result() if writer.done() and not writer.cancelled() else 0
    log.info("r2_worker.stopped", uploaded=total, db_written=db_written)


async def drain_remaining(pool: asyncpg.Pool) -> int:
    """Drain pending uploads during graceful shutdown."""
    timeout = settings.r2_drain_shutdown_timeout
    deadline = monotonic() + timeout
    num_consumers = min(settings.r2_max_connections, 10)
    buffer: asyncio.Queue = asyncio.Queue(maxsize=num_consumers * 2)
    done_queue: asyncio.Queue = asyncio.Queue(maxsize=num_consumers * 2)

    log.info("r2_worker.shutdown_drain_start", timeout_s=timeout, consumers=num_consumers)

    async def _timed_producer():
        batch_size = settings.r2_drain_batch_size
        while monotonic() < deadline:
            try:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(_FETCH_PENDING, batch_size)
                if not rows:
                    break
                prefetched = await asyncio.gather(*[_prefetch_r2_state(row) for row in rows])
                for item in prefetched:
                    if monotonic() >= deadline:
                        break
                    buffer.put_nowait(item)
            except Exception:
                log.warning("r2_worker.shutdown_drain_error", exc_info=True)
                break
        for _ in range(num_consumers):
            await buffer.put(_SENTINEL)

    consumers = [
        asyncio.create_task(_consumer(buffer, done_queue, i)) for i in range(num_consumers)
    ]
    writer = asyncio.create_task(_db_writer(pool, done_queue))

    await _timed_producer()
    await asyncio.gather(*consumers, return_exceptions=True)
    await done_queue.put(_DB_WRITER_SENTINEL)
    await writer

    drained = writer.result() if writer.done() and not writer.cancelled() else 0

    with contextlib.suppress(Exception):
        remaining = await pool.fetchval(_COUNT_PENDING)
        log.info(
            "r2_worker.shutdown_drain_done",
            drained=drained,
            remaining=remaining,
        )

    return drained
