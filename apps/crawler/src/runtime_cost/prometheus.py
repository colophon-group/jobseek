"""Read-only Prometheus capture for the current Python crawler fleet."""

from __future__ import annotations

import base64
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from src.runtime_cost.model import MEASUREMENT_SCHEMA, ModelError

TARGET_SCHEMA = "jobseek.crawler-runtime-capture-targets/v1"
Query = Callable[[str, datetime], list[dict[str, Any]]]


def _prom_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class PrometheusClient:
    """Minimal Prometheus instant-query client with optional basic auth."""

    def __init__(self, url: str, username: str | None = None, password: str | None = None):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ModelError("Prometheus URL must be an absolute HTTP(S) URL")
        self._url = url
        self._auth = None
        if username is not None or password is not None:
            if not username or password is None:
                raise ModelError("Prometheus basic auth requires both username and password")
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            self._auth = f"Basic {token}"

    def query(self, query: str, at: datetime) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"query": query, "time": at.timestamp()})
        request = urllib.request.Request(f"{self._url}?{params}")
        if self._auth:
            request.add_header("Authorization", self._auth)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = json.load(response)
        except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            raise ModelError(f"Prometheus query failed: {type(exc).__name__}") from exc
        if (
            payload.get("status") != "success"
            or payload.get("data", {}).get("resultType") != "vector"
        ):
            raise ModelError("Prometheus returned a non-vector or unsuccessful response")
        return list(payload["data"]["result"])


def _sum_vector(rows: list[dict[str, Any]], query_name: str) -> float:
    total = 0.0
    for row in rows:
        try:
            value = float(row["value"][1])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError(f"Prometheus {query_name} response is malformed") from exc
        if not math.isfinite(value):
            raise ModelError(f"Prometheus {query_name} returned a non-finite value")
        total += max(0.0, value)
    return total


def _instant_sum(query: Query, expression: str, at: datetime, query_name: str) -> float:
    return _sum_vector(query(expression, at), query_name)


