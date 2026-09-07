# ruff: noqa: E501

from __future__ import annotations

import copy
import socket
from pathlib import Path
from typing import Any

import pytest

from contracts.browser.lanes.v1 import model

NOW = 1_000
CORPUS_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "scenarios.json"


def _fence(lane: str, *, queue_revision: str = "queue-1") -> dict[str, Any]:
    return {
        "claim_fence": 1 if lane == "lightpanda" else 2,
        "config_revision": "config-1",
        "engine_owner": f"owner-{lane}",
        "queue_revision": queue_revision,
        "routing_epoch": 1,
        "shard_id": f"shard-{lane}",
    }


def _lane(name: str) -> dict[str, Any]:
    return {
        "capacity": {
            "admitted": 1,
            "current": 1,
            "desired": 1,
            "drain_started_at": 0,
            "draining": False,
            "hard_max": 4,
            "inflight": 0,
            "last_scale_at": 0,
            "running": 0,
            "scale_down_step": 1,
            "scale_up_step": 1,
            "warm_floor": 1,
        },
        "declared": {
            "assignment_count": 0,
            "eligible_ready_count": 0,
            "inflight_count": 0,
            "oldest_eligible_age": 0,
            "ready_count": 0,
        },
        "lane": name,
        "queue_fence": _fence(name),
        "service_state": "admitted",
        "telemetry": {
            "error_budget_burn": 0.0,
            "headroom_p05_ratio": 0.5,
            "observed_at": NOW,
            "queue_oldest_age": 0,
            "resource_saturated": False,
            "utilization_p95_ratio": 0.5,
        },
        "zero_proof": None,
    }


def _placement(
    ordinal: int,
    lane: str,
    *,
    admission: str = "permit",
    eligible_since: int = 990,
) -> dict[str, Any]:
    return {
        "admission": {"policy_revision": "policy-1", "verdict": admission},
        "assignment": {
            "backend": lane,
            "capability_class": "browser-default",
            "immutable_copy": {
                "backend": lane,
                "capability_class": "browser-default",
                "routing_revision": "routing-1",
                "service_lane": lane,
            },
            "routing_revision": "routing-1",
            "service_lane": lane,
        },
        "due_at": NOW,
        "eligible_since": eligible_since,
        "fallback_target": "none",
        "fence": _fence(lane),
        "lane": lane,
        "ordinal": ordinal,
        "priority": "monitor",
        "work_class": "monitor",
    }


