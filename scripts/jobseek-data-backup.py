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
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUS_DIR = Path(os.environ.get("BACKUP_STATUS_DIR", "/var/lib/jobseek-backup/status"))
POSTGRES_RETENTION_OPTIONS = (
    "--repo1-retention-full=4",
    "--repo1-retention-diff=7",
    "--repo1-retention-archive=2",
    "--repo1-retention-archive-type=diff",
)
WEB_POSTGRES_IMAGE = (
    "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
)
WEB_POSTGRES_IMAGE_LEASE = "jobseek-web-postgresql-backup-image-lease"
WEB_POSTGRES_IMAGE_LEASE_LABEL = "jobseek.backup.helper-image"
WEB_POSTGRES_IMAGE_LEASE_TMPFS = {"/var/lib/postgresql/data": "rw,noexec,nosuid,nodev,size=65536"}
# Durable web/product records plus the small relational support set required
# by their outbound foreign keys. Crawler postings, taxonomies, enrichment
# batches, Stripe's unused subscription table, and Murmur are deliberately
# outside this backup boundary.
WEB_POSTGRES_TABLES = (
    ("drizzle", "__drizzle_migrations"),
    ("public", "user"),
    ("public", "session"),
    ("public", "account"),
    ("public", "verification"),
    ("public", "user_preferences"),
    ("public", "industry"),
    ("public", "company"),
    ("public", "job_board"),
    ("public", "saved_job"),
    ("public", "application_interview"),
    ("public", "followed_company"),
    ("public", "company_request"),
    ("public", "hiring_signal"),
    ("public", "outreach_draft"),
    ("public", "watchlist"),
    ("public", "watchlist_company"),
)
WEB_POSTGRES_SEQUENCES = (("drizzle", "__drizzle_migrations_id_seq"),)
WEB_POSTGRES_CONTRACT_CREATED_AT = 1_785_757_200_000
WEB_POSTGRES_CONTRACT_HASH = "eec5962093a1eb8a7058f9bf031877d148718e2531eaa981b86c5c6bc51165ab"
WEB_POSTGRES_SAVED_JOB_TEXT_CHECK_DEFINITION = (
    "CHECK (NULLIF(btrim(posting_title), ''::text) IS NOT NULL AND "
    "NULLIF(btrim(posting_source_url), ''::text) IS NOT NULL AND "
    "NULLIF(btrim(company_name), ''::text) IS NOT NULL AND "
    "NULLIF(btrim(company_slug), ''::text) IS NOT NULL)"
)
_REDACTIONS = (
    (
        re.compile(r"(?i)(api[-_ ]?key|password|secret|token)([=: ]+)[^\s,;]+"),
        r"\1\2<redacted>",
    ),
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s]+"), r"\1<redacted>"),
    (re.compile(r"(?i)postgres(?:ql)?://[^\s'\"]+"), "postgresql://<redacted>"),
)
TYPESENSE_REVIEWED_MEMORY_POLICIES = frozenset(
    {
        (3 * 1024**3, 2560 * 1024**2, 3 * 1024**3),
        (6 * 1024**3, 5 * 1024**3, 6 * 1024**3),
    }
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
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
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


def write_status(service: str, record: dict[str, Any], status_dir: Path = STATUS_DIR) -> None:
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
        "last_success_details": previous.get("last_success_details", {}),
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
    record["last_success_details"] = details
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


def _postgres_archive_uses_repository_lock(container: str) -> bool:
    inspected = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Config.Cmd}}", container],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return (
        inspected.returncode == 0
        and "flock -s /var/spool/pgbackrest/repository.lock" in inspected.stdout
    )


