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

from src.metrics import exporter_cdc_barrier_timeouts, exporter_cdc_barrier_wait

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
# trigger; the exporter briefly takes the exclusive side before choosing its
# cutoff. ASCII-ish ``CDCLOCK`` encoded as a positive bigint.
CDC_WRITER_BARRIER_ID = 0x4344434C4F434B
_CDC_TRY_LOCK_SQL = "SELECT pg_try_advisory_lock($1::bigint)"
_CDC_CUTOFF_SQL = "SELECT clock_timestamp()"
_CDC_LOCK_TIMEOUT_SECONDS = 120.0
_CDC_LOCK_RETRY_SECONDS = 0.1
_CDC_SLOW_WAIT_SECONDS = 1.0

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
    from the first relevant statement until commit or rollback. Non-blocking
    exclusive probes avoid queuing healthy writers behind one long transaction.
    Once a probe succeeds:

    * writers that stamped before this call must commit before it returns;
    * writers that reach the trigger while it is held stamp only after the
      returned cutoff; and
    * the exporter can safely query ``updated_at < cutoff`` without holding
      the lock during downstream network I/O.

    A finite polling window bounds exporter staleness if a writer is wedged,
    without advancing a cursor or imposing that wait on new writers. Any
    acquisition/cutoff/release uncertainty terminates the dedicated pooled
    session so PostgreSQL releases a possibly-held lock.
    """

    async with pool.acquire() as conn:
        acquired = False
        started = time.monotonic()
        try:
            while not acquired:
                try:
                    attempt = await conn.fetchval(_CDC_TRY_LOCK_SQL, CDC_WRITER_BARRIER_ID)
                except BaseException:
                    conn.terminate()
                    raise

                if attempt is True:
                    acquired = True
                    break
                if attempt is not False:
                    conn.terminate()
                    raise RuntimeError("PostgreSQL returned an invalid CDC barrier result")

                elapsed = time.monotonic() - started
                if elapsed >= _CDC_LOCK_TIMEOUT_SECONDS:
                    exporter_cdc_barrier_timeouts.inc()
                    log.error(
                        "cdc_snapshot_barrier.timeout",
                        timeout_s=_CDC_LOCK_TIMEOUT_SECONDS,
                    )
                    raise TimeoutError("timed out waiting for a commit-safe CDC cutoff")
                await asyncio.sleep(
                    min(_CDC_LOCK_RETRY_SECONDS, _CDC_LOCK_TIMEOUT_SECONDS - elapsed)
                )

            wait_s = time.monotonic() - started
            exporter_cdc_barrier_wait.observe(wait_s)
            if wait_s >= _CDC_SLOW_WAIT_SECONDS:
                log.warning(
                    "cdc_snapshot_barrier.acquired_after_wait",
                    wait_s=round(wait_s, 3),
                )

            cutoff = await conn.fetchval(_CDC_CUTOFF_SQL)
            if not isinstance(cutoff, datetime):
                conn.terminate()
                raise RuntimeError("PostgreSQL returned an invalid CDC cutoff")
            return cutoff
        except BaseException:
            if acquired:
                conn.terminate()
            raise
        finally:
            if acquired and not conn.is_closed():
                try:
                    unlocked = await conn.fetchval(_UNLOCK_SQL, CDC_WRITER_BARRIER_ID)
                except BaseException:
                    conn.terminate()
                    raise
                if unlocked is not True:
                    conn.terminate()
                    raise RuntimeError("CDC writer advisory barrier was not held")
