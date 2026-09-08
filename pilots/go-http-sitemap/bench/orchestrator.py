#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import ipaddress
import json
import os
import platform
import queue
import random
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common import interval_max, job_manifest_sha256, load_corpus, percentile, scenario_map
from fixture import FixtureFleet

BENCH_ROOT = Path(__file__).resolve().parent
PILOT_ROOT = BENCH_ROOT.parent
PYTHON_SOURCE_FILES = (
    "src/core/monitors/sitemap.py",
    "src/shared/http_retry.py",
    "src/shared/tdm.py",
    "src/shared/constants.py",
    "src/metrics.py",
)
IMPLEMENTATIONS = ("go", "python")
EVIDENCE_ROOT = Path("/opt/jobseek-evidence")
EVIDENCE_SOURCE_ROOT = EVIDENCE_ROOT / "source"
EVIDENCE_PILOT_ROOT = EVIDENCE_SOURCE_ROOT / "pilots" / "go-http-sitemap"
EVIDENCE_PYTHON = Path("/opt/jobseek-bench-venv/bin/python")
EVIDENCE_GO = Path("/usr/local/go/bin/go")
EVIDENCE_GO_BINARY = EVIDENCE_ROOT / "go-runner"
EVIDENCE_PYTHON_ATTESTATION = EVIDENCE_ROOT / "python-runtime.json"
EVIDENCE_SOURCE_PROOF = Path("/source-proof")
EVIDENCE_OUTPUT_ROOT = Path("/evidence")
EVIDENCE_WRAPPER = EVIDENCE_PILOT_ROOT / "bench" / "evidence" / "enter-runner-cgroup.sh"
EVIDENCE_ENTRYPOINT = [str(EVIDENCE_PYTHON), str(EVIDENCE_PILOT_ROOT / "bench/orchestrator.py")]


def _run_text(command: list[str], cwd: Path) -> str:
    return subprocess.check_output(command, cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_root(path: Path) -> Path:
    return Path(_run_text(["git", "rev-parse", "--show-toplevel"], path)).resolve()


def stage_python_sources(
    source_root: Path,
    destination: Path,
    evidence: bool,
    frozen_source: dict[str, Any],
) -> dict[str, Any]:
    source_root = source_root.resolve()
    repo_root = _git_root(source_root)
    head = _run_text(["git", "rev-parse", "HEAD"], repo_root)
    current_origin_main = _run_text(["git", "rev-parse", "origin/main"], repo_root)
    frozen_commit = str(frozen_source["python_source_commit"])
    subprocess.check_call(
        ["git", "cat-file", "-e", f"{frozen_commit}^{{commit}}"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    crawler_relative = source_root.relative_to(repo_root)
    dockerfile = subprocess.check_output(
        ["git", "show", f"{frozen_commit}:{(crawler_relative / 'Dockerfile').as_posix()}"],
        cwd=repo_root,
        text=True,
    )
    image_match = re.search(
        r"^FROM (python:3\.13\.15-[^\s]+@sha256:[0-9a-f]{64}) AS base$", dockerfile, re.MULTILINE
    )
    if image_match is None:
        raise RuntimeError("frozen production Python image identity is not recognized")
    if image_match.group(1) != frozen_source["python_image_identity"]:
        raise RuntimeError("frozen Dockerfile image does not match hashed corpus")
    frozen_hashes = {
        item["relative_path"]: item["sha256"] for item in frozen_source["python_files"]
    }
    destination.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, str]] = []
    for relative in PYTHON_SOURCE_FILES:
        if evidence:
            git_path = (crawler_relative / relative).as_posix()
            data = subprocess.check_output(
                ["git", "show", f"{frozen_commit}:{git_path}"], cwd=repo_root
            )
            source_mode = "git-object-frozen-commit"
        else:
            data = (source_root / relative).read_bytes()
            source_mode = "worktree-smoke"
        digest = _sha256(data)
        if evidence and digest != frozen_hashes[relative]:
            raise RuntimeError(f"frozen Python source hash mismatch: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append({"relative_path": relative, "sha256": digest})
    identity = {
        "source_mode": source_mode,
        "repository_head": head,
        "current_origin_main_commit_context_only": current_origin_main,
        "frozen_source_commit": frozen_commit,
        "staged_commit": frozen_commit if evidence else f"worktree:{head}",
        "frozen_python_runtime": frozen_source["python_runtime"],
        "frozen_image_identity": image_match.group(1),
        "files": files,
    }
    identity_bytes = (json.dumps(identity, indent=2, sort_keys=True) + "\n").encode("utf-8")
    identity["identity_sha256"] = _sha256(identity_bytes)
    return identity


def _go_binary_build_info(binary: Path, go_command: str = "go") -> dict[str, Any]:
    output = subprocess.check_output(
        [go_command, "version", "-m", str(binary.resolve())], text=True, stderr=subprocess.STDOUT
    )
    lines = output.splitlines()
    if not lines or ": " not in lines[0]:
        raise RuntimeError("Go binary has no readable build information")
    runtime = lines[0].rsplit(": ", 1)[1]
    path = ""
    module = ""
    settings: dict[str, str] = {}
    for line in lines[1:]:
        fields = line.strip().split("\t")
        if len(fields) < 2:
            continue
        if fields[0] == "path":
            path = fields[1]
        elif fields[0] == "mod":
            module = fields[1]
        elif fields[0] == "build" and "=" in fields[1]:
            key, value = fields[1].split("=", 1)
            settings[key] = value
    return {
        "go_version": runtime,
        "path": path,
        "module": module,
        "settings": settings,
    }


def validate_go_binary_identity(
    identity: dict[str, Any], frozen_source: dict[str, Any], *, evidence: bool
) -> None:
    build = identity["build_info"]
    failures: list[str] = []
    if build["path"] != frozen_source["go_runner_path"]:
        failures.append("runner package path")
    if build["module"] != frozen_source["go_module"]:
        failures.append("module path")
    if build["settings"].get("-trimpath") != "true":
        failures.append("-trimpath")
    if evidence:
        if build["go_version"] != frozen_source["go_toolchain"]:
            failures.append("Go toolchain")
        if build["settings"].get("vcs.revision") != identity["repository_head"]:
            failures.append("VCS revision")
        if build["settings"].get("vcs.modified") != "false":
            failures.append("clean VCS state")
        if not identity["repository_clean"]:
            failures.append("clean source worktree")
        if build["settings"].get("GOOS") != "linux":
            failures.append("GOOS")
        if build["settings"].get("GOARCH") != "amd64":
            failures.append("GOARCH")
    if failures:
        raise RuntimeError("Go binary identity mismatch: " + ", ".join(failures))


def _git_cleanliness(repo_root: Path) -> dict[str, list[str]]:
    return {
        "status": _run_text(
            [
                "git",
                "--no-optional-locks",
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            repo_root,
        ).splitlines(),
        "ignored": _run_text(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard"], repo_root
        ).splitlines(),
    }


def _tree_manifest(root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            raise RuntimeError(f"source input contains a symlink: {path}")
        if path.is_file():
            rows.append(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "sha256": _sha256(path.read_bytes()),
                }
            )
    return rows


def _distribution_census() -> list[dict[str, str]]:
    def canonical(value: str) -> str:
        return value.lower().replace("_", "-").replace(".", "-")

    return sorted(
        (
            {"name": canonical(str(item.metadata["Name"])), "version": item.version}
            for item in importlib.metadata.distributions()
            if item.metadata["Name"]
        ),
        key=lambda row: (row["name"], row["version"]),
    )


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def _load_and_validate_python_build_attestation(
    source: dict[str, Any], lock_path: Path
) -> dict[str, Any]:
    if Path(sys.executable) != EVIDENCE_PYTHON:
        raise RuntimeError("evidence orchestrator did not use the fixed Python interpreter")
    build = json.loads(EVIDENCE_PYTHON_ATTESTATION.read_text(encoding="utf-8"))
    current = {
        "sys_executable": sys.executable,
        "executable_sha256": _sha256(EVIDENCE_PYTHON.read_bytes()),
        "runtime": platform.python_version(),
        "lock_sha256": _sha256(lock_path.read_bytes()),
        "runtime_package_versions": source["python_runtime_packages"],
        "absent_runtime_packages": source["python_absent_runtime_packages"],
        "installed_distributions": _distribution_census(),
        "os_release": _os_release(),
    }
    if build != current:
        raise RuntimeError("current Python runtime differs from its build-produced attestation")
    return build


def go_source_identity(
    binary: Path,
    frozen_source: dict[str, Any],
    *,
    evidence: bool,
    pilot_root: Path = PILOT_ROOT,
    go_command: str = "go",
) -> dict[str, Any]:
    pilot_root = pilot_root.resolve()
    repo_root = _git_root(pilot_root)
    files: list[dict[str, str]] = []
    for path in sorted(pilot_root.rglob("*.go")):
        if path.is_symlink():
            raise RuntimeError("Go source tree contains a symlink")
        data = path.read_bytes()
        files.append({"relative_path": str(path.relative_to(pilot_root)), "sha256": _sha256(data)})
    for module_file in ("go.mod", "go.sum"):
        module_path = pilot_root / module_file
        if module_path.is_file():
            files.append(
                {"relative_path": module_file, "sha256": _sha256(module_path.read_bytes())}
            )
    binary = binary.resolve()
    cleanliness = _git_cleanliness(repo_root)
    identity = {
        "repository_head": _run_text(["git", "rev-parse", "HEAD"], repo_root),
        "repository_clean": not cleanliness["status"] and not cleanliness["ignored"],
        "repository_status": cleanliness["status"],
        "repository_ignored_overlays": cleanliness["ignored"],
        "binary_path_basename": binary.name,
        "binary_sha256": _sha256(binary.read_bytes()),
        "build_info": _go_binary_build_info(binary, go_command),
        "source_files": files,
    }
    source_bytes = (json.dumps(files, sort_keys=True) + "\n").encode()
    identity["source_manifest_sha256"] = _sha256(source_bytes)
    validate_go_binary_identity(identity, frozen_source, evidence=evidence)
    return identity


def _rebuild_evidence_go_binary(
    *, source: dict[str, Any], staged_binary: Path, rebuilt_binary: Path
) -> dict[str, Any]:
    go_version = subprocess.check_output(
        [str(EVIDENCE_GO), "version"], text=True, stderr=subprocess.STDOUT
    ).strip()
    if go_version != "go version go1.24.0 linux/amd64":
        raise RuntimeError("evidence rebuild did not use exact Go 1.24.0 linux/amd64")
    rebuilt_binary.parent.mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": "/usr/local/go/bin:/usr/bin:/bin",
        "HOME": "/tmp/jobseek-evidence-go-home",
        "LANG": "C",
        "LC_ALL": "C",
        "CGO_ENABLED": "0",
        "GOTOOLCHAIN": "local",
        "GOCACHE": "/tmp/jobseek-evidence-go-cache",
        "GOFLAGS": "-mod=readonly",
    }
    Path(environment["HOME"]).mkdir(parents=True, exist_ok=True)
    Path(environment["GOCACHE"]).mkdir(parents=True, exist_ok=True)
    command = [
        str(EVIDENCE_GO),
        "build",
        "-trimpath",
        "-o",
        str(rebuilt_binary),
        "./bench/go-runner",
    ]
    subprocess.check_call(command, cwd=EVIDENCE_PILOT_ROOT, env=environment)
    if rebuilt_binary.read_bytes() != staged_binary.read_bytes():
        raise RuntimeError("evidence Go rebuild is not byte-identical to the executed binary")
    staged = go_source_identity(
        staged_binary,
        source,
        evidence=True,
        pilot_root=EVIDENCE_PILOT_ROOT,
        go_command=str(EVIDENCE_GO),
    )
    rebuilt = go_source_identity(
        rebuilt_binary,
        source,
        evidence=True,
        pilot_root=EVIDENCE_PILOT_ROOT,
        go_command=str(EVIDENCE_GO),
    )
    if staged["build_info"] != rebuilt["build_info"]:
        raise RuntimeError("evidence Go rebuild build information differs")
    return {
        "go_version": go_version,
        "command": command,
        "environment": environment,
        "rebuilt_sha256": rebuilt["binary_sha256"],
        "byte_identical_to_executed_staged_binary": True,
        "build_info_identical": True,
    }


def _safe_child_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C",
        "LC_ALL": "C",
        "GOMAXPROCS": "1",
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
    }


def _process_table() -> dict[int, tuple[int, int]]:
    if sys.platform.startswith("linux"):
        table: dict[int, tuple[int, int]] = {}
        for status in Path("/proc").glob("[0-9]*/status"):
            try:
                values: dict[str, str] = {}
                for line in status.read_text().splitlines():
                    if line.startswith(("Pid:", "PPid:", "VmRSS:")):
                        key, value = line.split(":", 1)
                        values[key] = value.strip().split()[0]
                table[int(values["Pid"])] = (int(values["PPid"]), int(values.get("VmRSS", "0")))
            except (FileNotFoundError, KeyError, PermissionError, ProcessLookupError, ValueError):
                continue
        return table
    output = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True)
    table = {}
    for line in output.splitlines():
        try:
            pid, ppid, rss = (int(value) for value in line.split())
        except ValueError:
            continue
        table[pid] = (ppid, rss)
    return table


