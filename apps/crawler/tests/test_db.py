from __future__ import annotations

from unittest.mock import AsyncMock, patch

import src.db as db


class TestCreateLocalPool:
    """The local pool must install persistent startup-level guards."""

    async def test_passes_persistent_server_settings(self):
        db._local_pool = None
        try:
            with (
                patch("src.db.asyncpg.create_pool", new_callable=AsyncMock) as create,
                patch("src.db._observe_pool"),
            ):
                create.return_value = object()
                await db.create_local_pool()
                kwargs = create.await_args.kwargs
                assert "init" not in kwargs
                assert kwargs["server_settings"] == {
                    "application_name": "jobseek:crawler:oneoff:local",
                    "statement_timeout": "30s",
                    "idle_in_transaction_session_timeout": "60s",
                    "tcp_keepalives_idle": "60",
                    "tcp_keepalives_interval": "10",
                    "tcp_keepalives_count": "3",
                }
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
                    "application_name": "jobseek:crawler:worker-1:local",
                    "statement_timeout": "30s",
                    "idle_in_transaction_session_timeout": "60s",
                    "tcp_keepalives_idle": "60",
                    "tcp_keepalives_interval": "10",
                    "tcp_keepalives_count": "3",
                }
                observe.assert_called_once_with(created_pool, "local")
        finally:
            db._local_pool = None


class TestCreatePool:
    """The mirror pool retains its longer persistent statement guard."""

    async def test_passes_persistent_server_settings(self):
        db._pool = None
        try:
            with (
                patch("src.db.asyncpg.create_pool", new_callable=AsyncMock) as create,
                patch("src.db._observe_pool"),
            ):
                create.return_value = object()
                await db.create_pool()
                kwargs = create.await_args.kwargs
                assert "init" not in kwargs
                assert kwargs["server_settings"]["statement_timeout"] == "5min"
                assert kwargs["server_settings"]["idle_in_transaction_session_timeout"] == "60s"
        finally:
            db._pool = None
