#!/usr/bin/env python3
"""Standalone grouping benchmark: Python 3.10+ stdlib and Docker, no private data.

Run: python3 repro-typesense-grouping.py --output ./grouping-results
Generates deterministic synthetic JSONL, runs pinned servers sequentially,
checks result equivalence, and removes its disposable containers in finally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from itertools import accumulate
from pathlib import Path

IMAGES = {
    "27.1": "typesense/typesense:27.1@sha256:5c12af89130b8ee0be11541321ba8a3a7c7a538d7c6cd95e0409dc2d75ca6455",
    "30.2": "typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110",
}
KEY = "synthetic-local-repro-only"
SCHEMA = {
    "name": "grouping_repro",
    "token_separators": ["-", "/"],
    "fields": [
        {"name": "title", "type": "string"},
        {"name": "company_split", "type": "string", "facet": True},
        {
            "name": "company_atomic",
            "type": "string",
            "facet": True,
            # A nonempty separator override removes the collection's hyphen
            # separator. Generated UUIDs contain no slash.
            "symbols_to_index": ["-"],
            "token_separators": ["/"],
        },
        {"name": "company_numeric", "type": "int32", "facet": True, "sort": False},
        {"name": "first_seen_at", "type": "int64"},
        {"name": "is_active", "type": "bool"},
        {"name": "has_content", "type": "bool"},
    ],
}
BASE_QUERY = {
    "q": "software engineer",
    "query_by": "title",
    "filter_by": "is_active:true && has_content:!=false",
    "sort_by": "_text_match:desc,first_seen_at:desc",
    "group_limit": 10,
    "per_page": 20,
    "include_fields": "id",
    "typo_tokens_threshold": 1,
    "drop_tokens_threshold": 1,
    "use_cache": "false",
}
QUERIES = {
    field: {**BASE_QUERY, "group_by": field}
    for field in ("company_split", "company_atomic", "company_numeric")
}
QUERIES["count_only"] = {
    key: value
    for key, value in BASE_QUERY.items()
    if key not in {"sort_by", "group_limit"}
}
QUERIES["count_only"]["per_page"] = 0


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def request(base, method, path, payload=None):
    data = payload
    if payload is not None and not isinstance(payload, bytes):
        data = json.dumps(payload).encode()
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"X-TYPESENSE-API-KEY": KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"{method} {path}: {exc.code} {exc.read().decode()}"
        ) from exc
    if path.startswith("/collections/grouping_repro/documents/import"):
        return [json.loads(line) for line in body.splitlines()]
    return json.loads(body)


def wait_ready(base, expected=None):
    deadline = time.monotonic() + 180
    last_error = None
    while time.monotonic() < deadline:
        try:
            if request(base, "GET", "/health").get("ok"):
                if expected is None:
                    return
                collection = request(base, "GET", "/collections/grouping_repro")
                if collection["num_documents"] == expected:
                    return
        except (OSError, RuntimeError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"Server did not become ready: {last_error}")


def generate(output, count):
    """Synthetic skewed companies, selective flags, and 4% keyword matches."""
    rng = random.Random(20260923)
    companies = [
        str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(2000)
    ]
    weights = list(accumulate(1 / (i + 20) for i in range(len(companies))))
    background_titles = [
        "Customer Support Specialist",
        "Software Developer",
        "Electrical Engineer",
        "Product Manager",
        "Sales Representative",
        "Data Analyst",
        "Mechanical Engineer",
        "Software Architect",
    ]
    digest = hashlib.sha256()
    matched = 0
    inactive = 0
    visible = 0
    matching_groups = set()
    with (output / "documents.jsonl").open("wb") as dest:
        for i in range(count):
            is_match = i % 25 == 0
            if is_match:
                company = (
                    (i // 25)
                    if i < 20000
                    else rng.choices(range(800), cum_weights=weights[:800])[0]
                )
                title = "Software Engineer"
                matched += 1
                matching_groups.add(company)
            else:
                company = rng.choices(range(len(companies)), cum_weights=weights)[0]
                title = rng.choice(background_titles)
            doc = {
                "id": str(i),
                "title": title,
                "company_split": companies[company],
                "company_atomic": companies[company],
                "company_numeric": company + 1,
                "first_seen_at": 1700000000 + i,
                "is_active": True if is_match else i % 7 == 0,
                "has_content": True if is_match else i % 11 != 0,
            }
            inactive += not doc["is_active"]
            visible += doc["is_active"] and doc["has_content"]
            line = (json.dumps(doc, separators=(",", ":")) + "\n").encode()
            digest.update(line)
            dest.write(line)
    return companies, {
        "documents": count,
        "sha256": digest.hexdigest(),
        "expected_matching_documents": matched,
        "expected_matching_groups": len(matching_groups),
        "inactive_documents": inactive,
        "active_with_content_documents": visible,
        "seed": 20260923,
    }


def projection(result, field, companies):
    groups = []
    for group in result.get("grouped_hits", []):
        key = group["group_key"][0]
        if field == "company_numeric":
            key = companies[int(key) - 1]
        groups.append(
            {
                "key": key,
                "found": group["found"],
                "ids": [hit["document"]["id"] for hit in group["hits"]],
            }
        )
    return {"found_docs": result.get("found_docs", result["found"]), "groups": groups}


def run_version(version, args, companies, dataset):
    container = None
    report = {"version": version, "image": IMAGES[version], "complete": False}
    try:
        print(f"{version}: starting", flush=True)
        container = docker(
            "run",
            "-d",
            "--memory=6g",
            "--memory-swap=6g",
            "--cpus=4",
            "--publish",
            "127.0.0.1::8108",
            IMAGES[version],
            "--data-dir=/tmp",
            f"--api-key={KEY}",
        )
        info = json.loads(docker("inspect", container))[0]
        port = info["NetworkSettings"]["Ports"]["8108/tcp"][0]["HostPort"]
        base = f"http://127.0.0.1:{port}"
        report["kernel"] = docker("exec", container, "uname", "-srmo")
        report["architecture"] = json.loads(
            docker("image", "inspect", IMAGES[version])
        )[0]["Architecture"]
        wait_ready(base)
        report["debug"] = request(base, "GET", "/debug")
        assert report["debug"]["version"] == version, report["debug"]
        request(base, "POST", "/collections", SCHEMA)
        accepted = 0
        print(
            f"{version}: importing {dataset['documents']} synthetic documents",
            flush=True,
        )
        with (args.output / "documents.jsonl").open("rb") as source:
            while True:
                lines = [source.readline() for _ in range(1000)]
                lines = [line for line in lines if line]
                if not lines:
                    break
                imported = request(
                    base,
                    "POST",
                    "/collections/grouping_repro/documents/import?action=create",
                    b"".join(lines),
                )
                assert len(imported) == len(lines), imported[:2]
                failures = [row for row in imported if not row.get("success")]
                assert not failures, failures[:2]
                accepted += len(imported)
        assert accepted == dataset["documents"], accepted
        report["accepted_documents"] = accepted
        wait_ready(base, accepted)
        print(f"{version}: restarting after import", flush=True)
        docker("restart", container)
        # Docker may assign a different ephemeral host port on restart.
        info = json.loads(docker("inspect", container))[0]
        port = info["NetworkSettings"]["Ports"]["8108/tcp"][0]["HostPort"]
        base = f"http://127.0.0.1:{port}"
        wait_ready(base, accepted)
        report["persisted_schema"] = request(base, "GET", "/collections/grouping_repro")
        observations = {field: [] for field in QUERIES}
        expected_projection = None
        rng = random.Random(20260923)
        for repeat in range(-args.warmups, args.repeats):
            order = list(QUERIES)
            rng.shuffle(order)
            for field in order:
                started = time.perf_counter()
                result = request(
                    base,
                    "GET",
                    "/collections/grouping_repro/documents/search?"
                    + urllib.parse.urlencode(QUERIES[field]),
                )
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                assert not result.get("search_cutoff"), result
                projected = projection(result, field, companies)
                assert (
                    projected["found_docs"] == dataset["expected_matching_documents"]
                ), projected
                if field != "count_only":
                    assert len(projected["groups"]) == 20, projected
                    if expected_projection is None:
                        expected_projection = projected
                    assert projected == expected_projection, (
                        field,
                        projected,
                        expected_projection,
                    )
                if repeat >= 0:
                    observations[field].append(
                        {
                            "server_ms": result["search_time_ms"],
                            "wall_ms": elapsed,
                            "found": result["found"],
                            "search_cutoff": result.get("search_cutoff"),
                        }
                    )
            print(f"{version}: round {repeat + 1}/{args.repeats}", flush=True)
        report["queries"] = {}
        for field, rows in observations.items():
            timings = [row["server_ms"] for row in rows]
            report["queries"][field] = {
                "median_ms": statistics.median(timings),
                "p95_ms": sorted(timings)[math.ceil(len(timings) * 0.95) - 1],
                "observations": rows,
            }
            print(
                f"{version} {field}: median={statistics.median(timings)} ms", flush=True
            )
        report["normalized_group_projection"] = expected_projection
        report["cgroup_memory_events"] = docker(
            "exec", container, "cat", "/sys/fs/cgroup/memory.events"
        )
        report["os_memory"] = docker("exec", container, "cat", "/proc/1/smaps_rollup")
        events = dict(
            line.split() for line in report["cgroup_memory_events"].splitlines()
        )
        assert all(
            int(events.get(key, "0")) == 0 for key in ("max", "oom", "oom_kill")
        ), events
        report["complete"] = True
        return report
    finally:
        if container:
            logs = subprocess.run(
                ["docker", "logs", container],
                capture_output=True,
                text=True,
                check=False,
            )
            (args.output / f"server-{version}.log").write_text(
                logs.stdout + logs.stderr
            )
            subprocess.run(
                ["docker", "rm", "-fv", container],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        write_json(args.output / f"result-{version}.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--documents", type=int, default=350000)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument(
        "--versions", nargs="+", choices=list(IMAGES), default=list(IMAGES)
    )
    args = parser.parse_args()
    if args.documents < 20000 or args.repeats < 1 or args.warmups < 1:
        parser.error("Use at least 20,000 documents, one repetition and one warm-up")
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error(
            "Output directory must be empty to avoid overwriting earlier evidence"
        )
    companies, dataset = generate(args.output, args.documents)
    write_json(args.output / "schema.json", SCHEMA)
    write_json(args.output / "queries.json", QUERIES)
    summary = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "complete": False,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dataset": dataset,
        "schema": SCHEMA,
        "queries": QUERIES,
        "memory_limit_bytes": 6 * 1024**3,
        "cpu_limit": 4,
        "warmup_rounds": args.warmups,
        "measured_rounds": args.repeats,
        "docker_version": docker("version", "--format", "{{.Server.Version}}"),
        "runs": [],
    }
    try:
        for version in args.versions:
            summary["runs"].append(run_version(version, args, companies, dataset))
        reference = summary["runs"][0]["normalized_group_projection"]
        assert all(
            run["normalized_group_projection"] == reference for run in summary["runs"]
        )
        summary["ordered_groups_counts_and_hits_equal"] = True
        summary["complete"] = True
    finally:
        write_json(args.output / "summary.json", summary)
    print(
        f"Result equivalence passed. Evidence: {args.output / 'summary.json'}",
        flush=True,
    )


if __name__ == "__main__":
    main()
