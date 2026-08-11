from __future__ import annotations

import asyncio

import asyncpg

from src.config import settings
from src.metrics import postgresql_pool_connections, postgresql_pool_limit

_pool: asyncpg.Pool | None = None
_web_pool: asyncpg.Pool | None = None
_local_pool: asyncpg.Pool | None = None


def _application_name(pool_name: str) -> str:
    """Return the bounded production owner visible in ``pg_stat_activity``."""
    return f"jobseek:crawler:{settings.crawler_db_role}:{pool_name}"


def _server_settings(pool_name: str, *, statement_timeout: str) -> dict[str, str]:
    """Return connection-startup defaults that survive asyncpg pool reset.

    asyncpg executes ``RESET ALL`` whenever a connection is released. Settings
    installed by a pool ``init`` callback are therefore lost after the first
    checkout. Startup parameters remain the session defaults restored by that
    reset, so every later acquisition retains the same server-side guards.
    """

    return {
        "application_name": _application_name(pool_name),
        "statement_timeout": statement_timeout,
        "idle_in_transaction_session_timeout": "60s",
        # Detect dead clients (OOM kill, network drop) within roughly 90s.
        "tcp_keepalives_idle": "60",
        "tcp_keepalives_interval": "10",
        "tcp_keepalives_count": "3",
    }


def _observe_pool(pool: asyncpg.Pool, pool_name: str) -> None:
    """Expose live ownership without polling or per-connection callbacks."""
    labels = {"role": settings.crawler_db_role, "pool": pool_name}
    postgresql_pool_connections.labels(**labels, state="open").set_function(pool.get_size)
    postgresql_pool_connections.labels(**labels, state="idle").set_function(pool.get_idle_size)
    postgresql_pool_connections.labels(**labels, state="in_use").set_function(
        lambda owned_pool=pool: owned_pool.get_size() - owned_pool.get_idle_size()
    )
    postgresql_pool_limit.labels(**labels, limit="min").set(settings.crawler_db_pool_min)
    postgresql_pool_limit.labels(**labels, limit="max").set(settings.crawler_db_pool_max)


async def _create_pool(
    dsn: str,
    *,
    pool_name: str,
    statement_timeout: str,
) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn,
        min_size=settings.crawler_db_pool_min,
        max_size=settings.crawler_db_pool_max,
        command_timeout=60,
        statement_cache_size=0,
        max_inactive_connection_lifetime=settings.crawler_db_pool_idle_seconds,
        server_settings=_server_settings(
            pool_name,
            statement_timeout=statement_timeout,
        ),
    )
    _observe_pool(pool, pool_name)
    return pool


async def create_pool() -> asyncpg.Pool:
    """Create the optional crawler-mirror pool used by legacy sync/export paths."""
    global _pool
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured for the crawler mirror")
    if _pool is None:
        _pool = await _create_pool(
            settings.database_url,
            pool_name="mirror",
            statement_timeout="5min",
        )
    return _pool


async def create_web_pool() -> asyncpg.Pool:
    """Create the provider-neutral pool for web-owned records such as watchlists."""
    global _web_pool
    if not settings.web_database_url:
        raise RuntimeError("WEB_DATABASE_URL is not configured for web-owned data")
    if _web_pool is None:
        _web_pool = await _create_pool(
            settings.web_database_url,
            pool_name="web",
            statement_timeout="5min",
        )
    return _web_pool


async def create_local_pool() -> asyncpg.Pool:
    """Create the local Postgres pool (same machine, used by workers)."""
    global _local_pool
    if _local_pool is None:
        _local_pool = await _create_pool(
            settings.local_database_url,
            pool_name="local",
            statement_timeout="30s",
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


async def close_web_pool() -> None:
    global _web_pool
    if _web_pool is not None:
        try:
            await asyncio.wait_for(_web_pool.close(), timeout=5.0)
        except TimeoutError:
            _web_pool.terminate()
        _web_pool = None


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
    await close_web_pool()
    await close_local_pool()
