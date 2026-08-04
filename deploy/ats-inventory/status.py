#!/usr/bin/env python3
"""Persist bounded, credential-free ATS inventory runner status and history."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_HISTORY = 32


class StatusError(RuntimeError):
    pass


def extract_complete_report(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > MAX_LOG_BYTES:
            raise StatusError("runner log exceeded the bounded parser size")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise StatusError("runner log is unreadable") from exc
    report = None
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("event") == "ats_inventory.complete"
            and isinstance(payload.get("report"), dict)
        ):
            report = payload["report"]
    return report


def _read_previous(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o770)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record(
    state_root: Path,
    log_path: Path,
    *,
    return_code: int,
    requested_mode: str,
    effective_mode: str,
    rollout_cap: int,
    started_at: int,
    finished_at: int | None = None,
) -> dict[str, Any]:
    finished = int(time.time()) if finished_at is None else finished_at
    report = extract_complete_report(log_path)
    success = return_code == 0 and report is not None
    current_path = state_root / "status" / "current.json"
    previous = _read_previous(current_path)
    payload: dict[str, Any] = {
        "schema": 1,
        "last_attempt_unixtime": finished,
        "last_attempt_started_unixtime": started_at,
        "last_attempt_duration_seconds": max(0, finished - started_at),
        "last_attempt_success": int(success),
        "last_success_unixtime": (
            finished if success else int(previous.get("last_success_unixtime") or 0)
        ),
        "return_code": return_code,
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "rollout_cap": rollout_cap,
        "report": report if success else None,
        "last_success_report": (
            report
            if success
            else previous.get("last_success_report") or previous.get("report")
        ),
    }
    _atomic_json(current_path, payload)
    history = state_root / "status" / "history"
    history.mkdir(parents=True, exist_ok=True, mode=0o770)
    _atomic_json(history / f"{finished:020d}.json", payload)
    entries = sorted(history.glob("*.json"))
    for stale in entries[:-MAX_HISTORY]:
        stale.unlink(missing_ok=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--return-code", type=int, required=True)
    parser.add_argument("--requested-mode", choices=("report", "dry-run", "refill"), required=True)
    parser.add_argument("--effective-mode", choices=("report", "dry-run", "refill"), required=True)
    parser.add_argument("--rollout-cap", type=int, choices=(1, 5, 25), required=True)
    parser.add_argument("--started-at", type=int, required=True)
    args = parser.parse_args()
    record(
        args.state_root,
        args.log,
        return_code=args.return_code,
        requested_mode=args.requested_mode,
        effective_mode=args.effective_mode,
        rollout_cap=args.rollout_cap,
        started_at=args.started_at,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StatusError as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        raise SystemExit(1) from None
