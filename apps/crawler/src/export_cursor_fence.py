"""Serialize CDC cursor advancement with operator repair writes.

The exporter reads mutable ``updated_at`` rows and advances a keyset cursor.
A bulk repair transaction can choose its ``now()`` timestamp before an
exporter statement snapshot, commit after that snapshot, and therefore land
behind the cursor the exporter just saved.  PostgreSQL advisory locks give the
two code paths a database-scoped fence without coupling their processes or
deployment topology.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import asyncpg

# Stable, repository-owned bigint (ASCII-ish ``JOBSEEK``) shared by the
# exporter and repair command.  Advisory locks are scoped to the local
# PostgreSQL cluster, which is exactly where the cursor and source rows live.
EXPORT_CURSOR_FENCE_ID = 0x4A4F425345454B

CursorFenceFactory = Callable[[asyncpg.Pool], AbstractAsyncContextManager[None]]


@asynccontextmanager
async def export_cursor_fence(pool: asyncpg.Pool) -> AsyncIterator[None]:
    """Hold the database-wide exporter/repair fence for one critical section.

    This is a session advisory lock rather than a transaction lock because the
    guarded section intentionally spans Typesense and Supabase I/O.  A lost
    database connection releases PostgreSQL session locks automatically; the
    explicit unlock keeps a healthy pooled connection safe for reuse.
    """

    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1::bigint)", EXPORT_CURSOR_FENCE_ID)
        try:
            yield
        finally:
            unlocked = await conn.fetchval(
                "SELECT pg_advisory_unlock($1::bigint)",
                EXPORT_CURSOR_FENCE_ID,
            )
            if unlocked is not True:
                raise RuntimeError("export cursor advisory fence was not held")
