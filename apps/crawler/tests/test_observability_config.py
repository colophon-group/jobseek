from __future__ import annotations

import json
import re
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
    assert panel["targets"][0]["expr"] == (
        "max by (wtype, lifecycle) (crawler_monitor_deadletter_lifecycle_depth)"
    )
    assert panel["targets"][0]["legendFormat"] == "{{wtype}} {{lifecycle}}"
    assert panel["fieldConfig"]["defaults"]["thresholds"]["steps"] == [
        {"color": "green", "value": None},
        {"color": "red", "value": 1},
    ]
    assert "actionable/unresolved" in panel["description"]
    assert "crawler deadletters inspect" in panel["description"]


def test_alert_fires_when_deadletter_queue_stays_nonempty() -> None:
    rule = _alert_rule("DeadletterQueueNotEmpty")

    assert rule["expr"] == (
        "max by (wtype) (crawler_monitor_deadletter_lifecycle_depth{"
        'lifecycle=~"actionable|unresolved"}) > 0'
    )
    assert rule["for"] == "1h"
    assert rule["labels"] == {
        "severity": "medium",
        "service": "crawler",
        "owner": "codex-error-review",
        "route": "codex-daily",
    }
    assert "crawler_monitor_deadletter_lifecycle_depth" in rule["annotations"]["description"]
    assert rule["annotations"]["runbook"].endswith(
        "docs/03-crawler-architecture.md#inflight-leases-and-dead-letter-recovery"
    )


def test_upstream_host_circuit_alert_uses_bounded_fleet_outcomes() -> None:
    rule = _alert_rule("UpstreamHostCircuitOpen")

    assert rule["expr"] == (
        'sum(increase(crawler_tasks_total{status=~"host_circuit_(open|half_open)"}[10m])) > 0'
    )
    assert rule["for"] == "5m"
    assert rule["labels"] == {
        "severity": "medium",
        "service": "crawler",
        "owner": "codex-error-review",
        "route": "codex-daily",
    }
    assert "Loki" in rule["annotations"]["description"]
    assert rule["annotations"]["runbook"].endswith("docs/03-crawler-architecture.md")


def test_alloy_delivery_alerts_cover_rejection_loss_memory_and_restart() -> None:
    expected = {
        "AlloyCollectorUnready",
        "AlloyRemoteWriteRejected",
        "AlloyRemoteWriteStale",
        "AlloyRemoteWriteBacklog",
        "AlloyRemoteWriteSamplesLost",
        "AlloyLokiEntriesDropped",
        "AlloyMemoryPressure",
        "ComposeAlloyRestartOrOOM",
        "TelemetrySeriesBudgetHigh",
    }
    rules = {rule["alert"]: rule for rule in _alert_rules() if rule.get("alert") in expected}

    assert set(rules) == expected
    assert "rejections_recent" in rules["AlloyRemoteWriteRejected"]["expr"]
    assert "samples_failed_total" in rules["AlloyRemoteWriteSamplesLost"]["expr"]
    assert "resident_memory_bytes" in rules["AlloyMemoryPressure"]["expr"]
    assert 'container="deploy-alloy-1"' in rules["ComposeAlloyRestartOrOOM"]["expr"]
    for name in expected - {"AlloyMemoryPressure", "TelemetrySeriesBudgetHigh"}:
        assert rules[name]["labels"]["severity"] == "critical"
        assert rules[name]["labels"]["page"] == "production"


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
    for rule in (delayed, unknown):
        assert rule["labels"]["severity"] == "high"
        assert rule["labels"]["owner"] == "codex-error-review"
        assert rule["labels"]["route"] == "codex-daily"
    for rule in (schema, failed, stale, drift, stuck):
        assert rule["for"] == "3m"
        assert rule["labels"]["severity"] == "critical"
        assert rule["labels"]["page"] == "production"
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
        "WebPostgreSQLBackupHelperImageUnprotected",
        "PostgreSQLUnavailable",
        "PostgreSQLIdleInTransaction",
        "PostgreSQLDataVolumeHeadroomLow",
        "PostgreSQLCheckpointPressure",
        "PostgreSQLBackupRepositoryHeadroomLow",
        "PostgreSQLEmergencyHeadroomMissing",
        "PostgreSQLSharedMemoryPressure",
        "PostgreSQLArchiveFailure",
        "TypesenseUnavailable",
        "TypesenseTunnelUnavailable",
        "RequiredHostUnitInactive",
        "RequiredContainerUnavailable",
        "HostRebootRequired",
    } <= names


def test_web_backup_helper_image_alert_preserves_the_source_service_label() -> None:
    rule = _alert_rule("WebPostgreSQLBackupHelperImageUnprotected")

    assert "helper_image_available" in rule["expr"]
    assert "helper_image_gc_protected" in rule["expr"]
    assert rule["for"] == "3m"
    assert "service" not in rule["labels"]
    assert rule["labels"]["component"] == "data-backup"
    assert rule["labels"]["severity"] == "critical"


