"""Deterministic, advisory-only crawler migration gate evaluation."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

POLICY_SCHEMA = "jobseek.crawler-migration-promotion-policy/v1"
EVIDENCE_SCHEMA = "jobseek.crawler-migration-promotion-evidence/v1"
DECISION_SCHEMA = "jobseek.crawler-migration-promotion-decision/v1"
RELEASE_PATTERN = r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$"
CLASS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SCHEMA_METADATA_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MAX_SCHEMA_LOCATION_LENGTH = 128
MAX_SCHEMA_PATH_COMPONENTS = 8
MAX_SAFE_NUMERIC_VALUE = (1 << 53) - 1

ZERO_TOLERANCE_REASONS = {
    "stale_authoritative_writes": "freeze:stale-authoritative-write",
    "bulk_gone_or_delist_events": "freeze:bulk-gone-or-delist",
    "tdm_violations": "freeze:tdm-violation",
    "queue_loss_or_duplication_events": "freeze:queue-loss-or-duplication",
    "origin_policy_violations": "freeze:origin-policy-violation",
    "cross_backend_runtime_fallbacks": "freeze:cross-backend-runtime-fallback",
}

WORK_CLASSES = ("monitor", "detail")
BROWSER_BACKENDS = ("lightpanda", "chromium")
BROWSER_CAPABILITY_CLASSES = (
    "navigation-evaluation",
    "interaction-capture",
    "identity-transport",
)
EXPECTED_METRIC_CONTRACT = {
    "gate_histograms": {
        "browser_operation": "crawler_migration_browser_operation_seconds",
        "due_to_claim": "crawler_migration_due_to_claim_seconds",
        "due_to_complete": "crawler_migration_due_to_complete_seconds",
    },
    "gate_labels": [
        "implementation",
        "region",
        "cohort",
        "work_class",
        "capability_class",
        "browser_class",
        "browser_backend",
        "service_lane",
        "provider_family",
    ],
    "release_identity": "crawler_build_info",
    "service_resource_authority": "isolated-service-cgroup-v2",
    "service_resource_labels": ["browser_backend", "service_lane"],
    "service_resources": {
        "browser_seconds_total": "crawler_browser_service_browser_seconds_total",
        "concurrency_limit": "crawler_browser_service_concurrency_limit",
        "cpu_seconds_total": "crawler_browser_service_cpu_seconds_total",
        "crashes_total": "crawler_browser_service_crashes_total",
        "recycles_total": "crawler_browser_service_recycles_total",
        "resident_memory_bytes": "crawler_browser_service_resident_memory_bytes",
        "resource_limit_outcomes_total": ("crawler_browser_service_resource_limit_outcomes_total"),
        "sessions": "crawler_browser_service_sessions",
    },
}


class GateModelError(ValueError):
    """Raised when policy or evidence is ambiguous or violates the gate contract."""


def _resource_json(*parts: str) -> dict[str, Any]:
    resource = resources.files(__package__).joinpath("resources", *parts)
    try:
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateModelError(f"cannot load packaged resource {'/'.join(parts)}") from exc
    if not isinstance(value, dict):
        raise GateModelError(f"packaged resource {'/'.join(parts)} must contain one JSON object")
    return value


def load_candidate_policy() -> dict[str, Any]:
    """Load the immutable candidate policy shipped with the crawler package."""

    return _resource_json("promotion-policy-v1.json")


def _validate_schema(value: dict[str, Any], schema_name: str, field: str) -> None:
    schema = _resource_json("schemas", schema_name)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        path = list(first.absolute_path)
        safe_parts = [
            (
                item
                if isinstance(item, str) and SCHEMA_METADATA_PATTERN.fullmatch(item)
                else "[]"
                if isinstance(item, int)
                else "<field>"
            )
            for item in path[:MAX_SCHEMA_PATH_COMPONENTS]
        ]
        if len(path) > MAX_SCHEMA_PATH_COMPONENTS:
            safe_parts.append("...")
        location = ".".join(safe_parts) or "<root>"
        if len(location) > MAX_SCHEMA_LOCATION_LENGTH:
            location = f"{location[: MAX_SCHEMA_LOCATION_LENGTH - 3]}..."
        validator_name = (
            first.validator
            if isinstance(first.validator, str)
            and SCHEMA_METADATA_PATTERN.fullmatch(first.validator)
            else "schema"
        )
        raise GateModelError(
            f"{field} violates {schema_name} at {location} (validator={validator_name})"
        )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GateModelError(message)


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GateModelError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise GateModelError(f"{field} must be an array")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise GateModelError(f"{field} must be a non-empty string")
    return value


def _identifier(value: object, field: str) -> str:
    result = _string(value, field)
    if not IDENTIFIER_PATTERN.fullmatch(result):
        raise GateModelError(f"{field} must be a safe identifier of at most 64 characters")
    return result


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise GateModelError(f"{field} must be an integer >= {minimum}")
    if value > MAX_SAFE_NUMERIC_VALUE:
        raise GateModelError(f"{field} exceeds the maximum safe numeric value")
    return value


def _number(value: object, field: str, *, minimum: float = 0.0) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise GateModelError(f"{field} must be numeric")
    try:
        result = float(value)
    except OverflowError as exc:
        raise GateModelError(f"{field} exceeds the maximum safe numeric value") from exc
    if not math.isfinite(result) or result < minimum:
        raise GateModelError(f"{field} must be finite and >= {minimum}")
    if result > MAX_SAFE_NUMERIC_VALUE:
        raise GateModelError(f"{field} exceeds the maximum safe numeric value")
    return result


def _timestamp(value: object, field: str) -> datetime:
    raw = _string(value, field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateModelError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise GateModelError(f"{field} must include a timezone")
    return parsed


def _unique_strings(value: object, field: str) -> set[str]:
    raw = _list(value, field)
    values = {_string(item, f"{field} item") for item in raw}
    _require(bool(values), f"{field} must not be empty")
    _require(len(values) == len(raw), f"{field} must not contain duplicates")
    return values


def _strictly_increasing_numbers(value: object, field: str) -> tuple[float, ...]:
    raw = _list(value, field)
    numbers = tuple(_number(item, f"{field} item", minimum=0.0) for item in raw)
    _require(len(numbers) >= 2, f"{field} must contain at least two buckets")
    _require(all(item > 0 for item in numbers), f"{field} buckets must be > 0")
    _require(
        all(current > previous for previous, current in zip(numbers, numbers[1:], strict=False)),
        f"{field} buckets must be strictly increasing",
    )
    return numbers


def _expected_required_classes() -> dict[str, dict[str, str]]:
    classes: dict[str, dict[str, str]] = {}
    for work_class in WORK_CLASSES:
        classes[f"{work_class}_http"] = {
            "work_class": work_class,
            "capability_class": "shared-http",
            "browser_class": "none",
            "browser_backend": "none",
            "service_lane": "none",
            "resource_authority": "worker-cgroup-v2",
            "sample_policy": "standard",
        }
        for backend in BROWSER_BACKENDS:
            for capability_class in BROWSER_CAPABILITY_CLASSES:
                class_id = f"{work_class}_{backend}_{capability_class.replace('-', '_')}"
                classes[class_id] = {
                    "work_class": work_class,
                    "capability_class": capability_class,
                    "browser_class": "service",
                    "browser_backend": backend,
                    "service_lane": backend,
                    "resource_authority": f"{backend}-service-cgroup-v2",
                    "sample_policy": "rare",
                }
    return classes


def _policy_contract(policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(policy.get("schema_version") == POLICY_SCHEMA, "policy schema_version is invalid")
    _require(policy.get("status") == "candidate", "policy must remain candidate")
    _require(policy.get("scope") == "crawler-runtime-only", "policy scope is invalid")
    _require(
        policy.get("browser_operation_mode") == "capability-routed-dual-engine",
        "policy browser operation mode is invalid",
    )
    _require(
        policy.get("browser_retirement_gate")
        == "7966-zero-chromium-assignments-and-service-removal",
        "policy browser retirement gate is invalid",
    )
    _require(
        policy.get("class_membership_authority") == "reviewed-immutable-policy-revision",
        "policy class membership authority is invalid",
    )
    _require(
        policy.get("required_class_mode") == "full-eligible-cross-product",
        "policy required class mode is invalid",
    )
    _identifier(policy.get("policy_id"), "policy.policy_id")
    _require(policy.get("release_pattern") == RELEASE_PATTERN, "policy release pattern is invalid")
    _require(
        policy.get("routing_revision_pattern") == RELEASE_PATTERN,
        "policy routing revision pattern is invalid",
    )
    _require(
        _integer(
            policy.get("release_max_active_values"), "policy.release_max_active_values", minimum=1
        )
        == 2,
        "policy release_max_active_values must be 2",
    )

    allowlists = _object(policy.get("label_allowlists"), "policy.label_allowlists")
    expected_dimensions = {
        "implementation",
        "region",
        "cohort",
        "work_class",
        "capability_class",
        "browser_class",
        "browser_backend",
        "service_lane",
        "provider_family",
    }
    _require(set(allowlists) == expected_dimensions, "policy label dimensions are invalid")
    normalized_allowlists = {
        name: _unique_strings(allowlists[name], f"policy.label_allowlists.{name}")
        for name in sorted(expected_dimensions)
    }
    _require(
        _object(policy.get("metric_contract"), "policy.metric_contract")
        == EXPECTED_METRIC_CONTRACT,
        "policy metric contract is invalid",
    )

    raw_classes = _list(policy.get("required_classes"), "policy.required_classes")
    _require(bool(raw_classes), "policy.required_classes must not be empty")
    classes: dict[str, dict[str, Any]] = {}
    for index, raw_class in enumerate(raw_classes):
        item = _object(raw_class, f"policy.required_classes[{index}]")
        class_id = _string(item.get("class_id"), f"policy.required_classes[{index}].class_id")
        _require(bool(CLASS_PATTERN.fullmatch(class_id)), f"policy class_id {class_id} is invalid")
        _require(class_id not in classes, f"policy class_id {class_id} is duplicated")
        work_class = _string(item.get("work_class"), f"policy.required_classes[{index}].work_class")
        _require(
            work_class in normalized_allowlists["work_class"],
            f"policy class {class_id} work_class is not allowed",
        )
        capability_class = _string(
            item.get("capability_class"),
            f"policy.required_classes[{index}].capability_class",
        )
        _require(
            capability_class in normalized_allowlists["capability_class"],
            f"policy class {class_id} capability_class is not allowed",
        )
        browser_class = _string(
            item.get("browser_class"), f"policy.required_classes[{index}].browser_class"
        )
        browser_backend = _string(
            item.get("browser_backend"), f"policy.required_classes[{index}].browser_backend"
        )
        service_lane = _string(
            item.get("service_lane"), f"policy.required_classes[{index}].service_lane"
        )
        resource_authority = _string(
            item.get("resource_authority"),
            f"policy.required_classes[{index}].resource_authority",
        )
        _require(
            browser_class in normalized_allowlists["browser_class"],
            f"policy class {class_id} browser_class is not allowed",
        )
        _require(
            browser_backend in normalized_allowlists["browser_backend"],
            f"policy class {class_id} browser_backend is not allowed",
        )
        _require(
            service_lane in normalized_allowlists["service_lane"],
            f"policy class {class_id} service_lane is not allowed",
        )
        if browser_backend == "none":
            _require(
                browser_class == "none"
                and capability_class == "shared-http"
                and service_lane == "none"
                and resource_authority == "worker-cgroup-v2",
                f"policy class {class_id} non-browser dimensions are inconsistent",
            )
        else:
            _require(
                browser_class == "service"
                and capability_class in BROWSER_CAPABILITY_CLASSES
                and service_lane == browser_backend
                and resource_authority == f"{browser_backend}-service-cgroup-v2",
                f"policy class {class_id} browser dimensions are inconsistent",
            )
        sample_policy = _string(
            item.get("sample_policy"), f"policy.required_classes[{index}].sample_policy"
        )
        _require(
            sample_policy in {"standard", "rare"},
            f"policy class {class_id} sample_policy is invalid",
        )
        classes[class_id] = {
            "work_class": work_class,
            "capability_class": capability_class,
            "browser_class": browser_class,
            "browser_backend": browser_backend,
            "service_lane": service_lane,
            "resource_authority": resource_authority,
            "sample_policy": sample_policy,
        }

    _require(
        classes == _expected_required_classes(),
        "policy required classes must equal the full eligible work/backend/capability matrix",
    )

    thresholds = _object(policy.get("thresholds"), "policy.thresholds")
    integer_thresholds = {
        "min_observation_seconds": 300,
        "min_completed_schedule_cycles": 1,
        "min_standard_class_samples": 1,
        "min_rare_class_samples": 1,
    }
    for name, minimum in integer_thresholds.items():
        _integer(thresholds.get(name), f"policy.thresholds.{name}", minimum=minimum)
    numeric_thresholds = (
        "schedule_compliance_ratio_min",
        "freshness_error_budget_burn_max",
        "due_to_claim_p95_max_seconds",
        "due_to_claim_p99_max_seconds",
        "due_to_complete_p95_max_seconds",
        "due_to_complete_p99_max_seconds",
        "request_amplification_ratio_max",
        "antibot_regression_ratio_max",
        "max_avoidable_idle_seconds_with_eligible_backlog",
        "max_backend_utilization_ratio",
        "min_backend_headroom_ratio",
    )
    for name in numeric_thresholds:
        _number(thresholds.get(name), f"policy.thresholds.{name}")
    _require(
        0 <= float(thresholds["schedule_compliance_ratio_min"]) <= 1,
        "policy schedule compliance ratio must be between 0 and 1",
    )
    _require(
        float(thresholds["due_to_claim_p95_max_seconds"])
        <= float(thresholds["due_to_claim_p99_max_seconds"]),
        "policy due-to-claim p95 must not exceed p99",
    )
    _require(
        float(thresholds["due_to_complete_p95_max_seconds"])
        <= float(thresholds["due_to_complete_p99_max_seconds"]),
        "policy due-to-complete p95 must not exceed p99",
    )
    _require(
        float(thresholds["request_amplification_ratio_max"]) >= 1,
        "policy request amplification ratio must be >= 1",
    )
    _require(
        float(thresholds["antibot_regression_ratio_max"]) >= 1,
        "policy anti-bot regression ratio must be >= 1",
    )
    _require(
        thresholds["max_avoidable_idle_seconds_with_eligible_backlog"] == 0,
        "policy avoidable idle threshold must be zero",
    )
    _require(
        0 < float(thresholds["max_backend_utilization_ratio"]) < 1,
        "policy backend utilization ratio must be between 0 and 1",
    )
    _require(
        0 < float(thresholds["min_backend_headroom_ratio"]) < 1,
        "policy backend headroom ratio must be between 0 and 1",
    )
    _require(
        float(thresholds["max_backend_utilization_ratio"])
        + float(thresholds["min_backend_headroom_ratio"])
        <= 1,
        "policy backend utilization and headroom bounds overlap",
    )

    buckets = _object(policy.get("histogram_buckets_seconds"), "policy.histogram_buckets_seconds")
    _require(
        set(buckets) == {"due_to_claim", "due_to_complete", "browser_operation"},
        "policy histogram families are invalid",
    )
    for name in sorted(buckets):
        _strictly_increasing_numbers(buckets[name], f"policy.histogram_buckets_seconds.{name}")

    zero_tolerance = _list(policy.get("zero_tolerance_signals"), "policy.zero_tolerance_signals")
    _require(
        zero_tolerance == sorted(ZERO_TOLERANCE_REASONS),
        "policy zero_tolerance_signals must equal the closed signal vocabulary",
    )
    eligible_implementations = _unique_strings(
        policy.get("eligible_candidate_implementations"),
        "policy.eligible_candidate_implementations",
    )
    _require(
        eligible_implementations == {"go"},
        "policy eligible_candidate_implementations must be exactly go",
    )
    return {"allowlists": normalized_allowlists, "classes": classes}, thresholds


def _add_reason(reasons: dict[str, set[str]], code: str, class_id: str | None = None) -> None:
    if class_id is not None:
        reasons[code].add(class_id)
    else:
        reasons[code]


def evaluate_promotion(policy: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Evaluate sanitized aggregate evidence without mutating runtime state."""

    _validate_schema(_object(policy, "policy"), "promotion-policy-v1.schema.json", "policy")
    _validate_schema(_object(evidence, "evidence"), "promotion-evidence-v1.schema.json", "evidence")
    policy_contract, thresholds = _policy_contract(_object(policy, "policy"))
    _require(
        evidence.get("schema_version") == EVIDENCE_SCHEMA,
        "evidence schema_version is invalid",
    )
    evidence_id = _identifier(evidence.get("evidence_id"), "evidence.evidence_id")
    policy_id = _identifier(policy.get("policy_id"), "policy.policy_id")
    _require(evidence.get("policy_id") == policy_id, "evidence policy_id does not match policy")
    routing_revision = _string(evidence.get("routing_revision"), "evidence.routing_revision")
    _require(
        bool(re.fullmatch(RELEASE_PATTERN, routing_revision)),
        "evidence routing_revision is invalid",
    )

    candidate = _object(evidence.get("candidate"), "evidence.candidate")
    expected_candidate_fields = {"implementation", "release", "region", "cohort"}
    _require(set(candidate) == expected_candidate_fields, "evidence candidate fields are invalid")
    for name in ("implementation", "region", "cohort"):
        value = _string(candidate.get(name), f"evidence.candidate.{name}")
        _require(
            value in policy_contract["allowlists"][name],
            f"evidence candidate {name} is not allowed",
        )
    _require(candidate["cohort"] == "candidate", "evidence candidate cohort must be candidate")
    _require(
        candidate["implementation"] in policy["eligible_candidate_implementations"],
        "evidence candidate implementation is not eligible for promotion",
    )
    release = _string(candidate.get("release"), "evidence.candidate.release")
    _require(bool(re.fullmatch(RELEASE_PATTERN, release)), "evidence candidate release is invalid")

    window = _object(evidence.get("window"), "evidence.window")
    start_at = _timestamp(window.get("start_at"), "evidence.window.start_at")
    end_at = _timestamp(window.get("end_at"), "evidence.window.end_at")
    duration_seconds = _integer(window.get("duration_seconds"), "evidence.window.duration_seconds")
    actual_duration = (end_at - start_at).total_seconds()
    _require(actual_duration > 0, "evidence window end must be after start")
    _require(
        actual_duration == duration_seconds,
        "evidence window duration_seconds does not match its boundaries",
    )

    reasons: dict[str, set[str]] = defaultdict(set)
    if duration_seconds < thresholds["min_observation_seconds"]:
        _add_reason(reasons, "hold:window-duration")

    freeze_signals = _object(evidence.get("freeze_signals"), "evidence.freeze_signals")
    _require(
        set(freeze_signals) == set(ZERO_TOLERANCE_REASONS),
        "evidence freeze signal vocabulary is invalid",
    )
    for signal, reason in ZERO_TOLERANCE_REASONS.items():
        if _integer(freeze_signals[signal], f"evidence.freeze_signals.{signal}") > 0:
            _add_reason(reasons, reason)

    observations_raw = _list(evidence.get("observations"), "evidence.observations")
    observations: dict[str, dict[str, Any]] = {}
    for index, raw_observation in enumerate(observations_raw):
        observation = _object(raw_observation, f"evidence.observations[{index}]")
        class_id = _string(observation.get("class_id"), f"evidence.observations[{index}].class_id")
        _require(class_id not in observations, f"evidence class_id {class_id} is duplicated")
        observations[class_id] = observation

    required_classes = policy_contract["classes"]
    missing = sorted(set(required_classes) - set(observations))
    extra = sorted(set(observations) - set(required_classes))
    _require(not missing, f"evidence is missing required classes: {', '.join(missing)}")
    _require(not extra, f"evidence contains unknown classes: {', '.join(extra)}")

    routed_assignment_count = 0
    for class_id, required_class in sorted(required_classes.items()):
        observation = observations[class_id]
        for name in (
            "work_class",
            "capability_class",
            "browser_class",
            "browser_backend",
            "service_lane",
            "provider_family",
        ):
            value = _string(observation.get(name), f"evidence {class_id}.{name}")
            _require(
                value in policy_contract["allowlists"][name],
                f"evidence {class_id} {name} is not allowed",
            )
        for name in (
            "work_class",
            "capability_class",
            "browser_class",
            "browser_backend",
            "service_lane",
        ):
            _require(
                observation[name] == required_class[name],
                f"evidence {class_id} {name} does not match policy",
            )
        resource_authority = _string(
            observation.get("resource_authority"),
            f"evidence {class_id}.resource_authority",
        )
        _require(
            resource_authority == required_class["resource_authority"],
            f"evidence {class_id} resource_authority does not match policy",
        )

        capacity = _object(observation.get("capacity"), f"evidence {class_id}.capacity")
        expected_capacity = {
            "eligible_demand_present",
            "routed_assignment_present",
            "zero_demand_proven",
            "zero_assignment_proven",
            "avoidable_idle_seconds_with_eligible_backlog",
            "utilization_p95_ratio",
            "headroom_p05_ratio",
        }
        _require(
            set(capacity) == expected_capacity,
            f"evidence {class_id} capacity vocabulary is invalid",
        )
        eligible_demand_present = capacity["eligible_demand_present"]
        routed_assignment_present = capacity["routed_assignment_present"]
        zero_demand_proven = capacity["zero_demand_proven"]
        zero_assignment_proven = capacity["zero_assignment_proven"]
        for name, value in (
            ("eligible_demand_present", eligible_demand_present),
            ("routed_assignment_present", routed_assignment_present),
            ("zero_demand_proven", zero_demand_proven),
            ("zero_assignment_proven", zero_assignment_proven),
        ):
            _require(
                isinstance(value, bool),
                f"evidence {class_id}.capacity.{name} is invalid",
            )
        _require(
            not (routed_assignment_present and not eligible_demand_present),
            f"evidence {class_id} cannot have assignment without eligible demand",
        )
        _require(
            not (eligible_demand_present and zero_demand_proven),
            f"evidence {class_id} cannot have demand and zero-demand proof",
        )
        _require(
            not (routed_assignment_present and zero_assignment_proven),
            f"evidence {class_id} cannot have assignment and zero-assignment proof",
        )
        if routed_assignment_present:
            routed_assignment_count += 1
        if not eligible_demand_present and not zero_demand_proven:
            _add_reason(reasons, "hold:zero-demand-unproven", class_id)
        if not routed_assignment_present and not zero_assignment_proven:
            _add_reason(reasons, "hold:zero-assignment-unproven", class_id)
        avoidable_idle = _number(
            capacity["avoidable_idle_seconds_with_eligible_backlog"],
            f"evidence {class_id}.capacity.avoidable_idle_seconds_with_eligible_backlog",
        )
        utilization = _number(
            capacity["utilization_p95_ratio"],
            f"evidence {class_id}.capacity.utilization_p95_ratio",
        )
        headroom = _number(
            capacity["headroom_p05_ratio"],
            f"evidence {class_id}.capacity.headroom_p05_ratio",
        )
        _require(utilization <= 1, f"evidence {class_id} utilization exceeds 1")
        _require(headroom <= 1, f"evidence {class_id} headroom exceeds 1")
        if routed_assignment_present:
            if avoidable_idle > thresholds["max_avoidable_idle_seconds_with_eligible_backlog"]:
                _add_reason(reasons, "hold:avoidable-idle-with-eligible-backlog", class_id)
            if (
                utilization > thresholds["max_backend_utilization_ratio"]
                or headroom < thresholds["min_backend_headroom_ratio"]
            ):
                _add_reason(reasons, "hold:backend-capacity-headroom", class_id)
        else:
            _require(
                avoidable_idle == 0 and utilization == 0 and headroom == 1,
                f"evidence {class_id} zero-assignment capacity values are inconsistent",
            )

        cycles = _integer(
            observation.get("completed_schedule_cycles"),
            f"evidence {class_id}.completed_schedule_cycles",
        )
        if routed_assignment_present and cycles < thresholds["min_completed_schedule_cycles"]:
            _add_reason(reasons, "hold:class-cycle-coverage", class_id)
        elif not routed_assignment_present:
            _require(
                cycles == 0,
                f"evidence {class_id} unassigned completed cycles must be zero",
            )

        sample_size = _integer(observation.get("sample_size"), f"evidence {class_id}.sample_size")
        population_size = observation.get("population_size")
        if required_class["sample_policy"] == "standard":
            _require(
                population_size is None,
                f"evidence {class_id} standard class population_size must be null",
            )
            _require(
                routed_assignment_present or sample_size == 0,
                f"evidence {class_id} unassigned standard class must have zero samples",
            )
            if routed_assignment_present and sample_size < thresholds["min_standard_class_samples"]:
                _add_reason(reasons, "hold:class-sample-coverage", class_id)
        else:
            if not routed_assignment_present:
                _require(
                    sample_size == 0 and population_size == 0,
                    f"evidence {class_id} unassigned rare class must have zero population",
                )
            elif population_size is None:
                _add_reason(reasons, "hold:rare-population-coverage", class_id)
            else:
                population = _integer(
                    population_size, f"evidence {class_id}.population_size", minimum=1
                )
                if sample_size < thresholds["min_rare_class_samples"] or sample_size != population:
                    _add_reason(reasons, "hold:rare-population-coverage", class_id)

        replay_complete = observation.get("replay_complete")
        _require(isinstance(replay_complete, bool), f"evidence {class_id}.replay_complete invalid")
        if routed_assignment_present and not replay_complete:
            _add_reason(reasons, "hold:replay-incomplete", class_id)
        elif not routed_assignment_present:
            _require(
                not replay_complete,
                f"evidence {class_id} unassigned replay_complete must be false",
            )

        mismatches = _object(observation.get("mismatches"), f"evidence {class_id}.mismatches")
        expected_mismatches = {"url_set", "field_hash", "result_flag", "projected_db_effect"}
        _require(
            set(mismatches) == expected_mismatches,
            f"evidence {class_id} mismatch vocabulary is invalid",
        )
        mismatch_present = any(
            _integer(value, f"evidence {class_id}.mismatches.{name}") > 0
            for name, value in mismatches.items()
        )
        if routed_assignment_present and mismatch_present:
            _add_reason(reasons, "hold:correctness-mismatch", class_id)
        elif not routed_assignment_present:
            _require(
                not mismatch_present,
                f"evidence {class_id} unassigned mismatch counts must be zero",
            )

        freshness = _object(observation.get("freshness"), f"evidence {class_id}.freshness")
        expected_freshness = {
            "schedule_compliance_ratio",
            "error_budget_burn",
            "due_to_claim_p95_seconds",
            "due_to_claim_p99_seconds",
            "due_to_complete_p95_seconds",
            "due_to_complete_p99_seconds",
        }
        _require(
            set(freshness) == expected_freshness,
            f"evidence {class_id} freshness vocabulary is invalid",
        )
        if routed_assignment_present:
            schedule_compliance = _number(
                freshness["schedule_compliance_ratio"],
                f"evidence {class_id}.freshness.schedule_compliance_ratio",
            )
            _require(schedule_compliance <= 1, f"evidence {class_id} schedule compliance exceeds 1")
            if schedule_compliance < thresholds["schedule_compliance_ratio_min"]:
                _add_reason(reasons, "hold:freshness-schedule-compliance", class_id)

            error_budget_burn = _number(
                freshness["error_budget_burn"],
                f"evidence {class_id}.freshness.error_budget_burn",
            )
            if error_budget_burn > thresholds["freshness_error_budget_burn_max"]:
                _add_reason(reasons, "freeze:freshness-error-budget", class_id)

            latency_pairs = (
                ("due_to_claim_p95_seconds", "due_to_claim_p95_max_seconds"),
                ("due_to_claim_p99_seconds", "due_to_claim_p99_max_seconds"),
                ("due_to_complete_p95_seconds", "due_to_complete_p95_max_seconds"),
                ("due_to_complete_p99_seconds", "due_to_complete_p99_max_seconds"),
            )
            latency_values = {
                observed: _number(
                    freshness[observed],
                    f"evidence {class_id}.freshness.{observed}",
                )
                for observed, _maximum in latency_pairs
            }
            _require(
                latency_values["due_to_claim_p95_seconds"]
                <= latency_values["due_to_claim_p99_seconds"],
                f"evidence {class_id} due-to-claim percentiles are inverted",
            )
            _require(
                latency_values["due_to_complete_p95_seconds"]
                <= latency_values["due_to_complete_p99_seconds"],
                f"evidence {class_id} due-to-complete percentiles are inverted",
            )
            if any(
                latency_values[observed] > thresholds[maximum]
                for observed, maximum in latency_pairs
            ):
                _add_reason(reasons, "hold:freshness-latency", class_id)

            request_amplification = _number(
                observation.get("request_amplification_ratio"),
                f"evidence {class_id}.request_amplification_ratio",
            )
            if request_amplification > thresholds["request_amplification_ratio_max"]:
                _add_reason(reasons, "freeze:request-amplification", class_id)

            antibot_regression = _number(
                observation.get("antibot_regression_ratio"),
                f"evidence {class_id}.antibot_regression_ratio",
            )
            if antibot_regression > thresholds["antibot_regression_ratio_max"]:
                _add_reason(reasons, "freeze:antibot-regression", class_id)
        else:
            _require(
                all(value is None for value in freshness.values())
                and observation.get("request_amplification_ratio") is None
                and observation.get("antibot_regression_ratio") is None,
                f"evidence {class_id} unassigned performance values must be null",
            )

        resource_saturation_events = _integer(
            observation.get("resource_saturation_events"),
            f"evidence {class_id}.resource_saturation_events",
        )
        if not routed_assignment_present:
            _require(
                resource_saturation_events == 0,
                f"evidence {class_id} unassigned resource saturation must be zero",
            )
        elif resource_saturation_events > 0:
            _add_reason(reasons, "freeze:backend-resource-saturation", class_id)

    _require(
        routed_assignment_count > 0,
        "evidence candidate cohort must contain at least one routed assignment",
    )

    reason_items = [
        {"code": code, "affected_classes": sorted(affected)}
        for code, affected in sorted(reasons.items())
    ]
    if any(item["code"].startswith("freeze:") for item in reason_items):
        decision = "freeze"
    elif reason_items:
        decision = "hold"
    else:
        decision = "promote"

    result = {
        "schema_version": DECISION_SCHEMA,
        "policy_id": policy_id,
        "policy_status": "candidate",
        "evidence_id": evidence_id,
        "routing_revision": routing_revision,
        "candidate": {name: candidate[name] for name in sorted(candidate)},
        "decision": decision,
        "reasons": reason_items,
    }
    _validate_schema(result, "promotion-decision-v1.schema.json", "decision")
    return result
