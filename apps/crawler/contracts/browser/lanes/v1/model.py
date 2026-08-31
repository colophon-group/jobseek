"""Strict, deterministic browser-lane v1 reference evaluator.

This is an intentionally offline contract.  It neither imports nor calls the
crawler, Redis, a browser service, or a clock.  The only time input is ``now``
in the supplied JSON document.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from typing import Any

FORMAT = "jobseek.browser-lanes.v1.conformance/v1"
LANES = ("lightpanda", "chromium")
MAX_DOCUMENT_BYTES = 1_048_576
MAX_DEPTH = 12
MAX_ARRAY = 4096
MAX_OBJECT = 64
MAX_STRING_BYTES = 4096
MAX_CASES = 512
MAX_ITEMS = 4096
MAX_INTEGER = 9_007_199_254_740_991
MAX_CONCURRENCY = 4096
TELEMETRY_MAX_AGE = 30
ZERO_PROOF_MAX_AGE = 30
ZERO_PROOF_MIN_WINDOW = 900
DRAIN_WINDOW = 30
SCALE_COOLDOWN = 60
AGE_OVERRIDE = 900
PRIORITY_CREDIT = {"first_time": 300, "monitor": 60, "detail": 0}
PRIORITY_ORDER = {"first_time": 0, "monitor": 1, "detail": 2}

DEFER_REASONS = frozenset(
    {
        "capacity_headroom_unsafe",
        "no_eligible_backlog",
        "scale_up_requested",
        "scale_cooldown_active",
        "hard_max_reached",
        "drain_active",
        "zero_proof_absent",
        "zero_proof_stale",
        "zero_proof_invalid",
        "zero_proof_revision_mismatch",
        "zero_proof_demand_present",
    }
)
FREEZE_REASONS = frozenset(
    {
        "error_budget_exhausted",
        "resource_saturation",
        "telemetry_stale",
        "invalid_input",
        "conservation_failure",
        "assignment_invalid",
        "assignment_mutated",
        "assignment_lane_mismatch",
        "revision_mismatch",
        "queue_fence_invalid",
        "policy_violation",
        "service_unready",
        "service_error",
        "service_unsupported",
        "service_full",
        "fallback_attempted",
    }
)
ALL_REASONS = DEFER_REASONS | FREEZE_REASONS
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$", re.ASCII)
PRIVATE_STRING = re.compile(
    r"[\x00-\x1f\x7f]|://|www\\.|@|[?#/\\\\]|(?:\d{1,3}\.){3}\d{1,3}|\[[0-9A-Fa-f:]+\]|"
    r"(?:[A-Za-z0-9-]+\.)+[A-Za-z][A-Za-z0-9-]*|"
    r"authorization|bearer|token|secret|password|apikey|api_key|cookie|session|key=",
    re.IGNORECASE,
)

ROOT_KEYS = frozenset(
    {
        "now",
        "policy_revision",
        "routing_revision",
        "queue_revision",
        "config_revision",
        "capability_census_revision",
        "items",
        "lanes",
    }
)
ITEM_KEYS = frozenset(
    {
        "ordinal",
        "work_class",
        "priority",
        "lane",
        "due_at",
        "eligible_since",
        "assignment",
        "queue",
        "admission",
    }
)
ASSIGNMENT_KEYS = frozenset({"backend", "assignment_revision", "immutable_copy"})
ASSIGNMENT_COPY_KEYS = frozenset({"backend", "assignment_revision"})
QUEUE_KEYS = frozenset({"route_revision", "config_revision", "epoch", "owner", "claim_fence"})
ADMISSION_KEYS = frozenset({"verdict", "policy_revision"})
LANE_KEYS = frozenset(
    {
        "lane",
        "routing_revision",
        "policy_revision",
        "queue_revision",
        "config_revision",
        "capability_census_revision",
        "queue_shard_id",
        "routing_epoch",
        "engine_owner",
        "capacity",
        "service",
        "telemetry",
        "declared",
        "zero_proof",
    }
)
CAPACITY_KEYS = frozenset(
    {
        "current",
        "desired",
        "inflight",
        "admitted",
        "running",
        "warm_floor",
        "hard_max",
        "scale_up_step",
        "scale_down_step",
        "last_scale_at",
        "draining",
        "drain_started_at",
    }
)
SERVICE_KEYS = frozenset({"ready", "admission"})
TELEMETRY_KEYS = frozenset(
    {
        "observed_at",
        "queue_oldest_age",
        "utilization_p95_ratio",
        "headroom_p05_ratio",
        "error_budget_burn",
        "resource_saturated",
    }
)
DECLARED_KEYS = frozenset({"eligible_ready", "oldest_eligible_age"})
PROOF_KEYS = frozenset(
    {
        "routing_revision",
        "policy_revision",
        "queue_shard_id",
        "routing_epoch",
        "engine_owner",
        "config_revision",
        "capability_census_revision",
        "started_at",
        "completed_at",
        "queue_count",
        "inflight_count",
        "assignment_count",
        "eligible_ready_count",
        "oldest_eligible_since",
    }
)


class ContractError(ValueError):
    """A deliberately detail-free contract parse/evaluation failure."""


def canonical_bytes(value: Any, *, newline: bool = False) -> bytes:
    """Return the v1 canonical JSON form without relying on locale or a clock."""
    rendered = json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return (rendered + ("\n" if newline else "")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ContractError("invalid_input")
        output[key] = value
    return output


def _reject_constant(value: str) -> None:
    raise ContractError("invalid_input")


def parse_document(raw: bytes) -> Any:
    """Strictly parse one canonical JSON document; never reflect parser input."""
    if not isinstance(raw, bytes) or len(raw) > MAX_DOCUMENT_BYTES:
        raise ContractError("invalid_input")
    try:
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_strict_object, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ContractError):
        raise ContractError("invalid_input") from None
    _validate_json_shape(value)
    # Corpus files have one final LF; payload inputs have no insignificant whitespace.
    accepted = {canonical_bytes(value), canonical_bytes(value, newline=True)}
    if raw not in accepted:
        raise ContractError("invalid_input")
    return value


def load_json(path: Any) -> Any:
    """Load a contract document through the strict canonical parser."""
    return parse_document(path.read_bytes())


def _validate_json_shape(value: Any, depth: int = 1) -> None:
    if depth > MAX_DEPTH:
        raise ContractError("invalid_input")
    if isinstance(value, str):
        # The fixed corpus format is protocol metadata, not caller-supplied
        # work data; its required ``/v1`` suffix is the one safe exception.
        if len(value.encode("utf-8")) > MAX_STRING_BYTES or (
            value != FORMAT and PRIVATE_STRING.search(value)
        ):
            raise ContractError("invalid_input")
        return
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if value < 0 or value > MAX_INTEGER:
            raise ContractError("invalid_input")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError("invalid_input")
        return
    if isinstance(value, list):
        if len(value) > MAX_ARRAY:
            raise ContractError("invalid_input")
        for child in value:
            _validate_json_shape(child, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT:
            raise ContractError("invalid_input")
        for key, child in value.items():
            if not isinstance(key, str):
                raise ContractError("invalid_input")
            _validate_json_shape(key, depth + 1)
            _validate_json_shape(child, depth + 1)
        return
    raise ContractError("invalid_input")


def _exact(value: Any, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise ContractError("invalid_input")
    return value


def _uint(value: Any, *, maximum: int = MAX_INTEGER) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ContractError("invalid_input")
    return value


def _safe_id(value: Any) -> str:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        raise ContractError("invalid_input")
    return value


def _ratio(value: Any, *, maximum: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= maximum:
        raise ContractError("invalid_input")
    # json's parser retains the lexical input only via reserialization; comparing
    # it to the shortest canonical JSON spelling rejects exponent/trailing zeros.
    if isinstance(value, float):
        fraction = str(value).partition(".")[2]
        if len(fraction) > 6:
            raise ContractError("invalid_input")
    return float(value)


def _base_lane_result(
    lane: str, desired: int, decision: str, reasons: Iterable[str], selected: int | None
) -> dict[str, Any]:
    unique = sorted(set(reasons))
    if decision == "claim":
        unique = []
        if selected is None:
            raise ContractError("invalid_input")
    elif selected is not None:
        raise ContractError("invalid_input")
    return {
        "decision": decision,
        "desired_concurrency": desired,
        "lane": lane,
        "reasons": unique,
        "selected_item_index": selected,
    }


def _validate_input(value: Any) -> dict[str, Any]:
    root = _exact(value, ROOT_KEYS)
    _uint(root["now"])
    for key in (
        "policy_revision",
        "routing_revision",
        "queue_revision",
        "config_revision",
        "capability_census_revision",
    ):
        _safe_id(root[key])
    if not isinstance(root["items"], list) or len(root["items"]) > MAX_ITEMS:
        raise ContractError("invalid_input")
    if not isinstance(root["lanes"], dict) or frozenset(root["lanes"]) != frozenset(LANES):
        raise ContractError("invalid_input")
    seen_ordinals: set[int] = set()
    for index, item in enumerate(root["items"]):
        _validate_item(item, root, index)
        ordinal = item["ordinal"]
        if ordinal != index or ordinal in seen_ordinals:
            raise ContractError("invalid_input")
        seen_ordinals.add(ordinal)
    for lane in LANES:
        _validate_lane(root["lanes"][lane], root, lane)
    return root


def _validate_item(item: Any, root: dict[str, Any], index: int) -> None:
    item = _exact(item, ITEM_KEYS)
    if _uint(item["ordinal"], maximum=MAX_ITEMS - 1) != index:
        raise ContractError("invalid_input")
    if item["work_class"] not in {"monitor", "detail"} or item["priority"] not in PRIORITY_CREDIT:
        raise ContractError("invalid_input")
    if (
        item["lane"] not in LANES
        or _uint(item["due_at"]) > root["now"]
        or _uint(item["eligible_since"]) > root["now"]
    ):
        raise ContractError("invalid_input")
    assignment = _exact(item["assignment"], ASSIGNMENT_KEYS)
    immutable = _exact(assignment["immutable_copy"], ASSIGNMENT_COPY_KEYS)
    if assignment["backend"] not in LANES:
        raise ContractError("invalid_input")
    _safe_id(assignment["assignment_revision"])
    if immutable["backend"] not in LANES:
        raise ContractError("invalid_input")
    _safe_id(immutable["assignment_revision"])
    queue = _exact(item["queue"], QUEUE_KEYS)
    _safe_id(queue["route_revision"])
    _safe_id(queue["config_revision"])
    _uint(queue["epoch"])
    _safe_id(queue["owner"])
    _uint(queue["claim_fence"])
    admission = _exact(item["admission"], ADMISSION_KEYS)
    if admission["verdict"] not in {"permit", "defer", "deny", "violation"}:
        raise ContractError("invalid_input")
    _safe_id(admission["policy_revision"])


def _validate_lane(value: Any, root: dict[str, Any], name: str) -> None:
    lane = _exact(value, LANE_KEYS)
    if lane["lane"] != name:
        raise ContractError("invalid_input")
    for key in (
        "routing_revision",
        "policy_revision",
        "queue_revision",
        "config_revision",
        "capability_census_revision",
    ):
        if lane[key] != root[key]:
            raise ContractError("invalid_input")
    _safe_id(lane["queue_shard_id"])
    _uint(lane["routing_epoch"])
    _safe_id(lane["engine_owner"])
    capacity = _exact(lane["capacity"], CAPACITY_KEYS)
    for key in (
        "current",
        "desired",
        "inflight",
        "admitted",
        "running",
        "warm_floor",
        "hard_max",
        "scale_up_step",
        "scale_down_step",
        "last_scale_at",
        "drain_started_at",
    ):
        _uint(
            capacity[key],
            maximum=MAX_CONCURRENCY
            if key not in {"last_scale_at", "drain_started_at"}
            else MAX_INTEGER,
        )
    if (
        not isinstance(capacity["draining"], bool)
        or capacity["scale_up_step"] < 1
        or capacity["scale_down_step"] < 1
    ):
        raise ContractError("invalid_input")
    service = _exact(lane["service"], SERVICE_KEYS)
    if not isinstance(service["ready"], bool) or service["admission"] not in {
        "admitted",
        "unready",
        "error",
        "unsupported",
        "full",
    }:
        raise ContractError("invalid_input")
    telemetry = _exact(lane["telemetry"], TELEMETRY_KEYS)
    _uint(telemetry["observed_at"])
    _uint(telemetry["queue_oldest_age"])
    for key in ("utilization_p95_ratio", "headroom_p05_ratio"):
        _ratio(telemetry[key])
    # Error-budget burn uses the same decimal grammar but is deliberately able
    # to exceed 1.0 so the evaluator can freeze at the specified boundary.
    _ratio(telemetry["error_budget_burn"], maximum=float(MAX_INTEGER))
    if not isinstance(telemetry["resource_saturated"], bool):
        raise ContractError("invalid_input")
    declared = _exact(lane["declared"], DECLARED_KEYS)
    _uint(declared["eligible_ready"])
    _uint(declared["oldest_eligible_age"])
    if lane["zero_proof"] is not None:
        proof = _exact(lane["zero_proof"], PROOF_KEYS)
        for key in (
            "routing_revision",
            "policy_revision",
            "queue_shard_id",
            "engine_owner",
            "config_revision",
            "capability_census_revision",
        ):
            _safe_id(proof[key])
        for key in (
            "routing_epoch",
            "started_at",
            "completed_at",
            "queue_count",
            "inflight_count",
            "assignment_count",
            "eligible_ready_count",
        ):
            _uint(proof[key])
        if proof["oldest_eligible_since"] is not None:
            _uint(proof["oldest_eligible_since"])


def _capacity_base(capacity: dict[str, Any]) -> int | None:
    current, inflight, running, admitted, floor, maximum = (
        capacity["current"],
        capacity["inflight"],
        capacity["running"],
        capacity["admitted"],
        capacity["warm_floor"],
        capacity["hard_max"],
    )
    if not (
        inflight <= running <= admitted <= current <= maximum <= MAX_CONCURRENCY
        and floor <= maximum
        and capacity["desired"] <= MAX_CONCURRENCY
    ):
        return None
    return min(max(current, inflight, floor), maximum)


def _item_failure(item: dict[str, Any], root: dict[str, Any], lane: dict[str, Any]) -> str | None:
    assignment = item["assignment"]
    immutable = assignment["immutable_copy"]
    if (
        assignment["backend"] != immutable["backend"]
        or assignment["assignment_revision"] != immutable["assignment_revision"]
    ):
        return "assignment_mutated"
    if assignment["backend"] != item["lane"]:
        return "assignment_lane_mismatch"
    if assignment["assignment_revision"] != root["routing_revision"]:
        return "revision_mismatch"
    queue = item["queue"]
    if (
        queue["route_revision"] != root["routing_revision"]
        or queue["config_revision"] != root["config_revision"]
    ):
        return "queue_fence_invalid"
    if queue["epoch"] != lane["routing_epoch"] or queue["owner"] != lane["engine_owner"]:
        return "queue_fence_invalid"
    admission = item["admission"]
    if (
        admission["policy_revision"] != root["policy_revision"]
        or admission["verdict"] == "violation"
    ):
        return "policy_violation"
    return None


def _eligible_items(root: dict[str, Any], lane_name: str) -> tuple[list[dict[str, Any]], set[str]]:
    lane = root["lanes"][lane_name]
    eligible: list[dict[str, Any]] = []
    failures: set[str] = set()
    fences: set[tuple[int, int]] = set()
    for item in root["items"]:
        if item["lane"] != lane_name:
            continue
        failure = _item_failure(item, root, lane)
        if failure:
            failures.add(failure)
            continue
        identity = (item["queue"]["epoch"], item["queue"]["claim_fence"])
        if identity in fences:
            failures.add("queue_fence_invalid")
        fences.add(identity)
        if item["due_at"] <= root["now"] and item["admission"]["verdict"] == "permit":
            eligible.append(item)
    return eligible, failures


def _choose(items: list[dict[str, Any]], now: int) -> dict[str, Any]:
    overrides = [item for item in items if now - item["eligible_since"] >= AGE_OVERRIDE]
    if overrides:
        return min(
            overrides,
            key=lambda item: (
                item["eligible_since"],
                PRIORITY_ORDER[item["priority"]],
                item["ordinal"],
            ),
        )
    return min(
        items,
        key=lambda item: (
            -(now - item["eligible_since"] + PRIORITY_CREDIT[item["priority"]]),
            item["eligible_since"],
            PRIORITY_ORDER[item["priority"]],
            item["ordinal"],
        ),
    )


def _proof_reason(
    root: dict[str, Any], lane: dict[str, Any], eligible: list[dict[str, Any]]
) -> str | None:
    proof = lane["zero_proof"]
    if proof is None:
        return "zero_proof_absent"
    revision_pairs = (
        ("routing_revision", lane["routing_revision"]),
        ("policy_revision", lane["policy_revision"]),
        ("queue_shard_id", lane["queue_shard_id"]),
        ("routing_epoch", lane["routing_epoch"]),
        ("engine_owner", lane["engine_owner"]),
        ("config_revision", lane["config_revision"]),
        ("capability_census_revision", lane["capability_census_revision"]),
    )
    if any(proof[key] != expected for key, expected in revision_pairs):
        return "zero_proof_revision_mismatch"
    if proof["completed_at"] < proof["started_at"] or proof["completed_at"] > root["now"]:
        return "zero_proof_invalid"
    if root["now"] - proof["completed_at"] > ZERO_PROOF_MAX_AGE:
        return "zero_proof_stale"
    if proof["completed_at"] - proof["started_at"] < ZERO_PROOF_MIN_WINDOW:
        return "zero_proof_invalid"
    capacity = lane["capacity"]
    expected = (
        0,
        capacity["inflight"],
        len([item for item in root["items"] if item["lane"] == lane["lane"]]),
        len(eligible),
    )
    actual = (
        proof["queue_count"],
        proof["inflight_count"],
        proof["assignment_count"],
        proof["eligible_ready_count"],
    )
    if actual != expected or proof["oldest_eligible_since"] != (
        min((item["eligible_since"] for item in eligible), default=None)
    ):
        return "zero_proof_invalid"
    if any(actual):
        return "zero_proof_demand_present"
    # The item schema has no separate assignment event.  Assignment binding and
    # entry into eligibility are both represented by eligible_since in v1.
    if any(
        item["eligible_since"] >= proof["completed_at"]
        for item in root["items"]
        if item["lane"] == lane["lane"]
    ):
        return "zero_proof_invalid"
    return None


def _evaluate_lane(root: dict[str, Any], lane_name: str) -> dict[str, Any]:
    lane = root["lanes"][lane_name]
    capacity = lane["capacity"]
    base = _capacity_base(capacity)
    if base is None:
        return _base_lane_result(
            lane_name,
            max(capacity["inflight"], capacity["warm_floor"]),
            "freeze",
            {"invalid_input"},
            None,
        )
    eligible, item_failures = _eligible_items(root, lane_name)
    if item_failures:
        return _base_lane_result(lane_name, base, "freeze", item_failures, None)
    declared_age = max((root["now"] - item["eligible_since"] for item in eligible), default=0)
    if (
        lane["declared"]["eligible_ready"] != len(eligible)
        or lane["declared"]["oldest_eligible_age"] != declared_age
    ):
        return _base_lane_result(lane_name, base, "freeze", {"conservation_failure"}, None)
    service = lane["service"]
    service_reason = {
        "unready": "service_unready",
        "error": "service_error",
        "unsupported": "service_unsupported",
        "full": "service_full",
    }.get(service["admission"])
    if not service["ready"] or service_reason:
        return _base_lane_result(
            lane_name, base, "freeze", {service_reason or "service_unready"}, None
        )
    telemetry = lane["telemetry"]
    if (
        telemetry["observed_at"] > root["now"]
        or root["now"] - telemetry["observed_at"] > TELEMETRY_MAX_AGE
    ):
        return _base_lane_result(lane_name, base, "freeze", {"telemetry_stale"}, None)
    if telemetry["error_budget_burn"] > 1.0:
        return _base_lane_result(lane_name, base, "freeze", {"error_budget_exhausted"}, None)
    if telemetry["resource_saturated"]:
        return _base_lane_result(lane_name, base, "freeze", {"resource_saturation"}, None)
    if telemetry["utilization_p95_ratio"] > 0.85 or telemetry["headroom_p05_ratio"] < 0.15:
        return _base_lane_result(lane_name, base, "defer", {"capacity_headroom_unsafe"}, None)
    if eligible:
        if capacity["admitted"] > capacity["inflight"]:
            return _base_lane_result(
                lane_name, base, "claim", (), _choose(eligible, root["now"])["ordinal"]
            )
        blockers: set[str] = set()
        draining = (
            capacity["draining"]
            and capacity["drain_started_at"] > 0
            and root["now"] - capacity["drain_started_at"] < DRAIN_WINDOW
        )
        cooldown = (
            capacity["last_scale_at"] != 0
            and root["now"] - capacity["last_scale_at"] < SCALE_COOLDOWN
        )
        if draining:
            blockers.add("drain_active")
        if cooldown:
            blockers.add("scale_cooldown_active")
        if base >= capacity["hard_max"]:
            blockers.add("hard_max_reached")
        if blockers:
            return _base_lane_result(lane_name, base, "defer", blockers, None)
        return _base_lane_result(
            lane_name,
            min(capacity["hard_max"], base + capacity["scale_up_step"]),
            "defer",
            {"scale_up_requested"},
            None,
        )
    reason = _proof_reason(root, lane, eligible)
    if reason is None and all(
        capacity[key] == 0 for key in ("inflight", "running", "admitted", "current")
    ):
        drain_complete = not capacity["draining"] or (
            capacity["drain_started_at"] > 0
            and root["now"] - capacity["drain_started_at"] >= DRAIN_WINDOW
        )
        cooldown_complete = (
            capacity["last_scale_at"] == 0
            or root["now"] - capacity["last_scale_at"] >= SCALE_COOLDOWN
        )
        if drain_complete and cooldown_complete:
            return _base_lane_result(lane_name, 0, "defer", {"no_eligible_backlog"}, None)
        reason = "drain_active" if not drain_complete else "scale_cooldown_active"
    return _base_lane_result(
        lane_name, base, "defer", {"no_eligible_backlog", reason or "zero_proof_invalid"}, None
    )


def evaluate(value: Any) -> dict[str, Any]:
    """Return the only public normalized result; invalid input never leaks detail."""
    try:
        root = _validate_input(value)
        return {"lanes": {lane: _evaluate_lane(root, lane) for lane in LANES}}
    except (ContractError, KeyError, TypeError, ValueError):
        return {"error": "invalid_input"}


def evaluate_document(raw: bytes) -> bytes:
    """Strict document entrypoint returning only canonical normalized output."""
    try:
        return canonical_bytes(evaluate(parse_document(raw)))
    except ContractError:
        return canonical_bytes({"error": "invalid_input"})
