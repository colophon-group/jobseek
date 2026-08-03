from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src import cli, db


async def test_crawler_mirror_pool_requires_database_url(monkeypatch) -> None:
    monkeypatch.setattr(db.settings, "database_url", "")
    monkeypatch.setattr(db, "_pool", None)

    with (
        patch("src.db.asyncpg.create_pool", new_callable=AsyncMock) as create,
        pytest.raises(RuntimeError, match="DATABASE_URL is not configured"),
    ):
        await db.create_pool()

    create.assert_not_awaited()


async def test_web_pool_uses_only_the_provider_neutral_url(monkeypatch) -> None:
    sentinel = object()
    create = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(db.settings, "database_url", "postgresql://mirror.invalid/db")
    monkeypatch.setattr(db.settings, "web_database_url", "postgresql://web.invalid/db")
    monkeypatch.setattr(db, "_web_pool", None)

    with patch("src.db.asyncpg.create_pool", new=create):
        pool = await db.create_web_pool()

    assert pool is sentinel
    assert create.await_args.args[0] == "postgresql://web.invalid/db"
    monkeypatch.setattr(db, "_web_pool", None)


async def test_web_pool_does_not_fall_back_to_the_crawler_mirror(monkeypatch) -> None:
    monkeypatch.setattr(db.settings, "database_url", "postgresql://mirror.invalid/db")
    monkeypatch.setattr(db.settings, "web_database_url", "")
    monkeypatch.setattr(db, "_web_pool", None)

    with (
        patch("src.db.asyncpg.create_pool", new_callable=AsyncMock) as create,
        pytest.raises(RuntimeError, match="WEB_DATABASE_URL is not configured"),
    ):
        await db.create_web_pool()

    create.assert_not_awaited()


async def test_optional_mirror_pool_skips_connection_without_database_url(monkeypatch) -> None:
    create = AsyncMock()
    monkeypatch.setattr(cli.settings, "database_url", "")
    monkeypatch.setattr(cli, "create_pool", create)

    assert await cli._create_optional_mirror_pool() is None
    create.assert_not_awaited()
