#!/usr/bin/env python3
"""Collect bounded, read-only service metrics for a Jobseek Hetzner host.

The script runs as a hardened root systemd oneshot because Docker's API is a
privileged boundary. It performs only inspect/log/readiness operations, writes
one atomic Prometheus textfile for the unprivileged Alloy process, and emits a
redacted subset of new container error lines to journald.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc  # noqa: UP017 - crawler host system Python is 3.10.
DEFAULT_TEXTFILE = Path("/var/lib/jobseek-observability/textfile/jobseek-host.prom")
DEFAULT_STATE_DIR = Path("/var/lib/jobseek-observability/state")
DEFAULT_BACKUP_STATUS_DIR = Path("/var/lib/jobseek-backup/status")
MAX_LOG_LINES = 200

ROLE_CONTAINERS = {
    "crawler": (
        "deploy-worker-1-1",
        "deploy-worker-2-1",
        "deploy-worker-3-1",
        "deploy-browser-1-1",
        "deploy-exporter-1",
        "deploy-drain-1",
        "deploy-redis-1",
    ),
    "postgresql": ("postgres",),
    "typesense": ("typesense",),
}

ROLE_UNITS = {
    "crawler": (
        "docker.service",
        "jobseek-crawler-reconciliation.timer",
        "jobseek-codex-governor.timer",
        "jobseek-codex-daily-annotations.timer",
        "jobseek-codex-daily-error-review.timer",
    ),
    "postgresql": (
        "docker.service",
        "jobseek-postgresql-backup-repository.service",
        "jobseek-postgresql-backup.timer",
    ),
    "typesense": (
        "docker.service",
        "cloudflared.service",
        "jobseek-typesense-backup.timer",
    ),
}

ROLE_BACKUPS = {
    "crawler": (),
    "postgresql": ("postgresql",),
    "typesense": ("typesense",),
}

_CREDENTIAL_RE = re.compile(
    r"(?i)\b(authorization|token|secret|password|api[_-]?key)\b\s*[:=]\s*\S+"
)
_URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?\S+")
_IP_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_ERROR_RE = re.compile(r"(?i)\b(error|fatal|panic|exception|oom|killed|failed)\b")


class ProbeError(RuntimeError):
    """A required read-only probe failed."""


def _run(argv: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"{argv[0]} unavailable or timed out: {type(exc).__name__}") from exc
    if result.returncode:
        detail = _redact((result.stderr or result.stdout or "command failed").strip())
        raise ProbeError(f"{argv[0]} exited {result.returncode}: {detail[-300:]}")
    return result


def _redact(value: str) -> str:
    text = _CREDENTIAL_RE.sub(r"\1=<redacted>", value)
    text = _URL_QUERY_RE.sub(r"\1?<redacted>", text)
    text = _IP_RE.sub("<redacted-ip>", text)
    text = _UUID_RE.sub("<redacted-uuid>", text)
    text = _EMAIL_RE.sub("<redacted-email>", text)
    return text


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _metric(name: str, value: int | float, **labels: str) -> str:
    rendered = ""
    if labels:
        pairs = ",".join(f'{key}="{_escape_label(val)}"' for key, val in sorted(labels.items()))
        rendered = "{" + pairs + "}"
    return f"{name}{rendered} {value}"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o644)
    os.replace(temporary, path)


def _docker_state(container: str) -> dict[str, Any]:
    result = _run(["docker", "inspect", container], timeout=30)
    try:
        inspected = json.loads(result.stdout)[0]
    except (IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"unparseable docker inspect output for {container}") from exc
    state = inspected.get("State") or {}
    return {
        "running": bool(state.get("Running")),
        "oom_killed": bool(state.get("OOMKilled")),
        "restart_count": int(inspected.get("RestartCount") or 0),
    }


def _collect_container_metrics(role: str, lines: list[str]) -> None:
    for container in ROLE_CONTAINERS[role]:
        state = _docker_state(container)
        labels = {"container": container, "host_role": role}
        lines.append(_metric("jobseek_container_running", int(state["running"]), **labels))
        lines.append(_metric("jobseek_container_oom_killed", int(state["oom_killed"]), **labels))
        lines.append(_metric("jobseek_container_restart_count", state["restart_count"], **labels))


def _collect_unit_metrics(role: str, lines: list[str]) -> None:
    for unit in ROLE_UNITS[role]:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        lines.append(
            _metric(
                "jobseek_host_unit_active",
                int(result.returncode == 0),
                host_role=role,
                unit=unit,
            )
        )


def _backup_number(record: dict[str, Any], key: str) -> float:
    value = record.get(key, 0)
    if isinstance(value, bool):
        return float(int(value))
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _collect_backup_metrics(role: str, status_dir: Path, lines: list[str]) -> None:
    for service in ROLE_BACKUPS[role]:
        path = status_dir / f"{service}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProbeError(f"missing or invalid {service} backup status") from exc
        if not isinstance(record, dict):
            raise ProbeError(f"invalid {service} backup status object")
        labels = {"host_role": role, "service": service}
        lines.extend(
            (
                _metric(
                    "jobseek_backup_last_attempt_unixtime",
                    _backup_number(record, "attempt_unix"),
                    **labels,
                ),
                _metric(
                    "jobseek_backup_last_success_unixtime",
                    _backup_number(record, "last_success_unix"),
                    **labels,
                ),
                _metric(
                    "jobseek_backup_last_attempt_success",
                    int(bool(record.get("success"))),
                    **labels,
                ),
                _metric(
                    "jobseek_backup_last_duration_seconds",
                    _backup_number(record, "duration_seconds"),
                    **labels,
                ),
            )
        )


POSTGRES_STATS_SQL = """
SELECT
  (SELECT COALESCE(sum(numbackends), 0) FROM pg_stat_database),
  current_setting('max_connections'),
  (SELECT archived_count FROM pg_stat_archiver),
  (SELECT failed_count FROM pg_stat_archiver),
  (SELECT checkpoints_timed FROM pg_stat_bgwriter),
  (SELECT checkpoints_req FROM pg_stat_bgwriter),
  (SELECT checkpoint_write_time FROM pg_stat_bgwriter),
  (SELECT checkpoint_sync_time FROM pg_stat_bgwriter),
  (SELECT buffers_checkpoint FROM pg_stat_bgwriter),
  (SELECT COALESCE(extract(epoch FROM stats_reset), 0) FROM pg_stat_bgwriter),
  (SELECT COALESCE(sum(pg_database_size(datname)), 0) FROM pg_database WHERE datallowconn);
