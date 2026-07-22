"""Tests for the root-owned Hetzner host telemetry sampler and rule sync."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

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


def test_collect_writes_atomic_failure_metrics(tmp_path: Path, monkeypatch) -> None:
    textfile = tmp_path / "metrics" / "host.prom"
    monkeypatch.setattr(host, "_collect_container_metrics", lambda *_: None)
    monkeypatch.setattr(host, "_collect_unit_metrics", lambda *_: None)
    monkeypatch.setattr(host, "_collect_backup_metrics", lambda *_: None)
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


def test_postgresql_probe_emits_capacity_and_durability_metrics(monkeypatch) -> None:
    class Result:
        returncode = 0

    monkeypatch.setattr(host.subprocess, "run", lambda *_args, **_kwargs: Result())
    monkeypatch.setattr(
        host,
        "_run",
        lambda *_args, **_kwargs: type(
            "Completed", (), {"stdout": "12\t100\t500\t2\t700\t900\t19000000000\n"}
        )(),
    )
    lines: list[str] = []

    host._collect_postgresql_metrics(lines)

    content = "\n".join(lines)
    assert "jobseek_postgresql_ready 1" in content
    assert "jobseek_postgresql_connections 12.0" in content
    assert "jobseek_postgresql_archive_failed_total 2.0" in content
    assert "jobseek_postgresql_database_bytes 19000000000.0" in content


def test_cursor_rejects_future_and_old_values(tmp_path: Path) -> None:
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps({"ok": 99_950, "old": 1, "future": 100_001, "bad": "x"}))
    assert host._load_cursor(path, now=100_000) == {"ok": 99_950}


def test_rule_source_has_bounded_owned_groups() -> None:
    groups = rules._load_groups(ROOT / "apps" / "crawler" / "alerts.yaml")
    assert {group["name"] for group in groups} == {
        "jobseek_hetzner_fleet",
        "jobseek_crawler_reliability",
    }
    assert sum(len(group["rules"]) for group in groups) >= 28
    for group in groups:
        assert 0 < len(group["rules"]) <= rules.MAX_RULES_PER_GROUP
        for rule in group["rules"]:
            assert rule["labels"]["owner"] == "codex-error-review"
            assert rule["labels"]["route"] == "codex-daily"
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
