#!/usr/bin/env python3
"""Compare upgrade settings on a local disposable server; never uses credentials.

Input: an existing posting sample. Runs one index at a time with a 6 GiB / 4 CPU
limit, restarts after import, and interleaves query variants in seeded order.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import random
import shutil
import statistics
import subprocess
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "footprint", ROOT / "scripts/typesense-footprint-lab.py"
)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
IMAGE = "typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--variant",
        choices=["baseline", "uuid-atomic", "key-types"],
        default="baseline",
    )
    parser.add_argument("--image", default=IMAGE)
    parser.add_argument("--repeats", type=int, default=9)
    args = parser.parse_args()
    schema = lab.build_variant_schema(lab.load_job_posting_schema(), "baseline")
    if args.variant == "uuid-atomic":
        company = next(f for f in schema["fields"] if f["name"] == "company_id")
        # Nonempty field separators override collection separators; UUIDs have
        # no slash, so preserving hyphens makes the whole UUID one token.
        company.update(token_separators=["/"], symbols_to_index=["-"])
    companies = set()
    sample_hash = hashlib.sha256()
    with gzip.open(args.sample, "rb") as src:
        for line in src:
            sample_hash.update(line)
            companies.add(json.loads(line)["company_id"])
    current = dict(lab.query_corpus()[0][1])
    current.update(
        typo_tokens_threshold=1,
        drop_tokens_threshold=1,
        max_facet_values=1,
        use_cache="false",
    )
    approx = {
        k: v
        for k, v in current.items()
        if k not in {"facet_by", "facet_strategy", "max_facet_values"}
    }
    queries = {
        "current": current,
        "approx": approx,
        "approx_lazy": {**approx, "enable_lazy_filter": "true"},
        "approx_no_highlight": {
            **approx,
            "highlight_fields": "none",
            "enable_highlight_v1": "false",
        },
        "approx_group1": {**approx, "group_limit": 1},
        "approx_page5": {**approx, "per_page": 5},
        "approx_filter_candidates1": {**approx, "max_filter_by_candidates": 1},
        "approx_exact_terms": {**approx, "num_typos": 0, "prefix": "false"},
        "approx_cutoff50": {**approx, "search_cutoff_ms": 50},
        "approx_recent_only": {**approx, "sort_by": "first_seen_at:desc"},
        "count_only": {
            k: v
            for k, v in approx.items()
            if k not in {"group_by", "group_limit", "sort_by"}
        },
    }
    queries["count_only"]["per_page"] = 0
    for n in (20, 250):
        ids = ",".join("`" + c + "`" for c in sorted(companies)[:n])
        queries[f"uuid_filter_{n}"] = {
            "q": "*",
            "filter_by": f"company_id:[{ids}]",
            "per_page": 0,
            "use_cache": "false",
        }
    experience = dict(dict(lab.query_corpus())["experience_overlap"])
    queries["experience_default"] = experience
    queries["experience_top_values"] = {**experience, "facet_strategy": "top_values"}
    scratch = Path(tempfile.mkdtemp(prefix="typesense-upgrade-diagnostics-"))
    data = scratch / "data"
    data.mkdir()
    input_sample = args.sample
    numeric_to_company = {}
    if args.variant == "key-types":
        # Diagnostic only: dense numeric IDs are local to this sample, not a
        # proposed production ID contract. All group keys map back to UUIDs.
        keep = {
            "title",
            "company_id",
            "first_seen_at",
            "is_active",
            "has_content",
            "experience_min",
        }
        schema["fields"] = [f for f in schema["fields"] if f["name"] in keep]
        schema["fields"].extend(
            [
                {
                    "name": "company_atomic",
                    "type": "string",
                    "facet": True,
                    "symbols_to_index": ["-"],
                    "token_separators": ["/"],
                },
                {
                    "name": "company_numeric",
                    "type": "int32",
                    "facet": True,
                    "sort": False,
                },
            ]
        )
        company_to_numeric = {c: i + 1 for i, c in enumerate(sorted(companies))}
        numeric_to_company = {v: k for k, v in company_to_numeric.items()}
        input_sample = scratch / "key-types.jsonl.gz"
        with (
            gzip.open(args.sample, "rt") as src,
            gzip.open(input_sample, "wt", compresslevel=1) as dest,
        ):
            for line in src:
                doc = {
                    k: v for k, v in json.loads(line).items() if k in keep or k == "id"
                }
                doc.update(
                    company_atomic=doc["company_id"],
                    company_numeric=company_to_numeric[doc["company_id"]],
                )
                dest.write(json.dumps(doc, separators=(",", ":")) + "\n")
        queries = {
            "string_split": approx,
            "string_atomic": {**approx, "group_by": "company_atomic"},
            "numeric": {**approx, "group_by": "company_numeric"},
            "count_only": queries["count_only"],
        }
    port = lab._free_loopback_port()
    name = f"typesense-upgrade-diagnostics-{os.getpid()}"
    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "complete": False,
        "image": args.image,
        "variant": args.variant,
        "schema": schema,
        "sample_uncompressed_sha256": sample_hash.hexdigest(),
        "repeats": args.repeats,
        "memory_limit_bytes": 6 * 1024**3,
        "cpus": 4,
        "query_parameters": queries,
    }
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--memory",
                "6g",
                "--memory-swap",
                "6g",
                "--cpus",
                "4",
                "-p",
                f"127.0.0.1:{port}:8108",
                "-v",
                f"{data}:/data",
                args.image,
                "--data-dir=/data",
                f"--api-key={lab.LAB_API_KEY}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        lab._wait_for_health(port)
        report["debug"] = lab._request_json(port, "GET", "/debug")
        print(f"{args.variant}: importing", flush=True)
        lab._request_json(port, "POST", "/collections", payload=schema)
        report["import"] = lab._import_documents(port, input_sample)
        lab._wait_for_document_count(port, report["import"]["accepted_documents"])
        print(f"{args.variant}: rebuilding", flush=True)
        report["rebuild_seconds"] = lab._restart_container(
            name, port, report["import"]["accepted_documents"]
        )
        report["readiness"] = lab._wait_for_semantic_readiness(port)
        report["memory_before_queries"] = lab._capture_memory(port)
        report["persisted_schema"] = lab._request_json(
            port, "GET", f"/collections/{lab.LAB_COLLECTION}"
        )
        observations = {label: [] for label in queries}
        rng = random.Random(20260923)
        for repeat in range(-2, args.repeats):
            labels = list(queries)
            rng.shuffle(labels)
            for label in labels:
                started = time.perf_counter()
                params = queries[label]
                if len(urllib.parse.urlencode(params)) > 3500:
                    result = lab._request_json(
                        port,
                        "POST",
                        "/multi_search?use_cache=false",
                        payload={
                            "searches": [{"collection": lab.LAB_COLLECTION, **params}]
                        },
                    )["results"][0]
                else:
                    result = lab._search(port, params)
                elapsed = (time.perf_counter() - started) * 1000
                projection = lab._result_projection(result)
                if label == "numeric":
                    for group in projection["groups"]:
                        group["group_key"] = [
                            numeric_to_company[int(group["group_key"][0])]
                        ]
                projection.update(
                    found_docs=result.get("found_docs"),
                    search_cutoff=result.get("search_cutoff"),
                )
                if repeat >= 0:
                    observations[label].append(
                        {
                            "server_ms": result["search_time_ms"],
                            "wall_ms": round(elapsed, 3),
                            "projection": projection,
                        }
                    )
            print(
                f"{args.variant}: query round {repeat + 1}/{args.repeats}", flush=True
            )
        report["queries"] = {}
        for label, results in observations.items():
            times = [r["server_ms"] for r in results]
            report["queries"][label] = {
                "median_ms": statistics.median(times),
                "p95_ms": lab._percentile(times, 0.95),
                "server_ms": times,
                "wall_ms": [r["wall_ms"] for r in results],
                "projection": results[0]["projection"],
                "projection_drift": any(
                    r["projection"] != results[0]["projection"] for r in results
                ),
                "cutoff_runs": sum(
                    bool(r["projection"]["search_cutoff"]) for r in results
                ),
            }
            print(label, report["queries"][label]["median_ms"], "ms", flush=True)
        report["memory_after_queries"] = lab._capture_memory(port)
        report["os_memory"] = subprocess.check_output(
            [
                "docker",
                "exec",
                name,
                "sh",
                "-c",
                "cat /proc/1/smaps_rollup /sys/fs/cgroup/memory.events",
            ],
            text=True,
        )
        report["complete"] = True
    finally:
        lab._remove_container(name)
        shutil.rmtree(scratch, ignore_errors=True)
        lab._write_json_atomic(args.output, report)


if __name__ == "__main__":
    main()
