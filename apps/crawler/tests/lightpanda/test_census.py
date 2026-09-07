from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from src.lightpanda.census import (
    DEFAULT_MANIFEST_PATH,
    CensusError,
    _sha256,
    build_manifest,
    compare_baseline_evolution,
    compare_semantic_manifests,
    manifest_bytes,
)

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


def _refresh_manifest_integrity(manifest: dict[str, Any]) -> None:
    records = manifest["records"]
    assert isinstance(records, list)
    for record in records:
        assert isinstance(record, dict)
        payload = {key: value for key, value in record.items() if key != "digest_sha256"}
        record["digest_sha256"] = _sha256(payload)

    configured = [record for record in records if record["profile_kind"] == "configured"]
    registry = [record for record in records if record["profile_kind"] == "registry"]
    summary = manifest["summary"]
    assert isinstance(summary, dict)
    summary.update(
        {
            "browser_required_step_count": sum(
                record["source_count"] for record in configured if record["browser_required"]
            ),
            "configured_profile_occurrence_count": sum(
                record["source_count"] for record in configured
            ),
            "configured_record_count": len(configured),
            "registry_record_count": len(registry),
            "total_record_count": len(records),
            "zero_browser_config_registry_count": sum(
                record["status"] == "zero_browser_config" for record in registry
            ),
        }
    )
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    manifest["manifest_sha256"] = _sha256(payload)


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
                    "resource_policy": "none",
                    "wait_fallback": "domcontentloaded",
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
    "states",
    [
        [],
        [{"selector": ".status"}],
        [{"selector": "a[", "exact_text": "Closed"}],
        [{"selector": ".status", "exact_text": "", "unexpected": True}],
    ],
)
def test_dom_inactive_detail_states_use_runtime_validation(tmp_path: Path, states: object) -> None:
    boards = _write_boards(
        tmp_path / "boards.csv",
        [
            _row(
                "invalid-inactive-state",
                monitor_type="dom",
                monitor_config={
                    "link_selector": "a.job",
                    "inactive_detail_states": states,
                },
            )
        ],
    )

    with pytest.raises(CensusError) as exc_info:
        build_manifest(boards)

    assert str(exc_info.value) == "monitor.dom.inactive_detail_states is invalid"


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


def test_semantic_baseline_ignores_catalog_provenance_churn(tmp_path: Path) -> None:
    baseline = build_manifest(
        _write_boards(
            tmp_path / "baseline.csv",
            [_row("first", monitor_type="dom", monitor_config={"render": True})],
        )
    )
    current = build_manifest(
        _write_boards(
            tmp_path / "current.csv",
            [
                _row("first", monitor_type="dom", monitor_config={"render": True}),
                _row("second", monitor_type="dom", monitor_config={"render": True}),
            ],
        )
    )

    comparison = compare_semantic_manifests(baseline, current)

    assert comparison["new_configured_profile_ids"] == []
    assert baseline["input"]["boards_sha256"] != current["input"]["boards_sha256"]
    baseline_profile = next(
        record for record in baseline["records"] if record["profile_kind"] == "configured"
    )
    current_profile = next(
        record for record in current["records"] if record["id"] == baseline_profile["id"]
    )
    assert baseline_profile["source_count"] == 1
    assert current_profile["source_count"] == 2


def test_semantic_baseline_rejects_duplicate_ids_before_mapping(tmp_path: Path) -> None:
    baseline = build_manifest(_write_boards(tmp_path / "boards.csv", [_row("static")]))
    current = deepcopy(baseline)
    current["records"].append(deepcopy(current["records"][0]))

    with pytest.raises(CensusError, match="duplicate record ids"):
        compare_semantic_manifests(baseline, current)


def test_semantic_baseline_validates_ignored_record_provenance_digest(tmp_path: Path) -> None:
    baseline = build_manifest(_write_boards(tmp_path / "boards.csv", [_row("static")]))
    current = deepcopy(baseline)
    current["records"][0]["source_count"] += 1

    with pytest.raises(CensusError, match="record .* digest does not match"):
        compare_semantic_manifests(baseline, current)


