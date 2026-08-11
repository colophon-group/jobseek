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
DEFAULT_RECONCILIATION_REVISION = Path("/var/lib/jobseek-reconciliation/deployed-sha")
DEFAULT_ATS_INVENTORY_STATUS = Path("/var/lib/jobseek-ats-inventory/status/current.json")
DEFAULT_CODEX_ERROR_REVIEW_STATUS = Path("/srv/jobseek-codex/state/error-review-status.json")
REDIS_CAPACITY_CACHE_MAX_AGE_SECONDS = 6 * 60 * 60
POSTGRES_EMERGENCY_RESERVE_NAME = ".jobseek-postgresql-emergency-reserve"
POSTGRES_EMERGENCY_RESERVE_BYTES = 2_147_483_648
WEB_POSTGRES_HELPER_IMAGE = (
    "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
)
WEB_POSTGRES_HELPER_IMAGE_LEASE = "jobseek-web-postgresql-backup-image-lease"
WEB_POSTGRES_HELPER_IMAGE_LEASE_LABEL = "jobseek.backup.helper-image"
WEB_POSTGRES_HELPER_IMAGE_LEASE_TMPFS = {
    "/var/lib/postgresql/data": "rw,noexec,nosuid,nodev,size=65536"
}
MAX_LOG_LINES = 200
ALLOY_METRICS = {
    "alloy_resources_process_resident_memory_bytes": ("resident_memory_bytes", "max"),
    "prometheus_remote_storage_queue_highest_sent_timestamp_seconds": (
        "remote_write_highest_sent_timestamp_seconds",
        "max",
    ),
    "prometheus_remote_storage_samples_pending": ("remote_write_samples_pending", "sum"),
    "prometheus_remote_storage_samples_retried_total": (
        "remote_write_samples_retried_total",
        "sum",
    ),
    "prometheus_remote_storage_samples_failed_total": (
        "remote_write_samples_failed_total",
        "sum",
    ),
    "prometheus_remote_storage_samples_dropped_total": (
        "remote_write_samples_dropped_total",
        "sum",
    ),
    "prometheus_remote_storage_enqueue_retries_total": (
        "remote_write_enqueue_retries_total",
        "sum",
    ),
    "loki_write_dropped_entries_total": ("loki_dropped_entries_total", "sum"),
}
_PROMETHEUS_SAMPLE_RE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{[^}]*\})?\s+(?P<value>\S+)"
)
_HTTP_429_RE = re.compile(r"(?i)(?:status|code|http(?: status)?)\D{0,8}429|429 too many")

ROLE_CONTAINERS = {
    "crawler": (
        "deploy-worker-1-1",
        "deploy-worker-2-1",
        "deploy-worker-3-1",
        "deploy-browser-1-1",
        "deploy-exporter-1",
        "deploy-drain-1",
        "deploy-redis-1",
        "deploy-alloy-1",
    ),
    "postgresql": ("postgres",),
    "typesense": ("typesense",),
}

