#!/usr/bin/env python3
"""Local-only follow-up to typesense-footprint-lab.py; never connects to production.

Input is a separately acquired JSONL.GZ posting sample. Each variant runs alone
in a disposable loopback-bound Typesense container (6 GiB, 4 CPUs). Results
are screening evidence, not the full-scale x86_64 rollout acceptance benchmark.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.util
import json
import platform
import random
import shutil
import subprocess
import tempfile
import urllib.parse
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "footprint", ROOT / "scripts/typesense-footprint-lab.py"
)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)

ALPHABET = "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz"
RESPONSE = frozenset(
    {"company_name", "location_names", "seniority_name", "technology_names"}
)
VARIANTS = (
    "baseline",
    "response-unindexed",
    "sort-pruned",
    "facet-pruned",
    "combined-pruned",
    "baseline-string",
    "combined-string",
    "combined-int64",
    "combined-active-string",
    "lean-active-string",
    "lean-active-base32",
)


def keys(identifier):
    value = int(identifier.replace("-", ""), 16)
    high = (value >> 64) - (1 << 63)
    low = (value & ((1 << 64) - 1)) - (1 << 63)
    chars = []
    for _ in range(22):
        chars.append(ALPHABET[value % 64])
        value //= 64
    return "".join(reversed(chars)), high, low


def base32_key(identifier):
    value = int(identifier.replace("-", ""), 16)
    chars = []
    for _ in range(26):
        chars.append("0123456789abcdefghijklmnopqrstuv"[value % 32])
        value //= 32
    return "".join(reversed(chars))


def schema_for(variant):
    transform = "combined-pruned" if variant.startswith("combined-") else variant
    if transform == "baseline-string":
        transform = "baseline"
    schema = lab.build_variant_schema(
        lab.load_job_posting_schema(),
        "response-unindexed" if variant.startswith("lean-") else transform,
        response_fields=RESPONSE,
    )
    if variant.startswith("lean-"):
        for field in schema["fields"]:
            field["sort"] = field["name"] in lab.REQUIRED_SORT_FIELDS
    if variant.endswith(("-string", "-base32", "-int64")):
        schema["fields"].append(
            {
                "name": "candidate_order_key",
                "type": "string",
                "optional": True,
                "index": variant.endswith(("-string", "-base32")),
                "sort": variant.endswith(("-string", "-base32")),
            }
        )
    if variant.endswith("-int64"):
        schema["fields"].extend(
            {"name": name, "type": "int64", "optional": True, "sort": True}
            for name in ("candidate_order_hi", "candidate_order_lo")
        )
    return schema


def probe_order_edges(port, *, uuid_version=None):
    """Check both halves' signed boundaries outside the prefix-biased job sample."""
    rng = random.Random(20260923)
    values = {rng.getrandbits(128) for _ in range(48)}
    values.update(
        {0, 1, (1 << 64) - 1, 1 << 64, (1 << 127) - 1, 1 << 127, (1 << 128) - 1}
    )
    identifiers = sorted(
        {str(uuid.UUID(int=value, version=uuid_version)) for value in values}
    )
    fields = [
        {"name": "first_seen_at", "type": "int64"},
        {"name": "b64", "type": "string", "sort": True},
        {"name": "b32", "type": "string", "sort": True},
        {"name": "hi", "type": "int64"},
        {"name": "lo", "type": "int64"},
    ]
    lab._request_json(
        port, "POST", "/collections", payload={"name": "order_edges", "fields": fields}
    )
    # Reverse insertion to prevent insertion order from concealing a sort failure.
    for identifier in reversed(identifiers):
        key, high, low = keys(identifier)
        lab._request_json(
            port,
            "POST",
            "/collections/order_edges/documents",
            payload={
                "id": identifier,
                "first_seen_at": 1,
                "b64": key,
                "b32": base32_key(identifier),
                "hi": high,
                "lo": low,
            },
        )
    report = {
        "uuid_domain": "all-128-bit"
        if uuid_version is None
        else f"version-{uuid_version}",
        "documents": len(identifiers),
        "expected_uuid_order": identifiers,
    }
    for label, sort in {
        "base64": "first_seen_at:desc,b64:asc",
        "base32": "first_seen_at:desc,b32:asc",
        "int64_pair": "first_seen_at:desc,hi:asc,lo:asc",
        "int64_pair_missing_first": (
            "first_seen_at:desc,hi(missing_values:first):asc,lo(missing_values:first):asc"
        ),
    }.items():
        params = urllib.parse.urlencode(
            {"q": "*", "sort_by": sort, "per_page": 250, "include_fields": "id"}
        )
        result = lab._request_json(
            port, "GET", f"/collections/order_edges/documents/search?{params}"
        )
        actual = [hit["document"]["id"] for hit in result["hits"]]
        report[label] = {"order_matches": actual == identifiers, "actual": actual}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS)
    )
    parser.add_argument("--repeats", type=int, default=21)
    parser.add_argument(
        "--image",
        default=lab.PINNED_TYPESENSE_IMAGE,
        help="Use an explicit digest for reproducible cross-version comparisons",
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    scratch = Path(tempfile.mkdtemp(prefix="jobseek-memory-proposals-"))
    original_start, original_stop, original_corpus = (
        lab._start_container,
        lab._stop_container,
        lab.query_corpus,
    )
    original_projection = lab._consumed_result_projection
    cgroup_snapshots = []
    edge_probes = []
    running_port = None

    def start(name, port, data_dir, image):
        nonlocal running_port
        running_port = port
        data_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--memory",
                "6g",
                "--memory-swap",
                "6g",
                "--cpus",
                "4",
                "--publish",
                f"127.0.0.1:{port}:8108",
                "--volume",
                f"{data_dir}:/data",
                image,
                "--data-dir=/data",
                f"--api-key={lab.LAB_API_KEY}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        lab._wait_for_health(port)

    def stop(name):
        proc = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "sh",
                "-c",
                "cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.events /sys/fs/cgroup/memory.stat /proc/1/smaps_rollup",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        cgroup_snapshots.append({"returncode": proc.returncode, "values": proc.stdout})
        # All measured queries and OS snapshots precede this tiny fixture import.
        if len(cgroup_snapshots) == 2 and proc.returncode == 0:
            try:
                edge_probes.append(probe_order_edges(running_port))
                lab._request_json(running_port, "DELETE", "/collections/order_edges")
                edge_probes.append(probe_order_edges(running_port, uuid_version=4))
            except (lab.LabError, OSError, ValueError) as exc:
                edge_probes.append({"error": str(exc)})
        original_stop(name)

    lab._start_container, lab._stop_container = start, stop

    def projection(label, result):
        projected = original_projection(label, result)
        projected["found_docs"] = result.get("found_docs")
        return projected

    lab._consumed_result_projection = projection
    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "complete": False,
        "base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "image": args.image,
        "host_arch": platform.machine(),
        "image_arch": lab._docker_image_architecture(args.image),
        "memory_limit_bytes": 6 * 1024**3,
        "cpus": 4,
        "repeats": args.repeats,
        "query_corpus_by_variant": {},
        "variants": [],
    }
    try:
        prepared = scratch / "sample.jsonl.gz"
        active_prepared = scratch / "active-sample.jsonl.gz"
        base32_prepared = scratch / "base32-sample.jsonl.gz"
        timestamps = Counter()
        uuid_buckets = Counter()
        uuid_versions = Counter()
        shape = {"documents": 0, "active": 0, "serialized_bytes": 0}
        lengths, presence, unique = Counter(), Counter(), {}
        expected_docs = []
        source_hash = hashlib.sha256()
        with (
            gzip.open(args.sample, "rb") as source,
            gzip.open(prepared, "wt", compresslevel=1) as dest,
            gzip.open(active_prepared, "wt", compresslevel=1) as active_dest,
            gzip.open(base32_prepared, "wt", compresslevel=1) as base32_dest,
        ):
            for raw in source:
                source_hash.update(raw)
                doc = json.loads(raw)
                shape["documents"] += 1
                shape["active"] += int(doc.get("is_active", False))
                shape["serialized_bytes"] += len(raw)
                timestamps[doc["first_seen_at"]] += 1
                uuid_buckets[doc["id"][:2]] += 1
                uuid_versions[str(uuid.UUID(doc["id"]).version)] += 1
                for field, value in doc.items():
                    presence[field] += 1
                    vals = value if isinstance(value, list) else [value]
                    if isinstance(value, list):
                        lengths[field] += len(value)
                    if field in RESPONSE or field in {
                        "company_id",
                        "title",
                        "location_ids",
                        "technology_ids",
                    }:
                        unique.setdefault(field, set()).update(
                            json.dumps(v, sort_keys=True) for v in vals
                        )
                key, hi, lo = keys(doc["id"])
                doc.update(
                    candidate_order_key=key,
                    candidate_order_hi=hi,
                    candidate_order_lo=lo,
                )
                if doc.get("is_active") and doc.get("has_content") is not False:
                    expected_docs.append((doc["first_seen_at"], doc["id"]))
                dest.write(
                    json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"
                )
                if not doc.get("is_active"):
                    doc.pop("candidate_order_key")
                active_dest.write(
                    json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"
                )
                if doc.get("is_active"):
                    doc["candidate_order_key"] = base32_key(doc["id"])
                base32_dest.write(
                    json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"
                )
        expected = [
            identifier
            for _, identifier in sorted(expected_docs, key=lambda x: (-x[0], x[1]))[
                :250
            ]
        ]
        report["sample"] = {
            **shape,
            "uncompressed_sha256": source_hash.hexdigest(),
            "uuid_buckets": dict(sorted(uuid_buckets.items())),
            "uuid_versions": dict(uuid_versions),
            "field_presence": dict(presence),
            "array_total_elements": dict(lengths),
            "distinct_values": {k: len(v) for k, v in unique.items()},
            "distinct_timestamps": len(timestamps),
            "largest_timestamp_tie": max(timestamps.values()),
        }
        for variant in args.variants:
            print(f"benchmarking {variant}", flush=True)
            cgroup_snapshots.clear()
            edge_probes.clear()
            schema = schema_for(variant)
            sort = (
                "first_seen_at:desc,candidate_order_key:asc"
                if variant.endswith(("-string", "-base32"))
                else (
                    "first_seen_at:desc,candidate_order_hi:asc,candidate_order_lo:asc"
                    if variant.endswith("-int64")
                    else None
                )
            )

            def corpus(sort=sort):
                queries = original_corpus()
                taxonomy = dict(dict(queries)["taxonomy_facets"])
                for percent in (10, 50):
                    sampled = dict(taxonomy)
                    sampled.update(
                        facet_sample_percent=percent, facet_sample_threshold=10_000
                    )
                    queries.append((f"taxonomy_sampled_{percent}", sampled))
                production = dict(queries[0][1])
                production.update(
                    typo_tokens_threshold=1,
                    drop_tokens_threshold=1,
                    max_facet_values=1,
                )
                queries.append(("production_keyword_grouped", production))
                production_approx = {
                    key: value
                    for key, value in production.items()
                    if key not in {"facet_by", "facet_strategy", "max_facet_values"}
                }
                queries.append(
                    ("production_keyword_without_count_facet", production_approx)
                )
                if ":30." in args.image:
                    bounded = dict(production_approx)
                    bounded["group_max_candidates"] = 10_000
                    queries.append(("production_keyword_bounded_groups", bounded))
                grouped = dict(queries[0][1])
                for field in ("facet_by", "facet_strategy", "max_facet_values"):
                    grouped.pop(field, None)
                queries.append(("keyword_grouped_without_count_facet", grouped))
                ungrouped = {
                    key: value
                    for key, value in grouped.items()
                    if key not in {"group_by", "group_limit", "sort_by"}
                }
                ungrouped["per_page"] = 0
                queries.append(("keyword_ungrouped_exact_count", ungrouped))
                if sort:
                    queries.append(
                        (
                            "stable_candidates",
                            {
                                "q": "*",
                                "query_by": "title",
                                "filter_by": "is_active:true && has_content:!=false",
                                "sort_by": sort,
                                "per_page": 250,
                                "include_fields": "id",
                                "use_cache": "false",
                            },
                        )
                    )
                return queries

            lab.query_corpus = corpus
            report["query_corpus_by_variant"][variant] = dict(corpus())
            variant_sample = (
                active_prepared if "-active-string" in variant else prepared
            )
            if variant.endswith("-base32"):
                variant_sample = base32_prepared
            result = lab.benchmark_variant(
                variant_sample,
                schema,
                "baseline",
                image=args.image,
                repeats=args.repeats,
                warmups=3,
                response_fields=RESPONSE,
                scratch_root=scratch / variant,
            )
            result["variant"] = variant
            result["cgroup_snapshots_before_import_stop_and_rebuild_stop"] = (
                copy.deepcopy(cgroup_snapshots)
            )
            result["order_edge_probes_after_measurements"] = copy.deepcopy(edge_probes)
            if sort:
                result["stable_order_matches_python_uuid_sort"] = (
                    result["queries"]["stable_candidates"]["projection"]["hits"]
                    == expected
                )
            report["variants"].append(result)
            lab._write_json_atomic(args.output, report)
            shutil.rmtree(scratch / variant)
        report["complete"] = True
        lab._write_json_atomic(args.output, report)
    finally:
        lab._start_container, lab._stop_container, lab.query_corpus = (
            original_start,
            original_stop,
            original_corpus,
        )
        lab._consumed_result_projection = original_projection
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