def test_semantic_baseline_validates_manifest_digest_for_ignored_input_provenance(
    tmp_path: Path,
) -> None:
    baseline = build_manifest(_write_boards(tmp_path / "boards.csv", [_row("static")]))
    current = deepcopy(baseline)
    current["input"]["boards_sha256"] = "0" * 64

    with pytest.raises(CensusError, match="manifest digest does not match"):
        compare_semantic_manifests(baseline, current)


def test_semantic_baseline_validates_summary_self_consistency(tmp_path: Path) -> None:
    baseline = build_manifest(_write_boards(tmp_path / "boards.csv", [_row("static")]))
    current = deepcopy(baseline)
    current["summary"]["total_record_count"] += 1
    payload = {key: value for key, value in current.items() if key != "manifest_sha256"}
    current["manifest_sha256"] = _sha256(payload)

    with pytest.raises(CensusError, match="summary.total_record_count is inconsistent"):
        compare_semantic_manifests(baseline, current)


def test_semantic_baseline_requires_sorted_unique_capabilities(tmp_path: Path) -> None:
    baseline = build_manifest(
        _write_boards(
            tmp_path / "boards.csv",
            [_row("browser", monitor_type="dom", monitor_config={"render": True})],
        )
    )
    current = deepcopy(baseline)
    profile = next(
        record for record in current["records"] if record["profile_kind"] == "configured"
    )
    profile["capabilities"].append(profile["capabilities"][0])
    _refresh_manifest_integrity(current)

    with pytest.raises(CensusError, match="capabilities must be sorted and unique"):
        compare_semantic_manifests(baseline, current)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("format", "jobseek.lightpanda.capability-census/v2", "format must be"),
        ("network_access", True, "network_access=false"),
        ("sanitization", "unsafe", "sanitization marker is invalid"),
    ],
)
def test_semantic_baseline_preserves_top_level_safety_markers(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    baseline = build_manifest(_write_boards(tmp_path / "boards.csv", [_row("static")]))
    current = deepcopy(baseline)
    if field == "format":
        current[field] = value
    else:
        current["input"][field] = value
    _refresh_manifest_integrity(current)

    with pytest.raises(CensusError, match=message):
        compare_semantic_manifests(baseline, current)


def test_semantic_baseline_rejects_changed_stable_profile_fields(tmp_path: Path) -> None:
    baseline = build_manifest(
        _write_boards(
            tmp_path / "boards.csv",
            [_row("browser", monitor_type="dom", monitor_config={"render": True})],
        )
    )
    current = deepcopy(baseline)
    profile = next(
        record for record in current["records"] if record["profile_kind"] == "configured"
    )
    profile["capabilities"].append("navigation.wait.load")
    profile["capabilities"].sort()
    _refresh_manifest_integrity(current)

    with pytest.raises(CensusError, match="changed pinned stable profile semantics"):
        compare_semantic_manifests(baseline, current)


def test_semantic_baseline_rejects_removed_or_renamed_configured_profile(
    tmp_path: Path,
) -> None:
    baseline = build_manifest(
        _write_boards(
            tmp_path / "baseline.csv",
            [
                _row(
                    "browser",
                    monitor_type="dom",
                    monitor_config={"render": True, "wait": "networkidle"},
                )
            ],
        )
    )
    current = build_manifest(
        _write_boards(
            tmp_path / "current.csv",
            [_row("browser", monitor_type="dom", monitor_config={"render": True})],
        )
    )

    with pytest.raises(CensusError, match="removed or renamed pinned configured profiles"):
        compare_semantic_manifests(baseline, current)


def test_semantic_baseline_requires_exact_registry_id_set(tmp_path: Path) -> None:
    baseline = build_manifest(_write_boards(tmp_path / "boards.csv", [_row("static")]))
    current = deepcopy(baseline)
    current["records"] = [
        record for record in current["records"] if record["id"] != "registry.monitor.darwinbox"
    ]
    _refresh_manifest_integrity(current)

    with pytest.raises(CensusError, match="removed or renamed pinned registry profiles"):
        compare_semantic_manifests(baseline, current)


def test_new_pending_profile_requires_an_exact_append_only_baseline_update(tmp_path: Path) -> None:
    baseline = build_manifest(
        _write_boards(
            tmp_path / "baseline.csv",
            [_row("first", monitor_type="dom", monitor_config={"render": True})],
        )
    )
    current = build_manifest(
        _write_boards(
            tmp_path / "current.csv",
            [
                _row("first", monitor_type="dom", monitor_config={"render": True}),
                _row(
                    "second",
                    monitor_type="dom",
                    monitor_config={"render": True, "wait": "networkidle"},
                ),
            ],
        )
    )

    with pytest.raises(CensusError, match="absent from the candidate baseline"):
        compare_semantic_manifests(baseline, current)

    comparison = compare_baseline_evolution(baseline, current, current)
    assert len(comparison["new_configured_profile_ids"]) == 1


def test_new_chain_context_profile_is_a_valid_explicit_baseline_addition(tmp_path: Path) -> None:
    fallback = {"type": "dom", "config": {"render": True}}
    baseline = build_manifest(
        _write_boards(
            tmp_path / "baseline.csv",
            [_row("first", scraper_config={"fallback": fallback})],
        )
    )
    current = build_manifest(
        _write_boards(
            tmp_path / "current.csv",
            [
                _row("first", scraper_config={"fallback": fallback}),
                _row(
                    "second",
                    scraper_config={"ignore_locations": True, "fallback": fallback},
                ),
            ],
        )
    )

    comparison = compare_baseline_evolution(baseline, current, current)

    assert len(comparison["new_configured_profile_ids"]) == 1
    added = next(
        record
        for record in current["records"]
        if record["id"] in comparison["new_configured_profile_ids"]
    )
    assert added["browser_required"] is False
    assert added["status"] == "chain_context_only"
    assert added["blocker"] == "downstream_browser_step_requires_replay"


def test_registry_occurrence_state_is_derived_not_pinned(tmp_path: Path) -> None:
    baseline = build_manifest(_write_boards(tmp_path / "baseline.csv", [_row("static")]))
    current = build_manifest(
        _write_boards(
            tmp_path / "current.csv",
            [_row("browser", monitor_type="dom", monitor_config={"render": True})],
        )
    )

    comparison = compare_baseline_evolution(baseline, current, current)
    baseline_registry = next(
        record for record in baseline["records"] if record["id"] == "registry.monitor.dom"
    )
    current_registry = next(
        record for record in current["records"] if record["id"] == "registry.monitor.dom"
    )

    assert comparison["new_configured_profile_ids"]
    assert baseline_registry["status"] == "zero_browser_config"
    assert baseline_registry["blocker"] == "no_configured_browser_profile"
    assert current_registry["status"] == "inventory"
    assert current_registry["blocker"] == "configured_profiles_pending_replay"


def test_configured_profile_requires_a_source_occurrence(tmp_path: Path) -> None:
    baseline = build_manifest(
        _write_boards(
            tmp_path / "boards.csv",
            [_row("browser", monitor_type="dom", monitor_config={"render": True})],
        )
    )
    current = deepcopy(baseline)
    profile = next(
        record for record in current["records"] if record["profile_kind"] == "configured"
    )
    profile["source_count"] = 0
    _refresh_manifest_integrity(current)

    with pytest.raises(CensusError, match="source_count must be at least one"):
        compare_semantic_manifests(baseline, current)


def test_configured_profile_requires_a_registered_browser_capable_type(tmp_path: Path) -> None:
    baseline = build_manifest(
        _write_boards(
            tmp_path / "boards.csv",
            [_row("browser", monitor_type="dom", monitor_config={"render": True})],
        )
    )
    current = deepcopy(baseline)
    current["records"] = [
        record for record in current["records"] if record["id"] != "registry.monitor.dom"
    ]
    _refresh_manifest_integrity(current)

    with pytest.raises(CensusError, match="no corresponding registry record: monitor.dom"):
        compare_semantic_manifests(baseline, current)


def test_append_only_baseline_rejects_fixture_shrink_reclassification_attack(
    tmp_path: Path,
) -> None:
    previous = build_manifest(
        _write_boards(
            tmp_path / "previous.csv",
            [
                _row("first", monitor_type="dom", monitor_config={"render": True}),
                _row(
                    "second",
                    monitor_type="dom",
                    monitor_config={"render": True, "wait": "networkidle"},
                ),
            ],
        )
    )
    shrunk_candidate = build_manifest(
        _write_boards(
            tmp_path / "candidate.csv",
            [_row("first", monitor_type="dom", monitor_config={"render": True})],
        )
    )

    with pytest.raises(CensusError, match="removed or renamed pinned configured profiles"):
        compare_baseline_evolution(previous, shrunk_candidate, shrunk_candidate)


def test_append_only_baseline_rejects_pinned_semantic_mutation(tmp_path: Path) -> None:
    previous = build_manifest(
        _write_boards(
            tmp_path / "boards.csv",
            [_row("browser", monitor_type="dom", monitor_config={"render": True})],
        )
    )
    candidate = deepcopy(previous)
    profile = next(
        record for record in candidate["records"] if record["profile_kind"] == "configured"
    )
    profile["capabilities"].append("navigation.wait.load")
    profile["capabilities"].sort()
    _refresh_manifest_integrity(candidate)

    with pytest.raises(CensusError, match="changed pinned stable profile semantics"):
        compare_baseline_evolution(previous, candidate, candidate)


def test_candidate_baseline_cannot_add_a_profile_absent_from_current(tmp_path: Path) -> None:
    previous = build_manifest(_write_boards(tmp_path / "previous.csv", [_row("static")]))
    candidate = build_manifest(
        _write_boards(
            tmp_path / "candidate.csv",
            [_row("browser", monitor_type="dom", monitor_config={"render": True})],
        )
    )

    with pytest.raises(CensusError, match="removed or renamed pinned configured profiles"):
        compare_baseline_evolution(previous, candidate, previous)


def test_data_only_addition_does_not_stall_later_baseline_checks(tmp_path: Path) -> None:
    baseline = build_manifest(
        _write_boards(
            tmp_path / "baseline.csv",
            [_row("first", monitor_type="dom", monitor_config={"render": True})],
        )
    )
    merged_data = build_manifest(
        _write_boards(
            tmp_path / "merged.csv",
            [
                _row("first", monitor_type="dom", monitor_config={"render": True}),
                _row(
                    "second",
                    monitor_type="dom",
                    monitor_config={"render": True, "wait": "networkidle"},
                ),
            ],
        )
    )
    later_ordinary_run = build_manifest(
        _write_boards(
            tmp_path / "later.csv",
            [
                _row("first", monitor_type="dom", monitor_config={"render": True}),
                _row(
                    "second",
                    monitor_type="dom",
                    monitor_config={"render": True, "wait": "networkidle"},
                ),
                _row(
                    "third",
                    monitor_type="dom",
                    monitor_config={"render": True, "wait": "networkidle"},
                ),
            ],
        )
    )

    accepted_addition = compare_baseline_evolution(baseline, merged_data, merged_data)
    later_comparison = compare_semantic_manifests(merged_data, later_ordinary_run)

    assert accepted_addition["new_configured_profile_ids"]
    assert later_comparison["new_configured_profile_ids"] == []


def test_committed_manifest_is_a_valid_semantic_baseline_with_kpmg_fallback() -> None:
    baseline = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="ascii"))
    current = build_manifest()

    comparison = compare_semantic_manifests(baseline, current)

    current_records = {record["id"]: record for record in current["records"]}
    for record_id in comparison["new_configured_profile_ids"]:
        record = current_records[record_id]
        expected_status = "pending_replay" if record["browser_required"] else "chain_context_only"
        expected_blocker = (
            "pinned_lightpanda_replay_required"
            if record["browser_required"]
            else "downstream_browser_step_requires_replay"
        )
        assert record["profile_kind"] == "configured"
        assert record["status"] == expected_status
        assert record["blocker"] == expected_blocker
        assert record["fixture"] == f"synthetic.{record['compatibility_class']}.v1"
    assert current["input"]["network_access"] is False
    assert any(
        record["profile_kind"] == "configured"
        and record["surface"] == "scraper"
        and record["crawler_type"] == "dom"
        and record["chain_role"] == "fallback"
        and record["browser_required"] is True
        for record in current["records"]
    )
