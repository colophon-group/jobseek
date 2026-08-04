"""Tests for the root-owned Hetzner host telemetry sampler and rule sync."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
HOST_SCRIPT = ROOT / "scripts" / "jobseek-host-observability.py"
RULE_SCRIPT = ROOT / "scripts" / "sync-grafana-rules.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = _load("jobseek_host_observability", HOST_SCRIPT)
rules = _load("sync_grafana_rules", RULE_SCRIPT)


def test_metric_labels_are_stable_and_escaped() -> None:
    assert host._metric("sample", 1, role='post"gres', unit="a\\b") == (
        'sample{role="post\\"gres",unit="a\\\\b"} 1'
    )


def test_redaction_removes_credentials_and_private_identifiers() -> None:
    redacted = host._redact(
        "token=secret https://example.test/path?q=secret "
        "192.0.2.4 user@example.test 123e4567-e89b-12d3-a456-426614174000"
    )
    assert "secret" not in redacted
    assert "192.0.2.4" not in redacted
    assert "user@example.test" not in redacted
    assert "123e4567" not in redacted
    assert "https://example.test/path?<redacted>" in redacted


def test_backup_status_is_republished_without_error_text(tmp_path: Path) -> None:
    (tmp_path / "postgresql.json").write_text(
        json.dumps(
            {
                "attempt_unix": 100,
                "last_success_unix": 90,
                "duration_seconds": 4.5,
                "success": False,
                "error": "password=must-not-escape",
            }
        ),
        encoding="utf-8",
    )
    lines: list[str] = []

    host._collect_backup_metrics("postgresql", tmp_path, lines)

    content = "\n".join(lines)
    assert "jobseek_backup_last_attempt_unixtime" in content
    assert "jobseek_backup_last_attempt_success" in content
    assert 'service="postgresql"' in content
    assert "must-not-escape" not in content


def test_codex_error_review_status_is_republished_without_result_text(tmp_path: Path) -> None:
    path = tmp_path / "error-review-status.json"
    path.write_text(
        json.dumps(
            {
                "last_attempt_unixtime": 120,
                "last_success_unixtime": 100,
                "last_attempt_success": 0,
                "run_in_progress": 0,
                "last_result": "password=must-not-escape",
            }
        ),
        encoding="utf-8",
    )
    lines: list[str] = []

    host._collect_codex_error_review_metrics(lines, path)

    assert lines == [
        "jobseek_codex_daily_error_review_last_attempt_unixtime 120",
        "jobseek_codex_daily_error_review_last_success_unixtime 100",
        "jobseek_codex_daily_error_review_last_attempt_success 0",
        "jobseek_codex_daily_error_review_run_in_progress 0",
    ]
    assert "must-not-escape" not in "\n".join(lines)


def test_missing_codex_error_review_status_publishes_fail_closed_zeros(
    tmp_path: Path,
) -> None:
    lines: list[str] = []

    host._collect_codex_error_review_metrics(lines, tmp_path / "missing.json")

    assert lines == [
        "jobseek_codex_daily_error_review_last_attempt_unixtime 0",
        "jobseek_codex_daily_error_review_last_success_unixtime 0",
        "jobseek_codex_daily_error_review_last_attempt_success 0",
        "jobseek_codex_daily_error_review_run_in_progress 0",
    ]


def test_invalid_codex_error_review_status_fails_probe(tmp_path: Path) -> None:
    path = tmp_path / "error-review-status.json"
    path.write_text('{"last_attempt_success": 2}', encoding="utf-8")

    with pytest.raises(host.ProbeError, match="last_attempt_success"):
        host._collect_codex_error_review_metrics([], path)


def test_typesense_process_limit_parser_requires_numeric_nofile() -> None:
    limits = """Limit                     Soft Limit           Hard Limit           Units