def _snapshot(
    *,
    ready: list[dict[str, Any]] | None = None,
    inflight: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    document = {
        "capability_census_revision": "census-1",
        "config_revision": "config-1",
        "declared_assignment_count": 0,
        "invalidation_events": [],
        "lanes": [_lane("lightpanda"), _lane("chromium")],
        "now": NOW,
        "placements": {"inflight": inflight or [], "ready": ready or []},
        "policy_revision": "policy-1",
        "queue_revision": "queue-1",
        "routing_revision": "routing-1",
    }
    _declare(document)
    return document


def _declare(document: dict[str, Any]) -> None:
    placements = document["placements"]
    document["declared_assignment_count"] = len(placements["ready"]) + len(placements["inflight"])
    for lane in document["lanes"]:
        name = lane["lane"]
        ready = [item for item in placements["ready"] if item["lane"] == name]
        inflight = [item for item in placements["inflight"] if item["lane"] == name]
        eligible = [
            item
            for item in ready
            if item["due_at"] <= document["now"] and item["admission"]["verdict"] == "permit"
        ]
        lane["declared"] = {
            "assignment_count": len(ready) + len(inflight),
            "eligible_ready_count": len(eligible),
            "inflight_count": len(inflight),
            "oldest_eligible_age": max(
                (document["now"] - item["eligible_since"] for item in eligible), default=0
            ),
            "ready_count": len(ready),
        }
        count = len(inflight)
        lane["capacity"].update(
            {
                "admitted": max(1, count),
                "current": max(1, count),
                "inflight": count,
                "running": count,
            }
        )


def _proof(document: dict[str, Any], lane_name: str) -> dict[str, Any]:
    lane = next(lane for lane in document["lanes"] if lane["lane"] == lane_name)
    declared = lane["declared"]
    eligible = [
        item
        for item in document["placements"]["ready"]
        if item["lane"] == lane_name
        and item["due_at"] <= document["now"]
        and item["admission"]["verdict"] == "permit"
    ]
    return {
        "assignment_count": declared["assignment_count"],
        "capability_census_revision": document["capability_census_revision"],
        "complete": True,
        "completed_at": NOW - 30,
        "config_revision": document["config_revision"],
        "eligible_ready_count": declared["eligible_ready_count"],
        "inflight_count": declared["inflight_count"],
        "oldest_eligible_since": min((item["eligible_since"] for item in eligible), default=None),
        "policy_revision": document["policy_revision"],
        "queue_fence": copy.deepcopy(lane["queue_fence"]),
        "queue_revision": document["queue_revision"],
        "ready_count": declared["ready_count"],
        "routing_revision": document["routing_revision"],
        "started_at": NOW - 930,
    }


def _event(
    document: dict[str, Any],
    lane: str,
    *,
    event_at: int,
    event_ordinal: int = 0,
    kind: str = "assignment_created",
) -> dict[str, Any]:
    return {
        "capability_census_revision": document["capability_census_revision"],
        "config_revision": document["config_revision"],
        "event_at": event_at,
        "event_ordinal": event_ordinal,
        "kind": kind,
        "lane": lane,
        "policy_revision": document["policy_revision"],
        "queue_revision": document["queue_revision"],
        "routing_revision": document["routing_revision"],
        "work_ordinal": 0,
    }


def _decision(document: dict[str, Any], lane: str) -> dict[str, Any]:
    result = model.evaluate(document)
    assert "lanes" in result
    return result["lanes"][lane]


def test_exact_global_snapshot_claims_only_from_ready() -> None:
    document = _snapshot(
        ready=[_placement(0, "lightpanda")],
        inflight=[_placement(1, "chromium")],
    )

    result = model.evaluate(document)["lanes"]

    assert result["lightpanda"] == {
        "decision": "claim",
        "desired_concurrency": 1,
        "lane": "lightpanda",
        "reasons": [],
        "selected_item_index": 0,
    }
    assert result["chromium"]["selected_item_index"] is None
    assert result["chromium"]["reasons"] == ["no_eligible_backlog", "zero_proof_absent"]


@pytest.mark.parametrize("failure", ["loss", "duplicate", "ready_inflight_overlap"])
def test_global_ordinal_conservation_freezes_all_affected_lanes(failure: str) -> None:
    document = _snapshot(
        ready=[_placement(0, "lightpanda")],
        inflight=[_placement(1, "chromium")],
    )
    if failure == "loss":
        document["declared_assignment_count"] = 3
    elif failure == "duplicate":
        document["placements"]["inflight"][0]["ordinal"] = 0
    else:
        document["placements"]["inflight"] = [copy.deepcopy(document["placements"]["ready"][0])]

    result = model.evaluate(document)["lanes"]

    assert "conservation_failure" in result["lightpanda"]["reasons"]
    if failure != "ready_inflight_overlap":
        assert "conservation_failure" in result["chromium"]["reasons"]
    assert all(decision["selected_item_index"] is None for decision in result.values())


def test_shared_cross_lane_queue_fence_freezes_both_before_claim() -> None:
    document = _snapshot(ready=[_placement(0, "lightpanda")])
    document["lanes"][1]["queue_fence"] = copy.deepcopy(document["lanes"][0]["queue_fence"])

    result = model.evaluate(document)["lanes"]

    assert result["lightpanda"]["decision"] == "freeze"
    assert result["chromium"]["decision"] == "freeze"
    assert "conservation_failure" in result["lightpanda"]["reasons"]
    assert "conservation_failure" in result["chromium"]["reasons"]


def test_placement_on_sibling_fence_attributes_cross_lane_occupancy_to_both() -> None:
    placement = _placement(0, "lightpanda")
    placement["fence"] = _fence("chromium")
    document = _snapshot(ready=[placement])

    result = model.evaluate(document)["lanes"]

    for lane in model.LANES:
        assert result[lane]["decision"] == "freeze"
        assert result[lane]["reasons"] == ["conservation_failure", "queue_fence_invalid"]


def test_lane_census_and_capacity_inflight_are_recomputed_globally() -> None:
    document = _snapshot(inflight=[_placement(0, "lightpanda")])
    document["lanes"][0]["declared"]["ready_count"] = 1
    document["lanes"][0]["capacity"]["inflight"] = 0

    result = model.evaluate(document)["lanes"]

    assert result["lightpanda"]["reasons"] == ["conservation_failure"]
    assert "conservation_failure" not in result["chromium"]["reasons"]


def test_assignment_fallback_revision_policy_and_mutation_reasons_are_all_attributed() -> None:
    placement = _placement(0, "lightpanda", admission="violation")
    placement["fallback_target"] = "chromium"
    placement["assignment"].update({"backend": "chromium", "routing_revision": "routing-old"})
    document = _snapshot(ready=[placement])

    result = model.evaluate(document)["lanes"]
    expected = [
        "assignment_invalid",
        "assignment_lane_mismatch",
        "assignment_mutated",
        "fallback_attempted",
        "policy_violation",
        "revision_mismatch",
    ]

    assert result["lightpanda"]["reasons"] == expected
    assert result["chromium"]["reasons"] == expected


@pytest.mark.parametrize(
    ("service_state", "reason"),
    [
        ("unready", "service_unready"),
        ("error", "service_error"),
        ("unsupported", "service_unsupported"),
        ("full", "service_full"),
    ],
)
def test_unified_service_state_has_one_closed_reason(service_state: str, reason: str) -> None:
    document = _snapshot()
    document["lanes"][0]["service_state"] = service_state

    assert _decision(document, "lightpanda")["reasons"] == [reason]


def test_all_applicable_freeze_reasons_are_ascii_sorted() -> None:
    document = _snapshot()
    lane = document["lanes"][0]
    lane["service_state"] = "error"
    lane["telemetry"].update(
        {
            "error_budget_burn": 1.000001,
            "observed_at": NOW - model.TELEMETRY_MAX_AGE - 1,
            "resource_saturated": True,
        }
    )

    decision = _decision(document, "lightpanda")

    assert decision["reasons"] == [
        "error_budget_exhausted",
        "resource_saturation",
        "service_error",
        "telemetry_stale",
    ]


def test_queue_revision_change_invalidates_an_otherwise_valid_zero_proof() -> None:
    document = _snapshot()
    document["lanes"][0]["zero_proof"] = _proof(document, "lightpanda")
    document["queue_revision"] = "queue-2"
    for lane in document["lanes"]:
        lane["queue_fence"]["queue_revision"] = "queue-2"

    decision = _decision(document, "lightpanda")

    assert decision["decision"] == "defer"
    assert decision["reasons"] == ["no_eligible_backlog", "zero_proof_revision_mismatch"]


@pytest.mark.parametrize("kind", ["assignment_created", "became_eligible"])
def test_current_revision_event_at_proof_completion_invalidates_proof(kind: str) -> None:
    document = _snapshot()
    proof = _proof(document, "lightpanda")
    document["lanes"][0]["zero_proof"] = proof
    document["invalidation_events"] = [
        _event(document, "lightpanda", event_at=proof["completed_at"], kind=kind)
    ]

    assert _decision(document, "lightpanda")["reasons"] == [
        "no_eligible_backlog",
        "zero_proof_invalid",
    ]


def test_stale_revision_event_does_not_invalidate_or_affect_sibling() -> None:
    document = _snapshot()
    for lane in document["lanes"]:
        lane["capacity"].update(
            {"admitted": 0, "current": 0, "desired": 0, "running": 0, "warm_floor": 0}
        )
        lane["zero_proof"] = _proof(document, lane["lane"])
    event = _event(document, "lightpanda", event_at=NOW - 30)
    event["queue_revision"] = "queue-old"
    document["invalidation_events"] = [event]

    result = model.evaluate(document)["lanes"]

    assert result["lightpanda"]["reasons"] == ["no_eligible_backlog"]
    assert result["chromium"]["reasons"] == ["no_eligible_backlog"]
    assert result["lightpanda"] == {
        **result["chromium"],
        "lane": "lightpanda",
    }


def test_accurate_nonzero_proof_emits_demand_present() -> None:
    document = _snapshot(ready=[_placement(0, "lightpanda", admission="defer")])
    document["lanes"][0]["zero_proof"] = _proof(document, "lightpanda")

    assert _decision(document, "lightpanda")["reasons"] == [
        "no_eligible_backlog",
        "zero_proof_demand_present",
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"items": []}),
        lambda value: value["lanes"].reverse(),
        lambda value: value["placements"]["ready"][0].update({"future": 1}),
        lambda value: value["invalidation_events"].append(
            {**_event(value, "lightpanda", event_at=NOW), "event_ordinal": 1}
        ),
    ],
)
def test_unknown_old_or_noncanonical_semantic_shape_is_invalid_input(mutation: Any) -> None:
    document = _snapshot(ready=[_placement(0, "lightpanda")])
    mutation(document)

    assert model.evaluate(document) == {"error": "invalid_input"}


