from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")


def canonical_job_manifest_bytes(jobs: list[dict[str, Any]]) -> bytes:
    lines: list[str] = []
    for job in jobs:
        fields = (
            str(job["id"]),
            str(job["origin"]),
            str(job["sitemap_url"]),
            str(job["scenario"]),
            str(job["expected_outcome"]),
        )
        if any("\t" in field or "\n" in field or "\r" in field for field in fields):
            raise ValueError("job manifest fields may not contain tabs or newlines")
        lines.append("\t".join(fields) + "\n")
    return "".join(lines).encode("utf-8")


def job_manifest_sha256(jobs: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_job_manifest_bytes(jobs)).hexdigest()


def load_corpus(bench_root: Path) -> dict[str, Any]:
    corpus_path = bench_root / "corpus.json"
    checksum_path = bench_root / "corpus.sha256"
    checksum_fields = checksum_path.read_text(encoding="utf-8").strip().split()
    if len(checksum_fields) != 2 or checksum_fields[1] != "corpus.json":
        raise ValueError("corpus.sha256 must contain exactly the corpus.json digest")
    actual = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    if actual != checksum_fields[0]:
        raise ValueError("corpus.json does not match corpus.sha256")
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    validate_corpus(corpus)
    lock_path = bench_root / "uv.lock"
    if (
        not lock_path.is_file()
        or hashlib.sha256(lock_path.read_bytes()).hexdigest()
        != corpus["source"]["python_lock_sha256"]
    ):
        raise ValueError("uv.lock does not match the hash frozen in corpus.json")
    return corpus


