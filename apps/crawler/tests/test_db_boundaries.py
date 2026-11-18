from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from src import cli


async def test_reconcile_command_execs_go_without_opening_python_pools(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "reconcile", "--repair"])
    local_pool = AsyncMock()
    monkeypatch.setattr(cli, "create_local_pool", local_pool)

    with (
        patch("src.cli.os.execvp", side_effect=SystemExit(0)) as execute,
        patch("src.db._create_pool", new_callable=AsyncMock) as any_pool,
        pytest.raises(SystemExit),
    ):
        await cli.run()

    assert execute.call_args.args[0] == "go-typesense-exporter"
    command = execute.call_args.args[1]
    assert "--reconcile" in command
    assert command[command.index("--target") + 1] == "typesense"
    local_pool.assert_not_awaited()
    any_pool.assert_not_awaited()


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
        pytest.raises(FileNotFoundError),
    ):
        await cli.run()

    local_pool.assert_not_awaited()


def test_relisted_supabase_repair_is_not_a_crawler_command(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "repair-relisted-cdc", "--dry-run"])
    with pytest.raises(SystemExit):
        cli.parse_args()
