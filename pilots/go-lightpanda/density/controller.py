#!/usr/bin/env python3
"""Hermetic two-arm density smoke controller.

The controller deliberately talks to Docker only through the CLI.  Containers
are created, inspected while stopped, and started only after their isolation
contract has been attested.  Raw container output is held in mode-0600
temporary files and is never copied to the final report or exception text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import subprocess
import tempfile
import threading
import time
from typing import Any


GIB = 1024**3
FIXTURE_MEMORY = 256 * 1024**2
FIXTURE_PIDS = 64
MEASURED_PIDS = 512
FIXTURE_TMPFS = "rw,noexec,nosuid,nodev,size=16777216,uid=10001,gid=10001,mode=0700"
MEASURED_TMPFS = "rw,noexec,nosuid,nodev,size=268435456,uid=10001,gid=10001,mode=0700"
START_GATE = "/tmp/controller-start"
ALIASES = tuple(f"origin-{i}.bench.test" for i in range(8))
LABEL_RUN = "org.jobseek.density.run"
LABEL_ROLE = "org.jobseek.density.role"
MAX_REPORT_BYTES = 1024 * 1024
CGROUP_FILES = (
    "memory.current", "memory.peak", "memory.max", "memory.swap.max",
    "memory.events", "cpu.max", "cpu.stat", "pids.max", "pids.events",
    "pids.peak", "cgroup.procs",
)
CGROUP_FILE_FAILURES = {
    name: "missing_cgroup_" + name.replace(".", "_") for name in CGROUP_FILES
}
GO_RUNNER_FAILURES = {
    name: "go_runner_" + name for name in (
        "arguments_invalid", "start_gate", "manifest_invalid", "adapter_initialization",
        "configuration_invalid", "pool_initialization", "invariant_failure",
    )
}
FAILURE_IDS = {
    "cleanup", "command", "concurrency", "conservation", "image", "inspect",
    "malformed_output", "missing_cgroup", "nonzero", "oom", "oracle",
    "missing_cgroup_membership", "missing_cgroup_path", "missing_cgroup_pid",
    "start_timeout", "timeout", "transcript", *CGROUP_FILE_FAILURES.values(),
    *GO_RUNNER_FAILURES.values(),
}

COMMON_KEYS = {
    "schema_version", "workload_sha256", "source_commit",
    "image_identity", "concurrency",
}
GO_KEYS = COMMON_KEYS | {
    "implementation_id", "runtime_id", "succeeded", "elapsed_ms", "submitted",
    "accepted", "terminal", "succeeded_jobs", "failed_jobs",
    "oracle_matches", "conservation_ok", "oracle_ok", "max_in_flight_ok",
    "zero_panic_residue", "waves", "pool", "failure_id",
}
GO_STATS_KEYS = {
    "accepted", "completed", "panics", "queued", "in_flight", "max_queued",
    "max_in_flight", "reserved_port_count",
}
GO_WAVE_KEYS = {"id", "succeeded", "submitted", "terminal", "succeeded_jobs", "failed_jobs", "oracle_matches", "max_in_flight", "jobs"}
GO_JOB_KEYS = {
    "id", "mode_id", "terminal", "succeeded", "oracle_match", "failure_id",
    "final_url_match", "status", "status_match", "html_bytes", "html_sha256",
    "html_match", "evaluation_present", "evaluation_bytes", "evaluation_sha256",
    "evaluation_match",
}
PY_KEYS = COMMON_KEYS | {
    "implementation", "ok", "accepted", "completed", "succeeded", "max_in_flight",
    "elapsed_ns", "driver_teardown_ok", "conservation", "tasks",
}
PY_CONSERVATION_KEYS = {"accepted", "completed", "succeeded", "failed", "queued", "in_flight"}
PY_TASK_KEYS = {
    "id", "mode", "outcome", "status_matches", "final_url_matches", "html_matches",
    "html_bytes", "html_sha256", "evaluation_present", "evaluation_matches",
    "evaluation_bytes", "evaluation_sha256", "cleanup_ok",
}
FIXTURE_KEYS = {
    "schema_version", "workload_sha256", "concurrency", "completed",
    "max_global_in_flight", "max_per_origin_in_flight", "waves", "unexpected",
}
FIXTURE_WAVE_KEYS = {"id", "completed", "task_ids", "response_sha256", "barrier_reached"}
FIXTURE_UNEXPECTED_KEYS = {"method", "path", "origin", "duplicate", "total"}


class SmokeFailure(RuntimeError):
    def __init__(self, failure_id: str):
        if failure_id not in FAILURE_IDS:
            failure_id = "command"
        self.failure_id = failure_id
        super().__init__(failure_id)


def _object(value: Any, keys: set[str], required: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - keys or not (required or keys) <= set(value):
        raise SmokeFailure("malformed_output")
    return value


def _integer(value: Any, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SmokeFailure("malformed_output")
    return value


def _digest(value: Any, *, prefixed: bool = False) -> str:
    pattern = r"sha256:[0-9a-f]{64}" if prefixed else r"[0-9a-f]{64}"
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise SmokeFailure("malformed_output")
    return value


def parse_json_object(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_REPORT_BYTES:
        raise SmokeFailure("malformed_output")
    try:
        text = raw.decode("utf-8", "strict")
        decoder = json.JSONDecoder(parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        value, end = decoder.raw_decode(text.lstrip())
        if text.lstrip()[end:].strip():
            raise ValueError
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise SmokeFailure("malformed_output") from None
    if not isinstance(value, dict):
        raise SmokeFailure("malformed_output")
    return value


def parse_fixture_output(raw: bytes) -> dict[str, Any]:
    prefix = b"DENSITY_FIXTURE_REPORT="
    lines = raw.splitlines()
    reports = [line[len(prefix):] for line in lines if line.startswith(prefix)]
    if len(reports) != 1 or any(line.strip() and not line.startswith(prefix) for line in lines):
        raise SmokeFailure("transcript")
    try:
        return parse_json_object(reports[0])
    except SmokeFailure:
        raise SmokeFailure("transcript") from None


def load_manifest(base: Path) -> tuple[dict[str, Any], str]:
    workload_raw = (base / "workload.v1.json").read_bytes()
    workload = parse_json_object(workload_raw)
    return workload, hashlib.sha256(workload_raw).hexdigest()


def expected_tasks(workload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    waves = workload.get("waves")
    if not isinstance(waves, list) or len(waves) != 2:
        raise SmokeFailure("malformed_output")
    result: dict[str, dict[str, Any]] = {}
    for wave in waves:
        if not isinstance(wave, dict) or wave.get("id") not in {"w0", "w1"}:
            raise SmokeFailure("malformed_output")
        tasks = wave.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 8:
            raise SmokeFailure("malformed_output")
        for task in tasks:
            if not isinstance(task, dict) or not isinstance(task.get("id"), str) or task["id"] in result:
                raise SmokeFailure("malformed_output")
            result[task["id"]] = task
    return result


def _common_report(report: dict[str, Any], concurrency: int, source: str, identity: str,
                   workload_sha: str) -> None:
    if report.get("schema_version") != 1 or report.get("concurrency") != concurrency:
        raise SmokeFailure("concurrency")
    if report.get("source_commit") != source or report.get("image_identity") != identity:
        raise SmokeFailure("malformed_output")
    if report.get("workload_sha256") != workload_sha:
        raise SmokeFailure("oracle")


def validate_measured(report: dict[str, Any], implementation: str, concurrency: int,
                      source: str, identity: str, workload_sha: str,
                      tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    keys = GO_KEYS if implementation == "go" else PY_KEYS
    _object(report, keys, keys - ({"failure_id"} if implementation == "go" else set()))
    _common_report(report, concurrency, source, identity, workload_sha)
    expected_ids = set(tasks)
    if implementation == "go":
        if (report.get("implementation_id") != "go-lightpanda" or report.get("succeeded") is not True or
                not isinstance(report.get("runtime_id"), str) or
                re.fullmatch(r"go1\.[0-9]+(?:\.[0-9]+)?(?:[a-z0-9.-]+)?", report["runtime_id"]) is None or
                report.get("failure_id") not in (None, "")):
            raise SmokeFailure("oracle")
        _integer(report.get("elapsed_ms"))
        stats = _object(report.get("pool"), GO_STATS_KEYS)
        if any(report.get(k) != v for k, v in {
            "submitted": 16, "accepted": 16, "terminal": 16, "succeeded_jobs": 16,
            "failed_jobs": 0, "oracle_matches": 16, "conservation_ok": True,
            "oracle_ok": True, "max_in_flight_ok": True, "zero_panic_residue": True,
        }.items()):
            raise SmokeFailure("conservation")
        if any(_integer(stats[k]) != v for k, v in {
            "accepted": 16, "completed": 16, "panics": 0, "queued": 0,
            "in_flight": 0, "reserved_port_count": 0,
        }.items()):
            raise SmokeFailure("conservation")
        for value in stats.values():
            _integer(value)
        if _integer(stats.get("max_in_flight")) != concurrency:
            raise SmokeFailure("concurrency")
        waves = report.get("waves")
        if not isinstance(waves, list) or len(waves) != 2:
            raise SmokeFailure("conservation")
        seen: set[str] = set()
        for wave_index, wave in enumerate(waves):
            wave = _object(wave, GO_WAVE_KEYS)
            expected_wave = ("w0", "w1")[wave_index]
            if wave.get("id") != expected_wave or wave.get("succeeded") is not True:
                raise SmokeFailure("oracle")
            if [wave.get(k) for k in ("submitted", "terminal", "succeeded_jobs", "failed_jobs", "oracle_matches")] != [8, 8, 8, 0, 8]:
                raise SmokeFailure("conservation")
            if wave.get("max_in_flight") != concurrency:
                raise SmokeFailure("concurrency")
            jobs = wave.get("jobs")
            if not isinstance(jobs, list) or len(jobs) != 8:
                raise SmokeFailure("conservation")
            expected_wave_ids = [task["id"] for task in next(item for item in workload_waves(tasks) if item["id"] == expected_wave)["tasks"]]
            if [job.get("id") for job in jobs if isinstance(job, dict)] != expected_wave_ids:
                raise SmokeFailure("conservation")
            for job in jobs:
                job = _object(job, GO_JOB_KEYS, GO_JOB_KEYS - {"failure_id", "evaluation_sha256"})
                for key in ("status", "html_bytes", "evaluation_bytes"):
                    _integer(job.get(key))
                task = tasks.get(job.get("id"))
                if task is None or job["id"] in seen:
                    raise SmokeFailure("conservation")
                seen.add(job["id"])
                if (job.get("terminal") is not True or job.get("succeeded") is not True or job.get("oracle_match") is not True or
                        job.get("mode_id") != task.get("mode") or job.get("status") != task.get("status") or
                        any(job.get(k) is not True for k in ("final_url_match", "status_match", "html_match", "evaluation_match")) or
                        job.get("html_bytes") != task.get("expected_outer_html_size") or
                        job.get("html_sha256") != task.get("expected_outer_html_sha256") or
                        job.get("evaluation_present") is not (task.get("expected_evaluation_sha256") is not None) or
                        job.get("evaluation_sha256") != task.get("expected_evaluation_sha256") or
                        job.get("evaluation_bytes") != (
                            len(task["expected_evaluation_json"].encode("ascii"))
                            if task.get("expected_evaluation_json") is not None else 0
                        ) or job.get("failure_id") not in (None, "")):
                    raise SmokeFailure("oracle")
        if seen != expected_ids:
            raise SmokeFailure("conservation")
    else:
        if report.get("implementation") != "python-chromium" or report.get("ok") is not True or report.get("driver_teardown_ok") is not True:
            raise SmokeFailure("oracle")
        cons = _object(report.get("conservation"), PY_CONSERVATION_KEYS)
        _integer(report.get("elapsed_ns"))
        if [report.get(k) for k in ("accepted", "completed", "succeeded")] != [16, 16, 16]:
            raise SmokeFailure("conservation")
        if [cons.get(k) for k in ("accepted", "completed", "succeeded", "failed", "queued", "in_flight")] != [16, 16, 16, 0, 0, 0]:
            raise SmokeFailure("conservation")
        if report.get("max_in_flight") != concurrency:
            raise SmokeFailure("concurrency")
        entries = report.get("tasks")
        if not isinstance(entries, list) or len(entries) != 16:
            raise SmokeFailure("conservation")
        if [entry.get("id") for entry in entries if isinstance(entry, dict)] != list(tasks):
            raise SmokeFailure("conservation")
        seen = set()
        for entry in entries:
            entry = _object(entry, PY_TASK_KEYS)
            for key in ("html_bytes", "evaluation_bytes"):
                _integer(entry.get(key))
            task = tasks.get(entry.get("id"))
            if task is None or entry["id"] in seen:
                raise SmokeFailure("conservation")
            seen.add(entry["id"])
            if (entry.get("mode") != task.get("mode") or entry.get("outcome") != "success" or
                    any(entry.get(k) is not True for k in ("status_matches", "final_url_matches", "html_matches", "evaluation_matches", "cleanup_ok")) or
                    entry.get("html_bytes") != task.get("expected_outer_html_size") or
                    entry.get("html_sha256") != task.get("expected_outer_html_sha256") or
                    entry.get("evaluation_present") is not (task.get("expected_evaluation_sha256") is not None) or
                    entry.get("evaluation_sha256") != task.get("expected_evaluation_sha256") or
                    entry.get("evaluation_bytes") != (
                        len(task["expected_evaluation_json"].encode("ascii"))
                        if task.get("expected_evaluation_json") is not None else 0
                    )):
                raise SmokeFailure("oracle")
        if seen != expected_ids:
            raise SmokeFailure("conservation")
    return report


def validate_fixture(report: dict[str, Any], concurrency: int, workload_sha: str,
                     tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    _object(report, FIXTURE_KEYS)
    if report.get("schema_version") != 1 or report.get("workload_sha256") != workload_sha:
        raise SmokeFailure("transcript")
    if report.get("concurrency") != concurrency or report.get("max_global_in_flight") != concurrency:
        raise SmokeFailure("concurrency")
    if report.get("completed") != 16 or report.get("max_per_origin_in_flight") != 1:
        raise SmokeFailure("conservation")
    unexpected = _object(report.get("unexpected"), FIXTURE_UNEXPECTED_KEYS)
    if any(_integer(unexpected.get(key)) != 0 for key in FIXTURE_UNEXPECTED_KEYS):
        raise SmokeFailure("transcript")
    waves = report.get("waves")
    if not isinstance(waves, list) or len(waves) != 2:
        raise SmokeFailure("transcript")
    seen: list[str] = []
    grouped = workload_waves(tasks)
    for wave_index, wave in enumerate(waves):
        wave = _object(wave, FIXTURE_WAVE_KEYS)
        ids, hashes = wave.get("task_ids"), wave.get("response_sha256")
        expected_wave = grouped[wave_index]
        if wave.get("id") != expected_wave["id"] or wave.get("completed") != 8 or wave.get("barrier_reached") is not True:
            raise SmokeFailure("transcript")
        if not isinstance(ids, list) or not isinstance(hashes, list) or len(ids) != 8 or len(hashes) != 8:
            raise SmokeFailure("transcript")
        if ids != [task["id"] for task in expected_wave["tasks"]]:
            raise SmokeFailure("transcript")
        for task_id, digest in zip(ids, hashes, strict=True):
            task = tasks.get(task_id)
            if task is None or digest != hashlib.sha256(task["response_body"].encode()).hexdigest():
                raise SmokeFailure("oracle")
            seen.append(task_id)
    if set(seen) != set(tasks) or len(seen) != 16:
        raise SmokeFailure("conservation")
    return report


def workload_waves(tasks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {"w0": [], "w1": []}
    for task in tasks.values():
        wave = task["id"].split("-", 1)[0]
        if wave not in grouped:
            raise SmokeFailure("malformed_output")
        grouped[wave].append(task)
    return [{"id": wave, "tasks": grouped[wave]} for wave in ("w0", "w1")]


def validate_resources(resources: dict[str, Any]) -> None:
    current = _integer(resources.get("memory_current"))
    peak = _integer(resources.get("memory_peak"), minimum=1)
    pids_peak = _integer(resources.get("pids_peak"), minimum=1)
    events = _object(resources.get("memory_events"), {"low", "high", "max", "oom", "oom_kill", "oom_group_kill"})
    cpu = resources.get("cpu_stat")
    if (resources.get("memory_limit") != GIB or resources.get("memory_swap_limit") != 0 or
            resources.get("pids_limit") != MEASURED_PIDS):
        raise SmokeFailure("inspect")
    cpu_max = resources.get("cpu_max")
    if (not isinstance(cpu_max, list) or len(cpu_max) != 2 or
            any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in cpu_max) or
            cpu_max[0] != cpu_max[1]):
        raise SmokeFailure("inspect")
    if current > GIB or peak > GIB or pids_peak > MEASURED_PIDS:
        raise SmokeFailure("inspect")
    if not isinstance(cpu, dict):
        raise SmokeFailure("missing_cgroup")
    for key in ("usage_usec", "user_usec", "system_usec"):
        _integer(cpu.get(key))
    for value in events.values():
        _integer(value)
    pids_events = _object(resources.get("pids_events"), {"max"})
    if (events["oom"] or events["oom_kill"] or events["oom_group_kill"] or
            _integer(pids_events.get("max")) != 0):
        raise SmokeFailure("oom")


class Docker:
    def __init__(self, binary: str = "docker") -> None:
        self.binary = binary

    def run(self, args: list[str], timeout: float = 15.0, *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run([self.binary, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise SmokeFailure("command") from None
        if len(result.stdout) + len(result.stderr) > MAX_REPORT_BYTES:
            raise SmokeFailure("command")
        if check and result.returncode != 0:
            raise SmokeFailure("command")
        return result

    def json(self, args: list[str], timeout: float = 15.0) -> Any:
        try:
            return json.loads(self.run(args, timeout).stdout)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            raise SmokeFailure("command") from None

    def logs(self, container: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="density-raw-") as directory:
            stdout_path, stderr_path = Path(directory) / "stdout", Path(directory) / "stderr"
            for path in (stdout_path, stderr_path):
                path.touch(mode=0o600)
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                try:
                    process = subprocess.Popen(
                        [self.binary, "logs", container], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                except OSError:
                    raise SmokeFailure("command") from None
                assert process.stdout is not None and process.stderr is not None
                streams = selectors.DefaultSelector()
                streams.register(process.stdout, selectors.EVENT_READ, out)
                streams.register(process.stderr, selectors.EVENT_READ, err)
                total = 0
                deadline = time.monotonic() + 15
                try:
                    while streams.get_map():
                        if time.monotonic() >= deadline:
                            raise SmokeFailure("command")
                        for key, _mask in streams.select(timeout=0.1):
                            chunk = key.fileobj.read1(min(65536, MAX_REPORT_BYTES - total + 1))
                            if not chunk:
                                streams.unregister(key.fileobj)
                                continue
                            total += len(chunk)
                            if total > MAX_REPORT_BYTES:
                                raise SmokeFailure("malformed_output")
                            key.data.write(chunk)
                    returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except (OSError, subprocess.TimeoutExpired, SmokeFailure) as error:
                    process.kill()
                    process.wait(timeout=2)
                    if isinstance(error, SmokeFailure):
                        raise
                    raise SmokeFailure("command") from None
                finally:
                    streams.close()
                    process.stdout.close()
                    process.stderr.close()
            if returncode != 0 or stderr_path.stat().st_size != 0:
                raise SmokeFailure("malformed_output")
            return stdout_path.read_bytes()


def attest_container(inspect: dict[str, Any], network: str, role: str, run_id: str,
                     image_identity: str, *, measured: bool) -> None:
    try:
        config, host = inspect["Config"], inspect["HostConfig"]
        labels = config["Labels"]
        state = inspect["State"]
        if (state["Status"] != "created" or state["Running"] is not False or
                state["Restarting"] is not False or state["Dead"] is not False or
                state["Pid"] != 0):
            raise KeyError
        if labels.get(LABEL_RUN) != run_id or labels.get(LABEL_ROLE) != role:
            raise KeyError
        if (inspect.get("Image") != image_identity or config.get("Image") != image_identity or
                config.get("User") != "10001:10001" or host.get("NetworkMode") != network):
            raise KeyError
        if not host.get("ReadonlyRootfs") or set(host.get("CapDrop") or ()) != {"ALL"}:
            raise KeyError
        if host.get("SecurityOpt") != ["no-new-privileges=true"]:
            raise KeyError
        if (host.get("Privileged") is not False or host.get("CapAdd") or host.get("Devices") or
                host.get("DeviceRequests") or host.get("PidMode") != "" or
                host.get("IpcMode") not in ("", "private") or host.get("UsernsMode") != "" or
                host.get("RestartPolicy", {}).get("Name") != "no" or host.get("Binds") or
                inspect.get("Mounts")):
            raise KeyError
        if (config.get("Volumes") or config.get("ExposedPorts") or host.get("PortBindings") or
                host.get("PublishAllPorts") is not False or
                inspect.get("NetworkSettings", {}).get("Ports") or
                host.get("Tmpfs") != {"/tmp": MEASURED_TMPFS if measured else FIXTURE_TMPFS}):
            raise KeyError
        aliases = inspect["NetworkSettings"]["Networks"][network].get("Aliases") or []
        if role == "fixture" and aliases != list(ALIASES):
            raise KeyError
        if role != "fixture" and aliases:
            raise KeyError
        expected_memory, expected_pids = (GIB, MEASURED_PIDS) if measured else (FIXTURE_MEMORY, FIXTURE_PIDS)
        if (host.get("NanoCpus") != 1_000_000_000 or host.get("Memory") != expected_memory or
                host.get("MemorySwap") != expected_memory or host.get("PidsLimit") != expected_pids):
            raise KeyError
        limits = {item.get("Name"): (item.get("Soft"), item.get("Hard")) for item in host.get("Ulimits") or []}
        if limits != {"nofile": (256, 256)}:
            raise KeyError
        homes = [item.split("=", 1)[1] for item in config.get("Env") or [] if item.startswith("HOME=")]
        if homes != ["/tmp"]:
            raise KeyError
    except (KeyError, TypeError, AttributeError):
        raise SmokeFailure("inspect") from None


def inspect_exit(inspect: dict[str, Any]) -> None:
    try:
        state = inspect["State"]
        if state["OOMKilled"] is True:
            raise SmokeFailure("oom")
        if (state["Status"] != "exited" or state["Running"] is not False or
                state["Restarting"] is not False or state["Dead"] is not False or
                state["Pid"] != 0):
            raise SmokeFailure("inspect")
        if isinstance(state["ExitCode"], bool) or state["ExitCode"] != 0:
            raise SmokeFailure("nonzero")
    except SmokeFailure:
        raise
    except (KeyError, TypeError, AttributeError):
        raise SmokeFailure("inspect") from None


def inspect_measured_exit(docker: Docker, container: str, implementation: str,
                          inspect: dict[str, Any]) -> None:
    try:
        inspect_exit(inspect)
    except SmokeFailure as error:
        if error.failure_id != "nonzero" or implementation != "go":
            raise
        try:
            report = parse_json_object(docker.logs(container))
        except SmokeFailure:
            raise error from None
        runner_failure = report.get("failure_id")
        mapped = GO_RUNNER_FAILURES.get(runner_failure) if isinstance(runner_failure, str) else None
        if mapped is None:
            raise error
        raise SmokeFailure(mapped) from None


def attest_network(data: Any, run_id: str) -> None:
    try:
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise KeyError
        labels = data[0]["Labels"]
        if (not isinstance(labels, dict) or data[0]["Internal"] is not True or
                labels.get(LABEL_RUN) != run_id or labels.get(LABEL_ROLE) != "network"):
            raise KeyError
    except (KeyError, TypeError, AttributeError):
        raise SmokeFailure("inspect") from None


def _parse_flat(raw: str, required: set[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if (len(parts) != 2 or parts[0] in result or
                re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", parts[0]) is None or
                not parts[1].isdigit()):
            raise ValueError
        result[parts[0]] = int(parts[1])
    if not required <= set(result):
        raise ValueError
    return result


class CgroupSampler:
    """Samples only aggregate cgroup counters; PIDs and argv never leave it."""
    def __init__(self, path: Path, interval: float = 0.1) -> None:
        self.path, self.interval = path, interval
        self.latest: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    @classmethod
    def from_pid(cls, pid: int) -> "CgroupSampler":
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise SmokeFailure("missing_cgroup_pid")
        try:
            lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
        except (OSError, UnicodeError):
            raise SmokeFailure("missing_cgroup_membership") from None
        try:
            relative = next(line.split("::", 1)[1] for line in lines if line.startswith("0::"))
            root = Path("/sys/fs/cgroup").resolve()
            path = (root / relative.lstrip("/")).resolve()
            path.relative_to(root)
        except (OSError, StopIteration, ValueError, IndexError):
            raise SmokeFailure("missing_cgroup_path") from None
        for name in CGROUP_FILES:
            if not (path / name).is_file():
                raise SmokeFailure(CGROUP_FILE_FAILURES[name])
        return cls(path)

    def start(self) -> None:
        self._sample()
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            raise SmokeFailure("missing_cgroup")
        try:
            result = self._read_resources()
        except (OSError, ValueError):
            result = self.latest
        if result is None:
            raise SmokeFailure("missing_cgroup")
        if result["memory_events"]["oom"] or result["memory_events"]["oom_kill"] or result["memory_events"]["oom_group_kill"]:
            raise SmokeFailure("oom")
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.interval)

    def _sample(self) -> None:
        try:
            self.latest = self._read_resources()
        except (OSError, ValueError):
            pass

    def _read_resources(self) -> dict[str, Any]:
        event_keys = {"low", "high", "max", "oom", "oom_kill", "oom_group_kill"}
        events = _parse_flat((self.path / "memory.events").read_text(), event_keys)
        pids_events = _parse_flat((self.path / "pids.events").read_text(), {"max"})
        cpu = _parse_flat((self.path / "cpu.stat").read_text(), {"usage_usec", "user_usec", "system_usec"})
        memory_limit = (self.path / "memory.max").read_text().strip()
        memory_swap_limit = (self.path / "memory.swap.max").read_text().strip()
        pids_limit = (self.path / "pids.max").read_text().strip()
        cpu_parts = (self.path / "cpu.max").read_text().split()
        if (memory_limit == "max" or memory_swap_limit == "max" or pids_limit == "max" or
                len(cpu_parts) != 2 or "max" in cpu_parts):
            raise ValueError
        return {
            "memory_current": int((self.path / "memory.current").read_text()),
            "memory_peak": int((self.path / "memory.peak").read_text()),
            "memory_limit": int(memory_limit),
            "memory_swap_limit": int(memory_swap_limit),
            "memory_events": {key: events[key] for key in event_keys},
            "cpu_max": [int(cpu_parts[0]), int(cpu_parts[1])],
            "cpu_stat": {key: cpu[key] for key in (
                "usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled",
                "throttled_usec", "nr_bursts", "burst_usec",
            ) if key in cpu},
            "pids_limit": int(pids_limit),
            "pids_events": {"max": pids_events["max"]},
            "pids_peak": int((self.path / "pids.peak").read_text()),
        }


class SmokeController:
    def __init__(self, docker: Docker, source: str, concurrency: int, timeout: float,
                 workload: dict[str, Any], workload_sha: str,
                 run_id: str | None = None) -> None:
        self.docker, self.source, self.concurrency, self.timeout = docker, source, concurrency, timeout
        self.workload, self.workload_sha = workload, workload_sha
        self.tasks = expected_tasks(workload)
        self.run_id = run_id or secrets.token_hex(8)

    def image_identity(self, image: str) -> str:
        try:
            data = self.docker.json(["image", "inspect", image])
            if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
                raise SmokeFailure("image")
            identity = data[0]["Id"]
            _digest(identity, prefixed=True)
        except (SmokeFailure, KeyError, IndexError, TypeError):
            raise SmokeFailure("image") from None
        return identity

    def _create(self, args: list[str]) -> str:
        try:
            raw = self.docker.run(["create", *args]).stdout.strip().decode("ascii", "strict")
        except (SmokeFailure, UnicodeDecodeError):
            raise SmokeFailure("command") from None
        if re.fullmatch(r"[0-9a-f]{64}", raw) is None:
            raise SmokeFailure("command")
        return raw

    def _wait(self, container: str, timeout: float) -> None:
        try:
            result = self.docker.run(["wait", container], timeout=timeout, check=False)
        except SmokeFailure:
            self.docker.run(["kill", container], timeout=10, check=False)
            raise SmokeFailure("timeout") from None
        try:
            waited_exit = int(result.stdout.decode("ascii", "strict").strip())
        except (UnicodeDecodeError, ValueError):
            raise SmokeFailure("command") from None
        if result.returncode != 0 or waited_exit < 0:
            raise SmokeFailure("command")

    def _start(self, container: str) -> None:
        try:
            self.docker.run(["start", container], timeout=10)
        except SmokeFailure:
            self.docker.run(["kill", container], timeout=10, check=False)
            raise SmokeFailure("start_timeout") from None

    def _release_start_gate(self, container: str) -> None:
        result = self.docker.run(["exec", container, "touch", START_GATE], timeout=5)
        if result.stdout or result.stderr:
            raise SmokeFailure("command")

    def _inspect_one(self, container: str) -> dict[str, Any]:
        try:
            data = self.docker.json(["inspect", container])
        except SmokeFailure:
            raise SmokeFailure("inspect") from None
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise SmokeFailure("inspect")
        return data[0]

    def _cleanup(self, network: str) -> None:
        failed = False
        result = self.docker.run(["ps", "-aq", "--no-trunc", "--filter", f"label={LABEL_RUN}={self.run_id}"], check=False)
        try:
            ids = result.stdout.decode("ascii", "strict").split()
        except UnicodeDecodeError:
            raise SmokeFailure("cleanup") from None
        if result.returncode != 0 or any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in ids):
            failed = True
            ids = []
        for container in ids:
            try:
                inspect = self._inspect_one(container)
                config = inspect.get("Config")
                if not isinstance(config, dict) or not isinstance(config.get("Labels"), dict):
                    raise SmokeFailure("cleanup")
                labels = config["Labels"]
                role = labels.get(LABEL_ROLE)
                if labels.get(LABEL_RUN) != self.run_id or role not in {"fixture", "go", "python"}:
                    failed = True
                    continue
                if self.docker.run(["rm", "-f", container], check=False).returncode != 0:
                    failed = True
            except SmokeFailure:
                failed = True
        try:
            network_result = self.docker.run(["network", "ls", "--format", "{{.Name}}", "--filter", f"label={LABEL_RUN}={self.run_id}"], check=False)
            networks = network_result.stdout.decode("ascii", "strict").split()
        except (SmokeFailure, UnicodeDecodeError):
            raise SmokeFailure("cleanup") from None
        if network_result.returncode != 0 or len(networks) != 1 or networks[0] != network:
            failed = True
        else:
            try:
                network_inspect = self.docker.json(["network", "inspect", network])
                if (not isinstance(network_inspect, list) or len(network_inspect) != 1 or
                        not isinstance(network_inspect[0], dict)):
                    raise SmokeFailure("cleanup")
                try:
                    attest_network(network_inspect, self.run_id)
                except SmokeFailure:
                    raise SmokeFailure("cleanup") from None
                if self.docker.run(["network", "rm", network], check=False).returncode != 0:
                    failed = True
            except (SmokeFailure, IndexError, TypeError):
                failed = True
        remaining_container_result = self.docker.run(["ps", "-aq", "--no-trunc", "--filter", f"label={LABEL_RUN}={self.run_id}"], check=False)
        remaining_network_result = self.docker.run(["network", "ls", "--format", "{{.Name}}", "--filter", f"label={LABEL_RUN}={self.run_id}"], check=False)
        if (remaining_container_result.returncode != 0 or remaining_network_result.returncode != 0 or
                remaining_container_result.stdout.strip() or remaining_network_result.stdout.strip()):
            failed = True
        if failed:
            raise SmokeFailure("cleanup")

    def run_arm(self, implementation: str, image: str, fixture_image: str) -> dict[str, Any]:
        network = f"density-{self.run_id}-{implementation}"
        identity = self.image_identity(image)
        fixture_identity = self.image_identity(fixture_image)
        fixture_id = measured_id = ""
        primary: SmokeFailure | None = None
        result: dict[str, Any] | None = None
        try:
            self.docker.run(["network", "create", "--internal", "--label", f"{LABEL_RUN}={self.run_id}", "--label", f"{LABEL_ROLE}=network", network])
            network_data = self.docker.json(["network", "inspect", network])
            attest_network(network_data, self.run_id)
            common = ["--user", "10001:10001", "--env", "HOME=/tmp", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true", "--restart", "no", "--network", network,
                      "--label", f"{LABEL_RUN}={self.run_id}"]
            fixture_args = [*common, "--label", f"{LABEL_ROLE}=fixture", "--cpus", "1", "--memory", "256m", "--memory-swap", "256m", "--pids-limit", str(FIXTURE_PIDS), "--ulimit", "nofile=256:256", "--tmpfs", f"/tmp:{FIXTURE_TMPFS}"]
            for alias in ALIASES:
                fixture_args.extend(["--network-alias", alias])
            fixture_args.extend([fixture_identity, "--workload", "/density/workload.v1.json", "--concurrency", str(self.concurrency)])
            fixture_id = self._create(fixture_args)
            attest_container(self._inspect_one(fixture_id), network, "fixture", self.run_id, fixture_identity, measured=False)
            self._start(fixture_id)
            ready_deadline = time.monotonic() + 20
            while self.docker.run(["exec", fixture_id, "test", "-f", "/tmp/ready"], timeout=2, check=False).returncode:
                if time.monotonic() >= ready_deadline:
                    raise SmokeFailure("start_timeout")
                time.sleep(0.05)
            measured_args = [*common, "--label", f"{LABEL_ROLE}={implementation}", "--cpus", "1", "--memory", "1g", "--memory-swap", "1g", "--pids-limit", str(MEASURED_PIDS), "--ulimit", "nofile=256:256", "--tmpfs", f"/tmp:{MEASURED_TMPFS}",
                             identity, "--workload", "/density/workload.v1.json", "--concurrency", str(self.concurrency), "--source-commit", self.source, "--image-identity", identity, "--start-gate", START_GATE]
            measured_id = self._create(measured_args)
            attest_container(self._inspect_one(measured_id), network, implementation, self.run_id, identity, measured=True)
            self._start(measured_id)
            running = self._inspect_one(measured_id)
            sampler = CgroupSampler.from_pid(running.get("State", {}).get("Pid", 0))
            sampler.start()
            try:
                self._release_start_gate(measured_id)
                self._wait(measured_id, self.timeout)
                resources = sampler.stop()
            except BaseException:
                try:
                    sampler.stop()
                except SmokeFailure:
                    pass
                raise
            inspect_measured_exit(self.docker, measured_id, implementation,
                                  self._inspect_one(measured_id))
            self._wait(fixture_id, 20)
            inspect_exit(self._inspect_one(fixture_id))
            measured = validate_measured(parse_json_object(self.docker.logs(measured_id)), implementation, self.concurrency, self.source, identity, self.workload_sha, self.tasks)
            fixture = validate_fixture(parse_fixture_output(self.docker.logs(fixture_id)), self.concurrency, self.workload_sha, self.tasks)
            validate_resources(resources)
            result = {"implementation": implementation, "image_identity": identity, "fixture_image_identity": fixture_identity, "measured": measured, "fixture": fixture, "resources": resources}
        except SmokeFailure as error:
            primary = error
        finally:
            try:
                self._cleanup(network)
            except SmokeFailure as cleanup_error:
                primary = cleanup_error
        if primary is not None:
            raise primary
        assert result is not None
        return result

    def run(self, go_image: str, python_image: str, fixture_image: str) -> dict[str, Any]:
        arms = [self.run_arm("go", go_image, fixture_image), self.run_arm("python", python_image, fixture_image)]
        return {"schema_version": 1, "ok": True, "source_commit": self.source, "mode": "implementation_smoke", "profile": f"c{self.concurrency}", "workload_sha256": self.workload_sha, "arms": arms}


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--go-image", required=True)
    parser.add_argument("--python-image", required=True)
    parser.add_argument("--fixture-image", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 4, 8), default=4)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if re.fullmatch(r"[0-9a-f]{40}", args.source_commit) is None or args.timeout_seconds <= 0:
        parser.error("source commit and timeout must be canonical")
    base = Path(__file__).resolve().parent
    try:
        workload, workload_sha = load_manifest(base)
        controller = SmokeController(Docker(), args.source_commit, args.concurrency, args.timeout_seconds, workload, workload_sha)
        report = controller.run(args.go_image, args.python_image, args.fixture_image)
    except (OSError, SmokeFailure) as error:
        failure_id = error.failure_id if isinstance(error, SmokeFailure) else "malformed_output"
        write_report(args.output, {"schema_version": 1, "ok": False, "failure_id": failure_id})
        return 1
    write_report(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
