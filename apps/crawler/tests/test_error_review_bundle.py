"""Tests for generation-aware cgroup evidence in the root collector."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "codex-error-review-bundle.py"
SPEC = importlib.util.spec_from_file_location("codex_error_review_bundle", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


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
