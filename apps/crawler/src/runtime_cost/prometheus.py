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


def _optional_instant_sum(
    query: Query, expression: str, at: datetime, query_name: str
) -> float | None:
    rows = query(expression, at)
    if not rows:
        return None
    return _sum_vector(rows, query_name)


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
        role_root_cpu = 0.0
        role_root_peak_rss = 0.0
        role_descendant_cpu = 0.0
        role_process_tree_peak_rss = 0.0
        role_process_tree_samples = 0.0
        role_process_tree_complete = True
        role_retries = 0.0
        role_target_ids: list[str] = []
        cost_categories: set[str] = set()
        discovery_concurrencies: set[float | None] = set()
        monitor_concurrencies: set[float | None] = set()
        vcpu_limits: set[float | None] = set()
        memory_limits: set[int | None] = set()

        for target in role_targets:
            instance = str(target["instance"])
            role_target_ids.append(str(target.get("id", instance)))
            label = _prom_label(instance)
            selector = f'job="crawler",instance="{label}"'
            root_cpu = _instant_sum(
                query,
                f"sum(increase(process_cpu_seconds_total{{{selector}}}[{range_selector}]))",
                end_at,
                f"{instance} process CPU",
            )
            root_peak_rss = _instant_sum(
                query,
                f"max(max_over_time(process_resident_memory_bytes{{{selector}}}[{range_selector}]))",
                end_at,
                f"{instance} peak RSS",
            )
            role_root_cpu += root_cpu
            role_root_peak_rss = max(role_root_peak_rss, root_peak_rss)

            process_tree_samples = _optional_instant_sum(
                query,
                (
                    "sum(increase(crawler_runtime_process_tree_samples_total"
                    f'{{{selector},outcome="success"}}[{range_selector}]))'
                ),
                end_at,
                f"{instance} process-tree samples",
            )
            descendant_cpu = _optional_instant_sum(
                query,
                (
                    "sum(increase(crawler_runtime_descendant_cpu_seconds_total"
                    f"{{{selector}}}[{range_selector}]))"
                ),
                end_at,
                f"{instance} descendant CPU",
            )
            process_tree_peak_rss = _optional_instant_sum(
                query,
                (
                    "max(max_over_time("
                    "crawler_runtime_process_tree_peak_resident_memory_bytes"
                    f"{{{selector}}}[{range_selector}]))"
                ),
                end_at,
                f"{instance} process-tree peak RSS",
            )
            if (
                process_tree_samples is None
                or process_tree_samples <= 0
                or descendant_cpu is None
                or process_tree_peak_rss is None
                or process_tree_peak_rss <= 0
            ):
                target_tree_complete = False
            else:
                target_tree_complete = True
                role_descendant_cpu += descendant_cpu
                role_process_tree_peak_rss = max(role_process_tree_peak_rss, process_tree_peak_rss)
                role_process_tree_samples += process_tree_samples
            role_process_tree_complete = role_process_tree_complete and target_tree_complete
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
            cost_category = target.get("cost_category")
            if not isinstance(cost_category, str) or not cost_category:
                raise ModelError(f"{instance} needs a cost category")
            cost_categories.add(cost_category)
            discovery = target.get("discovery_concurrency")
            monitor = target.get("monitor_concurrency")
            if execution_class == "support":
                if discovery is not None or monitor is not None:
                    raise ModelError(f"{instance} support concurrency must be null")
                discovery_concurrencies.add(None)
                monitor_concurrencies.add(None)
                continue
            if execution_class not in {"http", "browser"}:
                raise ModelError(f"unsupported execution class {execution_class!r}")
            if not isinstance(discovery, int) or discovery <= 0:
                raise ModelError(f"{instance} needs positive discovery_concurrency")
            if not isinstance(monitor, int) or monitor <= 0 or monitor > discovery:
                raise ModelError(
                    f"{instance} needs monitor_concurrency within discovery_concurrency"
                )
            discovery_concurrencies.add(float(discovery))
            monitor_concurrencies.add(float(monitor))

            for stage, metric_kind, duration_metric in (
                ("monitor", "monitor", "crawler_monitor_duration_seconds_sum"),
                ("detail", "scrape", "crawler_scrape_duration_seconds_sum"),
            ):
                key = (stage, str(execution_class))
                totals = lane_totals[key]
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

        if (
            len(cost_categories) != 1
            or len(discovery_concurrencies) != 1
            or len(monitor_concurrencies) != 1
            or len(vcpu_limits) != 1
            or len(memory_limits) != 1
        ):
            raise ModelError(f"targets in role {role} must use identical limits and category")
        if role_process_tree_complete:
            role_cpu = role_root_cpu + role_descendant_cpu
            role_peak_rss = role_process_tree_peak_rss
            resource_scope = "process-tree"
        else:
            # Mixing parent-only and process-tree totals within one role would
            # create an irreproducible average. Fail closed to the consistently
            # available parent-process scope until every target has coverage.
            role_cpu = role_root_cpu
            role_peak_rss = role_root_peak_rss
            role_descendant_cpu = 0.0
            role_process_tree_peak_rss = 0.0
            role_process_tree_samples = 0.0
            resource_scope = "root-process"
        role_measurements.append(
            {
                "role": role,
                "execution_class": str(role_targets[0].get("execution_class")),
                "cost_category": next(iter(cost_categories)),
                "instance_count": len(role_targets),
                "target_ids": sorted(role_target_ids),
                "discovery_concurrency_per_instance": next(iter(discovery_concurrencies)),
                "monitor_concurrency_per_instance": next(iter(monitor_concurrencies)),
                "vcpu_limit_per_instance": next(iter(vcpu_limits)),
                "memory_limit_bytes_per_instance": next(iter(memory_limits)),
                "resource_scope": resource_scope,
                "root_process_cpu_seconds": role_root_cpu,
                "descendant_process_cpu_seconds": (
                    role_descendant_cpu if role_process_tree_complete else None
                ),
                "process_cpu_seconds": role_cpu,
                "root_peak_rss_bytes_per_instance": role_root_peak_rss,
                "process_tree_peak_rss_bytes_per_instance": (
                    role_process_tree_peak_rss if role_process_tree_complete else None
                ),
                "peak_rss_bytes_per_instance": role_peak_rss,
                "process_tree_successful_samples": (
                    role_process_tree_samples if role_process_tree_complete else None
                ),
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

    browser_tree_complete = any(
        role["execution_class"] == "browser" and role["resource_scope"] == "process-tree"
        for role in role_measurements
    ) and all(
        role["resource_scope"] == "process-tree"
        for role in role_measurements
        if role["execution_class"] == "browser"
    )
    evidence_gaps = [
        "origin-attempts-not-in-current-metrics",
        "response-bytes-not-in-current-metrics",
        "proxy-attribution-not-in-current-metrics",
        "queue-and-redis-resource-use-requires-separate-capture",
    ]
    if not browser_tree_complete:
        evidence_gaps.insert(0, "browser-child-cpu-and-rss-not-in-process-metrics")

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
        "evidence_gaps": evidence_gaps,
    }