Max open files            65536                65536                files
"""
    assert host._parse_typesense_nofile_limits(limits) == (65_536, 65_536)

    with pytest.raises(host.ProbeError, match="omitted Max open files"):
        host._parse_typesense_nofile_limits("Max processes 100 100 processes\n")


def test_typesense_log_metrics_capture_the_incident_chain() -> None:
    metrics = host._parse_typesense_log_metrics(
        "\n".join(
            (
                "Threadpool exhaustion detected, task_queue_len: 938, thread_pool_len: 16",
                "event=slow_request, time=225102 ms, endpoint=GET /collections/example",
                "Fail to open /proc/self/fd: Too many open files [24]",
                "Timed snapshot failed, error: Fail to create SnapshotWriter",
                "Node with no leader. Resetting peers of size: 1",
                "node default_group is in state ERROR, can't reset_peer",
            )
        )
    )

    assert metrics["threadpool_queue_depth"] == 938
    assert metrics["slow_request_max_milliseconds"] == 225_102
    assert metrics["event_threadpool_exhaustion"] == 1
    assert metrics["event_slow_request"] == 1
    assert metrics["event_descriptor_exhaustion"] == 1
    assert metrics["event_snapshot_failure"] == 1
    assert metrics["event_leaderless"] == 2


def test_typesense_host_collects_web_backup_only_after_activation(
    tmp_path: Path, monkeypatch
) -> None:
    timer = "jobseek-web-postgresql-backup.timer"
    assert timer in host.OPTIONAL_ROLE_UNITS["typesense"]
    assert ("web-postgresql", timer) in host.OPTIONAL_ROLE_BACKUPS["typesense"]
    (tmp_path / "typesense.json").write_text(
        json.dumps({"success": True, "last_success_unix": 100}), encoding="utf-8"
    )
    (tmp_path / "web-postgresql.json").write_text(
        json.dumps({"success": True, "last_success_unix": 200}), encoding="utf-8"
    )

    monkeypatch.setattr(host, "_unit_enabled", lambda _unit: False)
    lines: list[str] = []
    host._collect_backup_metrics("typesense", tmp_path, lines)
    assert not any('service="web-postgresql"' in line for line in lines)

    monkeypatch.setattr(host, "_unit_enabled", lambda unit: unit == timer)
    lines = []
    host._collect_backup_metrics("typesense", tmp_path, lines)
    assert any('service="web-postgresql"' in line for line in lines)


def test_collect_writes_atomic_failure_metrics(tmp_path: Path, monkeypatch) -> None:
    textfile = tmp_path / "metrics" / "host.prom"
    monkeypatch.setattr(host, "_collect_container_metrics", lambda *_: None)
    monkeypatch.setattr(host, "_collect_unit_metrics", lambda *_: None)
    monkeypatch.setattr(host, "_collect_backup_metrics", lambda *_: None)
    monkeypatch.setattr(host, "_collect_alloy_metrics", lambda *_: None)
    monkeypatch.setattr(host, "_collect_new_error_logs", lambda *_args, **_kwargs: None)

    assert host.collect("crawler", textfile=textfile, state_dir=tmp_path / "state") is True
    content = textfile.read_text(encoding="utf-8")
    assert 'jobseek_host_observability_collect_success{host_role="crawler"} 1' in content
    assert textfile.stat().st_mode & 0o777 == 0o644

    def fail(*_args):
        raise host.ProbeError("token=do-not-print")

    monkeypatch.setattr(host, "_collect_container_metrics", fail)
    assert host.collect("crawler", textfile=textfile, state_dir=tmp_path / "state") is False
    content = textfile.read_text(encoding="utf-8")
    assert 'probe="containers"' in content
    assert "do-not-print" not in content
    assert 'jobseek_host_observability_collect_success{host_role="crawler"} 0' in content


def test_alloy_metric_parser_aggregates_only_fixed_families() -> None:
    payload = """
alloy_resources_process_resident_memory_bytes 123
prometheus_remote_storage_samples_pending{remote_name="a",url="redacted"} 2
prometheus_remote_storage_samples_pending{remote_name="b",url="redacted"} 3
prometheus_remote_storage_queue_highest_sent_timestamp_seconds{remote_name="a"} 100
prometheus_remote_storage_queue_highest_sent_timestamp_seconds{remote_name="b"} 105
loki_write_dropped_entries_total{reason="ingester_error"} 7
unbounded_metric{origin="example.test"} 999
"""

    assert host._parse_alloy_metrics(payload) == {
        "resident_memory_bytes": 123.0,
        "remote_write_highest_sent_timestamp_seconds": 105.0,
        "remote_write_samples_pending": 5.0,
        "remote_write_samples_retried_total": 0,
        "remote_write_samples_failed_total": 0,
        "remote_write_samples_dropped_total": 0,
        "remote_write_enqueue_retries_total": 0,
        "loki_dropped_entries_total": 7.0,
    }


def test_alloy_probe_emits_bounded_host_and_compose_health(monkeypatch) -> None:
    payload = """
