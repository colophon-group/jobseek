from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CRAWLER_ROOT = ROOT / "apps/crawler"


def _alert_rules() -> list[dict]:
    groups = yaml.safe_load((CRAWLER_ROOT / "alerts.yaml").read_text())["groups"]
    return [rule for group in groups for rule in group["rules"]]


def _dashboard_panel(title: str) -> dict:
    dashboard = json.loads((CRAWLER_ROOT / "grafana-dashboard.json").read_text())
    for panel in dashboard["panels"]:
        if panel.get("title") == title:
            return panel
    raise AssertionError(f"missing Grafana panel {title!r}")


def _alert_rule(name: str) -> dict:
    for rule in _alert_rules():
        if rule.get("alert") == name:
            return rule
    raise AssertionError(f"missing alert rule {name!r}")


def test_dashboard_surfaces_deadletter_depth() -> None:
    panel = _dashboard_panel("Dead-letter Depth")

    assert panel["type"] == "stat"
    assert panel["gridPos"] == {"h": 8, "w": 4, "x": 20, "y": 44}
    assert panel["targets"][0]["expr"] == ("max by (wtype) (crawler_inflight_deadletter_depth)")
    assert panel["targets"][0]["legendFormat"] == "{{wtype}}"
    assert panel["fieldConfig"]["defaults"]["thresholds"]["steps"] == [
        {"color": "green", "value": None},
        {"color": "red", "value": 1},
    ]
    assert "ZRANGE deadletter:simple" in panel["description"]
    assert "ZRANGE deadletter:browser" in panel["description"]


def test_alert_fires_when_deadletter_queue_stays_nonempty() -> None:
    rule = _alert_rule("DeadletterQueueNotEmpty")

    assert rule["expr"] == "max by (wtype) (crawler_inflight_deadletter_depth) > 0"
    assert rule["for"] == "1h"
    assert rule["labels"] == {
        "severity": "medium",
        "service": "crawler",
        "owner": "codex-error-review",
        "route": "codex-daily",
    }
    assert "crawler_inflight_deadletter_depth" in rule["annotations"]["description"]
    assert rule["annotations"]["runbook"].endswith(
        "docs/03-crawler-architecture.md#inflight-leases-and-dead-letter-recovery"
    )


def test_upstream_host_circuit_alert_groups_by_real_origin() -> None:
    rule = _alert_rule("UpstreamHostCircuitOpen")

    assert rule["expr"] == "max by (egress_host) (crawler_host_circuit_state) > 0"
    assert rule["for"] == "5m"
    assert rule["labels"] == {
        "severity": "medium",
        "service": "crawler",
        "owner": "codex-error-review",
        "route": "codex-daily",
    }
    assert rule["annotations"]["runbook"].endswith("docs/03-crawler-architecture.md")


def test_exporter_alert_selects_only_exporter_target() -> None:
    rule = _alert_rule("ExporterStale")

    assert rule["expr"] == ('time() - crawler_exporter_last_flush_ts{instance="exporter"} > 900')
    assert rule["labels"]["owner"] == "codex-error-review"
    assert rule["labels"]["route"] == "codex-daily"


def test_cdc_safety_alerts_route_to_daily_error_review() -> None:
    delayed = _alert_rule("CdcWriterCutoffDelayed")
    unknown = _alert_rule("CdcWriterIdentityUnavailable")
    schema = _alert_rule("CrossStoreReconciliationSchemaMissing")
    failed = _alert_rule("CrossStoreReconciliationFailed")
    stale = _alert_rule("CrossStoreReconciliationStale")
    drift = _alert_rule("CrossStoreReconciliationDrift")
    stuck = _alert_rule("CrossStoreReconciliationRunStuck")

    assert "crawler_exporter_cdc_cutoff_delay_seconds" in delayed["expr"]
    assert "crawler_exporter_cdc_unknown_writers_total" in unknown["expr"]
    assert "schema_ready" in schema["expr"]
    assert "last_attempt_success" in failed["expr"]
    assert "last_success_unixtime" in stale["expr"]
    assert "last_unresolved" in drift["expr"]
    assert "stuck_runs" in stuck["expr"]
    for rule in (delayed, unknown, schema, failed, stale, drift, stuck):
        assert rule["labels"]["severity"] == "high"
        assert rule["labels"]["owner"] == "codex-error-review"
        assert rule["labels"]["route"] == "codex-daily"
    assert delayed["annotations"]["runbook"].endswith(
        "docs/03-crawler-architecture.md#commit-safe-posting-cdc"
    )
    assert unknown["annotations"]["runbook"].endswith(
        "docs/03-crawler-architecture.md#commit-safe-posting-cdc"
    )
    for rule in (schema, failed, stale, drift, stuck):
        assert rule["annotations"]["runbook"].endswith(
            "docs/03-crawler-architecture.md#cross-store-reconciliation"
        )


