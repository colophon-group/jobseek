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
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_redact_removes_private_identifiers_and_quoted_credentials():
    raw_resource_id = "a" * 64
    text = bundle._redact(
        """
        endpoint=192.0.2.4 peer=[2001:db8::1]:8108
        posting=123e4567-e89b-12d3-a456-426614174000
        image=sha256:{raw_resource_id}
        TOKEN=plain-secret
        "api_key": "json-secret"
        "Authorization": "Bearer bearer-secret"
        """.replace("{raw_resource_id}", raw_resource_id).strip()
    )

    for private_value in (
        "192.0.2.4",
        "2001:db8::1",
        "123e4567-e89b-12d3-a456-426614174000",
        raw_resource_id,
        "plain-secret",
        "json-secret",
        "bearer-secret",
    ):
        assert private_value not in text
    assert text.count("<redacted-host-address>") == 2
    assert text.count("<redacted-resource-id>") == 2
    assert text.count("<redacted>") == 3

    redacted_json = bundle._redact(
        '{"api_key": "json-secret", "Authorization": "Bearer bearer-secret"}'
    )
    assert json.loads(redacted_json) == {
        "api_key": "<redacted>",
        "Authorization": "Bearer <redacted>",
    }


def test_daily_review_instruction_surfaces_require_disk_attribution_evidence():
    surfaces = (
        REPOSITORY_ROOT / ".agents/skills/jobseek-error-review/SKILL.md",
        REPOSITORY_ROOT / ".claude/commands/jobseek-error-review.md",
    )
    required = (
        "disk_capacity.complete=false",
        "host/docker-system-df.txt",
        "host/docker-container-sizes.txt",
        "host/docker-images.txt",
        "host/disk-attribution.txt",
        "host/docker-gc.log",
        "85%",
    )
    for surface in surfaces:
        text = surface.read_text(encoding="utf-8")
        for value in required:
            assert value in text, f"{surface} is missing {value}"


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
            "container_generation": "6ca13d52ca70c883",
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
    raw_id = "a" * 64

    def fake_run(command, **_kwargs):
        calls.append(command)
        return (
            0,
            json.dumps(
                {
                    "schema_version": 1,
                    "source": "docker_event",
                    "event_at": "2026-07-21T08:59:00+00:00",
                    "action": "die",
                    "container_id": raw_id,
                    "container_name": "deploy-worker-1-1",
                    "compose_service": "worker-1",
                    "compose_oneoff": "False",
                    "event_exit_code": "137",
                    "arbitrary_label": "must-not-leak",
                }
            )
            + "\n",
        )

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
    lifecycle = (tmp_path / "host" / "docker-lifecycle.jsonl").read_text()
    assert raw_id not in lifecycle
    assert "container_id" not in lifecycle
    assert "must-not-leak" not in lifecycle
    assert bundle.container_generation(raw_id) in lifecycle
    assert json.loads((tmp_path / "host" / "maintenance-correlation.json").read_text()) == {
        "schema_version": 1,
        "maintenance_windows": [],
        "unattributed_service_pauses": [
            {
                "downtime_seconds": 0.0,
                "forced_termination": True,
                "native_exit": True,
                "oom": False,
                "paused_at": "2026-07-21T08:59:00+00:00",
                "reason": "missing_provenance",
                "restored": False,
                "service": "worker-1",
            }
        ],
        "invalid_provenance_events": 0,
    }
    assert manifest["docker_lifecycle"]["returncode"] == 0
    assert manifest["maintenance_correlation"]["path"].endswith("host/maintenance-correlation.json")