ROLE_UNITS = {
    "crawler": (
        "docker.service",
        "jobseek-crawler-reconciliation.timer",
        "jobseek-ats-inventory.timer",
        "jobseek-codex-governor.timer",
        "jobseek-codex-daily-annotations.timer",
        "jobseek-codex-daily-error-review.timer",
    ),
    "postgresql": (
        "docker.service",
        "jobseek-postgresql-backup-repository.service",
        "jobseek-postgresql-backup.timer",
        "jobseek-postgresql-emergency-headroom.service",
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

OPTIONAL_ROLE_UNITS = {
    "crawler": (),
    "postgresql": (),
    "typesense": ("jobseek-web-postgresql-backup.timer",),
}

OPTIONAL_ROLE_BACKUPS = {
    "crawler": (),
    "postgresql": (),
    "typesense": (("web-postgresql", "jobseek-web-postgresql-backup.timer"),),
}

_CREDENTIAL_RE = re.compile(
    r"(?i)\b(authorization|token|secret|password|api[_-]?key)\b\s*[:=]\s*\S+"
)
_URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?\S+")
_IP_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_ERROR_RE = re.compile(r"(?i)\b(error|fatal|panic|exception|oom|killed|failed)\b")
_TYPESENSE_THREADPOOL_RE = re.compile(
    r"Threadpool exhaustion detected, task_queue_len: (\d+), thread_pool_len: (\d+)"
)
_TYPESENSE_SLOW_REQUEST_RE = re.compile(r"event=slow_request, time=(\d+) ms")
_TYPESENSE_LOG_EVENT_PATTERNS = {
    "descriptor_exhaustion": re.compile(r"Too many open files", re.IGNORECASE),
    "leaderless": re.compile(r"Node with no leader|state ERROR, can't reset_peer", re.IGNORECASE),
    "snapshot_failure": re.compile(
        r"Timed snapshot failed|SnapshotWriter|SnapshotError", re.IGNORECASE
    ),
    "slow_request": _TYPESENSE_SLOW_REQUEST_RE,
    "threadpool_exhaustion": _TYPESENSE_THREADPOOL_RE,
}


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


def _web_postgres_helper_image_state() -> tuple[int, int]:
    """Return exact-image availability and GC-protection without failing the sampler."""
    try:
        image = subprocess.run(
            ["docker", "image", "inspect", WEB_POSTGRES_HELPER_IMAGE],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if image.returncode:
            return 0, 0
        lease = subprocess.run(
            ["docker", "container", "inspect", WEB_POSTGRES_HELPER_IMAGE_LEASE],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0, 0
    if lease.returncode:
        return 1, 0
    try:
        payload = json.loads(lease.stdout)
        container = payload[0]
        config = container["Config"]
        state = container["State"]
        host_config = container["HostConfig"]
        labels = config.get("Labels") or {}
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return 1, 0
    protected = int(
        config.get("Image") == WEB_POSTGRES_HELPER_IMAGE
        and state.get("Running") is False
        and labels.get(WEB_POSTGRES_HELPER_IMAGE_LEASE_LABEL) == "web-postgresql"
        and config.get("Entrypoint") == ["/bin/true"]
        and host_config.get("NetworkMode") == "none"
        and host_config.get("ReadonlyRootfs") is True
        and host_config.get("CapDrop") == ["ALL"]
        and host_config.get("SecurityOpt") == ["no-new-privileges:true"]
        and host_config.get("Tmpfs") == WEB_POSTGRES_HELPER_IMAGE_LEASE_TMPFS
        and container.get("Mounts") == []
    )
    return 1, protected


def _collect_container_metrics(role: str, lines: list[str]) -> None:
    for container in ROLE_CONTAINERS[role]:
        state = _docker_state(container)
        labels = {"container": container, "host_role": role}
        lines.append(_metric("jobseek_container_running", int(state["running"]), **labels))
        lines.append(_metric("jobseek_container_oom_killed", int(state["oom_killed"]), **labels))
        lines.append(_metric("jobseek_container_restart_count", state["restart_count"], **labels))


def _collect_redis_capacity_metrics(
    lines: list[str],
    state_dir: Path,
    *,
    now: float | None = None,
) -> None:
    """Refresh the expensive key-family SCAN at most once every six hours.

    The crawler and host-observability deployments can finish in either order.
    Missing CLI support therefore publishes ``available=0`` without failing the
    whole host sampler; the stale-snapshot alert provides a bounded rollout
    grace period. A prior valid snapshot remains published during refresh
    failures so operators retain the last family attribution.
    """
    current = time.time() if now is None else now
    cache = state_dir / "redis-capacity.prom"
    cached = ""
    cache_age = float("inf")
    try:
        cached = cache.read_text(encoding="utf-8")
        cache_age = max(0.0, current - cache.stat().st_mtime)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"jobseek_redis_capacity_cache_failed error={type(exc).__name__}")

    available = False
    if cached and cache_age <= REDIS_CAPACITY_CACHE_MAX_AGE_SECONDS:
        available = True
    else:
        try:
            result = _run(
                [
                    "docker",
                    "exec",
                    "deploy-worker-1-1",
                    "/app/.venv/bin/crawler",
                    "redis-capacity",
                    "inspect",
                    "--format",
                    "prometheus",
                ],
                timeout=100,
            )
            rendered = "\n".join(
                line
                for line in result.stdout.splitlines()
                if line.startswith("jobseek_redis_")
            )
            if "jobseek_redis_capacity_snapshot_unixtime " not in rendered:
                raise ProbeError("Redis capacity output omitted its snapshot timestamp")
            cached = rendered + "\n"
            _atomic_write(cache, cached)
            available = True
        except Exception as exc:
            print(f"jobseek_redis_capacity_refresh_failed error={_redact(str(exc))}")

    if cached:
        lines.extend(line for line in cached.splitlines() if line.startswith("jobseek_redis_"))
    lines.append(_metric("jobseek_redis_capacity_snapshot_available", int(available)))


def _unit_enabled(unit: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-enabled", "--quiet", unit],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _collect_unit_metrics(role: str, lines: list[str]) -> None:
    units = (*ROLE_UNITS[role], *filter(_unit_enabled, OPTIONAL_ROLE_UNITS[role]))
    for unit in units:
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


def _collect_reconciliation_deployment_metrics(
    lines: list[str], revision_path: Path = DEFAULT_RECONCILIATION_REVISION
) -> None:
    available = 0
    modified = 0.0
    try:
        revision = revision_path.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("invalid revision")
        modified = revision_path.stat().st_mtime
        available = 1
        lines.append(
            _metric(
                "jobseek_cross_store_reconciliation_deployed_revision_info",
                1,
                revision=revision,
            )
        )
    except (OSError, UnicodeError, ValueError):
        pass
    lines.extend(
        (
            _metric("jobseek_cross_store_reconciliation_deployed_revision_available", available),
            _metric("jobseek_cross_store_reconciliation_deployed_revision_mtime_seconds", modified),
        )
    )


def _collect_ats_inventory_metrics(
    lines: list[str], status_path: Path = DEFAULT_ATS_INVENTORY_STATUS
) -> None:
    """Publish only bounded aggregate fields from the operator status report."""
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        lines.append(_metric("jobseek_ats_inventory_status_available", 0))
        return
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError("ATS inventory status is unreadable") from exc
    if not isinstance(payload, dict):
        raise ProbeError("ATS inventory status is not an object")

    def integer(name: str, *, minimum: int = 0) -> int:
        value = payload.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ProbeError(f"ATS inventory status has invalid {name}")
        return value

    requested_mode = payload.get("requested_mode")
    effective_mode = payload.get("effective_mode")
    if requested_mode not in {"report", "dry-run", "refill"} or effective_mode not in {
        "report",
        "dry-run",
        "refill",
    }:
        raise ProbeError("ATS inventory status has invalid mode")
    attempt_success = integer("last_attempt_success")
    if attempt_success not in {0, 1}:
        raise ProbeError("ATS inventory status has invalid last_attempt_success")
    attempt_degraded = integer("last_attempt_degraded")
    if attempt_degraded not in {0, 1}:
        raise ProbeError("ATS inventory status has invalid last_attempt_degraded")
    rollout_cap = integer("rollout_cap", minimum=1)
    if rollout_cap not in {1, 5, 25}:
        raise ProbeError("ATS inventory status has invalid rollout_cap")
    lines.extend(
        (
            _metric("jobseek_ats_inventory_status_available", 1),
            _metric(
                "jobseek_ats_inventory_last_attempt_unixtime", integer("last_attempt_unixtime")
            ),
            _metric(
                "jobseek_ats_inventory_last_success_unixtime", integer("last_success_unixtime")
            ),
            _metric("jobseek_ats_inventory_last_attempt_success", attempt_success),
            _metric("jobseek_ats_inventory_last_attempt_degraded", attempt_degraded),
            _metric(
                "jobseek_ats_inventory_last_attempt_duration_seconds",
                integer("last_attempt_duration_seconds"),
            ),
            _metric("jobseek_ats_inventory_rollout_cap", rollout_cap),
            _metric(
                "jobseek_ats_inventory_mode_info",
                1,
                effective_mode=effective_mode,
                requested_mode=requested_mode,
            ),
        )
    )
    report = payload.get("report") or payload.get("last_success_report")
    if not isinstance(report, dict):
        return
    coverage = report.get("coverage")
    impact = report.get("impact")
    candidate = report.get("candidate_issues")
    if not all(isinstance(value, dict) for value in (coverage, impact, candidate)):
        raise ProbeError("ATS inventory aggregate report is incomplete")

    def report_number(mapping: dict[str, Any], name: str) -> int | float:
        value = mapping.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ProbeError(f"ATS inventory report has invalid {name}")
        return value

    def optional_report_number(mapping: dict[str, Any], name: str) -> int | float:
        return 0 if mapping.get(name) is None else report_number(mapping, name)

    queue_status = candidate.get("status")
    if not isinstance(queue_status, str) or re.fullmatch(r"[a-z0-9_]{1,64}", queue_status) is None:
        raise ProbeError("ATS inventory queue status is invalid")
    unsupported_families = coverage.get("unsupported_families")
    if not isinstance(unsupported_families, list) or not all(
        isinstance(family, str) for family in unsupported_families
    ):
        raise ProbeError("ATS inventory unsupported families are invalid")
    lines.extend(
        (
            _metric("jobseek_ats_inventory_rows", report_number(report, "rows")),
            _metric(
                "jobseek_ats_inventory_candidate_coverage_percent",
                report_number(coverage, "candidate_coverage_pct"),
            ),
            _metric(
                "jobseek_ats_inventory_unsupported_families",
                len(unsupported_families),
            ),
            _metric(
                "jobseek_ats_inventory_active_companies",
                report_number(impact, "active_companies"),
            ),
            _metric(
                "jobseek_ats_inventory_queue_status_info",
                1,
                status=queue_status,
            ),
        )
    )
    queue = candidate.get("queue_before")
    if queue is None and queue_status == "rate_limited_preflight":
        return
    if not isinstance(queue, dict):
        raise ProbeError("ATS inventory queue report is unavailable")
    lines.extend(
        (
            _metric("jobseek_ats_inventory_queue_available", report_number(queue, "available")),
            _metric("jobseek_ats_inventory_queue_total_open", report_number(queue, "total_open")),
            _metric("jobseek_ats_inventory_import_open", report_number(queue, "import_open")),
            _metric("jobseek_ats_inventory_import_closed", report_number(queue, "import_closed")),
            _metric(
                "jobseek_ats_inventory_import_fresh_claimed",
                report_number(queue, "import_fresh_claimed"),
            ),
            _metric(
                "jobseek_ats_inventory_import_active_linked_pr",
                report_number(queue, "import_active_linked_pr"),
            ),
            _metric(
                "jobseek_ats_inventory_pickup_latency_avg_seconds",
                optional_report_number(queue, "import_pickup_latency_avg_seconds"),
            ),
            _metric("jobseek_ats_inventory_created_last_run", report_number(candidate, "created")),
        )
    )


def _collect_codex_error_review_metrics(
    lines: list[str], status_path: Path = DEFAULT_CODEX_ERROR_REVIEW_STATUS
) -> None:
    try:
        record = json.loads(status_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        record = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError("invalid Codex daily error-review status") from exc
    if not isinstance(record, dict):
        raise ProbeError("invalid Codex daily error-review status object")

    values: dict[str, int] = {}
    for key in (
        "last_attempt_unixtime",
        "last_success_unixtime",
        "last_attempt_success",
        "run_in_progress",
    ):
        raw = record.get(key, 0)
        if isinstance(raw, bool):
            raw = int(raw)
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ProbeError(f"invalid Codex daily error-review {key}") from exc
        is_boolean_metric = key in {"last_attempt_success", "run_in_progress"}
        if value < 0 or (is_boolean_metric and value not in (0, 1)):
            raise ProbeError(f"invalid Codex daily error-review {key}")
        values[key] = value

    lines.extend(
        (
            _metric(
                "jobseek_codex_daily_error_review_last_attempt_unixtime",
                values["last_attempt_unixtime"],
            ),
            _metric(
                "jobseek_codex_daily_error_review_last_success_unixtime",
                values["last_success_unixtime"],
            ),
            _metric(
                "jobseek_codex_daily_error_review_last_attempt_success",
                values["last_attempt_success"],
            ),
            _metric(
                "jobseek_codex_daily_error_review_run_in_progress",
                values["run_in_progress"],
            ),
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
    optional_services = (
        service for service, timer in OPTIONAL_ROLE_BACKUPS[role] if _unit_enabled(timer)
    )
    for service in (*ROLE_BACKUPS[role], *optional_services):
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
        if service == "web-postgresql":
            image_available, gc_protected = _web_postgres_helper_image_state()
            lines.extend(
                (
                    _metric(
                        "jobseek_backup_helper_image_available",
                        image_available,
                        **labels,
                    ),
                    _metric(
                        "jobseek_backup_helper_image_gc_protected",
                        gc_protected,
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

POSTGRES_CONNECTION_OWNERS_SQL = """
WITH owned AS (
  SELECT
    CASE application_name
      WHEN 'jobseek:crawler:worker-1:local' THEN 'worker-1'
      WHEN 'jobseek:crawler:worker-2:local' THEN 'worker-2'
      WHEN 'jobseek:crawler:worker-3:local' THEN 'worker-3'
      WHEN 'jobseek:crawler:browser-1:local' THEN 'browser-1'
      WHEN 'jobseek:crawler:exporter:local' THEN 'exporter'
      WHEN 'jobseek:crawler:drain:local' THEN 'drain'
      WHEN 'jobseek:crawler:reconciliation:local' THEN 'reconciliation'
      WHEN 'jobseek:crawler:deploy-sync:local' THEN 'deploy-sync'
      WHEN 'jobseek:crawler:deploy-migrate:local' THEN 'deploy-migrate'
      WHEN 'jobseek:crawler:maintenance:local' THEN 'maintenance'
      WHEN 'jobseek:crawler:csv-sync:local' THEN 'csv-sync'
      WHEN 'jobseek:crawler:currency-refresh:local' THEN 'currency-refresh'
      WHEN 'jobseek:crawler:location-taxonomy-repair:local'
        THEN 'location-taxonomy-repair'
      WHEN 'jobseek:crawler:labeller:local' THEN 'labeller'
      WHEN 'jobseek:crawler:oneoff:local' THEN 'oneoff'
      WHEN 'jobseek:murmur:node' THEN 'murmur-node'
      WHEN 'jobseek:murmur:python' THEN 'murmur-python'
      WHEN 'jobseek:ingress:private-path-verifier' THEN 'ingress-verifier'
      WHEN 'jobseek:host-observability' THEN 'host-observability'
      WHEN 'psql' THEN 'operator-psql'
      ELSE CASE
        WHEN application_name LIKE 'pgBackRest%' THEN 'backup'
        WHEN application_name LIKE 'jobseek:operator:%' THEN 'operator-tool'
        ELSE 'other'
      END
    END AS owner,
    CASE state
      WHEN 'active' THEN 'active'
      WHEN 'idle' THEN 'idle'
      WHEN 'idle in transaction' THEN 'idle_in_transaction'
      WHEN 'idle in transaction (aborted)' THEN 'idle_in_transaction_aborted'
      WHEN 'disabled' THEN 'disabled'
      ELSE 'other'
    END AS connection_state
  FROM pg_stat_activity
  WHERE backend_type = 'client backend'
)
SELECT owner, connection_state, count(*)
FROM owned
GROUP BY owner, connection_state
ORDER BY owner, connection_state;
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
  last_detected,
  last_payload_mismatch,
  last_repaired,
  last_unresolved,
  last_outcome,
  next_partition,
  partition_count,
  bootstrap_complete::int
FROM cross_store_reconciliation_state
WHERE target = 'typesense'
ORDER BY target;
""".strip()

RECONCILIATION_SCHEMA_SQL = """
SELECT count(*)
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'cross_store_reconciliation_state'
  AND column_name IN (
    'last_detected',
    'last_payload_mismatch'
  );
""".strip()

BOARD_QUARANTINE_SCHEMA_SQL = """
SELECT count(*)
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'job_board'
  AND column_name IN (
    'quarantined_at',
    'last_quarantined_at',
    'last_quarantine_error',
    'quarantine_probe_count',
    'last_recovered_at',
    'recovery_count'
  );
""".strip()

BOARD_QUARANTINE_STATS_SQL = """
SELECT
  count(*) FILTER (WHERE jb.board_status = 'quarantined'),
  COALESCE(max(extract(epoch FROM now() - jb.quarantined_at))
    FILTER (WHERE jb.board_status = 'quarantined'), 0),
  (SELECT count(*)
   FROM job_posting jp
   JOIN job_board owner ON owner.id = jp.board_id
   WHERE jp.is_active = true
     AND owner.board_status = 'quarantined'),
  COALESCE(sum(jb.recovery_count), 0)
FROM job_board jb;
""".strip()

BOARD_GONE_SCHEMA_SQL = """
SELECT count(*)
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'job_board'
  AND column_name IN (
    'gone_confirmation_count',
    'gone_first_confirmed_at',
    'gone_last_confirmed_at',
    'last_gone_error',
    'last_gone_endpoint',
    'last_gone_status',
    'gone_transition_count',
    'gone_recovery_count'
  );
""".strip()

BOARD_GONE_STATS_SQL = """
SELECT
  count(*) FILTER (WHERE jb.is_enabled = true AND jb.board_status = 'gone_pending'),
  COALESCE(sum(jb.gone_confirmation_count)
    FILTER (WHERE jb.is_enabled = true AND jb.board_status = 'gone_pending'), 0),
  COALESCE(max(extract(epoch FROM now() - jb.gone_first_confirmed_at))
    FILTER (WHERE jb.is_enabled = true AND jb.board_status = 'gone_pending'), 0),
  count(*) FILTER (WHERE jb.is_enabled = true AND jb.board_status = 'gone'),
  COALESCE(sum(jb.gone_transition_count), 0),
  COALESCE(sum(jb.gone_recovery_count), 0)
FROM job_board jb;
""".strip()

PHANTOM_ACTIVE_STATS_SQL = """
SELECT
  count(DISTINCT jb.id),
  count(jp.id),
  COALESCE(max(extract(epoch FROM now() - jp.updated_at)), 0)
FROM job_posting jp
JOIN job_board jb ON jb.id = jp.board_id
WHERE jp.is_active = true
  AND jb.board_status IN ('disabled', 'gone');
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
            'export PGAPPNAME=jobseek:host-observability; '
            'exec psql -U "${POSTGRES_USER:-postgres}" -d "$db" '
            "-XAt -F '\t' -v ON_ERROR_STOP=1 -c \"$1\"",
            "jobseek-observability",
            sql,
        ],
        timeout=timeout,
    )
    return result.stdout.strip()


def _collect_board_quarantine_metrics(lines: list[str], container: str) -> None:
    """Expose durable quarantine state from the source-of-truth database."""

    schema_columns = _postgresql_query(container, BOARD_QUARANTINE_SCHEMA_SQL)
    schema_ready = int(schema_columns == "6")
    lines.append(_metric("jobseek_crawler_board_quarantine_schema_ready", schema_ready))
    if not schema_ready:
        return

    fields = _postgresql_query(container, BOARD_QUARANTINE_STATS_SQL).split("\t")
    if len(fields) != 4:
        raise ProbeError("board quarantine statistics query returned an unexpected shape")
    try:
        quarantined, oldest_seconds, active_postings, recoveries = (
            float(value) for value in fields
        )
    except ValueError as exc:
        raise ProbeError("board quarantine statistics query returned a non-numeric value") from exc
    lines.extend(
        (
            _metric("jobseek_crawler_quarantined_boards", quarantined),
            _metric("jobseek_crawler_quarantine_oldest_seconds", oldest_seconds),
            _metric("jobseek_crawler_quarantine_active_postings", active_postings),
            _metric("jobseek_crawler_board_recoveries_total", recoveries),
        )
    )


def _collect_board_gone_metrics(lines: list[str], container: str) -> None:
    """Expose durable provider-gone confirmation and recovery state."""

    schema_columns = _postgresql_query(container, BOARD_GONE_SCHEMA_SQL)
    schema_ready = int(schema_columns == "8")
    lines.append(_metric("jobseek_crawler_board_gone_schema_ready", schema_ready))
    if not schema_ready:
        return

    fields = _postgresql_query(container, BOARD_GONE_STATS_SQL).split("\t")
    if len(fields) != 6:
        raise ProbeError("board gone statistics query returned an unexpected shape")
    try:
        pending, confirmations, oldest_seconds, terminal, transitions, recoveries = (
            float(value) for value in fields
        )
    except ValueError as exc:
        raise ProbeError("board gone statistics query returned a non-numeric value") from exc
    lines.extend(
        (
            _metric("jobseek_crawler_gone_pending_boards", pending),
            _metric("jobseek_crawler_gone_pending_confirmations", confirmations),
            _metric("jobseek_crawler_gone_pending_oldest_seconds", oldest_seconds),
            _metric("jobseek_crawler_gone_terminal_boards", terminal),
            _metric("jobseek_crawler_board_gone_transitions_total", transitions),
            _metric("jobseek_crawler_board_gone_recoveries_total", recoveries),
        )
    )


def _collect_phantom_active_metrics(lines: list[str], container: str) -> None:
    """Expose active postings that no terminal board can refresh."""

    fields = _postgresql_query(container, PHANTOM_ACTIVE_STATS_SQL).split("\t")
    if len(fields) != 3:
        raise ProbeError("phantom-active statistics query returned an unexpected shape")
    try:
        boards, postings, oldest_seconds = (float(value) for value in fields)
    except ValueError as exc:
        raise ProbeError("phantom-active statistics query returned a non-numeric value") from exc
    lines.extend(
        (
            _metric("jobseek_crawler_phantom_active_boards", boards),
            _metric("jobseek_crawler_phantom_active_postings", postings),
            _metric("jobseek_crawler_phantom_active_oldest_seconds", oldest_seconds),
        )
    )


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


def _collect_postgresql_emergency_reserve_metrics(lines: list[str], container: str) -> None:
    source = _run(
        [
            "docker",
            "inspect",
            "--format",
            '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}'
            "{{.Source}}{{end}}{{end}}",
            container,
        ],
        timeout=30,
    ).stdout.strip()
    if not source:
        raise ProbeError("PostgreSQL data bind mount was not found")
    mount = _run(["findmnt", "-n", "-o", "TARGET", "--target", source], timeout=30).stdout.strip()
    if not mount.startswith("/mnt/"):
        raise ProbeError("PostgreSQL data filesystem has an unexpected mountpoint")
    reserve = Path(mount) / POSTGRES_EMERGENCY_RESERVE_NAME
    lines.append(
        _metric(
            "jobseek_postgresql_emergency_reserve_target_bytes",
            POSTGRES_EMERGENCY_RESERVE_BYTES,
        )
    )
    try:
        metadata = reserve.lstat()
    except FileNotFoundError:
        lines.append(_metric("jobseek_postgresql_emergency_reserve_bytes", 0))
        return
    if reserve.is_symlink() or not reserve.is_file():
        raise ProbeError("PostgreSQL emergency reserve is not a regular file")
    allocated = metadata.st_blocks * 512
    lines.append(_metric("jobseek_postgresql_emergency_reserve_bytes", allocated))


def _collect_postgresql_metrics(lines: list[str], container: str = "postgres") -> None:
    _collect_postgresql_emergency_reserve_metrics(lines, container)
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

    owner_rows = _postgresql_query(container, POSTGRES_CONNECTION_OWNERS_SQL)
    for owner_row in owner_rows.splitlines():
        owner_fields = owner_row.split("\t")
        if len(owner_fields) != 3:
            raise ProbeError("PostgreSQL connection owner query returned an unexpected shape")
        owner, state, count = owner_fields
        try:
            lines.append(
                _metric(
                    "jobseek_postgresql_connections_by_owner",
                    float(count),
                    owner=owner,
                    state=state,
                )
            )
        except ValueError as exc:
            raise ProbeError(
                "PostgreSQL connection owner query returned a non-numeric value"
            ) from exc

    _collect_board_quarantine_metrics(lines, container)
    _collect_board_gone_metrics(lines, container)
    _collect_phantom_active_metrics(lines, container)

    schema_columns = _postgresql_query(container, RECONCILIATION_SCHEMA_SQL)
    schema_ready = int(schema_columns == "2")
    lines.append(_metric("jobseek_cross_store_reconciliation_schema_ready", schema_ready))
    if not schema_ready:
        return

    state_rows = _postgresql_query(container, RECONCILIATION_STATS_SQL)
    for raw in state_rows.splitlines():
        fields = raw.split("\t")
        if len(fields) != 15:
            raise ProbeError("reconciliation state query returned an unexpected shape")
        target = fields[0]
        outcome = fields[11]
        numbers = (*fields[1:11], *fields[12:15])
        try:
            (
                last_attempt,
                last_success,
                cycle_started,
                duration,
                local_rows,
                remote_rows,
                detected,
                payload_mismatch,
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
                _metric(
                    "jobseek_cross_store_reconciliation_last_payload_mismatch",
                    payload_mismatch,
                    **labels,
                ),
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


def _parse_typesense_nofile_limits(raw: str) -> tuple[int, int]:
    for line in raw.splitlines():
        if line.startswith("Max open files"):
            fields = line.split()
            try:
                return int(fields[3]), int(fields[4])
            except (IndexError, ValueError) as exc:
                raise ProbeError("Typesense nofile limits were not numeric") from exc
    raise ProbeError("Typesense process limits omitted Max open files")


def _parse_typesense_log_metrics(raw: str) -> dict[str, int]:
    queue_depths = [int(match.group(1)) for match in _TYPESENSE_THREADPOOL_RE.finditer(raw)]
    slow_requests = [int(match.group(1)) for match in _TYPESENSE_SLOW_REQUEST_RE.finditer(raw)]
    metrics = {
        "threadpool_queue_depth": max(queue_depths, default=0),
        "slow_request_max_milliseconds": max(slow_requests, default=0),
    }
    metrics.update(
        {
            f"event_{event}": sum(1 for line in raw.splitlines() if pattern.search(line))
            for event, pattern in _TYPESENSE_LOG_EVENT_PATTERNS.items()
        }
    )
    return metrics


def _collect_typesense_metrics(
    lines: list[str],
    container: str = "typesense",
    proc_root: Path = Path("/proc"),
) -> None:
    try:
        inspected = json.loads(_run(["docker", "inspect", container]).stdout)[0]
        pid = int(inspected["State"]["Pid"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProbeError("Typesense container inspect returned an unexpected shape") from exc
    process_root = proc_root / str(pid)
    try:
        nofile_soft, nofile_hard = _parse_typesense_nofile_limits(
            (process_root / "limits").read_text(encoding="utf-8")
        )
        open_fds = sum(1 for _entry in (process_root / "fd").iterdir())
        status = (process_root / "status").read_text(encoding="utf-8")
        threads_match = re.search(r"^Threads:\s+(\d+)$", status, re.MULTILINE)
        if threads_match is None:
            raise ProbeError("Typesense process status omitted Threads")
        threads = int(threads_match.group(1))
    except OSError as exc:
        raise ProbeError("Typesense process metrics were unreadable") from exc
    log_result = _run(
        ["docker", "logs", "--since", "5m", "--tail", "5000", container],
        timeout=30,
    )
    recent_logs = "\n".join(part for part in (log_result.stdout, log_result.stderr) if part)
    log_metrics = _parse_typesense_log_metrics(recent_logs)
    lines.extend(
        (
            _metric("jobseek_typesense_open_file_descriptors", open_fds),
            _metric("jobseek_typesense_nofile_soft_limit", nofile_soft),
            _metric("jobseek_typesense_nofile_hard_limit", nofile_hard),
            _metric("jobseek_typesense_threads", threads),
            _metric(
                "jobseek_typesense_threadpool_queue_depth",
                log_metrics["threadpool_queue_depth"],
            ),
            _metric(
                "jobseek_typesense_slow_request_max_milliseconds",
                log_metrics["slow_request_max_milliseconds"],
            ),
        )
    )
    for event in sorted(_TYPESENSE_LOG_EVENT_PATTERNS):
        lines.append(
            _metric(
                "jobseek_typesense_recent_log_events",
                log_metrics[f"event_{event}"],
                event=event,
            )
        )
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


def _read_loopback(url: str, *, timeout: int = 10) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            if response.status != 200:
                raise ProbeError(f"loopback probe returned HTTP {response.status}")
            return response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as exc:
        raise ProbeError(f"loopback probe failed: {type(exc).__name__}") from exc


def _parse_alloy_metrics(payload: str) -> dict[str, float]:
    values: dict[str, list[float]] = {name: [] for name in ALLOY_METRICS}
    for line in payload.splitlines():
        match = _PROMETHEUS_SAMPLE_RE.match(line)
        if match is None or match.group("name") not in values:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        if value == value and value not in (float("inf"), float("-inf")):
            values[match.group("name")].append(value)

    aggregated: dict[str, float] = {}
    for source, (target, operation) in ALLOY_METRICS.items():
        samples = values[source]
        aggregated[target] = max(samples) if samples and operation == "max" else sum(samples)
    return aggregated


def _recent_alloy_rejections(collector: str) -> int:
    if collector == "compose":
        result = _run(["docker", "logs", "--since", "10m", "deploy-alloy-1"], timeout=30)
    else:
        result = _run(
            [
                "journalctl",
                "--unit",
                "jobseek-alloy.service",
                "--since",
                "10 minutes ago",
                "--no-pager",
                "--output",
                "cat",
            ],
            timeout=30,
        )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return sum(bool(_HTTP_429_RE.search(line)) for line in output.splitlines())


def _collect_alloy_metrics(role: str, lines: list[str]) -> None:
    collectors = [("host", 12347)]
    if role == "crawler":
        collectors.append(("compose", 12346))

    for collector, port in collectors:
        labels = {"collector": collector, "host_role": role}
        try:
            _read_loopback(f"http://127.0.0.1:{port}/-/ready")
        except ProbeError:
            lines.append(_metric("jobseek_alloy_ready", 0, **labels))
            raise
        lines.append(_metric("jobseek_alloy_ready", 1, **labels))
        metrics = _parse_alloy_metrics(
            _read_loopback(f"http://127.0.0.1:{port}/metrics", timeout=15)
        )
        lines.extend(
            _metric(f"jobseek_alloy_{name}", value, **labels) for name, value in metrics.items()
        )
        lines.append(
            _metric(
                "jobseek_alloy_remote_write_rejections_recent",
                _recent_alloy_rejections(collector),
                **labels,
            )
        )


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
        ("alloy", lambda: _collect_alloy_metrics(role, lines)),
    ]
    if role == "postgresql":
        probes.append(("postgresql", lambda: _collect_postgresql_metrics(lines)))
    elif role == "typesense":
        probes.append(("typesense", lambda: _collect_typesense_metrics(lines)))
    elif role == "crawler":
        probes.extend(
            (
                (
                    "reconciliation-deployment",
                    lambda: _collect_reconciliation_deployment_metrics(lines),
                ),
                (
                    "codex-error-review",
                    lambda: _collect_codex_error_review_metrics(lines),
                ),
                (
                    "redis-capacity",
                    lambda: _collect_redis_capacity_metrics(lines, state_dir, now=now),
                ),
                (
                    "ats-inventory",
                    lambda: _collect_ats_inventory_metrics(lines),
                ),
            )
        )
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