def test_backup_alerts_keep_simultaneous_service_series_distinct() -> None:
    source_series = [
        {"instance": "shared-host", "service": "typesense"},
        {"instance": "shared-host", "service": "web-postgresql"},
    ]

    for name in ("DataBackupFailed", "DataBackupStale"):
        rule = _alert_rule(name)
        evaluated_labels = []
        for source_labels in source_series:
            labels = {**source_labels, **rule["labels"], "alertname": name}
            evaluated_labels.append(labels)

        assert "service" not in rule["labels"]
        assert rule["labels"]["component"] == "data-backup"
        assert {labels["service"] for labels in evaluated_labels} == {
            "typesense",
            "web-postgresql",
        }
        assert len({tuple(sorted(labels.items())) for labels in evaluated_labels}) == 2


def test_postgresql_connection_alerts_preserve_capacity_and_transaction_guards() -> None:
    capacity = _alert_rule("PostgreSQLConnectionsHigh")
    idle_transaction = _alert_rule("PostgreSQLIdleInTransaction")

    assert "> 0.80" in capacity["expr"]
    assert capacity["for"] == "15m"
    assert 'state=~"idle_in_transaction.*"' in idle_transaction["expr"]
    assert idle_transaction["for"] == "5m"
    for rule in (capacity, idle_transaction):
        assert rule["labels"]["severity"] == "high"
        assert rule["annotations"]["runbook"].endswith(
            "docs/22-postgresql-connections.md#incident-response"
        )


def test_ats_inventory_alerts_cover_freshness_coverage_and_hard_cap() -> None:
    alloy = (ROOT / "deploy/observability/alloy-host.alloy").read_text()
    journal_rule = re.search(
        r'source_labels = \["__journal__systemd_unit"\]\s+'
        r'regex\s+=\s+"([^"]+)"',
        alloy,
    )
    assert journal_rule is not None
    journal_pattern = json.loads(f'"{journal_rule.group(1)}"')
    assert re.fullmatch(journal_pattern, "jobseek-ats-inventory.service")
    assert re.fullmatch(journal_pattern, "jobseek-ats-inventory-network.service")

    expected = {
        "AtsInventoryStatusUnavailable": "status_available",
        "AtsInventoryRunFailed": "last_attempt_success",
        "AtsInventoryRunStale": "last_success_unixtime",
        "AtsInventoryCoverageQuarantined": "candidate_coverage_percent < 99",
        "AtsInventoryQueueAtHardCap": "queue_total_open >= 600",
    }
    for name, signal in expected.items():
        rule = _alert_rule(name)
        assert signal in rule["expr"]
        assert rule["labels"] == {
            "severity": "high",
            "service": "ats-inventory",
            "owner": "codex-error-review",
            "route": "codex-daily",
        }
        assert rule["annotations"]["runbook"].startswith(
            "https://github.com/colophon-group/jobseek/blob/main/docs/21-ats-inventory-runner.md#"
        )


def test_typesense_reliability_alerts_cover_the_incident_chain() -> None:
    expected = {
        "TypesenseNofileLimitUnsafe": "jobseek_typesense_nofile_soft_limit",
        "TypesenseFileDescriptorPressure": "jobseek_typesense_open_file_descriptors",
        "TypesenseThreadPoolSaturated": "jobseek_typesense_threadpool_queue_depth",
        "TypesenseSlowRequests": "jobseek_typesense_slow_request_max_milliseconds",
        "TypesenseDescriptorExhausted": 'event="descriptor_exhaustion"',
        "TypesenseLeaderless": 'event="leaderless"',
        "TypesenseSnapshotFailure": 'event="snapshot_failure"',
    }

    for name, signal in expected.items():
        rule = _alert_rule(name)
        assert signal in rule["expr"]
        assert rule["labels"]["service"] == "typesense"
        assert rule["labels"]["owner"] == "codex-error-review"
        assert rule["labels"]["route"] == "codex-daily"
        assert rule["annotations"]["runbook"].endswith(
            "docs/16-hetzner-maintenance.md#typesense-readiness-and-raft-recovery"
        )


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


def test_redis_capacity_alerts_cover_attribution_growth_and_lead_time() -> None:
    stale = _alert_rule("RedisCapacitySnapshotStale")
    orphan = _alert_rule("RedisOrphanScrapeConfigs")
    family = _alert_rule("RedisKeyFamilyBudgetHigh")
    forecast = _alert_rule("RedisMemoryForecastPressure")

    assert "snapshot_available" in stale["expr"]
    assert "snapshot_unixtime" in stale["expr"]
    assert 'state="orphan"' in orphan["expr"]
    assert "estimated_bytes" in family["expr"]
    assert "budget_bytes" in family["expr"]
    assert "predict_linear" in forecast["expr"]
    assert "0.75" in forecast["expr"]
    for rule in (stale, orphan, family, forecast):
        assert rule["labels"]["severity"] == "high"
        assert rule["labels"]["owner"] == "codex-error-review"
        assert rule["labels"]["route"] == "codex-daily"
        assert rule["annotations"]["runbook"].endswith("docs/20-redis-capacity.md") is False
        assert "docs/20-redis-capacity.md#" in rule["annotations"]["runbook"]


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


