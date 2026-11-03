from __future__ import annotations

import argparse
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from src import cli


def test_cli_exposes_only_the_fixed_cardinality_repair_command(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "repair-location-taxonomy-source"])
    args = cli.parse_args()
    assert args.command == "repair-location-taxonomy-source"
    assert vars(args) == {"command": "repair-location-taxonomy-source"}


async def test_cli_delegates_repair_before_opening_python_database_pools(monkeypatch) -> None:
    class ExecComplete(Exception):
        pass

    exec_call = MagicMock(side_effect=ExecComplete)
    local_pool = AsyncMock(side_effect=AssertionError("Python local pool must not open"))
    web_pool = AsyncMock(side_effect=AssertionError("Python web pool must not open"))
    monkeypatch.setattr(
        cli, "parse_args", lambda: argparse.Namespace(command="repair-location-taxonomy-source")
    )
    monkeypatch.setattr(cli.os, "execvp", exec_call)
    monkeypatch.setattr(cli, "create_local_pool", local_pool)
    monkeypatch.setattr("src.db.create_web_pool", web_pool)
    with pytest.raises(ExecComplete):
        await cli.run()
    exec_call.assert_called_once_with(
        "go-typesense-exporter", ["go-typesense-exporter", "--repair-location-taxonomy-source"]
    )
    local_pool.assert_not_awaited()
    web_pool.assert_not_awaited()
