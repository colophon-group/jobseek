"""Tests for the durable, allowlisted Docker lifecycle watcher."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "codex-docker-lifecycle-watch.py"
SPEC = importlib.util.spec_from_file_location("codex_docker_lifecycle_watch", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
watcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watcher)


def _event(action: str = "die") -> dict[str, object]:
    return {
        "Type": "container",
        "Action": action,
        "Actor": {
            "ID": "abc123",
            "Attributes": {
                "com.docker.compose.project": "deploy",
                "com.docker.compose.service": "worker-1",
                "com.docker.compose.container-number": "1",
                "com.docker.compose.oneoff": "False",
                "name": "deploy-worker-1-1",
                "image": "ghcr.io/colophon-group/jobseek-crawler:v0.13.131",
                "exitCode": "137",
                "signal": "9",
                "secret-token": "must-not-be-journaled",
                "com.docker.compose.project.config_files": "/home/deploy/docker-compose.yml",
            },
        },
        "time": 1_784_633_655,
        "timeNano": 1_784_633_655_153_406_410,
    }


def test_normalize_event_allowlists_lifecycle_evidence(monkeypatch):
    monkeypatch.setattr(watcher, "_now_iso", lambda: "2026-07-21T10:00:00+00:00")

    result = watcher._normalize_event(
        _event(),
        inspected_state={
            "status": "exited",
            "oom_killed": False,
            "exit_code": 137,
            "restart_count": 4,
        },
    )

    assert result == {
        "schema_version": 2,
        "source": "docker_event",
        "observed_at": "2026-07-21T10:00:00+00:00",
        "event_at": "2026-07-21T11:34:15.153406+00:00",
        "time_nano": 1_784_633_655_153_406_410,
        "action": "die",
        "container_generation": "6ca13d52ca70c883",
        "container_name": "deploy-worker-1-1",
        "image": "ghcr.io/colophon-group/jobseek-crawler:v0.13.131",
        "compose_project": "deploy",
        "compose_service": "worker-1",
        "compose_container_number": "1",
        "compose_oneoff": "False",
        "event_exit_code": "137",
        "event_signal": "9",
        "state": {
            "status": "exited",
            "oom_killed": False,
            "exit_code": 137,
            "restart_count": 4,
        },
    }
    assert "secret" not in str(result).lower()
    assert "config_files" not in str(result)


def test_normalize_event_allowlists_only_complete_valid_maintenance_provenance(monkeypatch):
    monkeypatch.setattr(watcher, "_now_iso", lambda: "2026-07-23T05:00:00+00:00")
    event = _event("start")
    attributes = event["Actor"]["Attributes"]  # type: ignore[index]
    attributes.update(  # type: ignore[union-attr]
        {
            "jobseek.maintenance.operation": "repair-relisted-cdc",
            "jobseek.maintenance.issue": "6016",
            "jobseek.maintenance.revision": "a" * 40,
            "jobseek.maintenance.budget-seconds": "1800",
            "jobseek.maintenance.secret": "must-not-be-journaled",
            "arbitrary": "must-not-be-journaled",
        }
    )

    result = watcher._normalize_event(event)

    assert result is not None
    assert result["maintenance_provenance"] == {
        "operation": "repair-relisted-cdc",
        "issue": 6016,
        "revision": "a" * 40,
        "budget_seconds": 1800,
    }
    assert "must-not-be-journaled" not in str(result)
    assert "maintenance.secret" not in str(result)


def test_normalize_event_fails_closed_on_partial_or_invalid_provenance():
    event = _event("start")
    attributes = event["Actor"]["Attributes"]  # type: ignore[index]
    attributes.update(  # type: ignore[union-attr]
        {
            "jobseek.maintenance.operation": "repair-relisted-cdc",
            "jobseek.maintenance.issue": "not-an-issue",
        }
    )

    result = watcher._normalize_event(event)

    assert result is not None
    assert result["maintenance_provenance_status"] == "invalid"
    assert "maintenance_provenance" not in result
    assert "not-an-issue" not in str(result)


def test_normalize_event_ignores_healthchecks_and_other_projects():
    assert watcher._normalize_event(_event("exec_die")) is None
    foreign = _event()
    foreign["Actor"]["Attributes"]["com.docker.compose.project"] = "other"  # type: ignore[index]
    assert watcher._normalize_event(foreign) is None


def test_docker_events_command_filters_before_the_volatile_buffer_is_streamed():
    command = watcher._docker_events_command("5m")

    assert command[:5] == ["docker", "events", "--since", "5m", "--format"]
    assert "label=com.docker.compose.project=deploy" in command
    assert "event=die" in command
    assert "event=oom" in command
    assert "event=exec_die" not in command


def test_since_value_preserves_nanosecond_position_for_reconnects():
    assert watcher._since_value(None) == "5m"
    assert watcher._since_value(1_784_633_655_153_406_410) == "1784633655.153406410"
