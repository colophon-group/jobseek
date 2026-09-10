#!/usr/bin/env python3
"""Atomically record the last attempt and success of a Codex host routine."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_STATUS = Path("/srv/jobseek-codex/state/error-review-status.json")
VALID_RESULTS = frozenset(
    {
        "success",
        "protocol",
        "timeout",
        "exit-code",
        "signal",
        "core-dump",
        "watchdog",
        "start-limit-hit",
        "resources",
        "oom-kill",
    }
)
SUCCESS_EXIT_CODE = "exited"
SUCCESS_EXIT_STATUS = "0"
ALREADY_COMPLETED_EXIT_STATUS = "10"


class RoutineStatusError(RuntimeError):
    """A routine status record is invalid or cannot be persisted."""


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutineStatusError(f"cannot read valid routine status from {path}") from exc
    if not isinstance(value, dict):
        raise RoutineStatusError("routine status must be a JSON object")
    return value


def _timestamp(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise RoutineStatusError(f"cannot atomically write routine status to {path}") from exc


def begin(path: Path, *, now: int | None = None) -> dict[str, Any]:
    timestamp = int(time.time()) if now is None else now
    previous = _load(path)
    record = {
        "schema_version": 1,
        "last_attempt_unixtime": timestamp,
        "last_success_unixtime": _timestamp(previous.get("last_success_unixtime")),
        "last_attempt_success": 0,
        "run_in_progress": 1,
        "last_result": "running",
    }
    _write(path, record)
    return record


def finish(
    path: Path,
    service_result: str,
    exit_code: str,
    exit_status: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    result = service_result.strip().lower()
    if result not in VALID_RESULTS:
        raise RoutineStatusError("unrecognized systemd service result")
    main_exit_code = exit_code.strip().lower()
    main_exit_status = exit_status.strip()
    completed = (
        result == "success"
        and main_exit_code == SUCCESS_EXIT_CODE
        and main_exit_status == SUCCESS_EXIT_STATUS
    )
    already_completed = (
        result == "success"
        and main_exit_code == SUCCESS_EXIT_CODE
        and main_exit_status == ALREADY_COMPLETED_EXIT_STATUS
    )
    if result == "success" and not (completed or already_completed):
        result = "protocol"
    timestamp = int(time.time()) if now is None else now
    previous = _load(path)
    attempt = _timestamp(previous.get("last_attempt_unixtime")) or timestamp
    success = completed or already_completed
    previous_success = _timestamp(previous.get("last_success_unixtime"))
    last_success = timestamp if completed else previous_success
    record = {
        "schema_version": 1,
        "last_attempt_unixtime": attempt,
        "last_success_unixtime": last_success,
        "last_attempt_success": int(success),
        "run_in_progress": 0,
        "last_result": "already-completed" if already_completed else result,
    }
    _write(path, record)
    return record


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-file", type=Path, default=DEFAULT_STATUS)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("begin")
    finish_parser = commands.add_parser("finish")
    finish_parser.add_argument("--service-result", required=True)
    finish_parser.add_argument("--exit-code", required=True)
    finish_parser.add_argument("--exit-status", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "begin":
        begin(args.status_file)
    else:
        finish(args.status_file, args.service_result, args.exit_code, args.exit_status)
    print("recorded Codex routine status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