alloy_resources_process_resident_memory_bytes 123
prometheus_remote_storage_queue_highest_sent_timestamp_seconds 1800000000
prometheus_remote_storage_samples_pending 0
"""

    def read(url: str, **_kwargs) -> str:
        return payload if url.endswith("/metrics") else "Alloy is ready."

    monkeypatch.setattr(host, "_read_loopback", read)
    monkeypatch.setattr(
        host,
        "_recent_alloy_rejections",
        lambda collector: 1 if collector == "compose" else 0,
    )
    lines: list[str] = []

    host._collect_alloy_metrics("crawler", lines)

    content = "\n".join(lines)
    assert 'jobseek_alloy_ready{collector="host",host_role="crawler"} 1' in content
    assert 'jobseek_alloy_ready{collector="compose",host_role="crawler"} 1' in content
    assert (
        'jobseek_alloy_remote_write_rejections_recent{collector="compose",host_role="crawler"} 1'
        in content
    )
    assert "unbounded_metric" not in content


def test_crawler_container_inventory_includes_compose_alloy() -> None:
    assert "deploy-alloy-1" in host.ROLE_CONTAINERS["crawler"]


def test_reconciliation_deployment_metrics_detect_and_recover_revision_state(
    tmp_path: Path,
) -> None:
    revision_path = tmp_path / "deployed-sha"
    lines: list[str] = []
    host._collect_reconciliation_deployment_metrics(lines, revision_path)
    assert "jobseek_cross_store_reconciliation_deployed_revision_available 0" in lines

    revision = "a" * 40
    revision_path.write_text(f"{revision}\n", encoding="ascii")
    lines = []
    host._collect_reconciliation_deployment_metrics(lines, revision_path)
    content = "\n".join(lines)
    assert "jobseek_cross_store_reconciliation_deployed_revision_available 1" in content
    assert f'revision="{revision}"' in content
    assert "jobseek_cross_store_reconciliation_deployed_revision_mtime_seconds" in content


def test_postgresql_probe_emits_capacity_and_durability_metrics(monkeypatch) -> None:
    class Result:
        returncode = 0

    monkeypatch.setattr(host.subprocess, "run", lambda *_args, **_kwargs: Result())
    monkeypatch.setattr(
        host,
        "_collect_postgresql_emergency_reserve_metrics",
        lambda lines, _container: lines.extend(
            (
                "jobseek_postgresql_emergency_reserve_target_bytes 2147483648",
                "jobseek_postgresql_emergency_reserve_bytes 2147483648",
            )
        ),
    )
    monkeypatch.setattr(
        host,
        "_collect_postgresql_shared_memory_metrics",
        lambda lines, _container: lines.extend(
            (
                "jobseek_postgresql_shared_memory_configured_bytes 1073741824",
                "jobseek_postgresql_shared_memory_capacity_bytes 1073741824",
                "jobseek_postgresql_shared_memory_used_bytes 67108864",
                "jobseek_postgresql_shared_memory_available_bytes 1006632960",
            )
        ),
    )

    def query(_container: str, sql: str, **_kwargs) -> str:
        if sql == host.POSTGRES_STATS_SQL:
            return "12\t100\t500\t2\t700\t900\t1234.5\t67.8\t4000\t1800000000\t19000000000"
        if sql == host.BOARD_QUARANTINE_SCHEMA_SQL:
            return "6"
        if sql == host.BOARD_QUARANTINE_STATS_SQL:
            return "121\t86400\t99134\t16"
        if sql == host.BOARD_GONE_SCHEMA_SQL:
            return "8"
        if sql == host.BOARD_GONE_STATS_SQL:
            return "58\t58\t21600\t181\t239\t19"
        if sql == host.PHANTOM_ACTIVE_STATS_SQL:
            return "4\t136\t7776000"
        if "to_regclass" in sql:
            return "cross_store_reconciliation_state"
        if sql == host.RECONCILIATION_STATS_SQL:
            return "typesense\t1001\t901\t951\t13.5\t670000\t694000\t7\t7\t0\trepaired\t16\t256\t0"
        if "cross_store_reconciliation_run" in sql:
            return "0"
        raise AssertionError(sql)

    monkeypatch.setattr(host, "_postgresql_query", query)
    lines: list[str] = []

    host._collect_postgresql_metrics(lines)

    content = "\n".join(lines)
    assert "jobseek_postgresql_ready 1" in content
    assert "jobseek_postgresql_connections 12.0" in content
    assert "jobseek_postgresql_archive_failed_total 2.0" in content
    assert "jobseek_postgresql_stats_query_duration_seconds " in content
    assert "jobseek_postgresql_checkpoint_write_seconds_total 1.2345" in content
    assert "jobseek_postgresql_checkpoint_sync_seconds_total 0.0678" in content
    assert "jobseek_postgresql_checkpoint_buffers_total 4000.0" in content
    assert "jobseek_postgresql_stats_reset_unixtime 1800000000.0" in content
    assert "jobseek_postgresql_database_bytes 19000000000.0" in content
    assert "jobseek_postgresql_emergency_reserve_bytes 2147483648" in content
    assert "jobseek_postgresql_shared_memory_configured_bytes 1073741824" in content
    assert "jobseek_crawler_board_quarantine_schema_ready 1" in content
    assert "jobseek_crawler_quarantined_boards 121.0" in content
    assert "jobseek_crawler_quarantine_oldest_seconds 86400.0" in content
    assert "jobseek_crawler_quarantine_active_postings 99134.0" in content
    assert "jobseek_crawler_board_recoveries_total 16.0" in content
    assert "jobseek_crawler_board_gone_schema_ready 1" in content
    assert "jobseek_crawler_gone_pending_boards 58.0" in content
    assert "jobseek_crawler_gone_pending_confirmations 58.0" in content
    assert "jobseek_crawler_gone_pending_oldest_seconds 21600.0" in content
    assert "jobseek_crawler_gone_terminal_boards 181.0" in content
    assert "jobseek_crawler_board_gone_transitions_total 239.0" in content
    assert "jobseek_crawler_board_gone_recoveries_total 19.0" in content
    assert "jobseek_crawler_phantom_active_boards 4.0" in content
    assert "jobseek_crawler_phantom_active_postings 136.0" in content
    assert "jobseek_crawler_phantom_active_oldest_seconds 7776000.0" in content
    assert "jobseek_cross_store_reconciliation_schema_ready 1" in content
    assert "WHERE target = 'typesense'" in host.RECONCILIATION_STATS_SQL
    assert 'target="supabase"' not in content
    assert (
        'jobseek_cross_store_reconciliation_last_attempt_success{target="typesense"} 1' in content
    )
    assert (
        'jobseek_cross_store_reconciliation_bootstrap_complete{target="typesense"} 0.0' in content
    )
    assert "jobseek_cross_store_reconciliation_stuck_runs 0.0" in content


def test_postgresql_probe_tolerates_reconciliation_schema_not_deployed(monkeypatch) -> None:
    class Result:
        returncode = 0

    monkeypatch.setattr(host.subprocess, "run", lambda *_args, **_kwargs: Result())
    monkeypatch.setattr(
        host, "_collect_postgresql_emergency_reserve_metrics", lambda _lines, _container: None
    )
    monkeypatch.setattr(
        host, "_collect_postgresql_shared_memory_metrics", lambda _lines, _container: None
    )

    def query(_container: str, sql: str, **_kwargs) -> str:
        if sql == host.POSTGRES_STATS_SQL:
            return "1\t2\t3\t0\t4\t5\t6\t7\t8\t9\t10"
        if sql in (host.BOARD_QUARANTINE_SCHEMA_SQL, host.BOARD_GONE_SCHEMA_SQL):
            return "0"
        if sql == host.PHANTOM_ACTIVE_STATS_SQL:
            return "0\t0\t0"
        if "to_regclass" in sql:
            return ""
        raise AssertionError(sql)

    monkeypatch.setattr(host, "_postgresql_query", query)
    lines: list[str] = []

    host._collect_postgresql_metrics(lines)

    assert "jobseek_cross_store_reconciliation_schema_ready 0" in "\n".join(lines)
    assert "jobseek_crawler_board_quarantine_schema_ready 0" in "\n".join(lines)
    assert "jobseek_crawler_board_gone_schema_ready 0" in "\n".join(lines)
    assert "jobseek_crawler_phantom_active_postings 0.0" in "\n".join(lines)


def test_postgresql_shared_memory_probe_emits_configured_and_live_capacity(
    monkeypatch,
) -> None:
    class Result:
        def __init__(self, stdout: str):
            self.stdout = stdout

    def run(argv, **_kwargs):
        if argv[:2] == ["docker", "inspect"]:
            return Result("1073741824\n")
        if argv[:3] == ["docker", "exec", "postgres"]:
            return Result(
                "Filesystem 1B-blocks Used Available Use% Mounted on\n"
                "shm 1073741824 67108864 1006632960 7% /dev/shm\n"
            )
        raise AssertionError(argv)

    monkeypatch.setattr(host, "_run", run)
    lines: list[str] = []

    host._collect_postgresql_shared_memory_metrics(lines, "postgres")

    assert lines == [
        "jobseek_postgresql_shared_memory_configured_bytes 1073741824",
        "jobseek_postgresql_shared_memory_capacity_bytes 1073741824",
        "jobseek_postgresql_shared_memory_used_bytes 67108864",
        "jobseek_postgresql_shared_memory_available_bytes 1006632960",
    ]


def test_postgresql_emergency_reserve_probe_reports_allocated_bytes(
    monkeypatch,
) -> None:
    class Result:
        def __init__(self, stdout: str):
            self.stdout = stdout

    def run(argv, **_kwargs):
        if argv[:2] == ["docker", "inspect"]:
            return Result("/mnt/postgresql/pgdata\n")
        if argv[0] == "findmnt":
            return Result("/mnt/postgresql\n")
        raise AssertionError(argv)

    monkeypatch.setattr(host, "_run", run)
    monkeypatch.setattr(host.Path, "lstat", lambda _self: SimpleNamespace(st_blocks=4_194_304))
    monkeypatch.setattr(host.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(host.Path, "is_symlink", lambda _self: False)
    lines: list[str] = []

    host._collect_postgresql_emergency_reserve_metrics(lines, "postgres")

    assert lines[0] == "jobseek_postgresql_emergency_reserve_target_bytes 2147483648"
    assert lines[1].startswith("jobseek_postgresql_emergency_reserve_bytes ")


def test_cursor_rejects_future_and_old_values(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps({"ok": 99_950, "old": 1, "future": 100_001, "bad": "x"}))
    assert host._load_cursor(path, now=100_000) == {"ok": 99_950}


def test_rule_source_has_bounded_owned_groups() -> None:
    groups = rules._load_groups(ROOT / "apps" / "crawler" / "alerts.yaml")
    assert {group["name"] for group in groups} == {
        "jobseek_hetzner_fleet",
        "jobseek_operator_handoffs",
        "jobseek_postgresql_capacity",
        "jobseek_telemetry_delivery",
        "jobseek_typesense_reliability",
        "jobseek_crawler_reliability",
        "jobseek_crawler_board_quarantine",
    }
    assert {group["name"]: len(group["rules"]) for group in groups} == {
        "jobseek_hetzner_fleet": 19,
        "jobseek_postgresql_capacity": 4,
        "jobseek_typesense_reliability": 7,
        "jobseek_telemetry_delivery": 9,
        "jobseek_crawler_reliability": 19,
        "jobseek_crawler_board_quarantine": 8,
        "jobseek_operator_handoffs": 3,
    }
    for group in groups:
        assert 0 < len(group["rules"]) <= rules.MAX_RULES_PER_GROUP
        for rule in group["rules"]:
            assert rule["labels"]["owner"] == "codex-error-review"
            assert rule["labels"]["route"] == "codex-daily"
            if rule["labels"]["severity"] == "critical":
                assert rule["labels"]["page"] == "production"
                assert isinstance(rules._duration_signature(rule["for"]), int)
                assert rules._duration_signature(rule["for"]) <= 3 * 60 * 1_000
            assert rule["annotations"]["runbook"].startswith(
                "https://github.com/colophon-group/jobseek/"
            )


def test_rule_url_accepts_read_or_write_endpoint() -> None:
    assert rules._ruler_base("https://metrics.example/api/prom") == (
        "https://metrics.example/api/prom"
    )
    assert rules._ruler_base("https://metrics.example/api/prom/push") == (
        "https://metrics.example/api/prom"
    )
    with pytest.raises(rules.RuleSyncError):
        rules._ruler_base("https://metrics.example/api")


@pytest.mark.parametrize(
    ("labels", "pending", "error"),
    (
        (
            {"severity": "critical", "owner": "codex-error-review", "route": "codex-daily"},
            "3m",
            "page=production",
        ),
        (
            {
                "severity": "critical",
                "owner": "codex-error-review",
                "route": "codex-daily",
                "page": "production",
            },
            "4m",
            "exceeds three minutes",
        ),
        (
            {
                "severity": "critical",
                "owner": "codex-error-review",
                "route": "codex-daily",
                "page": "production",
            },
            "invalid",
            "valid pending duration",
        ),
    ),
)
def test_rule_source_rejects_unpageable_or_delayed_critical_alerts(
    tmp_path: Path, labels: dict, pending: str, error: str
) -> None:
    path = tmp_path / "alerts.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "groups": [
                    {
                        "name": "test",
                        "rules": [
                            {
                                "alert": "CriticalTest",
                                "expr": "vector(1)",
                                "for": pending,
                                "labels": labels,
                                "annotations": {
                                    "runbook": "https://github.com/colophon-group/jobseek/test"
                                },
                            }
                        ],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(rules.RuleSyncError, match=error):
        rules._load_groups(path)


def test_rule_signature_normalizes_equivalent_prometheus_durations() -> None:
    assert rules._duration_signature("24h") == rules._duration_signature("1d")
    assert rules._duration_signature("1h30m") == rules._duration_signature("90m")
    assert rules._duration_signature("not-a-duration") == "not-a-duration"


def test_remote_namespace_yaml_keeps_all_groups() -> None:
    payload = b"""namespace:
  - name: first
    rules: []
  - name: second
    rules: []
