#!/usr/bin/env python3
"""Collect a redacted read-only evidence bundle for daily error review.

This script is intended to run as root from systemd ``ExecStartPre``. It
collects host signals and Docker logs, redacts common credential shapes, and
writes files readable by the unprivileged ``codex-runner`` account.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc  # noqa: UP017 - systemd runs this script with Python 3.10.

LONG_RUNNING_CONTAINERS = (
    "deploy-worker-1-1",
    "deploy-worker-2-1",
    "deploy-worker-3-1",
    "deploy-browser-1-1",
    "deploy-exporter-1",
    "deploy-drain-1",
    "deploy-redis-1",
    "deploy-alloy-1",
)

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_METRICS_RESPONSE_BYTES = 4 * 1024 * 1024
METRICS_STEP_SECONDS = 300
METRICS_SCHEMA_VERSION = 1
METRICS_CONFIG_KEYS = frozenset(
    {
        "GRAFANA_METRICS_READ_URL",
        "GRAFANA_METRICS_READ_USERNAME",
        "GRAFANA_METRICS_READ_TOKEN",
    }
)
METRIC_LABEL_ALLOWLIST = frozenset(
    {
        "alertname",
        "alertstate",
        "container",
        "host_role",
        "instance",
        "job",
        "metric",
        "outcome",
        "phase",
        "probe",
        "queue",
        "reason",
        "service",
        "severity",
        "signal",
        "status",
        "table",
        "target",
        "unit",
        "worker_id",
        "wtype",
    }
)

# Every expression is repo-owned and bounded. The evidence output records only
# the stable query ID, not the endpoint, credential, or arbitrary PromQL.
METRIC_QUERIES: tuple[dict[str, Any], ...] = (
    {
        "id": "scrape_targets",
        "signal": "Alloy and application scrape continuity",
        "query": (
            'max by (job, instance, host_role) (up{job=~"crawler|integrations/unix|jobseek-alloy"})'
        ),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "min_fresh_series": 12,
        "max_series": 30,
    },
    {
        "id": "alloy_remote_write_failures",
        "signal": "Alloy remote-write failures and retries",
        "query": (
            "label_replace(sum by (instance, host_role) "
            "(increase(prometheus_remote_storage_samples_failed_total[5m])), "
            '"signal", "failed_samples", "", ".*") '
            "or label_replace(sum by (instance, host_role) "
            "(increase(prometheus_remote_storage_enqueue_retries_total[5m])), "
            '"signal", "enqueue_retries", "", ".*")'
        ),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "min_fresh_series": 6,
        "required_signals": ("enqueue_retries", "failed_samples"),
        "max_series": 12,
    },
    {
        "id": "alloy_remote_write_pending",
        "signal": "Alloy remote-write queue pressure",
        "query": ("sum by (instance, host_role) (prometheus_remote_storage_samples_pending)"),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "min_fresh_series": 3,
        "max_series": 12,
    },
    {
        "id": "worker_browser_memory",
        "signal": "Worker and browser resident memory",
        "query": (
            'process_resident_memory_bytes{job="crawler",instance=~"worker-[123]|browser-1"}'
        ),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "min_fresh_series": 4,
        "max_series": 8,
    },
    {
        "id": "worker_browser_oom_restart",
        "signal": "Worker and browser OOM/restart state",
        "query": (
            "label_replace(max by (container) "
            '(jobseek_container_oom_killed{host_role="crawler",'
            'container=~"deploy-(worker-[123]|browser-1)-1"}), '
            '"signal", "oom_killed", "", ".*") '
            "or label_replace(max by (container) "
            '(jobseek_container_restart_count{host_role="crawler",'
            'container=~"deploy-(worker-[123]|browser-1)-1"}), '
            '"signal", "restart_count", "", ".*")'
        ),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "min_fresh_series": 8,
        "required_signals": ("oom_killed", "restart_count"),
        "max_series": 12,
    },
    {
        "id": "redis_queues",
        "signal": "Redis queue, inflight, and dead-letter depth",
        "query": (
            "label_replace(max by (queue) (crawler_redis_queue_depth), "
            '"signal", "queue_depth", "", ".*") '
            "or label_replace(max by (wtype) (crawler_inflight_depth), "
            '"signal", "inflight_depth", "", ".*") '
            "or label_replace(max by (wtype) (crawler_inflight_deadletter_depth), "
            '"signal", "deadletter_depth", "", ".*")'
        ),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "min_fresh_series": 10,
        "required_signals": ("deadletter_depth", "inflight_depth", "queue_depth"),
        "max_series": 24,
    },
    {
        "id": "redis_backpressure",
        "signal": "Redis memory and client backpressure",
        "query": (
            "label_replace(redis_memory_used_bytes / redis_memory_max_bytes, "
            '"signal", "memory_ratio", "", ".*") '
            'or label_replace(redis_blocked_clients, "signal", "blocked_clients", "", ".*") '
            "or label_replace(increase(redis_rejected_connections_total[5m]), "
            '"signal", "rejected_connections", "", ".*")'
        ),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "min_fresh_series": 3,
        "required_signals": ("blocked_clients", "memory_ratio", "rejected_connections"),
        "max_series": 12,
    },
    {
        "id": "exporter_progress",
        "signal": "Exporter cursors, lag, downstream state, and flush freshness",
        "query": (
            "label_replace(crawler_exporter_cursor_timestamp_seconds, "
            '"signal", "cursor_timestamp", "", ".*") '
            "or label_replace(crawler_exporter_export_lag, "
            '"signal", "supabase_lag", "", ".*") '
            "or label_replace(crawler_typesense_export_lag, "
            '"signal", "typesense_lag", "", ".*") '
            "or label_replace(crawler_exporter_downstream_available, "
            '"signal", "downstream_available", "", ".*") '
            "or label_replace(crawler_exporter_downstream_backoff_seconds, "
            '"signal", "downstream_backoff", "", ".*") '
            "or label_replace(crawler_exporter_last_flush_ts, "
            '"signal", "last_flush", "", ".*")'
        ),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "required_signals": (
            "cursor_timestamp",
            "downstream_available",
            "downstream_backoff",
            "last_flush",
            "supabase_lag",
            "typesense_lag",
        ),
        "max_series": 24,
    },
    {
        "id": "reconciliation",
        "signal": "Durable cross-store reconciliation freshness, outcome, and drift",
        "query": (
            "label_replace(jobseek_cross_store_reconciliation_schema_ready, "
            '"signal", "schema_ready", "", ".*") '
            "or label_replace(jobseek_cross_store_reconciliation_last_attempt_unixtime, "
            '"signal", "last_attempt", "", ".*") '
            "or label_replace(jobseek_cross_store_reconciliation_last_attempt_success, "
            '"signal", "last_attempt_success", "", ".*") '
            "or label_replace(jobseek_cross_store_reconciliation_last_success_unixtime, "
            '"signal", "last_success", "", ".*") '
            "or label_replace(jobseek_cross_store_reconciliation_last_detected, "
            '"signal", "detected", "", ".*") '
            "or label_replace(jobseek_cross_store_reconciliation_last_repaired, "
            '"signal", "repaired", "", ".*") '
            "or label_replace(jobseek_cross_store_reconciliation_last_unresolved, "
            '"signal", "unresolved", "", ".*") '
            "or label_replace(jobseek_cross_store_reconciliation_stuck_runs, "
            '"signal", "stuck_runs", "", ".*")'
        ),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "min_fresh_series": 14,
        "required_signals": (
            "detected",
            "last_attempt",
            "last_attempt_success",
            "last_success",
            "repaired",
            "schema_ready",
            "stuck_runs",
            "unresolved",
        ),
        "max_series": 20,
    },
    {
        "id": "drain_health",
        "signal": "R2 drain backlog, progress, and failures",
        "query": (
            'label_replace(crawler_r2_pending, "signal", "pending", "", ".*") '
            "or label_replace(crawler_redis_r2_stream_length, "
            '"signal", "stream_length", "", ".*") '
            "or label_replace((sum(increase(crawler_r2_uploaded_total[5m])) or vector(0)), "
            '"signal", "upload_progress", "", ".*") '
            "or label_replace((sum(increase(crawler_r2_retry_scheduled_total[5m])) "
            "or vector(0)), "
            '"signal", "retry_scheduled", "", ".*")'
        ),
        "mode": "range",
        "required": True,
        "allow_empty": False,
        "required_signals": (
            "pending",
            "retry_scheduled",
            "stream_length",
            "upload_progress",
        ),
        "max_series": 16,
    },
    {
        "id": "codex_alert_state",
        "signal": "Codex-owned pending/firing alert history",
        "query": 'ALERTS{owner="codex-error-review",route="codex-daily"}',
        "mode": "range",
        "required": True,
        "allow_empty": True,
        "allow_stale": True,
        "max_series": 50,
    },
)

REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*"
            r"(bearer|basic)\s+[A-Za-z0-9._~+/\-]+=*"
        ),
        r"\1: <redacted>",
    ),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API[_-]?KEY|PRIVATE[_-]?KEY)"
            r"[A-Z0-9_]*)\s*[:=]\s*([^\s,;\"']+)"
        ),
        r"\1=<redacted>",
    ),
    (
        re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/\-]+=*"),
        r"\1 <redacted>",
    ),
    (
        re.compile(r"://([^:/\s]+):([^@/\s]+)@"),
        r"://\1:<redacted>@",
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "-----BEGIN PRIVATE KEY-----<redacted>-----END PRIVATE KEY-----",
    ),
)


def _redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _utc_minute_floor() -> datetime:
    return datetime.now(tz=UTC).replace(second=0, microsecond=0)


def _run(cmd: list[str], *, timeout: int = 180) -> tuple[int | None, str]:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{type(exc).__name__}: {exc}\n"
    return result.returncode, result.stdout


def _run_shell(command: str, *, timeout: int = 180) -> tuple[int | None, str]:
    return _run(["/bin/bash", "-lc", command], timeout=timeout)


def _write(path: Path, text: str) -> dict[str, object]:
    redacted = _redact(text)
    data = redacted.encode("utf-8", errors="replace")
    truncated = len(data) > MAX_FILE_BYTES
    if truncated:
        data = data[:MAX_FILE_BYTES]
        data += b"\n\n[truncated by codex-error-review-bundle.py]\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o640)
    return {"path": str(path), "bytes": path.stat().st_size, "truncated": truncated}


def _write_bounded_json(path: Path, value: object) -> dict[str, object]:
    """Write complete JSON or fail; never leave truncated evidence behind."""
    data = _redact(json.dumps(value, indent=2, sort_keys=True)).encode("utf-8", errors="replace")
    if len(data) > MAX_FILE_BYTES:
        raise MetricsEvidenceError("normalized metrics evidence exceeded the byte limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o640)
    return {"path": str(path), "bytes": path.stat().st_size, "truncated": False}


class MetricsEvidenceError(RuntimeError):
    """A bounded metrics query or its dedicated credential is invalid."""


def _load_metrics_config(path: Path, *, required_uid: int | None = None) -> dict[str, str]:
    """Load the dedicated read-only config without shell expansion."""
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise MetricsEvidenceError("metrics credential file unavailable") from exc
    expected_uid = 0 if required_uid is None else required_uid
    if not stat.S_ISREG(file_stat.st_mode):
        raise MetricsEvidenceError("metrics credential path is not a regular file")
    if file_stat.st_uid != expected_uid or stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise MetricsEvidenceError("metrics credential ownership or mode is unsafe")

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MetricsEvidenceError("metrics credential file cannot be read") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise MetricsEvidenceError("metrics credential file contains an invalid row")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in METRICS_CONFIG_KEYS or key in values:
            raise MetricsEvidenceError("metrics credential file contains an unexpected key")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value or any(char in value for char in "\r\n\x00"):
            raise MetricsEvidenceError("metrics credential file contains an invalid value")
        values[key] = value
    if set(values) != METRICS_CONFIG_KEYS:
        raise MetricsEvidenceError("metrics credential file is incomplete")

    parsed = urllib.parse.urlsplit(values["GRAFANA_METRICS_READ_URL"])
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.rstrip("/").endswith("/api/prom")
    ):
        raise MetricsEvidenceError("metrics query URL is not an approved HTTPS query endpoint")
    if any(char.isspace() for char in values["GRAFANA_METRICS_READ_USERNAME"]):
        raise MetricsEvidenceError("metrics query username is invalid")
    if len(values["GRAFANA_METRICS_READ_TOKEN"]) < 20:
        raise MetricsEvidenceError("metrics read token is invalid")
    values["GRAFANA_METRICS_READ_URL"] = values["GRAFANA_METRICS_READ_URL"].rstrip("/")
    return values


def _metrics_request(
    config: dict[str, str],
    spec: dict[str, Any],
    *,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    mode = str(spec["mode"])
    params: dict[str, str] = {"query": str(spec["query"])}
    if mode == "range":
        params.update(
            {
                "start": str(int(since.timestamp())),
                "end": str(int(until.timestamp())),
                "step": str(METRICS_STEP_SECONDS),
            }
        )
        endpoint = "/api/v1/query_range"
    elif mode == "instant":
        params["time"] = str(int(until.timestamp()))
        endpoint = "/api/v1/query"
    else:
        raise MetricsEvidenceError("unsupported metrics query mode")

    body = urllib.parse.urlencode(params).encode("utf-8")
    login = (
        f"{config['GRAFANA_METRICS_READ_USERNAME']}:{config['GRAFANA_METRICS_READ_TOKEN']}"
    ).encode()
    request = urllib.request.Request(
        config["GRAFANA_METRICS_READ_URL"] + endpoint,
        data=body,
        headers={
            "Authorization": "Basic " + base64.b64encode(login).decode("ascii"),
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "jobseek-codex-error-review/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read(MAX_METRICS_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise MetricsEvidenceError(f"metrics query returned HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise MetricsEvidenceError(f"metrics query transport failed: {type(exc).__name__}") from exc
    if len(payload) > MAX_METRICS_RESPONSE_BYTES:
        raise MetricsEvidenceError("metrics query response exceeded the byte limit")
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetricsEvidenceError("metrics query returned invalid JSON") from exc
    if not isinstance(parsed, dict) or parsed.get("status") != "success":
        raise MetricsEvidenceError("metrics query did not report success")
    return parsed


def _normalized_metric_result(
    spec: dict[str, Any],
    response: dict[str, Any],
    *,
    until: datetime,
) -> dict[str, Any]:
    data = response.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        raise MetricsEvidenceError("metrics query result is not a list")
    max_series = int(spec["max_series"])
    if len(result) > max_series:
        raise MetricsEvidenceError("metrics query exceeded the series limit")

    normalized: list[dict[str, Any]] = []
    sample_count = 0
    newest: float | None = None
    fresh_series_count = 0
    fresh_signals: set[str] = set()
    observed_signals: set[str] = set()
    for raw_series in result:
        if not isinstance(raw_series, dict):
            raise MetricsEvidenceError("metrics query returned an invalid series")
        raw_labels = raw_series.get("metric")
        if not isinstance(raw_labels, dict):
            raise MetricsEvidenceError("metrics query returned invalid labels")
        labels: dict[str, str] = {}
        for key, value in raw_labels.items():
            output_key = "metric" if key == "__name__" else str(key)
            if output_key in METRIC_LABEL_ALLOWLIST:
                labels[output_key] = _redact(str(value))[:200]
        if labels.get("signal"):
            observed_signals.add(labels["signal"])

        if spec["mode"] == "range":
            raw_values = raw_series.get("values")
        else:
            value = raw_series.get("value")
            raw_values = [value] if isinstance(value, list) else None
        if not isinstance(raw_values, list):
            raise MetricsEvidenceError("metrics query returned invalid samples")
        samples: list[list[object]] = []
        series_newest: float | None = None
        for raw_sample in raw_values:
            if not isinstance(raw_sample, list) or len(raw_sample) != 2:
                raise MetricsEvidenceError("metrics query returned an invalid sample")
            try:
                timestamp = float(raw_sample[0])
            except (TypeError, ValueError) as exc:
                raise MetricsEvidenceError("metrics query returned an invalid timestamp") from exc
            value = str(raw_sample[1])
            if len(value) > 80 or not re.fullmatch(
                r"(?:[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|NaN|[-+]?Inf)",
                value,
            ):
                raise MetricsEvidenceError("metrics query returned an invalid value")
            samples.append([int(timestamp), value])
            sample_count += 1
            newest = timestamp if newest is None else max(newest, timestamp)
            series_newest = timestamp if series_newest is None else max(series_newest, timestamp)
        series_freshness = (
            max(0, int(until.timestamp() - series_newest)) if series_newest is not None else None
        )
        if series_freshness is not None and series_freshness <= METRICS_STEP_SECONDS * 3:
            fresh_series_count += 1
            if labels.get("signal"):
                fresh_signals.add(labels["signal"])
        normalized.append(
            {
                "labels": labels,
                "newest_sample_at": (
                    datetime.fromtimestamp(series_newest, tz=UTC).isoformat()
                    if series_newest is not None
                    else None
                ),
                "freshness_seconds": series_freshness,
                "samples": samples,
            }
        )

    max_samples = max_series * (24 * 3600 // METRICS_STEP_SECONDS + 2)
    if sample_count > max_samples:
        raise MetricsEvidenceError("metrics query exceeded the sample limit")

    allow_empty = bool(spec["allow_empty"])
    allow_stale = bool(spec.get("allow_stale"))
    required_signals = set(spec.get("required_signals", ()))
    coverage_signals = observed_signals if allow_stale else fresh_signals
    coverage_series_count = len(normalized) if allow_stale else fresh_series_count
    missing_required_signals = sorted(required_signals - coverage_signals)
    minimum_fresh = int(spec.get("min_fresh_series", 1))
    if not normalized:
        status = "ok_empty" if allow_empty else "missing"
        freshness_seconds = None
    else:
        freshness_seconds = max(0, int(until.timestamp() - (newest or 0)))
        if missing_required_signals or coverage_series_count < minimum_fresh:
            status = "missing" if fresh_series_count else "stale"
        else:
            status = "ok"
    return {
        "id": spec["id"],
        "signal": spec["signal"],
        "required": bool(spec["required"]),
        "status": status,
        "series_count": len(normalized),
        "fresh_series_count": fresh_series_count,
        "stale_series_count": len(normalized) - fresh_series_count,
        "sample_count": sample_count,
        "newest_sample_at": (
            datetime.fromtimestamp(newest, tz=UTC).isoformat() if newest is not None else None
        ),
        "freshness_seconds": freshness_seconds,
        "missing_required_signals": missing_required_signals,
        "series": normalized,
    }


def _unavailable_metric_queries(error_class: str) -> list[dict[str, Any]]:
    return [
        {
            "id": spec["id"],
            "signal": spec["signal"],
            "required": bool(spec["required"]),
            "status": "error",
            "series_count": 0,
            "fresh_series_count": 0,
            "stale_series_count": 0,
            "sample_count": 0,
            "newest_sample_at": None,
            "freshness_seconds": None,
            "missing_required_signals": list(spec.get("required_signals", ())),
            "error_class": error_class,
            "series": [],
        }
        for spec in METRIC_QUERIES
    ]


def _collect_historical_metrics(
    config_path: Path,
    *,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    try:
        config = _load_metrics_config(config_path)
    except MetricsEvidenceError:
        queries = _unavailable_metric_queries("credential_unavailable")
    else:
        queries = []
        for spec in METRIC_QUERIES:
            try:
                response = _metrics_request(config, spec, since=since, until=until)
                result = _normalized_metric_result(spec, response, until=until)
            except MetricsEvidenceError as exc:
                result = {
                    "id": spec["id"],
                    "signal": spec["signal"],
                    "required": bool(spec["required"]),
                    "status": "error",
                    "series_count": 0,
                    "fresh_series_count": 0,
                    "stale_series_count": 0,
                    "sample_count": 0,
                    "newest_sample_at": None,
                    "freshness_seconds": None,
                    "missing_required_signals": list(spec.get("required_signals", ())),
                    "error_class": _safe_name(str(exc))[:120],
                    "series": [],
                }
            queries.append(result)

    required_complete = all(
        not query["required"] or query["status"] in {"ok", "ok_empty"} for query in queries
    )
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "provider": "grafana-cloud-prometheus",
        "credential_boundary": "root-only dedicated metrics:read token",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "window": {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "hours": int((until - since).total_seconds() // 3600),
            "step_seconds": METRICS_STEP_SECONDS,
        },
        "required_complete": required_complete,
        "queries": queries,
    }


def _error_lines(text: str) -> str:
    keep: list[str] = []
    for line in text.splitlines():
        lower = line.lower()
        if any(
            marker in lower
            for marker in (
                '"level": "error"',
                '"level":"error"',
                "level=error",
                "traceback",
                "exception",
                "error",
                "oom",
                "killed",
            )
        ):
            keep.append(line)
    return "\n".join(keep) + ("\n" if keep else "")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "unknown"


def _parse_docker_timestamp(value: str) -> datetime | None:
    if not value or value.startswith("0001-01-01"):
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "." in normalized:
        prefix, suffix = normalized.split(".", 1)
        offset_index = max(suffix.rfind("+"), suffix.rfind("-"))
        if offset_index >= 0:
            fraction = suffix[:offset_index][:6]
            offset = suffix[offset_index:]
            normalized = f"{prefix}.{fraction}{offset}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_cgroup_scalar(value: str) -> int | str | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized == "max":
        return normalized
    try:
        return int(normalized)
    except ValueError:
        return normalized


def _parse_cgroup_key_values(text: str) -> dict[str, int]:
    """Parse a cgroup ``key value`` file, ignoring malformed rows."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            values[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return values