def test_fleet_alerts_cover_all_hosts_backups_and_core_services() -> None:
    names = {rule["alert"] for rule in _alert_rules()}
    assert {
        "CrawlerHostMetricsMissing",
        "PostgresqlHostMetricsMissing",
        "TypesenseHostMetricsMissing",
        "DiskNearFull",
        "InodesNearFull",
        "DataBackupFailed",
        "DataBackupStale",
        "PostgreSQLUnavailable",
        "PostgreSQLDataVolumeHeadroomLow",
        "PostgreSQLCheckpointPressure",
        "PostgreSQLSharedMemoryPressure",
        "PostgreSQLArchiveFailure",
        "TypesenseUnavailable",
        "TypesenseTunnelUnavailable",
        "RequiredHostUnitInactive",
        "RequiredContainerUnavailable",
        "HostRebootRequired",
    } <= names


def test_postgresql_capacity_alert_uses_current_and_forecast_headroom() -> None:
    rule = _alert_rule("PostgreSQLDataVolumeHeadroomLow")

    assert 'host_role="postgresql"' in rule["expr"]
    assert 'fstype="xfs"' in rule["expr"]
    assert "< 0.25" in rule["expr"]
    assert "predict_linear" in rule["expr"]
    assert "jobseek_postgresql_database_bytes" in rule["expr"]
    assert "30 * 24 * 60 * 60" in rule["expr"]
    assert rule["for"] == "6h"
    assert rule["labels"] == {
        "severity": "high",
        "service": "postgresql",
        "owner": "codex-error-review",
        "route": "codex-daily",
    }
    assert rule["annotations"]["runbook"].endswith(
        "docs/16-hetzner-maintenance.md#postgresql-capacity-and-checkpoint-pressure"
    )


def test_postgresql_checkpoint_alert_requires_requested_dominance() -> None:
    rule = _alert_rule("PostgreSQLCheckpointPressure")

    assert "jobseek_postgresql_checkpoints_requested_total" in rule["expr"]
    assert "jobseek_postgresql_checkpoints_timed_total" in rule["expr"]
    assert "[6h]" in rule["expr"]
    assert ">= 4" in rule["expr"]
    assert rule["for"] == "30m"
    assert rule["labels"] == {
        "severity": "high",
        "service": "postgresql",
        "owner": "codex-error-review",
        "route": "codex-daily",
    }
    assert rule["annotations"]["runbook"].endswith(
        "docs/16-hetzner-maintenance.md#postgresql-capacity-and-checkpoint-pressure"
    )


def test_postgresql_shared_memory_alert_enforces_contract_and_capacity() -> None:
    rule = _alert_rule("PostgreSQLSharedMemoryPressure")

    assert "jobseek_postgresql_shared_memory_configured_bytes < 1073741824" in rule["expr"]
    assert "jobseek_postgresql_shared_memory_capacity_bytes < 1073741824" in rule["expr"]
    assert "jobseek_postgresql_shared_memory_available_bytes" in rule["expr"]
    assert rule["for"] == "5m"
    assert rule["labels"] == {
        "severity": "critical",
        "service": "postgresql",
        "owner": "codex-error-review",
        "route": "codex-daily",
    }
    assert rule["annotations"]["runbook"].endswith(
        "docs/16-hetzner-maintenance.md#postgresql-shared-memory"
    )


def test_deadletter_operator_playbook_is_documented() -> None:
    text = (ROOT / "docs/03-crawler-architecture.md").read_text()

    for needle in [
        "crawler_inflight_deadletter_depth{wtype}",
        "DeadletterQueueNotEmpty",
        "ZRANGE deadletter:simple 0 -1 WITHSCORES",
        "ZRANGE deadletter:browser 0 -1 WITHSCORES",
        "task_type|domain|task_id",
        "ZREM deadletter:simple '<member>'",
    ]:
        assert needle in text
