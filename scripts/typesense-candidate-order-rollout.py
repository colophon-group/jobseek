#!/usr/bin/env python3
"""Operator tool for a measured, resumable Typesense UUID-order rollout.

Run through an SSH loopback tunnel to the Typesense host. The admin key is read
from an ignored local env file and never appears in process arguments or logs.
The commands are deliberately separate so each stage can be reviewed before
the next mutation. ``clear`` removes values from the exported ID prefix;
``drop`` then releases the optional schema fields and their indexes.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

FIELDS = (
    {"name": "candidate_order_key", "type": "string", "index": False, "optional": True},
    {"name": "candidate_order_hi", "type": "int64", "sort": True, "optional": True},
    {"name": "candidate_order_lo", "type": "int64", "sort": True, "optional": True},
)
ALPHABET = "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz"
CANONICAL_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def load_key(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("TYPESENSE_ADMIN_KEY="):
            key = line.partition("=")[2].strip().strip("\"'")
            if key:
                return key
    raise ValueError("TYPESENSE_ADMIN_KEY is missing")


def request(
    base_url: str,
    key: str,
    method: str,
    path: str,
    *,
    data: bytes | None = None,
    timeout: float = 300,
) -> bytes:
    headers = {"X-TYPESENSE-API-KEY": key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        # Typesense errors have no credential, but keep output bounded.
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise RuntimeError(f"Typesense HTTP {exc.code}: {detail}") from exc


def key_from_uuid(value: uuid.UUID) -> str:
    number = value.int
    digits = ["-"] * 22
    for index in range(21, -1, -1):
        number, digit = divmod(number, 64)
        digits[index] = ALPHABET[digit]
    return "".join(digits)


def document_update(id_text: str, *, clear: bool) -> dict[str, Any]:
    if not CANONICAL_UUID.fullmatch(id_text):
        raise ValueError("export contained a non-canonical UUID")
    if clear:
        return {"id": id_text, **{field["name"]: None for field in FIELDS}}
    value = uuid.UUID(id_text)
    return {
        "id": id_text,
        "candidate_order_key": key_from_uuid(value),
        "candidate_order_hi": (value.int >> 64) - (1 << 63),
        "candidate_order_lo": (value.int & ((1 << 64) - 1)) - (1 << 63),
    }


def import_batch(base_url: str, key: str, docs: list[dict[str, Any]]) -> None:
    body = b"\n".join(json.dumps(doc, separators=(",", ":")).encode() for doc in docs)
    result = request(
        base_url,
        key,
        "POST",
        "/collections/job_posting/documents/import?action=update",
        data=body,
        timeout=600,
    )
    lines = result.splitlines()
    if len(lines) != len(docs):
        raise RuntimeError(f"import returned {len(lines)} results for {len(docs)} documents")
    failures = sum(json.loads(line).get("success") is not True for line in lines)
    if failures:
        raise RuntimeError(f"import rejected {failures} of {len(docs)} document updates")


def export_ids(base_url: str, key: str, destination: Path) -> int:
    path = "/collections/job_posting/documents/export?include_fields=id"
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"X-TYPESENSE-API-KEY": key},
    )
    count = 0
    with (
        urllib.request.urlopen(req, timeout=3600) as response,
        gzip.open(
            destination,
            "wt",
            encoding="utf-8",
        ) as output,
    ):  # noqa: S310
        for line in response:
            document = json.loads(line)
            id_text = document.get("id")
            if not isinstance(id_text, str) or not CANONICAL_UUID.fullmatch(id_text):
                raise ValueError("export contained a non-canonical UUID")
            output.write(id_text + "\n")
            count += 1
    return count


def update_prefix(
    base_url: str,
    key: str,
    source: Path,
    *,
    start: int,
    limit: int,
    batch_size: int,
    clear: bool,
) -> int:
    accepted = 0
    batch: list[dict[str, Any]] = []
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        for index, line in enumerate(stream):
            if index < start:
                continue
            if accepted + len(batch) >= limit:
                break
            batch.append(document_update(line.strip(), clear=clear))
            if len(batch) == batch_size:
                import_batch(base_url, key, batch)
                accepted += len(batch)
                print(
                    json.dumps(
                        {
                            "phase": "clear" if clear else "update",
                            "accepted": accepted,
                            "end_offset": start + accepted,
                        }
                    ),
                    flush=True,
                )
                batch.clear()
    if batch:
        import_batch(base_url, key, batch)
        accepted += len(batch)
        print(
            json.dumps(
                {
                    "phase": "clear" if clear else "update",
                    "accepted": accepted,
                    "end_offset": start + accepted,
                }
            ),
            flush=True,
        )
    if accepted != limit:
        raise RuntimeError(f"source ended after {accepted} documents; expected {limit}")
    return accepted


def cgroup_memory() -> dict[str, int]:
    inspect = json.loads(
        subprocess.check_output(
            ["docker", "inspect", "typesense"],
            text=True,
        )
    )[0]
    pid = inspect["State"]["Pid"]
    group = next(
        line.partition("::")[2]
        for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
        if "::" in line
    )
    root = Path("/sys/fs/cgroup") / group.lstrip("/")
    stat = dict(line.split() for line in (root / "memory.stat").read_text().splitlines())
    events = dict(line.split() for line in (root / "memory.events").read_text().splitlines())
    current = int((root / "memory.current").read_text())
    limit = int((root / "memory.max").read_text())
    inactive = int(stat["inactive_file"])
    return {
        "cgroup_current_bytes": current,
        "cgroup_limit_bytes": limit,
        "inactive_file_bytes": inactive,
        "effective_working_set_bytes": current - inactive,
        "effective_headroom_bytes": limit - current + inactive,
        "oom_events": int(events.get("oom", 0)),
        "oom_kill_events": int(events.get("oom_kill", 0)),
        "container_restarts": int(inspect["RestartCount"]),
    }


def benchmark(base_url: str, key: str, *, stable: bool) -> dict[str, Any]:
    params = {
        "q": "*",
        "query_by": "title",
        "filter_by": "is_active:true && location_ids:=[2658434] && seniority_id:=1",
        "sort_by": (
            "first_seen_at:desc,candidate_order_hi(missing_values: first):asc,"
            "candidate_order_lo(missing_values: first):asc"
            if stable
            else "first_seen_at:desc"
        ),
        "per_page": 100,
    }
    path = "/collections/job_posting/documents/search?" + urllib.parse.urlencode(params)
    samples: list[float] = []
    found: int | None = None
    for _ in range(20):
        started = time.perf_counter()
        response = json.loads(request(base_url, key, "GET", path))
        samples.append((time.perf_counter() - started) * 1_000)
        found = response["found"]
    samples.sort()
    return {
        "query_found": found,
        "p95_ms": round(samples[18]),
        "samples": len(samples),
        "stable": stable,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("status", "bench", "snapshot", "add", "export", "update", "clear", "drop"),
    )
    parser.add_argument("--url", default="http://127.0.0.1:18111")
    key_source = parser.add_mutually_exclusive_group(required=True)
    key_source.add_argument("--key-env-file", type=Path)
    key_source.add_argument("--key-stdin", action="store_true")
    parser.add_argument("--id-file", type=Path)
    parser.add_argument("--snapshot-path")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--stable", action="store_true")
    args = parser.parse_args()
    key = sys.stdin.readline().rstrip("\n") if args.key_stdin else load_key(args.key_env_file)
    if not key:
        raise ValueError("Typesense admin key is missing")
    if args.command == "status":
        collection = json.loads(request(args.url, key, "GET", "/collections/job_posting"))
        metrics = json.loads(request(args.url, key, "GET", "/metrics.json"))
        print(
            json.dumps(
                {
                    "documents": collection["num_documents"],
                    "fields": {
                        field["name"]: field
                        for field in collection["fields"]
                        if field["name"].startswith("candidate_order_")
                    },
                    "resident_bytes": int(metrics["typesense_memory_resident_bytes"]),
                    "allocated_bytes": int(metrics["typesense_memory_allocated_bytes"]),
                    **cgroup_memory(),
                },
                sort_keys=True,
            )
        )
    elif args.command == "bench":
        print(json.dumps(benchmark(args.url, key, stable=args.stable), sort_keys=True))
    elif args.command == "snapshot":
        if not args.snapshot_path or not args.snapshot_path.startswith("/jobseek-snapshots/"):
            parser.error("snapshot requires a path under /jobseek-snapshots/")
        query = urllib.parse.urlencode({"snapshot_path": args.snapshot_path})
        print(
            request(args.url, key, "POST", f"/operations/snapshot?{query}", timeout=7200).decode()
        )
    elif args.command == "add":
        print(
            request(
                args.url,
                key,
                "PATCH",
                "/collections/job_posting",
                data=json.dumps({"fields": FIELDS}).encode(),
                timeout=7200,
            ).decode()[:500]
        )
    elif args.command == "drop":
        print(
            request(
                args.url,
                key,
                "PATCH",
                "/collections/job_posting",
                data=json.dumps(
                    {"fields": [{"name": field["name"], "drop": True} for field in FIELDS]}
                ).encode(),
                timeout=7200,
            ).decode()[:500]
        )
    elif args.command == "export":
        if args.id_file is None:
            parser.error("export requires --id-file")
        print(json.dumps({"exported": export_ids(args.url, key, args.id_file)}))
    else:
        if (
            args.id_file is None
            or args.start < 0
            or args.limit <= 0
            or args.batch_size <= 0
        ):
            parser.error(
                "update/clear require --id-file, nonnegative --start and positive --limit and --batch-size"
            )
        update_prefix(
            args.url,
            key,
            args.id_file,
            start=args.start,
            limit=args.limit,
            batch_size=args.batch_size,
            clear=args.command == "clear",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
