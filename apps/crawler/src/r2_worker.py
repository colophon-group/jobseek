"""Background R2 upload worker.

Drains pending R2 uploads from the ``description_pending`` and
``r2_pending_meta`` columns on ``job_posting`` at a controlled rate.
Runs as a long-lived coroutine alongside the main scheduler loop.

Design:
    Monitor/scrape tasks write to the pending columns (same transaction
    as job insert/update).  This worker reads them, uploads to R2, and
    NULLs the columns once the upload succeeds.

    If only extras changed (not description), ``description_pending`` is
    NULL and the worker fetches the existing HTML from R2 before calling
    ``upload_posting``.
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


# ---------------------------------------------------------------------------
# Token bucket rate limiter
# ---------------------------------------------------------------------------


class TokenBucket:
    """Async token bucket for R2 API rate limiting.

    R2 allows ~250 writes/sec.  We target ``rate`` ops/sec (default 200)
    with a small burst buffer.  Each ``upload_posting`` call uses ~4
    R2 operations (2 GET + 1-2 PUT).
    """

    def __init__(self, rate: float = 200.0, burst: int = 50):
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last = monotonic()

    async def acquire(self, tokens: int = 4) -> None:
        while True:
            now = monotonic()
            self._tokens = min(
                self._burst,
                self._tokens + (now - self._last) * self._rate,
            )
            self._last = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            wait = (tokens - self._tokens) / self._rate
            await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# Single-item drain
# ---------------------------------------------------------------------------


async def _drain_one(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
    bucket: TokenBucket,
) -> bool:
    """Process one pending R2 upload.  Returns True on success."""
    posting_id = str(row["id"])
    description = row["description_pending"]
    meta_raw = row["r2_pending_meta"]

    if meta_raw is None:
        # Shouldn't happen, but handle gracefully
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
        await bucket.acquire(4)

        if description:
            await upload_posting(posting_id, locale, description, extras)

            if localizations and isinstance(localizations, dict):
                for loc_locale, loc_html in localizations.items():
                    if loc_locale != locale and loc_html:
                        await bucket.acquire(3)
                        await upload_description(posting_id, loc_locale, loc_html)
        else:
            # Meta-only change: fetch existing description from R2
            existing_html = await get_description_html(posting_id, locale)
            if existing_html:
                await upload_posting(posting_id, locale, existing_html, extras)
            else:
                log.warning("r2_worker.no_existing_html", posting_id=posting_id)
                await conn.execute(_ABANDON_PENDING, posting_id)
                return True

        await conn.execute(_COMPLETE_R2_UPLOAD, posting_id, new_hash, tech_ids)
        r2_drain_total.labels(status="success").inc()
        return True

    except Exception:
        log.warning("r2_worker.upload_error", posting_id=posting_id, retry=retry_count)
        r2_drain_errors.inc()

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

        return False


# ---------------------------------------------------------------------------
# Drain loop
# ---------------------------------------------------------------------------


async def run_r2_drain_loop(
    pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
) -> None:
    """Continuously drain pending R2 uploads.

    Runs as a long-lived coroutine alongside the main scheduler loop.
    """
    bucket = TokenBucket(
        rate=settings.r2_drain_rate_limit,
        burst=50,
    )
    batch_size = settings.r2_drain_batch_size
    idle_interval = 2.0
    max_interval = 10.0
    current_interval = idle_interval

    log.info("r2_worker.starting", batch_size=batch_size, rate=settings.r2_drain_rate_limit)

    while not shutdown_event.is_set():
        try:
            drained = 0
            async with pool.acquire() as conn:
                rows = await conn.fetch(_FETCH_PENDING, batch_size)

                if not rows:
                    current_interval = min(current_interval * 2, max_interval)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(shutdown_event.wait(), timeout=current_interval)
                    continue

                current_interval = idle_interval

                for row in rows:
                    if shutdown_event.is_set():
                        break
                    if await _drain_one(conn, row, bucket):
                        drained += 1

            if drained:
                log.info("r2_worker.batch", drained=drained, total=len(rows))

            with contextlib.suppress(Exception):
                count = await pool.fetchval(_COUNT_PENDING)
                r2_pending_gauge.set(count)

        except (asyncpg.PostgresError, OSError) as exc:
            log.warning("r2_worker.db_error", error=str(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)

    log.info("r2_worker.stopped")


async def drain_remaining(pool: asyncpg.Pool) -> int:
    """Drain as many pending uploads as possible within the configured timeout.

    Called during graceful shutdown.
    """
    bucket = TokenBucket(rate=settings.r2_drain_rate_limit, burst=50)
    timeout = settings.r2_drain_shutdown_timeout
    drained = 0
    deadline = monotonic() + timeout

    log.info("r2_worker.shutdown_drain_start", timeout_s=timeout)

    while monotonic() < deadline:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_FETCH_PENDING, 50)
                if not rows:
                    break
                for row in rows:
                    if monotonic() >= deadline:
                        break
                    if await _drain_one(conn, row, bucket):
                        drained += 1
        except Exception:
            log.warning("r2_worker.shutdown_drain_error", exc_info=True)
            break

    with contextlib.suppress(Exception):
        remaining = await pool.fetchval(_COUNT_PENDING)
        log.info(
            "r2_worker.shutdown_drain_done",
            drained=drained,
            remaining=remaining,
        )

    return drained
