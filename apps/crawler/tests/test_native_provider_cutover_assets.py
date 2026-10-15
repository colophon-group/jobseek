"""Bind installed native retry assets to the original idempotent contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.nw_provider_cutover import _migration_sql as nw_sql
from src.umantis_identity_cutover import (
    _PARK_MONITORS_LUA,
    _REPAIR_SCRAPE_HASHES_LUA,
    _board_query,
    _migration_sql,
    _posting_query,
)


def test_browser_http_route_contracts_match_published_registry() -> None:
    root = Path(__file__).parents[1]
    targets = json.loads(
        (root / "go/typesense-exporter/provider-cutover/browser-http-routes.json").read_text()
    )
    with (root / "data/boards.csv").open(newline="") as file:
        boards = {row["board_slug"]: row for row in csv.DictReader(file)}
    assert len(targets) == 7
    for target in targets:
        row = boards[target["slug"]]
        assert row["board_url"] == target["board_url"]
        assert row["scraper_type"] == target["scraper_type"]
        assert json.loads(row["scraper_config"]) == target["forward_config"]
        assert target["forward_config"]["render"] is False
        assert target["reverse_config"]["render"] is True


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


def test_worker_route_cutover_uses_current_image_after_forward_and_rollback_sync() -> None:
    script = (Path(__file__).parents[1] / "deploy.sh").read_text()
    helper = script.split("repair_worker_route_cutover() {", 1)[1].split("\n}\n", 1)[0]
    assert '"$CRAWLER_IMAGE_REF" go-typesense-exporter --repair-worker-route-cutover' in helper
    assert "-e LOCAL_DATABASE_URL -e REDIS_URL" in helper
    assert '"${MAINTENANCE_PROVENANCE_LABELS[@]}"' in helper
    rollback = script.split("rollback_sync_previous_config() {", 1)[1].split(
        "rollback_compose_service_ready() {", 1
    )[0]
    assert rollback.index("uv run --no-sync crawler sync") < rollback.index(
        "repair_worker_route_cutover rollback-worker-route-cutover"
    )
    assert "repair_umantis_identity_cutover rollback-umantis-identity-cutover 1 &&" in rollback