def test_collect_reconciliation_journal_retains_redacted_exact_window(tmp_path, monkeypatch):
    calls = []
    raw_id = "123e4567-e89b-12d3-a456-426614174000"

    def fake_run(command, **_kwargs):
        calls.append(command)
        return (
            0,
            '{"event":"reconciliation.error","posting_id":"'
            f'{raw_id}","exception":"ConnectError: 192.0.2.4 TOKEN=secret"}}\n',
        )

    monkeypatch.setattr(bundle, "_run", fake_run)
    manifest = {}
    since = bundle.datetime(2026, 8, 14, 9, 0, tzinfo=bundle.UTC)
    until = bundle.datetime(2026, 8, 15, 9, 0, tzinfo=bundle.UTC)

    bundle._collect_reconciliation_journal(
        tmp_path,
        manifest,
        since=since,
        until=until,
    )

    assert calls == [
        [
            "journalctl",
            "--unit",
            "jobseek-crawler-reconciliation.service",
            "--since",
            "@1786698000",
            "--until",
            "@1786784400",
            "--output=cat",
            "--quiet",
            "--no-pager",
        ]
    ]
    journal = (tmp_path / "host" / "cross-store-reconciliation.log").read_text()
    assert "reconciliation.error" in journal
    assert raw_id not in journal
    assert "192.0.2.4" not in journal
    assert "secret" not in journal
    assert journal.count("<redacted-resource-id>") == 1
    assert journal.count("<redacted-host-address>") == 1
    assert journal.count("TOKEN=<redacted>") == 1
    assert manifest["reconciliation_journal"] == {
        "unit": "jobseek-crawler-reconciliation.service",
        "returncode": 0,
        "window_filtered": True,
        "path": str(tmp_path / "host" / "cross-store-reconciliation.log"),
        "bytes": len(journal.encode()),
        "truncated": False,
    }


def test_collect_redis_capacity_evidence_is_windowed_and_manifested(tmp_path, monkeypatch):
    calls = []
    since = bundle.datetime(2026, 9, 13, 9, 0, tzinfo=bundle.UTC)
    until = bundle.datetime(2026, 9, 14, 9, 0, tzinfo=bundle.UTC)
    cache = tmp_path / "source-capacity.prom"
    cache.write_text(
        f"jobseek_redis_capacity_snapshot_unixtime {until.timestamp():.0f}\n"
        "jobseek_redis_capacity_used_memory_bytes 1024\n",
        encoding="utf-8",
    )

    def fake_run(command, **_kwargs):
        calls.append(command)
        return (
            0,
            "unrelated host line\njobseek_redis_capacity_refresh_failed error=ProbeError\n",
        )

    monkeypatch.setattr(bundle, "_run", fake_run)
    manifest = {}

    bundle._collect_redis_capacity_evidence(
        tmp_path,
        manifest,
        since=since,
        until=until,
        cache_path=cache,
    )

    assert calls == [
        [
            "journalctl",
            "--unit",
            "jobseek-host-observability.service",
            "--since",
            "@1789290000",
            "--until",
            "@1789376400",
            "--output=cat",
            "--quiet",
            "--no-pager",
        ]
    ]
    assert (tmp_path / "host" / "redis-capacity.prom").read_text() == (cache.read_text())
    journal = (tmp_path / "host" / "redis-capacity-observer.log").read_text()
    assert "refresh_failed" in journal
    assert "unrelated host line" not in journal
    assert manifest["redis_capacity"]["complete"] is True
    assert manifest["redis_capacity"]["cache"]["fresh"] is True
    assert manifest["redis_capacity"]["cache"]["age_seconds"] == 0
    assert manifest["redis_capacity"]["observer_journal"]["window_filtered"] is True


def test_collect_redis_capacity_evidence_marks_missing_source_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "_run", lambda *_args, **_kwargs: (0, ""))
    manifest = {}
    now = bundle.datetime(2026, 9, 14, 9, 0, tzinfo=bundle.UTC)

    bundle._collect_redis_capacity_evidence(
        tmp_path,
        manifest,
        since=now - bundle.timedelta(hours=24),
        until=now,
        cache_path=tmp_path / "missing.prom",
    )

    assert manifest["redis_capacity"]["complete"] is False
    assert manifest["redis_capacity"]["cache"]["available"] is False
    assert manifest["redis_capacity"]["cache"]["fresh"] is False
    assert manifest["redis_capacity"]["cache"]["age_seconds"] is None
    assert "unavailable" in (tmp_path / "host" / "redis-capacity.prom").read_text()


