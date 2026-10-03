"""Bind installed native retry assets to the original idempotent contracts."""

from __future__ import annotations

from pathlib import Path

from src.nw_provider_cutover import _migration_sql as nw_sql
from src.umantis_identity_cutover import (
    _PARK_MONITORS_LUA,
    _REPAIR_SCRAPE_HASHES_LUA,
    _board_query,
    _migration_sql,
    _posting_query,
)


def test_native_provider_retry_assets_match_existing_python_contracts() -> None:
    root = Path(__file__).parents[1] / "go/typesense-exporter/provider-cutover"
    expected = {
        "nw.sql": nw_sql(),
        "umantis.sql": _migration_sql(),
        "umantis-postings.sql": _posting_query(),
        "umantis-boards.sql": _board_query(),
        "umantis-scrapes.lua": _REPAIR_SCRAPE_HASHES_LUA,
        "umantis-park.lua": _PARK_MONITORS_LUA,
    }
    for name, body in expected.items():
        assert (root / name).read_text() == body


def test_forward_deploy_uses_native_repairs_and_preserves_historical_rollback_cli() -> None:
    script = (Path(__file__).parents[1] / "deploy.sh").read_text()
    function = script.split("repair_umantis_identity_cutover() {", 1)[1].split("\n}\n", 1)[0]
    assert (
        "local -a command_args=(go-typesense-exporter --repair-umantis-identity-cutover)"
        in function
    )
    assert 'if [[ "$park_monitors" == 1 ]]' in function
    assert (
        "command_args=(uv run --no-sync crawler repair-umantis-identity-cutover --park-monitors)"
        in function
    )
    assert "-e REDIS_URL" in function
    assert '"${command_args[@]}"' in function
    assert "uv run --no-sync crawler repair-nw-provider-cutover" not in script