""".strip()

RECONCILIATION_STATS_SQL = """
SELECT
  target,
  COALESCE(extract(epoch FROM last_attempt_at), 0),
  COALESCE(extract(epoch FROM last_success_at), 0),
  COALESCE(extract(epoch FROM cycle_started_at), 0),
  last_duration_seconds,
  last_local_rows,
  last_remote_rows,
  last_missing_remote + last_state_mismatch + last_remote_only_active
    + CASE WHEN target = 'typesense' THEN last_remote_only_inactive ELSE 0 END,
  last_repaired,
  last_unresolved,
  last_outcome,
  next_partition,
  partition_count,
  bootstrap_complete::int
FROM cross_store_reconciliation_state
ORDER BY target;
""".strip()


def _postgresql_query(container: str, sql: str, *, timeout: int = 60) -> str:
    result = _run(
        [
            "docker",
            "exec",
            "--user",
            "postgres",
            container,
            "sh",
            "-c",
            'db="${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"; '
            'exec psql -U "${POSTGRES_USER:-postgres}" -d "$db" '
            "-XAt -F '\t' -v ON_ERROR_STOP=1 -c \"$1\"",
            "jobseek-observability",
            sql,
        ],
        timeout=timeout,
    )
    return result.stdout.strip()


def _collect_postgresql_shared_memory_metrics(lines: list[str], container: str) -> None:
    configured_result = _run(
        ["docker", "inspect", "--format", "{{.HostConfig.ShmSize}}", container],
        timeout=30,
    )
    usage_result = _run(["docker", "exec", container, "df", "-B1", "/dev/shm"], timeout=30)
    try:
        configured = int(configured_result.stdout.strip())
        fields = usage_result.stdout.splitlines()[-1].split()
        capacity, used, available = (int(value) for value in fields[1:4])
    except (IndexError, TypeError, ValueError) as exc:
        raise ProbeError("PostgreSQL shared-memory probe returned an unexpected shape") from exc
    if configured <= 0 or capacity <= 0 or used < 0 or available < 0:
        raise ProbeError("PostgreSQL shared-memory probe returned invalid capacity")
    lines.extend(
        (
            _metric("jobseek_postgresql_shared_memory_configured_bytes", configured),
            _metric("jobseek_postgresql_shared_memory_capacity_bytes", capacity),
            _metric("jobseek_postgresql_shared_memory_used_bytes", used),
            _metric("jobseek_postgresql_shared_memory_available_bytes", available),
        )
    )


def _collect_postgresql_metrics(lines: list[str], container: str = "postgres") -> None:
    _collect_postgresql_shared_memory_metrics(lines, container)
    ready = subprocess.run(
        [
            "docker",
            "exec",
            "--user",
            "postgres",
            container,
            "sh",
            "-c",
            'db="${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"; '
            'exec pg_isready -q -U "${POSTGRES_USER:-postgres}" -d "$db"',
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    lines.append(_metric("jobseek_postgresql_ready", int(ready.returncode == 0)))
    if ready.returncode:
        raise ProbeError("PostgreSQL readiness probe failed")
    query_started = time.monotonic()
    fields = _postgresql_query(container, POSTGRES_STATS_SQL).split("\t")
    query_duration = time.monotonic() - query_started
    lines.append(_metric("jobseek_postgresql_stats_query_duration_seconds", query_duration))
    if len(fields) != 11:
        raise ProbeError("PostgreSQL statistics query returned an unexpected shape")
    metrics = (
        ("jobseek_postgresql_connections", 1.0),
        ("jobseek_postgresql_max_connections", 1.0),
        ("jobseek_postgresql_archived_total", 1.0),
        ("jobseek_postgresql_archive_failed_total", 1.0),
        ("jobseek_postgresql_checkpoints_timed_total", 1.0),
        ("jobseek_postgresql_checkpoints_requested_total", 1.0),
        # PostgreSQL exposes the two checkpoint durations in milliseconds.
        ("jobseek_postgresql_checkpoint_write_seconds_total", 0.001),
        ("jobseek_postgresql_checkpoint_sync_seconds_total", 0.001),
        ("jobseek_postgresql_checkpoint_buffers_total", 1.0),
        ("jobseek_postgresql_stats_reset_unixtime", 1.0),
        ("jobseek_postgresql_database_bytes", 1.0),
    )
    try:
        lines.extend(
            _metric(name, float(value) * scale)
            for (name, scale), value in zip(metrics, fields, strict=True)
        )
    except ValueError as exc:
        raise ProbeError("PostgreSQL statistics query returned a non-numeric value") from exc

    relation = _postgresql_query(
        container,
        "SELECT COALESCE(to_regclass('cross_store_reconciliation_state')::text, '')",
    )
    schema_ready = int(relation == "cross_store_reconciliation_state")
    lines.append(_metric("jobseek_cross_store_reconciliation_schema_ready", schema_ready))
    if not schema_ready:
        return

    state_rows = _postgresql_query(container, RECONCILIATION_STATS_SQL)
    for raw in state_rows.splitlines():
        fields = raw.split("\t")
        if len(fields) != 14:
            raise ProbeError("reconciliation state query returned an unexpected shape")
        target = fields[0]
        outcome = fields[10]
        numbers = (*fields[1:10], *fields[11:14])
        try:
            (
                last_attempt,
                last_success,
                cycle_started,
                duration,
                local_rows,
                remote_rows,
                detected,
                repaired,
                unresolved,
                next_partition,
                partition_count,
                bootstrap_complete,
            ) = (float(value) for value in numbers)
        except ValueError as exc:
            raise ProbeError("reconciliation state query returned a non-numeric value") from exc
        labels = {"target": target}
        lines.extend(
            (
                _metric(
                    "jobseek_cross_store_reconciliation_last_attempt_unixtime",
                    last_attempt,
                    **labels,
                ),
                _metric(
                    "jobseek_cross_store_reconciliation_last_success_unixtime",
                    last_success,
                    **labels,
                ),
                _metric(
                    "jobseek_cross_store_reconciliation_cycle_started_unixtime",
                    cycle_started,
                    **labels,
                ),
                _metric(
                    "jobseek_cross_store_reconciliation_last_duration_seconds",
                    duration,
                    **labels,
                ),
                _metric("jobseek_cross_store_reconciliation_last_local_rows", local_rows, **labels),
                _metric(
                    "jobseek_cross_store_reconciliation_last_remote_rows",
                    remote_rows,
                    **labels,
                ),
                _metric("jobseek_cross_store_reconciliation_last_detected", detected, **labels),
                _metric("jobseek_cross_store_reconciliation_last_repaired", repaired, **labels),
                _metric(
                    "jobseek_cross_store_reconciliation_last_unresolved",
                    unresolved,
                    **labels,
                ),
                _metric(
                    "jobseek_cross_store_reconciliation_last_attempt_success",
                    int(outcome != "failed"),
                    **labels,
                ),
                _metric(
                    "jobseek_cross_store_reconciliation_progress_partition",
                    next_partition,
                    **labels,
                ),
                _metric(
                    "jobseek_cross_store_reconciliation_partition_count",
                    partition_count,
                    **labels,
                ),
                _metric(
                    "jobseek_cross_store_reconciliation_bootstrap_complete",
                    bootstrap_complete,
                    **labels,
                ),
                _metric(
                    "jobseek_cross_store_reconciliation_outcome_info",
                    1,
                    target=target,
                    outcome=outcome,
                ),
            )
        )

    stuck = _postgresql_query(
        container,
        "SELECT count(*) FROM cross_store_reconciliation_run "
        "WHERE status = 'running' AND started_at < clock_timestamp() - interval '2 hours'",
    )
    try:
        lines.append(_metric("jobseek_cross_store_reconciliation_stuck_runs", float(stuck)))
    except ValueError as exc:
        raise ProbeError("reconciliation run query returned a non-numeric value") from exc


def _collect_typesense_metrics(lines: list[str]) -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8108/health", timeout=10) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
            healthy = response.status == 200 and payload.get("ok") is True
    except (OSError, ValueError, urllib.error.URLError) as exc:
        lines.append(_metric("jobseek_typesense_healthy", 0))
        raise ProbeError(f"Typesense health probe failed: {type(exc).__name__}") from exc
    lines.append(_metric("jobseek_typesense_healthy", int(healthy)))
    if not healthy:
        raise ProbeError("Typesense health endpoint did not report ok")


def _load_cursor(path: Path, *, now: float) -> dict[str, float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in raw.items():
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if now - 86_400 <= parsed <= now:
            result[str(key)] = parsed
    return result


def _write_cursor(path: Path, cursor: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(cursor, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _collect_new_error_logs(role: str, state_dir: Path, *, now: float) -> None:
    if role == "crawler":
        return  # The crawler Alloy already tails these Docker logs directly.
    cursor_path = state_dir / "container-log-cursor.json"
    cursor = _load_cursor(cursor_path, now=now)
    updated = dict(cursor)
    until = datetime.fromtimestamp(now, tz=UTC).isoformat()
    for container in ROLE_CONTAINERS[role]:
        since_epoch = cursor.get(container, now - 300)
        since = datetime.fromtimestamp(since_epoch, tz=UTC).isoformat()
        result = _run(
            ["docker", "logs", "--since", since, "--until", until, container],
            timeout=45,
        )
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        matches = [line for line in output.splitlines() if _ERROR_RE.search(line)]
        for line in matches[-MAX_LOG_LINES:]:
            if "STATEMENT:" in line:
                continue
            print(
                "jobseek_container_error "
                f"host_role={role} container={container} message={_redact(line)[:2000]}"
            )
        updated[container] = now
    _write_cursor(cursor_path, updated)


def collect(
    role: str,
    *,
    textfile: Path = DEFAULT_TEXTFILE,
    state_dir: Path = DEFAULT_STATE_DIR,
    backup_status_dir: Path = DEFAULT_BACKUP_STATUS_DIR,
) -> bool:
    now = time.time()
    lines = [
        "# Jobseek fleet metrics; generated atomically by jobseek-host-observability.",
        _metric("jobseek_host_reboot_required", int(Path("/var/run/reboot-required").exists())),
    ]
    probes: list[tuple[str, Any]] = [
        ("containers", lambda: _collect_container_metrics(role, lines)),
        ("systemd", lambda: _collect_unit_metrics(role, lines)),
        ("backup", lambda: _collect_backup_metrics(role, backup_status_dir, lines)),
    ]
    if role == "postgresql":
        probes.append(("postgresql", lambda: _collect_postgresql_metrics(lines)))
    elif role == "typesense":
        probes.append(("typesense", lambda: _collect_typesense_metrics(lines)))
    probes.append(("container_logs", lambda: _collect_new_error_logs(role, state_dir, now=now)))

    success = True
    for name, probe in probes:
        try:
            probe()
        except Exception as exc:
            success = False
            print(f"jobseek_host_probe_failed probe={name} error={_redact(str(exc))}")
            lines.append(
                _metric(
                    "jobseek_host_observability_probe_success",
                    0,
                    host_role=role,
                    probe=name,
                )
            )
        else:
            lines.append(
                _metric(
                    "jobseek_host_observability_probe_success",
                    1,
                    host_role=role,
                    probe=name,
                )
            )

    lines.extend(
        (
            _metric(
                "jobseek_host_observability_collect_success",
                int(success),
                host_role=role,
            ),
            _metric(
                "jobseek_host_observability_last_collect_unixtime",
                int(now),
                host_role=role,
            ),
        )
    )
    _atomic_write(textfile, "\n".join(lines) + "\n")
    return success


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        choices=sorted(ROLE_CONTAINERS),
        default=os.environ.get("JOBSEEK_HOST_ROLE"),
        required=os.environ.get("JOBSEEK_HOST_ROLE") is None,
    )
    parser.add_argument("--textfile", type=Path, default=DEFAULT_TEXTFILE)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--backup-status-dir", type=Path, default=DEFAULT_BACKUP_STATUS_DIR)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    return (
        0
        if collect(
            args.role,
            textfile=args.textfile,
            state_dir=args.state_dir,
            backup_status_dir=args.backup_status_dir,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
