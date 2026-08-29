"""Crawler-only Python versus Go runtime cost foundation tests."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from src.runtime_cost.model import ModelError, project_runtime_cost
from src.runtime_cost.prometheus import capture_prometheus_measurement

ROOT = Path(__file__).parents[1]
RUNTIME_COST = ROOT / "runtime-cost"


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


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
    assert result["roles"][0]["discovery_concurrency_per_instance"] == 3
    assert result["roles"][0]["monitor_concurrency_per_instance"] == 2
    assert all("http" not in query.lower() or "crawler_http_retry" in query for query in queries)
    assert "browser-child-cpu-and-rss-not-in-process-metrics" in result["evidence_gaps"]
