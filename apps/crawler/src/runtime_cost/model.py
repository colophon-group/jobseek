"""Deterministic crawler-only capacity and cost calculations.

The workload is independent of an implementation. A Python or Go measurement
maps the same lanes onto its runtime roles, concurrency topology, and complete
crawler cost ledger. Unknown in-scope costs are blockers, never implicit zero.
"""

from __future__ import annotations

import math
from typing import Any

from src.runtime_cost.process_tree import SAMPLER_STRICT_TIMING_LIMIT_SECONDS

WORKLOAD_SCHEMA = "jobseek.crawler-runtime-workload/v1"
MEASUREMENT_SCHEMA = "jobseek.crawler-runtime-measurement/v1"
PRICING_SCHEMA = "jobseek.crawler-runtime-pricing/v1"
PROJECTION_SCHEMA = "jobseek.crawler-runtime-projection/v1"
PROCESS_TREE_MIN_COVERAGE_RATIO = 0.95
PROCESS_TREE_MAX_SAMPLE_INTERVAL_SECONDS = 1.0
PROCESS_TREE_BOUNDARY_TOLERANCE_SECONDS = 60
EXACT_PROCESS_TREE_TARGET_IDS = frozenset(
    {
        "http-worker-1",
        "http-worker-2",
        "http-worker-3",
        "browser-worker-1",
        "exporter-1",
        "drain-1",
    }
)
_BROWSER_RETRY_FAMILIES = {
    "navigation-network": {
        "stage": "browser-navigation",
        "keys": {
            (reason, outcome)
            for reason in ("connection_reset", "network_changed", "socket_not_connected")
            for outcome in ("retry", "recovered", "exhausted")
        },
    },
    "content": {
        "stage": "browser-content",
        "keys": {(None, outcome) for outcome in ("retry", "recovered", "failed")},
    },
    "target-closed": {
        "stage": "detail",
        "keys": {(None, outcome) for outcome in ("retry", "recovered", "failed")},
    },
}

ALLOWED_COST_CATEGORIES = frozenset(
    {
        "worker",
        "browser",
        "queue",
        "scheduler",
        "runtime-support",
        "proxy",
        "network",
    }
)
FIXED_COST_CATEGORIES = frozenset({"queue", "scheduler", "runtime-support", "proxy"})
EXCLUDED_COST_CATEGORIES = frozenset(
    {
        "postgres",
        "typesense",
        "r2",
        "web",
        "backup",
        "telemetry",
        "control-plane",
    }
)


