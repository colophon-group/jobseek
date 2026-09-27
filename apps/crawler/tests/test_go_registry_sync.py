"""Production CLI ownership and retained reference contracts for Go sync."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src import cli, sync
from src.core import scrapers


@pytest.mark.parametrize("checkout", [True, False])
async def test_sync_execs_go_before_python_pools_or_sync(monkeypatch, checkout):
    monkeypatch.setattr(sys, "argv", ["crawler", "sync"])
    monkeypatch.setattr(cli, "is_source_checkout", lambda: checkout)
    monkeypatch.setattr(cli, "get_data_dir", lambda: Path("/fixture/checkout/data"))
    pool = AsyncMock()
    monkeypatch.setattr(cli, "create_local_pool", pool)
    expected = ["go-typesense-exporter", "--sync-registry"]
    if checkout:
        expected.extend(["--source-data-dir", "/fixture/checkout/data"])
    with (
        patch("src.cli.os.execvp", side_effect=SystemExit(0)) as execute,
        patch("src.sync.run_sync") as reference,
        pytest.raises(SystemExit),
    ):
        await cli.run()
    execute.assert_called_once_with(expected[0], expected)
    pool.assert_not_awaited()
    reference.assert_not_called()


def test_go_registry_embedded_sql_and_routing_match_reference():
    root = Path(__file__).resolve().parents[1] / "go/typesense-exporter"
    for key, value in json.loads((root / "registry_sql.json").read_text()).items():
        expected = (
            "UPDATE occupation SET parent_id = NULL WHERE parent_id IS NOT NULL"
            if key == "clear_all_occupation_parents"
            else getattr(sync, key)
        )
        assert value == expected, key
    routes = json.loads((root / "registry_routes.json").read_text())
    assert routes["api_monitors"] == sorted(sync._API_MONITOR_TYPES)
    assert routes["scrapers"] == {
        name: entry.needs_browser for name, entry in scrapers._REGISTRY.items()
    }
    assert routes["render_scrapers"] == sorted(scrapers._RENDER_AWARE_SCRAPERS)


def test_standalone_sync_dry_run_execs_go(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sync", "--dry-run"])
    monkeypatch.setattr(sync, "is_source_checkout", lambda: False)
    with patch("os.execvp", side_effect=SystemExit(0)) as execute, pytest.raises(SystemExit):
        sync.main()
    execute.assert_called_once_with(
        "go-typesense-exporter", ["go-typesense-exporter", "--sync-registry", "--dry-run"]
    )
