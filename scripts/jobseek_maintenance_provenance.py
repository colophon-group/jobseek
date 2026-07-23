#!/usr/bin/env python3
"""Validate and correlate redacted Jobseek maintenance lifecycle evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc  # noqa: UP017 - production hosts still include Python 3.10.

OPERATION_LABEL = "jobseek.maintenance.operation"
ISSUE_LABEL = "jobseek.maintenance.issue"
REVISION_LABEL = "jobseek.maintenance.revision"
BUDGET_LABEL = "jobseek.maintenance.budget-seconds"
PROVENANCE_LABELS = (
    OPERATION_LABEL,
    ISSUE_LABEL,
    REVISION_LABEL,
    BUDGET_LABEL,
)

OPERATION_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
ISSUE_RE = re.compile(r"^[1-9][0-9]{0,9}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
MIN_BUDGET_SECONDS = 60
MAX_BUDGET_SECONDS = 8 * 60 * 60

CORRELATION_PADDING_SECONDS = 120
MERGE_GAP_SECONDS = 180
MONITORED_SERVICES = frozenset(
    {
        "redis",
        "worker-1",
        "worker-2",
        "worker-3",
        "browser-1",
        "exporter",
        "drain",
        "alloy",
    }
)

LIFECYCLE_FIELDS = frozenset(
    {
        "schema_version",
        "source",
        "observed_at",
        "event_at",
        "time_nano",
        "action",
        "container_generation",
        "container_name",
        "image",
        "compose_project",
        "compose_service",
        "compose_container_number",
        "compose_oneoff",
        "event_exit_code",
        "event_signal",
        "event_exec_duration",
        "maintenance_provenance_status",
        "return_code",
    }
)
STATE_FIELDS = frozenset(
    {
        "status",
        "running",
        "oom_killed",
        "exit_code",
        "started_at",
        "finished_at",
        "restart_count",
    }
)
GENERATION_RE = re.compile(r"^[0-9a-f]{16}$")


def container_generation(container_id: str) -> str:
    """Return a stable one-way identifier suitable for runner evidence."""
    return hashlib.sha256(container_id.encode("utf-8")).hexdigest()[:16]


def validate_provenance_labels(
    attributes: dict[str, object],
) -> tuple[str, dict[str, object] | None]:
    """Return missing, invalid, or a normalized all-or-nothing provenance set."""
    present = [label for label in PROVENANCE_LABELS if label in attributes]
    if not present:
        return "missing", None
    if len(present) != len(PROVENANCE_LABELS):
        return "invalid", None

    operation = str(attributes.get(OPERATION_LABEL, ""))
    issue_raw = str(attributes.get(ISSUE_LABEL, ""))
    revision = str(attributes.get(REVISION_LABEL, ""))
    budget_raw = str(attributes.get(BUDGET_LABEL, ""))
    if not OPERATION_RE.fullmatch(operation):
        return "invalid", None
    if not ISSUE_RE.fullmatch(issue_raw):
        return "invalid", None
    if not REVISION_RE.fullmatch(revision):
        return "invalid", None
    try:
        budget_seconds = int(budget_raw)
    except ValueError:
        return "invalid", None
    if not MIN_BUDGET_SECONDS <= budget_seconds <= MAX_BUDGET_SECONDS:
        return "invalid", None

    return (
        "validated",
        {
            "operation": operation,
            "issue": int(issue_raw),
            "revision": revision,
            "budget_seconds": budget_seconds,
        },
    )


def docker_label_arguments(
    *,
    operation: str,
    issue: int,
    revision: str,
    budget_seconds: int,
    compose_service: str,
) -> list[str]:
    """Return the exact Docker labels accepted by the lifecycle watcher."""
    status, normalized = validate_provenance_labels(
        {
            OPERATION_LABEL: operation,
            ISSUE_LABEL: str(issue),
            REVISION_LABEL: revision,
            BUDGET_LABEL: str(budget_seconds),
        }
    )
    if status != "validated" or normalized is None:
        raise ValueError("invalid maintenance provenance")
    if not OPERATION_RE.fullmatch(compose_service):
        raise ValueError("invalid maintenance Compose service")
    labels = {
        "com.docker.compose.project": "deploy",
        "com.docker.compose.service": compose_service,
        "com.docker.compose.container-number": "1",
        "com.docker.compose.oneoff": "True",
        OPERATION_LABEL: operation,
        ISSUE_LABEL: str(issue),
        REVISION_LABEL: revision,
        BUDGET_LABEL: str(budget_seconds),
    }
    arguments: list[str] = []
    for key, value in labels.items():
        arguments.extend(("--label", f"{key}={value}"))
    return arguments


def sanitize_lifecycle_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, object]]:
    """Allowlist runner-visible lifecycle fields and replace legacy raw IDs."""
    sanitized_events: list[dict[str, object]] = []
    for event in events:
        sanitized: dict[str, object] = {
            key: value
            for key, value in event.items()
            if key in LIFECYCLE_FIELDS and isinstance(value, (str, int, bool))
        }

        generation = event.get("container_generation")
        legacy_id = event.get("container_id")
        if isinstance(generation, str) and GENERATION_RE.fullmatch(generation):
            sanitized["container_generation"] = generation
        elif isinstance(legacy_id, str) and legacy_id:
            sanitized["container_generation"] = container_generation(legacy_id)
        else:
            sanitized.pop("container_generation", None)
        sanitized.pop("maintenance_provenance_status", None)
        if event.get("maintenance_provenance_status") == "invalid":
            sanitized["maintenance_provenance_status"] = "invalid"

        state = event.get("state")
        if isinstance(state, dict):
            sanitized_state = {
                key: value
                for key, value in state.items()
                if key in STATE_FIELDS and isinstance(value, (str, int, bool))
            }
            if sanitized_state:
                sanitized["state"] = sanitized_state

        provenance = event.get("maintenance_provenance")
        if isinstance(provenance, dict):
            provenance_status, normalized = validate_provenance_labels(
                {
                    OPERATION_LABEL: provenance.get("operation", ""),
                    ISSUE_LABEL: provenance.get("issue", ""),
                    REVISION_LABEL: provenance.get("revision", ""),
                    BUDGET_LABEL: provenance.get("budget_seconds", ""),
                }
            )
            if provenance_status == "validated" and normalized is not None:
                sanitized["maintenance_provenance"] = normalized
            else:
                sanitized["maintenance_provenance_status"] = "invalid"

        sanitized_events.append(sanitized)
    return sanitized_events


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _event_exit_code(event: dict[str, Any]) -> int | None:
    code = _safe_int(event.get("event_exit_code"))
    if code is not None:
        return code
    state = event.get("state")
    return _safe_int(state.get("exit_code")) if isinstance(state, dict) else None


def _event_oom(event: dict[str, Any]) -> bool:
    if event.get("action") == "oom":
        return True
    state = event.get("state")
    return bool(state.get("oom_killed", False)) if isinstance(state, dict) else False


def _event_identity(event: dict[str, Any]) -> str:
    generation = event.get("container_generation")
    if isinstance(generation, str) and generation:
        return f"generation:{generation}"
    name = event.get("container_name")
    return f"name:{name}" if isinstance(name, str) and name else ""


def _is_oneoff(event: dict[str, Any]) -> bool:
    return str(event.get("compose_oneoff", "")).lower() == "true"


def _provenance_key(provenance: dict[str, object]) -> tuple[str, int, str, int]:
    return (
        str(provenance["operation"]),
        int(provenance["issue"]),
        str(provenance["revision"]),
        int(provenance["budget_seconds"]),
    )


def _maintenance_intervals(
    events: list[tuple[datetime, dict[str, Any]]],
) -> list[dict[str, Any]]:
    by_container: dict[str, dict[str, Any]] = {}
    for event_at, event in events:
        provenance = event.get("maintenance_provenance")
        if not isinstance(provenance, dict):
            continue
        identity = _event_identity(event)
        if not identity:
            continue
        key = _provenance_key(provenance)
        interval = by_container.setdefault(
            identity,
            {
                "provenance_key": key,
                "start": event_at,
                "end": event_at,
                "compose_service": str(event.get("compose_service", "")),
                "exit_code": None,
                "oom": False,
            },
        )
        if interval["provenance_key"] != key:
            interval["conflicting_provenance"] = True
            continue
        interval["start"] = min(interval["start"], event_at)
        interval["end"] = max(interval["end"], event_at)
        code = _event_exit_code(event)
        if code is not None and event.get("action") in {"die", "destroy"}:
            interval["exit_code"] = code
        interval["oom"] = bool(interval["oom"]) or _event_oom(event)
    return [
        interval for interval in by_container.values() if not interval.get("conflicting_provenance")
    ]


def _merge_maintenance_intervals(
    intervals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_provenance: dict[tuple[str, int, str, int], list[dict[str, Any]]] = {}
    for interval in intervals:
        by_provenance.setdefault(interval["provenance_key"], []).append(interval)

    windows: list[dict[str, Any]] = []
    for provenance_key, candidates in by_provenance.items():
        candidates.sort(key=lambda item: item["start"])
        current: dict[str, Any] | None = None
        for interval in candidates:
            if (
                current is None
                or (interval["start"] - current["end"]).total_seconds() > MERGE_GAP_SECONDS
            ):
                if current is not None:
                    windows.append(current)
                current = {
                    "provenance_key": provenance_key,
                    "start": interval["start"],
                    "end": interval["end"],
                    "oneoffs": [interval],
                    "service_pauses": [],
                }
            else:
                current["end"] = max(current["end"], interval["end"])
                current["oneoffs"].append(interval)
        if current is not None:
            windows.append(current)
    return sorted(windows, key=lambda item: item["start"])


def _service_pauses(
    events: list[tuple[datetime, dict[str, Any]]],
) -> list[dict[str, Any]]:
    by_service: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for event_at, event in events:
        service = str(event.get("compose_service", ""))
        if service not in MONITORED_SERVICES or _is_oneoff(event):
            continue
        action = str(event.get("action", ""))
        if action not in {"kill", "stop", "die", "oom", "start", "restart"}:
            continue
        by_service.setdefault(service, []).append((event_at, event))

    pauses: list[dict[str, Any]] = []
    review_end = max((event_at for event_at, _ in events), default=None)
    for service, service_events in sorted(by_service.items()):
        service_events.sort(key=lambda item: item[0])
        active: dict[str, Any] | None = None
        for event_at, event in service_events:
            action = str(event.get("action", ""))
            if action in {"start", "restart"}:
                if active is not None:
                    active["restored_at"] = event_at
                    active["restored"] = True
                    active["end"] = event_at
                    pauses.append(active)
                    active = None
                continue
            if active is None:
                active = {
                    "service": service,
                    "start": event_at,
                    "end": event_at,
                    "restored": False,
                    "forced_termination": False,
                    "oom": False,
                    "docker_api_stop": False,
                    "native_exit": action == "die",
                }
            active["end"] = max(active["end"], event_at)
            active["oom"] = bool(active["oom"]) or _event_oom(event)
            signal = _safe_int(event.get("event_signal"))
            code = _event_exit_code(event)
            if action in {"kill", "stop"}:
                active["docker_api_stop"] = True
                active["native_exit"] = False
            if signal == 9 or (code == 137 and not active["oom"]):
                active["forced_termination"] = True
            if action == "die" and not active["docker_api_stop"] and not active["oom"]:
                active["native_exit"] = True
        if active is not None:
            active["end"] = review_end or active["end"]
            pauses.append(active)
    return pauses


def _candidate_windows(
    pause: dict[str, Any],
    windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    padding = CORRELATION_PADDING_SECONDS
    return [
        window
        for window in windows
        if pause["end"].timestamp() >= window["start"].timestamp() - padding
        and pause["start"].timestamp() <= window["end"].timestamp() + padding
    ]


def _window_distance(pause: dict[str, Any], window: dict[str, Any]) -> float:
    if pause["end"] < window["start"]:
        return (window["start"] - pause["end"]).total_seconds()
    if pause["start"] > window["end"]:
        return (pause["start"] - window["end"]).total_seconds()
    return 0.0


def _public_pause(pause: dict[str, Any]) -> dict[str, object]:
    end = pause.get("restored_at") or pause["end"]
    result: dict[str, object] = {
        "service": pause["service"],
        "paused_at": _iso(pause["start"]),
        "restored": bool(pause["restored"]),
        "downtime_seconds": round((end - pause["start"]).total_seconds(), 3),
        "forced_termination": bool(pause["forced_termination"]),
        "oom": bool(pause["oom"]),
        "native_exit": bool(pause["native_exit"]),
    }
    if pause.get("restored_at"):
        result["restored_at"] = _iso(pause["restored_at"])
    return result


def _public_window(window: dict[str, Any]) -> dict[str, object]:
    operation, issue, revision, budget_seconds = window["provenance_key"]
    pauses = sorted(window["service_pauses"], key=lambda item: (item["start"], item["service"]))
    start = min([window["start"], *(pause["start"] for pause in pauses)])
    end = max(
        [
            window["end"],
            *(pause.get("restored_at") or pause["end"] for pause in pauses),
        ]
    )
    reasons: set[str] = set()
    oneoffs: list[dict[str, object]] = []
    for interval in sorted(window["oneoffs"], key=lambda item: item["start"]):
        code = interval.get("exit_code")
        is_marker = interval["compose_service"] == "maintenance-window"
        if code not in (None, 0) and not is_marker:
            reasons.add("oneoff_nonzero_exit")
        if interval.get("oom"):
            reasons.add("oom")
        if is_marker:
            continue
        oneoffs.append(
            {
                "service": interval["compose_service"],
                "started_at": _iso(interval["start"]),
                "finished_at": _iso(interval["end"]),
                "exit_code": code,
                "oom": bool(interval.get("oom")),
            }
        )
    for pause in pauses:
        if pause["forced_termination"]:
            reasons.add("forced_termination")
        if pause["oom"]:
            reasons.add("oom")
        if pause["native_exit"]:
            reasons.add("native_service_exit")
        if not pause["restored"]:
            reasons.add("failed_restoration")
    duration_seconds = (end - start).total_seconds()
    if duration_seconds > budget_seconds:
        reasons.add("budget_overrun")

    public_pauses = [_public_pause(pause) for pause in pauses]
    result: dict[str, object] = {
        "operation": operation,
        "issue": issue,
        "revision": revision,
        "budget_seconds": budget_seconds,
        "started_at": _iso(start),
        "finished_at": _iso(end),
        "duration_seconds": round(duration_seconds, 3),
        "status": "actionable" if reasons else "completed",
        "actionable_reasons": sorted(reasons),
        "oneoffs": oneoffs,
        "service_pauses": public_pauses,
    }
    if public_pauses:
        fleet_start = min(pause["start"] for pause in pauses)
        fleet_end = max(pause.get("restored_at") or pause["end"] for pause in pauses)
        result["fleet_downtime_seconds"] = round(
            (fleet_end - fleet_start).total_seconds(),
            3,
        )
    return result


def correlate_events(events: Iterable[dict[str, Any]]) -> dict[str, object]:
    """Build a deterministic, redacted maintenance correlation summary."""
    parsed: list[tuple[datetime, dict[str, Any]]] = []
    invalid_provenance_events = 0
    for event in events:
        event_at = _parse_time(event.get("event_at"))
        if event_at is None or event.get("source") != "docker_event":
            continue
        if event.get("maintenance_provenance_status") == "invalid":
            invalid_provenance_events += 1
        parsed.append((event_at, event))
    parsed.sort(key=lambda item: item[0])

    windows = _merge_maintenance_intervals(_maintenance_intervals(parsed))
    unattributed: list[dict[str, object]] = []
    for pause in _service_pauses(parsed):
        candidates = _candidate_windows(pause, windows)
        exact_candidates = [
            candidate for candidate in candidates if _window_distance(pause, candidate) == 0
        ]
        preferred_candidates = exact_candidates or candidates
        provenance_keys = {candidate["provenance_key"] for candidate in preferred_candidates}
        if len(provenance_keys) == 1:
            selected = min(
                preferred_candidates,
                key=lambda item: _window_distance(pause, item),
            )
            selected["service_pauses"].append(pause)
            continue
        public_pause = _public_pause(pause)
        public_pause["reason"] = (
            "ambiguous_provenance" if len(provenance_keys) > 1 else "missing_provenance"
        )
        unattributed.append(public_pause)

    return {
        "schema_version": 1,
        "maintenance_windows": [_public_window(window) for window in windows],
        "unattributed_service_pauses": unattributed,
        "invalid_provenance_events": invalid_provenance_events,
    }


def parse_jsonl(text: str) -> list[dict[str, Any]]:
    """Parse object rows and ignore malformed/non-object journal messages."""
    import json

    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events
