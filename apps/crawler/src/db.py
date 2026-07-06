from __future__ import annotations

import asyncio

import asyncpg

from src.config import settings

_pool: asyncpg.Pool | None = None
_local_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.execute("SET statement_timeout = '5min'")
    await conn.execute("SET idle_in_transaction_session_timeout = '5min'")
    # Detect dead clients (OOM kill, network drop) within ~90s
    await conn.execute("SET tcp_keepalives_idle = 60")
    await conn.execute("SET tcp_keepalives_interval = 10")
    await conn.execute("SET tcp_keepalives_count = 3")


async def _init_local_connection(conn: asyncpg.Connection) -> None:
    """Init for the local Postgres pool used by workers.

    Tighter ``statement_timeout`` (30s) than the Supabase pool because worker
    queries are all narrow single-row reads or small upserts — a slow query is
    a bug to surface, not a feature to wait through. Keepalives match the
    remote pool: they cost nothing on the local socket but recycle dead
    connections within ~90s if the Postgres backend ever becomes unreachable.
    """

    await conn.execute("SET statement_timeout = '30s'")
    await conn.execute("SET idle_in_transaction_session_timeout = '5min'")
    await conn.execute("SET tcp_keepalives_idle = 60")
    await conn.execute("SET tcp_keepalives_interval = 10")
    await conn.execute("SET tcp_keepalives_count = 3")


async def create_pool() -> asyncpg.Pool:
    """Create the Supabase pool (remote, used by exporter + sync only)."""
    global _pool
    if _pool is None:
        max_size = settings.crawler_db_pool_max or (
            settings.crawler_max_concurrent + settings.crawler_max_browser
        )
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=max_size,
            command_timeout=60,
            statement_cache_size=0,
            max_inactive_connection_lifetime=300.0,
            init=_init_connection,
        )
    return _pool


async def create_local_pool() -> asyncpg.Pool:
    """Create the local Postgres pool (same machine, used by workers)."""
    global _local_pool
    if _local_pool is None:
        _local_pool = await asyncpg.create_pool(
            settings.local_database_url,
            min_size=1,
            max_size=settings.crawler_db_pool_max or 10,
            command_timeout=60,
            statement_cache_size=0,
            max_inactive_connection_lifetime=300.0,
            init=_init_local_connection,
        )
    return _local_pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        try:
            await asyncio.wait_for(_pool.close(), timeout=5.0)
        except TimeoutError:
            _pool.terminate()
        _pool = None


async def close_local_pool() -> None:
    global _local_pool
    if _local_pool is not None:
        try:
            await asyncio.wait_for(_local_pool.close(), timeout=5.0)
        except TimeoutError:
            _local_pool.terminate()
        _local_pool = None


async def close_all_pools() -> None:
    await close_pool()
    await close_local_pool()
