#!/usr/bin/env python3
"""Generate and audit the canonical browser-lanes v1 conformance corpus."""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from pathlib import Path
from typing import Any

V1_ROOT = Path(__file__).resolve().parents[1]
CRAWLER_ROOT = V1_ROOT.parents[4]
if str(CRAWLER_ROOT) not in sys.path:
    sys.path.insert(0, str(CRAWLER_ROOT))

from contracts.browser.lanes.v1 import model  # noqa: E402

FIXTURES = V1_ROOT / "fixtures"
CORPUS_PATH = FIXTURES / "scenarios.json"
DIGEST_PATH = FIXTURES / "scenarios.sha256"
NOW = 1_000


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
    priority: str = "monitor",
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
        "priority": priority,
        "work_class": "monitor",
    }


def _input(
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
    return _declare(document)


def _lane_named(document: dict[str, Any], name: str) -> dict[str, Any]:
    return next(lane for lane in document["lanes"] if lane["lane"] == name)


def _declare(document: dict[str, Any]) -> dict[str, Any]:
    ready = document["placements"]["ready"]
    inflight = document["placements"]["inflight"]
    document["declared_assignment_count"] = len(ready) + len(inflight)
    for lane in document["lanes"]:
        name = lane["lane"]
        lane_ready = [item for item in ready if item["lane"] == name]
        lane_inflight = [item for item in inflight if item["lane"] == name]
        eligible = [
            item
            for item in lane_ready
            if item["due_at"] <= document["now"] and item["admission"]["verdict"] == "permit"
        ]
        lane["declared"] = {
            "assignment_count": len(lane_ready) + len(lane_inflight),
            "eligible_ready_count": len(eligible),
            "inflight_count": len(lane_inflight),
            "oldest_eligible_age": max(
                (document["now"] - item["eligible_since"] for item in eligible), default=0
            ),
            "ready_count": len(lane_ready),
        }
        count = len(lane_inflight)
        lane["capacity"].update(
            {
                "admitted": max(lane["capacity"]["admitted"], count),
                "current": max(lane["capacity"]["current"], count),
                "inflight": count,
                "running": max(lane["capacity"]["running"], count),
            }
        )
    return document


def _zero_capacity(lane: dict[str, Any]) -> None:
    lane["capacity"].update(
        {"admitted": 0, "current": 0, "desired": 0, "inflight": 0, "running": 0, "warm_floor": 0}
    )


def _proof(document: dict[str, Any], lane_name: str, **overrides: Any) -> dict[str, Any]:
    lane = _lane_named(document, lane_name)
    eligible = [
        item
        for item in document["placements"]["ready"]
        if item["lane"] == lane_name
        and item["due_at"] <= document["now"]
        and item["admission"]["verdict"] == "permit"
    ]
    proof: dict[str, Any] = {
        "assignment_count": lane["declared"]["assignment_count"],
        "capability_census_revision": document["capability_census_revision"],
        "complete": True,
        "completed_at": NOW - 30,
        "config_revision": document["config_revision"],
        "eligible_ready_count": lane["declared"]["eligible_ready_count"],
        "inflight_count": lane["declared"]["inflight_count"],
        "oldest_eligible_since": min((item["eligible_since"] for item in eligible), default=None),
        "policy_revision": document["policy_revision"],
        "queue_fence": copy.deepcopy(lane["queue_fence"]),
        "queue_revision": document["queue_revision"],
        "ready_count": lane["declared"]["ready_count"],
        "routing_revision": document["routing_revision"],
        "started_at": NOW - 930,
    }
    proof.update(overrides)
    return proof


def _event(
    document: dict[str, Any], lane: str, *, kind: str, event_at: int, ordinal: int = 0
) -> dict[str, Any]:
    return {
        "capability_census_revision": document["capability_census_revision"],
        "config_revision": document["config_revision"],
        "event_at": event_at,
        "event_ordinal": ordinal,
        "kind": kind,
        "lane": lane,
        "policy_revision": document["policy_revision"],
        "queue_revision": document["queue_revision"],
        "routing_revision": document["routing_revision"],
        "work_ordinal": 0,
    }


def source_cases() -> list[dict[str, Any]]:
    """Return source-ordered, synthetic cases spanning the complete v1 registry."""
    cases: list[dict[str, Any]] = []

    def add(case_id: str, document: dict[str, Any]) -> None:
        cases.append({"id": case_id, "input": document})

    add("claim_lightpanda_ready_only", _input(ready=[_placement(0, "lightpanda")]))
    add("claim_chromium_ready_only", _input(ready=[_placement(0, "chromium")]))

    document = _input(ready=[_placement(0, "lightpanda")])
    _lane_named(document, "lightpanda")["telemetry"].update(
        {"headroom_p05_ratio": 0.15, "utilization_p95_ratio": 0.85}
    )
    add("capacity_exact_boundaries_claim", document)

    document = _input(ready=[_placement(0, "lightpanda")])
    _lane_named(document, "lightpanda")["telemetry"]["utilization_p95_ratio"] = 0.850001
    add("capacity_utilization_unsafe", document)
    document = _input(ready=[_placement(0, "chromium")])
    _lane_named(document, "chromium")["telemetry"]["headroom_p05_ratio"] = 0.149999
    add("capacity_headroom_unsafe", document)

    scale = _input(ready=[_placement(1, "lightpanda")], inflight=[_placement(0, "lightpanda")])
    add("scale_up_requested_bounded", scale)
    document = copy.deepcopy(scale)
    _lane_named(document, "lightpanda")["capacity"]["hard_max"] = 1
    add("scale_up_hard_max_blocked", document)
    document = copy.deepcopy(scale)
    _lane_named(document, "lightpanda")["capacity"]["last_scale_at"] = 950
    add("scale_up_cooldown_blocked", document)
    document = copy.deepcopy(scale)
    _lane_named(document, "lightpanda")["capacity"].update(
        {"draining": True, "drain_started_at": 990}
    )
    add("scale_up_drain_blocked", document)
    document = copy.deepcopy(scale)
    _lane_named(document, "lightpanda")["capacity"]["last_scale_at"] = 940
    add("scale_up_cooldown_exact_boundary_complete", document)
    document = copy.deepcopy(scale)
    _lane_named(document, "lightpanda")["capacity"].update(
        {"draining": True, "drain_started_at": 970}
    )
    add("scale_up_drain_exact_boundary_complete", document)

    document = _input(ready=[_placement(0, "lightpanda")])
    _lane_named(document, "lightpanda")["telemetry"]["error_budget_burn"] = 1.0
    add("error_budget_exact_boundary_claim", document)
    document = _input(ready=[_placement(0, "lightpanda")])
    _lane_named(document, "lightpanda")["telemetry"]["observed_at"] = 970
    add("telemetry_fresh_exact_boundary_claim", document)

    add("no_eligible_backlog_and_zero_proof_absent", _input())
    add(
        "publisher_defer_is_ineligible",
        _input(ready=[_placement(0, "lightpanda", admission="defer")]),
    )
    add("publisher_deny_is_ineligible", _input(ready=[_placement(0, "chromium", admission="deny")]))
    add(
        "publisher_policy_violation",
        _input(ready=[_placement(0, "lightpanda", admission="violation")]),
    )

    placement = _placement(0, "lightpanda")
    placement["assignment"]["capability_class"] = "browser-other"
    placement["assignment"]["immutable_copy"]["capability_class"] = "browser-other"
    add("assignment_invalid_capability", _input(ready=[placement]))

    placement = _placement(0, "chromium")
    placement["assignment"]["immutable_copy"]["routing_revision"] = "routing-old"
    add("assignment_immutable_copy_mutated", _input(ready=[placement]))

    placement = _placement(0, "lightpanda")
    for assignment in (placement["assignment"], placement["assignment"]["immutable_copy"]):
        assignment["backend"] = "chromium"
        assignment["service_lane"] = "chromium"
    add("assignment_lane_mismatch_both_lanes", _input(ready=[placement]))

    placement = _placement(0, "lightpanda")
    placement["assignment"]["routing_revision"] = "routing-old"
    placement["assignment"]["immutable_copy"]["routing_revision"] = "routing-old"
    add("assignment_routing_revision_mismatch", _input(ready=[placement]))

    placement = _placement(0, "lightpanda")
    placement["fence"]["claim_fence"] = 99
    add("placement_queue_fence_mismatch", _input(ready=[placement]))

    for field, value in (("queue_revision", "queue-old"), ("config_revision", "config-old")):
        placement = _placement(0, "lightpanda")
        placement["fence"][field] = value
        add(f"placement_{field}_mismatch", _input(ready=[placement]))

    document = _input()
    _lane_named(document, "chromium")["queue_fence"]["queue_revision"] = "queue-old"
    add("lane_queue_revision_mismatch", document)
    document = _input()
    _lane_named(document, "chromium")["queue_fence"]["config_revision"] = "config-old"
    add("lane_config_revision_mismatch", document)

    placement = _placement(0, "lightpanda")
    document = _input(ready=[placement])
    document["routing_revision"] = "routing-2"
    add("top_level_routing_revision_change", document)
    placement = _placement(0, "lightpanda")
    document = _input(ready=[placement])
    document["policy_revision"] = "policy-2"
    add("top_level_policy_revision_change", document)
    document = _input()
    document["queue_revision"] = "queue-2"
    add("top_level_queue_revision_change", document)
    document = _input()
    document["config_revision"] = "config-2"
    add("top_level_config_revision_change", document)

    for source, target in (("lightpanda", "chromium"), ("chromium", "lightpanda")):
        placement = _placement(0, source)
        placement["fallback_target"] = target
        add(f"fallback_{source}_to_{target}_blocked", _input(ready=[placement]))

    for field, value, case_id in (
        ("error_budget_burn", 1.000001, "error_budget_exhausted"),
        ("resource_saturated", True, "resource_saturation"),
        ("observed_at", 969, "telemetry_stale"),
    ):
        document = _input()
        _lane_named(document, "lightpanda")["telemetry"][field] = value
        add(case_id, document)

    document = _input()
    _lane_named(document, "lightpanda")["capacity"]["desired"] = 5
    add("semantic_invalid_capacity_order", document)

    for state in ("unready", "error", "unsupported", "full"):
        document = _input()
        _lane_named(document, "lightpanda")["service_state"] = state
        add(f"service_{state}", document)

    document = _input()
    lane = _lane_named(document, "lightpanda")
    lane["service_state"] = "error"
    lane["telemetry"].update(
        {"error_budget_burn": 1.000001, "observed_at": 969, "resource_saturated": True}
    )
    add("all_applicable_safety_freezes_sorted", document)

    placement = _placement(0, "lightpanda", admission="violation")
    placement["fallback_target"] = "chromium"
    placement["assignment"].update({"backend": "chromium", "routing_revision": "routing-old"})
    add("all_applicable_assignment_freezes_sorted", _input(ready=[placement]))

    document = _input()
    lane = _lane_named(document, "lightpanda")
    _zero_capacity(lane)
    lane["zero_proof"] = _proof(document, "lightpanda")
    add("zero_proof_valid_reaches_zero", document)

    document = _input()
    lane = _lane_named(document, "lightpanda")
    lane["capacity"].update({"admitted": 0, "current": 2, "desired": 2, "running": 0})
    lane["zero_proof"] = _proof(document, "lightpanda")
    add("zero_proof_does_not_create_ordinary_scale_down", document)

    document = _input(inflight=[_placement(0, "lightpanda")])
    _lane_named(document, "lightpanda")["capacity"]["desired"] = 0
    add("desired_output_never_cancels_inflight", document)

    document = _input()
    _lane_named(document, "lightpanda")["zero_proof"] = _proof(
        document, "lightpanda", completed_at=969, started_at=69
    )
    add("zero_proof_stale", document)
    document = _input()
    _lane_named(document, "lightpanda")["zero_proof"] = _proof(
        document, "lightpanda", started_at=71
    )
    add("zero_proof_window_incomplete", document)
    document = _input()
    _lane_named(document, "lightpanda")["zero_proof"] = _proof(
        document, "lightpanda", complete=False
    )
    add("zero_proof_incomplete", document)

    document = _input(ready=[_placement(0, "lightpanda", admission="defer")])
    _lane_named(document, "lightpanda")["zero_proof"] = _proof(document, "lightpanda")
    add("zero_proof_demand_present", document)

    for field in ("assignment_count", "eligible_ready_count", "inflight_count", "ready_count"):
        document = _input()
        _lane_named(document, "lightpanda")["zero_proof"] = _proof(
            document, "lightpanda", **{field: 1}
        )
        add(f"zero_proof_{field}_census_mismatch", document)
    document = _input()
    _lane_named(document, "lightpanda")["zero_proof"] = _proof(
        document, "lightpanda", oldest_eligible_since=1
    )
    add("zero_proof_oldest_eligible_census_mismatch", document)

    for field in (
        "routing_revision",
        "policy_revision",
        "queue_revision",
        "config_revision",
        "capability_census_revision",
    ):
        document = _input()
        _lane_named(document, "lightpanda")["zero_proof"] = _proof(
            document, "lightpanda", **{field: f"{field}-old"}
        )
        add(f"zero_proof_{field}_mismatch", document)
    document = _input()
    proof = _proof(document, "lightpanda")
    proof["queue_fence"]["claim_fence"] = 99
    _lane_named(document, "lightpanda")["zero_proof"] = proof
    add("zero_proof_queue_fence_mismatch", document)
    for field, value in (
        ("config_revision", "config-old"),
        ("engine_owner", "owner-old"),
        ("queue_revision", "queue-old"),
        ("routing_epoch", 2),
        ("shard_id", "shard-old"),
    ):
        document = _input()
        proof = _proof(document, "lightpanda")
        proof["queue_fence"][field] = value
        _lane_named(document, "lightpanda")["zero_proof"] = proof
        add(f"zero_proof_queue_fence_{field}_mismatch", document)

    document = _input()
    _lane_named(document, "lightpanda")["zero_proof"] = _proof(document, "lightpanda")
    document["capability_census_revision"] = "census-2"
    add("top_level_capability_census_revision_change", document)

    for kind in ("assignment_created", "became_eligible"):
        document = _input()
        proof = _proof(document, "lightpanda")
        _lane_named(document, "lightpanda")["zero_proof"] = proof
        document["invalidation_events"] = [
            _event(document, "lightpanda", kind=kind, event_at=proof["completed_at"])
        ]
        add(f"zero_proof_equal_time_{kind}_invalidation", document)

    document = _input()
    proof = _proof(document, "lightpanda")
    _lane_named(document, "lightpanda")["zero_proof"] = proof
    event = _event(
        document, "lightpanda", kind="assignment_created", event_at=proof["completed_at"]
    )
    event["queue_revision"] = "queue-old"
    document["invalidation_events"] = [event]
    add("zero_proof_stale_revision_event_ignored", document)

    document = _input(ready=[_placement(0, "lightpanda")])
    document["declared_assignment_count"] = 2
    add("conservation_assignment_loss", document)

    document = _input(ready=[_placement(0, "lightpanda")], inflight=[_placement(1, "chromium")])
    document["placements"]["inflight"][0]["ordinal"] = 0
    add("conservation_duplicate_ordinal_cross_lane", document)

    placement = _placement(0, "lightpanda")
    document = _input(ready=[placement], inflight=[copy.deepcopy(placement)])
    add("conservation_ready_inflight_overlap", document)

    document = _input()
    _lane_named(document, "chromium")["queue_fence"] = copy.deepcopy(
        _lane_named(document, "lightpanda")["queue_fence"]
    )
    add("conservation_shared_cross_lane_fence", document)

    placement = _placement(0, "lightpanda")
    placement["fence"] = _fence("chromium")
    add("conservation_cross_lane_queue_occupancy", _input(ready=[placement]))

    document = _input(ready=[_placement(0, "lightpanda")])
    _lane_named(document, "lightpanda")["declared"]["ready_count"] = 0
    add("conservation_lane_count_mismatch", document)

    document = _input(inflight=[_placement(0, "chromium")])
    _lane_named(document, "chromium")["capacity"]["inflight"] = 0
    add("conservation_capacity_inflight_mismatch", document)

    for priority in ("monitor", "detail"):
        older = _placement(0, "lightpanda", eligible_since=100, priority=priority)
        newer = _placement(1, "lightpanda", eligible_since=999, priority="first_time")
        add(f"age_override_prevents_{priority}_starvation", _input(ready=[older, newer]))

    document = _input()
    document["unknown"] = 0
    add("malformed_semantic_unknown_key", document)
    return cases


REQUIRED_CATEGORY_CASES = frozenset(
    {
        "all_applicable_assignment_freezes_sorted",
        "all_applicable_safety_freezes_sorted",
        "assignment_invalid_capability",
        "assignment_immutable_copy_mutated",
        "assignment_lane_mismatch_both_lanes",
        "assignment_routing_revision_mismatch",
        "conservation_assignment_loss",
        "conservation_cross_lane_queue_occupancy",
        "conservation_duplicate_ordinal_cross_lane",
        "conservation_ready_inflight_overlap",
        "conservation_shared_cross_lane_fence",
        "error_budget_exact_boundary_claim",
        "fallback_chromium_to_lightpanda_blocked",
        "fallback_lightpanda_to_chromium_blocked",
        "malformed_semantic_unknown_key",
        "service_error",
        "service_full",
        "service_unready",
        "service_unsupported",
        "telemetry_fresh_exact_boundary_claim",
        "top_level_config_revision_change",
        "top_level_policy_revision_change",
        "top_level_queue_revision_change",
        "top_level_routing_revision_change",
        "zero_proof_demand_present",
        "zero_proof_equal_time_assignment_created_invalidation",
        "zero_proof_equal_time_became_eligible_invalidation",
        "zero_proof_queue_revision_mismatch",
    }
)


def coverage_matrix(cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    matrix = {reason: [] for reason in sorted(model.ALL_REASONS)}
    for case in cases:
        expected = case["expected"]
        if expected == {"error": "invalid_input"}:
            matrix["invalid_input"].append(case["id"])
            continue
        for lane in model.LANES:
            for reason in expected["lanes"][lane]["reasons"]:
                if case["id"] not in matrix[reason]:
                    matrix[reason].append(case["id"])
    return matrix


def audit_cases(cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Fail generation unless registry, category, byte, and digest coverage is complete."""
    if not 1 <= len(cases) <= model.MAX_CASES:
        raise AssertionError("case count outside v1 bounds")
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or any(
        model.SAFE_ID.fullmatch(case_id) is None for case_id in ids
    ):
        raise AssertionError("case IDs must be unique safe IDs")
    missing_categories = REQUIRED_CATEGORY_CASES - set(ids)
    if missing_categories:
        raise AssertionError(f"missing mandatory categories: {sorted(missing_categories)}")
    for case in cases:
        expected = model.evaluate(case["input"])
        if expected != case["expected"]:
            raise AssertionError(f"stale expected result: {case['id']}")
        if model.digest(expected) != case["result_digest"]:
            raise AssertionError(f"stale result digest: {case['id']}")
        if model.evaluate_document(model.canonical_bytes(case["input"])) != model.canonical_bytes(
            expected
        ):
            raise AssertionError(f"document entrypoint mismatch: {case['id']}")
        if "lanes" in expected:
            for lane in model.LANES:
                reasons = expected["lanes"][lane]["reasons"]
                if reasons != sorted(set(reasons)):
                    raise AssertionError(f"noncanonical reasons: {case['id']}:{lane}")
    matrix = coverage_matrix(cases)
    missing_reasons = [reason for reason, case_ids in matrix.items() if not case_ids]
    if missing_reasons:
        raise AssertionError(f"uncovered closed reasons: {missing_reasons}")
    multi_reason_cases = {
        case["id"]
        for case in cases
        if "lanes" in case["expected"]
        and any(len(case["expected"]["lanes"][lane]["reasons"]) >= 4 for lane in model.LANES)
    }
    if len(multi_reason_cases) < 2:
        raise AssertionError("at least two all-applicable multi-reason cases are required")
    return matrix


def rendered_files() -> tuple[bytes, bytes]:
    cases: list[dict[str, Any]] = []
    for source in source_cases():
        expected = model.evaluate(source["input"])
        cases.append(
            {
                "expected": expected,
                "id": source["id"],
                "input": source["input"],
                "result_digest": model.digest(expected),
            }
        )
    audit_cases(cases)
    corpus = {"cases": cases, "format": model.FORMAT}
    raw = model.canonical_bytes(corpus, newline=True)
    if model.parse_document(raw) != corpus:
        raise AssertionError("canonical corpus does not round-trip through the strict parser")
    sidecar = f"{hashlib.sha256(raw).hexdigest()}\n".encode("ascii")
    return raw, sidecar


def _decoded_cases(raw: bytes) -> list[dict[str, Any]]:
    corpus = model.parse_document(raw)
    if not isinstance(corpus, dict) or frozenset(corpus) != {"cases", "format"}:
        raise AssertionError("invalid corpus envelope")
    if corpus["format"] != model.FORMAT or not isinstance(corpus["cases"], list):
        raise AssertionError("invalid corpus format")
    return corpus["cases"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", action="store_true", help="print reason-to-case coverage")
    parser.add_argument("--check", action="store_true", help="fail unless checked-in bytes match")
    arguments = parser.parse_args(argv)
    corpus, sidecar = rendered_files()
    if arguments.audit:
        cases = _decoded_cases(corpus)
        matrix = audit_cases(cases)
        for reason, case_ids in matrix.items():
            print(f"{reason}: {','.join(case_ids)}")
        print(f"cases: {len(cases)}")
        print(f"sha256: {hashlib.sha256(corpus).hexdigest()}")
    if arguments.check:
        return int(
            not (
                CORPUS_PATH.exists()
                and DIGEST_PATH.exists()
                and CORPUS_PATH.read_bytes() == corpus
                and DIGEST_PATH.read_bytes() == sidecar
            )
        )
    if not arguments.audit:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        CORPUS_PATH.write_bytes(corpus)
        DIGEST_PATH.write_bytes(sidecar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
