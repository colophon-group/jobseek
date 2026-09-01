"""Exact standalone #8402 fixture and fail-closed mutation tests."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from src.shared.browser_transport import (
    ADAPTER_BLOCKERS,
    HISTOGRAM_BUCKETS,
    INSTRUMENTATION_REASONS,
    PRETRANSPORT_REASONS,
    REQUEST_LABELS,
    TERMINAL_OUTCOMES,
    VALID_ROUTE_PROVIDER_PAIRS,
    validate_capture_fixture,
)

CRAWLER_ROOT = Path(__file__).parents[1]
FIXTURE_PATH = CRAWLER_ROOT / "runtime-cost/fixtures/chromium-transport-86400-v1.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _row(document: dict, boundary: str, **labels: str) -> dict:
    for row in document["boundaries"][boundary]["rows"]:
        if all(row["labels"].get(name) == value for name, value in labels.items()):
            return row
    raise AssertionError(f"row not found: {labels}")


def _assert_failed_closed(document: dict, *, stage: str, blocker: str | None = None) -> None:
    result = validate_capture_fixture(document)
    assert result.valid is False
    assert result.stages[stage] is None
    assert result.blockers[stage]
    assert set(result.blockers[stage]) <= set(ADAPTER_BLOCKERS)
    if blocker is not None:
        assert blocker in result.blockers[stage]


def test_exact_86400_fixture_conserves_every_frozen_total():
    document = _fixture()
    result = validate_capture_fixture(document)

    assert result.valid is True
    assert result.series_count == 1_449
    assert document["window"]["end_unix"] - document["window"]["start_unix"] == 86_400
    stages = [value for value in result.stages.values() if value is not None]
    assert sum(stage.attempts for stage in stages) == 11
    assert {
        outcome: sum(stage.outcomes[outcome] for stage in stages) for outcome in TERMINAL_OUTCOMES
    } == {
        "complete_response": 5,
        "partial_response": 2,
        "transport_failure": 1,
        "policy_rejected": 1,
        "cancelled": 1,
        "target_closed": 1,
    }
    assert sum(stage.transferred_bytes for stage in stages) == 5_770
    assert sum(stage.histogram_sum for stage in stages) == 5_770
    assert sum(stage.histogram_count for stage in stages) == 7
    assert {
        str(bucket): sum(stage.histogram_buckets[str(bucket)] for stage in stages)
        for bucket in HISTOGRAM_BUCKETS
    } == {
        "0": 1,
        "256": 4,
        "1024": 6,
        "4096": 7,
        "16384": 7,
        "65536": 7,
        "262144": 7,
        "1048576": 7,
        "16777216": 7,
        "+Inf": 7,
    }
    assert sum(stage.accepted_tasks for stage in stages) == 4
    assert sum(stage.pretransport_events for stage in stages) == 1


def test_fixture_exposes_all_48_rows_and_all_zero_children_at_both_boundaries():
    document = _fixture()
    expected = {
        (
            stage,
            "browser",
            "chromium",
            request_class,
            route,
            provider,
            capability,
        )
        for stage in ("monitor", "detail")
        for request_class in ("main", "redirect", "subresource", "warmup")
        for route, provider in VALID_ROUTE_PROVIDER_PAIRS
        for capability in (
            "navigation-evaluation",
            "interaction-capture",
            "identity-transport",
        )
    }
    for boundary in ("start", "end"):
        rows = document["boundaries"][boundary]["rows"]
        assert len(rows) == 48
        assert {tuple(row["labels"][name] for name in REQUEST_LABELS) for row in rows} == expected
        assert all(set(row["outcomes"]) == set(TERMINAL_OUTCOMES) for row in rows)
        assert all(
            set(row["histogram"]["buckets"]) == {str(bucket) for bucket in HISTOGRAM_BUCKETS}
            for row in rows
        )
        support = document["boundaries"][boundary]
        assert len(support["accepted_tasks"]) == 12
        assert len(support["pretransport"]) == 8
        assert len(support["instrumentation"]) == 16


def test_pretransport_is_separate_and_provider_none_optional_proxy_is_direct():
    tape = _fixture()["event_tape"]
    pretransport = [event for event in tape if event["kind"] == "pretransport"]
    requests = [event for event in tape if event["kind"] == "request_terminal"]
    optional_none = [
        event
        for event in requests
        if event["proxy_requested"] and event["configured_proxy_provider"] == "none"
    ]

    assert pretransport == [
        {
            "ordinal": 4,
            "kind": "pretransport",
            "task_id": "task-proxy-rejected",
            "stage": "detail",
            "reason": "required_proxy_unavailable",
        }
    ]
    assert optional_none
    assert all(
        (event["route"], event["provider"]) == ("direct", "direct") for event in optional_none
    )
    assert sum(event["kind"] == "request_terminal" for event in tape) == 11


def test_fixture_generator_check_is_idempotent():
    completed = subprocess.run(
        [
            "python3",
            "contracts/browser/transport/v1/tools/generate_fixture.py",
            "--check",
        ],
        cwd=CRAWLER_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_missing_zero_row_fails_closed():
    document = _fixture()
    rows = document["boundaries"]["end"]["rows"]
    rows.pop(next(index for index, row in enumerate(rows) if row["attempts"] == 0))
    _assert_failed_closed(document, stage="monitor", blocker="missing_series")


def test_duplicate_row_is_an_extra_series_and_fails_closed():
    document = _fixture()
    document["boundaries"]["end"]["rows"].append(deepcopy(document["boundaries"]["end"]["rows"][0]))
    _assert_failed_closed(document, stage="monitor", blocker="extra_series")


@pytest.mark.parametrize(
    ("name", "value", "blocker"),
    [
        ("stage", "arbitrary-stage", "illegal_label"),
        ("capability", "arbitrary-capability", "illegal_label"),
        ("provider", "static-proxy", "illegal_route_provider_pair"),
    ],
)
def test_illegal_label_values_and_route_provider_pairs_fail_closed(name, value, blocker):
    document = _fixture()
    document["boundaries"]["end"]["rows"][0]["labels"][name] = value
    _assert_failed_closed(document, stage="monitor", blocker=blocker)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_missing_or_extra_histogram_bucket_fails_closed(mutation):
    document = _fixture()
    row = _row(document, "end", stage="monitor")
    if mutation == "missing":
        row["histogram"]["buckets"].pop("256")
    else:
        row["histogram"]["buckets"]["999"] = 0
    _assert_failed_closed(document, stage="monitor", blocker="bucket_mismatch")


def test_counter_reset_fails_closed():
    document = _fixture()
    start = _row(document, "start", stage="monitor")
    end = _row(document, "end", **start["labels"])
    start["attempts"] = end["attempts"] + 1
    _assert_failed_closed(document, stage="monitor", blocker="counter_reset")


@pytest.mark.parametrize("field", ["attempts", "transferred_bytes"])
def test_fractional_boundary_counter_fails_closed(field):
    document = _fixture()
    row = _row(document, "end", stage="monitor")
    row[field] = 0.5
    _assert_failed_closed(document, stage="monitor", blocker="fractional_delta")


@pytest.mark.parametrize("reason", INSTRUMENTATION_REASONS)
def test_each_positive_instrumentation_reason_nulls_only_affected_stage(reason):
    document = _fixture()
    item = next(
        item
        for item in document["boundaries"]["end"]["instrumentation"]
        if item["labels"]["stage"] == "monitor" and item["labels"]["reason"] == reason
    )
    item["value"] = 1
    result = validate_capture_fixture(document)

    assert result.stages["monitor"] is None
    assert result.stages["detail"] is not None
    assert "instrumentation_failure" in result.blockers["monitor"]


def test_observer_lifecycle_gap_fails_closed_without_inferred_zero():
    document = _fixture()
    event = next(event for event in document["event_tape"] if event["kind"] == "request_terminal")
    event["byte_lifecycle_complete"] = False
    _assert_failed_closed(document, stage=event["stage"], blocker="observer_lifecycle_gap")


def test_terminal_duplication_fails_closed():
    document = _fixture()
    event = deepcopy(
        next(event for event in document["event_tape"] if event["kind"] == "request_terminal")
    )
    document["event_tape"].append(event)
    for ordinal, item in enumerate(document["event_tape"]):
        item["ordinal"] = ordinal
    _assert_failed_closed(document, stage=event["stage"], blocker="terminal_duplication")


def test_byte_histogram_sum_mismatch_fails_closed():
    document = _fixture()
    row = _row(
        document,
        "end",
        stage="monitor",
        request_class="main",
        route="direct",
        provider="direct",
        capability="navigation-evaluation",
    )
    row["transferred_bytes"] += 1
    _assert_failed_closed(document, stage="monitor", blocker="conservation_mismatch")


def test_histogram_distribution_cannot_be_reconstructed_from_only_sum_and_count():
    document = _fixture()
    row = _row(
        document,
        "end",
        stage="monitor",
        request_class="subresource",
        route="direct",
        provider="direct",
        capability="navigation-evaluation",
    )
    assert row["histogram"]["buckets"]["256"] == 1
    row["histogram"]["buckets"]["256"] = 0
    _assert_failed_closed(document, stage="monitor", blocker="event_tape_mismatch")


def test_partial_response_requires_positive_observed_bytes():
    document = _fixture()
    event = next(
        event for event in document["event_tape"] if event.get("outcome") == "partial_response"
    )
    event["encoded_bytes"] = 0
    _assert_failed_closed(document, stage=event["stage"], blocker="conservation_mismatch")


def test_registry_digest_mismatch_nulls_both_stages():
    document = _fixture()
    document["registry"]["sha256"] = "0" * 64
    result = validate_capture_fixture(document)

    assert result.stages == {"monitor": None, "detail": None}
    assert all("registry_mismatch" in reasons for reasons in result.blockers.values())


def test_provider_none_proxy_misclassification_fails_closed():
    document = _fixture()
    accepted = next(
        event
        for event in document["event_tape"]
        if event["kind"] == "accepted_task" and event["task_id"] == "task-a"
    )
    accepted["route"] = "proxy"
    accepted["provider"] = "static-proxy"
    for event in document["event_tape"]:
        if event.get("task_id") == "task-a" and event["kind"] == "request_terminal":
            event["route"] = "proxy"
            event["provider"] = "static-proxy"
    _assert_failed_closed(
        document,
        stage="monitor",
        blocker="provider_none_misclassification",
    )


def test_pretransport_reason_set_is_closed():
    assert set(PRETRANSPORT_REASONS) == {
        "required_proxy_unavailable",
        "resource_policy",
        "unknown_capability",
        "unknown_provider",
    }
