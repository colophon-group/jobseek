"""Real PostgreSQL lifecycle proof for asyncpg pool session guards."""

from __future__ import annotations

import os

import pytest

from src import db

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated PostgreSQL",
)


async def _session_settings(connection) -> tuple[int, int, int, int, int, str]:
    row = await connection.fetchrow(
        """
        SELECT
          (SELECT setting::int FROM pg_settings WHERE name = 'statement_timeout'),
          (SELECT setting::int FROM pg_settings
            WHERE name = 'idle_in_transaction_session_timeout'),
          current_setting('tcp_keepalives_idle')::int,
          current_setting('tcp_keepalives_interval')::int,
          current_setting('tcp_keepalives_count')::int,
          current_setting('application_name')
        """
    )
    assert row is not None
    return (
        int(row[0]),
        int(row[1]),
        int(row[2]),
        int(row[3]),
        int(row[4]),
        str(row[5]),
    )


async def test_pool_release_and_reacquire_restore_server_side_guards(monkeypatch) -> None:
    """asyncpg's release-time RESET ALL must restore, not erase, guards."""

    monkeypatch.setattr(db.settings, "crawler_db_role", "e2e")
    monkeypatch.setattr(db.settings, "crawler_db_pool_min", 1)
    monkeypatch.setattr(db.settings, "crawler_db_pool_max", 1)
    pool = await db._create_pool(
        os.environ["LOCAL_DATABASE_URL"],
        pool_name="lifecycle",
        statement_timeout="30s",
    )
    expected = (30_000, 60_000, 60, 10, 3, "jobseek:crawler:e2e:lifecycle")
    try:
        async with pool.acquire() as connection:
            assert await _session_settings(connection) == expected
            await connection.execute("SET statement_timeout = '1s'")
            await connection.execute("SET idle_in_transaction_session_timeout = '2s'")
            await connection.execute("SET application_name = 'mutated-before-release'")

        async with pool.acquire() as connection:
            assert await _session_settings(connection) == expected
    finally:
        await pool.close()
