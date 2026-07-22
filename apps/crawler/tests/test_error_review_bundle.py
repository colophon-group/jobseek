"""Tests for generation-aware cgroup evidence in the root collector."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "codex-error-review-bundle.py"
SPEC = importlib.util.spec_from_file_location("codex_error_review_bundle", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)

CONFORMANCE_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "codex-error-review-conformance.py"
)
CONFORMANCE_SPEC = importlib.util.spec_from_file_location(
    "codex_error_review_conformance", CONFORMANCE_PATH
)
assert CONFORMANCE_SPEC is not None and CONFORMANCE_SPEC.loader is not None
conformance = importlib.util.module_from_spec(CONFORMANCE_SPEC)
CONFORMANCE_SPEC.loader.exec_module(conformance)


def test_parse_cgroup_key_values_ignores_malformed_rows():
    assert bundle._parse_cgroup_key_values("low 0\nhigh 2\nmalformed\noom nope\noom_kill 3\n") == {
        "low": 0,
        "high": 2,
        "oom_kill": 3,
    }


def test_read_cgroup_memory_files(tmp_path: Path):
    (tmp_path / "memory.current").write_text("1735000000\n", encoding="utf-8")
    (tmp_path / "memory.peak").write_text("3411000000\n", encoding="utf-8")
    (tmp_path / "memory.max").write_text("6442450944\n", encoding="utf-8")
    (tmp_path / "memory.swap.current").write_text("0\n", encoding="utf-8")
    (tmp_path / "memory.events").write_text(
        "low 0\nhigh 0\nmax 1\noom 1\noom_kill 1\n",
        encoding="utf-8",
    )
    (tmp_path / "memory.events.local").write_text(
        "low 0\nhigh 0\nmax 1\noom 1\noom_kill 1\n",
        encoding="utf-8",
    )

    assert bundle._read_cgroup_memory_files(tmp_path) == {
        "version": 2,
        "current_bytes": 1_735_000_000,
        "peak_bytes": 3_411_000_000,
        "limit_bytes": 6_442_450_944,
        "swap_current_bytes": 0,
        "events": {"low": 0, "high": 0, "max": 1, "oom": 1, "oom_kill": 1},
        "events_local": {
            "low": 0,
            "high": 0,
            "max": 1,
            "oom": 1,
            "oom_kill": 1,
        },
    }


def test_read_cgroup_memory_files_tolerates_optional_files(tmp_path: Path):
    (tmp_path / "memory.max").write_text("max\n", encoding="utf-8")

    assert bundle._read_cgroup_memory_files(tmp_path) == {
        "version": 2,
        "limit_bytes": "max",
    }


def test_collect_container_cgroup_memory_records_container_generation(tmp_path, monkeypatch):
    inspect = [
        {
            "Id": "abc123",
            "Name": "/deploy-browser-1-1",
            "Created": "2026-07-20T14:28:00Z",
            "Config": {"Image": "ghcr.io/colophon-group/jobseek-crawler:v0.13.114"},
            "RestartCount": 0,
            "State": {
                "Pid": 4321,
                "Status": "running",
                "StartedAt": "2026-07-20T14:28:01Z",
                "OOMKilled": False,
            },
        }
    ]
    monkeypatch.setattr(bundle, "LONG_RUNNING_CONTAINERS", ("deploy-browser-1-1",))
    monkeypatch.setattr(bundle, "_run", lambda *_args, **_kwargs: (0, json.dumps(inspect)))
    monkeypatch.setattr(
        bundle,
        "_read_cgroup_memory_files",
        lambda root: {
            "version": 2,
            "current_bytes": 1_735_000_000,
            "events": {"oom": 0, "oom_kill": 0},
        },
    )
    manifest = {}

    bundle._collect_container_cgroup_memory(tmp_path, manifest)

    evidence = json.loads((tmp_path / "host" / "docker-cgroup-memory.json").read_text())
    assert evidence == [
        {
            "cgroup_memory": {
                "current_bytes": 1_735_000_000,
                "events": {"oom": 0, "oom_kill": 0},
                "version": 2,
            },
            "container_id": "abc123",
            "created_at": "2026-07-20T14:28:00Z",
            "exit_code": None,
            "finished_at": "",
            "image": "ghcr.io/colophon-group/jobseek-crawler:v0.13.114",
            "name": "deploy-browser-1-1",
            "oom_killed": False,
            "pid": 4321,
            "restart_count": 0,
            "state_error": "",
            "started_at": "2026-07-20T14:28:01Z",
            "status": "running",
        }
    ]
    assert manifest["container_cgroup_memory"]["path"].endswith("host/docker-cgroup-memory.json")


def test_collect_docker_lifecycle_journal_uses_exact_window(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return 0, '{"action":"die","event_exit_code":"137"}\n'

    monkeypatch.setattr(bundle, "_run", fake_run)
    manifest = {}
    since = bundle.datetime(2026, 7, 20, 9, 0, tzinfo=bundle.UTC)
    until = bundle.datetime(2026, 7, 21, 9, 0, tzinfo=bundle.UTC)

    bundle._collect_docker_lifecycle_journal(
        tmp_path,
        manifest,
        since=since,
        until=until,
    )

    assert calls == [
        [
            "journalctl",
            "--unit",
            "jobseek-codex-docker-lifecycle.service",
            "--identifier",
            "jobseek-docker-lifecycle",
            "--since",
            "@1784538000",
            "--until",
            "@1784624400",
            "--output=cat",
            "--quiet",
            "--no-pager",
        ]
    ]
    assert (tmp_path / "host" / "docker-lifecycle.jsonl").read_text() == (
        '{"action":"die","event_exit_code":"137"}\n'
    )
    assert manifest["docker_lifecycle"]["returncode"] == 0


def _write_metrics_config(path: Path, *, extra: str = "") -> None:
    path.write_text(
        "GRAFANA_METRICS_READ_URL=https://example.grafana.net/api/prom\n"
        "GRAFANA_METRICS_READ_USERNAME=123456\n"
        "GRAFANA_METRICS_READ_TOKEN=read-only-token-with-safe-length\n" + extra,
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_load_metrics_config_requires_dedicated_root_only_shape(tmp_path: Path):
    path = tmp_path / "metrics.env"
    _write_metrics_config(path)

    config = bundle._load_metrics_config(path, required_uid=os.getuid())

    assert set(config) == bundle.METRICS_CONFIG_KEYS
    assert config["GRAFANA_METRICS_READ_URL"].endswith("/api/prom")


def test_load_metrics_config_rejects_group_read_and_unrelated_keys(tmp_path: Path):
    path = tmp_path / "metrics.env"
    _write_metrics_config(path)
    path.chmod(0o640)
    with pytest.raises(bundle.MetricsEvidenceError, match="ownership or mode"):
        bundle._load_metrics_config(path, required_uid=os.getuid())

    _write_metrics_config(path, extra="GRAFANA_LOKI_TOKEN=forbidden\n")
    with pytest.raises(bundle.MetricsEvidenceError, match="unexpected key"):
        bundle._load_metrics_config(path, required_uid=os.getuid())


def test_load_metrics_config_rejects_non_grafana_exfiltration_endpoint(tmp_path: Path):
    path = tmp_path / "metrics.env"
    path.write_text(
        "GRAFANA_METRICS_READ_URL=https://attacker.example/api/prom\n"
        "GRAFANA_METRICS_READ_USERNAME=123456\n"
        "GRAFANA_METRICS_READ_TOKEN=read-only-token-with-safe-length\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(bundle.MetricsEvidenceError, match="approved HTTPS"):
        bundle._load_metrics_config(path, required_uid=os.getuid())


def test_normalized_metric_result_bounds_labels_and_redacts(tmp_path: Path):
    del tmp_path  # pytest fixture keeps the test signature consistent with nearby tests.
    until = bundle.datetime(2026, 7, 22, 9, 0, tzinfo=bundle.UTC)
    spec = {
        "id": "test_query",
        "signal": "test signal",
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "max_series": 2,
    }
    response = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {
                        "__name__": "up",
                        "instance": "API_TOKEN=should-not-escape",
                        "unapproved": "drop-me",
                    },
                    "values": [[until.timestamp(), "1"]],
                }
            ],
        },
    }

    result = bundle._normalized_metric_result(spec, response, until=until)

    assert result["status"] == "ok"
    assert result["series_count"] == 1
    assert result["sample_count"] == 1
    assert result["series"] == [
        {
            "labels": {"metric": "up", "instance": "API_TOKEN=<redacted>"},
            "newest_sample_at": until.isoformat(),
            "freshness_seconds": 0,
            "samples": [[int(until.timestamp()), "1"]],
        }
    ]


def test_normalized_metric_result_marks_missing_and_stale():
    until = bundle.datetime(2026, 7, 22, 9, 0, tzinfo=bundle.UTC)
    spec = {
        "id": "test_query",
        "signal": "test signal",
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "max_series": 2,
    }
    missing = bundle._normalized_metric_result(
        spec,
        {"status": "success", "data": {"resultType": "matrix", "result": []}},
        until=until,
    )
    stale = bundle._normalized_metric_result(
        spec,
        {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"__name__": "up"},
                        "values": [[until.timestamp() - 1_000, "1"]],
                    }
                ],
            },
        },
        until=until,
    )

    assert missing["status"] == "missing"
    assert stale["status"] == "stale"
    assert stale["freshness_seconds"] == 1_000


def test_normalized_metric_result_requires_each_named_signal():
    until = bundle.datetime(2026, 7, 22, 9, 0, tzinfo=bundle.UTC)
    spec = {
        "id": "test_query",
        "signal": "test signal",
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "required_signals": ("cursor", "lag"),
        "max_series": 3,
    }
    response = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"signal": "lag"},
                    "values": [[until.timestamp(), "0"]],
                }
            ],
        },
    }

    result = bundle._normalized_metric_result(spec, response, until=until)

    assert result["status"] == "missing"
    assert result["missing_required_signals"] == ["cursor"]


def test_collect_historical_metrics_fails_closed_per_query(monkeypatch, tmp_path: Path):
    since = bundle.datetime(2026, 7, 21, 9, 0, tzinfo=bundle.UTC)
    until = bundle.datetime(2026, 7, 22, 9, 0, tzinfo=bundle.UTC)
    spec = {
        "id": "required_query",
        "signal": "required signal",
        "query": "up",
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "max_series": 2,
    }
    monkeypatch.setattr(bundle, "METRIC_QUERIES", (spec,))
    monkeypatch.setattr(bundle, "_load_metrics_config", lambda _path: {"safe": "config"})

    def fail_query(*_args, **_kwargs):
        raise bundle.MetricsEvidenceError("metrics query returned HTTP 403")

    monkeypatch.setattr(bundle, "_metrics_request", fail_query)

    evidence = bundle._collect_historical_metrics(
        tmp_path / "metrics.env", since=since, until=until
    )

    assert evidence["required_complete"] is False
    assert evidence["queries"][0]["status"] == "error"
    assert evidence["queries"][0]["error_class"] == "metrics-query-returned-HTTP-403"
    serialized = json.dumps(evidence).lower()
    assert "token" in serialized  # boundary description only
    assert "authorization" not in serialized
    assert "grafana_metrics_read" not in serialized


def test_metrics_json_writer_never_leaves_truncated_json(tmp_path, monkeypatch):
    path = tmp_path / "historical-prometheus.json"
    monkeypatch.setattr(bundle, "MAX_FILE_BYTES", 10)

    with pytest.raises(bundle.MetricsEvidenceError, match="exceeded the byte limit"):
        bundle._write_bounded_json(path, {"required_complete": False})

    assert not path.exists()


def test_unprivileged_conformance_reads_only_normalized_evidence(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "latest"
    (bundle_dir / "metrics").mkdir(parents=True)
    metrics = {
        "schema_version": 1,
        "required_complete": False,
        "queries": [{"id": "scrape_targets", "status": "missing"}],
    }
    manifest = {"historical_metrics": {"query_count": 1}}
    (bundle_dir / "metrics" / "historical-prometheus.json").write_text(
        json.dumps(metrics), encoding="utf-8"
    )
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    credential = tmp_path / "root-only.env"

    monkeypatch.setattr(conformance.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(conformance.os, "getgroups", lambda: [])
    monkeypatch.setattr(conformance, "FORBIDDEN_PATHS", ())

    conformance.verify_boundary(bundle_dir, credential)