"""
    assert [group["name"] for group in rules._yaml_groups(payload, namespace="namespace")] == [
        "first",
        "second",
    ]


def test_sync_rejects_oversized_group_before_remote_access() -> None:
    group = {
        "name": "oversized",
        "rules": [{"alert": f"Rule{index}", "expr": "vector(1)"} for index in range(21)],
    }

    with pytest.raises(rules.RuleSyncError, match="between 1 and 20"):
        rules.sync_groups(object(), "namespace", [group])


def test_rule_sync_rolls_back_the_whole_namespace(monkeypatch) -> None:
    previous_group = {
        "name": "legacy",
        "rules": [{"alert": "Old", "expr": "vector(0)"}],
    }
    first = {"name": "first", "rules": [{"alert": "First", "expr": "vector(1)"}]}
    second = {"name": "second", "rules": [{"alert": "Second", "expr": "vector(1)"}]}
    state = {"legacy": previous_group}
    deleted: list[str] = []

    monkeypatch.setattr(rules, "_remote_groups", lambda *_args: dict(state))

    def post(_client, _namespace, group):
        if group["name"] == "second":
            raise rules.RuleSyncError("injected second-group failure")
        state[group["name"]] = group

    def delete(_client, _namespace, name):
        deleted.append(name)
        state.pop(name, None)

    monkeypatch.setattr(rules, "_post_group", post)
    monkeypatch.setattr(rules, "_delete_group", delete)
    monkeypatch.setattr(
        rules,
        "_groups_match",
        lambda _client, _namespace, expected, *, exact_names: (
            set(state) == set(expected) if exact_names else set(expected) <= set(state)
        ),
    )

    with pytest.raises(rules.RuleSyncError, match="injected second-group failure"):
        rules.sync_groups(object(), "namespace", [first, second])

    assert state == {"legacy": previous_group}
    assert deleted == ["first"]


def test_rule_sync_removes_stale_group_after_desired_groups_verify(monkeypatch) -> None:
    stale = {"name": "stale", "rules": [{"alert": "Old", "expr": "vector(0)"}]}
    desired = {"name": "desired", "rules": [{"alert": "New", "expr": "vector(1)"}]}
    state = {"stale": stale}

    monkeypatch.setattr(rules, "_remote_groups", lambda *_args: dict(state))
    monkeypatch.setattr(
        rules,
        "_post_group",
        lambda _client, _namespace, group: state.__setitem__(group["name"], group),
    )
    monkeypatch.setattr(
        rules,
        "_delete_group",
        lambda _client, _namespace, name: state.pop(name, None),
    )
    monkeypatch.setattr(
        rules,
        "_groups_match",
        lambda _client, _namespace, expected, *, exact_names: (
            set(state) == set(expected) if exact_names else set(expected) <= set(state)
        ),
    )

    rules.sync_groups(object(), "namespace", [desired])

    assert state == {"desired": desired}
