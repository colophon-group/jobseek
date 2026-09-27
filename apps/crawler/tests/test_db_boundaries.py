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

    with patch("src.db.asyncpg.create_pool", new=create), patch("src.db._observe_pool"):
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


async def test_reconcile_command_execs_go_without_opening_python_pools(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "reconcile", "--repair"])
    local_pool = AsyncMock()
    monkeypatch.setattr(cli, "create_local_pool", local_pool)
    monkeypatch.setattr(cli.settings, "database_url", "postgresql://must-not-open.invalid/mirror")

    with (
        patch("src.cli.os.execvp", side_effect=SystemExit(0)) as execute,
        patch("src.db.create_pool", new_callable=AsyncMock) as mirror_pool,
        patch("src.reconciliation.run_reconciliation", new_callable=AsyncMock) as python_run,
        pytest.raises(SystemExit),
    ):
        await cli.run()

    assert execute.call_args.args[0] == "go-typesense-exporter"
    command = execute.call_args.args[1]
    assert "--reconcile" in command
    assert command[command.index("--target") + 1] == "typesense"
    local_pool.assert_not_awaited()
    mirror_pool.assert_not_awaited()
    python_run.assert_not_awaited()


async def test_failed_go_reconcile_exec_cannot_issue_python_receipt(monkeypatch) -> None:
    # Runtime cancellation/ledger safety is covered by the real PostgreSQL Go
    # integration test. The shim must never fall back to Python after exec fails.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crawler",
            "reconcile",
            "--repair",
            "--full",
            "--fresh-cycle",
            "--candidate-order-benchmark-sha256",
            "a" * 64,
        ],
    )
    local_pool = AsyncMock()
    monkeypatch.setattr(cli, "create_local_pool", local_pool)
    with (
        patch("src.cli.os.execvp", side_effect=FileNotFoundError("missing Go executable")),
        patch(
            "src.reconciliation.issue_candidate_order_readiness_receipt", new_callable=AsyncMock
        ) as receipt,
        pytest.raises(FileNotFoundError),
    ):
        await cli.run()

    local_pool.assert_not_awaited()
    receipt.assert_not_awaited()


def test_relisted_supabase_repair_is_not_a_crawler_command(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "repair-relisted-cdc", "--dry-run"])
    with pytest.raises(SystemExit):
        cli.parse_args()


def test_transitional_bootstrap_has_no_executable_entrypoint() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")

    assert 'if __name__ == "__main__"' not in source
    assert "async def main(" not in source
