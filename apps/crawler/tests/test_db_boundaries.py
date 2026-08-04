from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src import cli, db

BOOTSTRAP = Path(__file__).resolve().parent.parent / "src/bootstrap.py"


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


async def test_export_command_never_opens_or_passes_the_crawler_mirror(monkeypatch) -> None:
    local_pool = object()
    run_exporter = AsyncMock()
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: argparse.Namespace(command="export", batch_size=20, interval=3),
    )
    monkeypatch.setattr(cli, "start_metrics_server", lambda _port: None)
    monkeypatch.setattr(cli, "create_local_pool", AsyncMock(return_value=local_pool))
    monkeypatch.setattr(cli, "close_all_pools", AsyncMock())
    monkeypatch.setattr(cli.settings, "database_url", "postgresql://must-not-open.invalid/mirror")

    with patch("src.exporter.run_exporter", new=run_exporter):
        await cli.run()

    run_exporter.assert_awaited_once()
    assert run_exporter.await_args.args[0] is local_pool
    assert run_exporter.await_args.args[1] is None


async def test_reconcile_command_always_passes_no_mirror_pool(monkeypatch) -> None:
    local_pool = object()
    run_reconciliation = AsyncMock()
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: argparse.Namespace(
            command="reconcile",
            repair=True,
            full=False,
            fresh_cycle=False,
            max_partitions=16,
            start_partition=0,
            target="typesense",
        ),
    )
    monkeypatch.setattr(cli, "create_local_pool", AsyncMock(return_value=local_pool))
    monkeypatch.setattr(cli, "close_all_pools", AsyncMock())
    monkeypatch.setattr(cli.settings, "database_url", "postgresql://must-not-open.invalid/mirror")

    with patch("src.reconciliation.run_reconciliation", new=run_reconciliation):
        await cli.run()

    run_reconciliation.assert_awaited_once()
    assert run_reconciliation.await_args.args == (local_pool, None)
    assert run_reconciliation.await_args.kwargs["target_scope"] == "typesense"


def test_relisted_supabase_repair_is_not_a_crawler_command(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "repair-relisted-cdc", "--dry-run"])
    with pytest.raises(SystemExit):
        cli.parse_args()


def test_transitional_bootstrap_has_no_executable_entrypoint() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")

    assert 'if __name__ == "__main__"' not in source
    assert "async def main(" not in source
