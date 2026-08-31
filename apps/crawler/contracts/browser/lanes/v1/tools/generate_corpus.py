#!/usr/bin/env python3
"""Generate the checked-in synthetic browser-lanes v1 corpus."""

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


def _lane(name: str, *, now: int = 1_000, **overrides: Any) -> dict[str, Any]:
    lane: dict[str, Any] = {
        "lane": name,
        "routing_revision": "route-1",
        "policy_revision": "policy-1",
        "queue_revision": "queue-1",
        "config_revision": "config-1",
        "capability_census_revision": "census-1",
        "queue_shard_id": f"shard-{name}",
        "routing_epoch": 1,
        "engine_owner": "python",
        "capacity": {
            "current": 1,
            "desired": 1,
            "inflight": 0,
            "admitted": 1,
            "running": 0,
            "warm_floor": 1,
            "hard_max": 4,
            "scale_up_step": 1,
            "scale_down_step": 1,
            "last_scale_at": 0,
            "draining": False,
            "drain_started_at": 0,
        },
        "service": {"ready": True, "admission": "admitted"},
        "telemetry": {
            "observed_at": now,
            "queue_oldest_age": 0,
            "utilization_p95_ratio": 0.5,
            "headroom_p05_ratio": 0.5,
            "error_budget_burn": 0.0,
            "resource_saturated": False,
        },
        "declared": {"eligible_ready": 0, "oldest_eligible_age": 0},
        "zero_proof": None,
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(lane.get(key), dict):
            lane[key].update(value)
        else:
            lane[key] = value
    return lane


def _item(ordinal: int, lane: str, *, now: int = 1_000, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "ordinal": ordinal,
        "work_class": "monitor",
        "priority": "monitor",
        "lane": lane,
        "due_at": now,
        "eligible_since": now - 10,
        "assignment": {
            "backend": lane,
            "assignment_revision": "route-1",
            "immutable_copy": {"backend": lane, "assignment_revision": "route-1"},
        },
        "queue": {
            "route_revision": "route-1",
            "config_revision": "config-1",
            "epoch": 1,
            "owner": "python",
            "claim_fence": ordinal + 1,
        },
        "admission": {"verdict": "permit", "policy_revision": "policy-1"},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(item.get(key), dict):
            item[key].update(value)
        else:
            item[key] = value
    return item


def _proof(lane: dict[str, Any], *, now: int = 1_000, **overrides: Any) -> dict[str, Any]:
    proof: dict[str, Any] = {
        "routing_revision": lane["routing_revision"],
        "policy_revision": lane["policy_revision"],
        "queue_shard_id": lane["queue_shard_id"],
        "routing_epoch": lane["routing_epoch"],
        "engine_owner": lane["engine_owner"],
        "config_revision": lane["config_revision"],
        "capability_census_revision": lane["capability_census_revision"],
        "started_at": now - 930,
        "completed_at": now - 30,
        "queue_count": 0,
        "inflight_count": 0,
        "assignment_count": 0,
        "eligible_ready_count": 0,
        "oldest_eligible_since": None,
    }
    proof.update(overrides)
    return proof


def _input(
    *,
    now: int = 1_000,
    items: list[dict[str, Any]] | None = None,
    lightpanda: dict[str, Any] | None = None,
    chromium: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "now": now,
        "policy_revision": "policy-1",
        "routing_revision": "route-1",
        "queue_revision": "queue-1",
        "config_revision": "config-1",
        "capability_census_revision": "census-1",
        "items": items or [],
        "lanes": {
            "lightpanda": lightpanda or _lane("lightpanda", now=now),
            "chromium": chromium or _lane("chromium", now=now),
        },
    }


def _declare(document: dict[str, Any]) -> dict[str, Any]:
    for name, lane in document["lanes"].items():
        eligible = [
            item
            for item in document["items"]
            if item["lane"] == name
            and item["due_at"] <= document["now"]
            and item["admission"]["verdict"] == "permit"
        ]
        lane["declared"] = {
            "eligible_ready": len(eligible),
            "oldest_eligible_age": max(
                (document["now"] - item["eligible_since"] for item in eligible), default=0
            ),
        }
    return document


def source_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def add(case_id: str, document: dict[str, Any]) -> None:
        cases.append({"id": case_id, "input": _declare(document)})

    add("claim_lightpanda_only", _input(items=[_item(0, "lightpanda")]))
    add("claim_chromium_only", _input(items=[_item(0, "chromium")]))
    exact = _input(items=[_item(0, "lightpanda")])
    exact["lanes"]["lightpanda"]["telemetry"].update(
        {"utilization_p95_ratio": 0.85, "headroom_p05_ratio": 0.15}
    )
    add("capacity_exact_boundary_claims", exact)
    unsafe_util = _input(items=[_item(0, "lightpanda")])
    unsafe_util["lanes"]["lightpanda"]["telemetry"]["utilization_p95_ratio"] = 0.850001
    add("capacity_utilization_above_boundary_defers", unsafe_util)
    unsafe_headroom = _input(items=[_item(0, "chromium")])
    unsafe_headroom["lanes"]["chromium"]["telemetry"]["headroom_p05_ratio"] = 0.149999
    add("capacity_headroom_below_boundary_defers", unsafe_headroom)
    scale = _input(items=[_item(0, "lightpanda")])
    scale["lanes"]["lightpanda"]["capacity"].update(
        {"inflight": 1, "running": 1, "admitted": 1, "current": 1}
    )
    add("eligible_backlog_requests_bounded_scale_up", scale)
    hard_max = copy.deepcopy(scale)
    hard_max["lanes"]["lightpanda"]["capacity"]["hard_max"] = 1
    add("hard_max_blocks_scale_up", hard_max)
    cooldown = copy.deepcopy(scale)
    cooldown["lanes"]["lightpanda"]["capacity"]["last_scale_at"] = 950
    add("cooldown_blocks_scale_up", cooldown)
    drain = copy.deepcopy(scale)
    drain["lanes"]["lightpanda"]["capacity"].update({"draining": True, "drain_started_at": 990})
    add("drain_blocks_scale_up", drain)
    for admission, case_id in (
        ("defer", "publisher_defer_is_ineligible"),
        ("deny", "publisher_deny_is_ineligible"),
    ):
        document = _input(
            items=[
                _item(
                    0, "lightpanda", admission={"verdict": admission, "policy_revision": "policy-1"}
                )
            ]
        )
        add(case_id, document)
    violation = _input(
        items=[
            _item(
                0, "lightpanda", admission={"verdict": "violation", "policy_revision": "policy-1"}
            )
        ]
    )
    add("publisher_violation_freezes_only_its_lane", violation)
    mutation = _input(
        items=[
            _item(
                0,
                "chromium",
                assignment={
                    "backend": "chromium",
                    "assignment_revision": "route-1",
                    "immutable_copy": {"backend": "chromium", "assignment_revision": "route-0"},
                },
            )
        ]
    )
    add("assignment_mutation_freezes", mutation)
    mismatch = _input(
        items=[
            _item(
                0,
                "lightpanda",
                assignment={
                    "backend": "chromium",
                    "assignment_revision": "route-1",
                    "immutable_copy": {"backend": "chromium", "assignment_revision": "route-1"},
                },
            )
        ]
    )
    add("assignment_lane_mismatch_freezes", mismatch)
    fence = _input(
        items=[
            _item(
                0,
                "lightpanda",
                queue={
                    "route_revision": "route-1",
                    "config_revision": "config-1",
                    "epoch": 2,
                    "owner": "python",
                    "claim_fence": 1,
                },
            )
        ]
    )
    add("queue_fence_mismatch_freezes", fence)
    for field, value, case_id in (
        ("error_budget_burn", 1.000001, "error_budget_exhausted_freezes"),
        ("resource_saturated", True, "resource_saturation_freezes"),
        ("observed_at", 969, "stale_telemetry_freezes"),
    ):
        document = _input(items=[_item(0, "lightpanda")])
        document["lanes"]["lightpanda"]["telemetry"][field] = value
        add(case_id, document)
    low = _input()
    low["lanes"]["lightpanda"]["telemetry"]["utilization_p95_ratio"] = 0.0
    add("low_utilization_does_not_manufacture_work", low)
    valid_zero = _input(
        lightpanda=_lane(
            "lightpanda",
            capacity={
                "current": 0,
                "desired": 0,
                "inflight": 0,
                "admitted": 0,
                "running": 0,
                "warm_floor": 0,
                "hard_max": 4,
                "scale_up_step": 1,
                "scale_down_step": 1,
                "last_scale_at": 0,
                "draining": False,
                "drain_started_at": 0,
            },
        )
    )
    valid_zero["lanes"]["lightpanda"]["zero_proof"] = _proof(valid_zero["lanes"]["lightpanda"])
    add("valid_fresh_complete_zero_proof_reaches_zero", valid_zero)
    proof_changes: tuple[tuple[dict[str, Any] | None, str], ...] = (
        (None, "zero_proof_absent_retains_floor"),
        ({"completed_at": 969}, "zero_proof_stale_retains_floor"),
        ({"started_at": 100}, "zero_proof_incomplete_window_retains_floor"),
        ({"routing_revision": "route-0"}, "zero_proof_revision_mismatch_retains_floor"),
        ({"queue_count": 1, "assignment_count": 1}, "zero_proof_demand_present_retains_floor"),
    )
    for change, case_id in proof_changes:
        document = _input()
        if change is not None:
            if case_id == "zero_proof_demand_present_retains_floor":
                document["items"] = [
                    _item(
                        0,
                        "lightpanda",
                        admission={"verdict": "defer", "policy_revision": "policy-1"},
                    )
                ]
            document["lanes"]["lightpanda"]["zero_proof"] = _proof(
                document["lanes"]["lightpanda"], **change
            )
        add(case_id, document)
    age = _input(
        items=[
            _item(0, "lightpanda", priority="detail", eligible_since=100),
            _item(1, "lightpanda", priority="first_time", eligible_since=999),
        ]
    )
    add("age_override_beats_new_first_time", age)
    credit = _input(
        items=[
            _item(0, "chromium", priority="detail", eligible_since=900),
            _item(1, "chromium", priority="monitor", eligible_since=900),
        ]
    )
    add("priority_credit_then_deterministic_tie", credit)
    return cases


def rendered_files() -> tuple[bytes, bytes]:
    cases = []
    for source in source_cases():
        expected = model.evaluate(source["input"])
        cases.append(
            {
                "id": source["id"],
                "input": source["input"],
                "expected": expected,
                "result_digest": model.digest(expected),
            }
        )
    corpus = {"format": model.FORMAT, "cases": cases}
    raw = model.canonical_bytes(corpus, newline=True)
    return raw, f"{hashlib.sha256(raw).hexdigest()}  scenarios.json\n".encode("ascii")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    corpus, digest = rendered_files()
    if arguments.check:
        return int(
            not (
                CORPUS_PATH.exists()
                and DIGEST_PATH.exists()
                and CORPUS_PATH.read_bytes() == corpus
                and DIGEST_PATH.read_bytes() == digest
            )
        )
    FIXTURES.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_bytes(corpus)
    DIGEST_PATH.write_bytes(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
