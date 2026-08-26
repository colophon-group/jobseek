#!/usr/bin/env python3
"""Benchmark Typesense ``job_posting`` schema footprint candidates locally.

The lab is deliberately incapable of mutating production. It starts one
disposable Typesense container at a time, binds it to loopback, imports an
operator-supplied JSONL/JSONL.GZ document sample, captures allocator metrics,
and replays a parity corpus. Production documents should be acquired with a
read-only ``documents:export`` request and transferred separately.

Examples::

    python scripts/typesense-footprint-lab.py schema --variant combined-pruned
    python scripts/typesense-footprint-lab.py benchmark sample.jsonl.gz \
      --output /tmp/typesense-footprint.json
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PINNED_TYPESENSE_IMAGE = (
    "typesense/typesense:27.1@"
    "sha256:5c12af89130b8ee0be11541321ba8a3a7c7a538d7c6cd95e0409dc2d75ca6455"
)
LAB_API_KEY = "jobseek-typesense-footprint-lab"
LAB_COLLECTION = "job_posting_lab"

# Facet fields observed in production web read paths. ``company_id`` must also
# remain faceted because Typesense requires that for ``group_by``.
REQUIRED_FACET_FIELDS = frozenset(
    {
        "company_id",
        "employment_type",
        "experience_min",
        "locales",
        "location_direct_ids",
        "location_ids",
        "location_types",
        "occupation_ids",
        "salary_eur",
        "seniority_id",
        "technology_ids",
    }
)

# ``first_seen_at`` is explicitly sorted by current read paths. Typesense 27.1
# also requires a sort index for numerical range facets, so ``salary_eur``
# remains sortable even though it never appears in a ``sort_by`` clause.
REQUIRED_SORT_FIELDS = frozenset({"first_seen_at", "salary_eur"})

# These values are returned to callers or retained for compatibility, but no
# production read path searches, filters, facets, groups, or sorts on them.
# Marking them unindexed preserves the stored response payload exactly.
RESPONSE_ONLY_INDEXED_FIELDS = frozenset(
    {
        "company_name",
        "last_seen_at",
        "location_names",
        "occupation_id",
        "occupation_name",
        "seniority_name",
        "technology_names",
    }
)
INITIAL_PRODUCTION_TUNING_FIELDS = frozenset({"last_seen_at", "occupation_id", "occupation_name"})

# Response-only fields were faceted in the pre-#8033 production schema except
# for the diagnostic timestamp. This lets the lab reconstruct that deployed
# baseline after the canonical schema itself has adopted an optimization.
LEGACY_FACETED_RESPONSE_FIELDS = RESPONSE_ONLY_INDEXED_FIELDS - {"last_seen_at"}

VARIANTS = (
    "baseline",
    "facet-pruned",
    "sort-pruned",
    "response-unindexed",
    "combined-pruned",
)


class LabError(RuntimeError):
    """A local benchmark invariant failed."""


class _PeakMemorySampler:
    """Poll allocator metrics while an import or startup rebuild is active."""

    def __init__(self, port: int, interval: float = 0.25) -> None:
        self._port = port
        self._interval = interval
        self._peaks: dict[str, int | float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def reset(self) -> None:
        with self._lock:
            self._peaks = {}

    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            return dict(self._peaks)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                memory = _capture_memory(self._port, timeout=0.5)
            except (LabError, OSError, urllib.error.URLError, TimeoutError):
                pass
            else:
                with self._lock:
                    for key, value in memory.items():
                        if key.endswith("_bytes"):
                            self._peaks[key] = max(int(value), int(self._peaks.get(key, 0)))
            self._stop.wait(self._interval)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_job_posting_schema(repo_root: Path | None = None) -> dict[str, Any]:
    """Read the canonical literal schema without importing crawler runtime deps."""

    root = repo_root or _repo_root()
    schema_path = root / "apps/crawler/src/typesense_schema.py"
    tree = ast.parse(schema_path.read_text(), filename=str(schema_path))
    collections: list[dict[str, Any]] | None = None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "COLLECTIONS" for target in targets
        ):
            continue
        value = node.value
        if value is None:
            break
        collections = ast.literal_eval(value)
        break
    if collections is None:
        raise LabError(f"Could not find literal COLLECTIONS in {schema_path}")
    try:
        schema = next(item for item in collections if item["name"] == "job_posting")
    except StopIteration as exc:
        raise LabError("Canonical job_posting schema is missing") from exc
    return copy.deepcopy(schema)


def build_variant_schema(
    base_schema: Mapping[str, Any],
    variant: str,
    *,
    collection_name: str = LAB_COLLECTION,
    response_fields: frozenset[str] = RESPONSE_ONLY_INDEXED_FIELDS,
) -> dict[str, Any]:
    """Return a candidate schema while keeping all document fields stored."""

    if variant not in VARIANTS:
        raise LabError(f"Unknown variant {variant!r}; choose from {', '.join(VARIANTS)}")
    schema = copy.deepcopy(dict(base_schema))
    schema["name"] = collection_name
    prune_facets = variant in {"facet-pruned", "combined-pruned"}
    prune_sorts = variant in {"sort-pruned", "combined-pruned"}
    unindex_responses = variant in {"response-unindexed", "combined-pruned"}

    for field in schema["fields"]:
        name = field["name"]
        if prune_facets:
            field["facet"] = name in REQUIRED_FACET_FIELDS
        if prune_sorts:
            field["sort"] = name in REQUIRED_SORT_FIELDS
        if unindex_responses and name in response_fields:
            field["index"] = False
            field["facet"] = False
            field["sort"] = False
    return schema


def restore_pre_tuning_indexes(
    base_schema: Mapping[str, Any],
    field_names: frozenset[str],
) -> dict[str, Any]:
    """Reconstruct selected response indexes from the pre-tuning schema.

    Benchmark inputs normally come from the current canonical schema. Once a
    candidate ships, silently using that schema as both sides of an A/B run
    would measure allocator noise instead of savings. This explicit transform
    preserves reproducibility without keeping a second full schema copy.
    """

    unknown = field_names - RESPONSE_ONLY_INDEXED_FIELDS
    if unknown:
        raise LabError(f"Cannot restore unknown response fields: {', '.join(sorted(unknown))}")

    schema = copy.deepcopy(dict(base_schema))
    fields_by_name = {field["name"]: field for field in schema["fields"]}
    missing = field_names - fields_by_name.keys()
    if missing:
        raise LabError(f"Canonical schema is missing fields: {', '.join(sorted(missing))}")

    for name in field_names:
        field = fields_by_name[name]
        field.pop("index", None)
        field.pop("sort", None)
        if name in LEGACY_FACETED_RESPONSE_FIELDS:
            field["facet"] = True
        else:
            field.pop("facet", None)
    return schema


def _request_json(
    port: int,
    method: str,
    path: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    body = None
    headers = {"X-TYPESENSE-API-KEY": LAB_API_KEY}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise LabError(f"Typesense {method} {path} returned {exc.code}: {detail}") from exc


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_checked(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, check=False, text=True, **kwargs)
    if result.returncode != 0:
        command = " ".join(args[:3])
        raise LabError(f"Command failed ({result.returncode}): {command}")
    return result


def _docker_image_architecture(image: str) -> str:
    result = _run_checked(
        ["docker", "image", "inspect", image, "--format", "{{.Architecture}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _wait_for_health(port: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _request_json(port, "GET", "/health", timeout=2.0).get("ok") is True:
                return
        except (LabError, OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise LabError("Disposable Typesense node did not become healthy")


def _metric_bytes(metrics: Mapping[str, Any], key: str) -> int:
    value = metrics.get(key)
    if value is None:
        raise LabError(f"Typesense metrics response omitted {key}")
    return int(value)


def _capture_memory(port: int, *, timeout: float = 120.0) -> dict[str, int | float]:
    metrics = _request_json(port, "GET", "/metrics.json", timeout=timeout)
    return {
        "active_bytes": _metric_bytes(metrics, "typesense_memory_active_bytes"),
        "allocated_bytes": _metric_bytes(metrics, "typesense_memory_allocated_bytes"),
        "mapped_bytes": _metric_bytes(metrics, "typesense_memory_mapped_bytes"),
        "metadata_bytes": _metric_bytes(metrics, "typesense_memory_metadata_bytes"),
        "resident_bytes": _metric_bytes(metrics, "typesense_memory_resident_bytes"),
        "retained_bytes": _metric_bytes(metrics, "typesense_memory_retained_bytes"),
        "fragmentation_ratio": float(metrics["typesense_memory_fragmentation_ratio"]),
    }


def _directory_bytes(path: Path) -> int:
    return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())


def _start_container(name: str, port: int, data_dir: Path, image: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _run_checked(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            f"127.0.0.1:{port}:8108",
            "--volume",
            f"{data_dir}:/data",
            image,
            "--data-dir=/data",
            f"--api-key={LAB_API_KEY}",
            "--enable-cors",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _wait_for_health(port)


def _stop_container(name: str) -> None:
    subprocess.run(
        ["docker", "stop", "--time", "30", name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _remove_container(name: str) -> None:
    subprocess.run(
        ["docker", "rm", "--force", name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_document_count(port: int, expected: int, timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    observed = -1
    while time.monotonic() < deadline:
        try:
            collection = _request_json(
                port,
                "GET",
                f"/collections/{LAB_COLLECTION}",
                timeout=5.0,
            )
            observed = int(collection["num_documents"])
            if observed == expected:
                return
        except (LabError, OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise LabError(
        f"Typesense rebuild did not reach {expected} documents; last observed {observed}"
    )


def _restart_container(name: str, port: int, expected_documents: int) -> float:
    _stop_container(name)
    started = time.monotonic()
    _run_checked(
        ["docker", "start", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _wait_for_health(port, timeout=300.0)
    _wait_for_document_count(port, expected_documents, timeout=300.0)
    return time.monotonic() - started


def _readiness_projection(port: int) -> dict[str, Any]:
    """Exercise rebuilt filter and facet indexes, not just the health endpoint."""

    return _result_projection(
        _search(
            port,
            {
                "q": "*",
                "filter_by": "is_active:[true,false]",
                "facet_by": "company_id,experience_min",
                "facet_strategy": "exhaustive",
                "max_facet_values": 100,
                "per_page": 0,
            },
        )
    )


def _wait_for_semantic_readiness(
    port: int,
    *,
    timeout: float = 180.0,
    required_stable_probes: int = 3,
    interval: float = 2.0,
) -> dict[str, Any]:
    """Wait until representative query output and allocation settle after load."""

    started = time.monotonic()
    deadline = started + timeout
    previous_projection: dict[str, Any] | None = None
    previous_allocated: int | None = None
    stable_probes = 0
    probes = 0
    while time.monotonic() < deadline:
        try:
            projection = _readiness_projection(port)
            allocated = int(_capture_memory(port)["allocated_bytes"])
        except (LabError, OSError, urllib.error.URLError):
            stable_probes = 0
        else:
            probes += 1
            allocation_stable = (
                previous_allocated is not None
                and abs(allocated - previous_allocated) <= 4 * 1024 * 1024
            )
            if projection == previous_projection and allocation_stable:
                stable_probes += 1
            else:
                stable_probes = 1
            previous_projection = projection
            previous_allocated = allocated
            if stable_probes >= required_stable_probes:
                return {
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "probes": probes,
                    "stable_probes": stable_probes,
                    "allocated_bytes": allocated,
                }
        time.sleep(interval)
    raise LabError("Typesense reached its document count but filter/facet output did not stabilize")


def _import_documents(port: int, sample_path: Path) -> dict[str, Any]:
    if not sample_path.is_file():
        raise LabError(f"Sample does not exist: {sample_path}")
    source_file = None
    decompressor: subprocess.Popen[bytes] | None = None
    if sample_path.suffix == ".gz":
        decompressor = subprocess.Popen(
            ["gzip", "-cd", str(sample_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdin = decompressor.stdout
    else:
        source_file = sample_path.open("rb")
        stdin = source_file

    url = (
        f"http://127.0.0.1:{port}/collections/{LAB_COLLECTION}/documents/import"
        "?action=create&batch_size=1000"
    )
    started = time.monotonic()
    curl = subprocess.Popen(
        [
            "curl",
            "--fail-with-body",
            "--silent",
            "--show-error",
            "--request",
            "POST",
            url,
            "--header",
            f"X-TYPESENSE-API-KEY: {LAB_API_KEY}",
            "--header",
            "Content-Type: text/plain",
            "--data-binary",
            "@-",
        ],
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    if decompressor is not None and decompressor.stdout is not None:
        decompressor.stdout.close()
    accepted = 0
    rejected: list[dict[str, Any]] = []
    assert curl.stdout is not None
    for raw_line in curl.stdout:
        if not raw_line.strip():
            continue
        acknowledgement = json.loads(raw_line)
        if acknowledgement.get("success") is True:
            accepted += 1
        elif len(rejected) < 10:
            rejected.append(acknowledgement)
    curl_stderr = curl.stderr.read().decode("utf-8", "replace") if curl.stderr else ""
    curl_returncode = curl.wait()
    if source_file is not None:
        source_file.close()
    if decompressor is not None:
        gzip_stderr = (
            decompressor.stderr.read().decode("utf-8", "replace") if decompressor.stderr else ""
        )
        gzip_returncode = decompressor.wait()
        if gzip_returncode != 0:
            raise LabError(f"gzip failed ({gzip_returncode}): {gzip_stderr[:1000]}")
    if curl_returncode != 0:
        raise LabError(f"Typesense import failed ({curl_returncode}): {curl_stderr[:2000]}")
    if rejected:
        raise LabError(f"Typesense rejected sample documents: {rejected[:3]}")
    elapsed = time.monotonic() - started
    return {
        "accepted_documents": accepted,
        "elapsed_seconds": round(elapsed, 3),
        "documents_per_second": round(accepted / elapsed, 2) if elapsed else None,
    }


def _one_year_ago_unix() -> int:
    now = datetime.now(UTC)
    try:
        return int(now.replace(year=now.year - 1).timestamp())
    except ValueError:  # February 29
        return int((now - timedelta(days=365)).timestamp())


def query_corpus() -> list[tuple[str, dict[str, Any]]]:
    """Representative current web queries that every safe variant must support."""

    base = "is_active:true && has_content:!=false"
    return [
        (
            "keyword_grouped",
            {
                "q": "software engineer",
                "query_by": "title",
                "filter_by": base,
                "sort_by": "_text_match:desc,first_seen_at:desc",
                "group_by": "company_id",
                "group_limit": 10,
                "facet_by": "company_id",
                "facet_strategy": "exhaustive",
                "max_facet_values": 10,
                "per_page": 20,
                "include_fields": "id",
            },
        ),
        (
            "active_location_facets",
            {
                "q": "*",
                "filter_by": f"{base} && location_ids:[6252001]",
                "facet_by": "company_id",
                "facet_strategy": "exhaustive",
                "max_facet_values": 20,
                "per_page": 0,
            },
        ),
        (
            "combined_filters",
            {
                "q": "*",
                "filter_by": (
                    f"{base} && occupation_ids:[1] && technology_ids:[133] "
                    "&& location_types:[remote,hybrid] && employment_type:[full_time] "
                    "&& locales:[en,_none]"
                ),
                "sort_by": "first_seen_at:desc",
                "per_page": 20,
                "include_fields": "id",
            },
        ),
        (
            "salary_histogram",
            {
                "q": "*",
                "filter_by": f"{base} && salary_eur:>0",
                "facet_by": (
                    "salary_eur(0-50k:[0,50000], 50-100k:[50000,100000], 100k+:[100000,999999999])"
                ),
                "max_facet_values": 3,
                "per_page": 0,
            },
        ),
        (
            "experience_overlap",
            {
                "q": "*",
                "query_by": "title",
                "filter_by": f"{base} && experience_min:>=0",
                "facet_by": "experience_min",
                "max_facet_values": 30,
                "per_page": 0,
            },
        ),
        (
            "year_flow",
            {
                "q": "*",
                "query_by": "title",
                "filter_by": f"has_content:!=false && first_seen_at:>{_one_year_ago_unix()}",
                "per_page": 0,
            },
        ),
        (
            "taxonomy_facets",
            {
                "q": "*",
                "filter_by": base,
                "facet_by": (
                    "location_ids,location_direct_ids,occupation_ids,seniority_id,"
                    "technology_ids,employment_type,location_types,locales"
                ),
                "facet_strategy": "exhaustive",
                "max_facet_values": 50,
                "per_page": 0,
            },
        ),
        (
            "reconciliation_partition",
            {
                "q": "*",
                "filter_by": "reconciliation_bucket:=00",
                "per_page": 20,
                "include_fields": "id,is_active,reconciliation_bucket",
            },
        ),
    ]


def _search(port: int, params: Mapping[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    return _request_json(
        port,
        "GET",
        f"/collections/{LAB_COLLECTION}/documents/search?{query}",
    )


def _result_projection(result: Mapping[str, Any]) -> dict[str, Any]:
    facets = []
    for facet in result.get("facet_counts", []) or []:
        facets.append(
            {
                "field_name": facet.get("field_name"),
                "counts": [
                    {"value": item.get("value"), "count": item.get("count")}
                    for item in facet.get("counts", [])
                ],
                "total_values": (facet.get("stats") or {}).get("total_values"),
            }
        )
    return {
        "found": result.get("found"),
        "hits": [hit.get("document", {}).get("id") for hit in result.get("hits", []) or []],
        "groups": [
            {
                "group_key": group.get("group_key"),
                "found": group.get("found"),
                "hits": [hit.get("document", {}).get("id") for hit in group.get("hits", []) or []],
            }
            for group in result.get("grouped_hits", []) or []
        ],
        "facets": facets,
    }


def _consumed_result_projection(label: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Project only response properties consumed by the matching web path."""

    projection = _result_projection(result)
    if label == "keyword_grouped":
        # Primary search asks for one company facet solely to read
        # ``stats.total_values``. The tied facet value itself is ignored and is
        # intrinsically non-deterministic across otherwise identical rebuilds.
        projection["facets"] = [
            {
                "field_name": facet["field_name"],
                "total_values": facet["total_values"],
            }
            for facet in projection["facets"]
        ]
    return projection


