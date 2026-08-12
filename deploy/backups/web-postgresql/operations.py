#!/usr/bin/env python3
"""Operate the staged web PostgreSQL backup surface with fail-closed evidence."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

STATUS_DIR = Path("/var/lib/jobseek-backup/status")
DEPLOYED_SHA_PATH = Path("/var/lib/jobseek-backup/web-postgresql-deployed-sha")
EVIDENCE_PATH = STATUS_DIR / "web-postgresql-activation.json"
BACKUP_STATUS_PATH = STATUS_DIR / "web-postgresql.json"
RESTORE_STATUS_PATH = STATUS_DIR / "web-postgresql-restore.json"
RESTORE_RUNTIME_ROOT = Path("/run/jobseek-backup/web-postgresql/drills")
RESTORE_LOCK_PATH = Path("/run/jobseek-data-backup-web-postgresql.lock")
DEPLOYMENT_LOCK_PATH = Path("/run/jobseek-backup-deployment.lock")
DEPLOYMENT_LOCK_FD_ENV = "JOBSEEK_BACKUP_DEPLOYMENT_LOCK_FD"
RESTORE_RESOURCE_LABEL = "jobseek.backup.service=web-postgresql-restore"
BACKUP_UNIT = "jobseek-web-postgresql-backup.service"
TIMER_UNIT = "jobseek-web-postgresql-backup.timer"
BACKUP_ENV_PATH = Path("/etc/jobseek-backup/web-postgresql.env")
DATABASE_CREDENTIAL_PATH = Path("/etc/jobseek-backup/web-postgresql.database-url")
RESTORE_IMAGE = (
    "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
)
HELPER_IMAGE_LEASE = "jobseek-web-postgresql-backup-image-lease"
HELPER_IMAGE_LEASE_LABEL = "jobseek.backup.helper-image"
HELPER_IMAGE_LEASE_TMPFS = {
    "/var/lib/postgresql/data": "rw,noexec,nosuid,nodev,size=65536"
}
BACKUP_FAILURE_ERROR_LIMIT = 512
BACKUP_FAILURE_DIAGNOSTIC_LIMIT = 768
_BACKUP_FAILURE_URI = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s'\"<>]+")
_BACKUP_FAILURE_AUTHORITY = re.compile(r"(?i)\b[^\s:@/]+:[^\s@/]+@[a-z0-9.-]+")
_BACKUP_FAILURE_AUTHORIZATION = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*(?:bearer|basic)\s+[^\s,;]+"
)
_BACKUP_FAILURE_SECRET = re.compile(
    r"(?i)\b(?P<name>api[-_ ]?key|credential|password|passwd|secret|token)"
    r"(?:\s*(?:=|:)\s*|\s+)(?P<value>[^\s,;]+)"
)
_BACKUP_FAILURE_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
ARTIFACT_PATHS = {
    "data_backup": Path("/usr/local/sbin/jobseek-data-backup"),
    "image_protector": Path("/usr/local/sbin/jobseek-web-postgresql-protect-client-image"),
    "operations": Path("/usr/local/sbin/jobseek-web-postgresql-operations"),
    "retirement_migration": Path(
        "/usr/local/share/jobseek-backup/0086_drop_supabase_job_posting.sql"
    ),
    "restore_drill": Path("/usr/local/sbin/jobseek-web-postgresql-restore-drill"),
    "service": Path("/etc/systemd/system/jobseek-web-postgresql-backup.service"),
    "timer": Path("/etc/systemd/system/jobseek-web-postgresql-backup.timer"),
}
ARTIFACT_MODES = {
    "data_backup": 0o755,
    "image_protector": 0o755,
    "operations": 0o755,
    "retirement_migration": 0o644,
    "restore_drill": 0o755,
    "service": 0o644,
    "timer": 0o644,
}
BACKUP_FIELDS = (
    "archive_bytes",
    "archive_sha256",
    "attempt_at",
    "attempt_unix",
    "duration_seconds",
    "finished_at",
    "last_success_unix",
    "repository_snapshot_id",
    "row_count",
    "service",
    "success",
    "table_count",
)
RESTORE_FIELDS = (
    "archive_sha256",
    "deploy_sha",
    "duration_seconds",
    "finished_at",
    "retirement_convergence_applied",
    "retirement_created_at",
    "retirement_ledger_count",
    "retirement_migration_sha256",
    "row_count",
    "saved_job_digest",
    "saved_job_rows",
    "service",
    "started_at",
    "success",
    "table_count",
)


class OperationError(RuntimeError):
    """A protected backup operation failed without exposing command output."""


@dataclass(frozen=True)
class ExpectedIdentity:
    deploy_sha: str
    artifact_sha256: dict[str, str]


@dataclass(frozen=True)
class RestoreResources:
    operation_id: str
    container: str
    network: str
    operation_root: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_checked(argv: list[str], *, timeout: int = 600, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if completed.returncode:
        raise OperationError(f"{Path(argv[0]).name} operation failed")
    return completed.stdout


def command_succeeds(argv: list[str], *, timeout: int = 60) -> bool:
    completed = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.returncode == 0


def reset_failed_unit_if_needed(unit: str) -> None:
    try:
        completed = subprocess.run(
            ["systemctl", "is-failed", "--quiet", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise OperationError("systemctl unit failure state is unavailable") from None
    # `is-failed` reserves 0 for failed and 1 for not failed; all other states fail closed.
    if completed.returncode == 0:
        run_checked(["systemctl", "reset-failed", unit])
        return
    if completed.returncode != 1:
        raise OperationError("systemctl unit failure state is unavailable")
    load_state = run_checked(["systemctl", "show", unit, "--property=LoadState", "--value"]).strip()
    if load_state != "loaded":
        raise OperationError("systemctl unit failure state is unavailable")


def new_restore_resources() -> RestoreResources:
    operation_id = secrets.token_hex(16)
    container = f"jobseek-web-postgresql-restore-{operation_id}"
    return RestoreResources(
        operation_id=operation_id,
        container=container,
        network=f"{container}-network",
        operation_root=RESTORE_RUNTIME_ROOT / f"operation-{operation_id}",
    )


def docker_resource_absent(kind: str, name: str) -> bool:
    if kind not in {"container", "network"}:
        raise OperationError("restore cleanup resource kind is invalid")
    arguments = ["docker", kind, "ls"]
    if kind == "container":
        arguments.append("--all")
    arguments.extend(("--format", "{{.Names}}" if kind == "container" else "{{.Name}}"))
    inventory = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if inventory.returncode:
        raise OperationError("Docker resource inventory is unavailable during restore cleanup")
    return name not in inventory.stdout.splitlines()


def listed_restore_resources(kind: str) -> list[str]:
    if kind not in {"container", "network"}:
        raise OperationError("restore cleanup resource kind is invalid")
    arguments = ["docker", kind, "ls"]
    if kind == "container":
        arguments.append("--all")
    arguments.extend(("--quiet", "--filter", f"label={RESTORE_RESOURCE_LABEL}"))
    completed = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode:
        raise OperationError("stale restore resource inventory is unavailable")
    return [line for line in completed.stdout.splitlines() if line]


def reconcile_restore_resources(resources: RestoreResources) -> None:
    cleanup_failed = False
    for command in (
        ["docker", "rm", "--force", resources.container],
        ["docker", "network", "rm", resources.network],
    ):
        try:
            subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            cleanup_failed = True
    for kind, name in (
        ("container", resources.container),
        ("network", resources.network),
    ):
        try:
            if not docker_resource_absent(kind, name):
                cleanup_failed = True
        except (OSError, subprocess.TimeoutExpired, OperationError):
            cleanup_failed = True
    try:
        if resources.operation_root.is_symlink() or resources.operation_root.is_file():
            resources.operation_root.unlink()
        elif resources.operation_root.exists():
            shutil.rmtree(resources.operation_root)
    except OSError:
        cleanup_failed = True
    if resources.operation_root.exists() or resources.operation_root.is_symlink():
        cleanup_failed = True
    if cleanup_failed:
        raise OperationError("isolated restore residue cleanup could not be proven")


@contextmanager
def service_data_lock() -> Iterator[int]:
    RESTORE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESTORE_LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OperationError("web PostgreSQL backup or restore is already running") from exc
        os.fchmod(handle.fileno(), 0o600)
        yield handle.fileno()


@contextmanager
def deployment_identity_lock() -> Iterator[int]:
    inherited_fd = os.environ.get(DEPLOYMENT_LOCK_FD_ENV, "")
    if inherited_fd:
        if not inherited_fd.isdecimal():
            raise OperationError("inherited backup deployment lock descriptor is invalid")
        descriptor = int(inherited_fd)
        try:
            target = os.readlink(f"/proc/self/fd/{descriptor}")
            metadata = os.fstat(descriptor)
        except OSError as exc:
            raise OperationError("inherited backup deployment lock is unavailable") from exc
        if (
            target != str(DEPLOYMENT_LOCK_PATH)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise OperationError("inherited backup deployment lock boundary is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OperationError("inherited backup deployment lock is not held") from exc
        yield descriptor
        return
    DEPLOYMENT_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DEPLOYMENT_LOCK_PATH.is_symlink():
        raise OperationError("backup deployment lock path is unsafe")
    with DEPLOYMENT_LOCK_PATH.open("a+", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0:
            raise OperationError("backup deployment lock path is unsafe")
        os.fchmod(handle.fileno(), 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OperationError("another backup deployment or operation is active") from exc
        yield handle.fileno()


def reconcile_stale_restore_resources() -> None:
    cleanup_failed = False
    for kind in ("container", "network"):
        try:
            identifiers = listed_restore_resources(kind)
        except (OSError, subprocess.TimeoutExpired, OperationError):
            cleanup_failed = True
            continue
        for identifier in identifiers:
            command = (
                ["docker", "rm", "--force", identifier]
                if kind == "container"
                else ["docker", "network", "rm", identifier]
            )
            try:
                subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                cleanup_failed = True
        try:
            if listed_restore_resources(kind):
                cleanup_failed = True
        except (OSError, subprocess.TimeoutExpired, OperationError):
            cleanup_failed = True
    if RESTORE_RUNTIME_ROOT.exists():
        try:
            for child in RESTORE_RUNTIME_ROOT.iterdir():
                if re.fullmatch(r"operation-[0-9a-f]{32}", child.name):
                    if child.is_symlink() or child.is_file():
                        child.unlink()
                    else:
                        shutil.rmtree(child)
                else:
                    cleanup_failed = True
        except OSError:
            cleanup_failed = True
    if cleanup_failed:
        raise OperationError("stale isolated restore cleanup could not be proven")


def process_group_absent(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def terminate_restore_process(process: subprocess.Popen[str]) -> None:
    if not process_group_absent(process.pid):
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        process.communicate(timeout=20)
    if not process_group_absent(process.pid):
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while not process_group_absent(process.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if process.poll() is None:
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
    if process.poll() is None or not process_group_absent(process.pid):
        raise OperationError("isolated restore process group termination could not be proven")


def run_restore_drill(
    resources: RestoreResources,
    *,
    deploy_sha: str,
    service_lock_fd: int | None = None,
    deployment_lock_fd: int | None = None,
    timeout: int = 90 * 60,
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "WEB_POSTGRES_RESTORE_OPERATION_ID": resources.operation_id,
            "WEB_POSTGRES_RESTORE_CONTAINER": resources.container,
            "WEB_POSTGRES_RESTORE_NETWORK": resources.network,
            "WEB_POSTGRES_RESTORE_OPERATION_ROOT": str(resources.operation_root),
            "WEB_POSTGRES_RESTORE_DEPLOY_SHA": deploy_sha,
        }
    )
    if (service_lock_fd is None) != (deployment_lock_fd is None):
        raise OperationError("restore lock boundary is incomplete")
    pass_fds: tuple[int, ...] = ()
    if service_lock_fd is not None and deployment_lock_fd is not None:
        env["WEB_POSTGRES_RESTORE_LOCK_FD"] = str(service_lock_fd)
        env["WEB_POSTGRES_RESTORE_DEPLOYMENT_LOCK_FD"] = str(deployment_lock_fd)
        pass_fds = (service_lock_fd, deployment_lock_fd)
    process: subprocess.Popen[str] | None = None
    failure: BaseException | None = None
    previous_handlers: dict[signal.Signals, Any] = {}

    def interrupted(signum: int, _frame: Any) -> None:
        raise OperationError(f"restore drill interrupted by signal {signum}")

    try:
        process = subprocess.Popen(
            [str(ARTIFACT_PATHS["restore_drill"])],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            pass_fds=pass_fds,
            start_new_session=True,
        )
        for event in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            previous_handlers[event] = signal.signal(event, interrupted)
        try:
            process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            failure = OperationError("isolated restore drill timed out")
            failure.add_note(str(exc))
        except BaseException as exc:
            failure = exc
        if process.poll() is None or not process_group_absent(process.pid):
            if failure is None:
                failure = OperationError("isolated restore left a live process group")
            terminate_restore_process(process)
        if failure is None and process.returncode:
            failure = OperationError("isolated restore drill failed")
    except BaseException as exc:
        failure = exc
    finally:
        for event, handler in previous_handlers.items():
            signal.signal(event, handler)
    if process is not None and (process.poll() is None or not process_group_absent(process.pid)):
        try:
            terminate_restore_process(process)
        except BaseException as exc:
            if failure is None:
                failure = exc
    if process is not None and (process.poll() is None or not process_group_absent(process.pid)):
        raise OperationError(
            "isolated restore process group liveness cannot be excluded"
        ) from failure
    try:
        reconcile_restore_resources(resources)
    except OperationError as cleanup_error:
        if failure is not None:
            raise cleanup_error from failure
        raise
    if failure is not None:
        if isinstance(failure, (OperationError, OSError)):
            raise failure
        raise OperationError("isolated restore drill was interrupted") from failure


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationError(f"required {path.name} evidence is unavailable") from exc
    if not isinstance(value, dict):
        raise OperationError(f"required {path.name} evidence is invalid")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def project(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: record.get(field) for field in fields}


def parse_timestamp(value: object) -> int:
    if not isinstance(value, str):
        return 0
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OperationError(f"backup evidence field is invalid: {field}")
    return value


def redact_backup_failure_error(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 1200:
        raise OperationError("fresh failed web PostgreSQL backup error is invalid")
    text = _BACKUP_FAILURE_URI.sub("<redacted-uri>", value)
    text = _BACKUP_FAILURE_AUTHORITY.sub("<redacted-authority>", text)
    text = _BACKUP_FAILURE_AUTHORIZATION.sub("authorization=<redacted>", text)
    text = _BACKUP_FAILURE_SECRET.sub(
        lambda match: f"{match.group('name')}=<redacted>",
        text,
    )
    text = " ".join(_BACKUP_FAILURE_CONTROL.sub(" ", text).split())
    if not text:
        raise OperationError("fresh failed web PostgreSQL backup error is invalid")
    if len(text) > BACKUP_FAILURE_ERROR_LIMIT:
        suffix = " <truncated>"
        prefix = text[: BACKUP_FAILURE_ERROR_LIMIT - len(suffix)]
        boundary = prefix.rfind(" ")
        text = (
            prefix[:boundary] + suffix
            if boundary >= BACKUP_FAILURE_ERROR_LIMIT // 2
            else "backup failure detail exceeded the safe diagnostic limit"
        )
    if (
        len(text) > BACKUP_FAILURE_ERROR_LIMIT
        or _BACKUP_FAILURE_URI.search(text)
        or _BACKUP_FAILURE_AUTHORITY.search(text)
        or _BACKUP_FAILURE_AUTHORIZATION.search(text)
        or any(
            match.group("value") != "<redacted>" for match in _BACKUP_FAILURE_SECRET.finditer(text)
        )
    ):
        raise OperationError("backup failure detail violated the nonsecret contract")
    return text


def backup_failure_diagnostic(status: dict[str, Any], *, started: int) -> str:
    attempt = required_int(status.get("attempt_unix"), "attempt_unix")
    attempt_at = status.get("attempt_at")
    finished_at = status.get("finished_at")
    duration = status.get("duration_seconds")
    if (
        status.get("schema_version") != 1
        or status.get("service") != "web-postgresql"
        or status.get("success") is not False
        or attempt < started
        or not isinstance(attempt_at, str)
        or len(attempt_at) > 64
        or parse_timestamp(attempt_at) < started
        or not isinstance(finished_at, str)
        or len(finished_at) > 64
        or parse_timestamp(finished_at) < attempt
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
        or duration > 2 * 60 * 60 + 300
    ):
        raise OperationError("fresh failed web PostgreSQL backup evidence is invalid")
    diagnostic = json.dumps(
        {
            "attempt_at": attempt_at,
            "duration_seconds": duration,
            "error": redact_backup_failure_error(status.get("error")),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(diagnostic) > BACKUP_FAILURE_DIAGNOSTIC_LIMIT:
        raise OperationError("backup failure diagnostic exceeded its safe size contract")
    return diagnostic


def emit_backup_failure_diagnostic(*, started: int) -> None:
    status = read_json(BACKUP_STATUS_PATH)
    print(
        "Backup failure evidence: " + backup_failure_diagnostic(status, started=started),
        file=sys.stderr,
    )


def validate_identity(expected: ExpectedIdentity) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", expected.deploy_sha):
        raise OperationError("expected deployment revision is invalid")
    if set(expected.artifact_sha256) != set(ARTIFACT_PATHS):
        raise OperationError("expected artifact boundary is invalid")
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in expected.artifact_sha256.values()
    ):
        raise OperationError("expected artifact digest is invalid")
    require_root_regular_file(DEPLOYED_SHA_PATH, mode=0o644)
    try:
        deployed_sha = DEPLOYED_SHA_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OperationError("installed deployment revision is unavailable") from exc
    if deployed_sha != expected.deploy_sha:
        raise OperationError("installed backup revision does not match this dispatch")
    for name, path in ARTIFACT_PATHS.items():
        require_root_regular_file(path, mode=ARTIFACT_MODES[name])
        try:
            actual = sha256_file(path)
        except OSError as exc:
            raise OperationError(f"installed backup artifact is unavailable: {name}") from exc
        if actual != expected.artifact_sha256[name]:
            raise OperationError(f"installed backup artifact does not match: {name}")
    return {
        str(ARTIFACT_PATHS[name]): expected.artifact_sha256[name] for name in sorted(ARTIFACT_PATHS)
    }


def systemctl_state(verb: str, unit: str) -> str:
    completed = subprocess.run(
        ["systemctl", verb, unit],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout.strip()


def timer_state() -> tuple[str, str]:
    return systemctl_state("is-enabled", TIMER_UNIT), systemctl_state("is-active", TIMER_UNIT)


def require_root_regular_file(path: Path, *, mode: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise OperationError(f"required root-only file is unavailable: {path.name}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise OperationError(f"required root-only file is unsafe: {path.name}")


def require_private_regular_file(path: Path) -> None:
    require_root_regular_file(path, mode=0o600)


def validate_loaded_unit(unit: str, expected_path: Path) -> None:
    output = run_checked(
        [
            "systemctl",
            "show",
            unit,
            "--property=FragmentPath",
            "--property=DropInPaths",
            "--property=NeedDaemonReload",
        ]
    )
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in properties:
            raise OperationError(f"loaded systemd unit metadata is invalid: {unit}")
        properties[key] = value
    if properties != {
        "FragmentPath": str(expected_path),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
    }:
        raise OperationError(f"loaded systemd unit does not match reviewed artifact: {unit}")


def validate_helper_image_lease() -> None:
    run_checked(["docker", "image", "inspect", RESTORE_IMAGE])
    output = run_checked(["docker", "container", "inspect", HELPER_IMAGE_LEASE])
    try:
        payload = json.loads(output)
        container = payload[0]
        config = container["Config"]
        state = container["State"]
        host_config = container["HostConfig"]
        labels = config.get("Labels") or {}
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OperationError("web PostgreSQL helper-image lease is invalid") from exc
    if (
        config.get("Image") != RESTORE_IMAGE
        or state.get("Running") is not False
        or labels.get(HELPER_IMAGE_LEASE_LABEL) != "web-postgresql"
        or config.get("Entrypoint") != ["/bin/true"]
        or host_config.get("NetworkMode") != "none"
        or host_config.get("ReadonlyRootfs") is not True
        or host_config.get("CapDrop") != ["ALL"]
        or host_config.get("SecurityOpt") != ["no-new-privileges:true"]
        or host_config.get("Tmpfs") != HELPER_IMAGE_LEASE_TMPFS
        or container.get("Mounts") != []
    ):
        raise OperationError("web PostgreSQL helper-image lease does not protect the pinned digest")


def validate_host_readiness(expected: ExpectedIdentity) -> None:
    validate_identity(expected)
    if os.geteuid() != 0:
        raise OperationError("backup operations must run as root")
    require_private_regular_file(BACKUP_ENV_PATH)
    require_private_regular_file(DATABASE_CREDENTIAL_PATH)
    assignments: dict[str, str] = {}
    for line in BACKUP_ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in assignments:
            raise OperationError("duplicate web backup configuration key")
        assignments[key] = value
    required = {"RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE", "RESTIC_SFTP_COMMAND"}
    if set(assignments) != required or any(not value for value in assignments.values()):
        raise OperationError("web backup configuration boundary is incomplete")
    database_url = DATABASE_CREDENTIAL_PATH.read_text(encoding="utf-8")
    if "\r" in database_url or database_url.count("\n") != 1:
        raise OperationError("web database credential must contain exactly one line")
    try:
        parsed = urlsplit(database_url.rstrip("\n"))
        hostname = parsed.hostname
    except ValueError as exc:
        raise OperationError("web database credential is not a PostgreSQL URI") from exc
    if parsed.scheme not in {"postgres", "postgresql"} or not hostname:
        raise OperationError("web database credential is not a PostgreSQL URI")
    for command in ("docker", "flock", "restic", "systemctl", "systemd-analyze"):
        if not command_succeeds(["sh", "-ceu", f"command -v {command} >/dev/null"]):
            raise OperationError(f"required host command is unavailable: {command}")
    if not command_succeeds(["systemctl", "is-active", "--quiet", "docker.service"]):
        raise OperationError("Docker is not active")
    for unit, path in (
        (BACKUP_UNIT, ARTIFACT_PATHS["service"]),
        (TIMER_UNIT, ARTIFACT_PATHS["timer"]),
    ):
        validate_loaded_unit(unit, path)
        run_checked(["systemctl", "cat", unit])
        run_checked(["systemd-analyze", "verify", str(path)])
    validate_helper_image_lease()
    repository_output = run_checked(
        [
            "bash",
            "-ceu",
            """
            set -a
            source "$1"
            set +a
            exec restic -o "sftp.command=${RESTIC_SFTP_COMMAND}" snapshots \
              --json --latest 1 \
              --tag jobseek-web-postgresql \
              --host jobseek-web-postgresql
            """,
            "jobseek-web-postgresql-readiness",
            str(BACKUP_ENV_PATH),
        ],
        timeout=600,
    )
    try:
        snapshots = json.loads(repository_output)
    except json.JSONDecodeError as exc:
        raise OperationError("Restic snapshot inventory is invalid") from exc
    if not isinstance(snapshots, list):
        raise OperationError("Restic snapshot inventory is invalid")
    enabled, active = timer_state()
    print(
        "Readiness passed: "
        f"snapshot_present={'true' if snapshots else 'false'} "
        f"timer_enabled={enabled} timer_active={active}"
    )


def validate_fresh_backup(status: dict[str, Any], *, started: int | None = None) -> None:
    attempt = required_int(status.get("attempt_unix"), "attempt_unix")
    last_success = required_int(status.get("last_success_unix"), "last_success_unix")
    archive_bytes = required_int(status.get("archive_bytes"), "archive_bytes")
    table_count = required_int(status.get("table_count"), "table_count")
    row_count = required_int(status.get("row_count"), "row_count")
    if (
        status.get("service") != "web-postgresql"
        or status.get("success") is not True
        or (started is not None and (attempt < started or last_success < started))
        or not re.fullmatch(r"[0-9a-f]{64}", str(status.get("archive_sha256", "")))
        or not re.fullmatch(r"[0-9a-f]{8,64}", str(status.get("repository_snapshot_id", "")))
        or archive_bytes <= 0
        or table_count <= 0
        or row_count < 0
    ):
        raise OperationError("fresh web PostgreSQL backup evidence is incomplete")


def write_backup_evidence(
    expected: ExpectedIdentity, artifact_hashes: dict[str, str], *, started: int
) -> dict[str, Any]:
    status = read_json(BACKUP_STATUS_PATH)
    validate_fresh_backup(status, started=started)
    backup = project(status, BACKUP_FIELDS)
    evidence = {
        "schema_version": 1,
        "deployed_sha": expected.deploy_sha,
        "artifact_sha256": artifact_hashes,
        "backup": backup,
    }
    atomic_json(EVIDENCE_PATH, evidence)
    return backup


def run_backup(expected: ExpectedIdentity) -> None:
    with deployment_identity_lock():
        run_backup_locked(expected)


def run_backup_locked(expected: ExpectedIdentity) -> None:
    validate_host_readiness(expected)
    artifact_hashes = validate_identity(expected)
    EVIDENCE_PATH.unlink(missing_ok=True)
    before = timer_state()
    started = int(time.time())
    reset_failed_unit_if_needed(BACKUP_UNIT)
    try:
        run_checked(["systemctl", "start", BACKUP_UNIT], timeout=2 * 60 * 60 + 300)
    except (OperationError, OSError, subprocess.TimeoutExpired):
        with suppress(OperationError, OSError):
            emit_backup_failure_diagnostic(started=started)
        raise
    if timer_state() != before:
        raise OperationError("backup operation changed the timer state")
    artifact_hashes = validate_identity(expected)
    backup = write_backup_evidence(expected, artifact_hashes, started=started)
    safe = {
        key: backup.get(key)
        for key in ("archive_bytes", "attempt_at", "duration_seconds", "table_count", "row_count")
    }
    print("Backup evidence: " + json.dumps(safe, sort_keys=True))


def load_bound_backup(
    expected: ExpectedIdentity, *, clear_restore: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_hashes = validate_identity(expected)
    evidence = read_json(EVIDENCE_PATH)
    if (
        evidence.get("schema_version") != 1
        or evidence.get("deployed_sha") != expected.deploy_sha
        or evidence.get("artifact_sha256") != artifact_hashes
    ):
        raise OperationError("backup activation evidence belongs to another deployment")
    backup = evidence.get("backup")
    if not isinstance(backup, dict):
        raise OperationError("bound backup evidence is missing")
    status = read_json(BACKUP_STATUS_PATH)
    if any(status.get(key) != value for key, value in backup.items()):
        raise OperationError("current backup status no longer matches bound evidence")
    validate_fresh_backup(status)
    age = time.time() - required_int(backup.get("last_success_unix"), "last_success_unix")
    if age < 0 or age > 9 * 60 * 60:
        raise OperationError("a fresh successful bound web PostgreSQL backup is required")
    if clear_restore:
        evidence.pop("restore", None)
        atomic_json(EVIDENCE_PATH, evidence)
    return evidence, backup


def run_restore(expected: ExpectedIdentity) -> None:
    with deployment_identity_lock() as deployment_lock_fd:
        run_restore_locked(expected, deployment_lock_fd=deployment_lock_fd)


def run_restore_locked(expected: ExpectedIdentity, *, deployment_lock_fd: int) -> None:
    validate_host_readiness(expected)
    load_bound_backup(expected, clear_restore=True)
    before = timer_state()
    started = int(time.time())
    with service_data_lock() as service_lock_fd:
        reconcile_stale_restore_resources()
        run_restore_drill(
            new_restore_resources(),
            deploy_sha=expected.deploy_sha,
            service_lock_fd=service_lock_fd,
            deployment_lock_fd=deployment_lock_fd,
        )
    if timer_state() != before:
        raise OperationError("restore operation changed the timer state")
    evidence, backup = load_bound_backup(expected)
    restore_status = read_json(RESTORE_STATUS_PATH)
    convergence_applied = restore_status.get("retirement_convergence_applied")
    retirement_ledger_count = restore_status.get("retirement_ledger_count")
    if (
        restore_status.get("service") != "web-postgresql-restore"
        or restore_status.get("success") is not True
        or parse_timestamp(restore_status.get("started_at")) < started
        or parse_timestamp(restore_status.get("finished_at")) < started
        or restore_status.get("archive_sha256") != backup.get("archive_sha256")
        or restore_status.get("table_count") != backup.get("table_count")
        or restore_status.get("row_count") != backup.get("row_count")
        or restore_status.get("deploy_sha") != expected.deploy_sha
        or restore_status.get("retirement_migration_sha256")
        != expected.artifact_sha256["retirement_migration"]
        or restore_status.get("retirement_created_at") != 1_785_760_800_000
        or not isinstance(convergence_applied, bool)
        or isinstance(retirement_ledger_count, bool)
        or not isinstance(retirement_ledger_count, int)
        or retirement_ledger_count < 76
        or (convergence_applied and retirement_ledger_count != 76)
        or isinstance(restore_status.get("saved_job_rows"), bool)
        or not isinstance(restore_status.get("saved_job_rows"), int)
        or restore_status.get("saved_job_rows", -1) < 0
        or not re.fullmatch(r"[0-9a-f]{32}", str(restore_status.get("saved_job_digest", "")))
    ):
        raise OperationError("fresh isolated restore evidence does not match the bound backup")
    restore = project(restore_status, RESTORE_FIELDS)
    evidence["restore"] = restore
    atomic_json(EVIDENCE_PATH, evidence)
    safe = {
        key: restore.get(key)
        for key in ("duration_seconds", "finished_at", "row_count", "started_at", "table_count")
    }
    print("Restore evidence: " + json.dumps(safe, sort_keys=True))


def validate_activation_evidence(expected: ExpectedIdentity) -> dict[str, Any]:
    evidence, backup = load_bound_backup(expected)
    restore = evidence.get("restore")
    if not isinstance(restore, dict):
        raise OperationError("bound restore evidence is missing")
    restore_status = read_json(RESTORE_STATUS_PATH)
    if any(restore_status.get(key) != value for key, value in restore.items()):
        raise OperationError("current restore status no longer matches bound evidence")
    now = time.time()
    backup_finished = required_int(backup.get("last_success_unix"), "last_success_unix")
    restore_started = parse_timestamp(restore.get("started_at"))
    restore_finished = parse_timestamp(restore.get("finished_at"))
    if restore_status.get("success") is not True or not 0 <= now - restore_finished <= 9 * 60 * 60:
        raise OperationError("fresh successful bound restore evidence is required")
    if restore_started < backup_finished:
        raise OperationError("bound restore evidence must postdate the successful backup")
    archive_sha256 = str(backup.get("archive_sha256", ""))
    if (
        not re.fullmatch(r"[0-9a-f]{64}", archive_sha256)
        or restore.get("archive_sha256") != archive_sha256
        or restore.get("table_count") != backup.get("table_count")
        or restore.get("row_count") != backup.get("row_count")
    ):
        raise OperationError("bound backup and restore evidence describe different archives")
    return {
        "backup_finished_at": backup.get("finished_at"),
        "restore_finished_at": restore.get("finished_at"),
        "table_count": backup.get("table_count"),
        "row_count": backup.get("row_count"),
    }


def enable_timer(expected: ExpectedIdentity) -> None:
    with deployment_identity_lock():
        enable_timer_locked(expected)


def enable_timer_locked(expected: ExpectedIdentity) -> None:
    validate_host_readiness(expected)
    safe = validate_activation_evidence(expected)
    if timer_state() != ("disabled", "inactive"):
        raise OperationError("timer must be disabled and inactive before activation")
    rollback_required = True
    previous_handlers: dict[signal.Signals, Any] = {}

    def interrupted(signum: int, _frame: Any) -> None:
        raise OperationError(f"timer activation interrupted by signal {signum}")

    for event in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        previous_handlers[event] = signal.signal(event, interrupted)
    try:
        reset_failed_unit_if_needed(BACKUP_UNIT)
        run_checked(["systemctl", "start", TIMER_UNIT])
        if timer_state() != ("disabled", "active"):
            raise OperationError("timer activation postcondition failed")
        if command_succeeds(["systemctl", "is-failed", "--quiet", BACKUP_UNIT]):
            raise OperationError("backup service is failed after timer activation")
        next_run = run_checked(
            ["systemctl", "show", TIMER_UNIT, "--property=NextElapseUSecRealtime", "--value"]
        ).strip()
        if not next_run or next_run == "n/a":
            raise OperationError("started timer has no next run")
        validate_identity(expected)
        run_checked(["systemctl", "enable", TIMER_UNIT])
        if timer_state() != ("enabled", "active"):
            raise OperationError("timer persistence commit failed")
        rollback_required = False
    finally:
        if rollback_required:
            rollback = subprocess.run(
                ["systemctl", "disable", "--now", TIMER_UNIT],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if rollback.returncode:
                print("ERROR: timer activation rollback failed", file=sys.stderr)
        for event, handler in previous_handlers.items():
            signal.signal(event, handler)
    print("Activation evidence: " + json.dumps(safe, sort_keys=True))
    print(f"Web PostgreSQL backup timer enabled; next_run={next_run}")


def expected_identity(args: argparse.Namespace) -> ExpectedIdentity:
    return ExpectedIdentity(
        deploy_sha=args.expected_deploy_sha,
        artifact_sha256={
            "data_backup": args.expected_data_backup_sha256,
            "image_protector": args.expected_image_protector_sha256,
            "operations": args.expected_operations_sha256,
            "retirement_migration": args.expected_retirement_migration_sha256,
            "restore_drill": args.expected_restore_drill_sha256,
            "service": args.expected_service_sha256,
            "timer": args.expected_timer_sha256,
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify", "backup", "restore", "enable-timer"))
    parser.add_argument("--expected-deploy-sha", required=True)
    parser.add_argument("--expected-data-backup-sha256", required=True)
    parser.add_argument("--expected-image-protector-sha256", required=True)
    parser.add_argument("--expected-operations-sha256", required=True)
    parser.add_argument("--expected-retirement-migration-sha256", required=True)
    parser.add_argument("--expected-restore-drill-sha256", required=True)
    parser.add_argument("--expected-service-sha256", required=True)
    parser.add_argument("--expected-timer-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expected = expected_identity(args)
    try:
        if args.mode == "verify":
            with deployment_identity_lock():
                validate_host_readiness(expected)
        elif args.mode == "backup":
            run_backup(expected)
        elif args.mode == "restore":
            run_restore(expected)
        else:
            enable_timer(expected)
    except (OperationError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
