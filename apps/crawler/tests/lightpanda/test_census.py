from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.lightpanda.census import CensusError, build_manifest, check_manifest, manifest_bytes

_COLUMNS: tuple[str, ...] = (
    "company_slug",
    "board_slug",
    "board_url",
    "monitor_type",
    "monitor_config",
    "scraper_type",
    "scraper_config",
)


def _write_boards(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(
    slug: str,
    *,
    monitor_type: str = "greenhouse",
    monitor_config: dict[str, object] | None = None,
    scraper_type: str = "json-ld",
    scraper_config: dict[str, object] | None = None,
) -> dict[str, str]:
    return {
        "company_slug": "secret-company",
        "board_slug": slug,
        "board_url": f"https://secret.example/{slug}?token=do-not-commit",
        "monitor_type": monitor_type,
        "monitor_config": json.dumps(monitor_config or {}, separators=(",", ":")),
        "scraper_type": scraper_type,
        "scraper_config": json.dumps(scraper_config or {}, separators=(",", ":")),
    }


def test_recursive_census_is_sanitized_and_deterministic(tmp_path: Path) -> None:
    boards = _write_boards(
        tmp_path / "boards.csv",
        [
            _row(
                "browser-monitor",
                monitor_type="dom",
                monitor_config={
                    "actions": [
                        {"action": "click", "selector": "#secret-selector", "required": True},
                        {"action": "evaluate", "script": "window.__secret = 'value'"},
                    ],
                    "bot_protection": False,
                    "render": True,
                    "resource_policy": "auto",
                    "wait": "networkidle",
                },
            ),
            _row(
                "kpmg-like",
                scraper_config={
                    "fallback": {
                        "config": {"render": True, "wait": "networkidle"},
                        "type": "dom",
                    }
                },
            ),
            _row(
                "nested-chain",
                scraper_type="dom",
                scraper_config={
                    "fallback": {
                        "config": {"fallback": {"config": {"render": True}, "type": "nextdata"}},
                        "type": "json-ld",
                    },
                    "render": True,
                },
            ),
        ],
    )

    first = build_manifest(boards)
    second = build_manifest(boards)

    assert manifest_bytes(first) == manifest_bytes(second)
    assert first["summary"]["browser_board_count"] == 3
    assert first["summary"]["configured_profile_occurrence_count"] == 6
    assert first["summary"]["browser_required_step_count"] == 4
    configured = [record for record in first["records"] if record["profile_kind"] == "configured"]
    assert any(
        record["crawler_type"] == "dom"
        and record["chain_role"] == "fallback"
        and record["browser_required"]
        for record in configured
    )
    assert any(
        record["crawler_type"] == "json-ld"
        and record["chain_role"] == "primary"
        and not record["browser_required"]
        for record in configured
    )
    rendered = manifest_bytes(first).decode("ascii")
    for secret in (
        "secret-company",
        "secret.example",
        "do-not-commit",
        "#secret-selector",
        "window.__secret",
    ):
        assert secret not in rendered
    assert all(len(record["digest_sha256"]) == 64 for record in first["records"])
    assert len(first["manifest_sha256"]) == 64


def test_nextdata_item_inclusions_are_sanitized_and_tracked(tmp_path: Path) -> None:
    boards = _write_boards(
        tmp_path / "boards.csv",
        [
            _row(
                "filtered-nextdata",
                monitor_type="nextdata",
                monitor_config={
                    "include_item_values": {"company": ["secret-tenant"]},
                    "render": True,
                },
            )
        ],
    )

    manifest = build_manifest(boards)
    rendered = manifest_bytes(manifest).decode("ascii")

    assert any(
        record["profile_kind"] == "configured" and record["crawler_type"] == "nextdata"
        for record in manifest["records"]
    )
    assert "secret-tenant" not in rendered


def test_registry_includes_zero_config_browser_types(tmp_path: Path) -> None:
    boards = _write_boards(tmp_path / "boards.csv", [_row("static")])

    manifest = build_manifest(boards)
    records = {record["id"]: record for record in manifest["records"]}

    assert records["registry.monitor.darwinbox"]["source_count"] == 0
    assert records["registry.monitor.darwinbox"]["status"] == "zero_browser_config"
    assert records["registry.scraper.embedded"]["status"] == "zero_browser_config"
    assert records["registry.scraper.api_sniffer"]["browser_required"] is True


@pytest.mark.parametrize(
    "monitor_config",
    [
        {"render": True, "unknown_browser_key": True},
        {"actions": [{"action": "teleport"}], "render": True},
        {"actions": [{"action": "click", "selector": ["#invalid"]}], "render": True},
        {"actions": [{"action": "click", "selector": "#ok", "unknown": True}], "render": True},
        {"render": True, "resource_policy": []},
    ],
)
def test_browser_monitor_config_fails_closed(
    tmp_path: Path, monitor_config: dict[str, object]
) -> None:
    boards = _write_boards(
        tmp_path / "boards.csv",
        [_row("invalid-monitor", monitor_type="dom", monitor_config=monitor_config)],
    )

    with pytest.raises(CensusError):
        build_manifest(boards)


@pytest.mark.parametrize(
    "row",
    [
        _row("monitor-typo", monitor_type="dom", monitor_config={"rendr": True}),
        _row("scraper-typo", scraper_type="dom", scraper_config={"rendr": True}),
        _row(
            "fallback-typo",
            scraper_config={"fallback": {"type": "dom", "config": {"rendr": True}}},
        ),
    ],
)
def test_browser_capable_config_fails_closed_before_relevance_filter(
    tmp_path: Path, row: dict[str, str]
) -> None:
    boards = _write_boards(tmp_path / "boards.csv", [row])

    with pytest.raises(CensusError, match="unknown (monitor|scraper) config keys"):
        build_manifest(boards)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_fail_closed(tmp_path: Path, constant: str) -> None:
    row = _row("nonfinite", monitor_type="dom")
    row["monitor_config"] = f'{{"render":true,"defaults":{constant}}}'
    boards = _write_boards(tmp_path / "boards.csv", [row])

    with pytest.raises(CensusError, match="non-standard JSON constant"):
        build_manifest(boards)


def test_boolean_action_integer_fails_closed(tmp_path: Path) -> None:
    boards = _write_boards(
        tmp_path / "boards.csv",
        [
            _row(
                "boolean-page-size",
                monitor_type="dom",
                monitor_config={
                    "actions": [{"action": "paginate_collect", "page_size": True}],
                    "render": True,
                },
            )
        ],
    )

    with pytest.raises(CensusError, match="page_size must be a string or integer"):
        build_manifest(boards)


@pytest.mark.parametrize(
    "rich_rows",
    [
        "not-an-object",
        {"row_selector": ".job", "unknown": "secret-value"},
        {"link_selector": "a"},
        {"row_selector": ".job", "allow_missing_locations": 1},
        {"row_selector": ".job", "section_start": {"selector": "h2"}},
        {
            "row_selector": ".job",
            "active_urls": ["https://secret.example/jobs/active"],
        },
        {"row_selector": ".job", "location_selectors": [".location"] * 5},
        {
            "row_selector": ".job",
            "metadata_selectors": {f"field-{index}": ".value" for index in range(9)},
        },
        {"row_selector": "a["},
        {
            "row_selector": ".job",
            "active_urls": ["https://secret.example/jobs/shared"],
            "inactive_urls": ["https://secret.example/jobs/shared"],
        },
    ],
    ids=[
        "non-mapping",
        "unknown-key",
        "missing-row-selector",
        "non-boolean-flag",
        "unpaired-boundary",
        "unpaired-lifecycle-urls",
        "location-selector-bound",
        "metadata-selector-bound",
        "malformed-css",
        "overlapping-lifecycle-urls",
    ],
)
def test_dom_rich_rows_uses_authoritative_fail_closed_validation(
    tmp_path: Path, rich_rows: object
) -> None:
    boards = _write_boards(
        tmp_path / "boards.csv",
        [_row("invalid-rich-rows", monitor_type="dom", monitor_config={"rich_rows": rich_rows})],
    )

    with pytest.raises(CensusError) as exc_info:
        build_manifest(boards)

    assert str(exc_info.value) == "monitor.dom.rich_rows is invalid"


@pytest.mark.parametrize(
    "rich_rows",
    [
        {"row_selector": ".job", "location_selectors": [], "metadata_selectors": {}},
        {
            "row_selector": ".job",
            "location_selectors": [".city", ".region", ".country"],
        },
        {"row_selector": ".job", "metadata_selectors": {"department": ".department"}},
        {
            "row_selector": ".job",
            "section_start": {"selector": "h2", "text": "Current roles"},
            "section_end": {"selector": "h2#students"},
        },
    ],
    ids=["empty-selectors", "three-location-selectors", "generic-metadata", "paired-boundary"],
)
def test_dom_rich_rows_accepts_authoritative_runtime_shapes(
    tmp_path: Path, rich_rows: dict[str, object]
) -> None:
    boards = _write_boards(
        tmp_path / "boards.csv",
        [
            _row(
                "valid-rich-rows",
                monitor_type="dom",
                monitor_config={"render": True, "rich_rows": rich_rows},
            )
        ],
    )

    manifest = build_manifest(boards)

    assert manifest["summary"]["browser_board_count"] == 1
    assert manifest["summary"]["browser_required_step_count"] == 1


@pytest.mark.parametrize(
    "fallback",
    [
        ["dom"],
        {"type": "unknown"},
        {"type": "dom", "unknown": True},
        {"type": "dom", "config": []},
        {"type": "dom", "fields": ["not_a_job_field"]},
    ],
)
def test_fallback_shape_fails_closed(tmp_path: Path, fallback: object) -> None:
    boards = _write_boards(
        tmp_path / "boards.csv",
        [_row("invalid-fallback", scraper_config={"fallback": fallback})],
    )

    with pytest.raises(CensusError):
        build_manifest(boards)


def test_committed_manifest_is_current_and_contains_kpmg_fallback() -> None:
    manifest = check_manifest()

    assert manifest["input"]["network_access"] is False
    assert manifest["summary"]["browser_board_count"] == 462
    assert manifest["summary"]["browser_required_step_count"] == 601
    assert manifest["summary"]["configured_profile_occurrence_count"] == 603
    assert any(
        record["profile_kind"] == "configured"
        and record["surface"] == "scraper"
        and record["crawler_type"] == "dom"
        and record["chain_role"] == "fallback"
        and record["browser_required"] is True
        for record in manifest["records"]
    )
