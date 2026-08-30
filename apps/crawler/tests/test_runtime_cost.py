"""Crawler-only Python versus Go runtime cost foundation tests."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from prometheus_client import CollectorRegistry, Counter, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from src.metrics import _seed_process_tree_sample_outcomes
from src.runtime_cost.model import ModelError, project_runtime_cost
from src.runtime_cost.prometheus import capture_prometheus_measurement

ROOT = Path(__file__).parents[1]
RUNTIME_COST = ROOT / "runtime-cost"

_ATTRIBUTION_METRICS = (
    "crawler_runtime_origin_attempts_total",
    "crawler_runtime_origin_outcomes_total",
    "crawler_runtime_response_body_bytes_total",
    "crawler_runtime_capability_executions_total",
    "crawler_runtime_executions_total",
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _exposed_counter_value(payload: bytes, metric: str, outcome: str) -> float:
    for family in text_string_to_metric_families(payload.decode()):
        for sample in family.samples:
            if sample.name == metric and sample.labels == {"outcome": outcome}:
                return float(sample.value)
    raise AssertionError(f"missing {metric} outcome={outcome}")


def test_runtime_cost_schemas_are_valid_draft_2020_12():
    for schema_path in sorted((RUNTIME_COST / "schemas").glob("*.schema.json")):
        Draft202012Validator.check_schema(_json(schema_path))


@pytest.mark.parametrize(
    ("document", "schema"),
    [
        ("projected-workload-v1.json", "workload-v1.schema.json"),
        ("python-production-targets-v1.json", "capture-targets-v1.schema.json"),
        (
            "evidence/python-production-2026-08-29-24h.json",
            "measurement-v1.schema.json",
        ),
        (
            "pricing/hetzner-eu-2026-06-15.json",
            "pricing-v1.schema.json",
        ),
    ],
)
def test_committed_runtime_cost_documents_match_versioned_schemas(document: str, schema: str):
    validator = Draft202012Validator(
        _json(RUNTIME_COST / "schemas" / schema),
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(_json(RUNTIME_COST / document)),
        key=lambda error: list(error.absolute_path),
    )
    assert errors == []


def test_projected_workload_keeps_authoritative_load_and_cost_boundary():
    workload = _json(RUNTIME_COST / "projected-workload-v1.json")
    lanes = workload["projected_peak_hour"]["lanes"]

    assert workload["population"] == {
        "active_postings": 100_000_000,
        "configured_boards": 10_000_000,
    }
    monitor_cycles = sum(
        lane["successful_cycles_per_hour"] for lane in lanes if lane["stage"] == "monitor"
    )
    detail_cycles = sum(
        lane["successful_cycles_per_hour"] for lane in lanes if lane["stage"] == "detail"
    )
    assert monitor_cycles == 1_000_000
    assert detail_cycles == 5_000_000
    assert workload["recovery"] == {
        "arrival_multiplier": 2,
        "lost_instances_per_scaling_role": 1,
    }
    assert workload["current_load_hour"]["monthly_traffic_hours"] is None
    assert workload["projected_peak_hour"]["monthly_traffic_hours"] is None
    assert workload["current_budget_reference"] == {
        "known_insufficient": True,
        "monthly_chf": 50,
        "purpose": "funding-shortfall-reference-only",
    }
    assert set(workload["cost_boundary"]["excluded_categories"]) == {
        "backup",
        "control-plane",
        "postgres",
        "r2",
        "telemetry",
        "typesense",
        "web",
    }


def test_live_python_evidence_keeps_unmeasured_values_unknown():
    measurement = _json(RUNTIME_COST / "evidence/python-production-2026-08-29-24h.json")

    assert measurement["capture"]["origin_requests_made"] == 0
    assert measurement["capture"]["read_only"] is True
    assert "browser-child-cpu-and-rss-not-in-process-metrics" in measurement["evidence_gaps"]
    assert "queue-and-redis-resource-use-requires-separate-capture" in measurement["evidence_gaps"]
    for role in measurement["roles"]:
        assert role["resource_scope"] == "root-process"
        assert role["root_process_cpu_seconds"] == role["process_cpu_seconds"]
        assert role["root_peak_rss_bytes_per_instance"] == role["peak_rss_bytes_per_instance"]
        assert role["descendant_process_cpu_seconds"] is None
        assert role["process_tree_cpu_scope"] is None
        assert role["process_tree_cpu_source"] is None
        assert role["process_tree_coverage"] == []
        assert role["process_tree_peak_rss_bytes_per_instance"] is None
        assert role["process_tree_successful_samples"] is None
        if role["execution_class"] == "support":
            assert role["cost_category"] == "runtime-support"
            assert role["discovery_concurrency_per_instance"] is None
            assert role["monitor_concurrency_per_instance"] is None
        else:
            assert role["discovery_concurrency_per_instance"] > 0
            assert (
                0
                < role["monitor_concurrency_per_instance"]
                <= role["discovery_concurrency_per_instance"]
            )
        for lane in role["lanes"]:
            assert lane["origin_attempts"] is None
            assert lane["response_bytes"] is None


def test_committed_pricing_preserves_source_currency_and_whole_skus():
    pricing = _json(RUNTIME_COST / "pricing/hetzner-eu-2026-06-15.json")

    assert pricing["provider"] == "hetzner"
    assert pricing["source_currency"] == "EUR"
    assert pricing["vat_treatment"] == "excluded"
    assert pricing["fx"] == {
        "as_of": "2026-08-27",
        "base": "EUR",
        "quote": "CHF",
        "quote_per_base": 0.9376,
        "source_url": ("https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX:C/2026/04110"),
    }
    assert pricing["scenario_selection"]["current_load_sku"] == "CX43"
    assert pricing["scenario_selection"]["projected_load_sku"] is None
    assert {item["category"] for item in pricing["attributable_monthly_costs"]} == {
        "queue",
        "scheduler",
        "runtime-support",
        "proxy",
    }
    assert all(item["status"] == "unknown" for item in pricing["attributable_monthly_costs"])
    assert all(isinstance(sku["vcpus"], int) for sku in pricing["server_skus"])
    assert all(isinstance(sku["memory_bytes"], int) for sku in pricing["server_skus"])


def _workload() -> dict:
    lanes_current = [
        {"stage": "monitor", "execution_class": "http", "successful_cycles_per_hour": 100},
        {"stage": "detail", "execution_class": "http", "successful_cycles_per_hour": 100},
    ]
    lanes_projected = [
        {"stage": "monitor", "execution_class": "http", "successful_cycles_per_hour": 1000},
        {"stage": "detail", "execution_class": "http", "successful_cycles_per_hour": 1000},
    ]
    return {
        "schema_version": "jobseek.crawler-runtime-workload/v1",
        "revision": "test-v1",
        "current_load_hour": {
            "lanes": lanes_current,
            "lost_instances_per_scaling_role": 1,
            "monthly_traffic_hours": 1,
        },
        "projected_peak_hour": {"lanes": lanes_projected, "monthly_traffic_hours": 1},
        "headroom": {
            "steady_max_utilization": 0.5,
            "recovery_max_utilization": 1,
            "memory_max_utilization": 0.8,
        },
        "recovery": {"arrival_multiplier": 2, "lost_instances_per_scaling_role": 1},
        "required_evidence": [{"key": "mix", "status": "frozen"}],
        "current_budget_reference": {"monthly_chf": 50},
        "cost_boundary": {
            "allowed_categories": [
                "worker",
                "browser",
                "queue",
                "scheduler",
                "runtime-support",
                "proxy",
                "network",
            ],
            "excluded_categories": [
                "postgres",
                "typesense",
                "r2",
                "web",
                "backup",
                "telemetry",
                "control-plane",
            ],
        },
    }


def _measurement() -> dict:
    return {
        "schema_version": "jobseek.crawler-runtime-measurement/v1",
        "measurement_id": "test-python",
        "workload_revision": "test-v1",
        "implementation": "python-playwright",
        "evidence_gaps": [],
        "roles": [
            {
                "role": "http-worker",
                "execution_class": "http",
                "cost_category": "worker",
                "discovery_concurrency_per_instance": 10,
                "monitor_concurrency_per_instance": 5,
                "vcpu_limit_per_instance": 1,
                "memory_limit_bytes_per_instance": 1024,
                "process_cpu_seconds": 360,
                "peak_rss_bytes_per_instance": 256,
                "lanes": [
                    {
                        "stage": "monitor",
                        "execution_class": "http",
                        "successful_cycles": 100,
                        "task_active_seconds": 3600,
                        "origin_attempts": 100,
                        "response_bytes": 100,
                    },
                    {
                        "stage": "detail",
                        "execution_class": "http",
                        "successful_cycles": 100,
                        "task_active_seconds": 3600,
                        "origin_attempts": 100,
                        "response_bytes": 100,
                    },
                ],
            }
        ],
    }


def _pricing() -> dict:
    return {
        "schema_version": "jobseek.crawler-runtime-pricing/v1",
        "revision": "test-prices",
        "provider": "hetzner",
        "source_currency": "EUR",
        "region_group": "eu-central-fsn-nbg-hel",
        "price_effective_at": "2026-06-15T08:00:00+02:00",
        "retrieved_at": "2026-08-29",
        "vat_treatment": "excluded",
        "billing_assumptions": {
            "long_lived_instances_use_monthly_cap": True,
            "hourly_rounding": "whole-hour",
            "current_crawler_sku_evidence": {
                "status": "evidenced",
                "sku": "TEST4",
                "datacenter": "test",
                "source": "test",
            },
        },
        "fx": {
            "base": "EUR",
            "quote": "CHF",
            "quote_per_base": 0.9,
            "as_of": "2026-08-27",
            "source_url": "https://example.com/fx",
        },
        "network": {
            "bytes_per_tb": 1_000_000_000_000,
            "measurement_basis": "crawler-response-bytes",
            "primary_ipv4_per_server": 1,
            "primary_ipv4_monthly_eur": 0.5,
            "traffic_cost_status": "priced",
            "overage_eur_per_tb": 1,
            "source_urls": ["https://example.com/network"],
        },
        "attributable_monthly_costs": [
            {
                "category": category,
                "status": "priced",
                "covered_roles": [],
                "current_sustainable_monthly_eur": 1,
                "projected_load_monthly_eur": 1,
            }
            for category in ("queue", "scheduler", "runtime-support", "proxy")
        ],
        "scenario_selection": {
            "current_load_sku": "TEST4",
            "projected_load_sku": "TEST4",
            "reason": "test",
        },
        "server_skus": [
            {
                "sku": "TEST4",
                "family": "regular-performance",
                "cpu_allocation": "shared",
                "vcpus": 4,
                "memory_bytes": 4096,
                "price_regions": ["NBG"],
                "hourly_eur_excluding_ipv4_vat": 0.02,
                "monthly_eur_excluding_ipv4_vat": 10,
                "included_traffic_tb_per_server": 20,
            }
        ],
        "sources": [
            {"kind": "price", "url": "https://example.com/price", "accessed_on": "2026-08-29"}
        ],
    }


def _committed_process_tree_measurement() -> dict:
    measurement = _json(RUNTIME_COST / "evidence/python-production-2026-08-29-24h.json")
    browser = measurement["roles"][0]
    root_cpu = browser["root_process_cpu_seconds"]
    root_rss = browser["root_peak_rss_bytes_per_instance"]
    browser.update(
        {
            "descendant_process_cpu_seconds": 100.0,
            "peak_rss_bytes_per_instance": root_rss + 1024,
            "process_cpu_seconds": root_cpu + 100.0,
            "process_tree_cpu_scope": "one-crawler-role-container-per-target",
            "process_tree_cpu_source": "container-cgroup-v2",
            "process_tree_coverage": [
                {
                    "boundary_tolerance_seconds": 60,
                    "counter_resets": 0,
                    "coverage_ratio": 0.96,
                    "end_covered": True,
                    "expected_samples": 172800,
                    "failed_samples": 0,
                    "gap_samples": 0,
                    "missing_samples": 6912,
                    "required_coverage_ratio": 0.95,
                    "sampler_restarts": 0,
                    "sample_interval_seconds": 0.5,
                    "start_covered": True,
                    "successful_samples": 165888,
                    "target_id": "browser-worker-1",
                }
            ],
            "process_tree_peak_rss_bytes_per_instance": root_rss + 1024,
            "process_tree_successful_samples": 165888,
            "resource_scope": "process-tree",
        }
    )
    return measurement


def test_model_reports_current_sustainable_budget_separately_from_projected_load():
    result = project_runtime_cost(_workload(), _measurement(), _pricing())

    current = result["comparison_points"]["current_sustainable"]
    projected = result["comparison_points"]["projected_load"]
    assert current["roles"][0]["required_instances"] == 2
    assert current["selected_sku"] == "TEST4"
    assert current["sku_scenarios"][0]["required_servers"] == 1
    assert current["selected_monthly_compute_ipv4_eur_excluding_vat"] == 10.5
    assert current["selected_monthly_compute_ipv4_chf_excluding_vat"] == pytest.approx(9.45)
    assert current["minimum_sustainable_monthly_chf_excluding_vat"] == pytest.approx(13.05)
    assert current["current_budget_shortfall_chf"] == 0
    assert projected["roles"][0]["required_instances"] == 5
    assert projected["sku_scenarios"][0]["required_servers"] == 2
    assert projected["selected_monthly_compute_ipv4_eur_excluding_vat"] == 21
    assert projected["selected_monthly_compute_ipv4_chf_excluding_vat"] == pytest.approx(18.9)
    assert projected["minimum_sustainable_monthly_chf_excluding_vat"] == pytest.approx(22.5)
    assert "current_budget_reference_chf" not in projected
    assert "current_budget_shortfall_chf" not in projected
    assert result["decision_ready"] is True


def test_shared_discovery_pool_does_not_double_count_monitor_and_detail_slots():
    workload = _workload()
    workload["headroom"]["steady_max_utilization"] = 1
    workload["current_load_hour"]["lost_instances_per_scaling_role"] = 0
    measurement = _measurement()
    measurement["roles"][0]["process_cpu_seconds"] = 0
    for lane in measurement["roles"][0]["lanes"]:
        lane["task_active_seconds"] = 180_000

    result = project_runtime_cost(workload, measurement, _pricing())

    role = result["comparison_points"]["current_sustainable"]["roles"][0]
    assert role["steady_shared_discovery_instances"] == 10
    assert role["steady_monitor_subcap_instances"] == 10
    assert role["steady_concurrency_instances"] == 10
    assert role["required_instances"] == 10


@pytest.mark.parametrize("category", ["queue", "scheduler", "runtime-support", "proxy"])
def test_each_fixed_crawler_cost_category_changes_total_or_blocks(category: str):
    baseline = project_runtime_cost(_workload(), _measurement(), _pricing())
    baseline_cost = baseline["comparison_points"]["current_sustainable"][
        "minimum_sustainable_monthly_eur_excluding_vat"
    ]
    changed_pricing = deepcopy(_pricing())
    changed_entry = next(
        item
        for item in changed_pricing["attributable_monthly_costs"]
        if item["category"] == category
    )
    changed_entry["current_sustainable_monthly_eur"] = 7
    changed = project_runtime_cost(_workload(), _measurement(), changed_pricing)
    changed_cost = changed["comparison_points"]["current_sustainable"][
        "minimum_sustainable_monthly_eur_excluding_vat"
    ]
    assert changed_cost == pytest.approx(baseline_cost + 6)

    blocked_pricing = deepcopy(_pricing())
    blocked_entry = next(
        item
        for item in blocked_pricing["attributable_monthly_costs"]
        if item["category"] == category
    )
    blocked_entry["status"] = "unknown"
    blocked_entry["current_sustainable_monthly_eur"] = None
    blocked = project_runtime_cost(_workload(), _measurement(), blocked_pricing)
    assert f"current_sustainable-fixed-cost-unpriced:{category}" in blocked["blockers"]
    assert (
        blocked["comparison_points"]["current_sustainable"][
            "minimum_sustainable_monthly_eur_excluding_vat"
        ]
        is None
    )


def test_missing_fixed_cost_category_is_a_structural_blocker():
    pricing = _pricing()
    pricing["attributable_monthly_costs"] = [
        item for item in pricing["attributable_monthly_costs"] if item["category"] != "queue"
    ]

    result = project_runtime_cost(_workload(), _measurement(), pricing)

    assert "current_sustainable-fixed-cost-category-missing:queue" in result["blockers"]
    assert result["decision_ready"] is False


def test_clearing_text_gaps_cannot_hide_missing_usage_quantities():
    measurement = _measurement()
    measurement["evidence_gaps"] = []
    measurement["roles"][0]["lanes"][0]["origin_attempts"] = None
    measurement["roles"][0]["lanes"][1]["response_bytes"] = None

    result = project_runtime_cost(_workload(), measurement, _pricing())

    assert "origin-attempts-unmeasured:monitor:http" in result["blockers"]
    assert "response-bytes-unmeasured:detail:http" in result["blockers"]
    assert result["decision_ready"] is False


def test_clearing_text_gaps_cannot_hide_root_only_browser_resources():
    workload = _json(RUNTIME_COST / "projected-workload-v1.json")
    measurement = _json(RUNTIME_COST / "evidence/python-production-2026-08-29-24h.json")
    pricing = _json(RUNTIME_COST / "pricing/hetzner-eu-2026-06-15.json")
    measurement["evidence_gaps"] = []
    for item in workload["required_evidence"]:
        item["status"] = "frozen"

    result = project_runtime_cost(workload, measurement, pricing)

    assert "browser-child-cpu-and-rss-not-in-process-metrics" in result["blockers"]
    assert result["decision_ready"] is False


def test_process_tree_schema_requires_integer_conditional_coverage() -> None:
    schema = _json(RUNTIME_COST / "schemas/measurement-v1.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    complete = _committed_process_tree_measurement()

    assert list(validator.iter_errors(complete)) == []

    complete["roles"][0]["process_tree_successful_samples"] = 0.1
    errors = list(validator.iter_errors(complete))
    assert errors
    assert any("integer" in error.message for error in errors)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda role: role.update(process_tree_successful_samples=0.1),
            "process_tree_successful_samples must be integer",
        ),
        (
            lambda role: role.update(process_tree_cpu_scope="host-cgroup"),
            "process-tree CPU scope is invalid",
        ),
        (
            lambda role: role.update(
                peak_rss_bytes_per_instance=1,
                process_tree_peak_rss_bytes_per_instance=1,
            ),
            "process-tree RSS is below root RSS",
        ),
        (
            lambda role: role["process_tree_coverage"][0].update(
                sample_interval_seconds=86400,
                expected_samples=1,
                successful_samples=1,
                missing_samples=0,
                coverage_ratio=1,
            ),
            "sample_interval_seconds exceeds contract maximum",
        ),
        (
            lambda role: role["process_tree_coverage"][0].update(failed_samples=1),
            "contains failed samples",
        ),
        (
            lambda role: role["process_tree_coverage"][0].update(sampler_restarts=1),
            "contains sampler restarts",
        ),
        (
            lambda role: role["process_tree_coverage"][0].update(start_covered=False),
            "does not cover window start",
        ),
    ],
)
def test_model_rejects_adversarial_process_tree_evidence(mutation, message: str) -> None:
    workload = _json(RUNTIME_COST / "projected-workload-v1.json")
    measurement = _committed_process_tree_measurement()
    pricing = _json(RUNTIME_COST / "pricing/hetzner-eu-2026-06-15.json")
    mutation(measurement["roles"][0])

    with pytest.raises(ModelError, match=message):
        project_runtime_cost(workload, measurement, pricing)


def test_huge_response_volume_changes_network_cost_and_complete_total():
    baseline = project_runtime_cost(_workload(), _measurement(), _pricing())
    baseline_cost = baseline["comparison_points"]["current_sustainable"][
        "minimum_sustainable_monthly_eur_excluding_vat"
    ]
    measurement = _measurement()
    for lane in measurement["roles"][0]["lanes"]:
        lane["response_bytes"] = 10**18

    result = project_runtime_cost(_workload(), measurement, _pricing())

    current = result["comparison_points"]["current_sustainable"]
    scenario = current["sku_scenarios"][0]
    assert current["monthly_response_bytes"] == 2 * 10**18
    assert scenario["monthly_network_traffic_eur_excluding_vat"] > 1900
    assert current["minimum_sustainable_monthly_eur_excluding_vat"] > baseline_cost + 1900


def test_observed_support_roles_must_be_covered_by_runtime_support_cost():
    measurement = _measurement()
    for role in ("python-drain", "python-exporter"):
        measurement["roles"].append(
            {
                "role": role,
                "execution_class": "support",
                "cost_category": "runtime-support",
                "lanes": [],
            }
        )

    blocked = project_runtime_cost(_workload(), measurement, _pricing())

    assert "runtime-support-role-uncovered:python-drain" in blocked["blockers"]
    assert "runtime-support-role-uncovered:python-exporter" in blocked["blockers"]
    assert blocked["decision_ready"] is False

    pricing = _pricing()
    support_entry = next(
        item
        for item in pricing["attributable_monthly_costs"]
        if item["category"] == "runtime-support"
    )
    support_entry["covered_roles"] = ["python-drain", "python-exporter"]
    complete = project_runtime_cost(_workload(), measurement, pricing)
    assert complete["decision_ready"] is True


def test_model_rejects_changed_crawler_cost_boundary():
    workload = deepcopy(_workload())
    workload["cost_boundary"]["allowed_categories"].append("postgres")

    with pytest.raises(ModelError, match="cost categories differ"):
        project_runtime_cost(workload, _measurement(), _pricing())


def test_model_rejects_non_hetzner_pricing():
    pricing = deepcopy(_pricing())
    pricing["provider"] = "other-cloud"

    with pytest.raises(ModelError, match="provider must be Hetzner"):
        project_runtime_cost(_workload(), _measurement(), pricing)


def test_unpriced_traffic_and_unselected_projected_sku_remain_blockers():
    pricing = deepcopy(_pricing())
    pricing["network"]["traffic_cost_status"] = "blocked-until-attributable-bytes"
    pricing["scenario_selection"]["projected_load_sku"] = None

    result = project_runtime_cost(_workload(), _measurement(), pricing)

    assert result["comparison_points"]["projected_load"]["selected_sku"] is None
    assert result["comparison_points"]["current_sustainable"]["cost_complete"] is False
    assert (
        result["comparison_points"]["current_sustainable"][
            "minimum_sustainable_monthly_chf_excluding_vat"
        ]
        is None
    )
    assert (
        result["comparison_points"]["current_sustainable"]["current_budget_shortfall_chf"] is None
    )
    assert "current_sustainable-network-traffic-cost-unpriced" in result["blockers"]
    assert "projected_load-hetzner-sku-unselected" in result["blockers"]
    assert result["decision_ready"] is False


def test_prometheus_capture_is_read_only_and_sanitized():
    targets = {
        "schema_version": "jobseek.crawler-runtime-capture-targets/v1",
        "revision": "test-targets",
        "workload_revision": "test-v1",
        "implementation": "python-playwright",
        "targets": [
            {
                "id": "worker-a",
                "instance": "worker-1",
                "role": "http-worker",
                "execution_class": "http",
                "cost_category": "worker",
                "discovery_concurrency": 3,
                "monitor_concurrency": 2,
                "vcpu_limit": 1,
                "memory_limit_bytes": 1024,
            }
        ],
    }
    queries: list[str] = []

    def fake_query(expression: str, _at: datetime) -> list[dict]:
        queries.append(expression)
        if any(metric in expression for metric in _ATTRIBUTION_METRICS):
            return []
        if "crawler_build_info" in expression:
            return [{"metric": {"version": "1.2.3"}, "value": [0, "1"]}]
        if "process_resident_memory_bytes" in expression:
            value = 128
        elif "process_cpu_seconds_total" in expression:
            value = 12
        elif 'status="succeeded"' in expression:
            value = 10
        elif "crawler_tasks_total" in expression:
            value = 12
        elif "duration_seconds_sum" in expression:
            value = 25
        elif "retry" in expression:
            value = 2
        else:
            value = 0
        return [{"metric": {}, "value": [0, str(value)]}]

    result = capture_prometheus_measurement(
        targets,
        query=fake_query,
        end_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        window_seconds=3600,
        source_revision="abc123",
    )

    assert result["capture"] == {
        "origin_requests_made": 0,
        "read_only": True,
        "source": "prometheus-read-api",
        "targets_revision": "test-targets",
    }
    assert result["source_releases"] == ["1.2.3"]
    assert result["roles"][0]["process_cpu_seconds"] == 12
    assert result["roles"][0]["peak_rss_bytes_per_instance"] == 128
    assert result["roles"][0]["resource_scope"] == "root-process"
    assert result["roles"][0]["root_process_cpu_seconds"] == 12
    assert result["roles"][0]["descendant_process_cpu_seconds"] is None
    assert result["roles"][0]["discovery_concurrency_per_instance"] == 3
    assert result["roles"][0]["monitor_concurrency_per_instance"] == 2
    assert all(
        "http://" not in query.lower() and "https://" not in query.lower() for query in queries
    )
    assert "browser-child-cpu-and-rss-not-in-process-metrics" in result["evidence_gaps"]


def _attributed_http_query(end_at: datetime, fault: str | None = None):
    start_at = datetime.fromtimestamp(end_at.timestamp() - 3600, tz=UTC)

    def fake_query(expression: str, at: datetime) -> list[dict]:
        is_start = at == start_at
        if "crawler_runtime_capability_executions_total" in expression:
            stage = "monitor" if 'stage="monitor"' in expression else "detail"
            if "resets(" in expression:
                capabilities = (
                    [("greenhouse", "success"), ("sitemap", "error")]
                    if stage == "monitor"
                    else [("json-ld", "success")]
                )
                rows = [
                    {
                        "metric": {"capability": capability, "outcome": outcome},
                        "value": [
                            0,
                            (
                                "inf"
                                if fault == "capability-nonfinite-reset" and index == 0
                                else "0"
                            ),
                        ],
                    }
                    for index, (capability, outcome) in enumerate(capabilities)
                ]
                if fault == "capability-extra-label":
                    rows[0]["metric"]["unexpected"] = "value"
                return rows
            values = (
                [("greenhouse", "success", 3, 8), ("sitemap", "error", 1, 2)]
                if stage == "monitor"
                else [("json-ld", "success", 4, 11)]
            )
            rows = [
                {
                    "metric": {"capability": capability, "outcome": outcome},
                    "value": [0, str(start if is_start else end)],
                }
                for capability, outcome, start, end in values
            ]
            if fault == "capability-negative-start" and is_start:
                rows[0]["value"][1] = "-1"
            if fault == "capability-fractional-end" and not is_start:
                rows[0]["value"][1] = "8.5"
            if fault == "capability-invalid-outcome":
                rows[0]["metric"]["outcome"] = "not-a-runtime-outcome"
            if fault == "capability-extra-label":
                rows[0]["metric"]["unexpected"] = "value"
            return rows
        if "crawler_runtime_executions_total" in expression:
            stage = "monitor" if 'stage="monitor"' in expression else "detail"
            if "resets(" in expression:
                outcomes = ("success", "error") if stage == "monitor" else ("success",)
                rows = [{"metric": {"outcome": outcome}, "value": [0, "0"]} for outcome in outcomes]
                if fault == "capability-invalid-outcome":
                    rows[0]["metric"]["outcome"] = "not-a-runtime-outcome"
                return rows
            values = (
                [("success", 10, 15), ("error", 2, 3)]
                if stage == "monitor"
                else [("success", 20, 27)]
            )
            if fault == "capability-mismatch" and stage == "monitor":
                values[0] = ("success", 10, 16)
            rows = [
                {
                    "metric": {"outcome": outcome},
                    "value": [0, str(start if is_start else end)],
                }
                for outcome, start, end in values
            ]
            if fault == "capability-invalid-outcome":
                rows[0]["metric"]["outcome"] = "not-a-runtime-outcome"
            return rows
        if any(
            metric in expression
            for metric in (
                "crawler_runtime_origin_attempts_total",
                "crawler_runtime_origin_outcomes_total",
                "crawler_runtime_response_body_bytes_total",
            )
        ):
            if fault == "missing-start" and is_start and "origin_attempts" in expression:
                return []
            if "resets(" in expression:
                reset_value = {
                    "negative-reset": "-1",
                    "fractional-reset": "0.5",
                    "nonfinite-reset": "inf",
                    "counter-reset": "1",
                }.get(fault or "", "0")
                rows = [
                    {
                        "metric": {},
                        "value": [0, reset_value],
                    }
                ]
                if fault == "duplicate-reset":
                    return [rows[0], deepcopy(rows[0])]
                return rows
            proxy = 'egress="proxy"' in expression
            if "origin_attempts" in expression:
                start, end = (5, 8) if proxy else (10, 17)
                if fault == "negative-start" and is_start:
                    start = -1
                if fault == "negative-end" and not is_start:
                    end = -1
                if fault == "fractional-boundary" and not is_start:
                    end = 17.5
                if fault == "nonfinite-boundary" and not is_start:
                    return [{"metric": {}, "value": [0, "nan"]}]
                if fault == "malformed-boundary" and not is_start:
                    return [{"metric": {}, "value": [0]}]
            elif 'outcome="response"' in expression:
                start, end = (5, 7) if proxy else (9, 15)
                if fault == "conservation-mismatch" and not proxy:
                    end += 1
            elif 'outcome="transport_error"' in expression:
                start, end = (0, 1) if proxy else (1, 2)
            else:
                start, end = (50, 350) if proxy else (100, 800)
            row = {"metric": {}, "value": [0, str(start if is_start else end)]}
            if fault == "duplicate-boundary" and not is_start and "origin_attempts" in expression:
                return [row, deepcopy(row)]
            if fault == "labeled-scalar" and not is_start and "origin_attempts" in expression:
                row["metric"] = {"unexpected": "value"}
            return [row]
        if "crawler_build_info" in expression:
            return [{"metric": {"version": "3.0.0"}, "value": [0, "1"]}]
        if "process_resident_memory_bytes" in expression:
            value = 128
        elif "process_cpu_seconds_total" in expression:
            value = 12
        elif 'status="succeeded"' in expression:
            value = 10
        elif "crawler_tasks_total" in expression:
            value = 12
        elif "duration_seconds_sum" in expression:
            value = 25
        else:
            value = 0
        return [{"metric": {}, "value": [0, str(value)]}]

    return fake_query


def _one_http_target() -> dict:
    return {
        "schema_version": "jobseek.crawler-runtime-capture-targets/v1",
        "revision": "attributed-http-targets",
        "workload_revision": "test-v1",
        "implementation": "python-playwright",
        "targets": [
            {
                "id": "worker-a",
                "instance": "worker-1",
                "role": "http-worker",
                "execution_class": "http",
                "cost_category": "worker",
                "discovery_concurrency": 3,
                "monitor_concurrency": 2,
                "vcpu_limit": 1,
                "memory_limit_bytes": 1024,
            }
        ],
    }


def test_prometheus_capture_promotes_only_conserved_complete_http_egress() -> None:
    end_at = datetime(2026, 8, 29, 12, tzinfo=UTC)
    result = capture_prometheus_measurement(
        _one_http_target(),
        query=_attributed_http_query(end_at),
        end_at=end_at,
        window_seconds=3600,
        source_revision="attributed123",
    )

    role = result["roles"][0]
    assert role["egress_coverage"] == [
        {
            "stage": stage,
            "execution_class": "http",
            "egress": egress,
            "scope": "shared-http-transport",
            "expected_targets": 1,
            "complete_targets": 1,
            "complete": True,
            "counter_resets": 0,
            "origin_attempts": 7 if egress == "direct" else 3,
            "responses": 6 if egress == "direct" else 2,
            "transport_errors": 1,
            "response_bytes": 700 if egress == "direct" else 300,
        }
        for stage in ("detail", "monitor")
        for egress in ("direct", "proxy")
    ]
    for lane in role["lanes"]:
        assert lane["origin_attempts"] == 10
        assert lane["response_bytes"] == 1000
    assert "origin-attempts-unmeasured:monitor:http" not in result["evidence_gaps"]
    assert "response-bytes-unmeasured:detail:http" not in result["evidence_gaps"]
    assert "origin-attempts-unmeasured:monitor:browser" in result["evidence_gaps"]
    assert "browser-transport-unmeasured:lightpanda" in result["evidence_gaps"]
    assert "browser-cgroup-cost-unmeasured:lightpanda" in result["evidence_gaps"]
    assert "browser-transport-unmeasured:chromium" in result["evidence_gaps"]
    assert "browser-cgroup-cost-unmeasured:chromium" in result["evidence_gaps"]
    assert role["capability_mix"]["coverage"] == [
        {"stage": "detail", "expected_targets": 1, "complete_targets": 1, "complete": True},
        {"stage": "monitor", "expected_targets": 1, "complete_targets": 1, "complete": True},
    ]
    assert {tuple(item.values()) for item in role["capability_mix"]["executions"]} == {
        ("detail", "json-ld", "success", 7),
        ("monitor", "greenhouse", "success", 5),
        ("monitor", "sitemap", "error", 1),
    }
    schema = _json(RUNTIME_COST / "schemas/measurement-v1.schema.json")
    assert (
        list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(result)) == []
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing-start",
        "negative-start",
        "negative-end",
        "fractional-boundary",
        "nonfinite-boundary",
        "malformed-boundary",
        "duplicate-boundary",
        "labeled-scalar",
        "negative-reset",
        "fractional-reset",
        "nonfinite-reset",
        "duplicate-reset",
        "counter-reset",
        "conservation-mismatch",
    ],
)
def test_prometheus_capture_fails_closed_for_incomplete_http_egress(fault: str) -> None:
    end_at = datetime(2026, 8, 29, 12, tzinfo=UTC)
    result = capture_prometheus_measurement(
        _one_http_target(),
        query=_attributed_http_query(end_at, fault),
        end_at=end_at,
        window_seconds=3600,
        source_revision=f"attributed-{fault}",
    )

    assert all(lane["origin_attempts"] is None for lane in result["roles"][0]["lanes"])
    assert all(lane["response_bytes"] is None for lane in result["roles"][0]["lanes"])
    assert "origin-attempts-unmeasured:monitor:http" in result["evidence_gaps"]


@pytest.mark.parametrize(
    "fault",
    [
        "capability-mismatch",
        "capability-negative-start",
        "capability-fractional-end",
        "capability-nonfinite-reset",
        "capability-malformed-row",
        "capability-invalid-outcome",
        "capability-extra-label",
    ],
)
def test_prometheus_capture_discards_invalid_capability_mix(fault: str) -> None:
    end_at = datetime(2026, 8, 29, 12, tzinfo=UTC)
    query = _attributed_http_query(end_at, fault)

    def malformed_capability_query(expression: str, at: datetime) -> list[dict]:
        rows = query(expression, at)
        if fault == "capability-malformed-row" and "capability_executions" in expression:
            return [{"metric": {"outcome": "success"}, "value": [0, "1"]}]
        return rows

    result = capture_prometheus_measurement(
        _one_http_target(),
        query=malformed_capability_query,
        end_at=end_at,
        window_seconds=3600,
        source_revision=fault,
    )

    mix = result["roles"][0]["capability_mix"]
    monitor = next(item for item in mix["coverage"] if item["stage"] == "monitor")
    assert monitor == {
        "stage": "monitor",
        "expected_targets": 1,
        "complete_targets": 0,
        "complete": False,
    }
    assert all(item["stage"] != "monitor" for item in mix["executions"])


def test_prometheus_capture_rejects_mixed_execution_classes_in_one_role() -> None:
    targets = _one_http_target()
    browser_target = deepcopy(targets["targets"][0])
    browser_target.update(id="browser-a", instance="browser-1", execution_class="browser")
    targets["targets"].append(browser_target)
    end_at = datetime(2026, 8, 29, 12, tzinfo=UTC)

    with pytest.raises(ModelError, match="identical limits, category, and execution class"):
        capture_prometheus_measurement(
            targets,
            query=_attributed_http_query(end_at),
            end_at=end_at,
            window_seconds=3600,
            source_revision="mixed-execution-class",
        )


def test_prometheus_capture_rejects_duplicate_lanes_across_roles() -> None:
    targets = _one_http_target()
    duplicate_lane_target = deepcopy(targets["targets"][0])
    duplicate_lane_target.update(id="worker-b", instance="worker-2", role="http-worker-b")
    targets["targets"].append(duplicate_lane_target)
    end_at = datetime(2026, 8, 29, 12, tzinfo=UTC)

    with pytest.raises(ModelError, match="duplicate workload lane"):
        capture_prometheus_measurement(
            targets,
            query=_attributed_http_query(end_at),
            end_at=end_at,
            window_seconds=3600,
            source_revision="duplicate-lane",
        )


def test_prometheus_capture_includes_complete_browser_process_tree() -> None:
    targets = {
        "schema_version": "jobseek.crawler-runtime-capture-targets/v1",
        "revision": "browser-tree-targets",
        "workload_revision": "test-v1",
        "implementation": "python-playwright",
        "targets": [
            {
                "id": "browser-a",
                "instance": "browser-1",
                "role": "browser-worker",
                "execution_class": "browser",
                "cost_category": "browser",
                "discovery_concurrency": 7,
                "monitor_concurrency": 4,
                "vcpu_limit": 3,
                "memory_limit_bytes": 4096,
            }
        ],
    }

    end_at = datetime(2026, 8, 29, 12, tzinfo=UTC)
    start_at = datetime(2026, 8, 29, 11, tzinfo=UTC)
    queries: list[str] = []
    registry = CollectorRegistry()
    exposed_samples = Counter(
        "crawler_runtime_process_tree_samples_total",
        "test process-tree sampler observations",
        ["outcome"],
        registry=registry,
    )
    _seed_process_tree_sample_outcomes(exposed_samples)
    exposed_samples.labels(outcome="success").inc(100)
    start_exposition = generate_latest(registry)
    exposed_samples.labels(outcome="success").inc(7000)
    end_exposition = generate_latest(registry)
    assert (
        _exposed_counter_value(
            start_exposition,
            "crawler_runtime_process_tree_samples_total",
            "failure",
        )
        == 0
    )

    def fake_query(expression: str, at: datetime) -> list[dict]:
        queries.append(expression)
        if any(metric in expression for metric in _ATTRIBUTION_METRICS):
            return []
        if "crawler_build_info" in expression:
            return [{"metric": {"version": "2.0.0"}, "value": [0, "1"]}]
        if "crawler_runtime_process_tree_sample_interval_seconds" in expression:
            value = 0.5
        elif "resets(crawler_runtime_process_tree_samples_total" in expression:
            value = 0
        elif (
            "crawler_runtime_process_tree_samples_total" in expression
            and 'outcome="success"' in expression
        ):
            value = _exposed_counter_value(
                start_exposition if at == start_at else end_exposition,
                "crawler_runtime_process_tree_samples_total",
                "success",
            )
        elif (
            "crawler_runtime_process_tree_samples_total" in expression
            and 'outcome="failure"' in expression
        ):
            value = _exposed_counter_value(
                start_exposition if at == start_at else end_exposition,
                "crawler_runtime_process_tree_samples_total",
                "failure",
            )
        elif "crawler_runtime_process_tree_sampling_gaps_total" in expression:
            value = 0
        elif "crawler_runtime_process_tree_sampler_starts_total" in expression:
            value = 1
        elif "crawler_runtime_process_tree_last_sample_unixtime_seconds" in expression:
            value = at.timestamp()
        elif "crawler_runtime_process_tree_cpu_seconds_total" in expression:
            value = 100 if at == start_at else 142
        elif "crawler_runtime_process_tree_resident_memory_bytes" in expression:
            value = 512
        elif "process_resident_memory_bytes" in expression:
            value = 128
        elif "process_cpu_seconds_total" in expression:
            value = 12
        elif 'status="succeeded"' in expression:
            value = 10
        elif "crawler_tasks_total" in expression:
            value = 12
        elif "duration_seconds_sum" in expression:
            value = 25
        else:
            value = 0
        return [{"metric": {}, "value": [0, str(value)]}]

    result = capture_prometheus_measurement(
        targets,
        query=fake_query,
        end_at=end_at,
        window_seconds=3600,
        source_revision="tree123",
    )

    role = result["roles"][0]
    assert role["resource_scope"] == "process-tree"
    assert role["root_process_cpu_seconds"] == 12
    assert role["descendant_process_cpu_seconds"] == 30
    assert role["process_cpu_seconds"] == 42
    assert role["process_tree_cpu_source"] == "container-cgroup-v2"
    assert role["process_tree_cpu_scope"] == "one-crawler-role-container-per-target"
    assert role["root_peak_rss_bytes_per_instance"] == 128
    assert role["process_tree_peak_rss_bytes_per_instance"] == 512
    assert role["peak_rss_bytes_per_instance"] == 512
    assert role["process_tree_successful_samples"] == 7000
    assert role["process_tree_coverage"] == [
        {
            "boundary_tolerance_seconds": 60,
            "counter_resets": 0,
            "coverage_ratio": pytest.approx(7000 / 7200),
            "end_covered": True,
            "expected_samples": 7200,
            "failed_samples": 0,
            "gap_samples": 0,
            "missing_samples": 200,
            "required_coverage_ratio": 0.95,
            "sampler_restarts": 0,
            "sample_interval_seconds": 0.5,
            "start_covered": True,
            "successful_samples": 7000,
            "target_id": "browser-a",
        }
    ]
    assert any("crawler_runtime_process_tree_resident_memory_bytes" in item for item in queries)
    assert all("process_tree_peak_resident_memory_bytes" not in item for item in queries)
    assert "browser-child-cpu-and-rss-not-in-process-metrics" not in result["evidence_gaps"]


def test_prometheus_capture_rejects_partial_process_tree_role_coverage() -> None:
    targets = {
        "schema_version": "jobseek.crawler-runtime-capture-targets/v1",
        "revision": "partial-browser-tree-targets",
        "workload_revision": "test-v1",
        "implementation": "python-playwright",
        "targets": [
            {
                "id": f"browser-{index}",
                "instance": f"browser-{index}",
                "role": "browser-worker",
                "execution_class": "browser",
                "cost_category": "browser",
                "discovery_concurrency": 7,
                "monitor_concurrency": 4,
                "vcpu_limit": 3,
                "memory_limit_bytes": 4096,
            }
            for index in (1, 2)
        ],
    }

    end_at = datetime(2026, 8, 29, 12, tzinfo=UTC)
    start_at = datetime(2026, 8, 29, 11, tzinfo=UTC)

    def fake_query(expression: str, at: datetime) -> list[dict]:
        if any(metric in expression for metric in _ATTRIBUTION_METRICS):
            return []
        if "crawler_build_info" in expression:
            return [{"metric": {"version": "2.0.0"}, "value": [0, "1"]}]
        is_tree_metric = "crawler_runtime_" in expression
        if is_tree_metric and 'instance="browser-2"' in expression:
            return []
        if "crawler_runtime_process_tree_sample_interval_seconds" in expression:
            value = 0.5
        elif "resets(crawler_runtime_process_tree_samples_total" in expression:
            value = 0
        elif (
            "crawler_runtime_process_tree_samples_total" in expression
            and 'outcome="success"' in expression
        ):
            value = 100 if at == start_at else 7100
        elif (
            "crawler_runtime_process_tree_samples_total" in expression
            and 'outcome="failure"' in expression
        ) or "crawler_runtime_process_tree_sampling_gaps_total" in expression:
            value = 0
        elif "crawler_runtime_process_tree_sampler_starts_total" in expression:
            value = 1
        elif "crawler_runtime_process_tree_last_sample_unixtime_seconds" in expression:
            value = at.timestamp()
        elif "crawler_runtime_process_tree_cpu_seconds_total" in expression:
            value = 100 if at == start_at else 142
        elif "crawler_runtime_process_tree_resident_memory_bytes" in expression:
            value = 512
        elif "process_resident_memory_bytes" in expression:
            value = 128
        elif "process_cpu_seconds_total" in expression:
            value = 12
        elif 'status="succeeded"' in expression:
            value = 10
        elif "crawler_tasks_total" in expression:
            value = 12
        elif "duration_seconds_sum" in expression:
            value = 25
        else:
            value = 0
        return [{"metric": {}, "value": [0, str(value)]}]

    result = capture_prometheus_measurement(
        targets,
        query=fake_query,
        end_at=end_at,
        window_seconds=3600,
        source_revision="partial123",
    )

    role = result["roles"][0]
    assert role["resource_scope"] == "root-process"
    assert role["process_cpu_seconds"] == 24
    assert role["peak_rss_bytes_per_instance"] == 128
    assert role["descendant_process_cpu_seconds"] is None
    assert "browser-child-cpu-and-rss-not-in-process-metrics" in result["evidence_gaps"]


@pytest.mark.parametrize(
    "fault",
    [
        "one-sample",
        "fractional-samples",
        "failed-sample",
        "missing-failure-series",
        "counter-reset",
        "sampler-restart",
        "sampling-gap",
        "missing-start",
        "missing-end",
        "tree-cpu-below-root",
        "tree-rss-below-root",
    ],
)
def test_prometheus_capture_fails_closed_for_incomplete_tree_window(fault: str) -> None:
    targets = {
        "schema_version": "jobseek.crawler-runtime-capture-targets/v1",
        "revision": "adversarial-browser-tree-targets",
        "workload_revision": "test-v1",
        "implementation": "python-playwright",
        "targets": [
            {
                "id": "browser-a",
                "instance": "browser-1",
                "role": "browser-worker",
                "execution_class": "browser",
                "cost_category": "browser",
                "discovery_concurrency": 7,
                "monitor_concurrency": 4,
                "vcpu_limit": 3,
                "memory_limit_bytes": 4096,
            }
        ],
    }
    end_at = datetime(2026, 8, 29, 12, tzinfo=UTC)
    start_at = datetime(2026, 8, 29, 11, tzinfo=UTC)

    def fake_query(expression: str, at: datetime) -> list[dict]:
        if any(metric in expression for metric in _ATTRIBUTION_METRICS):
            return []
        if "crawler_build_info" in expression:
            return [{"metric": {"version": "2.0.0"}, "value": [0, "1"]}]
        if "crawler_runtime_process_tree_sample_interval_seconds" in expression:
            value = 0.5
        elif "resets(crawler_runtime_process_tree_samples_total" in expression:
            value = 1 if fault == "counter-reset" else 0
        elif (
            "crawler_runtime_process_tree_samples_total" in expression
            and 'outcome="success"' in expression
        ):
            if at == start_at:
                value = 100
            elif fault == "one-sample":
                value = 101
            elif fault == "fractional-samples":
                value = 7100.5
            else:
                value = 7100
        elif (
            "crawler_runtime_process_tree_samples_total" in expression
            and 'outcome="failure"' in expression
        ):
            if fault == "missing-failure-series":
                return []
            value = 1 if fault == "failed-sample" and at == end_at else 0
        elif "crawler_runtime_process_tree_sampling_gaps_total" in expression:
            value = 1 if fault == "sampling-gap" and at == end_at else 0
        elif "crawler_runtime_process_tree_sampler_starts_total" in expression:
            value = 2 if fault == "sampler-restart" and at == end_at else 1
        elif "crawler_runtime_process_tree_last_sample_unixtime_seconds" in expression:
            if (fault == "missing-start" and at == start_at) or (
                fault == "missing-end" and at == end_at
            ):
                value = at.timestamp() - 61
            else:
                value = at.timestamp()
        elif "crawler_runtime_process_tree_cpu_seconds_total" in expression:
            value = 100 if at == start_at else (110 if fault == "tree-cpu-below-root" else 142)
        elif "crawler_runtime_process_tree_resident_memory_bytes" in expression:
            value = 64 if fault == "tree-rss-below-root" else 512
        elif "process_resident_memory_bytes" in expression:
            value = 128
        elif "process_cpu_seconds_total" in expression:
            value = 12
        elif 'status="succeeded"' in expression:
            value = 10
        elif "crawler_tasks_total" in expression:
            value = 12
        elif "duration_seconds_sum" in expression:
            value = 25
        else:
            value = 0
        return [{"metric": {}, "value": [0, str(value)]}]

    result = capture_prometheus_measurement(
        targets,
        query=fake_query,
        end_at=end_at,
        window_seconds=3600,
        source_revision=f"fault-{fault}",
    )

    role = result["roles"][0]
    assert role["resource_scope"] == "root-process"
    assert role["process_cpu_seconds"] == 12
    assert role["peak_rss_bytes_per_instance"] == 128
    assert role["process_tree_successful_samples"] is None
    assert "browser-child-cpu-and-rss-not-in-process-metrics" in result["evidence_gaps"]
