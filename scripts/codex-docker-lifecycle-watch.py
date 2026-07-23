#!/usr/bin/env python3
"""Persist a redacted stream of Jobseek Docker lifecycle events.

Docker keeps only a small in-memory event history.  This watcher normalizes
the lifecycle fields needed by error review and writes them to stdout, where
systemd-journald provides durable retention.  Actor attributes are allowlisted
so environment values, commands, and arbitrary labels never reach the journal.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from jobseek_maintenance_provenance import (  # noqa: E402
    container_generation,
    validate_provenance_labels,
)

UTC = timezone.utc  # noqa: UP017 - the production host runs Python 3.10.

COMPOSE_PROJECT = "deploy"
LIFECYCLE_ACTIONS = (
    "create",
    "start",
    "restart",
    "die",
    "oom",
    "kill",
    "stop",
    "destroy",
)
INSPECT_ACTIONS = frozenset(("start", "restart", "die", "oom", "kill", "stop"))
MAX_SEEN_EVENTS = 2048


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _event_time_iso(time_nano: int) -> str:
    seconds, nanos = divmod(time_nano, 1_000_000_000)
    value = datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=nanos // 1000)
    return value.isoformat()


def _since_value(time_nano: int | None) -> str:
    if time_nano is None:
        # Recover lifecycle events that occurred while the unit was being
        # installed or restarted.  Dedupe protects reconnects from replay.
        return "5m"
    seconds, nanos = divmod(time_nano, 1_000_000_000)
    return f"{seconds}.{nanos:09d}"


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _inspect_container(container_id: str) -> dict[str, object] | None:
    """Return an allowlisted Docker state snapshot, if the object still exists."""
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        inspect = json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError, TypeError):
        return None

    state = inspect.get("State", {})
    if not isinstance(state, dict):
        state = {}
    snapshot: dict[str, object] = {
        "status": str(state.get("Status", "")),
        "running": bool(state.get("Running", False)),
        "oom_killed": bool(state.get("OOMKilled", False)),
        "exit_code": _safe_int(state.get("ExitCode")),
        "started_at": str(state.get("StartedAt", "")),
        "finished_at": str(state.get("FinishedAt", "")),
        "restart_count": _safe_int(inspect.get("RestartCount")),
    }
    return {key: value for key, value in snapshot.items() if value not in (None, "")}


def _normalize_event(
    event: dict[str, Any],
    *,
    inspected_state: dict[str, object] | None = None,
) -> dict[str, object] | None:
    """Normalize one Docker event without copying arbitrary actor attributes."""
    if event.get("Type") != "container":
        return None
    action = str(event.get("Action") or event.get("status") or "").split(":", 1)[0]
    if action not in LIFECYCLE_ACTIONS:
        return None

    actor = event.get("Actor", {})
    if not isinstance(actor, dict):
        return None
    container_id = str(actor.get("ID", ""))
    if not container_id:
        return None
    attributes = actor.get("Attributes", {})
    if not isinstance(attributes, dict):
        return None
    if attributes.get("com.docker.compose.project") != COMPOSE_PROJECT:
        return None

    time_nano = _safe_int(event.get("timeNano"))
    if time_nano is None:
        seconds = _safe_int(event.get("time"))
        if seconds is None:
            return None
        time_nano = seconds * 1_000_000_000

    record: dict[str, object] = {
        "schema_version": 2,
        "source": "docker_event",
        "observed_at": _now_iso(),
        "event_at": _event_time_iso(time_nano),
        "time_nano": time_nano,
        "action": action,
        "container_generation": container_generation(container_id),
        "container_name": str(attributes.get("name", "")),
        "image": str(attributes.get("image", "")),
        "compose_project": COMPOSE_PROJECT,
        "compose_service": str(attributes.get("com.docker.compose.service", "")),
        "compose_container_number": str(attributes.get("com.docker.compose.container-number", "")),
        "compose_oneoff": str(attributes.get("com.docker.compose.oneoff", "")),
    }
    for source, target in (
        ("exitCode", "event_exit_code"),
        ("signal", "event_signal"),
        ("execDuration", "event_exec_duration"),
    ):
        value = attributes.get(source)
        if value not in (None, ""):
            record[target] = str(value)
    provenance_status, provenance = validate_provenance_labels(attributes)
    if provenance_status == "validated" and provenance is not None:
        record["maintenance_provenance"] = provenance
    elif provenance_status == "invalid":
        record["maintenance_provenance_status"] = "invalid"
    if inspected_state:
        record["state"] = inspected_state
    return record


def _docker_events_command(since: str) -> list[str]:
    command = [
        "docker",
        "events",
        "--since",
        since,
        "--format",
        "{{json .}}",
        "--filter",
        "type=container",
        "--filter",
        f"label=com.docker.compose.project={COMPOSE_PROJECT}",
    ]
    for action in LIFECYCLE_ACTIONS:
        command.extend(("--filter", f"event={action}"))
    return command


def watch() -> None:
    last_time_nano: int | None = None
    seen_order: deque[tuple[int, str, str]] = deque()
    seen: set[tuple[int, str, str]] = set()
    retry_seconds = 1

    while True:
        try:
            process = subprocess.Popen(
                _docker_events_command(_since_value(last_time_nano)),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source": "watcher",
                        "observed_at": _now_iso(),
                        "action": "connect_error",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            time.sleep(retry_seconds)
            retry_seconds = min(retry_seconds * 2, 30)
            continue

        assert process.stdout is not None
        received_event = False
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            time_nano = _safe_int(event.get("timeNano"))
            actor = event.get("Actor", {})
            container_id = str(actor.get("ID", "")) if isinstance(actor, dict) else ""
            action = str(event.get("Action") or event.get("status") or "").split(":", 1)[0]
            if time_nano is None:
                continue
            last_time_nano = max(last_time_nano or 0, time_nano)
            key = (time_nano, container_id, action)
            if key in seen:
                continue
            seen.add(key)
            seen_order.append(key)
            if len(seen_order) > MAX_SEEN_EVENTS:
                seen.discard(seen_order.popleft())

            inspected = _inspect_container(container_id) if action in INSPECT_ACTIONS else None
            normalized = _normalize_event(event, inspected_state=inspected)
            if normalized is None:
                continue
            print(json.dumps(normalized, sort_keys=True), flush=True)
            received_event = True

        return_code = process.wait()
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "source": "watcher",
                    "observed_at": _now_iso(),
                    "action": "stream_closed",
                    "return_code": return_code,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        retry_seconds = 1 if received_event else min(retry_seconds * 2, 30)
        time.sleep(retry_seconds)


def main() -> int:
    watch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
