"""Go schema migration contract and CLI ownership boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src import cli
from src.typesense_schema import COLLECTIONS


def test_embedded_go_schema_matches_retained_python_contract() -> None:
    path = Path(__file__).resolve().parents[1] / "go/typesense-exporter/schema_contract.json"
    assert json.loads(path.read_text()) == COLLECTIONS


@pytest.mark.parametrize("force", [False, True])
async def test_schema_setup_execs_go_without_database_pools(monkeypatch, force: bool) -> None:
    args = ["crawler", "setup-typesense"]
    command = ["go-typesense-exporter", "--setup-schemas"]
    if force:
        args.append("--force")
        command.append("--force")
    monkeypatch.setattr(sys, "argv", args)
    pool = AsyncMock()
    monkeypatch.setattr(cli, "create_local_pool", pool)
    with (
        patch("src.cli.os.execvp", side_effect=SystemExit(0)) as execute,
        patch("src.typesense_schema.run_setup") as python_setup,
        pytest.raises(SystemExit),
    ):
        await cli.run()
    execute.assert_called_once_with(command[0], command)
    pool.assert_not_awaited()
    python_setup.assert_not_called()