def _tree_pids(root_pid: int, table: dict[int, tuple[int, int]]) -> set[int]:
    result = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent, _) in table.items():
            if parent in result and pid not in result:
                result.add(pid)
                changed = True
    return {pid for pid in result if pid in table}


def _fd_count(pids: set[int]) -> int:
    if not sys.platform.startswith("linux"):
        return -1
    total = 0
    for pid in pids:
        with contextlib.suppress(FileNotFoundError, PermissionError, ProcessLookupError):
            total += len(list(Path(f"/proc/{pid}/fd").iterdir()))
    return total


class ProcessSampler:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.samples: list[dict[str, int]] = []
        self.periodic_samples = 0
        self.error: BaseException | None = None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._sample_loop, name=f"benchmark-sampler-{pid}", daemon=True
        )

    def _sample_once(self) -> None:
        table = _process_table()
        pids = _tree_pids(self.pid, table)
        if not pids:
            return
        self.samples.append(
            {
                "at_ns": time.monotonic_ns(),
                "rss_kib": sum(table[pid][1] for pid in pids),
                "processes": len(pids),
                "open_fds": _fd_count(pids),
            }
        )

    def _sample_loop(self) -> None:
        try:
            while not self.stop_event.wait(0.01):
                self._sample_once()
                self.periodic_samples += 1
        except BaseException as exc:
            self.error = exc
            self.stop_event.set()

    def start(self) -> None:
        self._sample_once()
        self.thread.start()

    def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        self.thread.join(timeout=2.0)
        if self.thread.is_alive():
            raise RuntimeError("process sampler thread did not stop")
        if self.error is not None:
            raise RuntimeError("process sampler thread failed") from self.error
        self._sample_once()
        rss = [sample["rss_kib"] for sample in self.samples]
        fds = [sample["open_fds"] for sample in self.samples if sample["open_fds"] >= 0]
        return {
            "sample_count": len(self.samples),
            "periodic_sample_count": self.periodic_samples,
            "coverage_ns": (
                self.samples[-1]["at_ns"] - self.samples[0]["at_ns"]
                if len(self.samples) >= 2
                else 0
            ),
            "sampler_thread_stopped": not self.thread.is_alive(),
            "steady_rss_kib": statistics.median(rss) if rss else 0,
            "peak_sampled_rss_kib": max(rss, default=0),
            "max_open_fds": max(fds, default=-1),
            "max_processes": max((sample["processes"] for sample in self.samples), default=0),
        }


def _cgroup_path(pid: int) -> str | None:
    for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0":
            return fields[2]
    return None


def _cgroup_snapshot_from_path(unified: str) -> dict[str, Any]:
    root = Path("/sys/fs/cgroup").resolve()
    base = (root / unified.lstrip("/")).resolve()
    if base != root and root not in base.parents:
        raise RuntimeError("cgroup path escaped the cgroup-v2 mount")
    result: dict[str, Any] = {"path": unified}
    for name in (
        "cpu.max",
        "memory.max",
        "memory.swap.max",
        "pids.max",
        "memory.events",
        "cgroup.procs",
    ):
        path = base / name
        if path.is_file():
            value = path.read_text().strip()
            result[name] = (
                sorted(int(line) for line in value.splitlines())
                if name == "cgroup.procs" and value
                else ([] if name == "cgroup.procs" else value)
            )
    return result


def _cgroup_snapshot(pid: int) -> dict[str, Any] | None:
    if not sys.platform.startswith("linux"):
        return None
    unified = _cgroup_path(pid)
    return _cgroup_snapshot_from_path(unified) if unified is not None else None


def _cgroup_fingerprint(snapshot: dict[str, Any]) -> str:
    stable = {
        key: snapshot.get(key)
        for key in ("path", "cpu.max", "memory.max", "memory.swap.max", "pids.max")
    }
    return _sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode())


def _require_cgroup_snapshot(snapshot: dict[str, Any]) -> None:
    required = {
        "cpu.max",
        "memory.max",
        "memory.swap.max",
        "pids.max",
        "memory.events",
        "cgroup.procs",
    }
    missing = sorted(required - set(snapshot))
    if missing:
        raise RuntimeError(f"runner cgroup snapshot is missing required controls: {missing}")


def _network_namespace_attestation(pid: int) -> dict[str, Any]:
    if not sys.platform.startswith("linux"):
        return {
            "supported": False,
            "platform": platform.system(),
            "fingerprint": "unsupported-non-linux-smoke",
        }
    namespace = os.readlink(f"/proc/{pid}/ns/net")
    net_dev = Path(f"/proc/{pid}/net/dev").read_text().splitlines()[2:]
    interfaces = sorted(line.split(":", 1)[0].strip() for line in net_dev if ":" in line)
    ipv4_routes: list[dict[str, str]] = []
    for line in Path(f"/proc/{pid}/net/route").read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 8:
            destination = str(ipaddress.IPv4Address(bytes.fromhex(fields[1])[::-1]))
            mask = str(ipaddress.IPv4Address(bytes.fromhex(fields[7])[::-1]))
            ipv4_routes.append(
                {
                    "interface": fields[0],
                    "destination": destination,
                    "mask": mask,
                    "gateway_hex": fields[2],
                }
            )
    ipv6_routes: list[dict[str, str | int]] = []
    ipv6_path = Path(f"/proc/{pid}/net/ipv6_route")
    if ipv6_path.is_file():
        for line in ipv6_path.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 10:
                ipv6_routes.append(
                    {
                        "interface": fields[-1],
                        "destination": str(ipaddress.IPv6Address(int(fields[0], 16))),
                        "prefix_length": int(fields[1], 16),
                    }
                )
    record = {
        "supported": True,
        "namespace": namespace,
        "interfaces": interfaces,
        "ipv4_routes": ipv4_routes,
        "ipv6_routes": ipv6_routes,
    }
    record["fingerprint"] = _sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    )
    return record


def _validate_loopback_only_network(attestation: dict[str, Any]) -> None:
    if not attestation.get("supported"):
        raise RuntimeError("evidence mode requires Linux network-namespace attestation")
    if attestation.get("interfaces") != ["lo"]:
        raise RuntimeError("evidence network namespace has a non-loopback interface")
    for route in attestation.get("ipv4_routes", []):
        destination = ipaddress.ip_address(route["destination"])
        if (
            route["interface"] != "lo"
            or not destination.is_loopback
            or (route["destination"] == "0.0.0.0" and route["mask"] == "0.0.0.0")
        ):
            raise RuntimeError("evidence network namespace has a non-loopback IPv4 route")
    for route in attestation.get("ipv6_routes", []):
        destination = ipaddress.ip_address(route["destination"])
        if (
            route["interface"] != "lo"
            or not destination.is_loopback
            or (route["destination"] == "::" and route["prefix_length"] == 0)
        ):
            raise RuntimeError("evidence network namespace has a non-loopback IPv6 route")


