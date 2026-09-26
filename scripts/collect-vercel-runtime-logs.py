#!/usr/bin/env python3
"""Archive overlapping Hobby runtime-log queries for Fluid CPU review.

This collector cannot make Vercel runtime logs an all-traffic source: Vercel
omits some static requests and does not expose User-Agent in CLI JSON output.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = "jobseek-web"
PROJECT_ID = "prj_NqkWn9aYWWGsrxGjQf69vkR1POAi"
SCOPE = "viktor-shcherbakovs-projects"
LIMIT = 1000
MIN_SLICE_SECONDS = 30


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def query_logs(start: datetime, end: datetime) -> list[dict]:
    command = [
        "vercel", "logs", "--project", PROJECT, "--scope", SCOPE,
        "--environment", "production", "--no-branch",
        "--since", utc_iso(start), "--until", utc_iso(end),
        "--limit", str(LIMIT), "--json",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode:
        raise RuntimeError(f"vercel logs exited {completed.returncode}: {completed.stderr[-500:]}")
    try:
        records = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise RuntimeError("vercel logs returned malformed JSONL") from exc
    if not all(
        isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and isinstance(row.get("timestamp"), (int, float))
        and row.get("projectId") == PROJECT_ID
        and row.get("environment") == "production"
        for row in records
    ):
        raise RuntimeError("vercel logs returned an invalid or non-production record")
    return records


def fetch_slice(start: datetime, end: datetime, slices: list[dict], rows: dict[str, dict]) -> None:
    records = query_logs(start, end)
    unique = {row["id"]: row for row in records}
    if len(records) >= LIMIT:
        if (end - start).total_seconds() <= MIN_SLICE_SECONDS:
            slices.append({"start": utc_iso(start), "end": utc_iso(end), "count": len(records), "uniqueCount": len(unique), "truncated": True})
            rows.update(unique)
            return
        middle = start + (end - start) / 2
        fetch_slice(start, middle, slices, rows)
        fetch_slice(middle, end, slices, rows)
        return
    slices.append({"start": utc_iso(start), "end": utc_iso(end), "count": len(records), "uniqueCount": len(unique), "truncated": False})
    rows.update(unique)


def private_archive_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o077:
        raise RuntimeError("Archive directory must be a private, non-symlink directory")


def write_private(path: Path, data: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def capture(archive: Path, lookback_minutes: int, slice_minutes: int) -> dict:
    private_archive_dir(archive)
    end = datetime.now(timezone.utc) - timedelta(seconds=10)
    start = end - timedelta(minutes=lookback_minutes)
    rows: dict[str, dict] = {}
    slices: list[dict] = []
    errors: list[str] = []
    cursor = start
    while cursor < end:
        next_cursor = min(cursor + timedelta(minutes=slice_minutes), end)
        try:
            fetch_slice(cursor, next_cursor, slices, rows)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{utc_iso(cursor)}–{utc_iso(next_cursor)}: {exc}")
        cursor = next_cursor

    stamp = end.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    data_name = f"{stamp}.jsonl"
    write_private(archive / data_name, "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows.values()))
    metadata = {
        "capturedAt": utc_iso(datetime.now(timezone.utc)),
        "requestedStart": utc_iso(start),
        "requestedEnd": utc_iso(end),
        "project": PROJECT,
        "scope": SCOPE,
        "dataFile": data_name,
        "uniqueLogIds": len(rows),
        "slices": slices,
        "errors": errors,
        "runtimeLogsOnly": True,
        "sourceIncludesAllTraffic": False,
    }
    write_private(archive / f"{stamp}.metadata.json", json.dumps(metadata, indent=2) + "\n")
    return metadata


def summarize(archive: Path, start: datetime, end: datetime) -> dict:
    rows: dict[str, dict] = {}
    intervals: list[tuple[datetime, datetime]] = []
    captures = 0
    errors = 0
    truncated = 0
    for path in sorted(archive.glob("*.metadata.json")):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        captures += 1
        errors += len(metadata.get("errors", []))
        for item in metadata.get("slices", []):
            if item.get("truncated"):
                truncated += 1
                continue
            left = max(start, parse_utc(item["start"]))
            right = min(end, parse_utc(item["end"]))
            if left < right:
                intervals.append((left, right))
        data_path = archive / metadata["dataFile"]
        for line in data_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            timestamp = datetime.fromtimestamp(row["timestamp"] / 1000, timezone.utc)
            if start <= timestamp < end:
                rows[row["id"]] = row

    intervals.sort()
    covered_until = start
    gaps = []
    for left, right in intervals:
        if left > covered_until:
            gaps.append({"start": utc_iso(covered_until), "end": utc_iso(left)})
        covered_until = max(covered_until, right)
    if covered_until < end:
        gaps.append({"start": utc_iso(covered_until), "end": utc_iso(end)})

    company_keys = set()
    sources: dict[str, int] = {}
    deployments: dict[str, int] = {}
    for row in rows.values():
        source = row.get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1
        deployment = row.get("deploymentId", "unknown")
        deployments[deployment] = deployments.get(deployment, 0) + 1
        parts = str(row.get("requestPath", "")).split("?", 1)[0].split("/")
        if len(parts) == 4 and parts[1] in {"en", "de", "fr", "it"} and parts[2] == "company":
            company_keys.add((parts[1], parts[3]))
    return {
        "window": {"start": utc_iso(start), "end": utc_iso(end)},
        "captures": captures,
        "uniqueRuntimeLogIds": len(rows),
        "uniqueCompanyLocaleKeys": len(company_keys),
        "sources": sources,
        "deployments": deployments,
        "queryGaps": gaps,
        "queryErrors": errors,
        "truncatedSlices": truncated,
        "sourceIncludesAllTraffic": False,
        "recognizedBotRequests": None,
        "note": "CLI runtime logs omit some static requests and User-Agent; coverage of queried intervals is not all-traffic coverage.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("capture")
    collect.add_argument("--lookback-minutes", type=int, default=45)
    collect.add_argument("--slice-minutes", type=int, default=5)
    summary = sub.add_parser("summary")
    summary.add_argument("--start", required=True)
    summary.add_argument("--end", required=True)
    args = parser.parse_args()

    if args.command == "capture":
        if not 1 <= args.slice_minutes <= args.lookback_minutes <= 55:
            parser.error("require 1 <= slice-minutes <= lookback-minutes <= 55")
        result = capture(args.archive, args.lookback_minutes, args.slice_minutes)
        print(json.dumps({key: result[key] for key in ("capturedAt", "requestedStart", "requestedEnd", "dataFile", "uniqueLogIds", "errors")}))
        return 1 if result["errors"] or any(item["truncated"] for item in result["slices"]) else 0

    start, end = parse_utc(args.start), parse_utc(args.end)
    if end <= start:
        parser.error("end must be after start")
    print(json.dumps(summarize(args.archive, start, end), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
