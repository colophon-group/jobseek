"""Deterministic crawler-only capacity and cost calculations.

The workload is intentionally independent of an implementation.  A Python or
Go measurement maps the same workload lanes onto its own runtime roles.  This
keeps the comparison honest when, for example, Go and Lightpanda run as two
separate services while Python and Chromium share one browser-worker role.
"""

from __future__ import annotations

import math
from typing import Any

WORKLOAD_SCHEMA = "jobseek.crawler-runtime-workload/v1"
MEASUREMENT_SCHEMA = "jobseek.crawler-runtime-measurement/v1"
PRICING_SCHEMA = "jobseek.crawler-runtime-pricing/v1"
PROJECTION_SCHEMA = "jobseek.crawler-runtime-projection/v1"

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


def project_runtime_cost(
    workload: dict[str, Any],
    measurement: dict[str, Any],
    pricing: dict[str, Any],
) -> dict[str, Any]:
    """Project one implementation against one shared workload.

    The result is evidence, not an automatic migration verdict.  Any declared
    evidence gap or unfrozen workload dimension is copied into ``blockers`` so
    a cheap but incomplete implementation cannot be presented as passing.
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

    def read_demand(point: dict[str, Any], name: str) -> dict[tuple[str, str], float]:
        result: dict[tuple[str, str], float] = {}
        for lane in point.get("lanes", []):
            key = _lane_key(lane)
            _require(key not in result, f"duplicate {name} lane {key}")
            result[key] = _positive_number(
                lane.get("successful_cycles_per_hour"),
                f"{name} lane {key} successful_cycles_per_hour",
                allow_zero=True,
            )
        _require(bool(result), f"workload must contain {name} lanes")
        return result

    current_demand = read_demand(workload.get("current_load_hour", {}), "current_load_hour")
    projected_demand = read_demand(workload.get("projected_peak_hour", {}), "projected_peak_hour")

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

    ipv4_monthly_eur = _positive_number(
        pricing.get("network", {}).get("primary_ipv4_monthly_eur"),
        "network.primary_ipv4_monthly_eur",
        allow_zero=True,
    )
    primary_ipv4_per_server = pricing.get("network", {}).get("primary_ipv4_per_server")
    _require(
        primary_ipv4_per_server in {0, 1},
        "network.primary_ipv4_per_server must be zero or one",
    )

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
    scaling_roles: list[dict[str, Any]] = []
    for observed in measurement.get("roles", []):
        role = observed.get("role")
        _require(isinstance(role, str) and bool(role), "measurement role must be non-empty")
        lanes = observed.get("lanes", [])
        if not lanes:
            # Support processes are represented as attributable fixed costs;
            # observing them does not imply they scale linearly with cycles.
            continue
        vcpus = _positive_number(
            observed.get("vcpu_limit_per_instance"),
            f"measurement role {role}.vcpu_limit_per_instance",
        )
        memory_bytes = _positive_number(
            observed.get("memory_limit_bytes_per_instance"),
            f"measurement role {role}.memory_limit_bytes_per_instance",
        )

        observed_active_seconds = 0.0
        normalized_lanes: list[dict[str, Any]] = []
        for lane in lanes:
            key = _lane_key(lane)
            supplied_lanes.add(key)
            successes = _positive_number(
                lane.get("successful_cycles"),
                f"measurement {role} lane {key} successful_cycles",
            )
            active_seconds = _positive_number(
                lane.get("task_active_seconds"),
                f"measurement {role} lane {key} task_active_seconds",
            )
            concurrency = _positive_number(
                lane.get("max_concurrency_per_instance"),
                f"measurement {role} lane {key} max_concurrency_per_instance",
            )
            seconds_per_success = active_seconds / successes
            observed_active_seconds += active_seconds
            normalized_lanes.append(
                {
                    "stage": key[0],
                    "execution_class": key[1],
                    "task_active_seconds_per_success": seconds_per_success,
                    "max_concurrency_per_instance": concurrency,
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
        memory_ratio = peak_rss / memory_bytes
        scaling_roles.append(
            {
                "role": role,
                "vcpus_per_instance": vcpus,
                "memory_bytes_per_instance": memory_bytes,
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

    blockers = [str(item) for item in measurement.get("evidence_gaps", [])]
    blockers.extend(
        str(item.get("key"))
        for item in workload.get("required_evidence", [])
        if item.get("status") != "frozen"
    )
    blockers.extend(
        f"memory:{item['role']}" for item in scaling_roles if not item["memory_gate_passes"]
    )
    if selected_projected_sku is None:
        blockers.append("projected-hetzner-sku-unselected")
    if pricing.get("network", {}).get("traffic_cost_status") != "priced":
        blockers.append("network-traffic-cost-unpriced")
    blockers = sorted(set(filter(None, blockers)))

    current_budget = _positive_number(
        workload.get("current_budget_reference", {}).get("monthly_chf"),
        "current_budget_reference.monthly_chf",
        allow_zero=True,
    )

    def size_point(
        demand: dict[tuple[str, str], float],
        *,
        point_arrival_multiplier: float,
        point_lost_instances: int,
        selected_sku: str | None,
        current_budget_reference: float | None,
        point_cost_blockers: list[str],
    ) -> dict[str, Any]:
        role_results: list[dict[str, Any]] = []
        total_vcpus = 0.0
        total_memory_bytes = 0.0
        for role in scaling_roles:
            instance_demand = 0.0
            projected_active_seconds = 0.0
            lane_results: list[dict[str, Any]] = []
            for lane in role["lanes"]:
                key = (lane["stage"], lane["execution_class"])
                lane_demand = demand.get(key, 0.0)
                projected_seconds = lane_demand * lane["task_active_seconds_per_success"]
                projected_active_seconds += projected_seconds
                instance_demand += projected_seconds / (3600 * lane["max_concurrency_per_instance"])
                lane_results.append({**lane, "successful_cycles_per_hour": lane_demand})

            steady_concurrency_count = _ceil(instance_demand / steady_utilization)
            recovery_concurrency_count = (
                _ceil(instance_demand * point_arrival_multiplier / recovery_utilization)
                + point_lost_instances
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
                    "required_instances": required_instances,
                    "steady_concurrency_instances": steady_concurrency_count,
                    "steady_cpu_instances": steady_cpu_count,
                    "recovery_concurrency_instances": recovery_concurrency_count,
                    "recovery_cpu_instances": recovery_cpu_count,
                    "observed_peak_rss_ratio": role["observed_peak_rss_ratio"],
                    "memory_gate_passes": role["memory_gate_passes"],
                    "reserved_vcpus": required_instances * role["vcpus_per_instance"],
                    "reserved_memory_bytes": (
                        required_instances * role["memory_bytes_per_instance"]
                    ),
                    "lanes": sorted(
                        lane_results,
                        key=lambda item: (item["stage"], item["execution_class"]),
                    ),
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
            compute_ipv4_chf = compute_ipv4_eur * eur_to_chf
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
                    "monthly_compute_ipv4_chf_excluding_vat": compute_ipv4_chf,
                    "annual_compute_ipv4_eur_excluding_vat": compute_ipv4_eur * 12,
                    "annual_compute_ipv4_chf_excluding_vat": compute_ipv4_chf * 12,
                    "included_traffic_tb_per_server": sku.get("included_traffic_tb_per_server"),
                }
            )
        selected = next(
            (item for item in sku_scenarios if item["sku"] == selected_sku),
            None,
        )
        monthly_compute_ipv4_eur = (
            selected["monthly_compute_ipv4_eur_excluding_vat"] if selected else None
        )
        monthly_compute_ipv4_chf = (
            selected["monthly_compute_ipv4_chf_excluding_vat"] if selected else None
        )
        cost_complete = not point_cost_blockers and selected is not None
        minimum_monthly_eur = monthly_compute_ipv4_eur if cost_complete else None
        minimum_monthly_chf = monthly_compute_ipv4_chf if cost_complete else None
        result = {
            "roles": sorted(role_results, key=lambda item: item["role"]),
            "required_runtime_vcpus": total_vcpus,
            "required_runtime_memory_bytes": total_memory_bytes,
            "sku_scenarios": sorted(sku_scenarios, key=lambda item: item["sku"]),
            "selected_sku": selected_sku,
            "selected_monthly_compute_ipv4_eur_excluding_vat": monthly_compute_ipv4_eur,
            "selected_monthly_compute_ipv4_chf_excluding_vat": monthly_compute_ipv4_chf,
            "cost_complete": cost_complete,
            "cost_blockers": sorted(point_cost_blockers),
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
    current_cost_blockers = [
        blocker for blocker in blockers if blocker != "projected-hetzner-sku-unselected"
    ]
    return {
        "schema_version": PROJECTION_SCHEMA,
        "workload_revision": workload["revision"],
        "measurement_id": measurement.get("measurement_id"),
        "implementation": measurement.get("implementation"),
        "pricing_revision": pricing.get("revision"),
        "currency": "CHF",
        "comparison_points": {
            "current_sustainable": size_point(
                current_demand,
                point_arrival_multiplier=1.0,
                point_lost_instances=current_loss,
                selected_sku=selected_current_sku,
                current_budget_reference=current_budget,
                point_cost_blockers=current_cost_blockers,
            ),
            "projected_load": size_point(
                projected_demand,
                point_arrival_multiplier=arrival_multiplier,
                point_lost_instances=lost_instances,
                selected_sku=selected_projected_sku,
                current_budget_reference=None,
                point_cost_blockers=blockers,
            ),
        },
        "pricing": {
            "provider": "hetzner",
            "region_group": pricing.get("region_group"),
            "source_currency": "EUR",
            "vat_treatment": pricing.get("vat_treatment"),
            "fx": fx,
            "network": pricing.get("network"),
            "price_effective_at": pricing.get("price_effective_at"),
            "retrieved_at": pricing.get("retrieved_at"),
        },
        "blockers": blockers,
        "decision_ready": not blockers,
    }