def _archive_push_active() -> bool:
    processes = subprocess.run(
        ["ps", "-eo", "args="],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if processes.returncode:
        raise BackupError("unable to inspect active pgBackRest processes")
    return any(
        line.strip().startswith("pgbackrest ") and "archive-push" in line
        for line in processes.stdout.splitlines()
    )


@contextmanager
def postgres_archive_hold(spool_dir: Path, container: str):
    """Pause new archive-push commands while repository expiration owns I/O."""

    sentinel = spool_dir / "archive-enabled"
    hold = spool_dir / "archive-enabled.retention-hold"
    repository_lock = spool_dir / "repository.lock"
    if sentinel.is_symlink():
        raise BackupError("the PostgreSQL archive sentinel is unsafe")
    enabled = sentinel.exists()
    drain_seconds = float(os.environ.get("PGBACKREST_ARCHIVE_DRAIN_SECONDS", "60"))
    if not 0 <= drain_seconds <= 600:
        raise BackupError("PGBACKREST_ARCHIVE_DRAIN_SECONDS must be between 0 and 600")
    if hold.exists() or hold.is_symlink():
        raise BackupError("a PostgreSQL archive-retention hold already exists")
    if enabled and not sentinel.is_file():
        raise BackupError("the PostgreSQL archive sentinel is unsafe")
    archive_uses_lock = _postgres_archive_uses_repository_lock(container)
    lock_existed = repository_lock.exists()
    if repository_lock.is_symlink():
        raise BackupError("the pgBackRest repository lock is unsafe")
    descriptor = os.open(
        repository_lock,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    lock_metadata = os.fstat(descriptor)
    if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_nlink != 1:
        os.close(descriptor)
        raise BackupError("the pgBackRest repository lock is unsafe")
    spool_metadata = spool_dir.stat()
    if not lock_existed:
        os.fchown(descriptor, spool_metadata.st_uid, spool_metadata.st_gid)
        os.fchmod(descriptor, 0o600)
    elif (
        lock_metadata.st_uid != spool_metadata.st_uid
        or lock_metadata.st_gid != spool_metadata.st_gid
        or stat.S_IMODE(lock_metadata.st_mode) != 0o600
    ):
        os.close(descriptor)
        raise BackupError("the pgBackRest repository lock ownership or mode is unsafe")
    sentinel_held = False
    try:
        if enabled and not archive_uses_lock:
            sentinel.rename(hold)
            sentinel_held = True
            # Close the race with an archive_command that passed its sentinel
            # check immediately before the atomic rename, then wait out any
            # archive-push worker already using the repository.
            time.sleep(min(drain_seconds, 2))
        deadline = time.monotonic() + max(0, drain_seconds - 2)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BackupError(
                        "timed out acquiring the pgBackRest repository lock"
                    ) from None
                time.sleep(1)
        if not archive_uses_lock:
            while True:
                if not _archive_push_active():
                    break
                if time.monotonic() >= deadline:
                    raise BackupError("timed out draining active PostgreSQL archive-push workers")
                time.sleep(1)
        yield
    finally:
        os.close(descriptor)
        if sentinel_held:
            if hold.is_symlink() or not hold.is_file():
                raise BackupError("the PostgreSQL archive-retention hold was altered")
            if sentinel.exists() or sentinel.is_symlink():
                raise BackupError("the PostgreSQL archive sentinel changed during retention")
            hold.replace(sentinel)


def postgres_expire_archives(stanza: str) -> dict[str, int | float]:
    """Expire obsolete WAL without depending on the live database container.

    The repository can fill while PostgreSQL is unavailable. Running expiration
    in a networkless one-shot container before the database health gate keeps
    that failure recoverable and applies the reviewed retention contract even
    if a root-only host config has not yet been reconciled.
    """

    image = os.environ.get("PGBACKREST_IMAGE", "jobseek-postgres:16-pgbackrest")
    config_dir = Path(os.environ.get("PGBACKREST_CONFIG_DIR", "/etc/jobseek-backup/postgresql"))
    repository_dir = Path(
        os.environ.get("PGBACKREST_REPOSITORY_DIR", "/mnt/jobseek-postgresql-backups")
    )
    spool_dir = Path(
        os.environ.get("PGBACKREST_SPOOL_DIR", "/var/lib/jobseek-backup/postgresql/spool")
    )
    container = os.environ.get("POSTGRES_CONTAINER", "postgres")
    with postgres_archive_hold(spool_dir, container):
        run_checked(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--user",
                "postgres",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "512m",
                "--cpus",
                "1.0",
                "--pids-limit",
                "64",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=64m",
                "--entrypoint",
                "pgbackrest",
                "--volume",
                f"{config_dir}:/etc/jobseek-backup:ro",
                "--volume",
                f"{config_dir / 'pgbackrest.conf'}:/etc/pgbackrest/pgbackrest.conf:ro",
                "--volume",
                f"{spool_dir}:/var/spool/pgbackrest:ro",
                "--volume",
                f"{repository_dir}:{repository_dir}",
                image,
                f"--stanza={stanza}",
                "--log-level-file=off",
                *POSTGRES_RETENTION_OPTIONS,
                "expire",
            ],
            timeout=7_200,
        )
    usage = shutil.disk_usage(repository_dir)
    return {
        "repository_capacity_bytes": usage.total,
        "repository_available_bytes": usage.free,
        "repository_available_ratio": usage.free / usage.total,
    }


def postgres_backup(backup_type: str) -> dict[str, Any]:
    container = os.environ.get("POSTGRES_CONTAINER", "postgres")
    stanza = os.environ.get("PGBACKREST_STANZA", "jobseek")
    if backup_type == "auto":
        backup_type = "full" if utc_now().isoweekday() == 7 else "diff"
    if backup_type not in {"full", "diff", "incr"}:
        raise BackupError(f"unsupported PostgreSQL backup type: {backup_type}")

    retention = postgres_expire_archives(stanza)

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
    run_checked(
        [
            *base,
            *POSTGRES_RETENTION_OPTIONS,
            f"--type={backup_type}",
            "backup",
        ],
        timeout=43_200,
    )
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
        **retention,
        "backup_type": latest.get("type", backup_type),
        "backup_label": latest.get("label"),
        "backup_database_bytes": latest.get("info", {}).get("size"),
        # pgBackRest 2.59 reports per-backup repository bytes as `delta`;
        # retain the older `size` fallback for compatible package releases.
        "backup_repository_bytes": repository_info.get("delta", repository_info.get("size")),
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
        raise BackupError(f"Typesense snapshot API did not report success: {redact(body)}")


_TYPESENSE_ALIASES = (
    "company",
    "job_posting",
    "location",
    "occupation",
    "seniority",
    "technology",
    "watchlist",
)
_TYPESENSE_ALIAS_STABILITY_ATTEMPTS = 3
_TYPESENSE_ALIAS_STABILITY_RETRY_SECONDS = 5


def _typesense_json_get(url: str, api_key: str, path: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{url.rstrip('/')}{path}",
        headers={"X-TYPESENSE-API-KEY": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.load(response)
    except Exception as exc:
        raise BackupError(f"Typesense inventory API failed: {redact(str(exc))}") from exc
    if not isinstance(payload, dict):
        raise BackupError("Typesense inventory API returned a non-object")
    return payload


def _typesense_inventory(url: str, api_key: str) -> dict[str, Any]:
    alias_payload = _typesense_json_get(url, api_key, "/aliases")
    try:
        alias_rows = alias_payload["aliases"]
    except (KeyError, TypeError) as exc:
        raise BackupError("Typesense alias inventory returned an unexpected shape") from exc
    if not isinstance(alias_rows, list):
        raise BackupError("Typesense alias inventory returned an unexpected shape")
    aliases: dict[str, str] = {}
    for item in alias_rows:
        try:
            name = item["name"]
            target = item["collection_name"]
        except (KeyError, TypeError) as exc:
            raise BackupError("Typesense alias inventory returned an unexpected shape") from exc
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(target, str)
            or not target
            or name in aliases
        ):
            raise BackupError("Typesense alias inventory returned an unexpected shape")
        aliases[name] = target
    if len(alias_rows) != len(_TYPESENSE_ALIASES) or set(aliases) != set(_TYPESENSE_ALIASES):
        raise BackupError("Typesense alias inventory is incomplete")
    collection_documents: dict[str, int] = {}
    for alias in _TYPESENSE_ALIASES:
        target = aliases[alias]
        collection = _typesense_json_get(
            url,
            api_key,
            f"/collections/{urllib.parse.quote(target, safe='')}",
        )
        try:
            collection_name = collection["name"]
            count = int(collection["num_documents"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupError(f"Typesense collection inventory is invalid for {alias}") from exc
        if collection_name != target:
            raise BackupError(f"Typesense alias target is invalid for {alias}")
        if count < 0:
            raise BackupError(f"Typesense collection inventory is negative for {alias}")
        collection_documents[alias] = count
    return {"aliases": aliases, "collection_documents": collection_documents}


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _directory_metadata(path: Path, description: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BackupError(f"{description} is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise BackupError(f"{description} is not a real directory")
    return metadata


def _remove_typesense_packet(path: Path, description: str) -> None:
    """Remove one known packet without following a replaced directory entry."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BackupError(f"{description} is not inspectable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise BackupError(f"{description} is not a real directory")
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise BackupError(f"{description} could not be removed safely") from exc
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BackupError(f"{description} removal could not be verified") from exc
    raise BackupError(f"{description} remained after cleanup")


def _remove_old_staging(staging_root: Path, *, older_than_seconds: int = 172_800) -> None:
    try:
        metadata = staging_root.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BackupError("backup staging root is not inspectable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise BackupError("backup staging root is not a real directory")
    cutoff = time.time() - older_than_seconds
    for child in staging_root.iterdir():
        try:
            metadata = child.lstat()
        except OSError as exc:
            raise BackupError("backup staging entry is not inspectable") from exc
        if stat.S_ISDIR(metadata.st_mode) and metadata.st_mtime < cutoff:
            _remove_typesense_packet(child, "expired backup staging packet")


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise BackupError(f"{name} must be an integer") from exc
    if value <= 0:
        raise BackupError(f"{name} must be positive")
    return value


def _validate_typesense_snapshot_staging(
    container: str,
    host_root: Path,
    container_mount_root: str,
) -> dict[str, Any]:
    """Prove isolated staging, bounded-memory policy, and write headroom."""
    live_root = Path(os.environ.get("TYPESENSE_LIVE_DATA_HOST_ROOT", "/mnt/typesense-data"))
    try:
        resolved_host = host_root.resolve(strict=True)
        resolved_live = live_root.resolve(strict=True)
        host_stat = resolved_host.stat()
    except OSError as exc:
        raise BackupError("Typesense snapshot staging or live data path is unavailable") from exc
    if resolved_host != host_root or host_root.is_symlink() or not host_root.is_mount():
        raise BackupError("Typesense snapshot staging is not an exact dedicated mount")
    if host_stat.st_uid != 0 or host_stat.st_gid != 0 or stat.S_IMODE(host_stat.st_mode) != 0o700:
        raise BackupError("Typesense snapshot staging ownership or mode is unsafe")
    if host_stat.st_dev in {Path("/").stat().st_dev, resolved_live.stat().st_dev}:
        raise BackupError("Typesense snapshot staging is not isolated from root and live data")

    staging_root = host_root / "staging"
    try:
        staging_root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise BackupError("Typesense snapshot staging root could not be created") from exc
    staging_stat = _directory_metadata(staging_root, "Typesense snapshot staging root")
    if (
        staging_stat.st_dev != host_stat.st_dev
        or staging_stat.st_uid != 0
        or staging_stat.st_gid != 0
        or stat.S_IMODE(staging_stat.st_mode) != 0o700
    ):
        raise BackupError("Typesense snapshot staging root ownership, mode, or device is unsafe")
    _remove_old_staging(staging_root)
    _remove_old_staging(staging_root / ".attempts")
    usage = shutil.disk_usage(host_root)
    minimum_capacity = _positive_int_env("TYPESENSE_SNAPSHOT_MIN_CAPACITY_BYTES", 20 * 1024**3)
    minimum_free = _positive_int_env("TYPESENSE_SNAPSHOT_MIN_FREE_BYTES", 8 * 1024**3)
    growth_reserve = _positive_int_env("TYPESENSE_SNAPSHOT_GROWTH_RESERVE_BYTES", 4 * 1024**3)
    if usage.total < minimum_capacity:
        raise BackupError("Typesense snapshot staging is smaller than the required capacity")
    live_usage = run_checked(
        ["du", "--summarize", "--one-file-system", "--block-size=1", str(resolved_live)],
        timeout=300,
    ).stdout.split()
    try:
        live_allocated = int(live_usage[0])
    except (IndexError, ValueError) as exc:
        raise BackupError("Typesense live-data allocation is not measurable") from exc
    required_before = minimum_free + growth_reserve + live_allocated
    if usage.free < required_before:
        raise BackupError(
            "Typesense snapshot staging lacks snapshot, growth, and free-floor headroom"
        )

    try:
        inspected = json.loads(run_checked(["docker", "inspect", container], timeout=30).stdout)
        container_info = inspected[0]
        host_config = container_info["HostConfig"]
        mounts = container_info["Mounts"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BackupError("Typesense container headroom contract is not inspectable") from exc
    if not isinstance(mounts, list) or not any(
        isinstance(mount, dict)
        and mount.get("Source") == str(host_root)
        and mount.get("Destination") == container_mount_root
        and mount.get("RW") is True
        for mount in mounts
    ):
        raise BackupError("Typesense snapshot staging is not mounted into the container")

    memory = int(host_config.get("Memory") or 0)
    reservation = int(host_config.get("MemoryReservation") or 0)
    memory_swap = int(host_config.get("MemorySwap") or 0)
    memory_policy_phase = os.environ.get("TYPESENSE_MEMORY_POLICY_PHASE", "enforced")
    if memory_policy_phase != "enforced":
        raise BackupError("Typesense memory policy phase is not recognized")
    # The legacy tuple remains accepted only across the staged CX33 migration:
    # the old backup runner must remain valid until the expanded host contract
    # disables its timer, and the new runner must smoke the expanded container.
    # A follow-up removes the legacy tuple after the promotion is proven.
    if (memory, reservation, memory_swap) not in TYPESENSE_REVIEWED_MEMORY_POLICIES:
        raise BackupError("Typesense container does not enforce the reviewed memory policy")
    return {
        "staging_capacity_bytes": usage.total,
        "staging_available_bytes_before": usage.free,
        "staging_minimum_capacity_bytes": minimum_capacity,
        "staging_minimum_free_bytes": minimum_free,
        "staging_growth_reserve_bytes": growth_reserve,
        "staging_required_bytes_before": required_before,
        "live_data_allocated_bytes_before": live_allocated,
        "memory_limit_bytes": memory,
        "memory_reservation_bytes": reservation,
        "memory_swap_limit_bytes": memory_swap,
        "memory_policy_phase": memory_policy_phase,
        "memory_limit_enforced": True,
    }


def _typesense_local_snapshot_packets(staging_root: Path) -> list[Path]:
    """Return all materialized or in-progress local packets, failing on odd entries."""
    if not staging_root.exists():
        return []
    _directory_metadata(staging_root, "Typesense snapshot staging root")
    packets: list[Path] = []
    for child in staging_root.iterdir():
        try:
            child_metadata = child.lstat()
        except OSError as exc:
            raise BackupError("Typesense snapshot staging contains an unreadable entry") from exc
        if child.name == ".attempts":
            if not stat.S_ISDIR(child_metadata.st_mode):
                raise BackupError("Typesense snapshot attempts root is unsafe")
            for entry in child.iterdir():
                try:
                    entry_metadata = entry.lstat()
                except OSError as exc:
                    raise BackupError(
                        "Typesense snapshot attempts contain an unreadable entry"
                    ) from exc
                if not stat.S_ISDIR(entry_metadata.st_mode):
                    raise BackupError("Typesense snapshot attempts contain an unsafe entry")
                packets.append(entry)
            continue
        if not stat.S_ISDIR(child_metadata.st_mode):
            raise BackupError("Typesense snapshot staging contains an unsafe entry")
        packets.append(child)
    return packets


def _restic_command(*arguments: str) -> list[str]:
    sftp_command = os.environ.get("RESTIC_SFTP_COMMAND", "")
    if not sftp_command:
        raise BackupError("RESTIC_SFTP_COMMAND is missing")
    return ["restic", "-o", f"sftp.command={sftp_command}", *arguments]


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _qualified_table(schema: str, table: str) -> str:
    return f"{_quote_identifier(schema)}.{_quote_identifier(table)}"


def _web_database_url() -> str:
    value = os.environ.get("WEB_DATABASE_URL", "").strip()
    if not value:
        credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
        if credentials_directory:
            credential = Path(credentials_directory) / "web-database-url"
            try:
                value = credential.read_text(encoding="utf-8").strip()
            except OSError:
                value = ""
    if not value:
        raise BackupError("WEB_DATABASE_URL credential is missing")
    if not value.startswith(("postgres://", "postgresql://")):
        raise BackupError("WEB_DATABASE_URL must be a PostgreSQL connection URI")
    return value


def _web_postgres_image() -> str:
    image = os.environ.get("WEB_POSTGRES_IMAGE", WEB_POSTGRES_IMAGE)
    if "@sha256:" not in image:
        raise BackupError("WEB_POSTGRES_IMAGE must be digest-pinned")
    return image


def _require_web_postgres_helper_image() -> None:
    """Require the exact image and its stopped GC-protection lease."""
    image = _web_postgres_image()
    run_checked(["docker", "image", "inspect", image], timeout=30)
    inspected = run_checked(
        ["docker", "container", "inspect", WEB_POSTGRES_IMAGE_LEASE], timeout=30
    )
    try:
        payload = json.loads(inspected.stdout)
        container = payload[0]
        config = container["Config"]
        state = container["State"]
        host_config = container["HostConfig"]
        labels = config.get("Labels") or {}
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BackupError("web PostgreSQL helper-image lease is invalid") from exc
    if (
        config.get("Image") != image
        or state.get("Running") is not False
        or labels.get(WEB_POSTGRES_IMAGE_LEASE_LABEL) != "web-postgresql"
        or config.get("Entrypoint") != ["/bin/true"]
        or host_config.get("NetworkMode") != "none"
        or host_config.get("ReadonlyRootfs") is not True
        or host_config.get("CapDrop") != ["ALL"]
        or host_config.get("SecurityOpt") != ["no-new-privileges:true"]
        or host_config.get("Tmpfs") != WEB_POSTGRES_IMAGE_LEASE_TMPFS
        or container.get("Mounts") != []
    ):
        raise BackupError("web PostgreSQL helper-image lease does not protect the pinned digest")


def _web_postgres_env() -> dict[str, str]:
    env = os.environ.copy()
    password_file_value = os.environ.get("WEB_DATABASE_PASSWORD_FILE", "").strip()
    if password_file_value:
        password_file = Path(password_file_value)
        try:
            metadata = password_file.lstat()
        except OSError as exc:
            raise BackupError("WEB_DATABASE_PASSWORD_FILE is not readable") from exc
        if (
            not password_file.is_absolute()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise BackupError("WEB_DATABASE_PASSWORD_FILE must be an absolute private regular file")
        connection = {
            "WEB_DATABASE_HOST": os.environ.get("WEB_DATABASE_HOST", "").strip(),
            "WEB_DATABASE_PORT": os.environ.get("WEB_DATABASE_PORT", "5432").strip(),
            "WEB_DATABASE_USER": os.environ.get("WEB_DATABASE_USER", "").strip(),
            "WEB_DATABASE_NAME": os.environ.get("WEB_DATABASE_NAME", "").strip(),
        }
        if any(
            not value or any(character.isspace() for character in value)
            for value in connection.values()
        ):
            raise BackupError("web database password-file connection fields are invalid")
        try:
            port = int(connection["WEB_DATABASE_PORT"])
        except ValueError as exc:
            raise BackupError("WEB_DATABASE_PORT is invalid") from exc
        if not 1 <= port <= 65_535:
            raise BackupError("WEB_DATABASE_PORT is invalid")
        env.update(connection)
        env["WEB_DATABASE_PASSWORD_FILE"] = str(password_file)
    else:
        env["WEB_DATABASE_URL"] = _web_database_url()
    # PGDATABASE is only a database-name default. The PostgreSQL 17 client
    # does not expand a connection URI supplied through that environment
    # variable, so it falls back to the container-local Unix socket. Keep the
    # complete credential in a dedicated variable and pass it explicitly as
    # --dbname from inside the short-lived client container.
    env["PGCONNECT_TIMEOUT"] = "30"
    env["PGAPPNAME"] = "jobseek-web-postgresql-backup"
    return env


def _web_postgres_client_command(
    *arguments: str,
    network: str | None = None,
    mounts: Sequence[tuple[Path, str, str]] = (),
    database_env: bool = True,
) -> list[str]:
    resolved_network = network or os.environ.get("WEB_POSTGRES_NETWORK", "host")
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network",
        resolved_network,
    ]
    password_file = os.environ.get("WEB_DATABASE_PASSWORD_FILE", "").strip()
    if database_env:
        if password_file:
            command.extend(
                (
                    "--env",
                    "PGPASSFILE=/run/secrets/web-database-pgpass",
                    "--env",
                    "WEB_DATABASE_HOST",
                    "--env",
                    "WEB_DATABASE_PORT",
                    "--env",
                    "WEB_DATABASE_USER",
                    "--env",
                    "WEB_DATABASE_NAME",
                    "--env",
                    "PGCONNECT_TIMEOUT",
                    "--env",
                    "PGAPPNAME",
                    "--volume",
                    f"{password_file}:/run/secrets/web-database-pgpass:ro",
                )
            )
        else:
            command.extend(
                (
                    "--env",
                    "WEB_DATABASE_URL",
                    "--env",
                    "PGCONNECT_TIMEOUT",
                    "--env",
                    "PGAPPNAME",
                )
            )
    for host_path, container_path, mode in mounts:
        command.extend(("--volume", f"{host_path}:{container_path}:{mode}"))
    command.append(_web_postgres_image())
    if database_env:
        if password_file:
            command.extend(
                (
                    "sh",
                    "-ceu",
                    'client="$1"; shift; exec "$client" '
                    '--host="$WEB_DATABASE_HOST" --port="$WEB_DATABASE_PORT" '
                    '--username="$WEB_DATABASE_USER" --dbname="$WEB_DATABASE_NAME" "$@"',
                    "jobseek-web-postgresql-client",
                    *arguments,
                )
            )
        else:
            # Expand the URI only inside the container. This makes libpq parse
            # the URI while keeping it out of the host command line and logs.
            command.extend(
                (
                    "sh",
                    "-ceu",
                    'client="$1"; shift; exec "$client" --dbname="$WEB_DATABASE_URL" "$@"',
                    "jobseek-web-postgresql-client",
                    *arguments,
                )
            )
    else:
        command.extend(arguments)
    return command


def _web_psql(sql: str, *, env: dict[str, str]) -> str:
    return run_checked(
        _web_postgres_client_command(
            "psql",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--field-separator=|",
            "--command",
            sql,
        ),
        env=env,
        timeout=600,
    ).stdout.strip()


def _included_tables_values_sql() -> str:
    values = ", ".join(
        f"({_quote_literal(schema)}, {_quote_literal(table)})"
        for schema, table in WEB_POSTGRES_TABLES
    )
    return f"VALUES {values}"


def _web_postgres_bootstrap_sql() -> str:
    schemas = sorted(
        {
            schema
            for schema, _relation in (*WEB_POSTGRES_TABLES, *WEB_POSTGRES_SEQUENCES)
            if schema != "public"
        }
    )
    return "".join(
        f"CREATE SCHEMA IF NOT EXISTS {_quote_identifier(schema)};\n" for schema in schemas
    )


def _validate_web_postgres_boundary(*, env: dict[str, str]) -> None:
    """Fail if the allowlist is missing or depends on an excluded table."""
    values = _included_tables_values_sql()
    output = _web_psql(
        f"""
        WITH included(schema_name, table_name) AS ({values}),
        missing AS (
          SELECT 'missing|' || format('%I.%I', schema_name, table_name) AS problem
          FROM included
          WHERE to_regclass(format('%I.%I', schema_name, table_name)) IS NULL
        ),
        external_fks AS (
          SELECT DISTINCT
            'external_fk|' || format('%I.%I -> %I.%I',
              source_ns.nspname, source.relname, target_ns.nspname, target.relname
            ) AS problem
          FROM pg_constraint constraint_row
          JOIN pg_class source ON source.oid = constraint_row.conrelid
          JOIN pg_namespace source_ns ON source_ns.oid = source.relnamespace
          JOIN pg_class target ON target.oid = constraint_row.confrelid
          JOIN pg_namespace target_ns ON target_ns.oid = target.relnamespace
          JOIN included source_included
            ON source_included.schema_name = source_ns.nspname
           AND source_included.table_name = source.relname
          LEFT JOIN included target_included
            ON target_included.schema_name = target_ns.nspname
           AND target_included.table_name = target.relname
          WHERE constraint_row.contype = 'f'
            AND target_included.table_name IS NULL
        )
        SELECT problem FROM missing
        UNION ALL
        SELECT problem FROM external_fks
        ORDER BY 1
        """,
        env=env,
    )
    if output:
        raise BackupError(
            "web PostgreSQL backup boundary is not self-contained: "
            + "; ".join(output.splitlines())
        )
    for schema, sequence in WEB_POSTGRES_SEQUENCES:
        qualified = _qualified_table(schema, sequence)
        output = _web_psql(
            "SELECT CASE WHEN relation.relkind = 'S' THEN '' ELSE "
            f"'missing_sequence|{qualified}' END "
            f"FROM (SELECT to_regclass({_quote_literal(qualified)}) AS oid) selected "
            "LEFT JOIN pg_class relation ON relation.oid = selected.oid",
            env=env,
        )
        if output:
            raise BackupError("web PostgreSQL backup boundary is not self-contained: " + output)
    _validate_web_postgres_contract(env=env)


def _validate_web_postgres_contract(*, env: dict[str, str]) -> None:
    """Require the exact 0085 ledger row and durable saved-job catalog."""
    output = _web_psql(
        f"""
        WITH expected_column(column_name, data_type, is_required) AS (
          VALUES
            ('job_posting_id', 'uuid', true),
            ('posting_title', 'text', true),
            ('posting_source_url', 'text', true),
            ('posting_first_seen_at', 'timestamp with time zone', true),
            ('posting_is_active', 'boolean', true),
            ('posting_salary_min', 'integer', false),
            ('posting_salary_max', 'integer', false),
            ('posting_salary_currency', 'text', false),
            ('posting_salary_period', 'text', false),
            ('company_id', 'uuid', true),
            ('company_name', 'text', true),
            ('company_slug', 'text', true),
            ('company_icon', 'text', false)
        ),
        column_problem AS (
          SELECT expected_column.column_name
          FROM expected_column
          LEFT JOIN pg_attribute AS attribute
            ON attribute.attrelid = 'public.saved_job'::regclass
           AND attribute.attname = expected_column.column_name
           AND NOT attribute.attisdropped
          LEFT JOIN pg_attrdef AS default_value
            ON default_value.adrelid = attribute.attrelid
           AND default_value.adnum = attribute.attnum
          WHERE attribute.attnum IS NULL
             OR format_type(attribute.atttypid, attribute.atttypmod)
                  <> expected_column.data_type
             OR attribute.attnotnull <> expected_column.is_required
             OR default_value.oid IS NOT NULL
        ),
        permanent_check AS (
          SELECT pg_get_constraintdef(oid, true) AS definition
          FROM pg_constraint
          WHERE conrelid = 'public.saved_job'::regclass
            AND conname = 'saved_job_snapshot_text_nonblank_check'
            AND contype = 'c'
            AND convalidated
        )
        SELECT 'contract_ledger' AS problem
        WHERE (
          SELECT count(*)
          FROM drizzle.__drizzle_migrations
          WHERE created_at = {WEB_POSTGRES_CONTRACT_CREATED_AT}
            AND hash = {_quote_literal(WEB_POSTGRES_CONTRACT_HASH)}
        ) <> 1
           OR (
             SELECT count(*)
             FROM drizzle.__drizzle_migrations
             WHERE created_at = {WEB_POSTGRES_CONTRACT_CREATED_AT}
           ) <> 1
        UNION ALL
        SELECT 'saved_job_columns'
        WHERE EXISTS (SELECT 1 FROM column_problem)
        UNION ALL
        SELECT 'saved_job_required_values'
        WHERE EXISTS (
          SELECT 1
          FROM public.saved_job
          WHERE NULLIF(btrim(posting_title), '') IS NULL
             OR NULLIF(btrim(posting_source_url), '') IS NULL
             OR posting_first_seen_at IS NULL
             OR posting_is_active IS NULL
             OR company_id IS NULL
             OR NULLIF(btrim(company_name), '') IS NULL
             OR NULLIF(btrim(company_slug), '') IS NULL
        )
        UNION ALL
        SELECT 'saved_job_permanent_check'
        WHERE (SELECT count(*) FROM permanent_check) <> 1
           OR NOT EXISTS (
             SELECT 1
             FROM permanent_check
             WHERE definition = {_quote_literal(WEB_POSTGRES_SAVED_JOB_TEXT_CHECK_DEFINITION)}
           )
        UNION ALL
        SELECT 'saved_job_temporary_check'
        WHERE EXISTS (
          SELECT 1
          FROM pg_constraint
          WHERE conrelid = 'public.saved_job'::regclass
            AND conname = 'saved_job_required_snapshot_check'
        )
        UNION ALL
        SELECT 'saved_job_compatibility_trigger'
        WHERE EXISTS (
          SELECT 1
          FROM pg_trigger
          WHERE tgrelid = 'public.saved_job'::regclass
            AND tgname = 'saved_job_snapshot_from_mirror_before_insert'
            AND NOT tgisinternal
        )
           OR to_regprocedure('public.saved_job_snapshot_from_mirror()') IS NOT NULL
        UNION ALL
        SELECT 'saved_job_posting_fk'
        WHERE EXISTS (
          SELECT 1
          FROM pg_constraint AS constraint_row
          WHERE constraint_row.conrelid = 'public.saved_job'::regclass
            AND constraint_row.contype = 'f'
            AND constraint_row.conkey = ARRAY[
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.saved_job'::regclass
                  AND attname = 'job_posting_id'
                  AND NOT attisdropped
              )
            ]::smallint[]
        )
        UNION ALL
        SELECT 'saved_job_user_fk'
        WHERE (
          SELECT count(*)
          FROM pg_constraint AS constraint_row
          WHERE constraint_row.conrelid = 'public.saved_job'::regclass
            AND constraint_row.confrelid = 'public.user'::regclass
            AND constraint_row.conname = 'saved_job_user_id_user_id_fk'
            AND constraint_row.contype = 'f'
            AND constraint_row.convalidated
            AND constraint_row.confdeltype = 'c'
            AND constraint_row.confupdtype = 'a'
            AND constraint_row.conkey = ARRAY[
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.saved_job'::regclass
                  AND attname = 'user_id'
                  AND NOT attisdropped
              )
            ]::smallint[]
            AND constraint_row.confkey = ARRAY[
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.user'::regclass
                  AND attname = 'id'
                  AND NOT attisdropped
              )
            ]::smallint[]
        ) <> 1
        UNION ALL
        SELECT 'saved_job_unique_index'
        WHERE (
          SELECT count(*)
          FROM pg_index
          WHERE indexrelid = to_regclass('public.idx_sj_user_posting')
            AND indrelid = 'public.saved_job'::regclass
            AND indisunique
            AND indisvalid
            AND indisready
            AND indpred IS NULL
            AND indexprs IS NULL
            AND indnkeyatts = 2
            AND indkey::text = format(
              '%s %s',
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.saved_job'::regclass
                  AND attname = 'user_id'
                  AND NOT attisdropped
              ),
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.saved_job'::regclass
                  AND attname = 'job_posting_id'
                  AND NOT attisdropped
              )
            )
        ) <> 1
        UNION ALL
        SELECT 'application_interview_saved_job_fk'
        WHERE (
          SELECT count(*)
          FROM pg_constraint
          WHERE conrelid = 'public.application_interview'::regclass
            AND confrelid = 'public.saved_job'::regclass
            AND conname = 'application_interview_saved_job_id_fkey'
            AND contype = 'f'
            AND convalidated
            AND confdeltype = 'c'
            AND confupdtype = 'a'
            AND conkey = ARRAY[
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.application_interview'::regclass
                  AND attname = 'saved_job_id'
                  AND NOT attisdropped
              )
            ]::smallint[]
            AND confkey = ARRAY[
              (
                SELECT attnum
                FROM pg_attribute
                WHERE attrelid = 'public.saved_job'::regclass
                  AND attname = 'id'
                  AND NOT attisdropped
              )
            ]::smallint[]
        ) <> 1
        ORDER BY 1
        """,
        env=env,
    )
    if output:
        raise BackupError(
            "web PostgreSQL saved-job contract is not the exact 0085 catalog: "
            + "; ".join(output.splitlines())
        )


def _web_postgres_fingerprints(*, env: dict[str, str]) -> dict[str, dict[str, Any]]:
    selects: list[str] = []
    for schema, table in WEB_POSTGRES_TABLES:
        key = f"{schema}.{table}".replace("'", "''")
        qualified = _qualified_table(schema, table)
        selects.append(
            "SELECT "
            f"'{key}' AS table_name, "
            "count(*)::bigint AS row_count, "
            "md5(COALESCE(string_agg(md5(to_jsonb(row_value)::text), '' "
            "ORDER BY md5(to_jsonb(row_value)::text)), '')) AS row_digest "
            f"FROM {qualified} AS row_value"
        )
    output = _web_psql(" UNION ALL ".join(selects) + " ORDER BY 1", env=env)
    fingerprints: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3 or not parts[1].isdigit() or not re.fullmatch(r"[0-9a-f]{32}", parts[2]):
            raise BackupError("web PostgreSQL fingerprint output was not parseable")
        fingerprints[parts[0]] = {
            "rows": int(parts[1]),
            "digest": parts[2],
        }
    expected = {f"{schema}.{table}" for schema, table in WEB_POSTGRES_TABLES}
    if set(fingerprints) != expected:
        raise BackupError("web PostgreSQL fingerprint omitted an allowlisted table")
    return fingerprints


def _web_postgres_sequence_fingerprints(*, env: dict[str, str]) -> dict[str, dict[str, Any]]:
    selects = [
        "SELECT "
        f"{_quote_literal(f'{schema}.{sequence}')} AS sequence_name, "
        "last_value::bigint, is_called "
        f"FROM {_qualified_table(schema, sequence)}"
        for schema, sequence in WEB_POSTGRES_SEQUENCES
    ]
    output = _web_psql(" UNION ALL ".join(selects) + " ORDER BY 1", env=env)
    fingerprints: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        parts = line.split("|", 2)
        if (
            len(parts) != 3
            or not re.fullmatch(r"-?[0-9]+", parts[1])
            or parts[2]
            not in {
                "t",
                "f",
            }
        ):
            raise BackupError("web PostgreSQL sequence fingerprint output was not parseable")
        fingerprints[parts[0]] = {
            "last_value": int(parts[1]),
            "is_called": parts[2] == "t",
        }
    expected = {f"{schema}.{sequence}" for schema, sequence in WEB_POSTGRES_SEQUENCES}
    if set(fingerprints) != expected:
        raise BackupError("web PostgreSQL fingerprint omitted an allowlisted sequence")
    return fingerprints


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_web_postgres_archive(output: str) -> None:
    table_definitions: set[str] = set()
    table_data: set[str] = set()
    sequence_definitions: set[str] = set()
    sequence_state: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 7 or not fields[0].endswith(";"):
            continue
        object_type = fields[3]
        if object_type == "TABLE" and fields[4] == "DATA":
            table_data.add(f"{fields[5]}.{fields[6]}")
        elif object_type == "TABLE":
            table_definitions.add(f"{fields[4]}.{fields[5]}")
        elif object_type == "SEQUENCE" and fields[4] == "SET":
            sequence_state.add(f"{fields[5]}.{fields[6]}")
        elif object_type == "SEQUENCE" and fields[4] != "OWNED":
            sequence_definitions.add(f"{fields[4]}.{fields[5]}")
    expected_tables = {f"{schema}.{table}" for schema, table in WEB_POSTGRES_TABLES}
    expected_sequences = {f"{schema}.{sequence}" for schema, sequence in WEB_POSTGRES_SEQUENCES}
    if table_definitions != expected_tables or table_data != expected_tables:
        raise BackupError("web PostgreSQL archive table boundary is incomplete or unexpected")
    if sequence_definitions != expected_sequences or sequence_state != expected_sequences:
        raise BackupError("web PostgreSQL archive sequence boundary is incomplete or unexpected")


def web_postgresql_backup() -> dict[str, Any]:
    required_restic = (
        "RESTIC_REPOSITORY",
        "RESTIC_PASSWORD_FILE",
        "RESTIC_SFTP_COMMAND",
    )
    missing = [name for name in required_restic if not os.environ.get(name)]
    if missing:
        raise BackupError(f"missing Restic configuration: {', '.join(missing)}")

    _require_web_postgres_helper_image()
    env = _web_postgres_env()
    _validate_web_postgres_boundary(env=env)
    server_version = _web_psql("SHOW server_version", env=env)
    if not server_version.startswith("17."):
        raise BackupError(
            "web PostgreSQL server version "
            f"{server_version!r} is not supported by the pinned client"
        )

    run_id = utc_now().strftime("%Y%m%dT%H%M%SZ")
    staging_root = (
        Path(
            os.environ.get(
                "WEB_POSTGRES_STAGING_ROOT",
                "/run/jobseek-backup/web-postgresql",
            )
        )
        / "staging"
    )
    run_path = staging_root / run_id
    dump_path = run_path / "web-postgresql.dump"
    bootstrap_path = run_path / "bootstrap.sql"
    manifest_path = run_path / "manifest.json"
    _remove_old_staging(staging_root)
    run_path.mkdir(parents=True, mode=0o700)
    success = False

    try:
        before = _web_postgres_fingerprints(env=env)
        sequences_before = _web_postgres_sequence_fingerprints(env=env)
        atomic_write(bootstrap_path, _web_postgres_bootstrap_sql())
        dump_arguments = [
            "pg_dump",
            "--format=custom",
            "--compress=6",
            "--no-owner",
            "--no-privileges",
            "--strict-names",
            "--lock-wait-timeout=60000",
            "--serializable-deferrable",
            "--file=/backup/web-postgresql.dump",
        ]
        for schema, table in WEB_POSTGRES_TABLES:
            dump_arguments.extend(("--table", _qualified_table(schema, table)))
        for schema, sequence in WEB_POSTGRES_SEQUENCES:
            dump_arguments.extend(("--table", _qualified_table(schema, sequence)))
        run_checked(
            _web_postgres_client_command(
                *dump_arguments,
                mounts=((run_path, "/backup", "rw"),),
            ),
            env=env,
            timeout=3_600,
        )
        after = _web_postgres_fingerprints(env=env)
        sequences_after = _web_postgres_sequence_fingerprints(env=env)
        if before != after or sequences_before != sequences_after:
            raise BackupError(
                "web PostgreSQL changed while the logical dump was created; retrying is required"
            )
        if not dump_path.is_file() or dump_path.stat().st_size <= 0:
            raise BackupError("web PostgreSQL logical dump is empty")
        archive_listing = run_checked(
            _web_postgres_client_command(
                "pg_restore",
                "--list",
                "/backup/web-postgresql.dump",
                network="none",
                mounts=((run_path, "/backup", "ro"),),
                database_env=False,
            ),
            timeout=600,
        ).stdout
        _validate_web_postgres_archive(archive_listing)

        dump_sha256 = _sha256_file(dump_path)
        manifest = {
            "schema_version": 1,
            "created_at": utc_now().isoformat(),
            "server_version": server_version,
            "archive": dump_path.name,
            "archive_bytes": dump_path.stat().st_size,
            "archive_sha256": dump_sha256,
            "bootstrap": bootstrap_path.name,
            "bootstrap_sha256": _sha256_file(bootstrap_path),
            "tables": [f"{schema}.{table}" for schema, table in WEB_POSTGRES_TABLES],
            "sequences": [f"{schema}.{sequence}" for schema, sequence in WEB_POSTGRES_SEQUENCES],
            "fingerprints": before,
            "sequence_fingerprints": sequences_before,
        }
        atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")

        restic_env = os.environ.copy()
        run_checked(
            _restic_command(
                "backup",
                "--tag",
                "jobseek-web-postgresql",
                "--host",
                "jobseek-web-postgresql",
                str(run_path),
            ),
            env=restic_env,
            timeout=3_600,
        )
        run_checked(
            _restic_command(
                "forget",
                "--tag",
                "jobseek-web-postgresql",
                "--host",
                "jobseek-web-postgresql",
                "--group-by",
                "host,tags",
                "--keep-daily",
                "30",
                "--keep-weekly",
                "12",
                "--keep-monthly",
                "12",
                "--prune",
            ),
            env=restic_env,
            timeout=3_600,
        )
        run_checked(_restic_command("check"), env=restic_env, timeout=3_600)
        snapshots_output = run_checked(
            _restic_command(
                "snapshots",
                "--json",
                "--latest",
                "1",
                "--tag",
                "jobseek-web-postgresql",
                "--host",
                "jobseek-web-postgresql",
            ),
            env=restic_env,
            timeout=300,
        ).stdout
        try:
            latest_snapshot = json.loads(snapshots_output)[-1]
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise BackupError("Restic returned no parseable web PostgreSQL snapshot") from exc
        success = True
        return {
            "archive_bytes": manifest["archive_bytes"],
            "archive_sha256": dump_sha256,
            "table_count": len(WEB_POSTGRES_TABLES),
            "row_count": sum(item["rows"] for item in before.values()),
            "server_version": server_version,
            "repository_snapshot_id": latest_snapshot.get("short_id")
            or latest_snapshot.get("id", "")[:8],
            "repository_snapshot_time": latest_snapshot.get("time"),
        }
    finally:
        if success:
            shutil.rmtree(run_path, ignore_errors=True)


def verify_web_postgresql_restore(
    manifest_path: Path, dump_path: Path, bootstrap_path: Path
) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("web PostgreSQL restore manifest is not parseable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise BackupError("web PostgreSQL restore manifest has an unsupported schema")
    expected_tables = [f"{schema}.{table}" for schema, table in WEB_POSTGRES_TABLES]
    if manifest.get("tables") != expected_tables:
        raise BackupError("web PostgreSQL restore manifest table boundary does not match code")
    expected_sequences = [f"{schema}.{sequence}" for schema, sequence in WEB_POSTGRES_SEQUENCES]
    if manifest.get("sequences") != expected_sequences:
        raise BackupError("web PostgreSQL restore manifest sequence boundary does not match code")
    expected_sha256 = manifest.get("archive_sha256")
    if (
        manifest.get("archive") != dump_path.name
        or manifest.get("archive_bytes") != dump_path.stat().st_size
        or not isinstance(expected_sha256, str)
        or _sha256_file(dump_path) != expected_sha256
    ):
        raise BackupError("web PostgreSQL restored archive checksum does not match")
    expected_bootstrap_sha256 = manifest.get("bootstrap_sha256")
    if (
        manifest.get("bootstrap") != bootstrap_path.name
        or not isinstance(expected_bootstrap_sha256, str)
        or _sha256_file(bootstrap_path) != expected_bootstrap_sha256
        or bootstrap_path.read_text(encoding="utf-8") != _web_postgres_bootstrap_sql()
    ):
        raise BackupError("web PostgreSQL restored bootstrap does not match")
    env = _web_postgres_env()
    _validate_web_postgres_boundary(env=env)
    actual = _web_postgres_fingerprints(env=env)
    if actual != manifest.get("fingerprints"):
        raise BackupError("web PostgreSQL restored row fingerprints do not match")
    actual_sequences = _web_postgres_sequence_fingerprints(env=env)
    if actual_sequences != manifest.get("sequence_fingerprints"):
        raise BackupError("web PostgreSQL restored sequence fingerprints do not match")
    return {
        "table_count": len(actual),
        "row_count": sum(item["rows"] for item in actual.values()),
        "archive_sha256": expected_sha256,
    }


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
        "TYPESENSE_SNAPSHOT_CONTAINER_ROOT", "/jobseek-snapshots/staging"
    ).rstrip("/")
    container_mount_root = os.environ.get(
        "TYPESENSE_SNAPSHOT_CONTAINER_MOUNT_ROOT", "/jobseek-snapshots"
    ).rstrip("/")
    if container_root != f"{container_mount_root}/staging":
        raise BackupError("Typesense snapshot container paths do not share the exact mount root")
    host_root = Path(
        os.environ.get("TYPESENSE_SNAPSHOT_HOST_ROOT", "/mnt/jobseek-typesense-backup")
    )
    headroom = _validate_typesense_snapshot_staging(
        container,
        host_root,
        container_mount_root,
    )
    staging_root = host_root / "staging"
    attempts_root = staging_root / ".attempts"
    run_path = staging_root / run_id
    data_path = run_path / "data"
    try:
        attempts_root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise BackupError("Typesense snapshot attempts root could not be created") from exc
    attempts_stat = _directory_metadata(attempts_root, "Typesense snapshot attempts root")
    staging_stat = _directory_metadata(staging_root, "Typesense snapshot staging root")
    if (
        attempts_stat.st_dev != staging_stat.st_dev
        or attempts_stat.st_uid != staging_stat.st_uid
        or attempts_stat.st_gid != staging_stat.st_gid
        or stat.S_IMODE(attempts_stat.st_mode) != 0o700
    ):
        raise BackupError("Typesense snapshot attempts root ownership, mode, or device is unsafe")
    local_packets_before = _typesense_local_snapshot_packets(staging_root)
    if local_packets_before:
        raise BackupError(
            "Typesense preserved snapshot packet exists; resolve it before another snapshot"
        )
    try:
        run_path.mkdir(mode=0o700)
    except OSError as exc:
        raise BackupError("Typesense snapshot run path could not be created") from exc
    run_stat = _directory_metadata(run_path, "Typesense snapshot run path")
    if (
        run_stat.st_dev != staging_stat.st_dev
        or run_stat.st_uid != staging_stat.st_uid
        or run_stat.st_gid != staging_stat.st_gid
        or stat.S_IMODE(run_stat.st_mode) != 0o700
    ):
        raise BackupError("Typesense snapshot run path ownership, mode, or device is unsafe")
    materialized = False
    success = False

    try:
        inventory_after: dict[str, Any] | None = None
        last_alias_error: BackupError | None = None
        selected_host_path: Path | None = None
        for attempt in range(1, _TYPESENSE_ALIAS_STABILITY_ATTEMPTS + 1):
            attempt_name = f"{run_id}-attempt-{attempt}"
            container_path = f"{container_root}/.attempts/{attempt_name}"
            host_attempt_path = attempts_root / attempt_name
            if host_attempt_path.exists() or host_attempt_path.is_symlink():
                raise BackupError("Typesense snapshot attempt path already exists")
            try:
                inventory_before = _typesense_inventory(url, api_key)
            except BackupError as exc:
                last_alias_error = exc
            else:
                _snapshot_request(url, api_key, container_path)
                try:
                    candidate_inventory = _typesense_inventory(url, api_key)
                except BackupError as exc:
                    last_alias_error = exc
                else:
                    if candidate_inventory["aliases"] == inventory_before["aliases"]:
                        attempt_stat = _directory_metadata(
                            host_attempt_path,
                            "materialized Typesense snapshot attempt",
                        )
                        if attempt_stat.st_dev != staging_stat.st_dev:
                            raise BackupError("Typesense snapshot materialized on the wrong device")
                        inventory_after = candidate_inventory
                        selected_host_path = host_attempt_path
                        break
                    last_alias_error = BackupError(
                        "Typesense aliases changed while the snapshot was being created"
                    )
            _remove_typesense_packet(host_attempt_path, "discarded Typesense snapshot attempt")
            if attempt < _TYPESENSE_ALIAS_STABILITY_ATTEMPTS:
                time.sleep(_TYPESENSE_ALIAS_STABILITY_RETRY_SECONDS)
        if inventory_after is None or selected_host_path is None:
            detail = redact(str(last_alias_error or "alias validation failed"))
            raise BackupError(
                "Typesense alias contract did not stabilize after "
                f"{_TYPESENSE_ALIAS_STABILITY_ATTEMPTS} attempts: {detail}"
            ) from last_alias_error

        selected_host_path.rename(data_path)
        materialized = True
        usage_after_snapshot = shutil.disk_usage(host_root)
        required_after_snapshot = int(headroom["staging_minimum_free_bytes"]) + int(
            headroom["staging_growth_reserve_bytes"]
        )
        if usage_after_snapshot.free < required_after_snapshot:
            _remove_typesense_packet(run_path, "headroom-breaching Typesense snapshot packet")
            materialized = False
            raise BackupError("Typesense snapshot consumed the protected free and growth headroom")
        snapshot_bytes = _tree_size(data_path)
        if snapshot_bytes <= 0:
            raise BackupError("Typesense snapshot is empty")

        restic_env = os.environ.copy()
        run_checked(
            _restic_command(
                "backup",
                "--tag",
                "jobseek-typesense",
                "--host",
                "jobseek-typesense",
                str(run_path),
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
                "snapshots",
                "--json",
                "--tag",
                "jobseek-typesense",
                "--host",
                "jobseek-typesense",
            ),
            env=restic_env,
            timeout=300,
        ).stdout
        try:
            snapshots = json.loads(snapshots_output)
            latest_snapshot = max(snapshots, key=lambda item: item.get("time") or "")
        except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BackupError("Restic returned no parseable Typesense snapshot") from exc
        success = True
        return {
            "snapshot_bytes": snapshot_bytes,
            "repository_snapshot_id": latest_snapshot.get("short_id")
            or latest_snapshot.get("id", "")[:8],
            "repository_snapshot_time": latest_snapshot.get("time"),
            "repository_snapshot_count": len(snapshots),
            "retention": {"keep_daily": 14, "keep_weekly": 4},
            "collection_documents_observation": "live_after_snapshot",
            "snapshot_local_copies_before": 0,
            "snapshot_local_copies_after_materialization": 1,
            "snapshot_peak_local_copies": 1,
            "staging_isolated": True,
            "staging_available_bytes_after_snapshot": usage_after_snapshot.free,
            "staging_required_bytes_after_snapshot": required_after_snapshot,
            **headroom,
            **inventory_after,
        }
    finally:
        # Attempts are removed synchronously before any retry. If the Snapshot
        # API itself fails, its target is deliberately preserved because the
        # server may have materialized data after the client lost the response.
        remove_run_path = success
        if not success and not materialized:
            try:
                run_metadata = run_path.lstat()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise BackupError("Typesense snapshot run path is not inspectable") from exc
            else:
                if not stat.S_ISDIR(run_metadata.st_mode):
                    raise BackupError("Typesense snapshot run path is not a real directory")
                remove_run_path = not any(run_path.iterdir())
        if remove_run_path:
            _remove_typesense_packet(run_path, "Typesense snapshot packet")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="service", required=True)
    postgres = subparsers.add_parser("postgresql")
    postgres.add_argument("--backup-type", choices=("auto", "full", "diff", "incr"), default="auto")
    subparsers.add_parser("typesense")
    subparsers.add_parser("web-postgresql")
    verify = subparsers.add_parser("web-postgresql-verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--dump", type=Path, required=True)
    verify.add_argument("--bootstrap", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.service == "web-postgresql-verify":
            result = verify_web_postgresql_restore(args.manifest, args.dump, args.bootstrap)
            print(
                "restore verification succeeded: "
                f"tables={result['table_count']} rows={result['row_count']}"
            )
            return 0
        with exclusive_lock(args.service):
            if args.service == "postgresql":
                record = execute_with_status(
                    "postgresql", lambda: postgres_backup(args.backup_type)
                )
            elif args.service == "web-postgresql":
                record = execute_with_status("web-postgresql", web_postgresql_backup)
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
