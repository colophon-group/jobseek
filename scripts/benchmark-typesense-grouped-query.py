#!/usr/bin/env python3
"""Read-only A/B replay of grouped queries; never prints credentials/documents.

Requires Python stdlib and curl. The env file is read locally; only the key
header goes to curl over stdin. Run against an explicitly selected server.
"""

import argparse
import concurrent.futures
import hashlib
import json
import math
import random
import statistics
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=9)
    parser.add_argument("--cases", nargs="+", help="Replay selected case names")
    parser.add_argument(
        "--lookahead",
        type=int,
        choices=[0, 1],
        default=1,
        help="Use 0 only to isolate lookahead cost on 27.1",
    )
    parser.add_argument("--concurrency", type=int, choices=[1, 4], default=1)
    args = parser.parse_args()
    env = {}
    for line in args.env_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    key = env.get("TYPESENSE_OPERATIONS_KEY") or env["TYPESENSE_ADMIN_KEY"]

    def request(path, params=None):
        url = args.url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = subprocess.check_output(
            ["curl", "-fsS", "--max-time", "30", "-H", "@-", url],
            input=("X-TYPESENSE-API-KEY: " + key + "\n").encode(),
        )
        return json.loads(body)

    def metrics():
        raw = request("/metrics.json")
        return {k: v for k, v in raw.items() if "memory" in k}

    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "complete": False,
        "concurrency": args.concurrency,
        "repeats": args.repeats,
        "warmups": 2,
        "lookahead": args.lookahead,
        "debug": request("/debug"),
        "documents_before": request("/collections/job_posting")["num_documents"],
        "memory_before": metrics(),
        "note": "Resident metric is not RSS; no claim of steady-memory savings. Live CDC may change paired results.",
        "cases": {},
    }
    base_filter = "is_active:true && has_content:!=false"
    cases = [
        ("software", "software engineer", "", 0),
        ("software_page2", "software engineer", "", 10),
        ("software_page5", "software engineer", "", 40),
        ("sales", "sales", "", 0),
        ("nurse", "nurse", "", 0),
        ("manager", "product manager", "", 0),
        (
            "remote_english",
            "software engineer",
            "location_types:=[remote] && locales:=[en]",
            0,
        ),
        ("salary", "software engineer", "salary_eur:>=80000", 0),
        ("zero", "zzzxqvnojobmatchzzzxqv", "", 0),
    ]
    if args.cases:
        unknown = set(args.cases) - {case[0] for case in cases}
        if unknown:
            parser.error(f"Unknown cases: {sorted(unknown)}")
        cases = [case for case in cases if case[0] in args.cases]

    def run_query(params, optimized):
        started = time.perf_counter()
        raw = request("/collections/job_posting/documents/search", params)
        if raw.get("search_cutoff"):
            raise RuntimeError("Query cut off")
        groups = raw.get("grouped_hits", [])[:10]
        normalized = {
            "found": raw["found"],
            "found_docs": raw.get("found_docs"),
            "groups": [
                {
                    "key": g["group_key"],
                    "found": g["found"],
                    "ids": [h["document"]["id"] for h in g["hits"]],
                }
                for g in groups
            ],
        }
        if not optimized:
            total = (
                (raw.get("facet_counts") or [{}])[0]
                .get("stats", {})
                .get("total_values", 0)
            )
            if total != raw["found"]:
                raise RuntimeError(
                    "27.1 grouped found differs from exhaustive facet total"
                )
        return {
            "server_ms": raw["search_time_ms"],
            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
            "projection_sha256": hashlib.sha256(
                json.dumps(normalized, sort_keys=True).encode()
            ).hexdigest(),
            "found": raw["found"],
            "found_docs": raw.get("found_docs"),
            "returned_groups": len(groups),
            "has_more": len(raw.get("grouped_hits", [])) > 10
            if optimized and args.lookahead
            else raw["found"] > params["offset"] + 10,
        }

    try:
        if report["debug"]["version"] != "27.1":
            raise RuntimeError(
                "This parity replay expects the deployed 27.1 exact group-count contract"
            )
        rng = random.Random(20260923)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.concurrency
        ) as pool:
            for label, query, extra, offset in cases:
                shared = {
                    "q": query,
                    "query_by": "title",
                    "filter_by": base_filter + (" && " + extra if extra else ""),
                    "sort_by": "_text_match:desc,first_seen_at:desc",
                    "group_by": "company_id",
                    "group_limit": 10,
                    "offset": offset,
                    "typo_tokens_threshold": 1,
                    "drop_tokens_threshold": 1,
                    "use_cache": "false",
                }
                params = {
                    "before": {
                        **shared,
                        "limit": 10,
                        "facet_by": "company_id",
                        "facet_strategy": "exhaustive",
                        "max_facet_values": 1,
                    },
                    "after": {**shared, "limit": 10 + args.lookahead},
                }
                rows = {"before": [], "after": []}
                mismatches = []
                for repeat in range(-2, args.repeats):
                    labels = ["before", "after"]
                    rng.shuffle(labels)
                    pair = {}
                    for variant in labels:
                        # A bounded burst of identical independent reads models
                        # concurrent visitors without issuing writes or a load test.
                        futures = [
                            pool.submit(run_query, params[variant], variant == "after")
                            for _ in range(args.concurrency)
                        ]
                        pair[variant] = [future.result() for future in futures]
                        if repeat >= 0:
                            rows[variant].extend(pair[variant])
                        time.sleep(0.1)
                    before = pair["before"][0]
                    if any(
                        (r["projection_sha256"], r["has_more"])
                        != (before["projection_sha256"], before["has_more"])
                        for values in pair.values()
                        for r in values
                    ):
                        mismatches.append({"round": repeat, "observations": pair})
                result = {"parameters": params, "mismatches": mismatches}
                for variant, observations in rows.items():
                    times = sorted(r["server_ms"] for r in observations)
                    result[variant] = {
                        "median_ms": statistics.median(times),
                        "p95_ms": times[math.ceil(len(times) * 0.95) - 1],
                        "observations": observations,
                    }
                report["cases"][label] = result
                print(
                    label,
                    {v: result[v]["median_ms"] for v in rows},
                    "mismatches",
                    len(mismatches),
                    flush=True,
                )
        report["memory_after"] = metrics()
        report["documents_after"] = request("/collections/job_posting")["num_documents"]
        report["complete"] = True
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