def _percentile(values: Sequence[int], percentile: float) -> int:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(percentile * len(ordered) + 0.9999) - 1))
    return ordered[index]


def _run_query_corpus(port: int, repeats: int, warmups: int) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for label, params in query_corpus():
        for _ in range(warmups):
            _search(port, params)
        durations: list[int] = []
        projections: list[dict[str, Any]] = []
        for _ in range(repeats):
            result = _search(port, params)
            durations.append(int(result.get("search_time_ms", 0)))
            projections.append(_consumed_result_projection(label, result))
        report[label] = {
            "search_time_ms": {
                "min": min(durations),
                "median": statistics.median(durations),
                "p95": _percentile(durations, 0.95),
                "max": max(durations),
            },
            "projection": projections[0],
            "repeat_projection_drift": any(
                projection != projections[0] for projection in projections[1:]
            ),
        }
    return report


def _parity_mismatches(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[str]:
    labels = set(baseline) | set(candidate)
    return sorted(
        label
        for label in labels
        if baseline.get(label, {}).get("projection") != candidate.get(label, {}).get("projection")
    )


def benchmark_variant(
    sample_path: Path,
    base_schema: Mapping[str, Any],
    variant: str,
    *,
    image: str,
    repeats: int,
    warmups: int,
    response_fields: frozenset[str],
    scratch_root: Path,
) -> dict[str, Any]:
    port = _free_loopback_port()
    name = f"jobseek-typesense-footprint-{os.getpid()}-{variant}"
    data_dir = scratch_root / variant
    schema = build_variant_schema(
        base_schema,
        variant,
        response_fields=response_fields,
    )
    _start_container(name, port, data_dir, image)
    sampler = _PeakMemorySampler(port)
    sampler.start()
    try:
        _request_json(port, "POST", "/collections", payload=schema)
        empty_memory = _capture_memory(port)
        sampler.reset()
        imported = _import_documents(port, sample_path)
        _wait_for_document_count(port, imported["accepted_documents"])
        import_peak_memory = sampler.snapshot()
        post_import_memory = _capture_memory(port)
        sampler.reset()
        rebuild_count_seconds = _restart_container(
            name,
            port,
            imported["accepted_documents"],
        )
        semantic_readiness = _wait_for_semantic_readiness(port)
        rebuild_peak_memory = sampler.snapshot()
        collection = _request_json(port, "GET", f"/collections/{LAB_COLLECTION}")
        memory = _capture_memory(port)
        queries = _run_query_corpus(port, repeats, warmups)
        _stop_container(name)
        data_directory_bytes = _directory_bytes(data_dir)
        return {
            "variant": variant,
            "response_unindexed_fields": (
                sorted(response_fields)
                if variant in {"response-unindexed", "combined-pruned"}
                else []
            ),
            "schema_fields": schema["fields"],
            "documents": int(collection["num_documents"]),
            "import": imported,
            "rebuild_seconds": round(
                rebuild_count_seconds + semantic_readiness["elapsed_seconds"], 3
            ),
            "rebuild_document_count_seconds": round(rebuild_count_seconds, 3),
            "semantic_readiness": semantic_readiness,
            "memory": memory,
            "import_peak_memory": import_peak_memory,
            "rebuild_peak_memory": rebuild_peak_memory,
            "post_import_memory": post_import_memory,
            "empty_memory": empty_memory,
            "index_delta_bytes": {
                key: int(memory[key]) - int(empty_memory[key])
                for key in (
                    "active_bytes",
                    "allocated_bytes",
                    "mapped_bytes",
                    "metadata_bytes",
                    "resident_bytes",
                    "retained_bytes",
                )
            },
            "data_directory_bytes_after_clean_stop": data_directory_bytes,
            "queries": queries,
        }
    finally:
        sampler.stop()
        _remove_container(name)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    sample_path = Path(args.sample).resolve()
    canonical_schema = load_job_posting_schema()
    restored_fields = frozenset(args.baseline_indexed_fields)
    response_fields = frozenset(args.response_fields)
    canonical_fields = {field["name"]: field for field in canonical_schema["fields"]}
    variants = list(dict.fromkeys(args.variants))
    if "baseline" not in variants:
        variants.insert(0, "baseline")
    invalid = [variant for variant in variants if variant not in VARIANTS]
    if invalid:
        raise LabError(f"Unknown variants: {', '.join(invalid)}")
    response_variants = {"response-unindexed", "combined-pruned"}.intersection(variants)
    already_unindexed = {
        name
        for name in response_fields - restored_fields
        if canonical_fields[name].get("index", True) is False
    }
    if response_variants and already_unindexed:
        raise LabError(
            "Selected response fields are already unindexed in the canonical schema: "
            f"{', '.join(sorted(already_unindexed))}. Reconstruct the deployed baseline with "
            "--baseline-indexed-fields."
        )
    base_schema = restore_pre_tuning_indexes(canonical_schema, restored_fields)
    _run_checked(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    environment = {
        "host_machine": platform.machine(),
        "docker_image_architecture": _docker_image_architecture(args.image),
    }

    scratch = Path(tempfile.mkdtemp(prefix="jobseek-typesense-footprint-"))
    reports: list[dict[str, Any]] = []
    try:
        for variant in variants:
            print(f"benchmarking {variant}", file=sys.stderr, flush=True)
            reports.append(
                benchmark_variant(
                    sample_path,
                    base_schema,
                    variant,
                    image=args.image,
                    repeats=args.repeats,
                    warmups=args.warmups,
                    response_fields=response_fields,
                    scratch_root=scratch,
                )
            )
            if args.output:
                _write_json_atomic(
                    args.output.resolve(),
                    {
                        "complete": False,
                        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "typesense_image": args.image,
                        "environment": environment,
                        "sample": str(sample_path),
                        "baseline_indexed_fields": sorted(restored_fields),
                        "variants": reports,
                    },
                )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    baseline = next(report for report in reports if report["variant"] == "baseline")
    baseline_queries = baseline["queries"]
    baseline_allocated = baseline["index_delta_bytes"]["allocated_bytes"]
    baseline_resident = baseline["memory"]["resident_bytes"]
    for report in reports:
        parity_mismatches = _parity_mismatches(
            baseline_queries,
            report["queries"],
        )
        unstable_queries = sorted(
            label
            for label in set(baseline_queries) | set(report["queries"])
            if baseline_queries.get(label, {}).get("repeat_projection_drift")
            or report["queries"].get(label, {}).get("repeat_projection_drift")
        )
        report["parity_mismatches"] = parity_mismatches
        report["parity_unstable_queries"] = unstable_queries
        report["stable_parity_mismatches"] = sorted(set(parity_mismatches) - set(unstable_queries))
        allocated = report["index_delta_bytes"]["allocated_bytes"]
        report["allocated_savings_vs_baseline_bytes"] = baseline_allocated - allocated
        report["allocated_savings_vs_baseline_pct"] = (
            round(100 * (baseline_allocated - allocated) / baseline_allocated, 3)
            if baseline_allocated
            else None
        )
        report["resident_savings_vs_baseline_bytes"] = (
            baseline_resident - report["memory"]["resident_bytes"]
        )
        report["peak_savings_vs_baseline_bytes"] = {
            phase: {
                metric: baseline[f"{phase}_peak_memory"].get(metric, 0)
                - report[f"{phase}_peak_memory"].get(metric, 0)
                for metric in ("allocated_bytes", "resident_bytes")
            }
            for phase in ("import", "rebuild")
        }
        baseline_rebuild = float(baseline["rebuild_seconds"])
        report["rebuild_regression_vs_baseline_pct"] = (
            round(
                100 * (float(report["rebuild_seconds"]) - baseline_rebuild) / baseline_rebuild,
                3,
            )
            if baseline_rebuild
            else None
        )
        query_regressions: dict[str, float | None] = {}
        for label, result in report["queries"].items():
            baseline_p95 = float(
                baseline_queries.get(label, {}).get("search_time_ms", {}).get("p95", 0)
            )
            candidate_p95 = float(result["search_time_ms"]["p95"])
            query_regressions[label] = (
                round(100 * (candidate_p95 - baseline_p95) / baseline_p95, 3)
                if baseline_p95
                else None
            )
        report["query_p95_regression_vs_baseline_pct"] = query_regressions
        report["query_p95_gate_failures"] = sorted(
            label
            for label, regression in query_regressions.items()
            if regression is not None and regression > 10.0
        )

    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "typesense_image": args.image,
        "environment": environment,
        "sample": str(sample_path),
        "sample_compressed_bytes": sample_path.stat().st_size,
        "baseline_indexed_fields": sorted(restored_fields),
        "variants": reports,
    }


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    schema_parser = subparsers.add_parser("schema", help="Print one candidate schema")
    schema_parser.add_argument("--variant", choices=VARIANTS, default="combined-pruned")
    schema_parser.add_argument("--name", default=LAB_COLLECTION)
    schema_parser.add_argument(
        "--response-fields",
        nargs="+",
        choices=sorted(RESPONSE_ONLY_INDEXED_FIELDS),
        default=sorted(RESPONSE_ONLY_INDEXED_FIELDS),
        help="stored fields to unindex in response-unindexed/combined-pruned variants",
    )

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Benchmark candidate schemas on disposable loopback containers",
    )
    benchmark_parser.add_argument("sample", help="Production-shaped JSONL or JSONL.GZ sample")
    benchmark_parser.add_argument(
        "--variants",
        nargs="+",
        choices=VARIANTS,
        default=list(VARIANTS),
    )
    benchmark_parser.add_argument("--repeats", type=int, default=7)
    benchmark_parser.add_argument("--warmups", type=int, default=2)
    benchmark_parser.add_argument(
        "--response-fields",
        nargs="+",
        choices=sorted(RESPONSE_ONLY_INDEXED_FIELDS),
        default=sorted(RESPONSE_ONLY_INDEXED_FIELDS - INITIAL_PRODUCTION_TUNING_FIELDS),
        help="stored fields to unindex in response-unindexed/combined-pruned variants",
    )
    benchmark_parser.add_argument(
        "--baseline-indexed-fields",
        nargs="+",
        choices=sorted(RESPONSE_ONLY_INDEXED_FIELDS),
        default=[],
        help=(
            "reconstruct pre-tuning indexes for fields already unindexed in the canonical schema"
        ),
    )
    benchmark_parser.add_argument("--image", default=PINNED_TYPESENSE_IMAGE)
    benchmark_parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "schema":
            schema = build_variant_schema(
                load_job_posting_schema(),
                args.variant,
                collection_name=args.name,
                response_fields=frozenset(args.response_fields),
            )
            json.dump(schema, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return 0

        if args.repeats < 1:
            raise LabError("--repeats must be positive")
        if args.warmups < 0:
            raise LabError("--warmups cannot be negative")
        report = run_benchmark(args)
        if args.output:
            _write_json_atomic(args.output.resolve(), report)
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (LabError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
