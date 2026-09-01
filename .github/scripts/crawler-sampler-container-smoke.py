#!/usr/bin/env python3
"""Exercise the real crawler sampler and shutdown lifecycle in built images.

This is a host-side, standard-library-only probe. It deliberately starts each
image with its unmodified entrypoint and command, inspects processes from
inside the private PID/cgroup namespace, and retains only credential-free
facts in its JSON evidence artifact.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import FrameType
from typing import Any, Final
from unittest import mock

READINESS_TIMEOUT_SECONDS: Final = 30.0
GRACEFUL_STOP_TIMEOUT_SECONDS: Final = 20.0
FORCED_STOP_TIMEOUT_SECONDS: Final = 5.0
ORPHAN_TIMEOUT_SECONDS: Final = 5.0
SAMPLER_INTERVAL_SECONDS: Final = 0.5
ROOT_RSS_ABSOLUTE_TOLERANCE_BYTES: Final = 16 * 1024 * 1024
SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTAINER_ID_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
URI_USERINFO_PATTERN: Final = re.compile(
    r"(?P<prefix>\b[a-z][a-z0-9+.-]*://)[^/\s@]+@",
    re.IGNORECASE,
)
AUTHORIZATION_PATTERN: Final = re.compile(
    r"(?P<prefix>\bauthorization(?:['\"])?\s*[:=]\s*(?:['\"])?\s*)"
    r"(?:(?P<scheme>bearer|basic)(?P<spacing>\s+))?"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
AUTH_SCHEME_PATTERN: Final = re.compile(
    r"\b(?P<scheme>bearer|basic)(?P<spacing>\s+)(?P<value>[A-Za-z0-9._~+/=-]+)",
    re.IGNORECASE,
)
SENSITIVE_KEY_PATTERN: Final = (
    r"[A-Za-z0-9_.-]*(?:password|token|secret|key|database_url|redis_url)"
    r"[A-Za-z0-9_.-]*"
)
SENSITIVE_ASSIGNMENT_PREFIX_PATTERN: Final = re.compile(
    rf"\b{SENSITIVE_KEY_PATTERN}(?:['\"])?\s*(?P<operator>[:=])\s*",
    re.IGNORECASE,
)
SHELL_CONTROL_OPERATOR_STARTS: Final = frozenset(";&|()<>")

REVISION_LABEL: Final = "org.opencontainers.image.revision"
SOURCE_LABEL: Final = "org.opencontainers.image.source"
PR_HEAD_LABEL: Final = "com.colophon-group.jobseek.pr-head"
BASE_LABEL: Final = "com.colophon-group.jobseek.base"
EVIDENCE_LABELS: Final = (
    REVISION_LABEL,
    SOURCE_LABEL,
    PR_HEAD_LABEL,
    BASE_LABEL,
)

INSIDE_PROCESS_SNAPSHOT: Final = r"""
import json
import os


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None


def parse_stat(raw):
    close = raw.rfind(")")
    if close < 0:
        raise ValueError("malformed proc stat")
    fields = raw[close + 2 :].split()
    if len(fields) < 22:
        raise ValueError("short proc stat")
    return {
        "state": fields[0],
        "ppid": int(fields[1]),
        "cpu_ticks": int(fields[11]) + int(fields[12]),
        "start_time_ticks": int(fields[19]),
        "rss_pages": int(fields[21]),
    }


clock_ticks = os.sysconf("SC_CLK_TCK")
page_size = os.sysconf("SC_PAGE_SIZE")
self_pid = os.getpid()
processes = []
for entry in os.scandir("/proc"):
    if not entry.name.isdecimal():
        continue
    pid = int(entry.name)
    raw_stat = read_text(f"/proc/{pid}/stat")
    raw_cmdline = None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            raw_cmdline = handle.read()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
    cgroup = read_text(f"/proc/{pid}/cgroup")
    comm = read_text(f"/proc/{pid}/comm")
    if raw_stat is None or raw_cmdline is None or cgroup is None or comm is None:
        continue
    try:
        stat = parse_stat(raw_stat)
    except (ValueError, IndexError):
        continue
    argv = [
        value.decode("utf-8", errors="replace")
        for value in raw_cmdline.rstrip(b"\0").split(b"\0")
        if value
    ]
    processes.append(
        {
            "pid": pid,
            "ppid": stat["ppid"],
            "argv": argv,
            "comm": comm.strip(),
            "cgroup": cgroup.strip(),
            "state": stat["state"],
            "start_time_ticks": stat["start_time_ticks"],
            "cpu_seconds": stat["cpu_ticks"] / clock_ticks,
            "rss_bytes": stat["rss_pages"] * page_size,
        }
    )

