#!/usr/bin/env python3
"""Detect stale resources created outside the crawler's managed services."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
UTC = timezone.utc


class HygieneError(RuntimeError):
    """Raised when host inventory cannot be inspected safely."""


@dataclass(frozen=True)
class Finding:
    kind: str
    name: str
    age_seconds: float
    detail: str
    cleanup: str


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise HygieneError(f"{' '.join(command)} failed: {detail}")
    return result.stdout


def _parse_started_at(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    # Docker emits nanoseconds, while the host's Python 3.10 ISO parser accepts
    # at most six fractional digits.
    normalized = re.sub(r"(\.\d{6})\d+([+-]\d{2}:\d{2})$", r"\1\2", normalized)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HygieneError(f"invalid Docker StartedAt timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise HygieneError(f"Docker StartedAt timestamp lacks a timezone: {value!r}")
    return parsed.astimezone(UTC)


def _container_findings(now: datetime, max_age_seconds: float) -> list[Finding]:
    ids = _run(["docker", "ps", "-q"]).split()
    if not ids:
        return []

    try:
        containers = json.loads(_run(["docker", "inspect", *ids]))
    except json.JSONDecodeError as exc:
        raise HygieneError("docker inspect returned invalid JSON") from exc

    findings: list[Finding] = []
    for container in containers:
        config = container.get("Config") or {}
        labels = config.get("Labels") or {}
        if labels.get(COMPOSE_PROJECT_LABEL):
            continue

        state = container.get("State") or {}
        started_at = _parse_started_at(str(state.get("StartedAt", "")))
        age_seconds = max(0.0, (now - started_at).total_seconds())
        if age_seconds < max_age_seconds:
            continue

        container_id = str(container.get("Id", ""))[:12] or "unknown"
        name = str(container.get("Name", "")).lstrip("/") or container_id
        image = str(config.get("Image", "")) or "unknown"
        findings.append(
            Finding(
                kind="unmanaged container",
                name=name,
                age_seconds=age_seconds,
                detail=f"id={container_id} image={image}",
                cleanup=f"docker rm -f -- {shlex.quote(name)}",
            )
        )
    return findings


def _properties(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _transient_unit_findings(
    *, max_age_seconds: float, uptime_seconds: float
) -> list[Finding]:
    output = _run(
        [
            "systemctl",
            "list-units",
            "--type=service",
            "--state=active",
            "--no-legend",
            "--plain",
        ]
    )
    findings: list[Finding] = []
    for line in output.splitlines():
        fields = line.split(None, 4)
        if len(fields) < 4 or fields[2:4] != ["active", "exited"]:
            continue

        unit = fields[0]
        props = _properties(
            _run(
                [
                    "systemctl",
                    "show",
                    unit,
                    "--property=FragmentPath",
                    "--property=ActiveEnterTimestampMonotonic",
                    "--no-pager",
                ]
            )
        )
        fragment = props.get("FragmentPath", "")
        if not fragment.startswith("/run/systemd/transient/"):
            continue

        try:
            active_at = int(props.get("ActiveEnterTimestampMonotonic", "0")) / 1_000_000
        except ValueError as exc:
            raise HygieneError(f"invalid monotonic timestamp for {unit}") from exc
        age_seconds = max(0.0, uptime_seconds - active_at)
        if age_seconds < max_age_seconds:
            continue

        findings.append(
            Finding(
                kind="active-exited transient service",
                name=unit,
                age_seconds=age_seconds,
                detail=f"fragment={fragment}",
                cleanup=f"systemctl stop {shlex.quote(unit)}",
            )
        )
    return findings


def _format_age(seconds: float) -> str:
    hours = int(seconds // 3600)
    days, remainder = divmod(hours, 24)
    return f"{days}d{remainder:02d}h" if days else f"{hours}h"


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    parsed = _parse_started_at(value)
    return parsed.astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    parser.add_argument("--now", help=argparse.SUPPRESS)
    parser.add_argument("--proc-uptime", type=Path, default=Path("/proc/uptime"))
    args = parser.parse_args(argv)

    if args.max_age_hours <= 0:
        parser.error("--max-age-hours must be positive")

    try:
        uptime_seconds = float(args.proc_uptime.read_text().split()[0])
        max_age_seconds = args.max_age_hours * 3600
        findings = [
            *_container_findings(_parse_now(args.now), max_age_seconds),
            *_transient_unit_findings(
                max_age_seconds=max_age_seconds,
                uptime_seconds=uptime_seconds,
            ),
        ]
    except (OSError, ValueError, HygieneError) as exc:
        print(f"host hygiene inspection failed: {exc}", file=sys.stderr)
        return 2

    if not findings:
        print(
            "host hygiene clean: no unmanaged containers or active-exited "
            f"transient services older than {args.max_age_hours:g}h"
        )
        return 0

    print(
        f"host hygiene found {len(findings)} stale resource(s) older than "
        f"{args.max_age_hours:g}h:",
        file=sys.stderr,
    )
    for finding in findings:
        print(
            f"- {finding.kind}: {finding.name} age={_format_age(finding.age_seconds)} "
            f"{finding.detail}",
            file=sys.stderr,
        )
        print(f"  cleanup after verification: {finding.cleanup}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