class ModelError(ValueError):
    """Raised when an input would make the comparison ambiguous or unsafe."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ModelError(message)


def _positive_number(value: object, field: str, *, allow_zero: bool = False) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ModelError(f"{field} must be numeric")
    result = float(value)
    _require(math.isfinite(result), f"{field} must be finite")
    if allow_zero:
        _require(result >= 0, f"{field} must be >= 0")
    else:
        _require(result > 0, f"{field} must be > 0")
    return result


def _optional_nonnegative_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _positive_number(value, field, allow_zero=True)


def _lane_key(lane: dict[str, Any]) -> tuple[str, str]:
    stage = lane.get("stage")
    execution_class = lane.get("execution_class")
    _require(stage in {"monitor", "detail"}, "lane.stage must be monitor or detail")
    _require(execution_class in {"http", "browser"}, "lane.execution_class must be http or browser")
    return str(stage), str(execution_class)


def _ceil(value: float) -> int:
    # Tiny floating point errors at an integer boundary must not buy a whole
    # extra machine in one implementation but not the other.
    return math.ceil(value - 1e-12)


def _nonnegative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ModelError(f"{field} must be integer")
    _require(value >= 0, f"{field} must be >= 0")
    return value


def _validate_strict_timing_coverage(value: object, *, prefix: str) -> None:
    _require(isinstance(value, dict), f"{prefix}.strict_timing is missing or invalid")
    assert isinstance(value, dict)
    _require(
        set(value) == {"limit_seconds", "phases", "complete"},
        f"{prefix}.strict_timing fields are invalid",
    )
    limit_seconds = value.get("limit_seconds")
    _require(
        isinstance(limit_seconds, int | float)
        and not isinstance(limit_seconds, bool)
        and math.isfinite(float(limit_seconds))
        and float(limit_seconds) == SAMPLER_STRICT_TIMING_LIMIT_SECONDS,
        f"{prefix}.strict_timing.limit_seconds differs from the contract",
    )
    phases = value.get("phases")
    _require(
        isinstance(phases, list) and len(phases) == 3,
        f"{prefix}.strict_timing phases must contain exactly three children",
    )
    assert isinstance(phases, list)
    expected_phases = {"wake_lateness", "collection", "handoff"}
    observed_phases: set[str] = set()
    for index, phase_item in enumerate(phases):
        phase_prefix = f"{prefix}.strict_timing.phases[{index}]"
        _require(isinstance(phase_item, dict), f"{phase_prefix} must be an object")
        assert isinstance(phase_item, dict)
        _require(
            set(phase_item) == {"phase", "start", "end", "violations", "resets"},
            f"{phase_prefix} fields are invalid",
        )
        phase = phase_item.get("phase")
        _require(
            isinstance(phase, str) and phase in expected_phases,
            f"{phase_prefix}.phase is invalid",
        )
        assert isinstance(phase, str)
        _require(
            phase not in observed_phases,
            f"{prefix}.strict_timing phase is duplicated",
        )
        observed_phases.add(phase)
        start = _nonnegative_int(phase_item.get("start"), f"{phase_prefix}.start")
        end = _nonnegative_int(phase_item.get("end"), f"{phase_prefix}.end")
        violations = _nonnegative_int(
            phase_item.get("violations"),
            f"{phase_prefix}.violations",
        )
        resets = _nonnegative_int(phase_item.get("resets"), f"{phase_prefix}.resets")
        _require(end >= start, f"{phase_prefix} counter regressed")
        _require(
            violations == end - start,
            f"{phase_prefix}.violations is inconsistent",
        )
        _require(resets == 0, f"{phase_prefix} contains counter resets")
        _require(violations == 0, f"{phase_prefix} contains timing limit violations")
    _require(
        observed_phases == expected_phases,
        f"{prefix}.strict_timing phase set differs from the contract",
    )
    _require(value.get("complete") is True, f"{prefix}.strict_timing is incomplete")


def _validate_process_tree_coverage(
    observed: dict[str, Any],
    *,
    role: str,
    window_seconds_value: object,
) -> int:
    window_seconds = _nonnegative_int(
        window_seconds_value,
        "measurement.window.seconds",
    )
    _require(window_seconds >= 300, "measurement.window.seconds must be >= 300")
    target_ids_raw = observed.get("target_ids")
    if not isinstance(target_ids_raw, list) or not all(
        isinstance(item, str) and bool(item) for item in target_ids_raw
    ):
        raise ModelError(f"measurement role {role} target_ids is invalid")
    target_ids = set(target_ids_raw)
    coverage_raw = observed.get("process_tree_coverage")
    if not isinstance(coverage_raw, list) or not coverage_raw:
        raise ModelError(f"measurement role {role} process-tree coverage is missing")
    coverage_ids: set[str] = set()
    total_successful_samples = 0
    for index, item in enumerate(coverage_raw):
        _require(
            isinstance(item, dict),
            f"measurement role {role} process-tree coverage {index} must be an object",
        )
        target_id = item.get("target_id")
        _require(
            isinstance(target_id, str) and bool(target_id),
            f"measurement role {role} process-tree coverage target_id is invalid",
        )
        _require(
            target_id not in coverage_ids,
            f"measurement role {role} process-tree coverage target is duplicated",
        )
        coverage_ids.add(target_id)
        prefix = f"measurement role {role} process-tree coverage {target_id}"
        _validate_strict_timing_coverage(item.get("strict_timing"), prefix=prefix)
        interval_seconds = _positive_number(
            item.get("sample_interval_seconds"),
            f"{prefix}.sample_interval_seconds",
        )
        _require(
            interval_seconds <= PROCESS_TREE_MAX_SAMPLE_INTERVAL_SECONDS,
            f"{prefix}.sample_interval_seconds exceeds contract maximum",
        )
        expected_samples = _nonnegative_int(
            item.get("expected_samples"), f"{prefix}.expected_samples"
        )
        _require(expected_samples > 1, f"{prefix}.expected_samples must be > 1")
        _require(
            expected_samples == math.floor(window_seconds / interval_seconds),
            f"{prefix}.expected_samples is inconsistent with the measurement window",
        )
        successful_samples = _nonnegative_int(
            item.get("successful_samples"), f"{prefix}.successful_samples"
        )
        failed_samples = _nonnegative_int(item.get("failed_samples"), f"{prefix}.failed_samples")
        missing_samples = _nonnegative_int(item.get("missing_samples"), f"{prefix}.missing_samples")
        counter_resets = _nonnegative_int(item.get("counter_resets"), f"{prefix}.counter_resets")
        sampler_restarts = _nonnegative_int(
            item.get("sampler_restarts"), f"{prefix}.sampler_restarts"
        )
        gap_samples = _nonnegative_int(item.get("gap_samples"), f"{prefix}.gap_samples")
        _require(failed_samples == 0, f"{prefix} contains failed samples")
        _require(counter_resets == 0, f"{prefix} contains counter resets")
        _require(sampler_restarts == 0, f"{prefix} contains sampler restarts")
        _require(gap_samples == 0, f"{prefix} contains sampling gaps")
        _require(
            missing_samples == max(0, expected_samples - successful_samples - failed_samples),
            f"{prefix}.missing_samples is inconsistent",
        )
        coverage_ratio = _positive_number(
            item.get("coverage_ratio"),
            f"{prefix}.coverage_ratio",
            allow_zero=True,
        )
        expected_ratio = min(1.0, successful_samples / expected_samples)
        _require(
            math.isclose(coverage_ratio, expected_ratio, rel_tol=0, abs_tol=1e-12),
            f"{prefix}.coverage_ratio is inconsistent",
        )
        _require(
            item.get("required_coverage_ratio") == PROCESS_TREE_MIN_COVERAGE_RATIO,
            f"{prefix}.required_coverage_ratio differs from the contract",
        )
        _require(
            coverage_ratio >= PROCESS_TREE_MIN_COVERAGE_RATIO,
            f"{prefix}.coverage_ratio is below the contract minimum",
        )
        _require(
            item.get("boundary_tolerance_seconds") == PROCESS_TREE_BOUNDARY_TOLERANCE_SECONDS,
            f"{prefix}.boundary_tolerance_seconds differs from the contract",
        )
        _require(item.get("start_covered") is True, f"{prefix} does not cover window start")
        _require(item.get("end_covered") is True, f"{prefix} does not cover window end")
        _require(item.get("paired_start") is True, f"{prefix} start observation is not paired")
        _require(item.get("paired_end") is True, f"{prefix} end observation is not paired")
        _require(item.get("complete") is True, f"{prefix} is incomplete")
        start_sequence = _nonnegative_int(
            item.get("start_observation_sequence"),
            f"{prefix}.start_observation_sequence",
        )
        end_sequence = _nonnegative_int(
            item.get("end_observation_sequence"),
            f"{prefix}.end_observation_sequence",
        )
        _require(start_sequence > 0, f"{prefix}.start_observation_sequence must be > 0")
        _require(
            end_sequence >= start_sequence,
            f"{prefix} observation sequence regressed",
        )
        _require(
            end_sequence - start_sequence == successful_samples,
            f"{prefix} observation sequence is inconsistent with successful samples",
        )
        total_successful_samples += successful_samples
    _require(
        coverage_ids == target_ids,
        f"measurement role {role} process-tree coverage targets differ from target_ids",
    )
    return total_successful_samples


def _validate_browser_retry_coverage(observed: dict[str, Any], *, role: str) -> None:
    target_ids_raw = observed.get("target_ids")
    assert isinstance(target_ids_raw, list)
    target_ids = set(target_ids_raw)
    coverage = observed.get("retry_coverage")
    _require(
        isinstance(coverage, list) and bool(coverage),
        f"measurement role {role} browser retry coverage is missing",
    )
    assert isinstance(coverage, list)
    observed_keys: set[tuple[str, str]] = set()
    total_retry_events = 0
    for index, item in enumerate(coverage):
        _require(
            isinstance(item, dict),
            f"measurement role {role} browser retry coverage {index} must be an object",
        )
        assert isinstance(item, dict)
        target_id = item.get("target_id")
        family = item.get("family")
        _require(
            isinstance(target_id, str) and target_id in target_ids,
            f"measurement role {role} browser retry target is invalid",
        )
        _require(
            family in _BROWSER_RETRY_FAMILIES,
            f"measurement role {role} browser retry family is invalid",
        )
        assert isinstance(target_id, str)
        assert isinstance(family, str)
        key = (target_id, str(family))
        _require(
            key not in observed_keys,
            f"measurement role {role} browser retry family is duplicated",
        )
        observed_keys.add(key)
        contract = _BROWSER_RETRY_FAMILIES[str(family)]
        prefix = f"measurement role {role} browser retry {target_id}/{family}"
        _require(item.get("stage") == contract["stage"], f"{prefix} stage is invalid")
        _require(item.get("execution_class") == "browser", f"{prefix} class is invalid")
        _require(item.get("complete") is True, f"{prefix} is incomplete")
        _require(item.get("counter_resets") == 0, f"{prefix} contains counter resets")
        required_children = _nonnegative_int(
            item.get("required_children"), f"{prefix}.required_children"
        )
        observed_children = _nonnegative_int(
            item.get("observed_children"), f"{prefix}.observed_children"
        )
        expected_event_keys = contract["keys"]
        assert isinstance(expected_event_keys, set)
        _require(
            required_children == len(expected_event_keys)
            and observed_children == required_children,
            f"{prefix} child presence is incomplete",
        )
        events_raw = item.get("events")
        _require(isinstance(events_raw, list), f"{prefix}.events must be an array")
        assert isinstance(events_raw, list)
        event_keys: set[tuple[str | None, str]] = set()
        outcome_events: dict[str, int] = {}
        retry_events = 0
        for event in events_raw:
            _require(isinstance(event, dict), f"{prefix} event must be an object")
            assert isinstance(event, dict)
            reason = event.get("reason")
            outcome = event.get("outcome")
            _require(isinstance(outcome, str), f"{prefix} event outcome is invalid")
            assert isinstance(outcome, str)
            event_key = (str(reason) if reason is not None else None, outcome)
            _require(event_key not in event_keys, f"{prefix} event is duplicated")
            event_keys.add(event_key)
            count = _nonnegative_int(event.get("events"), f"{prefix} event count")
            outcome_events[outcome] = outcome_events.get(outcome, 0) + count
            if outcome == "retry":
                retry_events += count
        _require(event_keys == expected_event_keys, f"{prefix} event children differ")
        _require(item.get("retry_events") == retry_events, f"{prefix} retry total differs")
        if family == "target-closed":
            terminal_events = outcome_events["recovered"] + outcome_events["failed"]
            _require(
                retry_events == terminal_events,
                f"{prefix} retry terminal accounting differs",
            )
        total_retry_events += retry_events
    _require(
        observed_keys
        == {(target_id, family) for target_id in target_ids for family in _BROWSER_RETRY_FAMILIES},
        f"measurement role {role} browser retry coverage targets differ from target_ids",
    )
    role_retry_events = _optional_nonnegative_number(
        observed.get("retry_events"), f"measurement role {role}.retry_events"
    )
    _require(
        role_retry_events is not None and role_retry_events >= total_retry_events,
        f"measurement role {role} browser retry total is incomplete",
    )


def project_runtime_cost(
    workload: dict[str, Any],
    measurement: dict[str, Any],
    pricing: dict[str, Any],
) -> dict[str, Any]:
    """Project one implementation against one shared workload.

    Readiness is derived from the modeled structure. Removing descriptive
    evidence-gap strings cannot make a projection complete when a concurrency
    limit, usage quantity, support role, or in-scope cost category is absent.
    """

    _require(workload.get("schema_version") == WORKLOAD_SCHEMA, "unsupported workload schema")
    _require(
        measurement.get("schema_version") == MEASUREMENT_SCHEMA,
        "unsupported measurement schema",
    )
    _require(pricing.get("schema_version") == PRICING_SCHEMA, "unsupported pricing schema")
    _require(
        measurement.get("workload_revision") == workload.get("revision"),
        "measurement and workload revisions differ",
    )
    measured_roles_raw = measurement.get("roles")
    process_tree_promoted = isinstance(measured_roles_raw, list) and any(
        isinstance(measured_role, dict) and measured_role.get("resource_scope") == "process-tree"
        for measured_role in measured_roles_raw
    )
    measured_target_ids: list[str] = []
    all_roles_process_tree = True
    exact_day = measurement.get("window", {}).get("seconds") == 86_400
    if exact_day or process_tree_promoted:
        _require(isinstance(measured_roles_raw, list), "measurement roles must be an array")
        assert isinstance(measured_roles_raw, list)
        for measured_role in measured_roles_raw:
            _require(isinstance(measured_role, dict), "measurement role must be an object")
            assert isinstance(measured_role, dict)
            role_target_ids = measured_role.get("target_ids")
            _require(
                isinstance(role_target_ids, list)
                and all(isinstance(target_id, str) for target_id in role_target_ids),
                "measurement role target_ids is invalid",
            )
            assert isinstance(role_target_ids, list)
            measured_target_ids.extend(str(target_id) for target_id in role_target_ids)
            all_roles_process_tree = (
                all_roles_process_tree and measured_role.get("resource_scope") == "process-tree"
            )

    if exact_day:
        source_releases = measurement.get("source_releases")
        _require(
            isinstance(source_releases, list)
            and len(source_releases) == 1
            and isinstance(source_releases[0], str)
            and bool(source_releases[0]),
            "86,400-second measurement requires one source release",
        )
        _require(
            len(measured_target_ids) == len(EXACT_PROCESS_TREE_TARGET_IDS)
            and set(measured_target_ids) == EXACT_PROCESS_TREE_TARGET_IDS,
            "86,400-second measurement requires the exact six-target fleet",
        )
    if process_tree_promoted:
        _require(
            all_roles_process_tree
            and len(measured_target_ids) == len(EXACT_PROCESS_TREE_TARGET_IDS)
            and set(measured_target_ids) == EXACT_PROCESS_TREE_TARGET_IDS,
            "strict process-tree measurement requires the exact complete six-target fleet",
        )
    _require(pricing.get("provider") == "hetzner", "pricing provider must be Hetzner")
    _require(pricing.get("source_currency") == "EUR", "Hetzner source currency must be EUR")
    _require(
        isinstance(pricing.get("revision"), str) and bool(pricing.get("revision")),
        "pricing revision must be non-empty",
    )
    boundary = workload.get("cost_boundary", {})
    _require(
        set(boundary.get("allowed_categories", [])) == ALLOWED_COST_CATEGORIES,
        "workload crawler-runtime cost categories differ from v1",
    )
    _require(
        set(boundary.get("excluded_categories", [])) == EXCLUDED_COST_CATEGORIES,
        "workload excluded cost categories differ from v1",
    )

    def read_point(
        point: dict[str, Any], name: str
    ) -> tuple[dict[tuple[str, str], float], float | None]:
        demand: dict[tuple[str, str], float] = {}
        for lane in point.get("lanes", []):
            key = _lane_key(lane)
            _require(key not in demand, f"duplicate {name} lane {key}")
            demand[key] = _positive_number(
                lane.get("successful_cycles_per_hour"),
                f"{name} lane {key} successful_cycles_per_hour",
                allow_zero=True,
            )
        _require(bool(demand), f"workload must contain {name} lanes")
        traffic_hours_raw = point.get("monthly_traffic_hours")
        traffic_hours = (
            None
            if traffic_hours_raw is None
            else _positive_number(traffic_hours_raw, f"{name}.monthly_traffic_hours")
        )
        return demand, traffic_hours

    current_demand, current_traffic_hours = read_point(
        workload.get("current_load_hour", {}), "current_load_hour"
    )
    projected_demand, projected_traffic_hours = read_point(
        workload.get("projected_peak_hour", {}), "projected_peak_hour"
    )

    headroom = workload.get("headroom", {})
    steady_utilization = _positive_number(
        headroom.get("steady_max_utilization"), "headroom.steady_max_utilization"
    )
    recovery_utilization = _positive_number(
        headroom.get("recovery_max_utilization"), "headroom.recovery_max_utilization"
    )
    memory_utilization = _positive_number(
        headroom.get("memory_max_utilization"), "headroom.memory_max_utilization"
    )
    for field, value in (
        ("steady_max_utilization", steady_utilization),
        ("recovery_max_utilization", recovery_utilization),
        ("memory_max_utilization", memory_utilization),
    ):
        _require(value <= 1, f"headroom.{field} must be <= 1")

    recovery = workload.get("recovery", {})
    arrival_multiplier = _positive_number(
        recovery.get("arrival_multiplier"), "recovery.arrival_multiplier"
    )
    lost_instances = _nonnegative_int(
        recovery.get("lost_instances_per_scaling_role"),
        "recovery.lost_instances_per_scaling_role",
    )

    fx = pricing.get("fx", {})
    eur_to_chf = _positive_number(fx.get("quote_per_base"), "fx.quote_per_base")
    _require(fx.get("base") == "EUR" and fx.get("quote") == "CHF", "FX must be EUR to CHF")

    network = pricing.get("network", {})
    ipv4_monthly_eur = _positive_number(
        network.get("primary_ipv4_monthly_eur"),
        "network.primary_ipv4_monthly_eur",
        allow_zero=True,
    )
    primary_ipv4_per_server = network.get("primary_ipv4_per_server")
    _require(
        primary_ipv4_per_server in {0, 1},
        "network.primary_ipv4_per_server must be zero or one",
    )
    _require(
        network.get("measurement_basis") == "crawler-response-bytes",
        "network measurement basis must be crawler-response-bytes",
    )
    bytes_per_tb = _positive_number(network.get("bytes_per_tb"), "network.bytes_per_tb")
    overage_eur_per_tb = _positive_number(
        network.get("overage_eur_per_tb"),
        "network.overage_eur_per_tb",
        allow_zero=True,
    )
    traffic_priced = network.get("traffic_cost_status") == "priced"

    fixed_costs: dict[str, dict[str, Any]] = {}
    for item in pricing.get("attributable_monthly_costs", []):
        category = item.get("category")
        _require(category in FIXED_COST_CATEGORIES, f"unsupported fixed cost category {category!r}")
        category_name = str(category)
        _require(category_name not in fixed_costs, f"duplicate fixed cost category {category_name}")
        roles = item.get("covered_roles", [])
        _require(
            isinstance(roles, list) and all(isinstance(role, str) and bool(role) for role in roles),
            f"fixed cost category {category_name} covered_roles must be strings",
        )
        fixed_costs[category_name] = {
            "category": category_name,
            "status": item.get("status"),
            "covered_roles": sorted(set(roles)),
            "current_sustainable_monthly_eur": _optional_nonnegative_number(
                item.get("current_sustainable_monthly_eur"),
                f"fixed cost {category_name}.current_sustainable_monthly_eur",
            ),
            "projected_load_monthly_eur": _optional_nonnegative_number(
                item.get("projected_load_monthly_eur"),
                f"fixed cost {category_name}.projected_load_monthly_eur",
            ),
        }

    server_skus: list[dict[str, Any]] = []
    seen_skus: set[str] = set()
    for item in pricing.get("server_skus", []):
        sku = item.get("sku")
        _require(isinstance(sku, str) and bool(sku), "Hetzner SKU must be non-empty")
        _require(sku not in seen_skus, f"duplicate Hetzner SKU {sku}")
        seen_skus.add(sku)
        server_skus.append(
            {
                **item,
                "vcpus": _positive_number(item.get("vcpus"), f"SKU {sku}.vcpus"),
                "memory_bytes": _positive_number(
                    item.get("memory_bytes"), f"SKU {sku}.memory_bytes"
                ),
                "monthly_eur_excluding_ipv4_vat": _positive_number(
                    item.get("monthly_eur_excluding_ipv4_vat"),
                    f"SKU {sku}.monthly_eur_excluding_ipv4_vat",
                ),
                "included_traffic_tb_per_server": _positive_number(
                    item.get("included_traffic_tb_per_server"),
                    f"SKU {sku}.included_traffic_tb_per_server",
                    allow_zero=True,
                ),
            }
        )
    _require(bool(server_skus), "pricing must contain at least one Hetzner SKU")

    selection = pricing.get("scenario_selection", {})
    selected_current_sku = selection.get("current_load_sku")
    selected_projected_sku = selection.get("projected_load_sku")
    for name, selected in (
        ("current_load_sku", selected_current_sku),
        ("projected_load_sku", selected_projected_sku),
    ):
        _require(selected is None or selected in seen_skus, f"unknown {name} {selected!r}")
    evidenced_current_sku = (
        pricing.get("billing_assumptions", {}).get("current_crawler_sku_evidence", {}).get("sku")
    )
    _require(
        selected_current_sku == evidenced_current_sku,
        "current-load SKU must match the evidenced current crawler SKU",
    )

    supplied_lanes: set[tuple[str, str]] = set()
    support_roles: set[str] = set()
    scaling_roles: list[dict[str, Any]] = []
    structural_blockers: list[str] = []
    for observed in measurement.get("roles", []):
        role = observed.get("role")
        _require(isinstance(role, str) and bool(role), "measurement role must be non-empty")
        execution_class = observed.get("execution_class")
        cost_category = observed.get("cost_category")
        expected_category = {
            "http": "worker",
            "browser": "browser",
            "support": "runtime-support",
        }.get(execution_class)
        _require(
            expected_category is not None, f"measurement role {role} execution class is invalid"
        )
        _require(
            cost_category == expected_category,
            f"measurement role {role} cost category must be {expected_category}",
        )
        lanes = observed.get("lanes", [])
        if not lanes:
            _require(execution_class == "support", f"scaling role {role} must declare lanes")
            support_roles.add(role)
            continue
        _require(execution_class != "support", f"support role {role} cannot own workload lanes")
        vcpus = _positive_number(
            observed.get("vcpu_limit_per_instance"),
            f"measurement role {role}.vcpu_limit_per_instance",
        )
        memory_bytes = _positive_number(
            observed.get("memory_limit_bytes_per_instance"),
            f"measurement role {role}.memory_limit_bytes_per_instance",
        )
        discovery_concurrency = _positive_number(
            observed.get("discovery_concurrency_per_instance"),
            f"measurement role {role}.discovery_concurrency_per_instance",
        )
        monitor_concurrency = _positive_number(
            observed.get("monitor_concurrency_per_instance"),
            f"measurement role {role}.monitor_concurrency_per_instance",
        )
        _require(
            monitor_concurrency <= discovery_concurrency,
            f"measurement role {role} monitor concurrency exceeds discovery pool",
        )

        observed_active_seconds = 0.0
        normalized_lanes: list[dict[str, Any]] = []
        for lane in lanes:
            key = _lane_key(lane)
            _require(key not in supplied_lanes, f"measurement lane {key} has multiple owners")
            supplied_lanes.add(key)
            successes = _positive_number(
                lane.get("successful_cycles"),
                f"measurement {role} lane {key} successful_cycles",
            )
            active_seconds = _positive_number(
                lane.get("task_active_seconds"),
                f"measurement {role} lane {key} task_active_seconds",
            )
            origin_attempts = _optional_nonnegative_number(
                lane.get("origin_attempts"),
                f"measurement {role} lane {key} origin_attempts",
            )
            response_bytes = _optional_nonnegative_number(
                lane.get("response_bytes"),
                f"measurement {role} lane {key} response_bytes",
            )
            if origin_attempts is None:
                structural_blockers.append(f"origin-attempts-unmeasured:{key[0]}:{key[1]}")
            if response_bytes is None:
                structural_blockers.append(f"response-bytes-unmeasured:{key[0]}:{key[1]}")
            observed_active_seconds += active_seconds
            normalized_lanes.append(
                {
                    "stage": key[0],
                    "execution_class": key[1],
                    "task_active_seconds_per_success": active_seconds / successes,
                    "origin_attempts_per_success": (
                        origin_attempts / successes if origin_attempts is not None else None
                    ),
                    "response_bytes_per_success": (
                        response_bytes / successes if response_bytes is not None else None
                    ),
                }
            )
        observed_cpu_seconds = _positive_number(
            observed.get("process_cpu_seconds"),
            f"measurement role {role}.process_cpu_seconds",
            allow_zero=True,
        )
        cpu_per_active_second = (
            observed_cpu_seconds / observed_active_seconds if observed_active_seconds else 0.0
        )
        peak_rss = _positive_number(
            observed.get("peak_rss_bytes_per_instance"),
            f"measurement role {role}.peak_rss_bytes_per_instance",
            allow_zero=True,
        )
        resource_scope = observed.get("resource_scope")
        if resource_scope == "process-tree":
            _require(
                observed.get("process_tree_cpu_source") == "container-cgroup-v2",
                f"measurement role {role} process-tree CPU source is invalid",
            )
            _require(
                observed.get("process_tree_cpu_scope") == "one-crawler-role-container-per-target",
                f"measurement role {role} process-tree CPU scope is invalid",
            )
            root_cpu = _optional_nonnegative_number(
                observed.get("root_process_cpu_seconds"),
                f"measurement role {role}.root_process_cpu_seconds",
            )
            descendant_cpu = _optional_nonnegative_number(
                observed.get("descendant_process_cpu_seconds"),
                f"measurement role {role}.descendant_process_cpu_seconds",
            )
            root_peak_rss = _optional_nonnegative_number(
                observed.get("root_peak_rss_bytes_per_instance"),
                f"measurement role {role}.root_peak_rss_bytes_per_instance",
            )
            tree_peak_rss = _optional_nonnegative_number(
                observed.get("process_tree_peak_rss_bytes_per_instance"),
                f"measurement role {role}.process_tree_peak_rss_bytes_per_instance",
            )
            if (
                root_cpu is None
                or descendant_cpu is None
                or root_peak_rss is None
                or tree_peak_rss is None
            ):
                raise ModelError(f"measurement role {role} process-tree evidence is incomplete")
            tree_samples = _nonnegative_int(
                observed.get("process_tree_successful_samples"),
                f"measurement role {role}.process_tree_successful_samples",
            )
            _require(tree_samples > 0, f"measurement role {role} process-tree samples are empty")
            coverage_samples = _validate_process_tree_coverage(
                observed,
                role=role,
                window_seconds_value=measurement.get("window", {}).get("seconds"),
            )
            _require(
                tree_samples == coverage_samples,
                f"measurement role {role} process-tree sample total is inconsistent",
            )
            _require(
                math.isclose(
                    observed_cpu_seconds,
                    root_cpu + descendant_cpu,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ),
                f"measurement role {role} process-tree CPU total is inconsistent",
            )
            _require(
                observed_cpu_seconds >= root_cpu,
                f"measurement role {role} process-tree CPU is below root CPU",
            )
            _require(
                math.isclose(peak_rss, tree_peak_rss, rel_tol=1e-9, abs_tol=1e-9),
                f"measurement role {role} process-tree RSS total is inconsistent",
            )
            _require(
                tree_peak_rss >= root_peak_rss,
                f"measurement role {role} process-tree RSS is below root RSS",
            )
            if execution_class == "browser":
                _validate_browser_retry_coverage(observed, role=role)
        elif resource_scope not in {None, "root-process"}:
            raise ModelError(f"measurement role {role} resource scope is invalid")
        if execution_class == "browser" and resource_scope != "process-tree":
            structural_blockers.append("browser-child-cpu-and-rss-not-in-process-metrics")
        memory_ratio = peak_rss / memory_bytes
        scaling_roles.append(
            {
                "role": role,
                "cost_category": cost_category,
                "vcpus_per_instance": vcpus,
                "memory_bytes_per_instance": memory_bytes,
                "discovery_concurrency_per_instance": discovery_concurrency,
                "monitor_concurrency_per_instance": monitor_concurrency,
                "cpu_per_active_second": cpu_per_active_second,
                "observed_peak_rss_ratio": memory_ratio,
                "memory_gate_passes": memory_ratio <= memory_utilization,
                "lanes": normalized_lanes,
            }
        )

    for name, demand in (
        ("current_load_hour", current_demand),
        ("projected_peak_hour", projected_demand),
    ):
        missing_lanes = sorted(
            key
            for key, lane_demand in demand.items()
            if lane_demand > 0 and key not in supplied_lanes
        )
        _require(
            not missing_lanes,
            f"measurement has no successful evidence for {name} lanes {missing_lanes}",
        )

    runtime_support_entry = fixed_costs.get("runtime-support")
    covered_support_roles = (
        set(runtime_support_entry["covered_roles"]) if runtime_support_entry is not None else set()
    )
    for role in sorted(support_roles - covered_support_roles):
        structural_blockers.append(f"runtime-support-role-uncovered:{role}")
    for role in sorted(covered_support_roles - support_roles):
        structural_blockers.append(f"runtime-support-role-unobserved:{role}")
    for category in FIXED_COST_CATEGORIES - {"runtime-support"}:
        entry = fixed_costs.get(category)
        if entry is not None and entry["covered_roles"]:
            structural_blockers.append(f"fixed-cost-role-coverage-invalid:{category}")

    evidence_blockers = [str(item) for item in measurement.get("evidence_gaps", [])]
    evidence_blockers.extend(
        str(item.get("key"))
        for item in workload.get("required_evidence", [])
        if item.get("status") != "frozen"
    )
    evidence_blockers.extend(
        f"memory:{item['role']}" for item in scaling_roles if not item["memory_gate_passes"]
    )
    common_blockers = sorted(set(filter(None, evidence_blockers + structural_blockers)))

    def point_blockers(
        point_name: str,
        *,
        traffic_hours: float | None,
        selected_sku: str | None,
    ) -> list[str]:
        result = list(common_blockers)
        if selected_sku is None:
            result.append(f"{point_name}-hetzner-sku-unselected")
        if not traffic_priced:
            result.append(f"{point_name}-network-traffic-cost-unpriced")
        if traffic_hours is None:
            result.append(f"{point_name}-monthly-traffic-hours-unfrozen")
        for category in sorted(FIXED_COST_CATEGORIES):
            entry = fixed_costs.get(category)
            if entry is None:
                result.append(f"{point_name}-fixed-cost-category-missing:{category}")
                continue
            value = entry[f"{point_name}_monthly_eur"]
            if entry["status"] != "priced" or value is None:
                result.append(f"{point_name}-fixed-cost-unpriced:{category}")
        return sorted(set(result))

    current_point_blockers = point_blockers(
        "current_sustainable",
        traffic_hours=current_traffic_hours,
        selected_sku=selected_current_sku,
    )
    projected_point_blockers = point_blockers(
        "projected_load",
        traffic_hours=projected_traffic_hours,
        selected_sku=selected_projected_sku,
    )

    current_budget = _positive_number(
        workload.get("current_budget_reference", {}).get("monthly_chf"),
        "current_budget_reference.monthly_chf",
        allow_zero=True,
    )

    def size_point(
        demand: dict[tuple[str, str], float],
        *,
        point_name: str,
        traffic_hours: float | None,
        point_arrival_multiplier: float,
        point_lost_instances: int,
        selected_sku: str | None,
        current_budget_reference: float | None,
        cost_blockers: list[str],
    ) -> dict[str, Any]:
        role_results: list[dict[str, Any]] = []
        total_vcpus = 0.0
        total_memory_bytes = 0.0
        monthly_response_bytes = 0.0 if traffic_hours is not None else None
        monthly_origin_attempts = 0.0 if traffic_hours is not None else None
        scaling_cost_categories: dict[str, list[str]] = {"worker": [], "browser": []}
        for role in scaling_roles:
            projected_active_seconds = 0.0
            projected_monitor_seconds = 0.0
            lane_results: list[dict[str, Any]] = []
            scaling_cost_categories[role["cost_category"]].append(role["role"])
            for lane in role["lanes"]:
                key = (lane["stage"], lane["execution_class"])
                lane_demand = demand.get(key, 0.0)
                projected_seconds = lane_demand * lane["task_active_seconds_per_success"]
                projected_active_seconds += projected_seconds
                if lane["stage"] == "monitor":
                    projected_monitor_seconds += projected_seconds
                if traffic_hours is not None:
                    response_per_success = lane["response_bytes_per_success"]
                    attempts_per_success = lane["origin_attempts_per_success"]
                    if response_per_success is None:
                        monthly_response_bytes = None
                    elif monthly_response_bytes is not None:
                        monthly_response_bytes += lane_demand * response_per_success * traffic_hours
                    if attempts_per_success is None:
                        monthly_origin_attempts = None
                    elif monthly_origin_attempts is not None:
                        monthly_origin_attempts += (
                            lane_demand * attempts_per_success * traffic_hours
                        )
                lane_results.append({**lane, "successful_cycles_per_hour": lane_demand})

            steady_shared_count = _ceil(
                projected_active_seconds
                / (3600 * role["discovery_concurrency_per_instance"] * steady_utilization)
            )
            steady_monitor_count = _ceil(
                projected_monitor_seconds
                / (3600 * role["monitor_concurrency_per_instance"] * steady_utilization)
            )
            steady_concurrency_count = max(steady_shared_count, steady_monitor_count)
            recovery_shared_count = _ceil(
                projected_active_seconds
                * point_arrival_multiplier
                / (3600 * role["discovery_concurrency_per_instance"] * recovery_utilization)
            )
            recovery_monitor_count = _ceil(
                projected_monitor_seconds
                * point_arrival_multiplier
                / (3600 * role["monitor_concurrency_per_instance"] * recovery_utilization)
            )
            recovery_concurrency_count = (
                max(recovery_shared_count, recovery_monitor_count) + point_lost_instances
            )
            projected_cpu_cores = projected_active_seconds * role["cpu_per_active_second"] / 3600
            steady_cpu_count = _ceil(
                projected_cpu_cores / (role["vcpus_per_instance"] * steady_utilization)
            )
            recovery_cpu_count = (
                _ceil(
                    projected_cpu_cores
                    * point_arrival_multiplier
                    / (role["vcpus_per_instance"] * recovery_utilization)
                )
                + point_lost_instances
            )
            required_instances = max(
                steady_concurrency_count,
                recovery_concurrency_count,
                steady_cpu_count,
                recovery_cpu_count,
            )
            total_vcpus += required_instances * role["vcpus_per_instance"]
            total_memory_bytes += required_instances * role["memory_bytes_per_instance"]
            role_results.append(
                {
                    "role": role["role"],
                    "cost_category": role["cost_category"],
                    "required_instances": required_instances,
                    "steady_shared_discovery_instances": steady_shared_count,
                    "steady_monitor_subcap_instances": steady_monitor_count,
                    "steady_concurrency_instances": steady_concurrency_count,
                    "steady_cpu_instances": steady_cpu_count,
                    "recovery_shared_discovery_instances": recovery_shared_count,
                    "recovery_monitor_subcap_instances": recovery_monitor_count,
                    "recovery_concurrency_instances": recovery_concurrency_count,
                    "recovery_cpu_instances": recovery_cpu_count,
                    "observed_peak_rss_ratio": role["observed_peak_rss_ratio"],
                    "memory_gate_passes": role["memory_gate_passes"],
                    "reserved_vcpus": required_instances * role["vcpus_per_instance"],
                    "reserved_memory_bytes": required_instances * role["memory_bytes_per_instance"],
                    "lanes": sorted(
                        lane_results,
                        key=lambda item: (item["stage"], item["execution_class"]),
                    ),
                }
            )

        fixed_cost_ledger: list[dict[str, Any]] = []
        known_fixed_eur = 0.0
        for category in sorted(FIXED_COST_CATEGORIES):
            entry = fixed_costs.get(category)
            value = entry[f"{point_name}_monthly_eur"] if entry is not None else None
            if value is not None:
                known_fixed_eur += value
            fixed_cost_ledger.append(
                {
                    "category": category,
                    "status": entry["status"] if entry is not None else "missing",
                    "covered_roles": entry["covered_roles"] if entry is not None else [],
                    "monthly_eur_excluding_vat": value,
                    "monthly_chf_excluding_vat": value * eur_to_chf if value is not None else None,
                }
            )

        sku_scenarios: list[dict[str, Any]] = []
        for sku in server_skus:
            cpu_servers = _ceil(total_vcpus / sku["vcpus"])
            memory_servers = _ceil(total_memory_bytes / sku["memory_bytes"])
            required_servers = max(cpu_servers, memory_servers)
            server_eur = required_servers * sku["monthly_eur_excluding_ipv4_vat"]
            ipv4_eur = required_servers * int(primary_ipv4_per_server) * ipv4_monthly_eur
            compute_ipv4_eur = server_eur + ipv4_eur
            included_traffic_bytes = (
                required_servers * sku["included_traffic_tb_per_server"] * bytes_per_tb
            )
            traffic_overage_bytes = (
                max(0.0, monthly_response_bytes - included_traffic_bytes)
                if traffic_priced and monthly_response_bytes is not None
                else None
            )
            traffic_eur = (
                traffic_overage_bytes / bytes_per_tb * overage_eur_per_tb
                if traffic_overage_bytes is not None
                else None
            )
            known_eur = compute_ipv4_eur + known_fixed_eur + (traffic_eur or 0.0)
            complete_eur = known_eur if not cost_blockers else None
            sku_scenarios.append(
                {
                    "sku": sku["sku"],
                    "family": sku.get("family"),
                    "cpu_allocation": sku.get("cpu_allocation"),
                    "required_servers": required_servers,
                    "cpu_required_servers": cpu_servers,
                    "memory_required_servers": memory_servers,
                    "monthly_server_eur_excluding_ipv4_vat": server_eur,
                    "monthly_ipv4_eur_excluding_vat": ipv4_eur,
                    "monthly_compute_ipv4_eur_excluding_vat": compute_ipv4_eur,
                    "monthly_compute_ipv4_chf_excluding_vat": compute_ipv4_eur * eur_to_chf,
                    "monthly_network_traffic_eur_excluding_vat": traffic_eur,
                    "monthly_attributable_fixed_eur_excluding_vat": known_fixed_eur,
                    "monthly_known_crawler_subtotal_eur_excluding_vat": known_eur,
                    "monthly_known_crawler_subtotal_chf_excluding_vat": known_eur * eur_to_chf,
                    "monthly_complete_crawler_cost_eur_excluding_vat": complete_eur,
                    "monthly_complete_crawler_cost_chf_excluding_vat": (
                        complete_eur * eur_to_chf if complete_eur is not None else None
                    ),
                    "included_traffic_tb_per_server": sku["included_traffic_tb_per_server"],
                    "included_traffic_bytes": included_traffic_bytes,
                    "traffic_overage_bytes": traffic_overage_bytes,
                }
            )
        selected = next(
            (item for item in sku_scenarios if item["sku"] == selected_sku),
            None,
        )
        minimum_monthly_eur = (
            selected["monthly_complete_crawler_cost_eur_excluding_vat"] if selected else None
        )
        minimum_monthly_chf = (
            selected["monthly_complete_crawler_cost_chf_excluding_vat"] if selected else None
        )
        cost_ledger = [
            {
                "category": category,
                "status": "packed-in-server-scenarios",
                "roles": sorted(roles),
            }
            for category, roles in sorted(scaling_cost_categories.items())
        ]
        cost_ledger.extend(fixed_cost_ledger)
        cost_ledger.append(
            {
                "category": "network",
                "status": "priced" if traffic_priced else "unknown",
                "measurement_basis": network.get("measurement_basis"),
                "monthly_response_bytes": monthly_response_bytes,
                "monthly_origin_attempts": monthly_origin_attempts,
                "primary_ipv4_per_server": primary_ipv4_per_server,
            }
        )
        result = {
            "roles": sorted(role_results, key=lambda item: item["role"]),
            "required_runtime_vcpus": total_vcpus,
            "required_runtime_memory_bytes": total_memory_bytes,
            "monthly_traffic_hours": traffic_hours,
            "monthly_response_bytes": monthly_response_bytes,
            "monthly_origin_attempts": monthly_origin_attempts,
            "cost_ledger": cost_ledger,
            "required_cost_categories": sorted(ALLOWED_COST_CATEGORIES),
            "sku_scenarios": sorted(sku_scenarios, key=lambda item: item["sku"]),
            "selected_sku": selected_sku,
            "selected_monthly_compute_ipv4_eur_excluding_vat": (
                selected["monthly_compute_ipv4_eur_excluding_vat"] if selected else None
            ),
            "selected_monthly_compute_ipv4_chf_excluding_vat": (
                selected["monthly_compute_ipv4_chf_excluding_vat"] if selected else None
            ),
            "cost_complete": not cost_blockers and selected is not None,
            "cost_blockers": cost_blockers,
            "minimum_sustainable_monthly_eur_excluding_vat": minimum_monthly_eur,
            "minimum_sustainable_monthly_chf_excluding_vat": minimum_monthly_chf,
        }
        if current_budget_reference is not None:
            result["current_budget_reference_chf"] = current_budget_reference
            result["current_budget_shortfall_chf"] = (
                max(0.0, minimum_monthly_chf - current_budget_reference)
                if minimum_monthly_chf is not None
                else None
            )
        return result

    current_loss = _nonnegative_int(
        workload.get("current_load_hour", {}).get("lost_instances_per_scaling_role"),
        "current_load_hour.lost_instances_per_scaling_role",
    )
    current_result = size_point(
        current_demand,
        point_name="current_sustainable",
        traffic_hours=current_traffic_hours,
        point_arrival_multiplier=1.0,
        point_lost_instances=current_loss,
        selected_sku=selected_current_sku,
        current_budget_reference=current_budget,
        cost_blockers=current_point_blockers,
    )
    projected_result = size_point(
        projected_demand,
        point_name="projected_load",
        traffic_hours=projected_traffic_hours,
        point_arrival_multiplier=arrival_multiplier,
        point_lost_instances=lost_instances,
        selected_sku=selected_projected_sku,
        current_budget_reference=None,
        cost_blockers=projected_point_blockers,
    )
    blockers = sorted(set(current_point_blockers + projected_point_blockers))
    decision_ready = (
        not blockers and current_result["cost_complete"] and projected_result["cost_complete"]
    )
    return {
        "schema_version": PROJECTION_SCHEMA,
        "workload_revision": workload["revision"],
        "measurement_id": measurement.get("measurement_id"),
        "implementation": measurement.get("implementation"),
        "pricing_revision": pricing.get("revision"),
        "currency": "CHF",
        "comparison_points": {
            "current_sustainable": current_result,
            "projected_load": projected_result,
        },
        "pricing": {
            "provider": "hetzner",
            "region_group": pricing.get("region_group"),
            "source_currency": "EUR",
            "vat_treatment": pricing.get("vat_treatment"),
            "fx": fx,
            "network": network,
            "price_effective_at": pricing.get("price_effective_at"),
            "retrieved_at": pricing.get("retrieved_at"),
        },
        "blockers": blockers,
        "decision_ready": bool(decision_ready),
    }
