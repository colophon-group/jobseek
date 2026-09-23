#!/usr/bin/env python3
"""Rehearse posting-index pruning in an owned, loopback-only Docker container.

The input is a separately exported schema and JSONL.GZ corpus. No production
credentials or remote URLs are accepted. Import requests are bounded, unlike
curl --data-binary @- which buffers a full multi-gigabyte stdin in memory.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.util
import json
import platform
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "footprint", Path(__file__).with_name("typesense-footprint-lab.py")
)
assert SPEC and SPEC.loader
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)

DISPLAY_FIELDS = ("company_name", "location_names", "seniority_name", "technology_names")
SORT_FIELDS = (
    "is_active",
    "has_content",
    "seniority_id",
    "experience_min",
    "experience_max",
    "experience_min_years",
    "experience_max_years",
)
LABEL = "jobseek.typesense-rss-lab"


def write(path, value):
    lab._write_json_atomic(path, value)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def candidate_field(field):
    result = copy.deepcopy(field)
    if field["name"] in DISPLAY_FIELDS:
        result.update(index=False, facet=False, sort=False, optional=True)
    elif field["name"] in SORT_FIELDS:
        result["sort"] = False
    else:
        raise ValueError("Field is not in the reviewed pruning set")
    return result


def capture_os(name):
    paths = (
        "/proc/1/smaps_rollup",
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory.peak",
        "/sys/fs/cgroup/memory.stat",
        "/sys/fs/cgroup/memory.events",
    )
    raw = subprocess.check_output(
        ["docker", "exec", name, "awk", 'FNR==1{print "FILE",FILENAME}{print}', *paths],
        text=True,
        timeout=10,
    )
    sections, current = {}, None
    for line in raw.splitlines():
        if line.startswith("FILE "):
            current = line[5:]
            sections[current] = {}
        else:
            parts = line.split()
            if len(parts) == 1 and parts[0].isdigit():
                sections[current]["value"] = int(parts[0])
            elif len(parts) >= 2 and parts[1].isdigit():
                sections[current][parts[0].rstrip(":")] = int(parts[1])
    memory = sections["/sys/fs/cgroup/memory.stat"]
    return {
        "time": time.time(),
        "rss_bytes": sections["/proc/1/smaps_rollup"]["Rss"] * 1024,
        "cgroup_bytes": sections["/sys/fs/cgroup/memory.current"]["value"],
        "cgroup_lifetime_peak_bytes": sections["/sys/fs/cgroup/memory.peak"]["value"],
        "stat": {k: memory.get(k, 0) for k in ("anon", "file", "kernel", "slab_unreclaimable")},
        "events": sections["/sys/fs/cgroup/memory.events"],
    }


class Sampler:
    def __init__(self, state):
        self.state, self.samples, self.errors = state, [], []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.samples.append(capture_os(self.state["container"]))
            except (subprocess.SubprocessError, ValueError, KeyError) as exc:
                self.errors.append(str(exc))
            self.stop_event.wait(1)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop_event.set()
        self.thread.join(timeout=12)


def api(state, method, path, payload=None, timeout=7200):
    return lab._request_json(state["port"], method, path, payload=payload, timeout=timeout)


def wait_for_writes(state, timeout=1800):
    started, stable, probes = time.monotonic(), 0, 0
    while time.monotonic() - started < timeout:
        pending = api(state, "GET", "/stats.json", timeout=10).get("pending_write_batches")
        if type(pending) is not int or pending < 0:
            raise RuntimeError("Server did not expose a valid pending-write count")
        probes += 1
        stable = stable + 1 if pending == 0 else 0
        if stable >= 3:
            return {"probes": probes, "elapsed_seconds": round(time.monotonic() - started, 3)}
        time.sleep(2)
    raise RuntimeError("Pending writes did not drain before measurement")


def import_documents(state, path, max_documents=None, skip_documents=0):
    accepted, source_hash, batch = 0, hashlib.sha256(), []
    url = f"http://127.0.0.1:{state['port']}/collections/{lab.LAB_COLLECTION}/documents/import?action=upsert&batch_size=1000"

    def send():
        request = urllib.request.Request(
            url,
            data=b"".join(batch),
            method="POST",
            headers={"X-TYPESENSE-API-KEY": lab.LAB_API_KEY, "Content-Type": "text/plain"},
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            # A batch response is small; consume the complete HTTP body before
            # validating every acknowledgement, including the final line.
            acknowledgements = [
                json.loads(line) for line in response.read().splitlines() if line.strip()
            ]
        if len(acknowledgements) != len(batch) or any(
            r.get("success") is not True for r in acknowledgements
        ):
            errors = [
                {k: v for k, v in r.items() if k != "document"}
                for r in acknowledgements
                if not r.get("success")
            ]
            raise RuntimeError(
                f"Import through {accepted}: expected {len(batch)} acknowledgements, "
                f"received {len(acknowledgements)}; errors: {errors[:3]}"
            )

    with gzip.open(path, "rb") as source:
        for line in source:
            if not line.strip():
                continue
            source_hash.update(line)
            accepted += 1
            if accepted <= skip_documents:
                continue
            batch.append(line if line.endswith(b"\n") else line + b"\n")
            if len(batch) == 5000 or accepted == max_documents:
                send()
                batch.clear()
                if accepted % 100000 == 0:
                    print(f"Imported {accepted:,}", flush=True)
            if accepted == max_documents:
                break
        if batch:
            send()
    count = api(state, "GET", f"/collections/{lab.LAB_COLLECTION}")["num_documents"]
    if count != accepted:
        raise RuntimeError(f"Expected {accepted} stored documents, found {count}")
    return {
        "documents": accepted,
        "resumed_after": skip_documents,
        "source_sha256": source_hash.hexdigest(),
    }


def query_corpus(state):
    queries = dict(lab.query_corpus())
    queries["year_flow"]["filter_by"] = (
        f"has_content:!=false && first_seen_at:>{state['year_start']}"
    )
    # Enumerate complete facet maps, so tied values at a truncated boundary do
    # not conceal a missing choice or produce a false semantic mismatch.
    queries["taxonomy_facets"]["max_facet_values"] = 50000
    queries["active_location_facets"]["max_facet_values"] = 50000
    queries["experience_overlap"]["max_facet_values"] = 50000
    grouped = copy.deepcopy(queries["keyword_grouped"])
    grouped.update(typo_tokens_threshold=1, drop_tokens_threshold=1, per_page=11)
    for key in ("facet_by", "facet_strategy", "max_facet_values", "include_fields"):
        grouped.pop(key, None)
    queries["keyword_current"] = grouped
    queries["keyword_page5"] = {**grouped, "page": 5}
    queries["keyword_sales"] = {**grouped, "q": "sales"}
    queries["keyword_remote"] = {
        **grouped,
        "filter_by": grouped["filter_by"] + " && location_types:=[remote] && locales:=[en]",
    }
    queries["zero"] = {**grouped, "q": "zzzxqvnojobmatchzzzxqv"}
    queries["stable_candidates"] = {
        "q": "*",
        "query_by": "title",
        "filter_by": "is_active:true && has_content:!=false",
        "sort_by": "first_seen_at:desc,candidate_order_hi:asc,candidate_order_lo:asc",
        "per_page": 250,
    }
    queries["inactive_history"] = {
        "q": "*",
        "filter_by": (
            f"is_active:false && has_content:!=false && first_seen_at:>{state['year_start']}"
        ),
        "per_page": 0,
        "facet_by": "company_id",
        "facet_strategy": "exhaustive",
        "max_facet_values": 50000,
    }
    queries["decimal_experience"] = {
        "q": "*",
        "filter_by": (
            "is_active:true && has_content:!=false "
            "&& experience_min_years:<=6.5 && experience_max_years:>=2.5"
        ),
        "sort_by": "first_seen_at:desc",
        "per_page": 50,
    }
    return queries


def project(label, result):
    value = lab._consumed_result_projection(label, result)
    value["found_docs"] = result.get("found_docs")
    for facet in value["facets"]:
        if "counts" in facet:
            facet["counts"].sort(key=lambda row: row["value"])
    # Also prove the actual stored display payload survives index removal.
    value["documents"] = [h["document"] for h in result.get("hits", [])]
    value["group_documents"] = [
        [h["document"] for h in g["hits"]] for g in result.get("grouped_hits", [])
    ]
    return value


class QueryProbe(Sampler):
    """Observe read correctness/availability while the schema patcher runs."""

    def __init__(self, state, baseline):
        super().__init__(state)
        self.baseline = baseline

    def run(self):
        queries = query_corpus(self.state)
        labels = ("keyword_current", "decimal_experience", "taxonomy_facets", "inactive_history")
        index = 0
        while not self.stop_event.is_set():
            label = labels[index % len(labels)]
            index += 1
            entry = {"time": time.time(), "query": label}
            started = time.monotonic()
            try:
                params = urllib.parse.urlencode({**queries[label], "use_cache": "false"})
                result = api(
                    self.state,
                    "GET",
                    f"/collections/{lab.LAB_COLLECTION}/documents/search?{params}",
                    timeout=5,
                )
                projected = digest(project(label, result))
                entry.update(
                    search_time_ms=result["search_time_ms"],
                    projection_sha256=projected,
                    matches_baseline=projected == self.baseline[label]["projection_sha256"],
                    search_cutoff=bool(result.get("search_cutoff")),
                )
            except Exception as exc:
                entry["error"] = str(exc)
                self.errors.append(str(exc))
            entry["client_ms"] = round((time.monotonic() - started) * 1000, 3)
            self.samples.append(entry)
            self.stop_event.wait(1)


def measure(state, repeats, concurrency=1, cases=None):
    queries, results = query_corpus(state), {}
    if cases:
        if set(cases) - queries.keys():
            raise ValueError("Unknown query cases")
        queries = {label: queries[label] for label in cases}

    def search(params):
        started = time.monotonic()
        result = lab._search(state["port"], {**params, "use_cache": "false"})
        return result, round((time.monotonic() - started) * 1000, 3)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for label, params in queries.items():
            times, wall_times, hashes, found, found_docs = [], [], [], None, None
            for repeat in range(-2, repeats):
                futures = [pool.submit(search, params) for _ in range(concurrency)]
                for future in futures:
                    result, wall_time = future.result()
                    if result.get("search_cutoff"):
                        raise RuntimeError(f"Query cutoff: {label}")
                    if repeat >= 0:
                        times.append(result["search_time_ms"])
                        wall_times.append(wall_time)
                        hashes.append(digest(project(label, result)))
                        found, found_docs = result.get("found"), result.get("found_docs")
            results[label] = {
                "median_ms": statistics.median(times),
                "p95_ms": lab._percentile(times, 0.95),
                "samples_ms": times,
                "client_median_ms": statistics.median(wall_times),
                "client_p95_ms": lab._percentile(wall_times, 0.95),
                "client_samples_ms": wall_times,
                "projection_sha256": hashes[0],
                "repeat_drift": len(set(hashes)) != 1,
                "found": found,
                "found_docs": found_docs,
            }
    return {"queries": results, "parameters": queries, "concurrency": concurrency}


def fingerprint(state):
    # Order-independent modular sum of per-document SHA-256 values, with count.
    total, count = 0, 0
    url = f"http://127.0.0.1:{state['port']}/collections/{lab.LAB_COLLECTION}/documents/export"
    request = urllib.request.Request(url, headers={"X-TYPESENSE-API-KEY": lab.LAB_API_KEY})
    with urllib.request.urlopen(request, timeout=600) as response:
        for line in response:
            if line.strip():
                total = (total + int(digest(json.loads(line)), 16)) % (1 << 256)
                count += 1
    return {"documents": count, "document_sha256_modular_sum": f"{total:064x}"}


def cdc_round_trip(state):
    """Replay a bounded delist/relist batch, then verify every affected payload."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/crawler"))
    from src.typesense_candidate_order import candidate_order_fields

    originals = []
    for page in (1, 2):
        response = lab._search(
            state["port"],
            {**query_corpus(state)["stable_candidates"], "page": page},
        )
        originals.extend(hit["document"] for hit in response["hits"])
    expected = {doc["id"]: doc for doc in originals}
    if len(expected) != len(originals) or not originals:
        raise RuntimeError("CDC probe needs distinct active documents")

    def counts():
        return {
            flag: lab._search(
                state["port"], {"q": "*", "filter_by": "is_active:" + flag, "per_page": 0}
            )["found"]
            for flag in ("true", "false")
        }

    def upsert(documents):
        body = b"\n".join(json.dumps(doc, separators=(",", ":")).encode() for doc in documents)
        request = urllib.request.Request(
            f"http://127.0.0.1:{state['port']}/collections/{lab.LAB_COLLECTION}/documents/import?action=upsert",
            data=body,
            method="POST",
            headers={"X-TYPESENSE-API-KEY": lab.LAB_API_KEY, "Content-Type": "text/plain"},
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            acknowledgements = [json.loads(line) for line in response.read().splitlines()]
        if len(acknowledgements) != len(documents) or any(
            row.get("success") is not True for row in acknowledgements
        ):
            raise RuntimeError("CDC probe did not acknowledge every document")

    before = counts()
    started = time.monotonic()
    try:
        upsert(
            [
                {
                    **doc,
                    "is_active": False,
                    **candidate_order_fields(uuid.UUID(doc["id"]), active=False),
                }
                for doc in originals
            ]
        )
        inactive = counts()
        if inactive != {
            "true": before["true"] - len(originals),
            "false": before["false"] + len(originals),
        }:
            raise RuntimeError("CDC delist changed unexpected counts")
    finally:
        upsert(originals)
    after = counts()
    if after != before:
        raise RuntimeError("CDC relist did not restore counts")
    for offset in range(0, len(originals), 100):
        identifiers = [doc["id"] for doc in originals[offset : offset + 100]]
        response = lab._search(
            state["port"],
            {"q": "*", "filter_by": "id:=[" + ",".join(identifiers) + "]", "per_page": 250},
        )
        actual = {hit["document"]["id"]: hit["document"] for hit in response["hits"]}
        if actual != {identifier: expected[identifier] for identifier in identifiers}:
            raise RuntimeError("CDC relist did not restore stored payloads")
    return {
        "updated_documents": len(originals),
        "before": before,
        "delisted": inactive,
        "restored": after,
        "payloads_restored": True,
        "round_trip_ms": round((time.monotonic() - started) * 1000, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "init",
            "import",
            "measure",
            "migrate",
            "rollback",
            "restart",
            "snapshot",
            "fingerprint",
            "setup",
            "cdc",
        ),
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--sample", type=Path)
    parser.add_argument("--field", choices=DISPLAY_FIELDS + SORT_FIELDS)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--max-documents", type=int)
    parser.add_argument("--skip-documents", type=int, default=0)
    parser.add_argument("--settle-seconds", type=int, default=45)
    parser.add_argument("--label", default=None)
    args = parser.parse_args()
    if (
        args.repeats < 1
        or not 1 <= args.concurrency <= 8
        or args.settle_seconds < 0
        or (args.max_documents is not None and args.max_documents < 1)
        or args.skip_documents < 0
        or (args.max_documents is not None and args.skip_documents >= args.max_documents)
    ):
        parser.error("repeats/document limits must be positive; settling cannot be negative")
    root = args.root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_path = root / "state.json"
    if args.command == "init":
        if state_path.exists() or not args.schema:
            parser.error("init needs --schema and a new root")
        schema = json.loads(args.schema.read_text())
        schema = {
            k: v
            for k, v in schema.items()
            if k
            in (
                "fields",
                "default_sorting_field",
                "token_separators",
                "symbols_to_index",
                "enable_nested_fields",
            )
        }
        schema["name"] = lab.LAB_COLLECTION
        name = "jobseek-ts-rss-" + uuid.uuid4().hex[:10]
        state = {
            "container": name,
            "port": lab._free_loopback_port(),
            "year_start": lab._one_year_ago_unix(),
            "native_architecture": platform.machine(),
            "image": lab.PINNED_TYPESENSE_IMAGE,
            "data_volume": name + "-data",
            "snapshot_volume": name + "-snapshots",
        }
        write(root / "baseline-schema.json", schema)
        write(state_path, state)
        for volume in (state["data_volume"], state["snapshot_volume"]):
            subprocess.run(
                ["docker", "volume", "create", "--label", LABEL + "=" + str(root), volume],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--label",
                LABEL + "=" + str(root),
                "--platform",
                "linux/arm64" if platform.machine() == "arm64" else "linux/amd64",
                "--memory",
                "6g",
                "--memory-swap",
                "6g",
                "--cpus",
                "4",
                "-p",
                f"127.0.0.1:{state['port']}:8108",
                "--mount",
                f"type=volume,source={state['data_volume']},target=/data",
                "--mount",
                f"type=volume,source={state['snapshot_volume']},target=/snapshots",
                lab.PINNED_TYPESENSE_IMAGE,
                "--data-dir=/data",
                f"--api-key={lab.LAB_API_KEY}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        lab._wait_for_health(state["port"], timeout=120)
        api(state, "POST", "/collections", schema)
        print(json.dumps(state), flush=True)
        return
    state = json.loads(state_path.read_text())
    container = json.loads(subprocess.check_output(["docker", "inspect", state["container"]]))[0]
    if (
        container["Config"]["Labels"].get(LABEL) != str(root)
        or container["HostConfig"]["Memory"] != 6 * 1024**3
    ):
        raise RuntimeError("Refusing a container without this lab's ownership and memory limit")
    label = args.label or args.command + ("-" + args.field if args.field else "")
    if not label.replace("-", "").replace("_", "").isalnum():
        parser.error("Invalid artifact label")
    if (root / (label + ".json")).exists():
        parser.error("Artifact already exists; use a different label")
    result = {
        "phase": label,
        "started_at": datetime.now(UTC).isoformat(),
        "complete": False,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runtime": {
            "image_id": container["Image"],
            "native_architecture": state["native_architecture"],
            "memory_limit_bytes": container["HostConfig"]["Memory"],
            "nano_cpus": container["HostConfig"]["NanoCpus"],
            "restart_count_before": container["RestartCount"],
            "oom_killed_before": container["State"]["OOMKilled"],
        },
    }
    started = time.monotonic()
    with Sampler(state) as sampler:
        try:
            if args.command != "import" or args.skip_documents:
                result["write_readiness"] = wait_for_writes(state)
            if args.command == "import":
                if not args.sample:
                    parser.error("import needs --sample")
                if args.skip_documents:
                    # /health can become true before a recovered write queue
                    # has fully replayed. Wait for stable content/allocation
                    # before resuming a previously acknowledged source prefix.
                    result["resume_readiness"] = lab._wait_for_semantic_readiness(
                        state["port"], timeout=1800
                    )
                    existing = api(state, "GET", f"/collections/{lab.LAB_COLLECTION}")
                    if existing["num_documents"] < args.skip_documents:
                        raise RuntimeError("Recovered collection is smaller than the resume prefix")
                result.update(
                    import_documents(state, args.sample, args.max_documents, args.skip_documents)
                )
            elif args.command == "measure":
                result.update(measure(state, args.repeats, args.concurrency, args.cases))
            elif args.command == "fingerprint":
                result.update(fingerprint(state))
            elif args.command == "cdc":
                result.update(cdc_round_trip(state))
            elif args.command in ("migrate", "rollback"):
                if not args.field:
                    parser.error("migration needs --field")
                baseline = json.loads((root / "baseline-schema.json").read_text())
                old = next(f for f in baseline["fields"] if f["name"] == args.field)
                desired = candidate_field(old) if args.command == "migrate" else old
                result["patch"] = {"fields": [{"name": args.field, "drop": True}, desired]}
                api(state, "PATCH", f"/collections/{lab.LAB_COLLECTION}", result["patch"])
                result["schema_after"] = api(state, "GET", f"/collections/{lab.LAB_COLLECTION}")
            elif args.command == "restart":
                expected = api(state, "GET", f"/collections/{lab.LAB_COLLECTION}")["num_documents"]
                # Match an existing, checkpointed production collection. A
                # fresh import otherwise replays its entire write journal on
                # restart, mixing ingestion with the index-rebuild measurement.
                checkpoint = "/snapshots/" + label + "-checkpoint"
                subprocess.run(
                    ["docker", "exec", state["container"], "test", "!", "-e", checkpoint],
                    check=True,
                )
                result["checkpoint_started_at"] = time.time()
                result["checkpoint"] = api(
                    state,
                    "POST",
                    "/operations/snapshot?" + urllib.parse.urlencode({"snapshot_path": checkpoint}),
                )
                result["restart_started_at"] = time.time()
                subprocess.run(
                    ["docker", "restart", state["container"]], check=True, stdout=subprocess.DEVNULL
                )
                lab._wait_for_health(state["port"], timeout=1800)
                lab._wait_for_document_count(state["port"], expected, timeout=1800)
                result["write_readiness_after_restart"] = wait_for_writes(state)
                result["semantic_readiness"] = lab._wait_for_semantic_readiness(
                    state["port"],
                    timeout=1800,
                )
                result["rebuild_completed_at"] = time.time()
                # Only remove the checkpoint just created by this owned lab.
                # The separately acquired source and labelled snapshots remain.
                subprocess.run(
                    ["docker", "exec", state["container"], "rm", "-rf", checkpoint], check=True
                )
            elif args.command == "setup":
                # Run with the crawler's uv environment to rehearse the exact
                # deployment patcher, including its per-field ordering.
                import typesense

                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/crawler"))
                from src.typesense_schema import _patch_missing_fields

                desired = lab.load_job_posting_schema()
                result["desired_schema"] = desired
                client = typesense.Client(
                    {
                        "nodes": [
                            {"host": "127.0.0.1", "port": str(state["port"]), "protocol": "http"}
                        ],
                        "api_key": lab.LAB_API_KEY,
                        "connection_timeout_seconds": 3600,
                        "num_retries": 0,
                    }
                )
                collection = client.collections[lab.LAB_COLLECTION]
                update = collection.update
                result["field_patches"] = []

                def measured_update(payload):
                    patch = {"payload": payload, "started_at": time.time(), "complete": False}
                    result["field_patches"].append(patch)
                    patch["os_before"] = capture_os(state["container"])
                    response = update(payload)
                    patch["completed_at"] = time.time()
                    patch["os_after"] = capture_os(state["container"])
                    patch["allocator_after"] = lab._capture_memory(state["port"])
                    patch["complete"] = True
                    return response

                collection.update = measured_update
                baseline_path = root / "baseline.json"
                if baseline_path.exists():
                    baseline = json.loads(baseline_path.read_text())
                    if not baseline["complete"]:
                        raise RuntimeError("Incomplete query baseline")
                    probe = QueryProbe(state, baseline["queries"])
                    try:
                        with probe:
                            _patch_missing_fields(client, lab.LAB_COLLECTION, desired["fields"])
                    finally:
                        result["query_probes"] = probe.samples.copy()
                        result["query_probe_errors"] = probe.errors.copy()
                else:
                    result["query_probe_unavailable"] = "No baseline.json in this lab"
                    _patch_missing_fields(client, lab.LAB_COLLECTION, desired["fields"])
                result["schema_after"] = api(state, "GET", f"/collections/{lab.LAB_COLLECTION}")
            elif args.command == "snapshot":
                result["response"] = api(
                    state,
                    "POST",
                    "/operations/snapshot?"
                    + urllib.parse.urlencode({"snapshot_path": "/snapshots/" + label}),
                )
            # Match the settling window between phases; don't mistake retained
            # allocator pages immediately after a rebuild for steady RSS.
            time.sleep(args.settle_seconds)
            result["allocator"] = lab._capture_memory(state["port"])
            result["os_after"] = capture_os(state["container"])
            after = json.loads(subprocess.check_output(["docker", "inspect", state["container"]]))[
                0
            ]
            result["runtime"].update(
                restart_count_after=after["RestartCount"],
                oom_killed_after=after["State"]["OOMKilled"],
            )
            result["complete"] = True
        except Exception as exc:
            result["error"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            result["samples"] = sampler.samples.copy()
            result["sample_errors"] = sampler.errors.copy()
            write(root / (label + ".json"), result)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k in ("phase", "complete", "elapsed_seconds", "documents", "os_after")
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