def validate_corpus(corpus: dict[str, Any]) -> None:
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported corpus schema")
    source = corpus["source"]
    if not re.fullmatch(r"[0-9a-f]{40}", source["python_source_commit"]):
        raise ValueError("invalid frozen Python source commit")
    if source["python_runtime"] != "3.13.15":
        raise ValueError("frozen Python runtime must remain 3.13.15")
    if not re.fullmatch(
        r"python:3\.13\.15-[^@]+@sha256:[0-9a-f]{64}", source["python_image_identity"]
    ):
        raise ValueError("invalid frozen Python image identity")
    if not re.fullmatch(r"[0-9a-f]{64}", source["python_lock_sha256"]):
        raise ValueError("invalid frozen Python lock hash")
    expected_packages = {
        "anyio",
        "certifi",
        "h11",
        "httpcore",
        "httpx",
        "idna",
        "prometheus-client",
        "structlog",
        "typing-extensions",
    }
    if set(source["python_runtime_packages"]) != expected_packages or any(
        not isinstance(version, str) or not version
        for version in source["python_runtime_packages"].values()
    ):
        raise ValueError("frozen Python runtime package census mismatch")
    if source["python_absent_runtime_packages"] != ["sniffio"]:
        raise ValueError("frozen absent-package census mismatch")
    if source["python_os_release"] != {"ID": "debian", "VERSION_ID": "13"}:
        raise ValueError("frozen Python OS release mismatch")
    if source["go_toolchain"] != "go1.24.0":
        raise ValueError("frozen Go toolchain must remain go1.24.0")
    if source["go_module"] != "github.com/colophon-group/jobseek/pilots/go-http-sitemap":
        raise ValueError("frozen Go module mismatch")
    if source["go_runner_path"] != source["go_module"] + "/bench/go-runner":
        raise ValueError("frozen Go runner path mismatch")
    source_files = source["python_files"]
    if {item["relative_path"] for item in source_files} != {
        "src/core/monitors/sitemap.py",
        "src/shared/http_retry.py",
        "src/shared/tdm.py",
        "src/shared/constants.py",
        "src/metrics.py",
    }:
        raise ValueError("frozen Python source file census mismatch")
    if any(not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in source_files):
        raise ValueError("invalid frozen Python file hash")
    defaults = corpus["defaults"]
    required_positive = (
        "global_active_requests",
        "global_total_connections",
        "global_idle_connections",
        "idle_connections_per_origin",
        "idle_expiry_seconds",
        "per_origin_concurrency",
        "request_timeout_seconds",
        "job_timeout_seconds",
        "file_descriptor_limit",
        "origin_count",
        "warmup_jobs",
        "capacity_jobs",
        "overload_jobs",
        "repetitions",
    )
    if any(
        not isinstance(defaults.get(key), int) or defaults[key] <= 0 for key in required_positive
    ):
        raise ValueError("corpus defaults must be positive integers")
    if defaults["repetitions"] < 5:
        raise ValueError("evidence requires at least five repetitions")
    if defaults["warmup_jobs"] < 128 or defaults["capacity_jobs"] < 512:
        raise ValueError("evidence batches are too short for stable measurement")
    if defaults["overload_jobs"] < 1024:
        raise ValueError("bounded conservation stress requires at least 1024 jobs")
    if defaults["idle_expiry_seconds"] != 5:
        raise ValueError("idle connection expiry must remain five seconds")
    workers = [entry["workers"] for entry in corpus["ladder"] if entry["kind"] == "capacity"]
    if workers != [1, 5, 20, 50]:
        raise ValueError("capacity ladder must remain 1, 5, 20, 50")
    if not any(entry["kind"] == "overload" for entry in corpus["ladder"]):
        raise ValueError("benchmark ladder requires a bounded conservation stress step")

    scenarios = corpus["scenarios"]
    ids = [scenario["id"] for scenario in scenarios]
    if len(ids) != len(set(ids)) or any(SAFE_ID.fullmatch(value) is None for value in ids):
        raise ValueError("scenario IDs must be unique safe identifiers")
    if not any(scenario["suite"] == "capacity" for scenario in scenarios):
        raise ValueError("capacity scenario is missing")
    if not any(scenario["suite"] == "policy" for scenario in scenarios):
        raise ValueError("policy scenario is missing")
    for scenario in scenarios:
        if scenario["suite"] not in {"capacity", "policy"}:
            raise ValueError("unknown scenario suite")
        if scenario["expected_outcome"] not in {"success", "retry_exhausted"}:
            raise ValueError("unknown expected outcome")
        if not re.fullmatch(r"[0-9a-f]{64}", scenario["expected_url_digest_sha256"]):
            raise ValueError("invalid expected URL digest")
        statuses = scenario["root_statuses"]
        if not statuses or any(
            not isinstance(status, int) or not 100 <= status <= 599 for status in statuses
        ):
            raise ValueError("root status sequence is invalid")
        if scenario["index_children"] not in {0, 4}:
            raise ValueError("fixture supports only urlset or four-child index cases")
        if scenario["suite"] == "capacity" and scenario["expected_outcome"] != "success":
            raise ValueError("capacity jobs must be successful")


def scenario_map(corpus: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(scenario["id"]): scenario for scenario in corpus["scenarios"]}


def validate_fixture_origin(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    if parsed.scheme != "http" or parsed.username is not None or parsed.password is not None:
        raise ValueError("fixture origins must be credential-free HTTP URLs")
    if not parsed.hostname or parsed.port is None or parsed.path not in {"", "/"}:
        raise ValueError("fixture origin must contain only an explicit host and port")
    addresses = {
        ipaddress.ip_address(info[4][0])
        for info in socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
    }
    if not addresses or any(
        not (address.is_loopback or address.is_private) for address in addresses
    ):
        raise ValueError("fixture origin resolved outside loopback/private address space")
    return f"http://{parsed.hostname}:{parsed.port}"


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def interval_max(intervals: list[tuple[int, int]]) -> int:
    events: list[tuple[int, int]] = []
    for start, finish in intervals:
        if finish <= start:
            continue
        events.append((start, 1))
        events.append((finish, -1))
    current = 0
    maximum = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        current += delta
        maximum = max(maximum, current)
    return maximum
