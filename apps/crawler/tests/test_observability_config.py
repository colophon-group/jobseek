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
        "PostgreSQLArchiveFailure",
        "TypesenseUnavailable",
        "TypesenseTunnelUnavailable",
        "RequiredHostUnitInactive",
        "RequiredContainerUnavailable",
        "HostRebootRequired",
    } <= names


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
