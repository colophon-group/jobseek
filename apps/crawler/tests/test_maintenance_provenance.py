"""Deterministic maintenance provenance and lifecycle correlation tests."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts/jobseek_maintenance_provenance.py"
FIXTURE_PATH = Path(__file__).parent / "fixtures/docker-lifecycle-july23-maintenance.jsonl"
SPEC = importlib.util.spec_from_file_location("jobseek_maintenance_provenance", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
provenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provenance)


def _fixture_events() -> list[dict[str, object]]:
    return provenance.parse_jsonl(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_july_23_fixture_attributes_6016_and_retains_maintenance_outcomes():
    summary = provenance.correlate_events(_fixture_events())

    assert summary["invalid_provenance_events"] == 0
    assert summary["unattributed_service_pauses"] == []
    windows = summary["maintenance_windows"]
    assert len(windows) == 1
    window = windows[0]
    assert window["operation"] == "repair-relisted-cdc"
    assert window["issue"] == 6016
    assert window["revision"] == "b" * 40
    assert window["status"] == "actionable"
    assert window["actionable_reasons"] == [
        "forced_termination",
        "oneoff_nonzero_exit",
    ]
    first_worker_2_pause = next(
        pause
        for pause in window["service_pauses"]
        if pause["service"] == "worker-2" and pause["paused_at"].startswith("2026-07-23T04:17:40")
    )
    assert first_worker_2_pause["forced_termination"] is True
    assert first_worker_2_pause["oom"] is False
    assert first_worker_2_pause["downtime_seconds"] == pytest.approx(1182.988, abs=0.001)
    assert window["fleet_downtime_seconds"] == pytest.approx(1312.688, abs=0.001)


def test_same_july_23_sequence_without_validated_labels_remains_unattributed():
    events = _fixture_events()
    for event in events:
        event.pop("maintenance_provenance", None)

    summary = provenance.correlate_events(events)

    assert summary["maintenance_windows"] == []
    assert len(summary["unattributed_service_pauses"]) == 8
    assert {pause["reason"] for pause in summary["unattributed_service_pauses"]} == {
        "missing_provenance"
    }


def test_oom_inside_labelled_window_remains_actionable():
    events = _fixture_events()
    worker_event = next(
        event
        for event in events
        if event.get("compose_service") == "worker-1"
        and str(event.get("event_at", "")).startswith("2026-07-23T04:17:40")
    )
    worker_event["action"] = "oom"
    worker_event["state"] = {"oom_killed": True}

    summary = provenance.correlate_events(events)

    window = summary["maintenance_windows"][0]
    assert "oom" in window["actionable_reasons"]
    assert any(pause["oom"] for pause in window["service_pauses"])


def test_graceful_sigterm_exit_is_not_reported_as_forced_termination():
    events = _fixture_events()
    worker_event = copy.deepcopy(
        next(
            event
            for event in events
            if event.get("compose_service") == "worker-1"
            and event.get("action") == "kill"
            and str(event.get("event_at", "")).startswith("2026-07-23T04:17:40")
        )
    )
    worker_event.update(
        {
            "action": "die",
            "event_at": "2026-07-23T04:17:41+00:00",
            "event_exit_code": "143",
        }
    )
    events.append(worker_event)

    summary = provenance.correlate_events(events)

    pause = next(
        event
        for event in summary["maintenance_windows"][0]["service_pauses"]
        if event["service"] == "worker-1" and event["paused_at"].startswith("2026-07-23T04:17:40")
    )
    assert pause["forced_termination"] is False


def test_expected_marker_sigterm_brackets_window_without_becoming_a_failed_oneoff():
    provenance_contract = {
        "operation": "crawler-deploy",
        "issue": 3409,
        "revision": "d" * 40,
        "budget_seconds": 1800,
    }
    events = [
        {
            "source": "docker_event",
            "event_at": "2026-07-23T05:00:00+00:00",
            "action": "start",
            "container_generation": "marker-generation",
            "compose_service": "maintenance-window",
            "compose_oneoff": "True",
            "maintenance_provenance": provenance_contract,
        },
        {
            "source": "docker_event",
            "event_at": "2026-07-23T05:00:10+00:00",
            "action": "stop",
            "container_generation": "worker-generation",
            "compose_service": "worker-1",
            "compose_oneoff": "False",
        },
        {
            "source": "docker_event",
            "event_at": "2026-07-23T05:01:00+00:00",
            "action": "start",
            "container_generation": "worker-generation",
            "compose_service": "worker-1",
            "compose_oneoff": "False",
        },
        {
            "source": "docker_event",
            "event_at": "2026-07-23T05:01:01+00:00",
            "action": "die",
            "container_generation": "marker-generation",
            "compose_service": "maintenance-window",
            "compose_oneoff": "True",
            "event_exit_code": "143",
            "maintenance_provenance": provenance_contract,
        },
    ]

    summary = provenance.correlate_events(events)

    assert summary["unattributed_service_pauses"] == []
    window = summary["maintenance_windows"][0]
    assert window["status"] == "completed"
    assert window["oneoffs"] == []
    assert window["service_pauses"][0]["downtime_seconds"] == 50.0


def test_missing_service_restoration_remains_actionable():
    events = [
        event
        for event in _fixture_events()
        if not (
            event.get("action") == "start"
            and str(event.get("event_at", "")).startswith("2026-07-23T04:39:32")
        )
    ]

    summary = provenance.correlate_events(events)

    window = summary["maintenance_windows"][0]
    assert "failed_restoration" in window["actionable_reasons"]
    assert any(not pause["restored"] for pause in window["service_pauses"])


def test_conflicting_overlapping_labelled_oneoff_does_not_guess_attribution():
    events = _fixture_events()
    conflicting = copy.deepcopy(
        next(event for event in events if event.get("maintenance_provenance"))
    )
    conflicting["container_generation"] = "unrelated-oneoff"
    conflicting["compose_service"] = "unrelated-maintenance"
    conflicting["event_at"] = "2026-07-23T04:20:00+00:00"
    conflicting["action"] = "start"
    conflicting["maintenance_provenance"] = {
        "operation": "unrelated-maintenance",
        "issue": 9999,
        "revision": "c" * 40,
        "budget_seconds": 600,
    }
    conflicting_end = copy.deepcopy(conflicting)
    conflicting_end["event_at"] = "2026-07-23T04:20:30+00:00"
    conflicting_end["action"] = "die"
    conflicting_end["event_exit_code"] = "0"
    events.extend((conflicting, conflicting_end))

    summary = provenance.correlate_events(events)

    assert any(
        pause["reason"] == "ambiguous_provenance"
        for pause in summary["unattributed_service_pauses"]
    )


def test_correlation_output_omits_container_identity_and_arbitrary_metadata():
    events = _fixture_events()
    events[0]["command"] = "must-not-leak"
    events[0]["environment"] = {"TOKEN": "must-not-leak"}
    events[0]["arbitrary_labels"] = {"cloud-resource-id": "must-not-leak"}

    rendered = json.dumps(provenance.correlate_events(events), sort_keys=True)

    assert "container_generation" not in rendered
    assert "container_name" not in rendered
    assert "must-not-leak" not in rendered


def test_runner_lifecycle_sanitizer_hashes_legacy_ids_and_drops_unknown_fields():
    raw_id = "a" * 64
    events = [
        {
            "schema_version": 1,
            "source": "docker_event",
            "event_at": "2026-07-23T04:17:40+00:00",
            "action": "die",
            "container_id": raw_id,
            "container_name": "deploy-worker-1-1",
            "command": "must-not-leak",
            "environment": {"TOKEN": "must-not-leak"},
            "labels": {"cloud-resource-id": "must-not-leak"},
            "state": {
                "exit_code": 137,
                "oom_killed": False,
                "raw_resource_id": "must-not-leak",
            },
        }
    ]

    sanitized = provenance.sanitize_lifecycle_events(events)
    rendered = json.dumps(sanitized, sort_keys=True)

    assert sanitized[0]["container_generation"] == provenance.container_generation(raw_id)
    assert raw_id not in rendered
    assert "container_id" not in rendered
    assert "must-not-leak" not in rendered