def _read_cgroup_memory_files(root: Path) -> dict[str, object]:
    """Read cgroup-v2 memory evidence from a container's cgroup mount."""
    result: dict[str, object] = {"version": 2}
    scalar_files = {
        "memory.current": "current_bytes",
        "memory.peak": "peak_bytes",
        "memory.max": "limit_bytes",
        "memory.swap.current": "swap_current_bytes",
    }
    for filename, key in scalar_files.items():
        try:
            parsed = _parse_cgroup_scalar((root / filename).read_text(encoding="utf-8"))
        except OSError:
            continue
        if parsed is not None:
            result[key] = parsed

    for filename, key in (
        ("memory.events", "events"),
        ("memory.events.local", "events_local"),
    ):
        try:
            result[key] = _parse_cgroup_key_values((root / filename).read_text(encoding="utf-8"))
        except OSError:
            continue
    return result


def _collect_command(
    run_dir: Path,
    manifest: dict[str, object],
    name: str,
    cmd: list[str],
    *,
    timeout: int = 180,
) -> None:
    code, output = _run(cmd, timeout=timeout)
    file_info = _write(run_dir / "host" / f"{name}.txt", output)
    manifest.setdefault("commands", []).append(
        {"name": name, "cmd": cmd, "returncode": code, **file_info}
    )