@pytest.mark.parametrize(
    "raw",
    [
        b'{"now":0,"now":1}',
        b'{"x":0} trailing',
        b'{"x":NaN}',
        b'{ "x":0}',
        b'{"x":"https://private.invalid"}',
        b'{"x":"bearer marker"}',
        b'{"x":"alpha.example"}',
        b'{"x":"127.0.0.1"}',
        b"[" * (model.MAX_DEPTH + 1) + b"0" + b"]" * (model.MAX_DEPTH + 1),
    ],
)
def test_malformed_documents_are_a_fixed_non_reflecting_error(raw: bytes) -> None:
    result = model.evaluate_document(raw)
    assert result == b'{"error":"invalid_input"}'
    assert b"private" not in result and b"marker" not in result and b"alpha" not in result


def test_runtime_final_lf_is_a_fixed_non_reflecting_error() -> None:
    document = _snapshot(ready=[_placement(0, "lightpanda")])
    raw = model.canonical_bytes(document)

    assert model.evaluate_document(raw) == model.canonical_bytes(model.evaluate(document))
    result = model.evaluate_document(raw + b"\n")

    assert result == b'{"error":"invalid_input"}'
    assert b"routing-1" not in result
    with pytest.raises(model.ContractError, match="invalid_input"):
        model.parse_document(raw + b"\n")


