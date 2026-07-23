"""Serialize CDC cursor advancement with operator repair writes.

The exporter reads mutable ``updated_at`` rows and advances a keyset cursor.
A bulk repair transaction can choose its ``now()`` timestamp before an
exporter statement snapshot, commit after that snapshot, and therefore land
behind the cursor the exporter just saved.  PostgreSQL advisory locks give the
two code paths a database-scoped fence without coupling their processes or
deployment topology.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime

import asyncpg
import structlog

from src.metrics import exporter_cdc_active_writers, exporter_cdc_cutoff_delay

log = structlog.get_logger()

# Stable, repository-owned bigint (ASCII-ish ``JOBSEEK``) shared by the
# exporter and repair command.  Advisory locks are scoped to the local
# PostgreSQL cluster, which is exactly where the cursor and source rows live.
EXPORT_CURSOR_FENCE_ID = 0x4A4F425345454B
_FENCE_RETRY_SECONDS = 1.0
_TRY_LOCK_SQL = "SELECT pg_try_advisory_lock($1::bigint)"
_UNLOCK_SQL = "SELECT pg_advisory_unlock($1::bigint)"

# Distinct from the long operator-repair fence above. Every transaction that
# changes an exported job_posting field takes the shared side from a database
# trigger. The exporter observes those holders to choose a conservative
# non-blocking cutoff. ASCII-ish ``CDCLOCK`` encoded as a positive bigint.
CDC_WRITER_BARRIER_ID = 0x4344434C4F434B
_CDC_CUTOFF_SQL = """
WITH captured AS MATERIALIZED (
    SELECT clock_timestamp() AS captured_at
),
writers AS MATERIALIZED (
    SELECT 1 AS present, activity.xact_start
    FROM captured
    JOIN pg_locks AS locks ON true
    LEFT JOIN pg_stat_activity AS activity ON activity.pid = locks.pid
    WHERE locks.locktype = 'advisory'
      AND locks.database = (
          SELECT oid FROM pg_database WHERE datname = current_database()
      )
      AND locks.classid = (($1::bigint >> 32) & 4294967295)::oid
      AND locks.objid = ($1::bigint & 4294967295)::oid
      AND locks.objsubid = 1
      AND locks.mode = 'ShareLock'
      AND locks.granted
)
SELECT captured.captured_at,
       LEAST(
           captured.captured_at,
           COALESCE(min(writers.xact_start), captured.captured_at)
       ) AS cutoff,
       count(writers.present)::int AS active_writers,
       count(writers.present) FILTER (
           WHERE writers.xact_start IS NULL
       )::int AS unknown_writers
FROM captured
LEFT JOIN writers ON true
GROUP BY captured.captured_at
"""
_CDC_SLOW_CUTOFF_SECONDS = 30.0

CursorFenceFactory = Callable[[asyncpg.Pool], AbstractAsyncContextManager[None]]
CutoffFactory = Callable[[asyncpg.Pool], Awaitable[datetime]]


@asynccontextmanager
async def export_cursor_fence(pool: asyncpg.Pool) -> AsyncIterator[None]:
    """Hold the database-wide exporter/repair fence for one critical section.

    This is a session advisory lock rather than a transaction lock because the
    guarded section intentionally spans Typesense and Supabase I/O.  A lost
    database connection releases PostgreSQL session locks automatically; the
    explicit unlock keeps a healthy pooled connection safe for reuse.

    Acquisition uses non-blocking attempts so a planned long repair cannot hit
    asyncpg's command timeout while the exporter waits. If cancellation or an
    exception makes either acquisition or release uncertain, terminate the
    dedicated session before it can return to the pool; PostgreSQL then drops
    every session lock held by that connection.
    """

    async with pool.acquire() as conn:
        acquired = False
        waiting_since: float | None = None
        try:
            while not acquired:
                try:
                    attempt = await conn.fetchval(_TRY_LOCK_SQL, EXPORT_CURSOR_FENCE_ID)
                except BaseException:
                    conn.terminate()
                    raise

                if attempt is True:
                    acquired = True
                    break
                if attempt is not False:
                    conn.terminate()
                    raise RuntimeError("PostgreSQL returned an invalid advisory lock result")

                if waiting_since is None:
                    waiting_since = time.monotonic()
                    log.info("export_cursor_fence.waiting")
                await asyncio.sleep(_FENCE_RETRY_SECONDS)

            if waiting_since is not None:
                log.info(
                    "export_cursor_fence.acquired_after_wait",
                    wait_s=round(time.monotonic() - waiting_since, 2),
                )
            yield
        finally:
            if acquired:
                try:
                    unlocked = await conn.fetchval(_UNLOCK_SQL, EXPORT_CURSOR_FENCE_ID)
                except BaseException:
                    conn.terminate()
                    raise
                if unlocked is not True:
                    conn.terminate()
                    raise RuntimeError("export cursor advisory fence was not held")


async def capture_cdc_snapshot_cutoff(pool: asyncpg.Pool) -> datetime:
    """Return a commit-safe upper bound for one posting export tick.

    The matching database trigger holds a shared transaction advisory lock
    from the first relevant statement until commit or rollback. In one
    statement, capture the database clock and inspect the transaction start of
    every current holder. The safe cutoff is no later than the oldest holder's
    transaction start:

    * an existing holder stamps with ``clock_timestamp()`` at or after its
      transaction start, so ``updated_at < cutoff`` excludes its invisible
      writes until a later tick;
    * a transaction that reaches the trigger after the clock capture stamps at
      or after the captured clock and is excluded by the same strict bound;
    * a holder that commits before the catalog scan is visible to the export
      query and is safe to include; and
    * older committed rows remain exportable while writers are active.

    This avoids both exclusive-lock starvation under continuously overlapping
    writers and blocking back-pressure on new worker statements. A prepared or
    otherwise unidentifiable holder fails closed because no safe transaction
    floor can be proven.
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(_CDC_CUTOFF_SQL, CDC_WRITER_BARRIER_ID)

    if row is None:
        raise RuntimeError("PostgreSQL returned no CDC cutoff")
    captured_at = row["captured_at"]
    cutoff = row["cutoff"]
    active_writers = row["active_writers"]
    unknown_writers = row["unknown_writers"]
    if (
        not isinstance(captured_at, datetime)
        or not isinstance(cutoff, datetime)
        or not isinstance(active_writers, int)
        or not isinstance(unknown_writers, int)
    ):
        raise RuntimeError("PostgreSQL returned an invalid CDC cutoff result")

    exporter_cdc_active_writers.set(active_writers)
    if unknown_writers:
        log.error(
            "cdc_snapshot_cutoff.unknown_writer",
            active_writers=active_writers,
            unknown_writers=unknown_writers,
        )
        raise RuntimeError("CDC writer transaction start is unavailable")

    delay_s = max(0.0, (captured_at - cutoff).total_seconds())
    exporter_cdc_cutoff_delay.set(delay_s)
    if delay_s >= _CDC_SLOW_CUTOFF_SECONDS:
        log.warning(
            "cdc_snapshot_cutoff.delayed",
            delay_s=round(delay_s, 3),
            active_writers=active_writers,
        )
    return cutoff
