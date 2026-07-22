#!/usr/bin/env python3
"""Run and report Jobseek's application-consistent Hetzner data backups.

The script deliberately contains no credentials. Secrets and repository
coordinates are supplied by root-only host configuration. Each attempt writes
an atomic JSON status file and Prometheus textfile so the daily Codex review
and later host monitoring can distinguish success, failure, and stale data.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUS_DIR = Path(os.environ.get("BACKUP_STATUS_DIR", "/var/lib/jobseek-backup/status"))
_REDACTIONS = (
    (
        re.compile(r"(?i)(api[-_ ]?key|password|secret|token)([=: ]+)[^\s,;]+"),
        r"\1\2<redacted>",
    ),
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s]+"), r"\1<redacted>"),
)


class BackupError(RuntimeError):
    """A backup command or validation failed safely."""


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def redact(value: str, *, limit: int = 1200) -> str:
    text = value.strip()
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text[-limit:]


def run_checked(
    argv: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    if completed.returncode:
        output = "\n".join(
            part for part in (completed.stdout, completed.stderr) if part
        )
        raise BackupError(f"{argv[0]} exited {completed.returncode}: {redact(output)}")
    return completed


def atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def read_previous_status(service: str, status_dir: Path = STATUS_DIR) -> dict[str, Any]:
    path = status_dir / f"{service}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_status(
    service: str, record: dict[str, Any], status_dir: Path = STATUS_DIR
) -> None:
    atomic_write(
        status_dir / f"{service}.json",
        json.dumps(record, indent=2, sort_keys=True) + "\n",
    )
    success = 1 if record.get("success") else 0
    attempt = int(record.get("attempt_unix") or 0)
    last_success = int(record.get("last_success_unix") or 0)
    duration = float(record.get("duration_seconds") or 0)
    metrics = (
        "# HELP jobseek_backup_last_attempt_unixtime Unix time of the latest backup attempt.\n"
        "# TYPE jobseek_backup_last_attempt_unixtime gauge\n"
        f'jobseek_backup_last_attempt_unixtime{{service="{service}"}} {attempt}\n'
        "# HELP jobseek_backup_last_success_unixtime Unix time of the latest successful backup.\n"
        "# TYPE jobseek_backup_last_success_unixtime gauge\n"
        f'jobseek_backup_last_success_unixtime{{service="{service}"}} {last_success}\n'
        "# HELP jobseek_backup_last_attempt_success Whether the latest attempt succeeded.\n"
        "# TYPE jobseek_backup_last_attempt_success gauge\n"
        f'jobseek_backup_last_attempt_success{{service="{service}"}} {success}\n'
        "# HELP jobseek_backup_last_duration_seconds Duration of the latest backup attempt.\n"
        "# TYPE jobseek_backup_last_duration_seconds gauge\n"
        f'jobseek_backup_last_duration_seconds{{service="{service}"}} {duration:.3f}\n'
    )
    atomic_write(status_dir / f"{service}.prom", metrics, mode=0o644)


@contextmanager
def exclusive_lock(service: str):
    lock_path = Path(f"/run/jobseek-data-backup-{service}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError(f"a {service} backup is already running") from exc
        yield


def execute_with_status(
    service: str,
    operation: Callable[[], dict[str, Any]],
    *,
    status_dir: Path = STATUS_DIR,
) -> dict[str, Any]:
    started = utc_now()
    previous = read_previous_status(service, status_dir)
    record: dict[str, Any] = {
        "schema_version": 1,
        "service": service,
        "attempt_at": started.isoformat(),
        "attempt_unix": int(started.timestamp()),
        "success": False,
        "last_success_at": previous.get("last_success_at"),
        "last_success_unix": previous.get("last_success_unix", 0),
    }
    try:
        details = operation()
    except Exception as exc:
        finished = utc_now()
        record.update(
            {
                "finished_at": finished.isoformat(),
                "duration_seconds": (finished - started).total_seconds(),
                "error": redact(str(exc)),
            }
        )
        write_status(service, record, status_dir)
        raise

    finished = utc_now()
    record.update(details)
    record.update(
        {
            "success": True,
            "finished_at": finished.isoformat(),
            "duration_seconds": (finished - started).total_seconds(),
            "last_success_at": finished.isoformat(),
            "last_success_unix": int(finished.timestamp()),
        }
    )
    record.pop("error", None)
    write_status(service, record, status_dir)
    return record


def postgres_backup(backup_type: str) -> dict[str, Any]:
    container = os.environ.get("POSTGRES_CONTAINER", "postgres")
    stanza = os.environ.get("PGBACKREST_STANZA", "jobseek")
    if backup_type == "auto":
        backup_type = "full" if utc_now().isoweekday() == 7 else "diff"
    if backup_type not in {"full", "diff", "incr"}:
        raise BackupError(f"unsupported PostgreSQL backup type: {backup_type}")

    running = run_checked(
        ["docker", "inspect", "--format", "{{.State.Running}}", container], timeout=30
    ).stdout.strip()
    if running != "true":
        raise BackupError(f"PostgreSQL container {container!r} is not running")

    base = [
        "docker",
        "exec",
        "--user",
        "postgres",
        container,
        "pgbackrest",
        f"--stanza={stanza}",
    ]
    run_checked([*base, "check"], timeout=900)
    run_checked([*base, f"--type={backup_type}", "backup"], timeout=43_200)
    run_checked([*base, "check"], timeout=1_800)
    info_output = run_checked([*base, "--output=json", "info"], timeout=300).stdout

    try:
        stanza_info = json.loads(info_output)[0]
        backups = stanza_info["backup"]
        latest = max(backups, key=lambda item: item["timestamp"]["stop"])
        repository_info = latest["info"]["repository"]
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError("pgBackRest returned no parseable completed backup") from exc

    return {
        "backup_type": latest.get("type", backup_type),
        "backup_label": latest.get("label"),
        "backup_database_bytes": latest.get("info", {}).get("size"),
        # pgBackRest 2.59 reports per-backup repository bytes as `delta`;
        # retain the older `size` fallback for compatible package releases.
        "backup_repository_bytes": repository_info.get(
            "delta", repository_info.get("size")
        ),
        "repository_backup_count": len(backups),
        "repository_latest_stop_unix": latest["timestamp"]["stop"],
    }


def _snapshot_request(url: str, api_key: str, snapshot_path: str) -> None:
    query = urllib.parse.urlencode({"snapshot_path": snapshot_path})
    request = urllib.request.Request(
        f"{url.rstrip('/')}/operations/snapshot?{query}",
        method="POST",
        headers={"X-TYPESENSE-API-KEY": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=7_200) as response:  # noqa: S310
            body = response.read().decode("utf-8")
    except Exception as exc:
        raise BackupError(f"Typesense snapshot API failed: {redact(str(exc))}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BackupError("Typesense snapshot API returned non-JSON output") from exc
    if payload.get("success") is not True:
        raise BackupError(
            f"Typesense snapshot API did not report success: {redact(body)}"
        )


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _remove_old_staging(
    staging_root: Path, *, older_than_seconds: int = 172_800
) -> None:
    if not staging_root.exists():
        return
    cutoff = time.time() - older_than_seconds
    for child in staging_root.iterdir():
        if child.is_dir() and not child.is_symlink() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child)


def _restic_command(*arguments: str) -> list[str]:
    sftp_command = os.environ.get("RESTIC_SFTP_COMMAND", "")
    if not sftp_command:
        raise BackupError("RESTIC_SFTP_COMMAND is missing")
    return ["restic", "-o", f"sftp.command={sftp_command}", *arguments]


def typesense_backup() -> dict[str, Any]:
    container = os.environ.get("TYPESENSE_CONTAINER", "typesense")
    url = os.environ.get("TYPESENSE_URL", "http://127.0.0.1:8108")
    api_key = os.environ.get("TYPESENSE_API_KEY", "")
    if not api_key:
        raise BackupError("TYPESENSE_API_KEY is missing")
    required_restic = (
        "RESTIC_REPOSITORY",
        "RESTIC_PASSWORD_FILE",
        "RESTIC_SFTP_COMMAND",
    )
    missing = [name for name in required_restic if not os.environ.get(name)]
    if missing:
        raise BackupError(f"missing Restic configuration: {', '.join(missing)}")

    running = run_checked(
        ["docker", "inspect", "--format", "{{.State.Running}}", container], timeout=30
    ).stdout.strip()
    if running != "true":
        raise BackupError(f"Typesense container {container!r} is not running")

    run_id = utc_now().strftime("%Y%m%dT%H%M%SZ")
    container_root = os.environ.get(
        "TYPESENSE_SNAPSHOT_CONTAINER_ROOT", "/tmp/jobseek-typesense-snapshots"
    ).rstrip("/")
    container_path = f"{container_root}/{run_id}"
    staging_root = (
        Path(
            os.environ.get(
                "TYPESENSE_SNAPSHOT_HOST_ROOT", "/var/lib/jobseek-backup/typesense"
            )
        )
        / "staging"
    )
    local_path = staging_root / run_id
    _remove_old_staging(staging_root)
    local_path.mkdir(parents=True, mode=0o700)
    copied = False
    success = False

    try:
        run_checked(
            ["docker", "exec", container, "rm", "-rf", "--", container_path], timeout=60
        )
        _snapshot_request(url, api_key, container_path)
        run_checked(
            ["docker", "cp", f"{container}:{container_path}/.", str(local_path)],
            timeout=7_200,
        )
        copied = True
        snapshot_bytes = _tree_size(local_path)
        if snapshot_bytes <= 0:
            raise BackupError("Typesense snapshot copy is empty")

        restic_env = os.environ.copy()
        run_checked(
            _restic_command(
                "backup",
                "--tag",
                "jobseek-typesense",
                "--host",
                "jobseek-typesense",
                str(local_path),
            ),
            env=restic_env,
            timeout=14_400,
        )
        run_checked(
            _restic_command(
                "forget",
                "--tag",
                "jobseek-typesense",
                "--host",
                "jobseek-typesense",
                "--group-by",
                "host,tags",
                "--keep-daily",
                "14",
                "--keep-weekly",
                "4",
                "--prune",
            ),
            env=restic_env,
            timeout=14_400,
        )
        run_checked(_restic_command("check"), env=restic_env, timeout=14_400)
        snapshots_output = run_checked(
            _restic_command(
                "snapshots", "--json", "--latest", "1", "--tag", "jobseek-typesense"
            ),
            env=restic_env,
            timeout=300,
        ).stdout
        try:
            snapshots = json.loads(snapshots_output)
            latest_snapshot = snapshots[-1]
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise BackupError(
                "Restic returned no parseable Typesense snapshot"
            ) from exc
        success = True
        return {
            "snapshot_bytes": snapshot_bytes,
            "repository_snapshot_id": latest_snapshot.get("short_id")
            or latest_snapshot.get("id", "")[:8],
            "repository_snapshot_time": latest_snapshot.get("time"),
        }
    finally:
        with suppress(Exception):
            run_checked(
                ["docker", "exec", container, "rm", "-rf", "--", container_path],
                timeout=60,
            )
        if success or not copied and not any(local_path.iterdir()):
            shutil.rmtree(local_path, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="service", required=True)
    postgres = subparsers.add_parser("postgresql")
    postgres.add_argument(
        "--backup-type", choices=("auto", "full", "diff", "incr"), default="auto"
    )
    subparsers.add_parser("typesense")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with exclusive_lock(args.service):
            if args.service == "postgresql":
                record = execute_with_status(
                    "postgresql", lambda: postgres_backup(args.backup_type)
                )
            else:
                record = execute_with_status("typesense", typesense_backup)
    except Exception as exc:
        print(f"backup failed: {redact(str(exc))}", file=sys.stderr)
        return 1
    print(
        f"backup succeeded: service={record['service']} "
        f"duration_seconds={record['duration_seconds']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
