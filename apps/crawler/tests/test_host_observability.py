"""Tests for the root-owned Hetzner host telemetry sampler and rule sync."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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


def test_rule_source_has_single_owned_group() -> None:
    group = rules._load_group(ROOT / "apps" / "crawler" / "alerts.yaml")
    assert group["name"] == "jobseek_crawler_reliability"
    assert len(group["rules"]) >= 20
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


def test_rule_sync_rolls_back_previous_group(monkeypatch) -> None:
    group = {
        "name": "jobseek_crawler_reliability",
        "rules": [{"alert": "New", "expr": "vector(1)"}],
    }
    previous = {"name": group["name"], "rules": [{"alert": "Old", "expr": "vector(0)"}]}

    class Client:
        def __init__(self):
            self.calls = []

        def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs.get("body")))
            if method == "GET" and "/config/" in path:
                return 200, yaml.safe_dump(previous).encode()
            if method == "POST":
                return 202, b""
            return 200, b"{}"

    client = Client()
    monkeypatch.setattr(rules, "_remote_rule_names", lambda *_args: set())
    monkeypatch.setattr(rules.time, "sleep", lambda *_args: None)

    with pytest.raises(rules.RuleSyncError):
        rules.sync_group(client, "namespace", group)

    post_bodies = [body for method, _path, body in client.calls if method == "POST"]
    assert len(post_bodies) == 2
    assert yaml.safe_load(post_bodies[-1]) == previous