def _collect_shell(
    run_dir: Path,
    manifest: dict[str, object],
    name: str,
    command: str,
    *,
    timeout: int = 180,
) -> None:
    code, output = _run_shell(command, timeout=timeout)
    file_info = _write(run_dir / "host" / f"{name}.txt", output)
    manifest.setdefault("commands", []).append(
        {"name": name, "cmd": command, "returncode": code, **file_info}
    )


def _collect_container_logs(
    run_dir: Path,
    manifest: dict[str, object],
    *,
    since: datetime,
    until: datetime,
) -> None:
    since_iso = since.isoformat().replace("+00:00", "Z")
    until_iso = until.isoformat().replace("+00:00", "Z")
    for container in LONG_RUNNING_CONTAINERS:
        code, output = _run(
            ["docker", "logs", "--since", since_iso, "--until", until_iso, container],
            timeout=600,
        )
        log_info = _write(run_dir / "logs" / f"{container}.log", output)
        err_info = _write(run_dir / "error-lines" / f"{container}.log", _error_lines(output))
        manifest.setdefault("container_logs", []).append(
            {
                "container": container,
                "returncode": code,
                "log": log_info,
                "error_lines": err_info,
            }
        )


def _collect_container_cgroup_memory(
    run_dir: Path,
    manifest: dict[str, object],
) -> None:
    """Capture generation-aware Docker and cgroup-v2 memory evidence."""
    containers: list[dict[str, object]] = []
    for container in LONG_RUNNING_CONTAINERS:
        code, output = _run(["docker", "inspect", container], timeout=60)
        if code != 0:
            containers.append(
                {
                    "name": container,
                    "inspect_returncode": code,
                    "inspect_error": output[:500],
                }
            )
            continue
        try:
            inspect_data = json.loads(output)[0]
        except (IndexError, json.JSONDecodeError) as exc:
            containers.append(
                {
                    "name": container,
                    "inspect_returncode": code,
                    "inspect_error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        state = inspect_data.get("State", {})
        try:
            pid = int(state.get("Pid", 0))
        except (TypeError, ValueError):
            pid = 0
        entry: dict[str, object] = {
            "name": str(inspect_data.get("Name", "")).lstrip("/") or container,
            "container_id": str(inspect_data.get("Id", "")),
            "image": str(inspect_data.get("Config", {}).get("Image", "")),
            "created_at": str(inspect_data.get("Created", "")),
            "started_at": str(state.get("StartedAt", "")),
            "finished_at": str(state.get("FinishedAt", "")),
            "status": str(state.get("Status", "")),
            "exit_code": state.get("ExitCode"),
            "state_error": str(state.get("Error", "")),
            "restart_count": inspect_data.get("RestartCount", 0),
            "oom_killed": bool(state.get("OOMKilled", False)),
            "pid": pid,
        }
        if pid > 0:
            cgroup_root = Path(f"/proc/{pid}/root/sys/fs/cgroup")
            cgroup = _read_cgroup_memory_files(cgroup_root)
            if len(cgroup) > 1:
                entry["cgroup_memory"] = cgroup
            else:
                entry["cgroup_memory_error"] = (
                    f"no readable cgroup-v2 memory files under {cgroup_root}"
                )
        containers.append(entry)

    file_info = _write(
        run_dir / "host" / "docker-cgroup-memory.json",
        json.dumps(containers, indent=2, sort_keys=True),
    )
    manifest["container_cgroup_memory"] = file_info


def _collect_exited_containers(
    run_dir: Path,
    manifest: dict[str, object],
    *,
    since: datetime,
    until: datetime,
) -> None:
    code, output = _run(
        [
            "docker",
            "ps",
            "-a",
            "-q",
            "--filter",
            "status=exited",
        ],
        timeout=120,
    )
    candidates: list[dict[str, str]] = []
    inspect_errors: list[dict[str, object]] = []

    if code == 0:
        for container_id in [line.strip() for line in output.splitlines() if line.strip()]:
            inspect_code, inspect_output = _run(["docker", "inspect", container_id], timeout=60)
            if inspect_code != 0:
                inspect_errors.append(
                    {
                        "id": container_id,
                        "returncode": inspect_code,
                        "output": inspect_output[:500],
                    }
                )
                continue
            try:
                inspect_data = json.loads(inspect_output)[0]
            except (IndexError, json.JSONDecodeError) as exc:
                inspect_errors.append(
                    {
                        "id": container_id,
                        "returncode": inspect_code,
                        "output": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            image = str(inspect_data.get("Config", {}).get("Image", ""))
            if not (image.startswith("ghcr.io/") and "/jobseek-crawler" in image):
                continue

            state = inspect_data.get("State", {})
            finished_at = _parse_docker_timestamp(str(state.get("FinishedAt", "")))
            if finished_at is None or not (since <= finished_at <= until):
                continue

            candidates.append(
                {
                    "id": container_id,
                    "name": str(inspect_data.get("Name", "")).lstrip("/") or container_id,
                    "image": image,
                    "finished_at": finished_at.isoformat(),
                    "status": str(state.get("Status", "")),
                    "exit_code": str(state.get("ExitCode", "")),
                    "state_error": str(state.get("Error", "")),
                    "restart_count": str(inspect_data.get("RestartCount", 0)),
                    "oom_killed": str(bool(state.get("OOMKilled", False))).lower(),
                }
            )

    listing = "\n".join(
        "\t".join(
            (
                candidate["id"],
                candidate["name"],
                candidate["image"],
                candidate["finished_at"],
                candidate["status"],
                candidate["exit_code"],
                candidate["state_error"],
                candidate["restart_count"],
                candidate["oom_killed"],
            )
        )
        for candidate in candidates
    )
    if listing:
        listing += "\n"
    list_info = _write(
        run_dir / "host" / "docker-exited-containers.txt",
        output if code != 0 else listing,
    )
    manifest.setdefault("commands", []).append(
        {
            "name": "docker-exited-containers",
            "cmd": "docker ps -aq --filter status=exited + docker inspect",
            "returncode": code,
            "window_filtered": code == 0,
            **list_info,
        }
    )
    if inspect_errors:
        manifest["exited_container_inspect_errors"] = inspect_errors[:30]
    if code != 0:
        return
    since_iso = since.isoformat().replace("+00:00", "Z")
    until_iso = until.isoformat().replace("+00:00", "Z")
    for candidate in candidates[:30]:
        container_id = candidate["id"]
        name = candidate["name"]
        code, logs = _run(
            [
                "docker",
                "logs",
                "--since",
                since_iso,
                "--until",
                until_iso,
                "--tail",
                "1000",
                container_id,
            ],
            timeout=180,
        )
        info = _write(
            run_dir / "exited" / f"{_safe_name(name)}-{container_id}.log",
            logs,
        )
        manifest.setdefault("exited_container_logs", []).append(
            {
                "id": container_id,
                "name": name,
                "image": candidate["image"],
                "finished_at": candidate["finished_at"],
                "exit_code": candidate["exit_code"],
                "state_error": candidate["state_error"],
                "restart_count": candidate["restart_count"],
                "oom_killed": candidate["oom_killed"],
                "returncode": code,
                **info,
            }
        )


def _collect_docker_lifecycle_journal(
    run_dir: Path,
    manifest: dict[str, object],
    *,
    since: datetime,
    until: datetime,
) -> None:
    """Collect the allowlisted event stream persisted by the root watcher."""
    code, output = _run(
        [
            "journalctl",
            "--unit",
            "jobseek-codex-docker-lifecycle.service",
            "--identifier",
            "jobseek-docker-lifecycle",
            "--since",
            f"@{since.timestamp():.0f}",
            "--until",
            f"@{until.timestamp():.0f}",
            "--output=cat",
            "--quiet",
            "--no-pager",
        ],
        timeout=180,
    )
    file_info = _write(run_dir / "host" / "docker-lifecycle.jsonl", output)
    manifest["docker_lifecycle"] = {"returncode": code, **file_info}


def _chgrp_readable(path: Path, *, group: str) -> None:
    import grp

    gid = grp.getgrnam(group).gr_gid
    paths = [path, *path.rglob("*")]
    for item in paths:
        try:
            os.chown(item, 0, gid)
            item.chmod(0o750 if item.is_dir() else 0o640)
        except OSError:
            continue


def collect_bundle(
    out_root: Path,
    *,
    window_hours: int,
    group: str,
    metrics_env: Path,
) -> Path:
    until = _utc_minute_floor()
    since = until - timedelta(hours=window_hours)
    run_dir = out_root / until.strftime("%Y-%m-%dT%H%MZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "created_at": datetime.now(tz=UTC).isoformat(),
        "window": {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "hours": window_hours,
        },
        "redaction": "common credential shapes redacted before codex-runner reads bundle",
        "max_file_bytes": MAX_FILE_BYTES,
    }

    _collect_command(run_dir, manifest, "df-root", ["df", "-h", "/"])
    _collect_command(run_dir, manifest, "df-docker", ["df", "-h", "/var/lib/docker"])
    _collect_command(run_dir, manifest, "free", ["free", "-h"])
    _collect_command(run_dir, manifest, "uptime", ["uptime"])
    _collect_command(run_dir, manifest, "cpu-count", ["nproc"])
    _collect_command(
        run_dir,
        manifest,
        "docker-ps",
        ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
    )
    _collect_command(
        run_dir,
        manifest,
        "docker-stats",
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
        ],
        timeout=120,
    )
    inspect_state_command = (
        'ids=$(docker ps -aq); test -z "$ids" || docker inspect --format '
        "'{{.Name}} ID={{.Id}} Image={{.Config.Image}} Created={{.Created}} "
        "StartedAt={{.State.StartedAt}} OOMKilled={{.State.OOMKilled}} "
        "Status={{.State.Status}} RestartCount={{.RestartCount}} "
        "FinishedAt={{.State.FinishedAt}} ExitCode={{.State.ExitCode}} "
        "Error={{json .State.Error}}' $ids"
    )
    _collect_shell(
        run_dir,
        manifest,
        "docker-inspect-state",
        inspect_state_command,
        timeout=180,
    )
    _collect_container_cgroup_memory(run_dir, manifest)
    _collect_docker_lifecycle_journal(run_dir, manifest, since=since, until=until)
    kernel_log_command = (
        f"journalctl -k --since '{since.isoformat()}' --until '{until.isoformat()}' "
        "--no-pager 2>/dev/null | tail -500"
    )
    _collect_shell(
        run_dir,
        manifest,
        "kernel-log",
        kernel_log_command,
        timeout=180,
    )
    _collect_container_logs(run_dir, manifest, since=since, until=until)
    _collect_exited_containers(run_dir, manifest, since=since, until=until)

    metrics_evidence = _collect_historical_metrics(metrics_env, since=since, until=until)
    metrics_info = _write_bounded_json(
        run_dir / "metrics" / "historical-prometheus.json",
        metrics_evidence,
    )
    manifest["historical_metrics"] = {
        "provider": metrics_evidence["provider"],
        "required_complete": metrics_evidence["required_complete"],
        "query_count": len(metrics_evidence["queries"]),
        "unavailable_query_ids": [
            query["id"]
            for query in metrics_evidence["queries"]
            if query["status"] not in {"ok", "ok_empty"}
        ],
        **metrics_info,
    }

    _write(run_dir / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    _chgrp_readable(run_dir, group=group)

    latest = out_root / "latest"
    tmp = out_root / ".latest.tmp"
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(run_dir.name, target_is_directory=True)
    tmp.replace(latest)
    try:
        import grp

        os.chown(out_root, 0, grp.getgrnam(group).gr_gid)
        out_root.chmod(0o750)
    except OSError:
        pass
    return run_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect daily error-review evidence bundle.")
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("/srv/jobseek-codex/inputs/error-review"),
    )
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--group", default="codex-runner")
    parser.add_argument(
        "--metrics-env",
        type=Path,
        default=Path("/etc/jobseek-codex/error-review-metrics.env"),
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if shutil.which("docker") is None:
        raise SystemExit("docker command not found")
    if args.window_hours != 24:
        raise SystemExit("error-review evidence window must be exactly 24 hours")
    run_dir = collect_bundle(
        args.out_root,
        window_hours=args.window_hours,
        group=args.group,
        metrics_env=args.metrics_env,
    )
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
