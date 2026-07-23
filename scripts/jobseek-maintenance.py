#!/usr/bin/env python3
"""Run bounded production maintenance with validated Docker provenance."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
INSTALLED_MODULE_DIR = Path("/usr/local/lib/jobseek-maintenance")
for module_dir in (SCRIPT_DIR, INSTALLED_MODULE_DIR):
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))

from jobseek_maintenance_provenance import (  # noqa: E402
    BUDGET_LABEL,
    ISSUE_LABEL,
    MAX_BUDGET_SECONDS,
    MIN_BUDGET_SECONDS,
    OPERATION_LABEL,
    OPERATION_RE,
    REVISION_LABEL,
    REVISION_RE,
    docker_label_arguments,
)

DEFAULT_EXPECTED_SERVICES = (
    "redis",
    "worker-1",
    "worker-2",
    "worker-3",
    "browser-1",
    "exporter",
    "drain",
    "alloy",
)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
RESERVED_LABEL_PREFIXES = (
    "com.docker.compose.",
    "jobseek.maintenance.",
)
MUTATION_LOCK = Path("/run/lock/jobseek-crawler-mutation.lock")


class MaintenanceError(RuntimeError):
    """The bounded maintenance contract was invalid or failed closed."""


class _TerminationRequested(Exception):
    def __init__(self, signal_number: int) -> None:
        self.signal_number = signal_number


@dataclass(frozen=True)
class Provenance:
    operation: str
    issue: int
    revision: str
    budget_seconds: int


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    timed_out: bool
    forced_termination: bool
    duration_seconds: float


def _validate_provenance(args: argparse.Namespace) -> Provenance:
    operation = str(args.operation)
    issue = int(args.issue)
    revision = str(args.revision)
    budget_seconds = int(args.budget_seconds)
    if not OPERATION_RE.fullmatch(operation):
        raise MaintenanceError("operation must be a lowercase maintenance slug")
    if issue <= 0:
        raise MaintenanceError("issue must be a positive GitHub issue number")
    if not REVISION_RE.fullmatch(revision):
        raise MaintenanceError("revision must be a full lowercase Git commit SHA")
    if not MIN_BUDGET_SECONDS <= budget_seconds <= MAX_BUDGET_SECONDS:
        raise MaintenanceError(
            f"budget must be between {MIN_BUDGET_SECONDS} and {MAX_BUDGET_SECONDS} seconds"
        )
    return Provenance(operation, issue, revision, budget_seconds)


def _label_value(command: Sequence[str], index: int) -> tuple[str | None, int]:
    token = command[index]
    if token in {"--label", "-l"}:
        return (command[index + 1] if index + 1 < len(command) else None, 2)
    for prefix in ("--label=", "-l="):
        if token.startswith(prefix):
            return token[len(prefix) :], 1
    return None, 1


def _reject_reserved_labels(command: Sequence[str]) -> None:
    index = 0
    while index < len(command):
        label, consumed = _label_value(command, index)
        if label is not None:
            key = label.split("=", 1)[0]
            if key.startswith(RESERVED_LABEL_PREFIXES):
                raise MaintenanceError("reserved Compose and maintenance labels are wrapper-owned")
        index += consumed


def _docker_run_name(command: Sequence[str]) -> str:
    if len(command) >= 5 and command[3] == "--name":
        name = command[4]
    elif len(command) >= 4 and command[3].startswith("--name="):
        name = command[3].split("=", 1)[1]
    else:
        raise MaintenanceError("one-off docker run must begin with --rm --name <stable-name>")
    if not NAME_RE.fullmatch(name):
        raise MaintenanceError("one-off Docker name is invalid")
    return name


def build_oneoff_command(command: Sequence[str], provenance: Provenance) -> tuple[list[str], str]:
    """Insert wrapper-owned labels into an explicit named ``docker run``."""
    if len(command) < 3 or list(command[:2]) != ["docker", "run"]:
        raise MaintenanceError("oneoff mode accepts only an explicit docker run command")
    if command[2] != "--rm":
        raise MaintenanceError("one-off docker run must begin with --rm")
    _reject_reserved_labels(command)
    name = _docker_run_name(command)
    labels = docker_label_arguments(
        operation=provenance.operation,
        issue=provenance.issue,
        revision=provenance.revision,
        budget_seconds=provenance.budget_seconds,
        compose_service=provenance.operation,
    )
    return [*command[:2], *labels, *command[2:]], name


def _run_capture(command: Sequence[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MaintenanceError(f"Docker control command failed: {type(exc).__name__}") from exc


@contextmanager
def _mutation_lock(timeout_seconds: int):
    try:
        lock_file = MUTATION_LOCK.open("a+", encoding="utf-8")
    except OSError as exc:
        raise MaintenanceError(f"maintenance lock unavailable: {type(exc).__name__}") from exc
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MaintenanceError(
                        "timed out waiting for the crawler mutation lock"
                    ) from None
                time.sleep(min(1, max(0.01, deadline - time.monotonic())))
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    grace_seconds: int,
) -> bool:
    forced = False
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return forced
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        forced = True
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    return forced


def _run_bounded(
    command: Sequence[str],
    *,
    budget_seconds: int,
    grace_seconds: int,
) -> CommandResult:
    started = time.monotonic()
    try:
        process = subprocess.Popen(list(command), start_new_session=True)
    except OSError as exc:
        raise MaintenanceError(
            f"maintenance command failed to start: {type(exc).__name__}"
        ) from exc
    timed_out = False
    forced = False
    returncode: int
    try:
        returncode = process.wait(timeout=budget_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        forced = _terminate_process_group(process, grace_seconds=grace_seconds)
        returncode = 124
    except _TerminationRequested as exc:
        forced = _terminate_process_group(process, grace_seconds=grace_seconds)
        returncode = 128 + exc.signal_number
    return CommandResult(
        returncode=returncode,
        timed_out=timed_out,
        forced_termination=forced,
        duration_seconds=time.monotonic() - started,
    )


def _cleanup_container(name: str) -> None:
    _run_capture(["docker", "rm", "-f", name], timeout=30)


def _service_is_ready(service: str) -> bool:
    listing = _run_capture(
        [
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.project=deploy",
            "--filter",
            f"label=com.docker.compose.service={service}",
            "--format",
            "{{.ID}}",
        ]
    )
    container_ids = [line for line in listing.stdout.splitlines() if line]
    if listing.returncode != 0 or len(container_ids) != 1:
        return False
    inspect = _run_capture(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            container_ids[0],
        ]
    )
    if inspect.returncode != 0:
        return False
    return inspect.stdout.strip() in {"running none", "running healthy"}


def _restoration_failures(expected_services: Sequence[str]) -> list[str]:
    return sorted(service for service in expected_services if not _service_is_ready(service))


def _marker_image() -> str:
    result = _run_capture(["docker", "inspect", "--format", "{{.Image}}", "deploy-redis-1"])
    image = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise MaintenanceError("the existing Redis image is unavailable for the window marker")
    return image


def _start_window_marker(provenance: Provenance) -> str:
    marker_name = (
        f"jobseek-maintenance-window-{provenance.operation}-{int(time.time())}-{os.getpid()}"
    )
    if not NAME_RE.fullmatch(marker_name):
        raise MaintenanceError("generated maintenance window name is invalid")
    labels = docker_label_arguments(
        operation=provenance.operation,
        issue=provenance.issue,
        revision=provenance.revision,
        budget_seconds=provenance.budget_seconds,
        compose_service="maintenance-window",
    )
    command = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        marker_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "16m",
        "--cpus",
        "0.05",
        "--pids-limit",
        "16",
        *labels,
        _marker_image(),
        "/bin/sh",
        "-c",
        f"trap 'exit 0' TERM INT; sleep {MAX_BUDGET_SECONDS}",
    ]
    result = _run_capture(command)
    if result.returncode != 0:
        raise MaintenanceError("failed to start the maintenance window marker")
    return marker_name


def _stop_window_marker(name: str) -> None:
    _run_capture(["docker", "stop", "--time", "1", name], timeout=10)
    _cleanup_container(name)


def _print_summary(
    provenance: Provenance,
    result: CommandResult,
    restoration_failures: Sequence[str],
) -> None:
    if result.timed_out:
        status = "budget-exceeded"
    elif result.returncode != 0:
        status = "command-failed"
    elif restoration_failures:
        status = "restoration-failed"
    else:
        status = "completed"
    print(
        "maintenance "
        f"operation={provenance.operation} "
        f"issue=#{provenance.issue} "
        f"revision={provenance.revision} "
        f"status={status} "
        f"duration_seconds={result.duration_seconds:.3f} "
        f"forced_termination={str(result.forced_termination).lower()} "
        f"restoration_failures={len(restoration_failures)}"
    )


def _expected_services(args: argparse.Namespace) -> tuple[str, ...]:
    services = tuple(args.expect_service or DEFAULT_EXPECTED_SERVICES)
    if not services or any(not OPERATION_RE.fullmatch(service) for service in services):
        raise MaintenanceError("expected service names must be lowercase slugs")
    return services


def _run_oneoff(args: argparse.Namespace, provenance: Provenance) -> int:
    command, name = build_oneoff_command(args.command, provenance)
    try:
        result = _run_bounded(
            command,
            budget_seconds=provenance.budget_seconds,
            grace_seconds=args.grace_seconds,
        )
    finally:
        _cleanup_container(name)
    failures = _restoration_failures(_expected_services(args))
    _print_summary(provenance, result, failures)
    if result.returncode != 0:
        return result.returncode
    return 70 if failures else 0


def _run_window(args: argparse.Namespace, provenance: Provenance) -> int:
    if not args.command:
        raise MaintenanceError("window mode requires a host command after --")
    marker = _start_window_marker(provenance)
    try:
        result = _run_bounded(
            args.command,
            budget_seconds=provenance.budget_seconds,
            grace_seconds=args.grace_seconds,
        )
        failures = _restoration_failures(_expected_services(args))
    finally:
        _stop_window_marker(marker)
    _print_summary(provenance, result, failures)
    if result.returncode != 0:
        return result.returncode
    return 70 if failures else 0


def _self_test() -> None:
    provenance = Provenance(
        operation="repair-relisted-cdc",
        issue=6016,
        revision="a" * 40,
        budget_seconds=1800,
    )
    command, name = build_oneoff_command(
        ["docker", "run", "--rm", "--name", "repair-relisted", "image", "true"],
        provenance,
    )
    assert name == "repair-relisted"
    joined = "\n".join(command)
    for label in (OPERATION_LABEL, ISSUE_LABEL, REVISION_LABEL, BUDGET_LABEL):
        assert joined.count(label) == 1
    assert "secret" not in joined.lower()


def _add_provenance_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation", required=True)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--budget-seconds", required=True, type=int)
    parser.add_argument("--grace-seconds", type=int, default=90)
    parser.add_argument("--lock-timeout-seconds", type=int, default=7200)
    parser.add_argument("--expect-service", action="append")
    parser.add_argument("command", nargs=argparse.REMAINDER)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="mode")
    _add_provenance_arguments(subparsers.add_parser("oneoff"))
    _add_provenance_arguments(subparsers.add_parser("window"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.self_test:
        _self_test()
        print("maintenance provenance self-test passed")
        return 0
    if args.mode not in {"oneoff", "window"}:
        raise SystemExit("oneoff or window mode is required")
    if not 1 <= args.grace_seconds <= 300:
        raise SystemExit("grace-seconds must be between 1 and 300")
    if not 0 <= args.lock_timeout_seconds <= 7200:
        raise SystemExit("lock-timeout-seconds must be between 0 and 7200")
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]

    previous_handlers: dict[int, signal.Handlers] = {}

    def request_termination(signal_number: int, _frame: object) -> None:
        raise _TerminationRequested(signal_number)

    for signal_number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        previous_handlers[signal_number] = signal.signal(signal_number, request_termination)
    try:
        provenance = _validate_provenance(args)
        with _mutation_lock(args.lock_timeout_seconds):
            return (
                _run_oneoff(args, provenance)
                if args.mode == "oneoff"
                else _run_window(args, provenance)
            )
    except MaintenanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        for signal_number, handler in previous_handlers.items():
            signal.signal(signal_number, handler)


if __name__ == "__main__":
    raise SystemExit(main())
