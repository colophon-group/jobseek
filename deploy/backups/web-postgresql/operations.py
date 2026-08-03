#!/usr/bin/env python3
"""Operate the staged web PostgreSQL backup surface with fail-closed evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
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
BACKUP_UNIT = "jobseek-web-postgresql-backup.service"
TIMER_UNIT = "jobseek-web-postgresql-backup.timer"
BACKUP_ENV_PATH = Path("/etc/jobseek-backup/web-postgresql.env")
DATABASE_CREDENTIAL_PATH = Path("/etc/jobseek-backup/web-postgresql.database-url")
RESTORE_IMAGE = (
    "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
)
ARTIFACT_PATHS = {
    "data_backup": Path("/usr/local/sbin/jobseek-data-backup"),
    "operations": Path("/usr/local/sbin/jobseek-web-postgresql-operations"),
    "restore_drill": Path("/usr/local/sbin/jobseek-web-postgresql-restore-drill"),
    "service": Path("/etc/systemd/system/jobseek-web-postgresql-backup.service"),
    "timer": Path("/etc/systemd/system/jobseek-web-postgresql-backup.timer"),
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
    "duration_seconds",
    "finished_at",
    "row_count",
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


def validate_identity(expected: ExpectedIdentity) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", expected.deploy_sha):
        raise OperationError("expected deployment revision is invalid")
    if set(expected.artifact_sha256) != set(ARTIFACT_PATHS):
        raise OperationError("expected artifact boundary is invalid")
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in expected.artifact_sha256.values()
    ):
        raise OperationError("expected artifact digest is invalid")
    try:
        deployed_sha = DEPLOYED_SHA_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OperationError("installed deployment revision is unavailable") from exc
    if deployed_sha != expected.deploy_sha:
        raise OperationError("installed backup revision does not match this dispatch")
    for name, path in ARTIFACT_PATHS.items():
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


def require_private_regular_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise OperationError(f"required root-only file is unavailable: {path.name}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise OperationError(f"required root-only file is unsafe: {path.name}")


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
    for command in ("docker", "flock", "restic", "systemctl"):
        if not command_succeeds(["sh", "-ceu", f"command -v {command} >/dev/null"]):
            raise OperationError(f"required host command is unavailable: {command}")
    if not command_succeeds(["systemctl", "is-active", "--quiet", "docker.service"]):
        raise OperationError("Docker is not active")
    run_checked(["systemctl", "cat", BACKUP_UNIT])
    run_checked(["systemctl", "cat", TIMER_UNIT])
    run_checked(["docker", "image", "inspect", RESTORE_IMAGE])
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
    artifact_hashes = validate_identity(expected)
    EVIDENCE_PATH.unlink(missing_ok=True)
    before = timer_state()
    started = int(time.time())
    run_checked(["systemctl", "reset-failed", BACKUP_UNIT])
    run_checked(["systemctl", "start", BACKUP_UNIT], timeout=2 * 60 * 60 + 300)
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
    load_bound_backup(expected, clear_restore=True)
    before = timer_state()
    started = int(time.time())
    run_checked([str(ARTIFACT_PATHS["restore_drill"])], timeout=90 * 60)
    if timer_state() != before:
        raise OperationError("restore operation changed the timer state")
    evidence, backup = load_bound_backup(expected)
    restore_status = read_json(RESTORE_STATUS_PATH)
    if (
        restore_status.get("service") != "web-postgresql-restore"
        or restore_status.get("success") is not True
        or parse_timestamp(restore_status.get("started_at")) < started
        or parse_timestamp(restore_status.get("finished_at")) < started
        or restore_status.get("archive_sha256") != backup.get("archive_sha256")
        or restore_status.get("table_count") != backup.get("table_count")
        or restore_status.get("row_count") != backup.get("row_count")
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
        run_checked(["systemctl", "reset-failed", BACKUP_UNIT])
        run_checked(["systemctl", "enable", "--now", TIMER_UNIT])
        if timer_state() != ("enabled", "active"):
            raise OperationError("timer activation postcondition failed")
        if command_succeeds(["systemctl", "is-failed", "--quiet", BACKUP_UNIT]):
            raise OperationError("backup service is failed after timer activation")
        next_run = run_checked(
            ["systemctl", "show", TIMER_UNIT, "--property=NextElapseUSecRealtime", "--value"]
        ).strip()
        if not next_run or next_run == "n/a":
            raise OperationError("enabled timer has no next run")
        validate_identity(expected)
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
            "operations": args.expected_operations_sha256,
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
    parser.add_argument("--expected-operations-sha256", required=True)
    parser.add_argument("--expected-restore-drill-sha256", required=True)
    parser.add_argument("--expected-service-sha256", required=True)
    parser.add_argument("--expected-timer-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expected = expected_identity(args)
    try:
        if args.mode == "verify":
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
