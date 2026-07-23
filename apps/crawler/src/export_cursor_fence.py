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
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import asyncpg
import structlog

log = structlog.get_logger()

# Stable, repository-owned bigint (ASCII-ish ``JOBSEEK``) shared by the
# exporter and repair command.  Advisory locks are scoped to the local
# PostgreSQL cluster, which is exactly where the cursor and source rows live.
EXPORT_CURSOR_FENCE_ID = 0x4A4F425345454B
_FENCE_RETRY_SECONDS = 1.0
_TRY_LOCK_SQL = "SELECT pg_try_advisory_lock($1::bigint)"
_UNLOCK_SQL = "SELECT pg_advisory_unlock($1::bigint)"

CursorFenceFactory = Callable[[asyncpg.Pool], AbstractAsyncContextManager[None]]


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
