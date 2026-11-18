from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.parametrize("dry_run", [False, True])
async def test_cli_delegates_currency_refresh_before_python_pool_open(monkeypatch, dry_run) -> None:
    import argparse

    from src import cli

    class ExecComplete(Exception):
        pass

    execute = MagicMock(side_effect=ExecComplete)
    pool = AsyncMock(side_effect=AssertionError("Python pool must not open"))
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: argparse.Namespace(command="refresh-currency-rates", dry_run=dry_run),
    )
    monkeypatch.setattr(cli.os, "execvp", execute)
    monkeypatch.setattr(cli, "create_local_pool", pool)
    with pytest.raises(ExecComplete):
        await cli.run()
    args = ["go-typesense-exporter", "--refresh-currency-rates"]
    if dry_run:
        args.append("--dry-run")
    execute.assert_called_once_with(args[0], args)
    pool.assert_not_awaited()
