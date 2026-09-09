#!/usr/bin/env python3
"""Sanitize and classify the fleet benchmark wire protocol.

This parser is intentionally dependency-free. It never copies untrusted report
objects wholesale: every retained field is type/range checked and reconstructed
from an allowlist, so discovered URLs and exception text cannot reach artifacts.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

PROFILES = {"c2": 2, "c4": 4, "c5": 5, "c8": 8, "c12": 12, "c16": 16}
EXPECTED_PAIR_COUNTS = {"c2": 4, "c4": 4, "c5": 6, "c8": 4, "c12": 6, "c16": 6}
PREFLIGHT_FAILURE_STAGES = frozenset(
    {
        "inventory",
        "protected_services",
        "host_resources_before_pull",
        "pull_go_image",
        "pull_python_image",
        "host_resources_after_pull",
        "image_attestation",
        "schedule_validation",
        "dns_pinning",
    }
)
MAX_WIRE_BYTES = 8_388_608
BYTE_IMBALANCE_LIMIT = 0.05
TOKEN_RE = re.compile(r"^[a-z0-9_]{1,64}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_RE = re.compile(r"^[A-Za-z0-9.+_-]{1,64}$")

TOP_FIELDS = {
    "schema_version",
    "implementation",
    "runtime_version",
    "profile",
    "concurrency",
    "source_commit",
    "image_identity",
    "manifest_sha256",
    "status",
    "error_kind",
    "startup_duration_ms",
    "startup_cpu_user_ms",
    "startup_cpu_system_ms",
    "startup_rss_bytes",
    "startup_open_fds",
    "run_duration_ms",
    "cpu_user_ms",
    "cpu_system_ms",
    "process_max_rss_bytes",
    "cgroup_memory_peak_bytes",
    "cgroup_memory_current_bytes",
    "cgroup_oom_events",
    "configured",
    "rounds",
    "final",
}
CONFIG_FIELDS = {
    "max_connections",
    "max_keepalive_connections",
    "per_origin_concurrency",
    "http_version",
    "accept_encoding",
    "request_timeout_ms",
    "job_timeout_ms",
    "max_response_bytes",
    "max_aggregate_bytes",
    "max_requests_per_job",
}
ROUND_FIELDS = {
    "round",
    "connection_state",
    "status",
    "error_kind",
    "run_duration_ms",
    "cpu_user_ms",
    "cpu_system_ms",
    "process_max_rss_bytes",
    "process_current_rss_bytes",
    "open_fds",
    "max_in_flight",
    "request_count",
    "wire_attempt_count",
    "decoded_bytes",
    "status_body_bytes",
    "jobs",
}
JOB_FIELDS = {
    "id",
    "status",
    "error_kind",
    "canonical_url_count",
    "canonical_url_sha256",
    "canonical_url_hash_algorithm",
    "filtered_count",
    "truncated",
    "request_count",
    "wire_attempt_count",
    "decoded_bytes",
    "status_body_bytes",
    "queue_duration_ms",
    "service_duration_ms",
}
FINAL_FIELDS = {
    "accepted",
    "completed",
    "failed",
    "queued",
    "in_flight",
    "max_in_flight",
    "connections_open",
    "connection_limit",
    "per_origin_limit",
    "connection_waiters",
    "maximum_connections",
    "process_current_rss_bytes",
    "open_fds",
}


class InputError(ValueError):
    """Raised for a trusted local input that violates the benchmark contract."""


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant rejected: {value}")


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle, parse_constant=_reject_constant)


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _kind(value: Any) -> bool:
    return value is None or isinstance(value, str) and TOKEN_RE.fullmatch(value) is not None


def _project_numeric(source: dict[str, Any], names: set[str]) -> dict[str, int | float] | None:
    output: dict[str, int | float] = {}
    for name in names:
        if name not in source:
            continue
        value = source[name]
        if not _number(value):
            return None
        output[name] = value
    return output


def _sanitize_job(raw: Any, expected_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not set(raw) <= JOB_FIELDS:
        return None
    job_id = raw.get("id")
    if not isinstance(job_id, str) or ID_RE.fullmatch(job_id) is None or job_id not in expected_ids:
        return None
    status = raw.get("status")
    if status not in {"succeeded", "failed"} or not _kind(raw.get("error_kind")):
        return None
    if "truncated" in raw and not isinstance(raw["truncated"], bool):
        return None
    digest = raw.get("canonical_url_sha256")
    if digest is not None and (not isinstance(digest, str) or SHA_RE.fullmatch(digest) is None):
        return None
    algorithm = raw.get("canonical_url_hash_algorithm")
    if algorithm is not None and algorithm != "sha256-length-prefixed-v1":
        return None
    numeric_names = JOB_FIELDS - {
        "id",
        "status",
        "error_kind",
        "canonical_url_sha256",
        "canonical_url_hash_algorithm",
        "truncated",
    }
    numeric = _project_numeric(raw, numeric_names)
    if numeric is None:
        return None
    output: dict[str, Any] = {"id": job_id, "status": status, **numeric}
    if raw.get("error_kind") is not None:
        output["error_kind"] = raw["error_kind"]
    if "truncated" in raw:
        output["truncated"] = raw["truncated"]
    if digest is not None:
        output["canonical_url_sha256"] = digest
    if algorithm is not None:
        output["canonical_url_hash_algorithm"] = algorithm
    return output


def _sanitize_round(raw: Any, expected_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not set(raw) <= ROUND_FIELDS:
        return None
    state = raw.get("connection_state")
    status = raw.get("status")
    if state not in {"cold", "pool_warm"} or status not in {"succeeded", "failed"}:
        return None
    if not _kind(raw.get("error_kind")):
        return None
    jobs_raw = raw.get("jobs")
    if not isinstance(jobs_raw, list) or len(jobs_raw) > 32:
        return None
    jobs = [_sanitize_job(item, expected_ids) for item in jobs_raw]
    if any(item is None for item in jobs):
        return None
    numeric_names = ROUND_FIELDS - {"connection_state", "status", "error_kind", "jobs"}
    numeric = _project_numeric(raw, numeric_names)
    if numeric is None:
        return None
    output: dict[str, Any] = {
        "connection_state": state,
        "status": status,
        "jobs": jobs,
        **numeric,
    }
    if raw.get("error_kind") is not None:
        output["error_kind"] = raw["error_kind"]
    return output


def _sanitize_config(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not set(raw) <= CONFIG_FIELDS:
        return None
    output: dict[str, Any] = {}
    for name, value in raw.items():
        if name == "http_version":
            if value != "1.1":
                return None
        elif name == "accept_encoding":
            if value != "identity":
                return None
        elif not _integer(value):
            return None
        output[name] = value
    return output


def _sanitize_final(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not set(raw) <= FINAL_FIELDS:
        return None
    output: dict[str, Any] = {}
    for name, value in raw.items():
        if not _integer(value):
            return None
        output[name] = value
    return output


def sanitize_raw_report(
    raw: Any,
    *,
    implementation: str,
    profile: str,
    source_commit: str,
    manifest_sha256: str,
    image_identity: str,
    expected_ids: set[str],
) -> dict[str, Any] | None:
    """Return a fresh safe projection, or None if any retained field is unsafe."""
    if not isinstance(raw, dict) or not set(raw) <= TOP_FIELDS:
        return None
    if (
        raw.get("schema_version") != 1
        or raw.get("implementation") != implementation
        or raw.get("profile") != profile
        or raw.get("source_commit") != source_commit
        or raw.get("manifest_sha256") != manifest_sha256
        or raw.get("image_identity") != image_identity
        or raw.get("status") not in {"succeeded", "failed"}
        or not _kind(raw.get("error_kind"))
    ):
        return None
    runtime = raw.get("runtime_version")
    if runtime is not None and (
        not isinstance(runtime, str) or RUNTIME_RE.fullmatch(runtime) is None
    ):
        return None
    top_numeric = TOP_FIELDS - {
        "schema_version",
        "implementation",
        "runtime_version",
        "profile",
        "source_commit",
        "image_identity",
        "manifest_sha256",
        "status",
        "error_kind",
        "configured",
        "rounds",
        "final",
    }
    numeric = _project_numeric(raw, top_numeric)
    if numeric is None:
        return None
    rounds_raw = raw.get("rounds", [])
    if not isinstance(rounds_raw, list) or len(rounds_raw) > 2:
        return None
    rounds = [_sanitize_round(item, expected_ids) for item in rounds_raw]
    if any(item is None for item in rounds):
        return None
    configured = _sanitize_config(raw["configured"]) if "configured" in raw else None
    if "configured" in raw and configured is None:
        return None
    final = _sanitize_final(raw["final"]) if "final" in raw else None
    if "final" in raw and raw["final"] is not None and final is None:
        return None
    output: dict[str, Any] = {
        "schema_version": 1,
        "implementation": implementation,
        "profile": profile,
        "source_commit": source_commit,
        "image_identity": image_identity,
        "manifest_sha256": manifest_sha256,
        "status": raw["status"],
        "rounds": rounds,
        "final": final,
        **numeric,
    }
    if runtime is not None:
        output["runtime_version"] = runtime
    if raw.get("error_kind") is not None:
        output["error_kind"] = raw["error_kind"]
    if configured is not None:
        output["configured"] = configured
    return output


def _load_contract(
    fleet_path: Path, schedule_path: Path, cooldown: int
) -> tuple[list[str], list[tuple[str, str, str]]]:
    fleet = _load_json(fleet_path)
    if (
        not isinstance(fleet, dict)
        or set(fleet) != {"schema_version", "jobs"}
        or fleet.get("schema_version") != 1
    ):
        raise InputError("invalid fleet contract")
    jobs = fleet.get("jobs")
    if not isinstance(jobs, list) or len(jobs) != 32:
        raise InputError("fleet must contain 32 jobs")
    ids: list[str] = []
    for job in jobs:
        if not isinstance(job, dict):
            raise InputError("invalid fleet IDs")
        value = job.get("id")
        if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
            raise InputError("invalid fleet IDs")
        ids.append(value)
    if len(set(ids)) != 32:
        raise InputError("duplicate fleet IDs")

    schedule = _load_json(schedule_path)
    if not isinstance(schedule, dict) or set(schedule) != {
        "schema_version",
        "seed",
        "cooldown_seconds",
        "pairs",
    }:
        raise InputError("invalid schedule contract")
    if (
        schedule.get("schema_version") != 1
        or schedule.get("seed") != 8648
        or schedule.get("cooldown_seconds") != cooldown
    ):
        raise InputError("unexpected schedule identity")
    pairs = schedule.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 30:
        raise InputError("schedule must contain 30 pairs")
    expected: list[tuple[str, str, str]] = []
    counts = {profile: 0 for profile in EXPECTED_PAIR_COUNTS}
    firsts = {
        (profile, implementation): 0
        for profile in EXPECTED_PAIR_COUNTS
        for implementation in ("go", "python")
    }
    for number, pair in enumerate(pairs, 1):
        if not isinstance(pair, dict) or set(pair) != {"id", "profile", "first"}:
            raise InputError("invalid schedule pair")
        pair_id, profile, first = pair["id"], pair["profile"], pair["first"]
        if pair_id != f"p{number:02d}" or profile not in PROFILES or first not in {"go", "python"}:
            raise InputError("invalid schedule pair value")
        counts[profile] += 1
        firsts[profile, first] += 1
        second = "python" if first == "go" else "go"
        expected.extend(((pair_id, profile, first), (pair_id, profile, second)))
    if counts != EXPECTED_PAIR_COUNTS or any(
        firsts[profile, implementation] != count // 2
        for profile, count in EXPECTED_PAIR_COUNTS.items()
        for implementation in ("go", "python")
    ):
        raise InputError("schedule is not balanced")
    return sorted(ids), expected


def _successful_arm(arm: dict[str, Any], expected_ids: list[str]) -> bool:
    concurrency = PROFILES[arm["profile"]]
    configured = {
        "max_connections": 20,
        "max_keepalive_connections": 10,
        "per_origin_concurrency": 1,
        "http_version": "1.1",
        "accept_encoding": "identity",
        "request_timeout_ms": 20_000,
        "job_timeout_ms": 25_000,
        "max_response_bytes": 8_388_608,
        "max_aggregate_bytes": 33_554_432,
        "max_requests_per_job": 1,
    }
    if (
        not arm["raw_report_valid"]
        or arm["source_failure_kind"] != "none"
        or arm.get("status") != "succeeded"
        or arm.get("error_kind") is not None
        or arm.get("concurrency") != concurrency
        or arm.get("configured") != configured
        or arm["container_lifecycle"]
        != {
            "status": "exited",
            "exit_code": 0,
            "oom_killed": False,
            "valid": True,
        }
    ):
        return False
    if (
        not all(
            _number(arm.get(name))
            for name in (
                "startup_duration_ms",
                "startup_cpu_user_ms",
                "startup_cpu_system_ms",
                "run_duration_ms",
                "cpu_user_ms",
                "cpu_system_ms",
            )
        )
        or arm["run_duration_ms"] <= 0
    ):
        return False
    if not 0 < arm.get("startup_rss_bytes", 0) <= 1_073_741_824:
        return False
    if not 3 <= arm.get("startup_open_fds", -1) <= 256:
        return False
    if not 0 < arm.get("process_max_rss_bytes", 0) <= 1_073_741_824:
        return False
    if not 0 < arm.get("cgroup_memory_peak_bytes", 0) <= 1_073_741_824:
        return False
    if (
        not 0 < arm.get("cgroup_memory_current_bytes", 0) <= 1_073_741_824
        or arm.get("cgroup_oom_events") != 0
    ):
        return False
    rounds = arm.get("rounds")
    if not isinstance(rounds, list) or len(rounds) != 2:
        return False
    for number, state, round_report in zip((1, 2), ("cold", "pool_warm"), rounds, strict=True):
        if (
            round_report.get("round") != number
            or round_report.get("connection_state") != state
            or round_report.get("status") != "succeeded"
            or round_report.get("error_kind") is not None
            or round_report.get("max_in_flight") != concurrency
            or round_report.get("request_count") != 32
            or round_report.get("wire_attempt_count") != 32
            or round_report.get("status_body_bytes") != 0
            or not _number(round_report.get("run_duration_ms"))
            or round_report["run_duration_ms"] <= 0
            or not _number(round_report.get("cpu_user_ms"))
            or not _number(round_report.get("cpu_system_ms"))
            or not 0 < round_report.get("process_max_rss_bytes", 0) <= 1_073_741_824
            or not 0 < round_report.get("process_current_rss_bytes", 0) <= 1_073_741_824
            or not 3 <= round_report.get("open_fds", -1) <= 256
        ):
            return False
        jobs = round_report.get("jobs")
        if not isinstance(jobs, list) or [job.get("id") for job in jobs] != expected_ids:
            return False
        if len({job["id"] for job in jobs}) != 32:
            return False
        if round_report.get("decoded_bytes") != sum(job.get("decoded_bytes", -1) for job in jobs):
            return False
        for job in jobs:
            if (
                job.get("status") != "succeeded"
                or job.get("error_kind") is not None
                or job.get("truncated") is not False
                or job.get("request_count") != 1
                or job.get("wire_attempt_count") != 1
                or job.get("status_body_bytes") != 0
                or not 0 < job.get("decoded_bytes", 0) <= 8_388_608
                or not 0 < job.get("canonical_url_count", 0) <= 50_000
                or not _integer(job.get("filtered_count"))
                or not _number(job.get("queue_duration_ms"))
                or not _number(job.get("service_duration_ms"))
                or job.get("canonical_url_hash_algorithm") != "sha256-length-prefixed-v1"
                or not isinstance(job.get("canonical_url_sha256"), str)
                or SHA_RE.fullmatch(job["canonical_url_sha256"]) is None
            ):
                return False
    final = arm.get("final")
    return bool(
        isinstance(final, dict)
        and final.get("accepted") == 64
        and final.get("completed") == 64
        and final.get("failed") == 0
        and final.get("queued") == 0
        and final.get("in_flight") == 0
        and final.get("max_in_flight") == concurrency
        and final.get("connections_open") == 0
        and final.get("connection_limit") == 20
        and final.get("per_origin_limit") == 1
        and final.get("connection_waiters") == 0
        and 1 <= final.get("maximum_connections", 0) <= 20
        and 0 < final.get("process_current_rss_bytes", 0) <= 1_073_741_824
        and 3 <= final.get("open_fds", -1) <= arm["startup_open_fds"]
    )


def parse_report(args: argparse.Namespace) -> dict[str, Any]:
    expected_ids, expected_arms = _load_contract(args.fleet, args.schedule, args.cooldown)
    expected_ids_set = set(expected_ids)
    protocol_valid = args.wire.is_file() and args.wire.stat().st_size <= MAX_WIRE_BYTES
    try:
        lines = (
            args.wire.read_text(encoding="utf-8", errors="strict").splitlines()
            if protocol_valid
            else []
        )
    except (OSError, UnicodeError):
        lines, protocol_valid = [], False
    protected_baseline_sha256: str | None = None
    preflight_failure_stage: str | None = None
    meta: dict[str, Any] | None = None
    postflight_valid = False
    arms: list[dict[str, Any]] = []
    index = 0
    if lines:
        fields = lines[0].split("\t")
        if fields[0] == "PREFLIGHT_FAILURE":
            marker_valid = False
            if len(fields) == 4:
                try:
                    marker_status = int(fields[2])
                except ValueError:
                    marker_status = -1
                marker_valid = bool(
                    len(lines) == 1
                    and fields[1] in PREFLIGHT_FAILURE_STAGES
                    and marker_status == args.remote_status
                    and marker_status in {70, 71}
                    and (fields[3] == "none" or SHA_RE.fullmatch(fields[3]))
                )
            protocol_valid &= marker_valid
            if marker_valid:
                preflight_failure_stage = fields[1]
                if fields[3] != "none":
                    protected_baseline_sha256 = fields[3]
            index = 1
        elif (
            len(fields) == 10
            and fields[0] == "RUN_META"
            and SHA_RE.fullmatch(fields[1])
            and SHA_RE.fullmatch(fields[2])
        ):
            try:
                protected_baseline_sha256 = fields[2]
                meta = {
                    "pinset_sha256": fields[1],
                    "protected_baseline_sha256": fields[2],
                    "cooldown_seconds": int(fields[3]),
                    "mem_available_kib_before_pull": int(fields[4]),
                    "docker_free_kib_before_pull": int(fields[5]),
                    "load1_before_pull": float(fields[6]),
                    "mem_available_kib_after_pull": int(fields[7]),
                    "docker_free_kib_after_pull": int(fields[8]),
                    "load1_after_pull": float(fields[9]),
                }
                protocol_valid &= meta["cooldown_seconds"] == args.cooldown
                protocol_valid &= all(
                    _number(value)
                    for key, value in meta.items()
                    if key not in {"pinset_sha256", "protected_baseline_sha256"}
                )
                if not protocol_valid:
                    meta = None
            except ValueError:
                protocol_valid = False
            index += 1
        else:
            protocol_valid = False
    else:
        protocol_valid = False

    while protocol_valid and index < len(lines):
        fields = lines[index].split("\t")
        if fields[0] == "RUN_POSTFLIGHT":
            postflight_valid = bool(
                len(fields) == 4
                and fields[1] == "true"
                and meta is not None
                and fields[2] == meta["pinset_sha256"]
                and fields[3] == meta["protected_baseline_sha256"]
                and fields[3] == args.protected_postflight_sha256
            )
            index += 1
            protocol_valid &= index == len(lines)
            break
        if meta is None:
            protocol_valid = False
            break
        if len(fields) != 15 or fields[0] != "ARM_LIFECYCLE" or index + 1 >= len(lines):
            protocol_valid = False
            break
        (
            _,
            pair_id,
            implementation,
            profile,
            container_status,
            exit_text,
            oom_text,
            lifecycle_text,
            protected_text,
            load_before_text,
            load_after_text,
            mem_before_text,
            mem_after_text,
            source_kind,
            remote_raw_text,
        ) = fields
        try:
            exit_code = int(exit_text)
            load_before = float(load_before_text)
            load_after = float(load_after_text)
            mem_before = int(mem_before_text)
            mem_after = int(mem_after_text)
        except ValueError:
            protocol_valid = False
            break
        expected = expected_arms[len(arms)] if len(arms) < len(expected_arms) else None
        if (
            expected != (pair_id, profile, implementation)
            or re.fullmatch(r"[a-z]+", container_status) is None
            or not 0 <= exit_code <= 255
            or oom_text not in {"true", "false"}
            or lifecycle_text not in {"true", "false"}
            or protected_text not in {"true", "false"}
            or remote_raw_text not in {"true", "false"}
            or TOKEN_RE.fullmatch(source_kind) is None
            or not all(_number(value) for value in (load_before, load_after, mem_before, mem_after))
        ):
            protocol_valid = False
            break
        try:
            raw = json.loads(lines[index + 1], parse_constant=_reject_constant)
        except (json.JSONDecodeError, ValueError):
            raw = None
        identity = args.go_identity if implementation == "go" else args.python_identity
        sanitized = None
        if remote_raw_text == "true":
            sanitized = sanitize_raw_report(
                raw,
                implementation=implementation,
                profile=profile,
                source_commit=args.source_commit,
                manifest_sha256=args.manifest_sha256,
                image_identity=identity,
                expected_ids=expected_ids_set,
            )
        raw_valid = sanitized is not None
        if sanitized is None:
            sanitized = {
                "schema_version": 1,
                "implementation": implementation,
                "profile": profile,
                "status": "failed",
                "error_kind": "raw_report_invalid",
                "rounds": [],
                "final": None,
            }
        sanitized.update(
            {
                "pair_id": pair_id,
                "image_digest": args.go_digest if implementation == "go" else args.python_digest,
                "raw_report_valid": raw_valid,
                "source_failure_kind": source_kind,
                "container_lifecycle": {
                    "status": container_status,
                    "exit_code": exit_code,
                    "oom_killed": oom_text == "true",
                    "valid": lifecycle_text == "true",
                },
                "protected_services_unchanged": protected_text == "true",
                "host": {
                    "load1_before": load_before,
                    "load1_after": load_after,
                    "mem_available_kib_before": mem_before,
                    "mem_available_kib_after": mem_after,
                },
            }
        )
        arms.append(sanitized)
        index += 2

    expected_count = 2 if args.remote_status == 72 else 60 if args.remote_status == 0 else len(arms)
    full_run_structural = bool(
        protocol_valid
        and meta is not None
        and args.cleanup_verified
        and len(arms) == expected_count
        and all(arm["raw_report_valid"] for arm in arms)
        and (postflight_valid or args.remote_status not in {0, 72, 73})
    )
    protected_digest_match = bool(
        protected_baseline_sha256
        and args.protected_postflight_sha256
        and protected_baseline_sha256 == args.protected_postflight_sha256
    )
    preflight_failure_structural = bool(
        protocol_valid
        and meta is None
        and args.remote_status in {70, 71}
        and args.cleanup_verified
        and protected_digest_match
        and preflight_failure_stage is not None
        and not arms
        and index == len(lines)
    )
    structural = full_run_structural or preflight_failure_structural
    all_successful = len(arms) == 60 and all(_successful_arm(arm, expected_ids) for arm in arms)
    correctness = all_successful
    parity: dict[tuple[str, int, str], list[tuple[int, str]]] = {}
    byte_values: dict[tuple[str, int], dict[str, int]] = {}
    if all_successful:
        for arm in arms:
            for round_report in arm["rounds"]:
                pair_round = (arm["pair_id"], round_report["round"])
                byte_values.setdefault(pair_round, {})[arm["implementation"]] = round_report[
                    "decoded_bytes"
                ]
                for job in round_report["jobs"]:
                    key = (*pair_round, job["id"])
                    parity.setdefault(key, []).append(
                        (job["canonical_url_count"], job["canonical_url_sha256"])
                    )
        correctness = len(parity) == 30 * 2 * 32 and all(
            len(values) == 2 and values[0] == values[1] for values in parity.values()
        )
    byte_deltas: list[dict[str, Any]] = []
    if all_successful:
        for (pair_id, round_number), values in sorted(byte_values.items()):
            go_bytes = values.get("go", 0)
            python_bytes = values.get("python", 0)
            absolute = abs(go_bytes - python_bytes)
            relative = (
                absolute / max(go_bytes, python_bytes) if max(go_bytes, python_bytes) else 0.0
            )
            byte_deltas.append(
                {
                    "pair_id": pair_id,
                    "round": round_number,
                    "go_decoded_bytes": go_bytes,
                    "python_decoded_bytes": python_bytes,
                    "absolute_delta_bytes": absolute,
                    "relative_delta": relative,
                }
            )
    timing_comparable = bool(
        all_successful
        and correctness
        and len(byte_deltas) == 60
        and all(item["relative_delta"] <= BYTE_IMBALANCE_LIMIT for item in byte_deltas)
    )

    safety_event = (
        args.remote_status == 70
        or not args.cleanup_verified
        or not protected_digest_match
        or any(not arm["protected_services_unchanged"] for arm in arms)
    )
    if safety_event:
        status, error_kind = "aborted", "production_safety_violation"
    elif not structural:
        status, error_kind = "failed", "report_transport_invalid"
    elif args.remote_status == 73:
        policy_kinds = {arm["source_failure_kind"] for arm in arms}
        error_kind = (
            "egress_policy_denial"
            if "egress_policy_denial" in policy_kinds
            else "source_policy_denial"
        )
        status = "failed"
    elif args.remote_status == 72:
        status, error_kind = "failed", "cohort_admission_failed"
    elif args.remote_status != 0:
        status, error_kind = "failed", "benchmark_infrastructure_failure"
    elif not all_successful:
        status, error_kind = "failed", "benchmark_or_source_failure"
    elif not correctness:
        status, error_kind = "failed", "correctness_parity_mismatch"
    elif not timing_comparable:
        status, error_kind = "inconclusive", "inconclusive_workload_imbalance"
    else:
        status, error_kind = "succeeded", None

    output: dict[str, Any] = {
        "schema_version": 1,
        "source_commit": args.source_commit,
        "manifest_sha256": args.manifest_sha256,
        "schedule_sha256": args.schedule_sha256,
        "go_image_digest": args.go_digest,
        "python_image_digest": args.python_digest,
        "pinset_sha256": meta["pinset_sha256"] if meta else None,
        "protected_baseline_sha256": protected_baseline_sha256,
        "preflight_failure_stage": preflight_failure_stage,
        "cooldown_seconds": args.cooldown,
        "protocol_valid": protocol_valid,
        "postflight_valid": postflight_valid,
        "cleanup_verified": args.cleanup_verified,
        "host_preflight": meta,
        "status": status,
        "report_valid": structural,
        "timing_comparable": timing_comparable,
        "byte_imbalance_threshold": BYTE_IMBALANCE_LIMIT,
        "byte_deltas": byte_deltas,
        "arms": arms,
    }
    if error_kind is not None:
        output["error_kind"] = error_kind
    return output


def _sha256(value: str, label: str, *, prefixed: bool = False) -> str:
    pattern = r"^sha256:[0-9a-f]{64}$" if prefixed else r"^[0-9a-f]{64}$"
    if re.fullmatch(pattern, value) is None:
        raise argparse.ArgumentTypeError(f"invalid {label}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sanitize a fleet benchmark wire report")
    parser.add_argument("--wire", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fleet", type=Path, required=True)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--remote-status", type=int, required=True)
    parser.add_argument("--cleanup-verified", choices=("true", "false"), required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--schedule-sha256", required=True)
    parser.add_argument("--go-identity", required=True)
    parser.add_argument("--python-identity", required=True)
    parser.add_argument("--go-digest", required=True)
    parser.add_argument("--python-digest", required=True)
    parser.add_argument("--protected-postflight-sha256", required=True)
    parser.add_argument("--cooldown", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if re.fullmatch(r"^[0-9a-f]{40}$", args.source_commit) is None:
        raise SystemExit("invalid source commit")
    for value, label in (
        (args.manifest_sha256, "manifest digest"),
        (args.schedule_sha256, "schedule digest"),
    ):
        _sha256(value, label)
    if args.cleanup_verified == "true" or args.protected_postflight_sha256:
        _sha256(args.protected_postflight_sha256, "protected postflight digest")
    _sha256(args.go_digest, "Go image digest", prefixed=True)
    _sha256(args.python_digest, "Python image digest", prefixed=True)
    args.cleanup_verified = args.cleanup_verified == "true"
    report = parse_report(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{args.out.name}.", dir=args.out.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, sort_keys=True, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, args.out)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
