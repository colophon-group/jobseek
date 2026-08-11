from __future__ import annotations

from unittest.mock import AsyncMock, patch

import src.db as db


class TestInitLocalConnection:
    """The local pool init must mirror the Supabase init for keepalives, but
    tighten ``statement_timeout`` to 30s so slow worker queries surface as
    errors instead of holding pool slots up to the 5min Supabase ceiling.
    """

    async def test_sets_30s_statement_timeout(self):
        conn = AsyncMock()
        await db._init_local_connection(conn)
        executed = [call.args[0] for call in conn.execute.await_args_list]
        assert "SET statement_timeout = '30s'" in executed

    async def test_sets_idle_in_transaction_timeout(self):
        conn = AsyncMock()
        await db._init_local_connection(conn)
        executed = [call.args[0] for call in conn.execute.await_args_list]
        assert "SET idle_in_transaction_session_timeout = '60s'" in executed

    async def test_sets_tcp_keepalives(self):
        conn = AsyncMock()
        await db._init_local_connection(conn)
        executed = [call.args[0] for call in conn.execute.await_args_list]
        assert "SET tcp_keepalives_idle = 60" in executed
        assert "SET tcp_keepalives_interval = 10" in executed
        assert "SET tcp_keepalives_count = 3" in executed


class TestInitConnection:
    """The Supabase pool init must keep its 5min statement_timeout — the
    exporter performs batch COPYs that legitimately exceed 30s.
    """

    async def test_sets_5min_statement_timeout(self):
        conn = AsyncMock()
        await db._init_connection(conn)
        executed = [call.args[0] for call in conn.execute.await_args_list]
        assert "SET statement_timeout = '5min'" in executed

    async def test_sets_60s_idle_transaction_timeout(self):
        conn = AsyncMock()
        await db._init_connection(conn)
        executed = [call.args[0] for call in conn.execute.await_args_list]
        assert "SET idle_in_transaction_session_timeout = '60s'" in executed


class TestCreateLocalPool:
    """Regression test for #3188: ``create_local_pool`` must wire the
    ``_init_local_connection`` callback so the local Postgres backend
    enforces a statement_timeout server-side, not just the client-side
    ``command_timeout`` (which leaves the backend running after the
    client raises).
    """

    async def test_passes_init_callback(self):
        db._local_pool = None
        try:
            with (
                patch("src.db.asyncpg.create_pool", new_callable=AsyncMock) as create,
                patch("src.db._observe_pool"),
            ):
                create.return_value = object()
                await db.create_local_pool()
                kwargs = create.await_args.kwargs
                assert kwargs.get("init") is db._init_local_connection
        finally:
            db._local_pool = None

    async def test_keeps_client_command_timeout(self):
        """``command_timeout`` is the asyncio-level guard; it must remain so
        a fully unresponsive backend still releases the pool slot."""
        db._local_pool = None
        try:
            with (
                patch("src.db.asyncpg.create_pool", new_callable=AsyncMock) as create,
                patch("src.db._observe_pool"),
            ):
                create.return_value = object()
                await db.create_local_pool()
                kwargs = create.await_args.kwargs
                assert kwargs.get("command_timeout") == 60
        finally:
            db._local_pool = None

    async def test_enforces_owned_pool_budget(self, monkeypatch):
        db._local_pool = None
        monkeypatch.setattr(db.settings, "crawler_db_role", "worker-1")
        monkeypatch.setattr(db.settings, "crawler_db_pool_min", 1)
        monkeypatch.setattr(db.settings, "crawler_db_pool_max", 8)
        monkeypatch.setattr(db.settings, "crawler_db_pool_idle_seconds", 60.0)
        try:
            with (
                patch("src.db.asyncpg.create_pool", new_callable=AsyncMock) as create,
                patch("src.db._observe_pool") as observe,
            ):
                created_pool = AsyncMock()
                create.return_value = created_pool
                await db.create_local_pool()
                kwargs = create.await_args.kwargs
                assert kwargs["min_size"] == 1
                assert kwargs["max_size"] == 8
                assert kwargs["max_inactive_connection_lifetime"] == 60.0
                assert kwargs["server_settings"] == {
                    "application_name": "jobseek:crawler:worker-1:local"
                }
                observe.assert_called_once_with(created_pool, "local")
        finally:
            db._local_pool = None


class TestCreatePool:
    """The Supabase pool wiring must not regress — it still passes the
    long-running ``_init_connection`` (5min).
    """

    async def test_passes_init_callback(self):
        db._pool = None
        try:
            with (
                patch("src.db.asyncpg.create_pool", new_callable=AsyncMock) as create,
                patch("src.db._observe_pool"),
            ):
                create.return_value = object()
                await db.create_pool()
                kwargs = create.await_args.kwargs
                assert kwargs.get("init") is db._init_connection
        finally:
            db._pool = None