def _validate_evidence_envelope(
    *,
    runner_pid: int,
    process_pid: int,
    ready: dict[str, Any],
    fd_limit: int,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise RuntimeError("evidence mode requires Linux/amd64")
    if runner_pid != process_pid:
        raise RuntimeError(
            "evidence runner prefix must exec; wrapper process trees are not admitted"
        )
    snapshot = _cgroup_snapshot(runner_pid)
    if snapshot is None:
        raise RuntimeError("cgroup v2 limits are unavailable")
    _require_cgroup_snapshot(snapshot)
    quota, period = str(snapshot.get("cpu.max", "max 1")).split()
    if quota == "max" or int(quota) != int(period):
        raise RuntimeError("runner cgroup must provide exactly one vCPU")
    memory = snapshot.get("memory.max")
    swap = snapshot.get("memory.swap.max", "0")
    if memory == "max" or swap == "max" or int(memory) + int(swap) != 1_073_741_824:
        raise RuntimeError("runner cgroup must provide exactly 1 GiB combined memory+swap")
    pids = snapshot.get("pids.max")
    if pids == "max" or int(pids) != 128:
        raise RuntimeError("runner cgroup must provide exactly 128 PIDs")
    if snapshot.get("cgroup.procs") != [runner_pid]:
        raise RuntimeError("runner cgroup has a stale or co-tenant process")
    if int(ready["file_descriptor_soft_limit"]) != fd_limit:
        raise RuntimeError("runner file-descriptor limit mismatch")
    own = _cgroup_snapshot(os.getpid())
    if own is not None and own["path"] == snapshot["path"]:
        raise RuntimeError("fixture/orchestrator must be outside the measured runner cgroup")
    fingerprint = _cgroup_fingerprint(snapshot)
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise RuntimeError("runner cgroup path or exact limits changed between arms")
    snapshot["fingerprint"] = fingerprint
    return snapshot


def _validate_empty_cgroup(path: str, expected_fingerprint: str) -> dict[str, Any]:
    deadline = time.monotonic() + 2.0
    while True:
        snapshot = _cgroup_snapshot_from_path(path)
        _require_cgroup_snapshot(snapshot)
        if _cgroup_fingerprint(snapshot) != expected_fingerprint:
            raise RuntimeError("runner cgroup path or exact limits changed after shutdown")
        if snapshot.get("cgroup.procs") == []:
            snapshot["fingerprint"] = expected_fingerprint
            return snapshot
        if time.monotonic() >= deadline:
            raise RuntimeError("runner cgroup retained a stale process after shutdown")
        time.sleep(0.01)


def _memory_event(snapshot: dict[str, Any] | None, name: str) -> int:
    if snapshot is None:
        raise RuntimeError("cgroup memory event snapshot is unavailable")
    if "memory.events" not in snapshot:
        raise RuntimeError("cgroup memory.events is unavailable")
    for line in str(snapshot.get("memory.events", "")).splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == name:
            return int(fields[1])
    raise RuntimeError(f"cgroup memory event counter is unavailable: {name}")


class RunnerProcess:
    def __init__(self, command: list[str], *, cwd: Path, ready_timeout: float = 30.0) -> None:
        self.command = command
        self.stderr: list[str] = []
        self.lines: queue.Queue[str] = queue.Queue()
        started = time.monotonic_ns()
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=_safe_child_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        assert self.process.stdout is not None and self.process.stderr is not None
        self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self.stdout_thread.start()
        self.stderr_thread.start()
        try:
            self.ready = self._read_json(ready_timeout)
            if self.ready.get("type") != "ready":
                raise RuntimeError(f"runner did not emit ready record: {self.ready}")
        except Exception as exc:
            self.terminate()
            raise RuntimeError(
                f"runner startup failed: exit={self.process.returncode} "
                f"stderr={''.join(self.stderr)!r}"
            ) from exc
        self.startup_ns = time.monotonic_ns() - started

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr.append(line)

    def _close_pipes(self) -> None:
        for handle in (self.process.stdin, self.process.stdout, self.process.stderr):
            if handle is not None and not handle.closed:
                with contextlib.suppress(BrokenPipeError, OSError):
                    handle.close()

    def _read_json(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = self.lines.get(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
                break
            except queue.Empty as exc:
                if self.process.poll() is not None or time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"runner output timed out; exit={self.process.poll()}"
                    ) from exc
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"runner emitted non-JSON stdout: {line!r}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"runner emitted a non-mapping JSON record: {value!r}")
        return value

    def batch(
        self, command: dict[str, Any], timeout: float
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        assert self.process.stdin is not None
        sampler = ProcessSampler(int(self.ready["pid"]))
        sampler.start()
        try:
            self.process.stdin.write(json.dumps(command, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            result = self._read_json(timeout)
        finally:
            samples = sampler.stop()
        if result.get("type") != "batch":
            raise RuntimeError(f"runner emitted unexpected batch record: {result}")
        return result, samples

    def close(self) -> dict[str, Any]:
        if self.process.poll() is not None:
            raise RuntimeError(f"runner exited early with {self.process.returncode}")
        assert self.process.stdin is not None
        self.process.stdin.write('{"action":"shutdown"}\n')
        self.process.stdin.flush()
        stopped = self._read_json(40.0)
        self.process.stdin.close()
        returncode = self.process.wait(timeout=10.0)
        self.stdout_thread.join(timeout=2.0)
        self.stderr_thread.join(timeout=2.0)
        self._close_pipes()
        if stopped.get("type") != "stopped" or returncode != 0:
            raise RuntimeError(f"runner shutdown failed: stopped={stopped} exit={returncode}")
        return {"stopped": stopped, "returncode": returncode, "stderr": "".join(self.stderr)}

    def terminate(self) -> None:
        if os.name == "posix":
            with contextlib.suppress(PermissionError, ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
        elif self.process.poll() is None:
            self.process.terminate()
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    with contextlib.suppress(PermissionError, ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGKILL)
                else:
                    self.process.kill()
                self.process.wait(timeout=3.0)
        if os.name == "posix":
            # A non-exec prefix can exit before its descendants. Its process
            # group remains ours and must not survive a failed benchmark arm.
            with contextlib.suppress(PermissionError, ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
        self.stdout_thread.join(timeout=2.0)
        self.stderr_thread.join(timeout=2.0)
        self._close_pipes()


def _scenario_schedule(corpus: dict[str, Any], suite: str) -> list[dict[str, Any]]:
    schedule: list[dict[str, Any]] = []
    for scenario in corpus["scenarios"]:
        if scenario["suite"] == suite:
            schedule.extend([scenario] * int(scenario["weight"]))
    return schedule


def make_jobs(
    corpus: dict[str, Any], origins: list[str], *, batch_id: str, suite: str, count: int
) -> list[dict[str, Any]]:
    schedule = _scenario_schedule(corpus, suite)
    if not schedule:
        raise RuntimeError(f"empty scenario schedule for {suite}")
    jobs = []
    for index in range(count):
        scenario = schedule[index % len(schedule)]
        origin = origins[index % len(origins)]
        job_id = f"job-{index:04d}"
        jobs.append(
            {
                "id": job_id,
                "origin": origin,
                "sitemap_url": f"{origin}/fixture/{batch_id}/{job_id}/sitemap/root.xml",
                "scenario": scenario["id"],
                "expected_outcome": scenario["expected_outcome"],
            }
        )
    return jobs


def _write_manifest(out: Path, batch_id: str, jobs: list[dict[str, Any]]) -> str:
    digest = job_manifest_sha256(jobs)
    record = {"batch_id": batch_id, "manifest_sha256": digest, "jobs": jobs}
    path = out / "manifests" / f"{batch_id}.json"
    encoded = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text() != encoded:
        raise RuntimeError("batch manifest changed between implementations")
    path.write_text(encoded)
    return digest


def _runner_command(
    *,
    implementation: str,
    python_executable: Path,
    runner_prefix: list[str],
    source_bundle: Path,
    python_identity: dict[str, Any],
    staged_lock_file: Path,
    staged_go_binary: Path,
    staged_python_runner: Path,
    workers: int,
    capacity: int,
    per_origin: int,
    defaults: dict[str, int],
) -> list[str]:
    common = [
        "--workers",
        str(workers),
        "--capacity",
        str(capacity),
        "--result-capacity",
        str(capacity),
        "--per-origin",
        str(per_origin),
        "--global-active",
        str(defaults["global_active_requests"]),
        "--total-connections",
        str(defaults["global_total_connections"]),
        "--global-idle",
        str(defaults["global_idle_connections"]),
        "--idle-per-origin",
        str(per_origin),
        "--idle-expiry-seconds",
        str(defaults["idle_expiry_seconds"]),
        "--request-timeout-seconds",
        str(defaults["request_timeout_seconds"]),
        "--job-timeout-seconds",
        str(defaults["job_timeout_seconds"]),
        "--fd-limit",
        str(defaults["file_descriptor_limit"]),
    ]
    if implementation == "go":
        command = [str(staged_go_binary), *common]
    else:
        command = [
            str(python_executable),
            str(staged_python_runner),
            "--source-bundle",
            str(source_bundle.resolve()),
            "--source-commit",
            str(python_identity["staged_commit"]),
            "--source-identity-sha256",
            str(python_identity["identity_sha256"]),
            "--lock-file",
            str(staged_lock_file),
            *common,
        ]
    return [*runner_prefix, *command]


def _validate_ready(
    ready: dict[str, Any], *, workers: int, capacity: int, per_origin: int, defaults: dict[str, int]
) -> None:
    expected = {
        "workers": workers,
        "capacity": capacity,
        "result_capacity": capacity,
        "per_origin_concurrency": per_origin,
        "global_active_requests": defaults["global_active_requests"],
        "global_total_connections": defaults["global_total_connections"],
        "global_idle_connections": defaults["global_idle_connections"],
        "idle_expiry_seconds": defaults["idle_expiry_seconds"],
        "request_timeout_seconds": defaults["request_timeout_seconds"],
        "job_timeout_seconds": defaults["job_timeout_seconds"],
        "file_descriptor_soft_limit": defaults["file_descriptor_limit"],
        "client_constructed_before_ready": True,
        "pool_constructed_before_ready": True,
    }
    mismatches = {
        key: (ready.get(key), value) for key, value in expected.items() if ready.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"runner config mismatch: {mismatches}")
    if ready.get("implementation") not in {"go-worker-pilot", "python-production-sitemap"}:
        raise RuntimeError("runner implementation identity is unknown")
    if ready["implementation"] == "go-worker-pilot" and ready.get("gomaxprocs") != 1:
        raise RuntimeError("Go runner must use GOMAXPROCS=1")
    if ready["implementation"] == "go-worker-pilot":
        if ready.get("idle_connections_per_origin") != per_origin:
            raise RuntimeError("Go per-origin idle safety bound mismatch")
        if (
            ready.get("per_origin_idle_enforcement")
            != "go-transport-extra-nonbinding-at-active-origin-cap"
        ):
            raise RuntimeError("Go per-origin idle bound was not disclosed")
    if ready["implementation"] == "python-production-sitemap":
        if ready.get("runtime", "").split(".")[:2] != ["3", "13"]:
            raise RuntimeError("Python comparator is not Python 3.13")
        if ready.get("production_entrypoint") != "src.core.monitors.sitemap.discover":
            raise RuntimeError("Python comparator did not load the production sitemap entrypoint")
        if ready.get("per_origin_idle_audit_bound") != per_origin:
            raise RuntimeError("Python per-origin idle audit bound mismatch")
        if (
            ready.get("per_origin_idle_enforcement")
            != "workload-and-fixture-audit-not-httpx-setting"
        ):
            raise RuntimeError("Python per-origin idle behavior was not disclosed")


def _validate_go_ready(ready: dict[str, Any], identity: dict[str, Any]) -> str:
    if ready.get("executable_sha256") != identity["binary_sha256"]:
        raise RuntimeError("running Go executable does not match the staged binary")
    if ready.get("build_info") != identity["build_info"]:
        raise RuntimeError("running Go build information does not match pre-exec inspection")
    return _sha256(
        json.dumps(
            {"executable_sha256": ready["executable_sha256"], "build_info": ready["build_info"]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def _validate_python_ready_environment(
    ready: dict[str, Any],
    source: dict[str, Any],
    *,
    evidence: bool,
    build_attestation: dict[str, Any] | None = None,
) -> str:
    expected_packages = source["python_runtime_packages"]
    if ready.get("lock_sha256") != source["python_lock_sha256"]:
        raise RuntimeError("Python runner lock hash does not match the frozen corpus")
    if ready.get("runtime_package_versions") != expected_packages:
        raise RuntimeError("Python runtime package versions do not match the frozen corpus")
    if ready.get("absent_runtime_packages") != source["python_absent_runtime_packages"]:
        raise RuntimeError("Python absent-package census does not match the frozen corpus")
    installed_rows = ready.get("installed_distributions")
    if not isinstance(installed_rows, list) or any(
        not isinstance(row, dict) for row in installed_rows
    ):
        raise RuntimeError("Python installed-distribution census is invalid")
    installed = [(row.get("name"), row.get("version")) for row in installed_rows]
    installed_names = [name for name, _ in installed]
    if len(installed_names) != len(set(installed_names)):
        raise RuntimeError("Python installed-distribution census contains duplicates")
    if evidence and dict(installed) != expected_packages:
        raise RuntimeError("evidence Python environment contains an undeclared distribution")
    os_release = ready.get("os_release")
    if evidence and (
        not isinstance(os_release, dict)
        or any(os_release.get(key) != value for key, value in source["python_os_release"].items())
    ):
        raise RuntimeError("evidence Python OS release does not match the frozen runtime")
    if not re.fullmatch(r"[0-9a-f]{64}", str(ready.get("executable_sha256", ""))):
        raise RuntimeError("Python executable hash attestation is missing")
    if evidence:
        if build_attestation is None:
            raise RuntimeError("evidence Python build attestation is missing")
        expected_runtime = {
            "sys_executable": str(EVIDENCE_PYTHON),
            "executable_sha256": ready.get("executable_sha256"),
            "runtime": ready.get("runtime"),
            "lock_sha256": ready.get("lock_sha256"),
            "runtime_package_versions": ready.get("runtime_package_versions"),
            "absent_runtime_packages": ready.get("absent_runtime_packages"),
            "installed_distributions": ready.get("installed_distributions"),
            "os_release": ready.get("os_release"),
        }
        if ready.get("executable_path") != str(EVIDENCE_PYTHON):
            raise RuntimeError("Python runner did not execute the fixed evidence interpreter")
        if build_attestation != expected_runtime:
            raise RuntimeError("Python runtime differs from the immutable build attestation")
    record = {
        "runtime": ready.get("runtime"),
        "lock_sha256": ready.get("lock_sha256"),
        "runtime_package_versions": ready.get("runtime_package_versions"),
        "absent_runtime_packages": ready.get("absent_runtime_packages"),
        "installed_distributions": installed_rows,
        "executable_sha256": ready.get("executable_sha256"),
        "executable_path": ready.get("executable_path"),
        "os_release": ready.get("os_release"),
    }
    return _sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())


def _wait_for_transcript(
    fleet: FixtureFleet, arm_token: str, batch_id: str, expected: int
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 2.0
    while True:
        rows = fleet.state.requests_for(arm_token, batch_id)
        if len(rows) == expected:
            return rows
        if len(rows) > expected or time.monotonic() >= deadline:
            raise RuntimeError(
                f"fixture transcript mismatch for {arm_token}/{batch_id}: {len(rows)} != {expected}"
            )
        time.sleep(0.005)


def _wait_for_connection_bounds(
    fleet: FixtureFleet, arm_token: str, global_idle: int
) -> dict[str, int]:
    deadline = time.monotonic() + 1.0
    while True:
        snapshot = fleet.state.connection_snapshot(arm_token)
        if snapshot["active"] == 0 and snapshot["idle"] <= global_idle:
            return snapshot
        if time.monotonic() >= deadline:
            raise RuntimeError(f"connection pool did not settle within bounds: {snapshot}")
        time.sleep(0.01)


def _wait_for_arm_connections_closed(fleet: FixtureFleet, arm_token: str) -> None:
    deadline = time.monotonic() + 2.0
    while fleet.state.connection_snapshot(arm_token)["open"]:
        if time.monotonic() >= deadline:
            raise RuntimeError("runner left fixture connections open after shutdown")
        time.sleep(0.01)


def _status_sequence(rows: list[dict[str, Any]], job_id: str) -> list[int]:
    return [
        int(row["status"])
        for row in sorted(
            (row for row in rows if row["job_id"] == job_id), key=lambda row: row["started_ns"]
        )
    ]


def validate_batch(
    *,
    result: dict[str, Any],
    jobs: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
    connection_snapshot: dict[str, int],
    samples: dict[str, Any],
    scenarios: dict[str, dict[str, Any]],
    defaults: dict[str, int],
    workers: int,
    capacity: int,
    implementation: str,
    batch_id: str,
    phase: str,
    evidence: bool,
) -> list[dict[str, Any]]:
    expected_implementation = {
        "go": "go-worker-pilot",
        "python": "python-production-sitemap",
    }[implementation]
    metadata = {
        "type": "batch",
        "implementation": expected_implementation,
        "batch_id": batch_id,
        "phase": phase,
    }
    mismatches = {
        key: (result.get(key), value) for key, value in metadata.items() if result.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"runner batch metadata mismatch: {mismatches}")
    if result.get("wall_ns", 0) <= 0:
        raise RuntimeError("runner batch wall time must be positive")
    if result.get("user_cpu_ns", -1) < 0 or result.get("sys_cpu_ns", -1) < 0:
        raise RuntimeError("runner batch CPU times must be non-negative")
    expected_digest = job_manifest_sha256(jobs)
    if result["manifest_sha256"] != expected_digest:
        raise RuntimeError("runner consumed the wrong ordered job manifest")
    outputs = {output["id"]: output for output in result["jobs"]}
    if set(outputs) != {job["id"] for job in jobs} or len(outputs) != len(result["jobs"]):
        raise RuntimeError("lost or duplicate terminal result")
    if result["accepted"] != len(jobs) or result["completed"] != len(jobs):
        raise RuntimeError("admission/completion conservation failed")
    if result["unfinished"] or result["panics"]:
        raise RuntimeError("unfinished or panicked work is not admissible")
    queue_intervals: list[tuple[int, int]] = []
    service_intervals: list[tuple[int, int]] = []
    unfinished_intervals: list[tuple[int, int]] = []
    service_by_origin: defaultdict[str, list[tuple[int, int]]] = defaultdict(list)
    for output in outputs.values():
        accepted = output.get("accepted_ns")
        started = output.get("started_ns")
        finished = output.get("finished_ns")
        if not all(isinstance(value, int) and value > 0 for value in (accepted, started, finished)):
            raise RuntimeError("runner emitted invalid job timestamps")
        if not accepted <= started < finished:
            raise RuntimeError("runner emitted unordered job timestamps")
        queue_ns = output.get("queue_ns")
        service_ns = output.get("service_ns")
        end_to_end_ns = output.get("end_to_end_ns")
        if (
            not isinstance(queue_ns, int)
            or queue_ns < 0
            or not isinstance(service_ns, int)
            or service_ns <= 0
            or not isinstance(end_to_end_ns, int)
            or end_to_end_ns <= 0
            or queue_ns != started - accepted
            or service_ns != finished - started
            or end_to_end_ns != finished - accepted
        ):
            raise RuntimeError("runner job duration metadata is inconsistent")
        queue_intervals.append((accepted, started))
        service_intervals.append((started, finished))
        unfinished_intervals.append((accepted, finished))
        service_by_origin[str(output.get("origin"))].append((started, finished))
    observed = {
        "max_queued": interval_max(queue_intervals),
        "max_in_flight": interval_max(service_intervals),
        "max_unfinished": interval_max(unfinished_intervals),
    }
    for key, value in observed.items():
        if result.get(key) != value:
            raise RuntimeError(f"runner {key} does not match timestamp intervals")
    if observed["max_queued"] > capacity or observed["max_in_flight"] > workers:
        raise RuntimeError("runner exceeded queue or service bounds")
    if observed["max_unfinished"] > capacity:
        raise RuntimeError("runner exceeded accepted-but-unfinished capacity")
    if samples["max_processes"] != 1:
        raise RuntimeError("runner spawned child processes during a measured command")
    if result.get("process_children") != 0:
        raise RuntimeError("runner reported child processes")
    if not samples.get("sampler_thread_stopped", False):
        raise RuntimeError("resource sampler did not stop")
    if samples.get("sample_count", 0) < 2:
        raise RuntimeError("resource sampler produced insufficient boundary samples")
    if evidence and (
        samples.get("periodic_sample_count", 0) < 3
        or samples.get("coverage_ns", 0) < result["wall_ns"]
    ):
        raise RuntimeError("evidence RSS sampler coverage is insufficient")
    if connection_snapshot["max_active_global"] > defaults["global_active_requests"]:
        raise RuntimeError("global request cap exceeded")
    if connection_snapshot["max_open_global"] > defaults["global_total_connections"]:
        raise RuntimeError("global total-connection cap exceeded")
    effective_per_origin = min(defaults["per_origin_concurrency"], workers)
    if connection_snapshot["max_active_per_origin"] > effective_per_origin:
        raise RuntimeError("per-origin request cap exceeded")
    if connection_snapshot["idle"] > defaults["global_idle_connections"]:
        raise RuntimeError("global idle-connection cap exceeded")
    if connection_snapshot["max_open_per_origin_observed"] > effective_per_origin:
        raise RuntimeError("fixture observed too many connections for one origin")
    max_service_per_origin = max(
        (interval_max(intervals) for intervals in service_by_origin.values()), default=0
    )
    if max_service_per_origin > effective_per_origin:
        raise RuntimeError("runner exceeded per-origin job/service concurrency")
    result["validated_max_unfinished"] = observed["max_unfinished"]
    result["validated_max_service_per_origin"] = max_service_per_origin
    result["validated_per_origin_service_bound"] = effective_per_origin

    by_job: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in transcript:
        by_job[str(row["job_id"])].append(row)
        if row["method"] != "GET" or row["protocol"] != "HTTP/1.1" or not row["peer_is_loopback"]:
            raise RuntimeError("fixture observed a non-admitted request")

    enriched: list[dict[str, Any]] = []
    for job in jobs:
        output = outputs[job["id"]]
        scenario = scenarios[job["scenario"]]
        rows = sorted(by_job[job["id"]], key=lambda row: row["started_ns"])
        expected_statuses = list(scenario["root_statuses"])
        if scenario["expected_outcome"] == "success" and scenario["index_children"]:
            expected_statuses += [200] * int(scenario["index_children"])
        if [row["status"] for row in rows] != expected_statuses:
            raise RuntimeError(f"status sequence mismatch for {job['id']}")
        expected = {
            "origin": job["origin"],
            "scenario": job["scenario"],
            "outcome": scenario["expected_outcome"],
            "url_count": scenario["url_count"],
            "url_digest_sha256": scenario["expected_url_digest_sha256"],
            "requests": scenario["expected_requests"],
            "wire_attempts": scenario["expected_requests"],
            "filtered_count": 0,
            "truncated": False,
        }
        mismatch = {
            key: (output.get(key), value)
            for key, value in expected.items()
            if output.get(key) != value
        }
        if mismatch:
            raise RuntimeError(f"job output mismatch for {job['id']}: {mismatch}")
        if output["requests"] != len(rows):
            raise RuntimeError("runner/fixture request count mismatch")
        decoded = sum(row["response_body_bytes"] for row in rows if row["status"] == 200)
        status_bytes = sum(row["response_body_bytes"] for row in rows if row["status"] != 200)
        if output["decoded_bytes"] != decoded or output["status_body_bytes"] != status_bytes:
            raise RuntimeError("runner/fixture response byte mismatch")
        root_rows = [row for row in rows if row["path"].endswith("/root.xml")]
        retry_schedule_ms = [
            (root_rows[index]["started_ns"] - root_rows[index - 1]["started_ns"]) / 1_000_000
            for index in range(1, len(root_rows))
        ]
        enriched.append(
            {
                **output,
                "fixture_statuses": [row["status"] for row in rows],
                "fixture_response_body_bytes": sum(row["response_body_bytes"] for row in rows),
                "fixture_response_wire_bytes": sum(row["response_wire_bytes"] for row in rows),
                "root_attempts": len(root_rows),
                "root_retry_intervals": len(retry_schedule_ms),
                "retry_schedule_ms": retry_schedule_ms,
            }
        )
    return enriched


def validate_pair(left: dict[str, Any], right: dict[str, Any]) -> None:
    if left["manifest_sha256"] != right["manifest_sha256"]:
        raise RuntimeError("implementation manifests differ")
    fields = (
        "id",
        "origin",
        "scenario",
        "outcome",
        "url_count",
        "url_digest_sha256",
        "requests",
        "wire_attempts",
        "decoded_bytes",
        "status_body_bytes",
        "filtered_count",
        "truncated",
        "fixture_statuses",
        "fixture_response_body_bytes",
        "fixture_response_wire_bytes",
    )
    left_jobs = {job["id"]: job for job in left["enriched_jobs"]}
    right_jobs = {job["id"]: job for job in right["enriched_jobs"]}
    if set(left_jobs) != set(right_jobs):
        raise RuntimeError("implementation terminal-result sets differ")
    for job_id in left_jobs:
        for field in fields:
            if left_jobs[job_id].get(field) != right_jobs[job_id].get(field):
                raise RuntimeError(f"per-job implementation parity failed for {job_id}/{field}")


def _latency(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": percentile(values, 0.50) / 1_000_000,
        "p95_ms": percentile(values, 0.95) / 1_000_000,
        "p99_ms": percentile(values, 0.99) / 1_000_000,
    }


def arm_metrics(arm: dict[str, Any]) -> dict[str, Any]:
    result = arm["result"]
    jobs = arm["enriched_jobs"]
    successes = sum(job["outcome"] == "success" for job in jobs)
    wall_seconds = result["wall_ns"] / 1_000_000_000
    cpu_seconds = (result["user_cpu_ns"] + result["sys_cpu_ns"]) / 1_000_000_000
    return {
        "completed_jobs_per_second": len(jobs) / wall_seconds,
        "successful_jobs_per_second": successes / wall_seconds,
        "successful_jobs": successes,
        "error_jobs": len(jobs) - successes,
        "queue": _latency([job["queue_ns"] for job in jobs]),
        "service": _latency([job["service_ns"] for job in jobs]),
        "end_to_end": _latency([job["end_to_end_ns"] for job in jobs]),
        "cpu_seconds_per_successful_job": cpu_seconds / successes if successes else None,
        "steady_rss_kib": arm["samples"]["steady_rss_kib"],
        "peak_process_tree_rss_kib": max(
            arm["samples"]["peak_sampled_rss_kib"], result["peak_rss_kib"]
        ),
        "runner_cumulative_peak_rss_kib": result["peak_rss_kib"],
        "max_open_fds": arm["samples"]["max_open_fds"],
        "max_queued": result["max_queued"],
        "max_in_flight": result["max_in_flight"],
        "max_unfinished": result.get("validated_max_unfinished", result.get("max_unfinished")),
        "max_service_per_origin": result.get("validated_max_service_per_origin"),
        "per_origin_service_bound": result.get("validated_per_origin_service_bound"),
        "connections": arm["connections"],
        "requests": sum(job["requests"] for job in jobs),
        "response_bytes": sum(job["fixture_response_body_bytes"] for job in jobs),
        "root_attempts": sum(job["root_attempts"] for job in jobs),
        "root_retry_intervals": sum(job["root_retry_intervals"] for job in jobs),
        "timeouts": sum(job["outcome"] == "deadline" for job in jobs),
        "panics": result["panics"],
        "unfinished": result["unfinished"],
        "ooms": arm.get("ooms", 0),
    }


def _bootstrap_lcb95(values: list[float]) -> float:
    if not values:
        return 0.0
    generator = random.Random(7948)
    estimates = []
    for _ in range(10_000):
        sample = [values[generator.randrange(len(values))] for _ in values]
        estimates.append(statistics.median(sample))
    return percentile(estimates, 0.025)


def summarize(
    arms: list[dict[str, Any]], pairs: list[dict[str, Any]], *, mode: str
) -> dict[str, Any]:
    if mode not in {"smoke", "evidence"}:
        raise ValueError("unknown benchmark mode")
    grouped: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for arm in arms:
        if arm["suite"] in {"capacity", "stress"}:
            grouped[(arm["level"], arm["implementation"])].append(arm["metrics"])
    levels: dict[str, Any] = {}
    for level in sorted({key[0] for key in grouped}):
        implementations: dict[str, Any] = {}
        for implementation in IMPLEMENTATIONS:
            rows = grouped[(level, implementation)]
            implementations[implementation] = {
                "repetitions": len(rows),
                "saturated_successful_jobs_per_second_median": statistics.median(
                    row["successful_jobs_per_second"] for row in rows
                ),
                "planning_headroom_rate_85pct_of_observed_median": 0.85
                * statistics.median(row["successful_jobs_per_second"] for row in rows),
                "cpu_seconds_per_successful_job_median": statistics.median(
                    row["cpu_seconds_per_successful_job"] for row in rows
                ),
                "steady_rss_kib_median": statistics.median(row["steady_rss_kib"] for row in rows),
                "peak_process_tree_rss_kib_max": max(
                    row["peak_process_tree_rss_kib"] for row in rows
                ),
                "end_to_end_p99_ms_median": statistics.median(
                    row["end_to_end"]["p99_ms"] for row in rows
                ),
                "errors": sum(row["error_jobs"] for row in rows),
            }
        level_pairs = [
            pair
            for pair in pairs
            if pair["level"] == level and pair["suite"] in {"capacity", "stress"}
        ]
        speedups = [
            pair["go"]["metrics"]["successful_jobs_per_second"]
            / pair["python"]["metrics"]["successful_jobs_per_second"]
            for pair in level_pairs
        ]
        paired_resources = [
            {
                "repetition": pair["repetition"],
                "parity": pair.get("parity") is True,
                "zero_errors": (
                    pair["go"]["metrics"]["error_jobs"] == 0
                    and pair["python"]["metrics"]["error_jobs"] == 0
                ),
                "go_p99_not_worse": (
                    pair["go"]["metrics"]["end_to_end"]["p99_ms"]
                    <= pair["python"]["metrics"]["end_to_end"]["p99_ms"]
                ),
                "go_steady_rss_not_worse": (
                    pair["go"]["metrics"]["steady_rss_kib"]
                    <= pair["python"]["metrics"]["steady_rss_kib"]
                ),
            }
            for pair in level_pairs
        ]
        hard_pair_correctness = all(
            row["parity"] and row["zero_errors"] for row in paired_resources
        )
        paired_resources_pass = all(
            row["go_p99_not_worse"] and row["go_steady_rss_not_worse"] for row in paired_resources
        )
        threshold_components_pass = (
            len(level_pairs) >= 5
            and _bootstrap_lcb95(speedups) >= 1.20
            and hard_pair_correctness
            and paired_resources_pass
        )
        decision_eligible = mode == "evidence" and level == "c5"
        levels[level] = {
            "measurement_role": (
                "bounded conservation stress; not offered-arrival or sustainable capacity"
                if level == "overload-c20"
                else (
                    "production-anchor saturated executor throughput"
                    if level == "c5"
                    else "diagnostic saturated executor throughput"
                )
            ),
            "implementations": implementations,
            "paired_go_over_python_speedups": speedups,
            "speedup_median": statistics.median(speedups),
            "speedup_lower_95pct_bootstrap_bound": _bootstrap_lcb95(speedups),
            "paired_resource_checks": paired_resources,
            "hard_pair_correctness_pass": hard_pair_correctness,
            "paired_resources_pass": paired_resources_pass,
            "threshold_components_pass": threshold_components_pass,
            "decision_gate_eligible": decision_eligible,
            "decision_gate_pass": decision_eligible and threshold_components_pass,
        }
    policy = [arm for arm in arms if arm["suite"] == "policy"]
    return {
        "mode": mode,
        "label": (
            "saturated executor throughput; not production throughput, migration ROI, "
            "or fleet savings"
        ),
        "hard_correctness_gate": all(pair["parity"] for pair in pairs),
        "production_anchor_c5_decision_gate": bool(
            levels.get("c5", {}).get("decision_gate_pass", False)
        ),
        "planning_headroom_is_not_a_gate": True,
        "levels": levels,
        "policy_runs_reported_separately": len(policy),
        "policy": [
            {
                "implementation": arm["implementation"],
                "repetition": arm["repetition"],
                "jobs": [
                    {
                        "scenario": job["scenario"],
                        "outcome": job["outcome"],
                        "root_attempts": job["root_attempts"],
                        "root_retry_intervals": job["root_retry_intervals"],
                        "retry_schedule_ms": job["retry_schedule_ms"],
                    }
                    for job in arm["enriched_jobs"]
                ],
            }
            for arm in policy
        ],
    }


def _report(summary: dict[str, Any], smoke: bool) -> str:
    lines = [
        "# Concurrent sitemap executor benchmark",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "This output measures saturated executor throughput only. It is not production throughput, "
        "migration ROI, crawler-wide readiness, infrastructure sizing, or fleet-cost evidence.",
        "",
        f"Mode: {'smoke verification (not evidence)' if smoke else 'evidence'}.",
        f"Hard correctness/request-conservation gate: `{summary['hard_correctness_gate']}`.",
        "",
        "| Step | Go jobs/s | Python jobs/s | Go/Python median | lower 95% bound | "
        "decision gate (c5 evidence only) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for level, row in summary["levels"].items():
        go = row["implementations"]["go"]
        python = row["implementations"]["python"]
        lines.append(
            f"| {level} | {go['saturated_successful_jobs_per_second_median']:.2f} | "
            f"{python['saturated_successful_jobs_per_second_median']:.2f} | "
            f"{row['speedup_median']:.3f}x | "
            f"{row['speedup_lower_95pct_bootstrap_bound']:.3f}x | "
            f"{row['decision_gate_pass']} |"
        )
    lines.extend(
        [
            "",
            "Retry-recovery and retry-exhaustion jobs are excluded from every capacity rate "
            "and reported only under `summary.json.policy`; Python jitter and Go deterministic "
            "waits are different current policies.",
            "",
            "`overload-c20` is only a bounded conservation stress batch. It is not an "
            "offered-arrival or sustainable-capacity measurement. The reported 85% rate is "
            "planning headroom only and never participates in a gate.",
            "",
            "Only the c5 production anchor can pass the evidence decision gate; c1, c20, c50, "
            "and overload-c20 are diagnostics. Smoke mode makes every decision gate false.",
            "",
            "Raw per-arm, per-job, manifest, source identity, command, resource, and HTTP/1.1 "
            "transcript evidence is retained beside this report.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_status(out: Path, state: str, **extra: Any) -> None:
    record = {"state": state, "updated_at": datetime.now(UTC).isoformat(), **extra}
    (out / "attempt-status.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def _single_docker_inspect(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} inspect must contain exactly one JSON object")
    return value


def _live_mount_attestation() -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        before, after = line.split(" - ", 1)
        fields = before.split()
        filesystem = after.split()
        mountpoint = fields[4].replace("\\040", " ")
        records[mountpoint] = {
            "mount_options": sorted(fields[5].split(",")),
            "filesystem_type": filesystem[0],
            "source": filesystem[1],
            "super_options": sorted(filesystem[2].split(",")),
        }
    return records


def _validate_docker_inspect(
    *,
    base_path: Path,
    image_path: Path,
    container_path: Path,
    derived_image_id: str,
    frozen_base: str,
    live_hostname: str,
    live_cgroup: str,
    live_pid: int,
    executing_arguments: list[str],
    live_mounts: dict[str, dict[str, Any]],
    evidence_commit: str,
) -> dict[str, Any]:
    base = _single_docker_inspect(base_path, "base image")
    image = _single_docker_inspect(image_path, "image")
    container = _single_docker_inspect(container_path, "container")
    labels = image.get("Config", {}).get("Labels") or {}
    container_id = str(container.get("Id", ""))
    short_id = container_id[:12]
    mounts = {mount.get("Destination"): mount for mount in container.get("Mounts", [])}
    expected_mounts = {
        "/source-proof": False,
        "/evidence": True,
        "/sys/fs/cgroup": True,
    }
    mount_mismatches = {
        destination: mounts.get(destination)
        for destination, rw in expected_mounts.items()
        if destination not in mounts or mounts[destination].get("RW") is not rw
    }
    extra_mounts = sorted(set(mounts) - set(expected_mounts))
    base_layers = base.get("RootFS", {}).get("Layers") or []
    derived_layers = image.get("RootFS", {}).get("Layers") or []
    base_repo_digests = base.get("RepoDigests") or []
    frozen_digest = frozen_base.rsplit("@", 1)[-1]
    frozen_repository = frozen_base.split(":", 1)[0]
    base_digest_match = any(
        value == f"{frozen_repository}@{frozen_digest}" for value in base_repo_digests
    )
    live_mount_modes = {
        destination: (
            "rw"
            if "rw" in live_mounts.get(destination, {}).get("mount_options", [])
            else "ro"
            if "ro" in live_mounts.get(destination, {}).get("mount_options", [])
            else "missing"
        )
        for destination in ("/", "/source-proof", "/evidence", "/sys/fs/cgroup")
    }
    mismatches = {
        "base.RepoDigests": (base_digest_match, True),
        "base.RootFS.Layers": (bool(base_layers), True),
        "derived base layer prefix": (derived_layers[: len(base_layers)], base_layers),
        "image.Id": (image.get("Id"), derived_image_id),
        "image.Config.Entrypoint": (image.get("Config", {}).get("Entrypoint"), EVIDENCE_ENTRYPOINT),
        "container.Image": (container.get("Image"), derived_image_id),
        "container.HostConfig.NetworkMode": (
            container.get("HostConfig", {}).get("NetworkMode"),
            "none",
        ),
        "container.HostConfig.ReadonlyRootfs": (
            container.get("HostConfig", {}).get("ReadonlyRootfs"),
            True,
        ),
        "container.HostConfig.Privileged": (
            container.get("HostConfig", {}).get("Privileged"),
            True,
        ),
        "container.HostConfig.CgroupnsMode": (
            container.get("HostConfig", {}).get("CgroupnsMode"),
            "host",
        ),
        "container.Config.Entrypoint": (
            container.get("Config", {}).get("Entrypoint"),
            EVIDENCE_ENTRYPOINT,
        ),
        "container.Config.Cmd": (container.get("Config", {}).get("Cmd"), executing_arguments),
        "live orchestrator PID": (live_pid, 1),
        "container.Config.Hostname": (container.get("Config", {}).get("Hostname"), short_id),
        "live hostname": (live_hostname, short_id),
        "live cgroup container binding": (
            bool(container_id and (container_id in live_cgroup or short_id in live_cgroup)),
            True,
        ),
        "base label": (labels.get("org.opencontainers.image.base.name"), frozen_base),
        "Python attestation label": (
            labels.get("org.jobseek.evidence.python-attestation"),
            str(EVIDENCE_PYTHON_ATTESTATION),
        ),
        "Go binary label": (
            labels.get("org.jobseek.evidence.go-binary"),
            str(EVIDENCE_GO_BINARY),
        ),
        "source root label": (
            labels.get("org.jobseek.evidence.source-root"),
            str(EVIDENCE_SOURCE_ROOT),
        ),
        "evidence commit label": (
            labels.get("org.jobseek.evidence.commit"),
            evidence_commit,
        ),
    }
    if mount_mismatches or extra_mounts:
        mismatches["container mounts"] = (
            {"mismatches": mount_mismatches, "extra": extra_mounts},
            {"mismatches": {}, "extra": []},
        )
    expected_live_mount_modes = {
        "/": "ro",
        "/source-proof": "ro",
        "/evidence": "rw",
        "/sys/fs/cgroup": "rw",
    }
    if live_mount_modes != expected_live_mount_modes:
        mismatches["live mount modes"] = (live_mount_modes, expected_live_mount_modes)
    failed = {key: value for key, value in mismatches.items() if value[0] != value[1]}
    if failed:
        raise RuntimeError(f"Docker runtime inspection mismatch: {failed}")
    return {
        "image_id": image["Id"],
        "image_repo_digests": image.get("RepoDigests") or [],
        "image_rootfs_layers": image.get("RootFS", {}).get("Layers") or [],
        "base_image_id": base.get("Id"),
        "base_repo_digests": base_repo_digests,
        "base_rootfs_layers": base_layers,
        "base_layers_are_exact_derived_prefix": True,
        "layer_ancestry_claim_scope": (
            "Docker inspect layer metadata; not independent cryptographic registry provenance"
        ),
        "image_labels": labels,
        "container_id": container_id,
        "live_hostname": live_hostname,
        "live_cgroup": live_cgroup,
        "live_orchestrator_pid": live_pid,
        "container_command": container.get("Config", {}).get("Cmd"),
        "container_image_id": container["Image"],
        "container_network_mode": container["HostConfig"]["NetworkMode"],
        "container_security_opt": container["HostConfig"].get("SecurityOpt") or [],
        "container_cap_add": container["HostConfig"].get("CapAdd") or [],
        "container_mounts": mounts,
        "live_mounts": {
            destination: live_mounts[destination] for destination in expected_live_mount_modes
        },
        "container_read_only_rootfs": True,
        "container_privileged": True,
        "container_cgroupns_mode": "host",
    }


def _validate_evidence_source_proof(evidence_commit: str) -> dict[str, Any]:
    embedded_repo = _git_root(EVIDENCE_SOURCE_ROOT)
    proof_repo = _git_root(EVIDENCE_SOURCE_PROOF)
    records: dict[str, Any] = {}
    for label, repo in (("embedded", embedded_repo), ("source_proof", proof_repo)):
        clean = _git_cleanliness(repo)
        if clean["status"] or clean["ignored"]:
            raise RuntimeError(f"{label} evidence source contains an overlay: {clean}")
        origin = _run_text(["git", "remote", "get-url", "origin"], repo)
        if origin != "https://github.com/colophon-group/jobseek.git":
            raise RuntimeError(f"{label} source is not the unauthenticated public repository")
        head = _run_text(["git", "rev-parse", "HEAD"], repo)
        if head != evidence_commit:
            raise RuntimeError(f"{label} source HEAD is not the pinned evidence commit")
        manifest = _tree_manifest(repo)
        records[label] = {
            "repository_head": head,
            "pinned_evidence_commit": evidence_commit,
            "origin": origin,
            "repository_clean_including_untracked_and_ignored": True,
            "manifest": manifest,
            "manifest_sha256": _sha256((json.dumps(manifest, sort_keys=True) + "\n").encode()),
        }
    if records["embedded"]["manifest"] != records["source_proof"]["manifest"]:
        raise RuntimeError("embedded build source differs from read-only public source proof")
    return records


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--go-binary", type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument(
        "--python-source-root",
        type=Path,
        default=(PILOT_ROOT.parent.parent / "apps" / "crawler"),
    )
    parser.add_argument("--runner-cgroup")
    parser.add_argument("--evidence-commit")
    parser.add_argument("--derived-image-id")
    parser.add_argument("--docker-base-inspect", type=Path)
    parser.add_argument("--docker-image-inspect", type=Path)
    parser.add_argument("--docker-container-inspect", type=Path)
    parser.add_argument("--levels", default="")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--evidence", action="store_true")
    args = parser.parse_args()
    if args.smoke and args.evidence:
        parser.error("--smoke and --evidence are mutually exclusive")
    if not args.smoke and not args.evidence:
        parser.error("select --smoke or --evidence explicitly")
    if args.smoke and (not args.go_binary or not args.python):
        parser.error("--smoke requires --go-binary and --python")
    if args.evidence and (args.go_binary or args.python):
        parser.error("evidence rejects operator-selected --go-binary and --python")
    if args.evidence and (
        not args.evidence_commit or re.fullmatch(r"[0-9a-f]{40}", args.evidence_commit) is None
    ):
        parser.error("evidence requires a full 40-hex --evidence-commit")
    if args.evidence and args.python_source_root.resolve() != (
        EVIDENCE_SOURCE_ROOT / "apps/crawler"
    ):
        parser.error("evidence rejects an operator-selected Python source root")
    if args.evidence and (
        not args.runner_cgroup
        or re.fullmatch(r"/sys/fs/cgroup/jobseek-7948-runner-[a-z0-9-]+", args.runner_cgroup)
        is None
    ):
        parser.error("evidence requires a dedicated --runner-cgroup child path")
    if args.evidence and (
        not args.derived_image_id
        or re.fullmatch(r"sha256:[0-9a-f]{64}", args.derived_image_id) is None
    ):
        parser.error("--evidence requires a Docker-derived --derived-image-id sha256 digest")
    if args.evidence and (
        not args.docker_base_inspect
        or not args.docker_image_inspect
        or not args.docker_container_inspect
    ):
        parser.error("--evidence requires base, derived image, and container inspect JSON files")
    return args


def main() -> int:
    args = _parse_args()
    corpus = load_corpus(BENCH_ROOT)
    defaults = corpus["defaults"]
    lock_digest = _sha256((BENCH_ROOT / "uv.lock").read_bytes())
    if lock_digest != corpus["source"]["python_lock_sha256"]:
        raise RuntimeError("bench uv.lock does not match the hashed corpus")
    repetitions = args.repetitions or int(defaults["repetitions"])
    ladder = list(corpus["ladder"])
    if args.levels:
        selected = set(args.levels.split(","))
        ladder = [entry for entry in ladder if entry["name"] in selected]
        if {entry["name"] for entry in ladder} != selected:
            raise RuntimeError("unknown concurrency level")
    if args.smoke:
        repetitions = args.repetitions or 1
        ladder = [entry for entry in ladder if entry["name"] in {"c1", "c5"}]
    if args.evidence:
        if repetitions < 5:
            raise RuntimeError("evidence requires at least five repetitions")
        required = {"c1", "c5", "c20", "c50", "overload-c20"}
        if {entry["name"] for entry in ladder} != required:
            raise RuntimeError("evidence requires the complete predeclared ladder")

    out = args.out.resolve()
    if args.evidence and EVIDENCE_OUTPUT_ROOT not in out.parents:
        # /evidence is the only separately mounted writable output path in the
        # checked container configuration.
        raise RuntimeError("evidence output must be beneath /evidence")
    out.mkdir(parents=True, exist_ok=False)
    for relative in ("raw", "raw/stderr", "manifests", "preexec"):
        (out / relative).mkdir(parents=True, exist_ok=True)
    _write_status(out, "running")

    source_proof = (
        _validate_evidence_source_proof(str(args.evidence_commit)) if args.evidence else None
    )
    python_build_attestation = (
        _load_and_validate_python_build_attestation(corpus["source"], BENCH_ROOT / "uv.lock")
        if args.evidence
        else None
    )
    python_source_root = (
        EVIDENCE_SOURCE_ROOT / "apps/crawler" if args.evidence else args.python_source_root
    )
    python_executable = EVIDENCE_PYTHON if args.evidence else Path(os.path.abspath(args.python))
    input_go_binary = EVIDENCE_GO_BINARY if args.evidence else args.go_binary.resolve()
    runner_prefix = [str(EVIDENCE_WRAPPER), str(args.runner_cgroup)] if args.evidence else []
    if args.evidence:
        subprocess.check_call(["/bin/sh", "-n", str(EVIDENCE_WRAPPER)])
        wrapper = EVIDENCE_WRAPPER.read_text(encoding="utf-8")
        if not wrapper.startswith("#!/bin/sh\nset -eu\n") or not wrapper.rstrip().endswith(
            'exec "$@"'
        ):
            raise RuntimeError("fixed evidence cgroup wrapper structure is invalid")

    source_bundle = out / "preexec" / "python-source"
    python_identity = stage_python_sources(
        python_source_root,
        source_bundle,
        args.evidence,
        corpus["source"],
    )
    staged_go_binary = out / "preexec" / "go-runner"
    staged_go_binary.write_bytes(input_go_binary.read_bytes())
    staged_go_binary.chmod(0o755)
    go_identity = go_source_identity(
        staged_go_binary,
        corpus["source"],
        evidence=args.evidence,
        pilot_root=EVIDENCE_PILOT_ROOT if args.evidence else PILOT_ROOT,
        go_command=str(EVIDENCE_GO) if args.evidence else "go",
    )
    if args.evidence and go_identity["repository_head"] != args.evidence_commit:
        raise RuntimeError("Go runner VCS identity does not match the pinned evidence commit")
    go_rebuild = (
        _rebuild_evidence_go_binary(
            source=corpus["source"],
            staged_binary=staged_go_binary,
            rebuilt_binary=out / "preexec" / "go-runner-rebuilt",
        )
        if args.evidence
        else None
    )
    staged_harness = out / "preexec" / "harness"
    staged_harness.mkdir()
    for harness_name in ("python_runner.py", "common.py"):
        data = (BENCH_ROOT / harness_name).read_bytes()
        (staged_harness / harness_name).write_bytes(data)
    (out / "preexec" / "corpus.json").write_bytes((BENCH_ROOT / "corpus.json").read_bytes())
    (out / "preexec" / "corpus.sha256").write_bytes((BENCH_ROOT / "corpus.sha256").read_bytes())
    staged_lock_file = out / "preexec" / "uv.lock"
    staged_lock_file.write_bytes((BENCH_ROOT / "uv.lock").read_bytes())
    harness_identity = {
        name: _sha256((BENCH_ROOT / name).read_bytes())
        for name in (
            "common.py",
            "corpus.json",
            "fixture.py",
            "orchestrator.py",
            "python_runner.py",
            "pyproject.toml",
            "uv.lock",
            "go-runner/main.go",
            "evidence/Dockerfile",
            "evidence/Dockerfile.dockerignore",
            "evidence/enter-runner-cgroup.sh",
            "evidence/write_runtime_attestation.py",
        )
    }
    (out / "harness-identity.json").write_text(
        json.dumps(harness_identity, indent=2, sort_keys=True) + "\n"
    )
    source_identity = {
        "python": python_identity,
        "python_build_attestation": python_build_attestation,
        "go": go_identity,
        "go_deterministic_rebuild": go_rebuild,
        "public_read_only_source_proof": source_proof,
    }
    (out / "source-identity.json").write_text(
        json.dumps(source_identity, indent=2, sort_keys=True) + "\n"
    )
    image_identity = (
        str(python_identity["frozen_image_identity"])
        if args.evidence
        else "local-uncontainerized-smoke"
    )
    docker_inspect = (
        _validate_docker_inspect(
            base_path=args.docker_base_inspect,
            image_path=args.docker_image_inspect,
            container_path=args.docker_container_inspect,
            derived_image_id=str(args.derived_image_id),
            frozen_base=str(python_identity["frozen_image_identity"]),
            live_hostname=Path("/etc/hostname").read_text(encoding="utf-8").strip(),
            live_cgroup=Path("/proc/self/cgroup").read_text(encoding="utf-8"),
            live_pid=os.getpid(),
            executing_arguments=sys.argv[1:],
            live_mounts=_live_mount_attestation(),
            evidence_commit=str(args.evidence_commit),
        )
        if args.evidence
        else None
    )
    if args.evidence:
        (out / "docker-base-inspect.json").write_bytes(args.docker_base_inspect.read_bytes())
        (out / "docker-image-inspect.json").write_bytes(args.docker_image_inspect.read_bytes())
        (out / "docker-container-inspect.json").write_bytes(
            args.docker_container_inspect.read_bytes()
        )
    (out / "image-identity.json").write_text(
        json.dumps(
            {
                "frozen_base_image_claim": image_identity,
                "derived_image_id_operator_recorded": args.derived_image_id,
                "runtime_attestation_pending": True,
                "docker_inspect": docker_inspect,
                "warning": "a supplied image string alone is not proof of the executing runtime",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    orchestrator_network = _network_namespace_attestation(os.getpid())
    if args.evidence:
        _validate_loopback_only_network(orchestrator_network)
    runtime_fingerprints: dict[str, str | None] = {"go": None, "python": None}
    runtime_attestations: dict[str, dict[str, Any]] = {}
    evidence_cgroup_fingerprint: str | None = None

    transcript_path = out / "raw" / "fixture.jsonl"
    arms: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    command_records: list[dict[str, Any]] = []
    scenarios = scenario_map(corpus)
    started = time.monotonic()

    with FixtureFleet(corpus, transcript_path=transcript_path) as fleet:
        origins = fleet.origins

        def run_arm(
            *,
            implementation: str,
            level: str,
            suite: str,
            repetition: int,
            workers: int,
            capacity: int,
            jobs: list[dict[str, Any]],
            measured_batch_id: str,
        ) -> dict[str, Any]:
            nonlocal evidence_cgroup_fingerprint
            per_origin = min(int(defaults["per_origin_concurrency"]), workers)
            arm_token = f"{level}-r{repetition:02d}-{implementation}"
            command = _runner_command(
                implementation=implementation,
                python_executable=python_executable,
                runner_prefix=runner_prefix,
                source_bundle=source_bundle,
                python_identity=python_identity,
                staged_lock_file=staged_lock_file,
                staged_go_binary=staged_go_binary,
                staged_python_runner=staged_harness / "python_runner.py",
                workers=workers,
                capacity=capacity,
                per_origin=per_origin,
                defaults=defaults,
            )
            command_records.append(
                {
                    "arm_token": arm_token,
                    "command": [
                        Path(item).name if index == 0 else item
                        for index, item in enumerate(command)
                    ],
                    "environment_keys": sorted(_safe_child_environment()),
                }
            )
            fleet.state.activate_arm(arm_token)
            runner = RunnerProcess(command, cwd=out / "preexec")
            try:
                _validate_ready(
                    runner.ready,
                    workers=workers,
                    capacity=capacity,
                    per_origin=per_origin,
                    defaults=defaults,
                )
                if implementation == "python":
                    if args.evidence and runner.ready.get("runtime") != "3.13.15":
                        raise RuntimeError("evidence Python runtime must be exactly 3.13.15")
                    observed_sources = {
                        item["relative_path"]: item["sha256"]
                        for item in runner.ready.get("source_modules", [])
                    }
                    expected_sources = {
                        item["relative_path"]: item["sha256"] for item in python_identity["files"]
                    }
                    if observed_sources != expected_sources:
                        raise RuntimeError("Python imported source hashes do not match staging")
                    if runner.ready.get("source_commit") != python_identity["staged_commit"]:
                        raise RuntimeError("Python did not report the staged source commit")
                    if (
                        runner.ready.get("source_identity_sha256")
                        != python_identity["identity_sha256"]
                    ):
                        raise RuntimeError("Python source identity digest mismatch")
                    runtime_fingerprint = _validate_python_ready_environment(
                        runner.ready,
                        corpus["source"],
                        evidence=args.evidence,
                        build_attestation=python_build_attestation,
                    )
                else:
                    runtime_fingerprint = _validate_go_ready(runner.ready, go_identity)
                if runtime_fingerprints[implementation] is None:
                    runtime_fingerprints[implementation] = runtime_fingerprint
                    runtime_attestations[implementation] = {
                        key: runner.ready.get(key)
                        for key in (
                            "runtime",
                            "executable_sha256",
                            "build_info",
                            "lock_sha256",
                            "runtime_package_versions",
                            "absent_runtime_packages",
                            "installed_distributions",
                            "os_release",
                        )
                        if key in runner.ready
                    }
                elif runtime_fingerprints[implementation] != runtime_fingerprint:
                    raise RuntimeError(f"{implementation} runtime attestation changed between arms")

                runner_network = _network_namespace_attestation(int(runner.ready["pid"]))
                if args.evidence:
                    _validate_loopback_only_network(runner_network)
                    if runner_network["fingerprint"] != orchestrator_network["fingerprint"]:
                        raise RuntimeError(
                            "runner and loopback fixture are not in the same network namespace"
                        )
                cgroup = (
                    _validate_evidence_envelope(
                        runner_pid=int(runner.ready["pid"]),
                        process_pid=runner.process.pid,
                        ready=runner.ready,
                        fd_limit=int(defaults["file_descriptor_limit"]),
                        expected_fingerprint=evidence_cgroup_fingerprint,
                    )
                    if args.evidence
                    else _cgroup_snapshot(int(runner.ready["pid"]))
                )
                if args.evidence and evidence_cgroup_fingerprint is None:
                    evidence_cgroup_fingerprint = str(cgroup["fingerprint"])

                warmup_id = f"{level}-r{repetition:02d}-warmup"
                warmup_count = 8 if args.smoke else int(defaults["warmup_jobs"])
                warmup_jobs = make_jobs(
                    corpus, origins, batch_id=warmup_id, suite="capacity", count=warmup_count
                )
                warmup_digest = _write_manifest(out, warmup_id, warmup_jobs)
                fleet.state.register_batch(
                    arm_token=arm_token, batch_id=warmup_id, jobs=warmup_jobs
                )
                warmup_result, warmup_samples = runner.batch(
                    {
                        "action": "batch",
                        "batch_id": warmup_id,
                        "phase": "warmup",
                        "manifest_sha256": warmup_digest,
                        "jobs": warmup_jobs,
                    },
                    timeout=60.0,
                )
                warmup_rows = _wait_for_transcript(
                    fleet,
                    arm_token,
                    warmup_id,
                    sum(job["requests"] for job in warmup_result["jobs"]),
                )
                warmup_connections = _wait_for_connection_bounds(
                    fleet, arm_token, int(defaults["global_idle_connections"])
                )
                warmup_enriched = validate_batch(
                    result=warmup_result,
                    jobs=warmup_jobs,
                    transcript=warmup_rows,
                    connection_snapshot=warmup_connections,
                    samples=warmup_samples,
                    scenarios=scenarios,
                    defaults=defaults,
                    workers=workers,
                    capacity=capacity,
                    implementation=implementation,
                    batch_id=warmup_id,
                    phase="warmup",
                    evidence=args.evidence,
                )

                manifest_digest = _write_manifest(out, measured_batch_id, jobs)
                fleet.state.register_batch(
                    arm_token=arm_token, batch_id=measured_batch_id, jobs=jobs
                )
                result, samples = runner.batch(
                    {
                        "action": "batch",
                        "batch_id": measured_batch_id,
                        "phase": "measured",
                        "manifest_sha256": manifest_digest,
                        "jobs": jobs,
                    },
                    timeout=120.0,
                )
                rows = _wait_for_transcript(
                    fleet,
                    arm_token,
                    measured_batch_id,
                    sum(job["requests"] for job in result["jobs"]),
                )
                if any(row["batch_id"] == warmup_id for row in rows):
                    raise RuntimeError("warmup fixture state leaked into measured batch")
                connections = _wait_for_connection_bounds(
                    fleet, arm_token, int(defaults["global_idle_connections"])
                )
                enriched = validate_batch(
                    result=result,
                    jobs=jobs,
                    transcript=rows,
                    connection_snapshot=connections,
                    samples=samples,
                    scenarios=scenarios,
                    defaults=defaults,
                    workers=workers,
                    capacity=capacity,
                    implementation=implementation,
                    batch_id=measured_batch_id,
                    phase="measured",
                    evidence=args.evidence,
                )
                cgroup_after = (
                    _validate_evidence_envelope(
                        runner_pid=int(runner.ready["pid"]),
                        process_pid=runner.process.pid,
                        ready=runner.ready,
                        fd_limit=int(defaults["file_descriptor_limit"]),
                        expected_fingerprint=evidence_cgroup_fingerprint,
                    )
                    if args.evidence
                    else _cgroup_snapshot(int(runner.ready["pid"]))
                )
                ooms = (
                    max(
                        0,
                        _memory_event(cgroup_after, "oom_kill") - _memory_event(cgroup, "oom_kill"),
                    )
                    if args.evidence
                    else 0
                )
                if ooms:
                    raise RuntimeError("runner cgroup recorded an OOM kill")
                shutdown = runner.close()
                _wait_for_arm_connections_closed(fleet, arm_token)
                cgroup_post_shutdown = (
                    _validate_empty_cgroup(str(cgroup["path"]), str(evidence_cgroup_fingerprint))
                    if args.evidence
                    else None
                )
            except Exception:
                runner.terminate()
                with contextlib.suppress(RuntimeError):
                    fleet.state.deactivate_arm(arm_token)
                raise

            fleet.state.deactivate_arm(arm_token)

            stderr_path = out / "raw" / "stderr" / f"{arm_token}.log"
            stderr_path.write_text(shutdown["stderr"])
            arm = {
                "arm_token": arm_token,
                "implementation": implementation,
                "level": level,
                "suite": suite,
                "repetition": repetition,
                "workers": workers,
                "capacity": capacity,
                "order_sequence": len(arms) + 1,
                "startup_ns": runner.startup_ns,
                "ready": runner.ready,
                "cgroup_before": cgroup,
                "cgroup_after": cgroup_after,
                "cgroup_post_shutdown": cgroup_post_shutdown,
                "network_namespace": runner_network,
                "source_identity": source_identity,
                "image_identity": image_identity,
                "warmup": {
                    "manifest_sha256": warmup_digest,
                    "result": warmup_result,
                    "enriched_jobs": warmup_enriched,
                    "samples": warmup_samples,
                },
                "manifest_sha256": manifest_digest,
                "result": result,
                "enriched_jobs": enriched,
                "samples": samples,
                "connections": connections,
                "crashes": 0,
                "ooms": ooms,
                "shutdown_returncode": shutdown["returncode"],
                "stderr_file": str(stderr_path.relative_to(out)),
            }
            arm["metrics"] = arm_metrics(arm)
            with (out / "raw" / "arms.jsonl").open("a") as handle:
                handle.write(json.dumps(arm, sort_keys=True) + "\n")
            with (out / "raw" / "jobs.jsonl").open("a") as handle:
                for job in enriched:
                    handle.write(
                        json.dumps(
                            {
                                "arm_token": arm_token,
                                "level": level,
                                "suite": suite,
                                "repetition": repetition,
                                **job,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
            arms.append(arm)
            return arm

        for level_index, entry in enumerate(ladder):
            level = str(entry["name"])
            measurement_suite = "stress" if entry["kind"] == "overload" else "capacity"
            workers = int(entry["workers"])
            capacity = workers * int(entry["capacity_multiplier"])
            job_count = (
                16
                if args.smoke
                else int(
                    defaults["overload_jobs" if entry["kind"] == "overload" else "capacity_jobs"]
                )
            )
            for repetition in range(repetitions):
                batch_id = f"{level}-r{repetition:02d}-measured"
                jobs = make_jobs(
                    corpus, origins, batch_id=batch_id, suite="capacity", count=job_count
                )
                order = (
                    IMPLEMENTATIONS
                    if (level_index + repetition) % 2 == 0
                    else tuple(reversed(IMPLEMENTATIONS))
                )
                pair: dict[str, Any] = {
                    "level": level,
                    "suite": measurement_suite,
                    "repetition": repetition,
                    "order": list(order),
                }
                for implementation in order:
                    pair[implementation] = run_arm(
                        implementation=implementation,
                        level=level,
                        suite=measurement_suite,
                        repetition=repetition,
                        workers=workers,
                        capacity=capacity,
                        jobs=jobs,
                        measured_batch_id=batch_id,
                    )
                validate_pair(pair["go"], pair["python"])
                pair["parity"] = True
                pairs.append(pair)

        policy_repetitions = repetitions
        for repetition in range(policy_repetitions):
            level = "policy-c1"
            batch_id = f"policy-r{repetition:02d}-measured"
            jobs = make_jobs(corpus, origins, batch_id=batch_id, suite="policy", count=2)
            order = IMPLEMENTATIONS if repetition % 2 == 0 else tuple(reversed(IMPLEMENTATIONS))
            pair = {
                "level": level,
                "suite": "policy",
                "repetition": repetition,
                "order": list(order),
            }
            for implementation in order:
                pair[implementation] = run_arm(
                    implementation=implementation,
                    level=level,
                    suite="policy",
                    repetition=repetition,
                    workers=1,
                    capacity=2,
                    jobs=jobs,
                    measured_batch_id=batch_id,
                )
            validate_pair(pair["go"], pair["python"])
            pair["parity"] = True
            pairs.append(pair)

    summary = summarize(arms, pairs, mode="smoke" if args.smoke else "evidence")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (out / "commands.json").write_text(json.dumps(command_records, indent=2, sort_keys=True) + "\n")
    environment = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "orchestrator_python": platform.python_version(),
        "fixture_origins": len(origins),
        "fixture_origin_kind": "distinct loopback host:port origin keys",
        "elapsed_seconds": time.monotonic() - started,
        "mode": "smoke" if args.smoke else "evidence",
        "repetitions": repetitions,
        "levels": [entry["name"] for entry in ladder],
        "network_namespace": orchestrator_network,
        "evidence_cgroup_fingerprint": evidence_cgroup_fingerprint,
    }
    (out / "environment.json").write_text(json.dumps(environment, indent=2, sort_keys=True) + "\n")
    (out / "image-identity.json").write_text(
        json.dumps(
            {
                "frozen_base_image_claim": image_identity,
                "derived_image_id_operator_recorded": args.derived_image_id,
                "runtime_attestation_fingerprints": runtime_fingerprints,
                "runtime_attestations": runtime_attestations,
                "docker_inspect": docker_inspect,
                "warning": (
                    "runtime attestations supplement but do not cryptographically prove "
                    "the supplied image string"
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (out / "report.md").write_text(_report(summary, args.smoke))
    _write_status(
        out,
        "complete",
        arms=len(arms),
        pairs=len(pairs),
        hard_correctness_gate=summary["hard_correctness_gate"],
        elapsed_seconds=environment["elapsed_seconds"],
    )
    print(
        json.dumps(
            {"out": str(out), "summary": summary, "environment": environment}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # The output directory may not exist if argument validation failed.
        candidate = next(
            (
                Path(sys.argv[index + 1]).resolve()
                for index, value in enumerate(sys.argv[:-1])
                if value == "--out"
            ),
            None,
        )
        if candidate is not None and candidate.is_dir():
            _write_status(candidate, "failed", error_type=type(exc).__name__, error=str(exc))
        raise