def test_corpus_envelope_requires_exactly_one_final_lf(tmp_path: Path) -> None:
    raw = CORPUS_PATH.read_bytes()
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")

    corpus = model.load_json(CORPUS_PATH)

    assert corpus["format"] == model.FORMAT
    assert len(corpus["cases"]) == 79
    for name, malformed in (("missing-lf.json", raw[:-1]), ("double-lf.json", raw + b"\n")):
        path = tmp_path / name
        path.write_bytes(malformed)
        with pytest.raises(model.ContractError, match="invalid_input"):
            model.load_json(path)

    case_input = model.canonical_bytes(corpus["cases"][0]["input"])
    assert model.parse_document(case_input) == corpus["cases"][0]["input"]
    with pytest.raises(model.ContractError, match="invalid_input"):
        model.parse_document(case_input + b"\n")


def test_small_canonical_decimal_round_trips_but_exponent_form_is_rejected() -> None:
    document = _snapshot()
    document["lanes"][0]["telemetry"]["utilization_p95_ratio"] = 0.000001
    raw = model.canonical_bytes(document)

    assert b"0.000001" in raw
    assert model.evaluate_document(raw) == model.canonical_bytes(model.evaluate(document))
    assert (
        model.evaluate_document(raw.replace(b"0.000001", b"1e-06")) == b'{"error":"invalid_input"}'
    )


def test_reference_is_network_free(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("browser-lane conformance attempted network access")

    monkeypatch.setattr(socket, "socket", blocked)
    model.evaluate(_snapshot(ready=[_placement(0, "lightpanda")]))