def test_postgresql_backup_repository_alert_forecasts_cifs_exhaustion() -> None:
    rule = _alert_rule("PostgreSQLBackupRepositoryHeadroomLow")

    assert 'host_role="postgresql"' in rule["expr"]
    assert 'fstype="cifs"' in rule["expr"]
    assert "< 0.35" in rule["expr"]
    assert "predict_linear" in rule["expr"]
    assert "7 * 24 * 60 * 60" in rule["expr"]
    assert rule["for"] == "3m"
    assert rule["labels"]["severity"] == "critical"
    assert rule["labels"]["page"] == "production"


def test_postgresql_emergency_reserve_alert_enforces_allocated_headroom() -> None:
    rule = _alert_rule("PostgreSQLEmergencyHeadroomMissing")

    assert "jobseek_postgresql_emergency_reserve_bytes" in rule["expr"]
    assert "jobseek_postgresql_emergency_reserve_target_bytes" in rule["expr"]
    assert rule["for"] == "3m"
    assert rule["labels"]["severity"] == "critical"


def test_postgresql_shared_memory_alert_enforces_contract_and_capacity() -> None:
    rule = _alert_rule("PostgreSQLSharedMemoryPressure")

    assert "jobseek_postgresql_shared_memory_configured_bytes < 1073741824" in rule["expr"]
    assert "jobseek_postgresql_shared_memory_capacity_bytes < 1073741824" in rule["expr"]
    assert "jobseek_postgresql_shared_memory_available_bytes" in rule["expr"]
    assert rule["for"] == "3m"
    assert rule["labels"] == {
        "severity": "critical",
        "service": "postgresql",
        "owner": "codex-error-review",
        "route": "codex-daily",
        "page": "production",
    }
    assert rule["annotations"]["runbook"].endswith(
        "docs/16-hetzner-maintenance.md#postgresql-shared-memory"
    )


def test_phantom_active_postings_page_until_terminal_drift_is_zero() -> None:
    rule = _alert_rule("CrawlerPhantomActivePostings")

    assert rule["expr"] == "jobseek_crawler_phantom_active_postings > 0"
    assert rule["for"] == "15m"
    assert rule["labels"]["severity"] == "high"
    assert rule["labels"]["page"] == "production"
    assert rule["annotations"]["runbook"].endswith(
        "docs/16-hetzner-maintenance.md#phantom-active-posting-sweep"
    )


def test_operator_handoffs_cover_daily_review_without_duplicate_mimir_deadman() -> None:
    failed = _alert_rule("CodexDailyErrorReviewFailed")
    stale = _alert_rule("CodexDailyErrorReviewStale")
    stuck = _alert_rule("CodexDailyErrorReviewRunStuck")

    assert all(rule.get("alert") != "PagingRouteDeadman" for rule in _alert_rules())
    assert "last_attempt_success" in failed["expr"]
    assert "run_in_progress == 0" in failed["expr"]
    assert "last_success_unixtime" in stale["expr"]
    assert "36 * 60 * 60" in stale["expr"]
    assert "run_in_progress == 1" in stuck["expr"]
    for rule in (failed, stale, stuck):
        assert rule["for"] == "3m"
        assert rule["labels"]["severity"] == "critical"
        assert rule["labels"]["page"] == "production"
        assert rule["labels"]["route"] == "codex-daily"


def test_daily_error_review_records_status_across_the_systemd_lifecycle() -> None:
    unit = (ROOT / "deploy/systemd/jobseek-codex-daily-error-review.service").read_text(
        encoding="utf-8"
    )
    deploy = (ROOT / "scripts/deploy-codex-runner-host.sh").read_text(encoding="utf-8")

    assert "ConditionPathExists=/srv/jobseek-codex/repo/scripts/codex-routine-status.py" in unit
    assert (
        "ExecStartPre=+/usr/bin/python3 "
        "/srv/jobseek-codex/repo/scripts/codex-routine-status.py begin"
    ) in unit
    assert (
        "ExecStopPost=+/usr/bin/python3 "
        "/srv/jobseek-codex/repo/scripts/codex-routine-status.py finish "
        "--service-result ${SERVICE_RESULT}"
    ) in unit
    assert '"${REPO_DIR}/scripts/codex-routine-status.py"' in deploy


def test_deadletter_operator_playbook_is_documented() -> None:
    text = (ROOT / "docs/03-crawler-architecture.md").read_text()

    for needle in [
        "crawler_inflight_deadletter_depth{wtype}",
        "crawler_monitor_deadletter_lifecycle_depth{wtype,lifecycle}",
        "DeadletterQueueNotEmpty",
        "crawler deadletters inspect",
        "crawler deadletters retry",
        "crawler deadletters prune",
        "task_type|domain|task_id",
        "--apply",
    ]:
        assert needle in text