def capture_prometheus_measurement(
    targets: dict[str, Any],
    *,
    query: Query,
    end_at: datetime,
    window_seconds: int,
    source_revision: str,
) -> dict[str, Any]:
    """Capture a historical window without touching a publisher origin."""

    if targets.get("schema_version") != TARGET_SCHEMA:
        raise ModelError("unsupported capture-target schema")
    if targets.get("implementation") != "python-playwright":
        raise ModelError("this capture adapter is limited to the current Python fleet")
    if window_seconds < 300:
        raise ModelError("measurement window must be at least 300 seconds")
    if end_at.tzinfo is None:
        raise ModelError("measurement end time must be timezone-aware")
    end_at = end_at.astimezone(UTC)
    start_at = end_at - timedelta(seconds=window_seconds)
    range_selector = f"{window_seconds}s"

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for target in targets.get("targets", []):
        role = target.get("role")
        instance = target.get("instance")
        if not isinstance(role, str) or not role or not isinstance(instance, str) or not instance:
            raise ModelError("capture target role and instance must be non-empty strings")
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", instance) is None:
            raise ModelError(
                f"capture target instance {instance!r} contains unsupported characters"
            )
        grouped[role].append(target)

    role_measurements: list[dict[str, Any]] = []
    releases: set[str] = set()
    for role, role_targets in sorted(grouped.items()):
        lane_totals: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"successful_cycles": 0.0, "attempted_cycles": 0.0, "task_active_seconds": 0.0}
        )
        role_cpu = 0.0
        role_peak_rss = 0.0
        role_retries = 0.0
        role_target_ids: list[str] = []
        vcpu_limits: set[float | None] = set()
        memory_limits: set[int | None] = set()

        for target in role_targets:
            instance = str(target["instance"])
            role_target_ids.append(str(target.get("id", instance)))
            label = _prom_label(instance)
            selector = f'job="crawler",instance="{label}"'
            role_cpu += _instant_sum(
                query,
                f"sum(increase(process_cpu_seconds_total{{{selector}}}[{range_selector}]))",
                end_at,
                f"{instance} process CPU",
            )
            role_peak_rss = max(
                role_peak_rss,
                _instant_sum(
                    query,
                    f"max(max_over_time(process_resident_memory_bytes{{{selector}}}[{range_selector}]))",
                    end_at,
                    f"{instance} peak RSS",
                ),
            )
            role_retries += _instant_sum(
                query,
                f'sum(increase(crawler_http_retry_attempts_total{{{selector},outcome="retry"}}[{range_selector}]))',
                end_at,
                f"{instance} HTTP retries",
            )
            if target.get("execution_class") == "browser":
                for metric in (
                    "crawler_browser_navigation_network_retry_total",
                    "crawler_browser_content_retry_total",
                    "crawler_browser_target_closed_retries_total",
                ):
                    role_retries += _instant_sum(
                        query,
                        f'sum(increase({metric}{{{selector},outcome="retry"}}[{range_selector}]))',
                        end_at,
                        f"{instance} {metric}",
                    )

            for row in query(f"max by (version) (crawler_build_info{{{selector}}})", end_at):
                version = row.get("metric", {}).get("version")
                if isinstance(version, str) and version:
                    releases.add(version)

            vcpu_limits.add(
                float(target["vcpu_limit"]) if target.get("vcpu_limit") is not None else None
            )
            memory_limits.add(
                int(target["memory_limit_bytes"])
                if target.get("memory_limit_bytes") is not None
                else None
            )
            execution_class = target.get("execution_class")
            if execution_class == "support":
                continue
            if execution_class not in {"http", "browser"}:
                raise ModelError(f"unsupported execution class {execution_class!r}")

            concurrency = target.get("max_concurrency", {})
            for stage, metric_kind, duration_metric in (
                ("monitor", "monitor", "crawler_monitor_duration_seconds_sum"),
                ("detail", "scrape", "crawler_scrape_duration_seconds_sum"),
            ):
                max_concurrency = concurrency.get(stage)
                if not isinstance(max_concurrency, int) or max_concurrency <= 0:
                    raise ModelError(f"{instance} needs positive max_concurrency.{stage}")
                key = (stage, str(execution_class))
                totals = lane_totals[key]
                totals["max_concurrency_per_instance"] = float(max_concurrency)
                totals["successful_cycles"] += _instant_sum(
                    query,
                    (
                        "sum(increase(crawler_tasks_total"
                        f'{{{selector},kind="{metric_kind}",status="succeeded"}}'
                        f"[{range_selector}]))"
                    ),
                    end_at,
                    f"{instance} {stage} successes",
                )
                totals["attempted_cycles"] += _instant_sum(
                    query,
                    (
                        "sum(increase(crawler_tasks_total"
                        f'{{{selector},kind="{metric_kind}",status=~"succeeded|failed|gone"}}'
                        f"[{range_selector}]))"
                    ),
                    end_at,
                    f"{instance} {stage} attempts",
                )
                totals["task_active_seconds"] += _instant_sum(
                    query,
                    f"sum(increase({duration_metric}{{{selector}}}[{range_selector}]))",
                    end_at,
                    f"{instance} {stage} active seconds",
                )

        if len(vcpu_limits) != 1 or len(memory_limits) != 1:
            raise ModelError(f"targets in role {role} must use identical resource limits")
        role_measurements.append(
            {
                "role": role,
                "execution_class": str(role_targets[0].get("execution_class")),
                "instance_count": len(role_targets),
                "target_ids": sorted(role_target_ids),
                "vcpu_limit_per_instance": next(iter(vcpu_limits)),
                "memory_limit_bytes_per_instance": next(iter(memory_limits)),
                "process_cpu_seconds": role_cpu,
                "peak_rss_bytes_per_instance": role_peak_rss,
                "retry_events": role_retries,
                "lanes": [
                    {
                        "stage": key[0],
                        "execution_class": key[1],
                        **totals,
                        # Current metrics do not yet count all origin attempts
                        # or response bytes. Null is deliberate and blocks a
                        # final ROI claim rather than pretending zero traffic.
                        "origin_attempts": None,
                        "response_bytes": None,
                    }
                    for key, totals in sorted(lane_totals.items())
                ],
            }
        )

    measurement_id = f"python-production-{end_at:%Y%m%d}-{window_seconds}s"
    return {
        "schema_version": MEASUREMENT_SCHEMA,
        "measurement_id": measurement_id,
        "workload_revision": targets.get("workload_revision"),
        "implementation": targets.get("implementation"),
        "source_revision": source_revision,
        "source_releases": sorted(releases),
        "window": {
            "start_at": start_at.isoformat().replace("+00:00", "Z"),
            "end_at": end_at.isoformat().replace("+00:00", "Z"),
            "seconds": window_seconds,
        },
        "capture": {
            "source": "prometheus-read-api",
            "read_only": True,
            "origin_requests_made": 0,
            "targets_revision": targets.get("revision"),
        },
        "roles": role_measurements,
        "evidence_gaps": [
            "browser-child-cpu-and-rss-not-in-process-metrics",
            "origin-attempts-not-in-current-metrics",
            "response-bytes-not-in-current-metrics",
            "proxy-attribution-not-in-current-metrics",
            "queue-and-redis-resource-use-requires-separate-capture",
        ],
    }
