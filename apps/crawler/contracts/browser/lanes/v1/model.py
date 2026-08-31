"""Strict, deterministic browser-lane v1 reference evaluator.

The evaluator consumes one complete, global two-lane snapshot.  It is an
offline contract: the supplied document is its only source of state or time.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
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
        "declared_assignment_count",
        "invalidation_events",
        "lanes",
        "placements",
    }
)
PLACEMENTS_KEYS = frozenset({"ready", "inflight"})
PLACEMENT_KEYS = frozenset(
    {
        "admission",
        "assignment",
        "due_at",
        "eligible_since",
        "fallback_target",
        "fence",
        "lane",
        "ordinal",
        "priority",
        "work_class",
    }
)
ADMISSION_KEYS = frozenset({"policy_revision", "verdict"})
ASSIGNMENT_KEYS = frozenset(
    {"backend", "capability_class", "immutable_copy", "routing_revision", "service_lane"}
)
ASSIGNMENT_COPY_KEYS = frozenset(
    {"backend", "capability_class", "routing_revision", "service_lane"}
)
FENCE_KEYS = frozenset(
    {
        "claim_fence",
        "config_revision",
        "engine_owner",
        "queue_revision",
        "routing_epoch",
        "shard_id",
    }
)
LANE_KEYS = frozenset(
    {"capacity", "declared", "lane", "queue_fence", "service_state", "telemetry", "zero_proof"}
)
CAPACITY_KEYS = frozenset(
    {
        "admitted",
        "current",
        "desired",
        "drain_started_at",
        "draining",
        "hard_max",
        "inflight",
        "last_scale_at",
        "running",
        "scale_down_step",
        "scale_up_step",
        "warm_floor",
    }
)
DECLARED_KEYS = frozenset(
    {
        "assignment_count",
        "eligible_ready_count",
        "inflight_count",
        "oldest_eligible_age",
        "ready_count",
    }
)
TELEMETRY_KEYS = frozenset(
    {
        "error_budget_burn",
        "headroom_p05_ratio",
        "observed_at",
        "queue_oldest_age",
        "resource_saturated",
        "utilization_p95_ratio",
    }
)
PROOF_KEYS = frozenset(
    {
        "assignment_count",
        "capability_census_revision",
        "complete",
        "completed_at",
        "config_revision",
        "eligible_ready_count",
        "inflight_count",
        "oldest_eligible_since",
        "policy_revision",
        "queue_fence",
        "queue_revision",
        "ready_count",
        "routing_revision",
        "started_at",
    }
)
EVENT_KEYS = frozenset(
    {
        "capability_census_revision",
        "config_revision",
        "event_at",
        "event_ordinal",
        "kind",
        "lane",
        "policy_revision",
        "queue_revision",
        "routing_revision",
        "work_ordinal",
    }
)


class ContractError(ValueError):
    """A deliberately detail-free contract parse/evaluation failure."""


def canonical_bytes(value: Any, *, newline: bool = False) -> bytes:
    rendered = _canonical_text(value)
    return (rendered + ("\n" if newline else "")).encode("utf-8")


def _canonical_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        whole, _, fraction = f"{value:.6f}".partition(".")
        return f"{whole}.{fraction.rstrip('0') or '0'}"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return f"[{','.join(_canonical_text(child) for child in value)}]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return (
            "{"
            + ",".join(
                f"{_canonical_text(key)}:{_canonical_text(value[key])}" for key in sorted(value)
            )
            + "}"
        )
    raise TypeError("unsupported JSON value")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ContractError("invalid_input")
        output[key] = value
    return output


def _reject_constant(_value: str) -> None:
    raise ContractError("invalid_input")


def _parse_integer(token: str) -> int:
    if re.fullmatch(r"0|[1-9][0-9]*", token, re.ASCII) is None:
        raise ContractError("invalid_input")
    value = int(token)
    if value > MAX_INTEGER:
        raise ContractError("invalid_input")
    return value


def _parse_decimal(token: str) -> float:
    match = re.fullmatch(r"(?:0|[1-9][0-9]*)\.([0-9]{1,6})", token, re.ASCII)
    if match is None or (match.group(1).endswith("0") and match.group(1) != "0"):
        raise ContractError("invalid_input")
    return float(token)


def parse_document(raw: bytes) -> Any:
    """Strictly parse one canonical JSON document; never reflect parser input."""
    if not isinstance(raw, bytes) or len(raw) > MAX_DOCUMENT_BYTES:
        raise ContractError("invalid_input")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
            parse_float=_parse_decimal,
            parse_int=_parse_integer,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ContractError):
        raise ContractError("invalid_input") from None
    _validate_json_shape(value)
    if raw not in {canonical_bytes(value), canonical_bytes(value, newline=True)}:
        raise ContractError("invalid_input")
    return value


def load_json(path: Any) -> Any:
    return parse_document(path.read_bytes())


def _validate_json_shape(value: Any, depth: int = 1) -> None:
    if depth > MAX_DEPTH:
        raise ContractError("invalid_input")
    if isinstance(value, str):
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
    if isinstance(value, float) and len(str(value).partition(".")[2]) > 6:
        raise ContractError("invalid_input")
    return float(value)


def _validate_fence(value: Any) -> dict[str, Any]:
    fence = _exact(value, FENCE_KEYS)
    for key in ("config_revision", "engine_owner", "queue_revision", "shard_id"):
        _safe_id(fence[key])
    _uint(fence["claim_fence"])
    _uint(fence["routing_epoch"])
    return fence


def _validate_placement(value: Any, root: dict[str, Any]) -> dict[str, Any]:
    placement = _exact(value, PLACEMENT_KEYS)
    _uint(placement["ordinal"], maximum=MAX_ITEMS - 1)
    if placement["lane"] not in LANES or placement["priority"] not in PRIORITY_CREDIT:
        raise ContractError("invalid_input")
    if placement["work_class"] not in {"monitor", "detail"}:
        raise ContractError("invalid_input")
    if placement["fallback_target"] not in {"none", *LANES}:
        raise ContractError("invalid_input")
    if _uint(placement["due_at"]) > root["now"] or _uint(placement["eligible_since"]) > root["now"]:
        raise ContractError("invalid_input")
    assignment = _exact(placement["assignment"], ASSIGNMENT_KEYS)
    immutable = _exact(assignment["immutable_copy"], ASSIGNMENT_COPY_KEYS)
    for candidate in (assignment, immutable):
        if candidate["backend"] not in LANES or candidate["service_lane"] not in LANES:
            raise ContractError("invalid_input")
        _safe_id(candidate["capability_class"])
        _safe_id(candidate["routing_revision"])
    _validate_fence(placement["fence"])
    admission = _exact(placement["admission"], ADMISSION_KEYS)
    if admission["verdict"] not in {"permit", "defer", "deny", "violation"}:
        raise ContractError("invalid_input")
    _safe_id(admission["policy_revision"])
    return placement


def _validate_lane(value: Any, expected_name: str) -> dict[str, Any]:
    lane = _exact(value, LANE_KEYS)
    if lane["lane"] != expected_name:
        raise ContractError("invalid_input")
    _validate_fence(lane["queue_fence"])
    capacity = _exact(lane["capacity"], CAPACITY_KEYS)
    for key in (
        "admitted",
        "current",
        "desired",
        "hard_max",
        "inflight",
        "running",
        "scale_down_step",
        "scale_up_step",
        "warm_floor",
    ):
        _uint(capacity[key], maximum=MAX_CONCURRENCY)
    for key in ("drain_started_at", "last_scale_at"):
        _uint(capacity[key])
    if (
        not isinstance(capacity["draining"], bool)
        or min(capacity["scale_down_step"], capacity["scale_up_step"]) < 1
    ):
        raise ContractError("invalid_input")
    declared = _exact(lane["declared"], DECLARED_KEYS)
    for key in ("assignment_count", "eligible_ready_count", "inflight_count", "ready_count"):
        _uint(declared[key], maximum=MAX_ITEMS)
    _uint(declared["oldest_eligible_age"])
    if lane["service_state"] not in {"admitted", "unready", "error", "unsupported", "full"}:
        raise ContractError("invalid_input")
    telemetry = _exact(lane["telemetry"], TELEMETRY_KEYS)
    _uint(telemetry["observed_at"])
    _uint(telemetry["queue_oldest_age"])
    _ratio(telemetry["utilization_p95_ratio"])
    _ratio(telemetry["headroom_p05_ratio"])
    _ratio(telemetry["error_budget_burn"], maximum=float(MAX_INTEGER))
    if not isinstance(telemetry["resource_saturated"], bool):
        raise ContractError("invalid_input")
    if lane["zero_proof"] is not None:
        proof = _exact(lane["zero_proof"], PROOF_KEYS)
        for key in (
            "capability_census_revision",
            "config_revision",
            "policy_revision",
            "queue_revision",
            "routing_revision",
        ):
            _safe_id(proof[key])
        for key in (
            "assignment_count",
            "completed_at",
            "eligible_ready_count",
            "inflight_count",
            "ready_count",
            "started_at",
        ):
            _uint(proof[key], maximum=MAX_ITEMS if key.endswith("count") else MAX_INTEGER)
        if not isinstance(proof["complete"], bool):
            raise ContractError("invalid_input")
        if proof["oldest_eligible_since"] is not None:
            _uint(proof["oldest_eligible_since"])
        _validate_fence(proof["queue_fence"])
    return lane


def _validate_event(value: Any, root: dict[str, Any], index: int) -> dict[str, Any]:
    event = _exact(value, EVENT_KEYS)
    if _uint(event["event_ordinal"], maximum=MAX_ITEMS - 1) != index:
        raise ContractError("invalid_input")
    _uint(event["work_ordinal"], maximum=MAX_ITEMS - 1)
    if _uint(event["event_at"]) > root["now"]:
        raise ContractError("invalid_input")
    if event["kind"] not in {"assignment_created", "became_eligible"} or event["lane"] not in LANES:
        raise ContractError("invalid_input")
    for key in (
        "capability_census_revision",
        "config_revision",
        "policy_revision",
        "queue_revision",
        "routing_revision",
    ):
        _safe_id(event[key])
    return event


def _validate_input(value: Any) -> dict[str, Any]:
    _validate_json_shape(value)
    root = _exact(value, ROOT_KEYS)
    _uint(root["now"])
    for key in (
        "capability_census_revision",
        "config_revision",
        "policy_revision",
        "queue_revision",
        "routing_revision",
    ):
        _safe_id(root[key])
    _uint(root["declared_assignment_count"], maximum=MAX_ITEMS)
    placements = _exact(root["placements"], PLACEMENTS_KEYS)
    if any(not isinstance(placements[key], list) for key in PLACEMENTS_KEYS):
        raise ContractError("invalid_input")
    if len(placements["ready"]) + len(placements["inflight"]) > MAX_ITEMS:
        raise ContractError("invalid_input")
    for collection in ("ready", "inflight"):
        previous = -1
        for value in placements[collection]:
            placement = _validate_placement(value, root)
            if placement["ordinal"] < previous:
                raise ContractError("invalid_input")
            previous = placement["ordinal"]
    if not isinstance(root["lanes"], list) or len(root["lanes"]) != len(LANES):
        raise ContractError("invalid_input")
    root["lanes"] = [
        _validate_lane(lane, expected) for lane, expected in zip(root["lanes"], LANES, strict=True)
    ]
    if (
        not isinstance(root["invalidation_events"], list)
        or len(root["invalidation_events"]) > MAX_ITEMS
    ):
        raise ContractError("invalid_input")
    for index, event in enumerate(root["invalidation_events"]):
        _validate_event(event, root, index)
    return root


def _fence_identity(fence: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(fence[key] for key in sorted(FENCE_KEYS))


def _implicated_lanes(placement: dict[str, Any]) -> set[str]:
    assignment = placement["assignment"]
    immutable = assignment["immutable_copy"]
    lanes = {placement["lane"], assignment["backend"], assignment["service_lane"]}
    lanes.update({immutable["backend"], immutable["service_lane"]})
    if placement["fallback_target"] != "none":
        lanes.add(placement["fallback_target"])
    return lanes


def _placement_failures(
    placement: dict[str, Any], root: dict[str, Any], lane: dict[str, Any]
) -> set[str]:
    assignment = placement["assignment"]
    immutable = assignment["immutable_copy"]
    failures: set[str] = set()
    if (
        assignment["backend"] != assignment["service_lane"]
        or immutable["backend"] != immutable["service_lane"]
    ):
        failures.add("assignment_invalid")
    if (
        assignment["capability_class"] != "browser-default"
        or immutable["capability_class"] != "browser-default"
    ):
        failures.add("assignment_invalid")
    if any(assignment[key] != immutable[key] for key in ASSIGNMENT_COPY_KEYS):
        failures.add("assignment_mutated")
    if any(
        candidate != placement["lane"]
        for candidate in (
            assignment["backend"],
            assignment["service_lane"],
            immutable["backend"],
            immutable["service_lane"],
        )
    ):
        failures.add("assignment_lane_mismatch")
    if (
        assignment["routing_revision"] != root["routing_revision"]
        or immutable["routing_revision"] != root["routing_revision"]
    ):
        failures.add("revision_mismatch")
    if placement["fence"] != lane["queue_fence"] or any(
        placement["fence"][key] != root[key] for key in ("queue_revision", "config_revision")
    ):
        failures.add("queue_fence_invalid")
    admission = placement["admission"]
    if (
        admission["policy_revision"] != root["policy_revision"]
        or admission["verdict"] == "violation"
    ):
        failures.add("policy_violation")
    if placement["fallback_target"] != "none":
        failures.add("fallback_attempted")
    return failures


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
        and capacity["desired"] <= maximum
    ):
        return None
    return min(max(current, inflight, floor), maximum)


def _choose(placements: list[dict[str, Any]], now: int) -> dict[str, Any]:
    overrides = [item for item in placements if now - item["eligible_since"] >= AGE_OVERRIDE]
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
        placements,
        key=lambda item: (
            -(now - item["eligible_since"] + PRIORITY_CREDIT[item["priority"]]),
            item["eligible_since"],
            PRIORITY_ORDER[item["priority"]],
            item["ordinal"],
        ),
    )


def _global_audit(
    root: dict[str, Any],
) -> tuple[dict[str, set[str]], dict[str, list[dict[str, Any]]], dict[str, dict[str, int | None]]]:
    """Audit conservation and attribution completely before any decision is made."""
    lanes = {lane["lane"]: lane for lane in root["lanes"]}
    freeze = {name: set() for name in LANES}
    eligible = {name: [] for name in LANES}
    ready = root["placements"]["ready"]
    inflight = root["placements"]["inflight"]
    combined = ready + inflight

    ordinals = [placement["ordinal"] for placement in combined]
    counts = Counter(ordinals)
    expected_ordinals = set(range(root["declared_assignment_count"]))
    if len(combined) != root["declared_assignment_count"] or set(ordinals) != expected_ordinals:
        for name in LANES:
            freeze[name].add("conservation_failure")
    for ordinal, count in counts.items():
        if count > 1:
            affected = {
                placement["lane"] for placement in combined if placement["ordinal"] == ordinal
            }
            for name in affected:
                freeze[name].add("conservation_failure")
    ready_ordinals = {placement["ordinal"] for placement in ready}
    inflight_ordinals = {placement["ordinal"] for placement in inflight}
    for ordinal in ready_ordinals & inflight_ordinals:
        for placement in combined:
            if placement["ordinal"] == ordinal:
                freeze[placement["lane"]].add("conservation_failure")

    fence_owners: dict[tuple[Any, ...], set[str]] = {}
    for name, lane in lanes.items():
        identity = _fence_identity(lane["queue_fence"])
        fence_owners.setdefault(identity, set()).add(name)
        if any(
            lane["queue_fence"][key] != root[key] for key in ("queue_revision", "config_revision")
        ):
            freeze[name].add("queue_fence_invalid")
    for owners in fence_owners.values():
        if len(owners) > 1:
            for name in owners:
                freeze[name].add("conservation_failure")

    for placement in combined:
        source = placement["lane"]
        failures = _placement_failures(placement, root, lanes[source])
        implicated = _implicated_lanes(placement)
        for failure in failures:
            for name in implicated:
                freeze[name].add(failure)
        placement_fence_owners = fence_owners.get(_fence_identity(placement["fence"]), set())
        if placement_fence_owners and placement_fence_owners != {source}:
            for name in placement_fence_owners | {source}:
                freeze[name].update({"conservation_failure", "queue_fence_invalid"})
    facts: dict[str, dict[str, int | None]] = {}
    assignment_total = 0
    for name, lane in lanes.items():
        lane_ready = [placement for placement in ready if placement["lane"] == name]
        lane_inflight = [placement for placement in inflight if placement["lane"] == name]
        lane_eligible = [
            placement
            for placement in lane_ready
            if placement["due_at"] <= root["now"] and placement["admission"]["verdict"] == "permit"
        ]
        eligible[name] = lane_eligible
        oldest_since = min(
            (placement["eligible_since"] for placement in lane_eligible), default=None
        )
        facts[name] = {
            "assignment_count": len(lane_ready) + len(lane_inflight),
            "eligible_ready_count": len(lane_eligible),
            "inflight_count": len(lane_inflight),
            "oldest_eligible_age": 0 if oldest_since is None else root["now"] - oldest_since,
            "oldest_eligible_since": oldest_since,
            "ready_count": len(lane_ready),
        }
        assignment_total += len(lane_ready) + len(lane_inflight)
        expected = {key: facts[name][key] for key in DECLARED_KEYS}
        if lane["declared"] != expected or lane["capacity"]["inflight"] != len(lane_inflight):
            freeze[name].add("conservation_failure")

    if assignment_total != root["declared_assignment_count"]:
        for name in LANES:
            freeze[name].add("conservation_failure")
    return freeze, eligible, facts


def _proof_reason(
    root: dict[str, Any], lane: dict[str, Any], facts: dict[str, int | None]
) -> str | None:
    proof = lane["zero_proof"]
    if proof is None:
        return "zero_proof_absent"
    if (
        any(
            proof[key] != root[key]
            for key in (
                "capability_census_revision",
                "config_revision",
                "policy_revision",
                "queue_revision",
                "routing_revision",
            )
        )
        or proof["queue_fence"] != lane["queue_fence"]
    ):
        return "zero_proof_revision_mismatch"
    if (
        not proof["complete"]
        or proof["completed_at"] < proof["started_at"]
        or proof["completed_at"] > root["now"]
        or proof["completed_at"] - proof["started_at"] < ZERO_PROOF_MIN_WINDOW
    ):
        return "zero_proof_invalid"
    if root["now"] - proof["completed_at"] > ZERO_PROOF_MAX_AGE:
        return "zero_proof_stale"
    for key in ("assignment_count", "eligible_ready_count", "inflight_count", "ready_count"):
        if proof[key] != facts[key]:
            return "zero_proof_invalid"
    if proof["oldest_eligible_since"] != facts["oldest_eligible_since"]:
        return "zero_proof_invalid"
    if (
        any(
            proof[key] != 0
            for key in ("assignment_count", "eligible_ready_count", "inflight_count", "ready_count")
        )
        or proof["oldest_eligible_since"] is not None
    ):
        return "zero_proof_demand_present"
    current_event_revisions = {
        "capability_census_revision": root["capability_census_revision"],
        "config_revision": root["config_revision"],
        "policy_revision": root["policy_revision"],
        "queue_revision": root["queue_revision"],
        "routing_revision": root["routing_revision"],
    }
    if any(
        event["lane"] == lane["lane"]
        and event["event_at"] >= proof["completed_at"]
        and all(event[key] == expected for key, expected in current_event_revisions.items())
        for event in root["invalidation_events"]
    ):
        return "zero_proof_invalid"
    return None


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


def _evaluate_lane(
    root: dict[str, Any],
    lane: dict[str, Any],
    freeze: set[str],
    eligible: list[dict[str, Any]],
    facts: dict[str, int | None],
) -> dict[str, Any]:
    name = lane["lane"]
    capacity = lane["capacity"]
    base = _capacity_base(capacity)
    if base is None:
        freeze.add("invalid_input")
        base = max(capacity["inflight"], capacity["warm_floor"])
    service_reason = {
        "unready": "service_unready",
        "error": "service_error",
        "unsupported": "service_unsupported",
        "full": "service_full",
    }.get(lane["service_state"])
    if service_reason:
        freeze.add(service_reason)
    telemetry = lane["telemetry"]
    if (
        telemetry["observed_at"] > root["now"]
        or root["now"] - telemetry["observed_at"] > TELEMETRY_MAX_AGE
    ):
        freeze.add("telemetry_stale")
    if telemetry["error_budget_burn"] > 1.0:
        freeze.add("error_budget_exhausted")
    if telemetry["resource_saturated"]:
        freeze.add("resource_saturation")
    if freeze:
        return _base_lane_result(name, base, "freeze", freeze & FREEZE_REASONS, None)

    defer: set[str] = set()
    if telemetry["utilization_p95_ratio"] > 0.85 or telemetry["headroom_p05_ratio"] < 0.15:
        defer.add("capacity_headroom_unsafe")
    draining = (
        capacity["draining"]
        and capacity["drain_started_at"] > 0
        and root["now"] - capacity["drain_started_at"] < DRAIN_WINDOW
    )
    cooldown = (
        capacity["last_scale_at"] != 0 and root["now"] - capacity["last_scale_at"] < SCALE_COOLDOWN
    )
    if eligible:
        if capacity["admitted"] > capacity["inflight"] and not defer:
            return _base_lane_result(
                name, base, "claim", (), _choose(eligible, root["now"])["ordinal"]
            )
        if draining:
            defer.add("drain_active")
        if cooldown:
            defer.add("scale_cooldown_active")
        if base >= capacity["hard_max"]:
            defer.add("hard_max_reached")
        if not defer:
            defer.add("scale_up_requested")
            base = min(capacity["hard_max"], base + capacity["scale_up_step"])
        return _base_lane_result(name, base, "defer", defer, None)

    defer.add("no_eligible_backlog")
    proof_reason = _proof_reason(root, lane, facts)
    if proof_reason is not None:
        defer.add(proof_reason)
    if draining:
        defer.add("drain_active")
    if cooldown:
        defer.add("scale_cooldown_active")
    if (
        proof_reason is None
        and not draining
        and not cooldown
        and all(capacity[key] == 0 for key in ("inflight", "running", "admitted", "current"))
    ):
        base = 0
    return _base_lane_result(name, base, "defer", defer & DEFER_REASONS, None)


def evaluate(value: Any) -> dict[str, Any]:
    """Return the normalized result; malformed input never leaks detail."""
    try:
        root = _validate_input(value)
        freeze, eligible, facts = _global_audit(root)
        lanes = {lane["lane"]: lane for lane in root["lanes"]}
        return {
            "lanes": {
                name: _evaluate_lane(root, lanes[name], freeze[name], eligible[name], facts[name])
                for name in LANES
            }
        }
    except (ContractError, KeyError, TypeError, ValueError):
        return {"error": "invalid_input"}


def evaluate_document(raw: bytes) -> bytes:
    try:
        return canonical_bytes(evaluate(parse_document(raw)))
    except ContractError:
        return canonical_bytes({"error": "invalid_input"})