def test_collect_redis_capacity_evidence_rejects_stale_malformed_and_future_cache(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bundle, "_run", lambda *_args, **_kwargs: (0, ""))
    until = bundle.datetime(2026, 9, 14, 9, 0, tzinfo=bundle.UTC)
    values = (
        f"{until.timestamp() - bundle.REDIS_CAPACITY_EVIDENCE_MAX_AGE_SECONDS - 1:.0f}",
        "not-a-number",
        f"{until.timestamp() + 61:.0f}",
    )

    for index, value in enumerate(values):
        case_dir = tmp_path / str(index)
        cache = case_dir / "source.prom"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            f"jobseek_redis_capacity_snapshot_unixtime {value}\n",
            encoding="utf-8",
        )
        manifest = {}
        bundle._collect_redis_capacity_evidence(
            case_dir,
            manifest,
            since=until - bundle.timedelta(hours=24),
            until=until,
            cache_path=cache,
        )

        assert manifest["redis_capacity"]["complete"] is False
        assert manifest["redis_capacity"]["cache"]["fresh"] is False


def test_collect_disk_capacity_evidence_is_bounded_windowed_and_manifested(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return 0, "bounded evidence\n"

    monkeypatch.setattr(bundle, "_run", fake_run)
    manifest = {}
    since = bundle.datetime(2026, 9, 13, 9, 0, tzinfo=bundle.UTC)
    until = bundle.datetime(2026, 9, 14, 9, 0, tzinfo=bundle.UTC)

    bundle._collect_disk_capacity_evidence(
        tmp_path,
        manifest,
        since=since,
        until=until,
    )

    assert manifest["disk_capacity"]["complete"] is True
    assert set(manifest["disk_capacity"]["artifacts"]) == {
        "docker-system-df",
        "docker-container-sizes",
        "docker-images",
        "disk-attribution",
        "docker-gc-timer",
        "docker-gc-journal",
    }
    timer = next(
        call for call in calls if call[0][:3] == ["systemctl", "show", "jobseek-docker-gc.timer"]
    )
    assert timer == (
        [
            "systemctl",
            "show",
            "jobseek-docker-gc.timer",
            "--property=ActiveState",
            "--property=SubState",
            "--property=LastTriggerUSec",
            "--property=NextElapseUSecRealtime",
            "--property=NextElapseUSecMonotonic",
        ],
        30,
    )
    journal = calls[-1]
    assert journal == (
        [
            "journalctl",
            "--unit",
            "jobseek-docker-gc.service",
            "--since",
            "@1789290000",
            "--until",
            "@1789376400",
            "--output=cat",
            "--quiet",
            "--no-pager",
        ],
        180,
    )
    assert all(timeout <= 180 for _command, timeout in calls)
    assert (tmp_path / "host" / "docker-container-sizes.txt").read_text() == ("bounded evidence\n")


def test_collect_disk_capacity_evidence_fails_closed_on_command_error(tmp_path, monkeypatch):
    def fake_run(command, **_kwargs):
        if command[:3] == ["docker", "system", "df"]:
            return 1, "daemon error\n"
        return 0, "ok\n"

    monkeypatch.setattr(bundle, "_run", fake_run)
    manifest = {}
    now = bundle.datetime(2026, 9, 14, 9, 0, tzinfo=bundle.UTC)

    bundle._collect_disk_capacity_evidence(
        tmp_path,
        manifest,
        since=now - bundle.timedelta(hours=24),
        until=now,
    )

    assert manifest["disk_capacity"]["complete"] is False
    assert manifest["disk_capacity"]["artifacts"]["docker-system-df"]["returncode"] == 1


def test_collect_reconciliation_journal_marks_failed_collection_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bundle,
        "_run",
        lambda *_args, **_kwargs: (None, "TimeoutExpired: journalctl timed out\n"),
    )
    manifest = {}

    bundle._collect_reconciliation_journal(
        tmp_path,
        manifest,
        since=bundle.datetime(2026, 8, 14, 9, 0, tzinfo=bundle.UTC),
        until=bundle.datetime(2026, 8, 15, 9, 0, tzinfo=bundle.UTC),
    )

    assert manifest["reconciliation_journal"]["returncode"] is None
    assert manifest["reconciliation_journal"]["window_filtered"] is False
    assert "TimeoutExpired" in (tmp_path / "host" / "cross-store-reconciliation.log").read_text()