print(
    json.dumps(
        {
            "self_pid": self_pid,
            "clock_ticks_per_second": clock_ticks,
            "cgroup_v2": os.path.isfile("/sys/fs/cgroup/cgroup.controllers"),
            "processes": sorted(processes, key=lambda process: process["pid"]),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
)
"""


class ProbeError(RuntimeError):
    """A fail-closed lifecycle contract violation."""


class ProbeInterrupted(BaseException):
    """A handled runner cancellation that must traverse lifecycle cleanup."""


class _SignalController:
    """Turn the first SIGINT/SIGTERM into a cleanup-aware interruption."""

    def __init__(self) -> None:
        self.received: str | None = None
        self._previous: dict[int, Any] = {}
        self._cleanup_started = False

    def install(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle)

    def restore(self) -> None:
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)
        self._previous.clear()

    def begin_cleanup(self) -> None:
        self._cleanup_started = True

    def _handle(self, signum: int, _frame: FrameType | None) -> None:
        if self.received is not None:
            return
        self.received = signal.Signals(signum).name
        if self._cleanup_started:
            return
        raise ProbeInterrupted(f"received {self.received}")


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeError(message)


def _redact_sensitive_assignments(value: str) -> tuple[str, dict[str, int]]:
    """Mask complete shell words and line-bounded structured values."""

    pieces: list[str] = []
    replacement_counts: dict[str, int] = {}
    cursor = 0
    while match := SENSITIVE_ASSIGNMENT_PREFIX_PATTERN.search(value, cursor):
        value_start = match.end()
        pieces.append(value[cursor:value_start])
        initial_quote = (
            value[value_start]
            if value_start < len(value) and value[value_start] in {'"', "'"}
            else None
        )
        category = "quoted_assignment" if initial_quote is not None else "assignment"
        structured = match.group("operator") == ":"
        quote: str | None = None
        escaped = False
        continuation_breaks: list[str] = []
        value_end = value_start
        while value_end < len(value):
            character = value[value_end]
            if escaped:
                escaped = False
                if character in "\r\n":
                    if structured:
                        break
                    if character == "\r" and value[value_end : value_end + 2] == "\r\n":
                        continuation_breaks.append("\r\n")
                        value_end += 2
                    else:
                        continuation_breaks.append(character)
                        value_end += 1
                    continue
                value_end += 1
                continue
            # An unterminated diagnostic value must not swallow later log lines.
            if character in "\r\n":
                break
            if character == "\\":
                escaped = True
                value_end += 1
                continue
            if quote is not None:
                if character == quote:
                    quote = None
                value_end += 1
                continue
            if character in {'"', "'"}:
                quote = character
                value_end += 1
                continue
            if character.isspace():
                break
            if structured and character in ",;]}":
                break
            if not structured and character in SHELL_CONTROL_OPERATOR_STARTS:
                break
            value_end += 1

        if structured and initial_quote is not None:
            closing_quote = initial_quote if quote is None else ""
            replacement = f"{initial_quote}[REDACTED]{closing_quote}"
        else:
            replacement = "[REDACTED]"
        pieces.append(replacement + "".join(continuation_breaks))

        replacement_counts[category] = replacement_counts.get(category, 0) + 1
        cursor = value_end

    pieces.append(value[cursor:])
    return "".join(pieces), replacement_counts


def _redact_text(value: str) -> tuple[str, dict[str, Any]]:
    """Mask credential forms before diagnostic text enters durable evidence."""

    replacement_counts: dict[str, int] = {}

    def apply(
        name: str,
        pattern: re.Pattern[str],
        replacement: Any,
        text: str,
    ) -> str:
        redacted, count = pattern.subn(replacement, text)
        if count:
            replacement_counts[name] = replacement_counts.get(name, 0) + count
        return redacted

    redacted, assignment_counts = _redact_sensitive_assignments(value)
    replacement_counts.update(assignment_counts)
    redacted = apply(
        "authorization",
        AUTHORIZATION_PATTERN,
        lambda match: (
            f"{match.group('prefix')}"
            f"{(match.group('scheme') + match.group('spacing')) if match.group('scheme') else ''}"
            "[REDACTED]"
        ),
        redacted,
    )
    redacted = apply(
        "auth_scheme",
        AUTH_SCHEME_PATTERN,
        lambda match: f"{match.group('scheme')}{match.group('spacing')}[REDACTED]",
        redacted,
    )
    redacted = apply(
        "uri_userinfo",
        URI_USERINFO_PATTERN,
        lambda match: f"{match.group('prefix')}[REDACTED]@",
        redacted,
    )
    return redacted, {
        "total_replacements": sum(replacement_counts.values()),
        "replacement_counts": replacement_counts,
    }


def _merge_redaction_counts(target: dict[str, int], source: Mapping[str, Any]) -> None:
    counts = source.get("replacement_counts")
    if not isinstance(counts, dict):
        return
    for name, count in counts.items():
        if isinstance(name, str) and isinstance(count, int):
            target[name] = target.get(name, 0) + count


def _redact_diagnostic_value(value: Any) -> tuple[Any, dict[str, Any]]:
    replacement_counts: dict[str, int] = {}

    def redact(item: Any) -> Any:
        if isinstance(item, str):
            redacted, metadata = _redact_text(item)
            _merge_redaction_counts(replacement_counts, metadata)
            return redacted
        if isinstance(item, list):
            return [redact(child) for child in item]
        if isinstance(item, dict):
            return {key: redact(child) for key, child in item.items()}
        return item

    redacted = redact(value)
    return redacted, {
        "total_replacements": sum(replacement_counts.values()),
        "replacement_counts": replacement_counts,
    }


def _typed_redacted_error(exc: BaseException) -> dict[str, str]:
    message, _metadata = _redact_text(str(exc))
    return {"type": type(exc).__name__, "message": message[:500]}


def _redacted_log_stream(value: str) -> dict[str, Any]:
    redacted, metadata = _redact_text(value)
    return {
        "text": redacted,
        "raw_byte_count": len(value.encode("utf-8", errors="replace")),
        "redacted_byte_count": len(redacted.encode("utf-8", errors="replace")),
        "line_count": len(value.splitlines()),
        "redaction": metadata,
    }


def _run(
    command: Sequence[str],
    *,
    description: str,
    timeout: float = 30.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"{description} exceeded {timeout:g}s") from exc
    except OSError as exc:
        raise ProbeError(
            f"{description} could not start: {type(exc).__name__}"
        ) from exc
    if check and result.returncode != 0:
        raise ProbeError(f"{description} failed with exit status {result.returncode}")
    return result


def _atomic_write_json(path: Path, evidence: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _parse_proc_stat(raw: str) -> tuple[int, int]:
    close = raw.rfind(")")
    if close < 0:
        raise ProbeError("host proc stat is malformed")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        raise ProbeError("host proc stat is truncated")
    return int(fields[1]), int(fields[19])


def _host_start_time_ticks(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    _parent_pid, start_time_ticks = _parse_proc_stat(raw)
    return start_time_ticks


def _host_namespace_pid(pid: int) -> int | None:
    try:
        lines = (
            Path(f"/proc/{pid}/status")
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
        )
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    for line in lines:
        if line.startswith("NSpid:"):
            values = line.split()[1:]
            return int(values[-1]) if values else None
    return None


def _parse_docker_top(raw: str) -> list[tuple[int, int, str]]:
    rows: list[tuple[int, int, str]] = []
    for line in raw.splitlines()[1:]:
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        try:
            rows.append((int(fields[0]), int(fields[1]), fields[2]))
        except ValueError:
            continue
    return rows


def _record_host_processes(container_name: str) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 2.0
    last_problem = "docker top did not return a stable process set"
    while (remaining := deadline - time.monotonic()) > 0:
        result = _run(
            ["docker", "top", container_name, "-eo", "pid,ppid,args"],
            description="record container process identities",
            timeout=remaining,
        )
        rows = _parse_docker_top(result.stdout)
        identities: list[dict[str, Any]] = []
        for pid, parent_pid, argv in rows:
            start_time_ticks = _host_start_time_ticks(pid)
            container_pid = _host_namespace_pid(pid)
            if start_time_ticks is None or container_pid is None:
                last_problem = f"host process {pid} changed during identity capture"
                break
            identities.append(
                {
                    "host_pid": pid,
                    "host_ppid": parent_pid,
                    "container_pid": container_pid,
                    "argv": argv,
                    "start_time_ticks": start_time_ticks,
                }
            )
        else:
            _require(bool(identities), "container process identity set is empty")
            return sorted(identities, key=lambda identity: identity["host_pid"])
        time.sleep(0.05)
    raise ProbeError(last_problem)


def _identity_alive(identity: Mapping[str, Any]) -> bool:
    current = _host_start_time_ticks(int(identity["host_pid"]))
    return current == int(identity["start_time_ticks"])


def _wait_for_identities_gone(
    identities: Sequence[Mapping[str, Any]],
    *,
    timeout: float = ORPHAN_TIMEOUT_SECONDS,
) -> tuple[float, list[dict[str, Any]]]:
    started = time.monotonic()
    deadline = started + timeout
    while True:
        survivors = [
            dict(identity) for identity in identities if _identity_alive(identity)
        ]
        if not survivors:
            return time.monotonic() - started, []
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return time.monotonic() - started, survivors
        time.sleep(min(0.05, remaining))


def _load_single_json(
    result: subprocess.CompletedProcess[str], description: str
) -> Any:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"{description} returned malformed JSON") from exc


def _inspect_image(
    image: str,
    *,
    tested_build_sha: str,
    pr_head_sha: str,
    base_sha: str,
    source_url: str,
) -> dict[str, Any]:
    result = _run(
        ["docker", "image", "inspect", image],
        description=f"inspect image {image}",
    )
    decoded = _load_single_json(result, f"inspect image {image}")
    _require(
        isinstance(decoded, list) and len(decoded) == 1, "image inspect was ambiguous"
    )
    raw = decoded[0]
    _require(isinstance(raw, dict), "image inspect record is invalid")
    config = raw.get("Config")
    _require(isinstance(config, dict), "image config is missing")
    labels = config.get("Labels")
    _require(isinstance(labels, dict), "image labels are missing")
    selected_labels = {name: labels.get(name) for name in EVIDENCE_LABELS}
    expected_labels = {
        REVISION_LABEL: tested_build_sha,
        SOURCE_LABEL: source_url,
        PR_HEAD_LABEL: pr_head_sha,
        BASE_LABEL: base_sha,
    }
    _require(
        selected_labels == expected_labels,
        "image provenance labels do not match inputs",
    )
    image_id = raw.get("Id")
    _require(
        isinstance(image_id, str) and IMAGE_ID_PATTERN.fullmatch(image_id) is not None,
        "image content ID is not an exact sha256 digest",
    )
    _require(raw.get("Os") == "linux", "image OS is not linux")
    _require(raw.get("Architecture") == "amd64", "image architecture is not amd64")
    created = raw.get("Created")
    _require(
        isinstance(created, str) and bool(created), "image creation time is missing"
    )
    return {
        "reference": image,
        "content_id": image_id,
        "platform": "linux/amd64",
        "created": created,
        "labels": selected_labels,
        "tested_build_sha": tested_build_sha,
        "pr_head_sha": pr_head_sha,
        "base_sha": base_sha,
    }


def _inspect_container(container_name: str) -> dict[str, Any]:
    result = _run(
        ["docker", "inspect", container_name],
        description=f"inspect container {container_name}",
        timeout=10.0,
    )
    decoded = _load_single_json(result, f"inspect container {container_name}")
    _require(
        isinstance(decoded, list)
        and len(decoded) == 1
        and isinstance(decoded[0], dict),
        "container inspect was invalid",
    )
    raw = decoded[0]
    state = raw.get("State")
    host_config = raw.get("HostConfig")
    _require(isinstance(state, dict), "container state is missing")
    _require(isinstance(host_config, dict), "container host configuration is missing")
    return {
        "id": raw.get("Id"),
        "image_content_id": raw.get("Image"),
        "path": raw.get("Path"),
        "args": raw.get("Args"),
        "state": {
            "status": state.get("Status"),
            "running": state.get("Running"),
            "paused": state.get("Paused"),
            "restarting": state.get("Restarting"),
            "dead": state.get("Dead"),
            "pid": state.get("Pid"),
            "exit_code": state.get("ExitCode"),
            "oom_killed": state.get("OOMKilled"),
            "error": state.get("Error"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
        },
        "runtime": {
            "init": host_config.get("Init"),
            "cgroupns_mode": host_config.get("CgroupnsMode"),
            "network_mode": host_config.get("NetworkMode"),
            "read_only_rootfs": host_config.get("ReadonlyRootfs"),
            "stop_timeout_seconds": host_config.get("StopTimeout"),
            "tmpfs_tmp": (host_config.get("Tmpfs") or {}).get("/tmp"),
        },
    }


def _validate_container_runtime(container: Mapping[str, Any], image_id: str) -> None:
    state = container["state"]
    runtime = container["runtime"]
    _require(state["running"] is True, "container exited before live assertions")
    _require(container["image_content_id"] == image_id, "container image ID changed")
    _require(runtime["init"] is True, "container was not created with Docker init")
    _require(
        runtime["cgroupns_mode"] == "private",
        "container cgroup namespace is not private",
    )
    _require(runtime["network_mode"] == "host", "container network mode is not host")
    _require(
        runtime["read_only_rootfs"] is True, "container root filesystem is writable"
    )
    _require(
        runtime["stop_timeout_seconds"] == 30,
        "container stop timeout is not 30 seconds",
    )
    tmpfs = runtime["tmpfs_tmp"]
    _require(isinstance(tmpfs, str), "container /tmp tmpfs is missing")
    for option in ("rw", "noexec", "nosuid", "nodev"):
        _require(option in tmpfs.split(","), f"container /tmp tmpfs lacks {option}")
    _require(
        "size=64m" in tmpfs or "size=67108864" in tmpfs,
        "container /tmp tmpfs is not bounded to 64 MiB",
    )


def _inside_snapshot(container_name: str, *, timeout: float = 5.0) -> dict[str, Any]:
    result = _run(
        [
            "docker",
            "exec",
            container_name,
            "/app/.venv/bin/python",
            "-I",
            "-c",
            INSIDE_PROCESS_SNAPSHOT,
        ],
        description="inspect processes inside crawler container",
        timeout=timeout,
    )
    decoded = _load_single_json(result, "inside process snapshot")
    _require(isinstance(decoded, dict), "inside process snapshot is invalid")
    return decoded


def _is_crawler_argv(argv: Sequence[str], expected_role: str) -> bool:
    return (
        len(argv) == 3
        and Path(argv[0]).name.startswith("python")
        and Path(argv[1]).name == "crawler"
        and argv[2] == expected_role
    )


def _is_launcher_argv(argv: Sequence[str], expected_role: str) -> bool:
    return (
        len(argv) == 5
        and Path(argv[0]).name == "uv"
        and list(argv[1:]) == ["run", "--no-sync", "crawler", expected_role]
    )


def _is_sampler_argv(argv: Sequence[str]) -> bool:
    command = " ".join(argv)
    return "multiprocessing.spawn" in command and "--multiprocessing-fork" in argv


def _validate_inside_snapshot(
    snapshot: Mapping[str, Any], expected_role: str
) -> dict[str, Any]:
    _require(
        snapshot.get("cgroup_v2") is True, "hosted container does not expose cgroup v2"
    )
    processes = snapshot.get("processes")
    if not isinstance(processes, list):
        raise ProbeError("inside process list is missing")
    by_pid = {
        process.get("pid"): process
        for process in processes
        if isinstance(process, dict) and isinstance(process.get("pid"), int)
    }
    pid1 = by_pid.get(1)
    if pid1 is None:
        raise ProbeError("container PID 1 is missing")
    _require(pid1.get("comm") == "docker-init", "container PID 1 is not docker-init")

    launcher_candidates = [
        process
        for pid, process in by_pid.items()
        if pid != 1
        and pid != snapshot.get("self_pid")
        and isinstance(process.get("argv"), list)
        and _is_launcher_argv(process["argv"], expected_role)
    ]
    _require(
        len(launcher_candidates) == 1,
        "expected exactly one default uv crawler launcher command",
    )
    launcher = launcher_candidates[0]
    _require(
        launcher.get("ppid") == 1,
        "crawler launcher is not a direct child of Docker init",
    )

    crawler_candidates = [
        process
        for pid, process in by_pid.items()
        if pid != 1
        and pid != snapshot.get("self_pid")
        and isinstance(process.get("argv"), list)
        and _is_crawler_argv(process["argv"], expected_role)
    ]
    _require(
        len(crawler_candidates) == 1, "expected exactly one real crawler root command"
    )
    crawler = crawler_candidates[0]
    _require(
        crawler.get("ppid") == launcher.get("pid"),
        "crawler root is not a direct child of its default uv launcher",
    )

    sampler_candidates = [
        process
        for pid, process in by_pid.items()
        if pid != snapshot.get("self_pid")
        and isinstance(process.get("argv"), list)
        and _is_sampler_argv(process["argv"])
    ]
    _require(
        len(sampler_candidates) == 1,
        "expected exactly one multiprocessing sampler child",
    )
    sampler = sampler_candidates[0]
    _require(
        sampler.get("ppid") == crawler.get("pid"),
        "sampler parent is not the crawler root",
    )

    xvfb_candidates = [
        process
        for pid, process in by_pid.items()
        if pid != snapshot.get("self_pid") and process.get("comm") == "Xvfb"
    ]
    if expected_role == "run-browser":
        _require(len(xvfb_candidates) == 1, "full image did not start exactly one Xvfb")
        _require(
            xvfb_candidates[0].get("ppid") == launcher.get("pid"),
            "Xvfb is not a direct child of the full-image launcher",
        )
    else:
        _require(not xvfb_candidates, "slim image unexpectedly started Xvfb")

    cgroup_processes = [pid1, launcher, crawler, sampler, *xvfb_candidates]
    cgroups = [process.get("cgroup") for process in cgroup_processes]
    _require(len(set(cgroups)) == 1, "container lifecycle process cgroups differ")
    _require(
        cgroups[0] == "0::/",
        "processes are not at the private cgroup-v2 namespace root",
    )

    return {
        "pid1": pid1,
        "launcher": launcher,
        "crawler": crawler,
        "sampler": sampler,
        "xvfb": xvfb_candidates[0] if xvfb_candidates else None,
        "cgroup_v2": True,
        "common_cgroup": cgroups[0],
    }


def _parse_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for match in re.finditer(r'(\w+)="((?:\\.|[^"\\])*)"(?:,|$)', raw):
        labels[match.group(1)] = bytes(match.group(2), "utf-8").decode("unicode_escape")
    return labels


def _parse_prometheus(raw: str) -> list[tuple[str, dict[str, str], float]]:
    samples: list[tuple[str, dict[str, str], float]] = []
    pattern = re.compile(
        r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+"
        r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|NaN|Inf|-Inf)"
        r"(?:\s+\d+)?$"
    )
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        match = pattern.fullmatch(line.strip())
        if match is None:
            continue
        labels = _parse_labels(match.group("labels") or "")
        samples.append((match.group("name"), labels, float(match.group("value"))))
    return samples


def _metric_value(
    samples: Sequence[tuple[str, Mapping[str, str], float]],
    name: str,
    labels: Mapping[str, str] | None = None,
) -> float:
    expected_labels = dict(labels or {})
    values = [
        value
        for metric, actual_labels, value in samples
        if metric == name and actual_labels == expected_labels
    ]
    _require(len(values) == 1, f"metric {name} with {expected_labels} is not unique")
    _require(math.isfinite(values[0]), f"metric {name} is not finite")
    return values[0]


def _fetch_metrics(port: int, *, timeout: float = 1.0) -> str:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/metrics",
        headers={"User-Agent": "jobseek-crawler-sampler-smoke/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            _require(
                response.status == 200,
                f"metrics endpoint returned HTTP {response.status}",
            )
            return response.read(4 * 1024 * 1024).decode("utf-8", errors="strict")
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        raise ProbeError("metrics endpoint is not ready") from exc


def _validate_metrics(raw: str) -> dict[str, Any]:
    samples = _parse_prometheus(raw)
    success = _metric_value(
        samples,
        "crawler_runtime_process_tree_samples_total",
        {"outcome": "success"},
    )
    failure = _metric_value(
        samples,
        "crawler_runtime_process_tree_samples_total",
        {"outcome": "failure"},
    )
    starts = _metric_value(samples, "crawler_runtime_process_tree_sampler_starts_total")
    gaps = _metric_value(samples, "crawler_runtime_process_tree_sampling_gaps_total")
    gap_reasons = {
        reason: _metric_value(
            samples,
            "crawler_runtime_process_tree_sampling_gap_reasons_total",
            {"reason": reason},
        )
        for reason in ("scheduler_late", "collection_overrun")
    }
    interval = _metric_value(
        samples, "crawler_runtime_process_tree_sample_interval_seconds"
    )
    root_cpu = _metric_value(samples, "crawler_runtime_process_root_cpu_seconds_total")
    tree_cpu = _metric_value(samples, "crawler_runtime_process_tree_cpu_seconds_total")
    root_rss = _metric_value(
        samples, "crawler_runtime_process_root_resident_memory_bytes"
    )
    tree_rss = _metric_value(
        samples, "crawler_runtime_process_tree_resident_memory_bytes"
    )
    descendants = _metric_value(samples, "crawler_runtime_process_tree_descendants")

    components = ("root_cpu", "tree_cpu", "root_rss", "tree_rss", "descendants")
    sequences = {
        component: _metric_value(
            samples,
            "crawler_runtime_process_tree_observation_sequence",
            {"component": component},
        )
        for component in components
    }
    observed_at = {
        component: _metric_value(
            samples,
            "crawler_runtime_process_tree_observation_unixtime_seconds",
            {"component": component},
        )
        for component in components
    }

    _require(
        success >= 1 and success.is_integer(), "sampler has no successful observation"
    )
    _require(failure == 0, "sampler reported a failed observation")
    _require(starts == 1, "sampler start count is not exactly one")
    _require(gaps == 0, "sampler reported an initial sampling gap")
    _require(
        all(value == 0 for value in gap_reasons.values()),
        "sampler gap reason is non-zero",
    )
    _require(
        interval == SAMPLER_INTERVAL_SECONDS, "sampler interval is not 0.5 seconds"
    )
    _require(root_cpu > 0 and tree_cpu > 0, "sampled CPU fields are not positive")
    _require(root_rss > 0 and tree_rss > 0, "sampled RSS fields are not positive")
    _require(tree_cpu >= root_cpu, "tree CPU is below root CPU")
    _require(tree_rss >= root_rss, "tree RSS is below root RSS")
    _require(
        descendants >= 1 and descendants.is_integer(), "descendant count is below one"
    )
    _require(
        len(set(sequences.values())) == 1,
        "observation sequence components are incoherent",
    )
    sequence = next(iter(sequences.values()))
    _require(
        sequence > 0 and sequence.is_integer(), "observation sequence is not positive"
    )
    _require(
        sequence == success, "observation sequence does not match successful samples"
    )
    _require(
        len(set(observed_at.values())) == 1, "observation timestamps are incoherent"
    )
    observation_time = next(iter(observed_at.values()))
    _require(observation_time > 0, "observation timestamp is not positive")

    return {
        "successful_samples": int(success),
        "failed_samples": int(failure),
        "sampler_starts": int(starts),
        "sampling_gaps": int(gaps),
        "gap_reasons": {key: int(value) for key, value in gap_reasons.items()},
        "interval_seconds": interval,
        "observation_sequence": int(sequence),
        "observation_unixtime_seconds": observation_time,
        "root_cpu_seconds": root_cpu,
        "tree_cpu_seconds": tree_cpu,
        "root_rss_bytes": int(root_rss),
        "tree_rss_bytes": int(tree_rss),
        "descendant_count": int(descendants),
    }


def _validate_root_compatibility(
    metrics: Mapping[str, Any], crawler: Mapping[str, Any], clock_ticks: int
) -> dict[str, Any]:
    current_cpu = float(crawler["cpu_seconds"])
    current_rss = int(crawler["rss_bytes"])
    sampled_cpu = float(metrics["root_cpu_seconds"])
    sampled_rss = int(metrics["root_rss_bytes"])
    observation_age = time.time() - float(metrics["observation_unixtime_seconds"])
    tick_tolerance = 1.0 / clock_ticks + 1e-6
    _require(-1.0 <= observation_age <= 5.0, "sample observation is not current")
    _require(
        sampled_cpu <= current_cpu + tick_tolerance,
        "sampled root CPU is ahead of crawler",
    )
    _require(
        current_cpu - sampled_cpu <= max(1.0, observation_age + 0.25),
        "sampled root CPU is incompatible with current crawler proc data",
    )
    rss_tolerance = max(ROOT_RSS_ABSOLUTE_TOLERANCE_BYTES, int(current_rss * 0.25))
    _require(
        abs(sampled_rss - current_rss) <= rss_tolerance,
        "sampled root RSS is incompatible with current crawler proc data",
    )
    return {
        "current_cpu_seconds": current_cpu,
        "current_rss_bytes": current_rss,
        "sample_age_seconds": observation_age,
        "cpu_tick_tolerance_seconds": tick_tolerance,
        "rss_tolerance_bytes": rss_tolerance,
    }


def _wait_for_live_assertions(
    container_name: str,
    *,
    expected_role: str,
    metrics_port: int,
    started_at: float,
) -> tuple[dict[str, Any], float]:
    deadline = started_at + READINESS_TIMEOUT_SECONDS
    last_error = "crawler did not become ready"

    def remaining_timeout(cap: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("live readiness exceeded 30s")
        return min(cap, remaining)

    while time.monotonic() < deadline:
        try:
            first_snapshot = _inside_snapshot(
                container_name, timeout=remaining_timeout(5.0)
            )
            first = _validate_inside_snapshot(first_snapshot, expected_role)
            metrics = _validate_metrics(
                _fetch_metrics(metrics_port, timeout=remaining_timeout(1.0))
            )
            second_snapshot = _inside_snapshot(
                container_name, timeout=remaining_timeout(5.0)
            )
            second = _validate_inside_snapshot(second_snapshot, expected_role)
            _require(
                first["launcher"]["pid"] == second["launcher"]["pid"]
                and first["launcher"]["start_time_ticks"]
                == second["launcher"]["start_time_ticks"],
                "crawler launcher identity changed across the live scrape",
            )
            _require(
                first["crawler"]["pid"] == second["crawler"]["pid"]
                and first["crawler"]["start_time_ticks"]
                == second["crawler"]["start_time_ticks"],
                "crawler identity changed across the live scrape",
            )
            _require(
                first["sampler"]["pid"] == second["sampler"]["pid"]
                and first["sampler"]["start_time_ticks"]
                == second["sampler"]["start_time_ticks"],
                "sampler identity changed across the live scrape",
            )
            clock_ticks = int(second_snapshot["clock_ticks_per_second"])
            compatibility = _validate_root_compatibility(
                metrics, second["crawler"], clock_ticks
            )
            startup_logs = _container_logs(
                container_name, timeout=remaining_timeout(5.0)
            )
            _require(
                "pipeline.starting" in startup_logs,
                "crawler pipeline did not start against the pinned services",
            )
            live = {
                "pid1": second["pid1"],
                "launcher": second["launcher"],
                "crawler": second["crawler"],
                "sampler": second["sampler"],
                "xvfb": second["xvfb"],
                "cgroup_v2": second["cgroup_v2"],
                "common_cgroup": second["common_cgroup"],
                "metrics": metrics,
                "crawler_proc_compatibility": compatibility,
                "startup_log_markers": {"pipeline.starting": True},
            }
            elapsed = time.monotonic() - started_at
            _require(
                elapsed <= READINESS_TIMEOUT_SECONDS,
                "live readiness exceeded 30s",
            )
            return live, elapsed
        except ProbeError as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.2, remaining))
    raise ProbeError(f"live readiness exceeded 30s: {last_error}")


def _docker_run_command(
    *,
    image: str,
    container_name: str,
    role_name: str,
    metrics_port: int,
) -> list[str]:
    environment = {
        "LOCAL_DATABASE_URL": "postgresql://crawler:crawler@127.0.0.1:5432/crawler",
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "CRAWLER_DB_ROLE": role_name,
        "CRAWLER_DB_POOL_MIN": "1",
        "CRAWLER_DB_POOL_MAX": "2",
        "DISCOVERY_CONCURRENCY": "1",
        "MONITOR_CONCURRENCY": "1",
        "SHUTDOWN_GRACE_SECONDS": "2",
        "PROXY_PROVIDER": "none",
        "METRICS_PORT": str(metrics_port),
        "UV_CACHE_DIR": "/tmp/uv-cache",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LOG_LEVEL": "INFO",
    }
    command = [
        "docker",
        "run",
        "--detach",
        "--pull=never",
        "--name",
        container_name,
        "--platform",
        "linux/amd64",
        "--init",
        "--cgroupns=private",
        "--network",
        "host",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--stop-timeout",
        "30",
    ]
    for name, value in environment.items():
        command.extend(["--env", f"{name}={value}"])
    command.append(image)
    return command


def _remove_container(container_name: str, *, force: bool) -> bool:
    command = ["docker", "rm"]
    if force:
        command.append("--force")
    command.append(container_name)
    result = _run(
        command,
        description=f"remove container {container_name}",
        timeout=10.0,
        check=False,
    )
    return result.returncode == 0


def _force_remove_container_name(container_name: str) -> dict[str, Any]:
    """Force-remove one intended name and prove removal or prior absence."""

    started = time.monotonic()
    try:
        result = _run(
            ["docker", "rm", "--force", container_name],
            description=f"force-remove container {container_name}",
            timeout=10.0,
            check=False,
        )
    # Cancellation cleanup must record its outcome rather than escape.
    except BaseException as exc:  # noqa: BLE001
        return {
            "attempted": True,
            "success": False,
            "outcome": "command-error",
            "duration_seconds": time.monotonic() - started,
            "error": _typed_redacted_error(exc),
        }

    output = result.stdout + result.stderr
    if result.returncode == 0:
        return {
            "attempted": True,
            "success": True,
            "outcome": "removed",
            "returncode": result.returncode,
            "duration_seconds": time.monotonic() - started,
        }
    if "No such container" in output or "No such object" in output:
        return {
            "attempted": True,
            "success": True,
            "outcome": "already-absent",
            "returncode": result.returncode,
            "duration_seconds": time.monotonic() - started,
        }
    return {
        "attempted": True,
        "success": False,
        "outcome": "remove-failed",
        "returncode": result.returncode,
        "duration_seconds": time.monotonic() - started,
    }


def _read_container_logs(
    container_name: str, *, timeout: float = 10.0
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["docker", "logs", container_name],
        description=f"read logs for {container_name}",
        timeout=timeout,
        check=False,
    )


def _container_logs(container_name: str, *, timeout: float = 10.0) -> str:
    result = _read_container_logs(container_name, timeout=timeout)
    return result.stdout + result.stderr


def _capture_container_diagnostics(
    container_name: str,
    *,
    phase: str,
) -> dict[str, Any]:
    """Capture redacted inspect state and complete logs before name removal."""

    started = time.monotonic()
    try:
        raw_inspect = _inspect_container(container_name)
        inspect_record, inspect_redaction = _redact_diagnostic_value(raw_inspect)
        if not isinstance(inspect_record, dict):
            raise ProbeError("redacted container inspect record is invalid")
        inspect_capture: dict[str, Any] = {
            "success": True,
            "record": inspect_record,
            "redaction": inspect_redaction,
        }
    # A missing/half-created container must not prevent the independent log read.
    except BaseException as exc:  # noqa: BLE001
        inspect_capture = {
            "success": False,
            "error": _typed_redacted_error(exc),
        }

    try:
        result = _read_container_logs(container_name)
        logs_capture: dict[str, Any] = {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": _redacted_log_stream(result.stdout),
            "stderr": _redacted_log_stream(result.stderr),
        }
        if result.returncode != 0:
            logs_capture["error"] = {
                "type": "ProbeError",
                "message": (
                    f"read container logs failed with exit status {result.returncode}"
                ),
            }
    # Preserve an explicit typed error and zero-length stream facts on capture failure.
    except BaseException as exc:  # noqa: BLE001
        logs_capture = {
            "success": False,
            "stdout": _redacted_log_stream(""),
            "stderr": _redacted_log_stream(""),
            "error": _typed_redacted_error(exc),
        }

    return {
        "phase": phase,
        "captured_at": _utc_now(),
        "duration_seconds": time.monotonic() - started,
        "inspect": inspect_capture,
        "logs": logs_capture,
    }


class _ActiveContainerRegistry:
    """Track intended names and every observed host PID identity until final cleanup."""

    def __init__(
        self,
        evidence: dict[str, Any] | None = None,
        evidence_path: Path | None = None,
    ) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._evidence = evidence
        self._evidence_path = evidence_path

    def _persist(self) -> None:
        if self._evidence is not None and self._evidence_path is not None:
            _atomic_write_json(self._evidence_path, self._evidence)

    def register(self, container_name: str, case: dict[str, Any]) -> None:
        _require(
            container_name not in self._entries, "container name was registered twice"
        )
        self._entries[container_name] = {
            "case": case,
            "identities": {},
        }
        case["cleanup_registration"] = {
            "registered_before_create": True,
            "container_name": container_name,
        }

    def note_identities(
        self, container_name: str, identities: Sequence[Mapping[str, Any]]
    ) -> None:
        entry = self._entries[container_name]
        known: dict[tuple[int, int], dict[str, Any]] = entry["identities"]
        for identity in identities:
            key = (int(identity["host_pid"]), int(identity["start_time_ticks"]))
            known[key] = dict(identity)
        entry["case"]["known_process_identities"] = sorted(
            known.values(), key=lambda identity: identity["host_pid"]
        )

    def capture_best_effort(
        self,
        container_name: str,
        *,
        phase: str,
        propagate_interruption: bool = True,
    ) -> list[dict[str, Any]]:
        entry = self._entries[container_name]
        case: dict[str, Any] = entry["case"]
        started = time.monotonic()
        try:
            identities = _record_host_processes(container_name)
        except ProbeInterrupted:
            if propagate_interruption:
                raise
            identities = []
            outcome: dict[str, Any] = {
                "phase": phase,
                "success": False,
                "duration_seconds": time.monotonic() - started,
                "error": {
                    "type": "ProbeInterrupted",
                    "message": "identity capture interrupted",
                },
            }
        # Best-effort capture must not block the name-driven cleanup attempt.
        except BaseException as exc:  # noqa: BLE001
            identities = []
            outcome = {
                "phase": phase,
                "success": False,
                "duration_seconds": time.monotonic() - started,
                "error": _typed_redacted_error(exc),
            }
        else:
            self.note_identities(container_name, identities)
            outcome = {
                "phase": phase,
                "success": True,
                "duration_seconds": time.monotonic() - started,
                "identity_count": len(identities),
            }
        case.setdefault("identity_capture_attempts", []).append(outcome)
        return identities

    def capture_diagnostics(
        self,
        container_name: str,
        *,
        phase: str,
    ) -> dict[str, Any]:
        case: dict[str, Any] = self._entries[container_name]["case"]
        try:
            capture = _capture_container_diagnostics(container_name, phase=phase)
        # An unexpected diagnostic defect must be typed and cannot skip name removal.
        except BaseException as exc:  # noqa: BLE001
            error = _typed_redacted_error(exc)
            capture = {
                "phase": phase,
                "captured_at": _utc_now(),
                "inspect": {"success": False, "error": error},
                "logs": {
                    "success": False,
                    "stdout": _redacted_log_stream(""),
                    "stderr": _redacted_log_stream(""),
                    "error": error,
                },
            }
        case.setdefault("diagnostic_captures", []).append(capture)
        # Diagnostics must reach partial JSON before any following removal attempt.
        try:
            self._persist()
        # Removal still has to proceed when the evidence medium itself fails.
        except BaseException as exc:  # noqa: BLE001
            capture["persistence_error"] = _typed_redacted_error(exc)
        return capture

    def cleanup_all(self) -> list[str]:
        """Unconditionally remove every registered name and poll known identities."""

        failures: list[str] = []
        for container_name, entry in list(self._entries.items()):
            case: dict[str, Any] = entry["case"]
            self.capture_best_effort(
                container_name,
                phase="outer-finally",
                propagate_interruption=False,
            )
            self.capture_diagnostics(
                container_name,
                phase="outer-finally-before-force-remove",
            )
            removal = _force_remove_container_name(container_name)
            identities = sorted(
                entry["identities"].values(), key=lambda identity: identity["host_pid"]
            )
            try:
                orphan_seconds, survivors = _wait_for_identities_gone(identities)
                orphan_error = None
            # Cleanup must continue for every registered name.
            except BaseException as exc:  # noqa: BLE001
                orphan_seconds = 0.0
                survivors = identities
                orphan_error = _typed_redacted_error(exc)
            success = bool(removal.get("success")) and not survivors
            case["final_cleanup"] = {
                "name_driven": True,
                "forced_removal": removal,
                "known_identity_count": len(identities),
                "orphan_poll_seconds": orphan_seconds,
                "all_known_identities_gone": not survivors,
                "surviving_identities": survivors,
                "success": success,
            }
            if orphan_error is not None:
                case["final_cleanup"]["orphan_poll_error"] = orphan_error
            if not success:
                case["status"] = "failed"
                failures.append(container_name)
            try:
                self._persist()
            except BaseException as exc:  # noqa: BLE001
                case["final_cleanup"]["persistence_error"] = _typed_redacted_error(exc)
                case["status"] = "failed"
                if container_name not in failures:
                    failures.append(container_name)
            del self._entries[container_name]
        return failures


def _send_sigstop(container_name: str, crawler_pid: int) -> None:
    code = "import os,signal,sys; os.kill(int(sys.argv[1]), signal.SIGSTOP)"
    _run(
        [
            "docker",
            "exec",
            container_name,
            "/app/.venv/bin/python",
            "-I",
            "-c",
            code,
            str(crawler_pid),
        ],
        description="send SIGSTOP to exact crawler PID",
        timeout=5.0,
    )


def _run_lifecycle_case(
    *,
    image_key: str,
    image: Mapping[str, Any],
    mode: str,
    expected_role: str,
    metrics_port: int,
    evidence: dict[str, Any],
    evidence_path: Path,
    registry: _ActiveContainerRegistry,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    container_name = f"crawler-sampler-{image_key}-{mode}-{suffix}"
    role_name = f"ci-sampler-{image_key}-{mode}"
    case: dict[str, Any] = {
        "image": image_key,
        "image_reference": image["reference"],
        "image_content_id": image["content_id"],
        "mode": mode,
        "expected_role": expected_role,
        "container_name": container_name,
        "status": "starting",
        "started_at": _utc_now(),
        "timings_seconds": {},
    }
    evidence["runs"].append(case)
    registry.register(container_name, case)
    _atomic_write_json(evidence_path, evidence)
    recorded_identities: list[dict[str, Any]] = []
    try:
        started = time.monotonic()
        result = _run(
            _docker_run_command(
                image=str(image["reference"]),
                container_name=container_name,
                role_name=role_name,
                metrics_port=metrics_port,
            ),
            description=f"start {image_key} {mode} crawler container",
            timeout=15.0,
        )
        container_id = result.stdout.strip()
        _require(
            CONTAINER_ID_PATTERN.fullmatch(container_id) is not None,
            "docker run did not return an exact container ID",
        )
        case["container_id"] = container_id
        _atomic_write_json(evidence_path, evidence)
        registry.capture_best_effort(container_name, phase="post-create")
        _atomic_write_json(evidence_path, evidence)
        raw_container = _inspect_container(container_name)
        container, container_redaction = _redact_diagnostic_value(raw_container)
        if not isinstance(container, dict):
            raise ProbeError("redacted initial container inspect is invalid")
        case["container"] = container
        case["container_inspect_redaction"] = container_redaction
        _atomic_write_json(evidence_path, evidence)
        _validate_container_runtime(container, str(image["content_id"]))
        case["status"] = "waiting-for-live-sample"
        _atomic_write_json(evidence_path, evidence)

        live, readiness_seconds = _wait_for_live_assertions(
            container_name,
            expected_role=expected_role,
            metrics_port=metrics_port,
            started_at=started,
        )
        case["live"] = live
        case["timings_seconds"]["readiness"] = readiness_seconds
        recorded_identities = _record_host_processes(container_name)
        registry.note_identities(container_name, recorded_identities)
        required_container_pids = {
            1,
            int(live["launcher"]["pid"]),
            int(live["crawler"]["pid"]),
            int(live["sampler"]["pid"]),
        }
        if live["xvfb"] is not None:
            required_container_pids.add(int(live["xvfb"]["pid"]))
        recorded_container_pids = {
            int(identity["container_pid"]) for identity in recorded_identities
        }
        _require(
            required_container_pids <= recorded_container_pids,
            "host identity capture omitted PID 1, crawler, or sampler",
        )
        case["processes_before_shutdown"] = recorded_identities
        case["status"] = "stopping"
        _atomic_write_json(evidence_path, evidence)

        if mode == "forced":
            _send_sigstop(container_name, int(live["crawler"]["pid"]))
            case["forced_signal"] = {
                "signal": "SIGSTOP",
                "container_pid": int(live["crawler"]["pid"]),
                "start_time_ticks": int(live["crawler"]["start_time_ticks"]),
            }
            _atomic_write_json(evidence_path, evidence)
            stop_time = "1"
            stop_timeout = FORCED_STOP_TIMEOUT_SECONDS
        else:
            stop_time = "30"
            stop_timeout = GRACEFUL_STOP_TIMEOUT_SECONDS

        stop_started = time.monotonic()
        stop_result = _run(
            ["docker", "stop", f"--time={stop_time}", container_name],
            description=f"{mode} docker stop for {image_key}",
            timeout=stop_timeout,
            check=False,
        )
        stop_seconds = time.monotonic() - stop_started
        _require(
            stop_result.returncode == 0, "docker stop returned a non-zero exit status"
        )
        _require(
            stop_seconds < stop_timeout, f"{mode} docker stop exceeded its strict bound"
        )

        stopped_capture = registry.capture_diagnostics(
            container_name,
            phase=f"{mode}-stopped-before-remove",
        )
        _require(
            "persistence_error" not in stopped_capture,
            "post-stop diagnostics could not be persisted before removal",
        )
        stopped_inspect = stopped_capture["inspect"]
        _require(
            stopped_inspect.get("success") is True,
            "post-stop container inspect capture failed",
        )
        stopped = stopped_inspect.get("record")
        _require(isinstance(stopped, dict), "post-stop container inspect is missing")
        stopped_logs = stopped_capture["logs"]
        _require(
            stopped_logs.get("success") is True,
            "post-stop container log capture failed",
        )
        stdout = stopped_logs.get("stdout")
        stderr = stopped_logs.get("stderr")
        _require(
            isinstance(stdout, dict) and isinstance(stderr, dict),
            "post-stop container log streams are missing",
        )
        logs = str(stdout.get("text", "")) + str(stderr.get("text", ""))
        markers = {
            marker: marker in logs
            for marker in ("pipeline.stopped", "cli.shutting_down", "cli.stopped")
        }
        orphan_seconds, survivors = _wait_for_identities_gone(recorded_identities)
        shutdown = {
            "docker_stop_seconds": stop_seconds,
            "docker_stop_returncode": stop_result.returncode,
            "requested_timeout_seconds": int(stop_time),
            "exit_code": stopped["state"]["exit_code"],
            "oom_killed": stopped["state"]["oom_killed"],
            "log_markers": markers,
            "orphan_poll_seconds": orphan_seconds,
            "all_recorded_identities_gone": not survivors,
            "surviving_identities": survivors,
        }
        case["shutdown"] = shutdown
        case["timings_seconds"]["docker_stop"] = stop_seconds
        case["timings_seconds"]["orphan_poll"] = orphan_seconds
        _require(
            stopped["state"]["running"] is False, "stopped container is still running"
        )
        _require(
            stopped["state"]["status"] == "exited", "container state is not exited"
        )
        _require(stopped["state"]["oom_killed"] is False, "container was OOM-killed")
        _require(not survivors, "recorded container process identity survived shutdown")
        if mode == "graceful":
            _require(
                stopped["state"]["exit_code"] == 0,
                "graceful container exit code is not zero",
            )
            _require(
                all(markers.values()),
                "graceful crawler shutdown log markers are incomplete",
            )
        else:
            _require(
                stopped["state"]["exit_code"] == 137,
                "forced container exit code is not 137",
            )
            _require(
                not markers["cli.stopped"],
                "forced crawler shutdown unexpectedly completed",
            )
        case["container_removed"] = _remove_container(container_name, force=False)
        _require(
            case["container_removed"] is True, "stopped test container was not removed"
        )
        case["status"] = "passed"
        case["completed_at"] = _utc_now()
        _atomic_write_json(evidence_path, evidence)
    except BaseException as exc:
        case["status"] = "failed"
        case["completed_at"] = _utc_now()
        case["error"] = _typed_redacted_error(exc)
        _atomic_write_json(evidence_path, evidence)
        raise


def _append_summary(evidence: Mapping[str, Any], summary_path: str | None) -> None:
    if not summary_path:
        return
    status = evidence.get("status", "unknown")
    lines = [
        "## Crawler sampler container lifecycle",
        "",
        f"Overall: **{status}**",
        "",
        "| Image | Mode | Result | Ready | Stop | Exit | OOM | Orphans |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    runs = evidence.get("runs", [])
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict):
                continue
            timings = run.get("timings_seconds") or {}
            shutdown = run.get("shutdown") or {}
            final_cleanup = run.get("final_cleanup") or {}
            ready = timings.get("readiness", "-")
            stop = timings.get("docker_stop", "-")
            ready_text = f"{ready:.2f}s" if isinstance(ready, (int, float)) else "-"
            stop_text = f"{stop:.2f}s" if isinstance(stop, (int, float)) else "-"
            lines.append(
                "| {image} | {mode} | {status} | {ready} | {stop} | {exit_code} | "
                "{oom} | {orphans} |".format(
                    image=run.get("image", "-"),
                    mode=run.get("mode", "-"),
                    status=run.get("status", "-"),
                    ready=ready_text,
                    stop=stop_text,
                    exit_code=shutdown.get("exit_code", "-"),
                    oom=shutdown.get("oom_killed", "-"),
                    orphans=(
                        "none"
                        if shutdown.get("all_recorded_identities_gone") is True
                        or final_cleanup.get("all_known_identities_gone") is True
                        else "not-proven"
                    ),
                )
            )
    error = evidence.get("error")
    if isinstance(error, dict):
        lines.extend(
            [
                "",
                f"Failure: `{error.get('type', 'error')}` — {error.get('message', '')}",
            ]
        )
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _validate_sha(value: str, field: str) -> str:
    if SHA_PATTERN.fullmatch(value) is None:
        raise ProbeError(f"{field} must be a lowercase 40-character Git SHA")
    return value


def _run_probe(args: argparse.Namespace) -> int:
    evidence_path = Path(args.evidence)
    tested_build_sha = str(args.tested_build_sha)
    pr_head_sha = str(args.pr_head_sha)
    base_sha = str(args.base_sha)
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at": _utc_now(),
        "tested_build_sha": tested_build_sha,
        "pr_head_sha": pr_head_sha,
        "base_sha": base_sha,
        "source_url": args.source_url,
        "images": {},
        "runs": [],
    }
    registry = _ActiveContainerRegistry(evidence, evidence_path)
    signal_controller = _SignalController()
    exit_code = 1
    try:
        signal_controller.install()
        _atomic_write_json(evidence_path, evidence)
        _validate_sha(tested_build_sha, "tested build SHA")
        _validate_sha(pr_head_sha, "PR head SHA")
        _validate_sha(base_sha, "base SHA")
        _require(
            1024 <= args.metrics_port <= 65535,
            "metrics port is outside the unprivileged range",
        )
        images = {
            "slim": _inspect_image(
                args.slim_image,
                tested_build_sha=tested_build_sha,
                pr_head_sha=pr_head_sha,
                base_sha=base_sha,
                source_url=args.source_url,
            ),
            "full": _inspect_image(
                args.full_image,
                tested_build_sha=tested_build_sha,
                pr_head_sha=pr_head_sha,
                base_sha=base_sha,
                source_url=args.source_url,
            ),
        }
        evidence["images"] = images
        _atomic_write_json(evidence_path, evidence)
        for image_key, expected_role in (("slim", "run"), ("full", "run-browser")):
            for mode in ("graceful", "forced"):
                _run_lifecycle_case(
                    image_key=image_key,
                    image=images[image_key],
                    mode=mode,
                    expected_role=expected_role,
                    metrics_port=args.metrics_port,
                    evidence=evidence,
                    evidence_path=evidence_path,
                    registry=registry,
                )
        evidence["status"] = "passed"
        _atomic_write_json(evidence_path, evidence)
        exit_code = 0
    # Persist evidence for both ordinary defects and handled cancellation.
    except BaseException as exc:  # noqa: BLE001
        evidence["status"] = "failed"
        evidence["error"] = _typed_redacted_error(exc)
        _atomic_write_json(evidence_path, evidence)
        print(
            "crawler sampler container smoke failed: "
            f"{evidence['error']['type']}: {evidence['error']['message']}",
            file=sys.stderr,
        )
    finally:
        signal_controller.begin_cleanup()
        try:
            try:
                cleanup_failures = registry.cleanup_all()
            # Final evidence must survive even an unexpected cleanup defect.
            except BaseException as cleanup_exc:  # noqa: BLE001
                cleanup_failures = ["cleanup-registry-error"]
                evidence["cleanup_registry_error"] = _typed_redacted_error(cleanup_exc)
            if cleanup_failures:
                evidence["status"] = "failed"
                evidence["cleanup_failures"] = cleanup_failures
                evidence.setdefault(
                    "error",
                    {
                        "type": "ProbeError",
                        "message": "one or more registered container names failed cleanup",
                    },
                )
                exit_code = 1
            if signal_controller.received is not None:
                evidence["received_signal"] = signal_controller.received
            evidence["completed_at"] = _utc_now()
            _atomic_write_json(evidence_path, evidence)
            _append_summary(evidence, os.environ.get("GITHUB_STEP_SUMMARY"))
        finally:
            signal_controller.restore()
    return exit_code


class _SelfTests(unittest.TestCase):
    def test_proc_stat_handles_parentheses(self) -> None:
        fields = ["S", "7", *(["0"] * 17), "123", "0", "0"]
        parent_pid, start_time = _parse_proc_stat(
            f"9 (crawler worker) {' '.join(fields)}"
        )
        self.assertEqual(parent_pid, 7)
        self.assertEqual(start_time, 123)

    def test_docker_top_parser_preserves_argv(self) -> None:
        rows = _parse_docker_top(
            "PID PPID COMMAND\n101 99 /app/.venv/bin/crawler run-browser --example\n"
        )
        self.assertEqual(
            rows, [(101, 99, "/app/.venv/bin/crawler run-browser --example")]
        )

    def test_crawler_and_sampler_command_classification(self) -> None:
        self.assertTrue(
            _is_crawler_argv(
                [
                    "/app/.venv/bin/python3",
                    "/app/.venv/bin/crawler",
                    "run",
                ],
                "run",
            )
        )
        self.assertFalse(
            _is_crawler_argv(
                [
                    "/app/.venv/bin/python3",
                    "/app/.venv/bin/crawler",
                    "run-browser",
                ],
                "run",
            )
        )
        self.assertTrue(
            _is_launcher_argv(
                ["/bin/uv", "run", "--no-sync", "crawler", "run-browser"],
                "run-browser",
            )
        )
        self.assertTrue(
            _is_sampler_argv(
                [
                    "python",
                    "-c",
                    "from multiprocessing.spawn import spawn_main",
                    "--multiprocessing-fork",
                ]
            )
        )

    def test_prometheus_contract_is_coherent(self) -> None:
        lines = [
            'crawler_runtime_process_tree_samples_total{outcome="success"} 2',
            'crawler_runtime_process_tree_samples_total{outcome="failure"} 0',
            "crawler_runtime_process_tree_sampler_starts_total 1",
            "crawler_runtime_process_tree_sampling_gaps_total 0",
            'crawler_runtime_process_tree_sampling_gap_reasons_total{reason="scheduler_late"} 0',
            'crawler_runtime_process_tree_sampling_gap_reasons_total{reason="collection_overrun"} 0',
            "crawler_runtime_process_tree_sample_interval_seconds 0.5",
            "crawler_runtime_process_root_cpu_seconds_total 0.2",
            "crawler_runtime_process_tree_cpu_seconds_total 0.3",
            "crawler_runtime_process_root_resident_memory_bytes 100",
            "crawler_runtime_process_tree_resident_memory_bytes 200",
            "crawler_runtime_process_tree_descendants 1",
        ]
        for component in (
            "root_cpu",
            "tree_cpu",
            "root_rss",
            "tree_rss",
            "descendants",
        ):
            lines.append(
                "crawler_runtime_process_tree_observation_sequence"
                f'{{component="{component}"}} 2'
            )
            lines.append(
                "crawler_runtime_process_tree_observation_unixtime_seconds"
                f'{{component="{component}"}} 1800000000'
            )
        metrics = _validate_metrics("\n".join(lines))
        self.assertEqual(metrics["observation_sequence"], 2)
        self.assertEqual(metrics["descendant_count"], 1)

    def test_full_image_process_topology(self) -> None:
        def process(
            pid: int,
            parent_pid: int,
            argv: list[str],
            comm: str,
        ) -> dict[str, Any]:
            return {
                "pid": pid,
                "ppid": parent_pid,
                "argv": argv,
                "comm": comm,
                "cgroup": "0::/",
                "start_time_ticks": pid * 10,
                "cpu_seconds": 0.1,
                "rss_bytes": 1024,
            }

        snapshot = {
            "self_pid": 99,
            "cgroup_v2": True,
            "processes": [
                process(1, 0, ["docker-init"], "docker-init"),
                process(
                    2,
                    1,
                    ["uv", "run", "--no-sync", "crawler", "run-browser"],
                    "uv",
                ),
                process(
                    3,
                    2,
                    ["python3", "/app/.venv/bin/crawler", "run-browser"],
                    "python3",
                ),
                process(
                    4,
                    3,
                    [
                        "python3",
                        "-c",
                        "from multiprocessing.spawn import spawn_main",
                        "--multiprocessing-fork",
                    ],
                    "python3",
                ),
                process(5, 2, ["Xvfb", ":99"], "Xvfb"),
            ],
        }
        topology = _validate_inside_snapshot(snapshot, "run-browser")
        self.assertEqual(topology["launcher"]["pid"], 2)
        self.assertEqual(topology["crawler"]["pid"], 3)
        self.assertEqual(topology["sampler"]["pid"], 4)
        self.assertEqual(topology["xvfb"]["pid"], 5)

    def test_docker_run_uses_tmp_uv_cache_without_overrides(self) -> None:
        for image in ("slim:test", "full:test"):
            command = _docker_run_command(
                image=image,
                container_name="fixed-container-name",
                role_name="ci-sampler",
                metrics_port=19091,
            )
            self.assertIn("UV_CACHE_DIR=/tmp/uv-cache", command)
            self.assertNotIn("--entrypoint", command)
            self.assertEqual(command[-1], image)

    def test_credential_redaction_is_escape_aware(self) -> None:
        escaped_left = "escaped-left-fragment"
        escaped_right = "escaped-right-fragment"
        slash_left = "slash-left-fragment"
        slash_right = "slash-right-fragment"
        even_slash_secret = "even-slash-secret"
        multi_password = "multiple-password-secret"
        multi_token = "multiple-token-secret"
        shell_left = "shell-left-fragment"
        shell_right = "shell-right-fragment"
        unicode_secret = "päss-秘密-secret"
        unterminated_secret = "unterminated-line-secret"
        uri_user = "combo-uri-user"
        uri_password = "combo-uri-password"
        bearer_secret = "combo-bearer-secret"
        raw_lines = [
            (
                'escaped-json={"password":"'
                + escaped_left
                + '\\"'
                + escaped_right
                + '","safe":"visible-json"}'
            ),
            (
                'escaped-slashes={"api_token":"'
                + slash_left
                + ("\\" * 3)
                + '"'
                + slash_right
                + '","safe":"visible-slashes"}'
            ),
            (
                'even-slashes={"api_key":"'
                + even_slash_secret
                + ("\\" * 2)
                + '","safe":"visible-even"}'
            ),
            (
                'multiple={"password":"'
                + multi_password
                + '","token":"'
                + multi_token
                + '","safe":"visible-multiple"}'
            ),
            (
                "shell SECRET_KEY='"
                + shell_left
                + "\\'"
                + shell_right
                + "' SAFE_LABEL=visible-shell"
            ),
            f'unicode PASSWORD="{unicode_secret}" safe=visible-unicode',
            f'unterminated PASSWORD="{unterminated_secret}',
            "normal line after unterminated value ✓",
            (
                f"combo=redis://{uri_user}:{uri_password}@localhost/0 "
                f"Authorization: Bearer {bearer_secret}"
            ),
        ]
        raw = "\n".join(raw_lines)

        redacted, metadata = _redact_text(raw)
        serialized = json.dumps(
            {"diagnostics": {"text": redacted, "redaction": metadata}},
            ensure_ascii=False,
        )

        for secret in (
            escaped_left,
            escaped_right,
            slash_left,
            slash_right,
            even_slash_secret,
            multi_password,
            multi_token,
            shell_left,
            shell_right,
            unicode_secret,
            unterminated_secret,
            uri_user,
            uri_password,
            bearer_secret,
        ):
            self.assertNotIn(secret, serialized)
        for useful_evidence in (
            '"safe":"visible-json"',
            '"safe":"visible-slashes"',
            '"safe":"visible-even"',
            '"safe":"visible-multiple"',
            "SAFE_LABEL=visible-shell",
            "safe=visible-unicode",
            "normal line after unterminated value ✓",
            "redis://[REDACTED]@localhost/0 Authorization: Bearer [REDACTED]",
        ):
            self.assertIn(useful_evidence, redacted)
        self.assertGreaterEqual(
            metadata["replacement_counts"]["quoted_assignment"],
            7,
        )

    def test_shell_assignment_redaction_consumes_complete_word(self) -> None:
        adjacent_left = "adjacent-left-A17Q"
        adjacent_right = "adjacent-right-Z93M"
        splice_left = "splice-left-B28R"
        splice_right = "splice-right-Y84N"
        mixed_left = "mixed-left-C39S"
        mixed_middle = "mixed-middle-X75P"
        mixed_right = "mixed-right-W66K"
        escaped_left = "escaped-segment-left-D40T"
        escaped_middle = "escaped-segment-middle-V57J"
        escaped_right = "escaped-segment-right-U48H"
        whitespace_left = "whitespace-left-E51U"
        whitespace_right = "whitespace-right-T39G"
        backslash_tail = "backslash-tail-S20F"
        raw = (
            f"SECRET_KEY='{adjacent_left}'{adjacent_right} "
            "SAFE_LABEL=visible-adjacent SAFE_MODE=enabled-adjacent\n"
            f"SECRET='{splice_left}'\\''{splice_right}' "
            "SAFE_LABEL=visible-splice SAFE_MODE=enabled-splice\n"
            f"SECRET='{mixed_left}'\"{mixed_middle}\"{mixed_right} "
            "SAFE_LABEL=visible-mixed SAFE_MODE=enabled-mixed\n"
            f"SECRET='{escaped_left}'\\\"{escaped_middle}\\\"{escaped_right} "
            "SAFE_LABEL=visible-escaped SAFE_MODE=enabled-escaped\n"
            f"SECRET={whitespace_left}\\ {whitespace_right}\\\\{backslash_tail} "
            "SAFE_LABEL=visible-whitespace SAFE_MODE=enabled-whitespace"
        )

        artifact = _redacted_log_stream(raw)
        serialized = json.dumps(artifact, ensure_ascii=False, sort_keys=True)

        for secret_fragment in (
            adjacent_left,
            adjacent_right,
            splice_left,
            splice_right,
            mixed_left,
            mixed_middle,
            mixed_right,
            escaped_left,
            escaped_middle,
            escaped_right,
            whitespace_left,
            whitespace_right,
            backslash_tail,
        ):
            self.assertNotIn(secret_fragment, serialized)
        for safe_evidence in (
            "SAFE_LABEL=visible-adjacent SAFE_MODE=enabled-adjacent",
            "SAFE_LABEL=visible-splice SAFE_MODE=enabled-splice",
            "SAFE_LABEL=visible-mixed SAFE_MODE=enabled-mixed",
            "SAFE_LABEL=visible-escaped SAFE_MODE=enabled-escaped",
            "SAFE_LABEL=visible-whitespace SAFE_MODE=enabled-whitespace",
        ):
            self.assertIn(safe_evidence, artifact["text"])
        self.assertEqual(artifact["line_count"], 5)
        self.assertEqual(
            artifact["redaction"]["replacement_counts"],
            {"assignment": 1, "quoted_assignment": 4},
        )

    def test_shell_assignment_redaction_honors_continuations_and_controls(
        self,
    ) -> None:
        newline_left = "newline-left-H8"
        newline_right = "newline-right-I9"
        crlf_left = "crlf-left-J10"
        crlf_right = "crlf-right-K11"
        continuation = "\\"
        continuation_raw = (
            f"PASSWORD={newline_left}{continuation}\n{newline_right} "
            "SAFE_LABEL=newline-safe SAFE_MODE=newline-enabled\n"
            f"TOKEN={crlf_left}{continuation}\r\n{crlf_right} "
            "SAFE_LABEL=crlf-safe SAFE_MODE=crlf-enabled"
        )
        continuation_artifact = _redacted_log_stream(continuation_raw)
        continuation_serialized = json.dumps(
            continuation_artifact,
            ensure_ascii=False,
            sort_keys=True,
        )

        for secret_fragment in (
            newline_left,
            newline_right,
            crlf_left,
            crlf_right,
        ):
            self.assertNotIn(secret_fragment, continuation_serialized)
        for safe_evidence in (
            "SAFE_LABEL=newline-safe SAFE_MODE=newline-enabled",
            "SAFE_LABEL=crlf-safe SAFE_MODE=crlf-enabled",
        ):
            self.assertIn(safe_evidence, continuation_artifact["text"])
        self.assertEqual(
            continuation_artifact["text"].count("\n"),
            continuation_raw.count("\n"),
        )
        self.assertEqual(continuation_artifact["text"].count("\r\n"), 1)

        operator_cases = (
            ("semicolon-control-L12", ";", "semicolon"),
            ("and-control-M13", "&&", "and"),
            ("or-control-N14", "||", "or"),
            ("pipe-control-O15", "|", "pipe"),
            ("pipe-background-control-P16", "|&", "pipe-background"),
            ("background-control-Q17", "&", "background"),
            ("left-parenthesis-control-R18", "(", "left-parenthesis"),
            ("right-parenthesis-control-S19", ")", "right-parenthesis"),
            ("redirect-out-control-T20", ">>output.log;", "redirect-out"),
            ("redirect-in-control-U21", "<<input-marker;", "redirect-in"),
            ("redirect-fd-control-V22", ">&2;", "redirect-fd"),
        )
        operator_lines = [
            (
                f"SECRET={secret}{control}SAFE_LABEL={label}-visible;"
                f"SAFE_MODE={label}-enabled"
            )
            for secret, control, label in operator_cases
        ]
        quoted_left = "quoted-control-left-W23"
        quoted_right = "quoted-control-right-X24"
        quoted_tail = "quoted-control-tail-Y25"
        escaped_left = "escaped-control-left-Z26"
        escaped_right = "escaped-control-right-A27"
        operator_lines.extend(
            (
                (
                    f"SECRET='{quoted_left};&|()<>{quoted_right}'{quoted_tail} "
                    "SAFE_LABEL=quoted-control-visible "
                    "SAFE_MODE=quoted-control-enabled"
                ),
                (
                    f"SECRET={escaped_left}\\;\\&\\|\\(\\)\\<\\>{escaped_right} "
                    "SAFE_LABEL=escaped-control-visible "
                    "SAFE_MODE=escaped-control-enabled"
                ),
            )
        )
        operator_artifact = _redacted_log_stream("\n".join(operator_lines))
        operator_serialized = json.dumps(
            operator_artifact,
            ensure_ascii=False,
            sort_keys=True,
        )

        for secret_fragment in (
            *(case[0] for case in operator_cases),
            quoted_left,
            quoted_right,
            quoted_tail,
            escaped_left,
            escaped_right,
        ):
            self.assertNotIn(secret_fragment, operator_serialized)
        for _secret, control, label in operator_cases:
            self.assertIn(
                f"{control}SAFE_LABEL={label}-visible;SAFE_MODE={label}-enabled",
                operator_artifact["text"],
            )
        for safe_evidence in (
            "SAFE_LABEL=quoted-control-visible SAFE_MODE=quoted-control-enabled",
            "SAFE_LABEL=escaped-control-visible SAFE_MODE=escaped-control-enabled",
        ):
            self.assertIn(safe_evidence, operator_artifact["text"])

    def test_early_exit_diagnostics_are_redacted_before_remove(self) -> None:
        container_id = "a" * 64
        image_id = "sha256:" + "b" * 64
        evidence: dict[str, Any] = {"runs": []}
        evidence_snapshots: list[dict[str, Any]] = []
        events: list[str] = []
        raw_inspect = {
            "id": container_id,
            "image_content_id": image_id,
            "path": "/bin/uv",
            "args": ["run", "--no-sync", "crawler", "run"],
            "state": {
                "status": "exited",
                "running": False,
                "paused": False,
                "restarting": False,
                "dead": True,
                "pid": 0,
                "exit_code": 17,
                "oom_killed": False,
                "error": "launcher failed token=state-secret",
                "started_at": "2026-09-01T07:22:17.500000000Z",
                "finished_at": "2026-09-01T07:22:17.600000000Z",
            },
            "runtime": {
                "init": True,
                "cgroupns_mode": "private",
                "network_mode": "host",
                "read_only_rootfs": True,
                "stop_timeout_seconds": 30,
                "tmpfs_tmp": "rw,noexec,nosuid,nodev,size=64m",
            },
        }
        stdout = (
            "normal stdout line one\n"
            "normal stdout line two\n"
            "postgresql://uri-user:uri-pass@localhost/db\n"
            "Authorization: Bearer bearer-secret\n"
            "PASSWORD=password-secret\n"
            "API_TOKEN=token-secret\n"
            "CLIENT_SECRET=client-secret\n"
            "API_KEY=key-secret\n"
            "DATABASE_URL=postgresql://db-user:db-pass@localhost/db\n"
            "REDIS_URL=redis://redis-user:redis-pass@localhost/0\n"
            'config={"password":"json-secret"}\n'
            r'escaped={"password":"evidence-left-fragment\"evidence-right-fragment",'
            r'"safe":"visible-after-escape"}'
        )
        stderr = "Authorization=Basic basic-secret\nnormal stderr line"

        def record_evidence(_path: Path, current: Mapping[str, Any]) -> None:
            evidence_snapshots.append(json.loads(json.dumps(current)))

        def inspect_container(_name: str) -> dict[str, Any]:
            events.append("inspect")
            return raw_inspect

        def read_logs(
            _name: str, *, timeout: float = 10.0
        ) -> subprocess.CompletedProcess[str]:
            del timeout
            events.append("logs")
            return subprocess.CompletedProcess(
                args=["docker", "logs"],
                returncode=0,
                stdout=stdout,
                stderr=stderr,
            )

        def force_remove(_name: str) -> dict[str, Any]:
            events.append("remove")
            return {"attempted": True, "success": True, "outcome": "removed"}

        registry = _ActiveContainerRegistry(evidence, Path("unused.json"))
        with (
            mock.patch(
                __name__ + "._run",
                return_value=subprocess.CompletedProcess(
                    args=["docker", "run"],
                    returncode=0,
                    stdout=f"{container_id}\n",
                    stderr="",
                ),
            ),
            mock.patch(
                __name__ + "._record_host_processes",
                side_effect=ProbeError("injected early docker top failure"),
            ),
            mock.patch(__name__ + "._inspect_container", side_effect=inspect_container),
            mock.patch(__name__ + "._read_container_logs", side_effect=read_logs),
            mock.patch(
                __name__ + "._force_remove_container_name",
                side_effect=force_remove,
            ),
            mock.patch(
                __name__ + "._wait_for_identities_gone",
                return_value=(0.0, []),
            ),
            mock.patch(
                __name__ + "._atomic_write_json",
                side_effect=record_evidence,
            ),
        ):
            with self.assertRaisesRegex(ProbeError, "container exited"):
                _run_lifecycle_case(
                    image_key="slim",
                    image={"reference": "slim:test", "content_id": image_id},
                    mode="graceful",
                    expected_role="run",
                    metrics_port=19091,
                    evidence=evidence,
                    evidence_path=Path("unused.json"),
                    registry=registry,
                )
            self.assertEqual(registry.cleanup_all(), [])

        case = evidence["runs"][0]
        self.assertEqual(case["container_id"], container_id)
        self.assertEqual(
            case["cleanup_registration"]["container_name"], case["container_name"]
        )
        self.assertEqual(case["container"]["id"], container_id)
        self.assertEqual(case["container"]["image_content_id"], image_id)
        self.assertEqual(case["container"]["path"], "/bin/uv")
        self.assertEqual(
            case["container"]["args"], ["run", "--no-sync", "crawler", "run"]
        )
        self.assertEqual(
            case["container"]["state"],
            {
                "status": "exited",
                "running": False,
                "paused": False,
                "restarting": False,
                "dead": True,
                "pid": 0,
                "exit_code": 17,
                "oom_killed": False,
                "error": "launcher failed token=[REDACTED]",
                "started_at": "2026-09-01T07:22:17.500000000Z",
                "finished_at": "2026-09-01T07:22:17.600000000Z",
            },
        )
        capture = case["diagnostic_captures"][-1]
        self.assertTrue(capture["inspect"]["success"])
        self.assertTrue(capture["logs"]["success"])
        self.assertEqual(capture["logs"]["stdout"]["line_count"], 12)
        self.assertEqual(capture["logs"]["stderr"]["line_count"], 2)
        self.assertEqual(
            capture["logs"]["stdout"]["raw_byte_count"],
            len(stdout.encode("utf-8")),
        )
        self.assertEqual(
            capture["logs"]["stderr"]["raw_byte_count"],
            len(stderr.encode("utf-8")),
        )
        redacted_logs = (
            capture["logs"]["stdout"]["text"] + capture["logs"]["stderr"]["text"]
        )
        for line in (
            "normal stdout line one",
            "normal stdout line two",
            "normal stderr line",
            '"safe":"visible-after-escape"',
        ):
            self.assertIn(line, redacted_logs)
        self.assertGreater(
            capture["logs"]["stdout"]["redaction"]["total_replacements"],
            0,
        )
        self.assertTrue(case["final_cleanup"]["forced_removal"]["success"])
        self.assertEqual(case["final_cleanup"]["known_identity_count"], 0)
        remove_index = events.index("remove")
        self.assertIn("inspect", events[:remove_index])
        self.assertIn("logs", events[:remove_index])
        serialized = json.dumps(evidence)
        for secret in (
            "state-secret",
            "uri-user",
            "uri-pass",
            "bearer-secret",
            "basic-secret",
            "password-secret",
            "token-secret",
            "client-secret",
            "key-secret",
            "db-user",
            "db-pass",
            "redis-user",
            "redis-pass",
            "json-secret",
            "evidence-left-fragment",
            "evidence-right-fragment",
        ):
            self.assertNotIn(secret, serialized)
        self.assertTrue(
            any(
                snapshot["runs"][0].get("container_id") == container_id
                and "diagnostic_captures" not in snapshot["runs"][0]
                for snapshot in evidence_snapshots
            )
        )
        self.assertTrue(
            evidence_snapshots[0]["runs"][0]["cleanup_registration"][
                "registered_before_create"
            ]
        )

    def test_diagnostic_inspect_failure_is_typed(self) -> None:
        with (
            mock.patch(
                __name__ + "._inspect_container",
                side_effect=ProbeError("inspect failed token=inspect-secret"),
            ),
            mock.patch(
                __name__ + "._read_container_logs",
                return_value=subprocess.CompletedProcess(
                    args=["docker", "logs"],
                    returncode=0,
                    stdout="available log line\n",
                    stderr="",
                ),
            ),
        ):
            capture = _capture_container_diagnostics(
                "inspect-failed",
                phase="injected-inspect-failure",
            )
        self.assertFalse(capture["inspect"]["success"])
        self.assertEqual(capture["inspect"]["error"]["type"], "ProbeError")
        self.assertNotIn("inspect-secret", json.dumps(capture))
        self.assertTrue(capture["logs"]["success"])

    def test_diagnostic_log_failure_is_typed(self) -> None:
        with (
            mock.patch(
                __name__ + "._inspect_container",
                return_value={"id": "c" * 64, "state": {"status": "exited"}},
            ),
            mock.patch(
                __name__ + "._read_container_logs",
                side_effect=ProbeError("logs failed password=log-secret"),
            ),
        ):
            capture = _capture_container_diagnostics(
                "logs-failed",
                phase="injected-log-failure",
            )
        self.assertTrue(capture["inspect"]["success"])
        self.assertFalse(capture["logs"]["success"])
        self.assertEqual(capture["logs"]["error"]["type"], "ProbeError")
        self.assertEqual(capture["logs"]["stdout"]["raw_byte_count"], 0)
        self.assertEqual(capture["logs"]["stderr"]["raw_byte_count"], 0)
        self.assertNotIn("log-secret", json.dumps(capture))

    def test_create_failure_cleanup_is_name_driven(self) -> None:
        registry = _ActiveContainerRegistry()
        evidence: dict[str, Any] = {"runs": []}
        removal = {
            "attempted": True,
            "success": True,
            "outcome": "already-absent",
            "returncode": 1,
        }
        with (
            mock.patch(
                __name__ + "._run",
                side_effect=ProbeError("injected docker create failure"),
            ),
            mock.patch(__name__ + "._atomic_write_json"),
        ):
            with self.assertRaisesRegex(ProbeError, "injected docker create failure"):
                _run_lifecycle_case(
                    image_key="slim",
                    image={"reference": "slim:test", "content_id": "sha256:slim"},
                    mode="graceful",
                    expected_role="run",
                    metrics_port=19091,
                    evidence=evidence,
                    evidence_path=Path("unused.json"),
                    registry=registry,
                )

        case = evidence["runs"][0]
        container_name = case["container_name"]
        self.assertTrue(case["cleanup_registration"]["registered_before_create"])
        with (
            mock.patch(
                __name__ + "._record_host_processes",
                side_effect=ProbeError("container name is absent"),
            ),
            mock.patch(
                __name__ + "._force_remove_container_name",
                return_value=removal,
            ) as remove,
            mock.patch(
                __name__ + "._capture_container_diagnostics",
                return_value={
                    "inspect": {"success": False},
                    "logs": {"success": False},
                },
            ),
            mock.patch(
                __name__ + "._wait_for_identities_gone",
                return_value=(0.0, []),
            ),
        ):
            failures = registry.cleanup_all()
        self.assertEqual(failures, [])
        remove.assert_called_once_with(container_name)
        self.assertTrue(case["final_cleanup"]["success"])

    def test_readiness_failure_cleanup_polls_early_identity(self) -> None:
        registry = _ActiveContainerRegistry()
        evidence: dict[str, Any] = {"runs": []}
        identity = {
            "host_pid": 123,
            "host_ppid": 1,
            "container_pid": 3,
            "argv": "python crawler",
            "start_time_ticks": 456,
        }
        removal = {"attempted": True, "success": True, "outcome": "removed"}
        with (
            mock.patch(
                __name__ + "._run",
                return_value=subprocess.CompletedProcess(
                    args=["docker", "run"],
                    returncode=0,
                    stdout=f"{'a' * 64}\n",
                    stderr="",
                ),
            ),
            mock.patch(
                __name__ + "._record_host_processes",
                return_value=[identity],
            ),
            mock.patch(__name__ + "._inspect_container", return_value={}),
            mock.patch(__name__ + "._validate_container_runtime"),
            mock.patch(
                __name__ + "._wait_for_live_assertions",
                side_effect=ProbeError("injected readiness failure"),
            ),
            mock.patch(__name__ + "._atomic_write_json"),
        ):
            with self.assertRaisesRegex(ProbeError, "injected readiness failure"):
                _run_lifecycle_case(
                    image_key="full",
                    image={"reference": "full:test", "content_id": "sha256:full"},
                    mode="graceful",
                    expected_role="run-browser",
                    metrics_port=19091,
                    evidence=evidence,
                    evidence_path=Path("unused.json"),
                    registry=registry,
                )

        case = evidence["runs"][0]
        with (
            mock.patch(
                __name__ + "._record_host_processes",
                return_value=[identity],
            ),
            mock.patch(
                __name__ + "._force_remove_container_name",
                return_value=removal,
            ),
            mock.patch(
                __name__ + "._capture_container_diagnostics",
                return_value={"inspect": {"success": True}, "logs": {"success": True}},
            ),
            mock.patch(
                __name__ + "._wait_for_identities_gone",
                return_value=(0.1, []),
            ) as orphan_poll,
        ):
            failures = registry.cleanup_all()
        self.assertEqual(failures, [])
        orphan_poll.assert_called_once_with([identity])
        self.assertEqual(case["final_cleanup"]["known_identity_count"], 1)
        self.assertTrue(case["final_cleanup"]["all_known_identities_gone"])

    def test_signal_interruption_is_idempotent_for_cleanup(self) -> None:
        handlers: dict[int, Any] = {}
        evidence_snapshots: list[dict[str, Any]] = []

        def remember_handler(signum: int, handler: Any) -> None:
            if callable(handler):
                handlers[signum] = handler

        def interrupt_case(**kwargs: Any) -> None:
            case = {
                "image": kwargs["image_key"],
                "mode": kwargs["mode"],
                "status": "starting",
                "container_name": "signal-interrupted",
            }
            kwargs["evidence"]["runs"].append(case)
            kwargs["registry"].register("signal-interrupted", case)
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        def record_evidence(_path: Path, evidence: Mapping[str, Any]) -> None:
            evidence_snapshots.append(json.loads(json.dumps(evidence)))

        args = argparse.Namespace(
            evidence="unused.json",
            tested_build_sha="1" * 40,
            pr_head_sha="2" * 40,
            base_sha="3" * 40,
            source_url="https://github.com/example/repository",
            slim_image="slim:test",
            full_image="full:test",
            metrics_port=19091,
        )
        with (
            mock.patch(__name__ + ".signal.getsignal", return_value=signal.SIG_DFL),
            mock.patch(__name__ + ".signal.signal", side_effect=remember_handler),
            mock.patch(
                __name__ + "._inspect_image",
                side_effect=lambda image, **_kwargs: {
                    "reference": image,
                    "content_id": f"sha256:{image}",
                },
            ),
            mock.patch(__name__ + "._run_lifecycle_case", side_effect=interrupt_case),
            mock.patch(
                __name__ + "._record_host_processes",
                side_effect=ProbeError("container is already absent"),
            ),
            mock.patch(
                __name__ + "._force_remove_container_name",
                return_value={"attempted": True, "success": True, "outcome": "removed"},
            ) as remove,
            mock.patch(
                __name__ + "._capture_container_diagnostics",
                return_value={
                    "inspect": {"success": False},
                    "logs": {"success": False},
                },
            ),
            mock.patch(
                __name__ + "._wait_for_identities_gone",
                return_value=(0.0, []),
            ),
            mock.patch(__name__ + "._atomic_write_json", side_effect=record_evidence),
            mock.patch(__name__ + "._append_summary") as append_summary,
        ):
            self.assertEqual(_run_probe(args), 1)

        remove.assert_called_once_with("signal-interrupted")
        append_summary.assert_called_once()
        final_evidence = evidence_snapshots[-1]
        self.assertEqual(final_evidence["status"], "failed")
        self.assertEqual(final_evidence["received_signal"], "SIGTERM")
        self.assertTrue(final_evidence["runs"][0]["final_cleanup"]["success"])
        self.assertIn("completed_at", final_evidence)

        cleanup_controller = _SignalController()
        cleanup_controller.begin_cleanup()
        cleanup_controller._handle(signal.SIGINT, None)
        cleanup_controller._handle(signal.SIGTERM, None)
        self.assertEqual(cleanup_controller.received, "SIGINT")

    def test_cleanup_failure_fails_closed(self) -> None:
        registry = _ActiveContainerRegistry()
        case: dict[str, Any] = {"status": "failed"}
        identity = {
            "host_pid": 123,
            "host_ppid": 1,
            "container_pid": 3,
            "argv": "python crawler",
            "start_time_ticks": 456,
        }
        registry.register("cleanup-failed", case)
        registry.note_identities("cleanup-failed", [identity])
        removal = {
            "attempted": True,
            "success": False,
            "outcome": "remove-failed",
            "returncode": 1,
        }
        with (
            mock.patch(
                __name__ + "._record_host_processes",
                return_value=[identity],
            ),
            mock.patch(
                __name__ + "._force_remove_container_name",
                return_value=removal,
            ),
            mock.patch(
                __name__ + "._capture_container_diagnostics",
                return_value={"inspect": {"success": True}, "logs": {"success": True}},
            ),
            mock.patch(
                __name__ + "._wait_for_identities_gone",
                return_value=(ORPHAN_TIMEOUT_SECONDS, [identity]),
            ),
        ):
            failures = registry.cleanup_all()
        self.assertEqual(failures, ["cleanup-failed"])
        self.assertEqual(case["status"], "failed")
        self.assertFalse(case["final_cleanup"]["success"])
        self.assertEqual(case["final_cleanup"]["surviving_identities"], [identity])


def _run_self_tests() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(_SelfTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--slim-image")
    parser.add_argument("--full-image")
    parser.add_argument("--tested-build-sha")
    parser.add_argument("--pr-head-sha")
    parser.add_argument("--base-sha")
    parser.add_argument("--source-url")
    parser.add_argument("--metrics-port", type=int, default=19091)
    parser.add_argument("--evidence")
    args = parser.parse_args(argv)
    if args.self_test:
        return args
    for name in (
        "slim_image",
        "full_image",
        "tested_build_sha",
        "pr_head_sha",
        "base_sha",
        "source_url",
        "evidence",
    ):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test:
        return _run_self_tests()
    return _run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
