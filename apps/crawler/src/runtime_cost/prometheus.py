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
from itertools import product
from typing import Any

from src.metrics import BROWSER_RETRY_CAPTURE_CONTRACT
from src.runtime_cost.model import (
    MEASUREMENT_SCHEMA,
    PROCESS_TREE_BOUNDARY_TOLERANCE_SECONDS,
    PROCESS_TREE_MAX_SAMPLE_INTERVAL_SECONDS,
    PROCESS_TREE_MIN_COVERAGE_RATIO,
    ModelError,
)
from src.runtime_cost.process_tree import SAMPLER_STRICT_TIMING_LIMIT_SECONDS

TARGET_SCHEMA = "jobseek.crawler-runtime-capture-targets/v1"
EXACT_24H_TARGET_INSTANCES = frozenset(
    {"worker-1", "worker-2", "worker-3", "browser-1", "exporter", "drain"}
)
_PROCESS_TREE_OBSERVATION_COMPONENTS = (
    "root_cpu",
    "tree_cpu",
    "root_rss",
    "tree_rss",
    "descendants",
)
_SAMPLER_TIMING_PHASES = ("wake_lateness", "collection", "handoff")
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


def _strict_counter_sum(query: Query, expression: str, at: datetime, query_name: str) -> int | None:
    """Return a complete integer counter sum without normalizing bad evidence."""

    rows = query(expression, at)
    if len(rows) != 1:
        return None
    row = rows[0]
    if row.get("metric") != {}:
        return None
    try:
        raw_value = float(row["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return _nonnegative_integer(raw_value)


def _nonnegative_integer(value: float | None) -> int | None:
    if value is None or value < 0 or not value.is_integer():
        return None
    return int(value)


def _counter_delta(start: float | None, end: float | None) -> int | None:
    start_count = _nonnegative_integer(start)
    end_count = _nonnegative_integer(end)
    if start_count is None or end_count is None or end_count < start_count:
        return None
    return end_count - start_count


def _strict_scalar(
    query: Query,
    expression: str,
    at: datetime,
    query_name: str,
    *,
    integer: bool = False,
) -> float | int | None:
    """Read one unlabelled finite scalar without hiding duplicate evidence."""

    rows = query(expression, at)
    if len(rows) != 1 or rows[0].get("metric") != {}:
        return None
    try:
        value = float(rows[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    if not integer:
        return value
    return _nonnegative_integer(value)


def _strict_counter_boundary(
    query: Query,
    metric: str,
    *,
    selector: str,
    at: datetime,
    query_name: str,
) -> int | None:
    count = _strict_scalar(
        query,
        f"count({metric}{{{selector}}})",
        at,
        f"{query_name} series count",
        integer=True,
    )
    if count != 1:
        return None
    value = _strict_scalar(
        query,
        f"sum({metric}{{{selector}}})",
        at,
        query_name,
        integer=True,
    )
    return value if isinstance(value, int) else None


def _strict_numeric_boundary(
    query: Query,
    metric: str,
    *,
    selector: str,
    at: datetime,
    query_name: str,
) -> float | None:
    count = _strict_scalar(
        query,
        f"count({metric}{{{selector}}})",
        at,
        f"{query_name} series count",
        integer=True,
    )
    if count != 1:
        return None
    value = _strict_scalar(
        query,
        f"sum({metric}{{{selector}}})",
        at,
        query_name,
    )
    return float(value) if value is not None else None


def _capture_egress_target_stage(
    query: Query,
    *,
    selector: str,
    target_id: str,
    stage: str,
    execution_class: str,
    start_at: datetime,
    end_at: datetime,
    range_selector: str,
) -> list[dict[str, Any]]:
    """Capture route-specific shared-HTTP counters with exact conservation."""

    entries: list[dict[str, Any]] = []
    for egress in ("direct", "proxy"):
        labels = f'{selector},stage="{stage}",execution_class="{execution_class}",egress="{egress}"'

        def boundary(
            metric: str,
            at: datetime,
            name: str,
            extra: str = "",
            *,
            _labels: str = labels,
            _egress: str = egress,
        ) -> float | None:
            return _strict_counter_sum(
                query,
                f"sum({metric}{{{_labels}{extra}}})",
                at,
                f"{target_id} {stage} {_egress} {name}",
            )

        attempts = _counter_delta(
            boundary("crawler_runtime_origin_attempts_total", start_at, "start origin attempts"),
            boundary("crawler_runtime_origin_attempts_total", end_at, "end origin attempts"),
        )
        responses = _counter_delta(
            boundary(
                "crawler_runtime_origin_outcomes_total",
                start_at,
                "start response outcomes",
                ',outcome="response"',
            ),
            boundary(
                "crawler_runtime_origin_outcomes_total",
                end_at,
                "end response outcomes",
                ',outcome="response"',
            ),
        )
        transport_errors = _counter_delta(
            boundary(
                "crawler_runtime_origin_outcomes_total",
                start_at,
                "start transport-error outcomes",
                ',outcome="transport_error"',
            ),
            boundary(
                "crawler_runtime_origin_outcomes_total",
                end_at,
                "end transport-error outcomes",
                ',outcome="transport_error"',
            ),
        )
        response_bytes = _counter_delta(
            boundary(
                "crawler_runtime_response_body_bytes_total",
                start_at,
                "start response bytes",
            ),
            boundary("crawler_runtime_response_body_bytes_total", end_at, "end response bytes"),
        )

        reset_counts = [
            _strict_counter_sum(
                query,
                f"sum(resets({metric}{{{labels}}}[{range_selector}]))",
                end_at,
                f"{target_id} {stage} {egress} {name} resets",
            )
            for metric, name in (
                ("crawler_runtime_origin_attempts_total", "origin-attempt"),
                ("crawler_runtime_origin_outcomes_total", "origin-outcome"),
                ("crawler_runtime_response_body_bytes_total", "response-byte"),
            )
        ]
        present_reset_counts = [value for value in reset_counts if value is not None]
        counter_resets = (
            sum(present_reset_counts) if len(present_reset_counts) == len(reset_counts) else None
        )
        complete = (
            attempts is not None
            and responses is not None
            and transport_errors is not None
            and response_bytes is not None
            and counter_resets == 0
            and attempts == responses + transport_errors
        )
        entries.append(
            {
                "stage": stage,
                "execution_class": execution_class,
                "egress": egress,
                "complete": complete,
                "counter_resets": counter_resets,
                "origin_attempts": attempts if complete else None,
                "responses": responses if complete else None,
                "transport_errors": transport_errors if complete else None,
                "response_bytes": response_bytes if complete else None,
            }
        )
    return entries


_CAPABILITY_RE = re.compile(r"(?:[a-z0-9][a-z0-9_-]{0,63}|_unknown)\Z")


def _counter_vector(
    query: Query,
    expression: str,
    at: datetime,
    query_name: str,
    label_names: tuple[str, ...],
) -> dict[tuple[str, ...], int] | None:
    rows = query(expression, at)
    if not rows:
        return None
    values: dict[tuple[str, ...], int] = {}
    for row in rows:
        metric = row.get("metric")
        if not isinstance(metric, dict):
            return None
        if set(metric) != set(label_names):
            return None
        try:
            label_values = tuple(metric[name] for name in label_names)
            raw_value = float(row["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if any(not isinstance(value, str) or not value for value in label_values):
            return None
        key = tuple(label_values)
        value = _nonnegative_integer(raw_value)
        if value is None or key in values:
            return None
        values[key] = value
    return values


def _capture_capability_target_stage(
    query: Query,
    *,
    selector: str,
    target_id: str,
    metric_stage: str,
    output_stage: str,
    start_at: datetime,
    end_at: datetime,
    range_selector: str,
) -> dict[str, Any]:
    """Capture capability mix only when it reconciles to runtime executions."""

    capability_labels = f'{selector},stage="{metric_stage}",implementation="python"'
    capability_expression = (
        "sum by (capability,outcome) "
        f"(crawler_runtime_capability_executions_total{{{capability_labels}}})"
    )
    capability_resets_expression = (
        "sum by (capability,outcome) "
        f"(resets(crawler_runtime_capability_executions_total"
        f"{{{capability_labels}}}[{range_selector}]))"
    )
    runtime_labels = f'{selector},stage="{metric_stage}",implementation="python"'
    runtime_expression = f"sum by (outcome) (crawler_runtime_executions_total{{{runtime_labels}}})"
    runtime_resets_expression = (
        "sum by (outcome) "
        f"(resets(crawler_runtime_executions_total{{{runtime_labels}}}[{range_selector}]))"
    )

    capability_start = _counter_vector(
        query,
        capability_expression,
        start_at,
        f"{target_id} {output_stage} capability start",
        ("capability", "outcome"),
    )
    capability_end = _counter_vector(
        query,
        capability_expression,
        end_at,
        f"{target_id} {output_stage} capability end",
        ("capability", "outcome"),
    )
    capability_resets = _counter_vector(
        query,
        capability_resets_expression,
        end_at,
        f"{target_id} {output_stage} capability resets",
        ("capability", "outcome"),
    )
    runtime_start = _counter_vector(
        query,
        runtime_expression,
        start_at,
        f"{target_id} {output_stage} runtime start",
        ("outcome",),
    )
    runtime_end = _counter_vector(
        query,
        runtime_expression,
        end_at,
        f"{target_id} {output_stage} runtime end",
        ("outcome",),
    )
    runtime_resets = _counter_vector(
        query,
        runtime_resets_expression,
        end_at,
        f"{target_id} {output_stage} runtime resets",
        ("outcome",),
    )

    mappings = (
        capability_start,
        capability_end,
        capability_resets,
        runtime_start,
        runtime_end,
        runtime_resets,
    )
    if any(mapping is None for mapping in mappings):
        return {"stage": output_stage, "complete": False, "executions": []}
    assert capability_start is not None
    assert capability_end is not None
    assert capability_resets is not None
    assert runtime_start is not None
    assert runtime_end is not None
    assert runtime_resets is not None
    if (
        capability_start.keys() != capability_end.keys()
        or capability_start.keys() != capability_resets.keys()
        or runtime_start.keys() != runtime_end.keys()
        or runtime_start.keys() != runtime_resets.keys()
        or any(value != 0 for value in capability_resets.values())
        or any(value != 0 for value in runtime_resets.values())
        or any(_CAPABILITY_RE.fullmatch(key[0]) is None for key in capability_start)
    ):
        return {"stage": output_stage, "complete": False, "executions": []}

    allowed_outcomes = {
        "monitor": {"success", "cancelled", "error", "incomplete"},
        "scrape": {"success", "cancelled", "error"},
    }[metric_stage]
    if any(key[1] not in allowed_outcomes for key in capability_start) or any(
        key[0] not in allowed_outcomes for key in runtime_start
    ):
        return {"stage": output_stage, "complete": False, "executions": []}

    capability_deltas: dict[tuple[str, str], int] = {}
    by_outcome: dict[str, int] = defaultdict(int)
    for key, start_value in capability_start.items():
        if len(key) != 2:
            return {"stage": output_stage, "complete": False, "executions": []}
        end_value = capability_end[key]
        if end_value < start_value:
            return {"stage": output_stage, "complete": False, "executions": []}
        delta = end_value - start_value
        capability_key = (key[0], key[1])
        capability_deltas[capability_key] = delta
        by_outcome[capability_key[1]] += delta

    runtime_deltas: dict[str, int] = {}
    for key, start_value in runtime_start.items():
        if len(key) != 1:
            return {"stage": output_stage, "complete": False, "executions": []}
        end_value = runtime_end[key]
        if end_value < start_value:
            return {"stage": output_stage, "complete": False, "executions": []}
        runtime_deltas[key[0]] = end_value - start_value
    if {key: value for key, value in by_outcome.items() if value} != {
        key: value for key, value in runtime_deltas.items() if value
    }:
        return {"stage": output_stage, "complete": False, "executions": []}

    return {
        "stage": output_stage,
        "complete": True,
        "executions": [
            {
                "stage": output_stage,
                "capability": capability,
                "outcome": outcome,
                "executions": delta,
            }
            for (capability, outcome), delta in sorted(capability_deltas.items())
            if delta
        ],
    }


def _boundary_covered(last_sample: float | None, boundary: datetime) -> bool:
    if last_sample is None:
        return False
    age_seconds = boundary.timestamp() - last_sample
    return 0 <= age_seconds <= PROCESS_TREE_BOUNDARY_TOLERANCE_SECONDS


def _strict_labelled_source_counter_vector(
    rows: list[dict[str, Any]],
    *,
    label_names: tuple[str, ...],
    expected_keys: set[tuple[str, ...]],
) -> (
    tuple[
        dict[tuple[str, ...], int],
        dict[tuple[str, ...], tuple[tuple[str, str], ...]],
    ]
    | None
):
    """Parse exactly one identity-preserved source series per bounded child."""

    values: dict[tuple[str, ...], int] = {}
    source_identities: dict[tuple[str, ...], tuple[tuple[str, str], ...]] = {}
    for row in rows:
        metric = row.get("metric")
        if (
            not isinstance(metric, dict)
            or not set(label_names) <= set(metric)
            or not all(
                isinstance(name, str) and isinstance(value, str) for name, value in metric.items()
            )
        ):
            return None
        raw_key = tuple(metric.get(name) for name in label_names)
        if not all(isinstance(value, str) for value in raw_key):
            return None
        key = tuple(str(value) for value in raw_key)
        if key in values:
            return None
        try:
            raw_value = float(row["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        value = _nonnegative_integer(raw_value)
        if value is None:
            return None
        values[key] = value
        source_identities[key] = tuple(
            sorted((name, value) for name, value in metric.items() if name != "__name__")
        )
    if set(values) != expected_keys:
        return None
    return values, source_identities


def _capture_browser_retry_target(
    query: Query,
    *,
    selector: str,
    target_id: str,
    start_at: datetime,
    end_at: datetime,
    range_selector: str,
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Capture every pre-seeded bounded browser retry child exactly."""

    coverage: list[dict[str, Any]] = []
    target_complete = True
    target_retry_events = 0
    for contract in BROWSER_RETRY_CAPTURE_CONTRACT:
        metric = str(contract["metric"])
        family = str(contract["family"])
        stage = str(contract["stage"])
        label_contract = tuple(contract["labels"])
        label_names = tuple(str(item[0]) for item in label_contract)
        expected_keys = {
            tuple(str(value) for value in values)
            for values in product(*(tuple(item[1]) for item in label_contract))
        }
        boundary_expression = f"{metric}{{{selector}}}"
        start_boundary = _strict_labelled_source_counter_vector(
            query(boundary_expression, start_at),
            label_names=label_names,
            expected_keys=expected_keys,
        )
        end_boundary = _strict_labelled_source_counter_vector(
            query(boundary_expression, end_at),
            label_names=label_names,
            expected_keys=expected_keys,
        )
        reset_boundary = _strict_labelled_source_counter_vector(
            query(
                f"resets({metric}{{{selector}}}[{range_selector}])",
                end_at,
            ),
            label_names=label_names,
            expected_keys=expected_keys,
        )

        events: list[dict[str, Any]] = []
        retry_events: int | None = 0
        counter_resets: int | None = None
        complete = (
            start_boundary is not None and end_boundary is not None and reset_boundary is not None
        )
        if complete:
            assert start_boundary is not None
            assert end_boundary is not None
            assert reset_boundary is not None
            start_values, start_sources = start_boundary
            end_values, end_sources = end_boundary
            reset_values, reset_sources = reset_boundary
            complete = start_sources == end_sources == reset_sources
            counter_resets = sum(reset_values.values())
            complete = complete and counter_resets == 0
            for key in sorted(expected_keys):
                start_value = start_values[key]
                end_value = end_values[key]
                if end_value < start_value:
                    complete = False
                    break
                delta = end_value - start_value
                event: dict[str, Any] = {
                    name: value for name, value in zip(label_names, key, strict=True)
                }
                event["events"] = delta
                events.append(event)
                if event.get("outcome") == "retry":
                    assert retry_events is not None
                    retry_events += delta
        if not complete:
            retry_events = None
            events = []
        coverage.append(
            {
                "target_id": target_id,
                "family": family,
                "stage": stage,
                "execution_class": "browser",
                "required_children": len(expected_keys),
                "observed_children": (
                    len(start_boundary[0])
                    if start_boundary is not None
                    and end_boundary is not None
                    and reset_boundary is not None
                    else 0
                ),
                "counter_resets": counter_resets,
                "retry_events": retry_events,
                "events": events,
                "complete": complete,
            }
        )
        target_complete = target_complete and complete
        if retry_events is not None:
            target_retry_events += retry_events

    return coverage, target_complete, target_retry_events if target_complete else None


def _capture_strict_sampler_timing(
    query: Query,
    *,
    selector: str,
    start_at: datetime,
    end_at: datetime,
    range_selector: str,
) -> tuple[dict[str, Any], bool, int | None]:
    """Retain exact reset-free boundaries for every fixed sampler phase."""

    metric = "crawler_runtime_process_tree_sampler_timing_limit_violations_total"
    expected_keys = {(phase,) for phase in _SAMPLER_TIMING_PHASES}
    expression = f"{metric}{{{selector}}}"
    start_boundary = _strict_labelled_source_counter_vector(
        query(expression, start_at),
        label_names=("phase",),
        expected_keys=expected_keys,
    )
    end_boundary = _strict_labelled_source_counter_vector(
        query(expression, end_at),
        label_names=("phase",),
        expected_keys=expected_keys,
    )
    reset_boundary = _strict_labelled_source_counter_vector(
        query(f"resets({expression}[{range_selector}])", end_at),
        label_names=("phase",),
        expected_keys=expected_keys,
    )

    boundaries_complete = (
        start_boundary is not None and end_boundary is not None and reset_boundary is not None
    )
    source_identity_complete = False
    reset_total: int | None = None
    if boundaries_complete:
        assert start_boundary is not None
        assert end_boundary is not None
        assert reset_boundary is not None
        start_values, start_sources = start_boundary
        end_values, end_sources = end_boundary
        reset_values, reset_sources = reset_boundary
        source_identity_complete = start_sources == end_sources == reset_sources
        reset_total = sum(reset_values.values())
    else:
        start_values = {}
        end_values = {}
        reset_values = {}

    phases: list[dict[str, Any]] = []
    complete = boundaries_complete and source_identity_complete
    for phase in _SAMPLER_TIMING_PHASES:
        key = (phase,)
        start_value = start_values.get(key)
        end_value = end_values.get(key)
        reset_value = reset_values.get(key)
        violations = (
            end_value - start_value
            if start_value is not None and end_value is not None and end_value >= start_value
            else None
        )
        phase_complete = violations == 0 and reset_value == 0
        complete = complete and phase_complete
        phases.append(
            {
                "phase": phase,
                "start": start_value,
                "end": end_value,
                "violations": violations,
                "resets": reset_value,
            }
        )

    return (
        {
            "limit_seconds": SAMPLER_STRICT_TIMING_LIMIT_SECONDS,
            "phases": phases,
            "complete": complete,
        },
        complete,
        reset_total,
    )


def _capture_process_tree_target(
    query: Query,
    *,
    selector: str,
    target_id: str,
    start_at: datetime,
    end_at: datetime,
    window_seconds: int,
    range_selector: str,
) -> tuple[
    dict[str, Any],
    bool,
    float | None,
    float | None,
    float | None,
    float | None,
]:
    """Return strict same-generation root/tree evidence for one target."""

    interval_start = _strict_numeric_boundary(
        query,
        "crawler_runtime_process_tree_sample_interval_seconds",
        selector=selector,
        at=start_at,
        query_name=f"{target_id} process-tree start interval",
    )
    interval_end = _strict_numeric_boundary(
        query,
        "crawler_runtime_process_tree_sample_interval_seconds",
        selector=selector,
        at=end_at,
        query_name=f"{target_id} process-tree end interval",
    )
    interval_seconds: float | None = None
    if (
        interval_start is not None
        and interval_end is not None
        and 0 < interval_start <= PROCESS_TREE_MAX_SAMPLE_INTERVAL_SECONDS
        and math.isclose(interval_start, interval_end, rel_tol=0, abs_tol=1e-12)
    ):
        interval_seconds = interval_end
    expected_samples = (
        math.floor(window_seconds / interval_seconds) if interval_seconds is not None else None
    )

    success_start = _strict_counter_boundary(
        query,
        "crawler_runtime_process_tree_samples_total",
        selector=f'{selector},outcome="success"',
        at=start_at,
        query_name=f"{target_id} process-tree start successes",
    )
    success_end = _strict_counter_boundary(
        query,
        "crawler_runtime_process_tree_samples_total",
        selector=f'{selector},outcome="success"',
        at=end_at,
        query_name=f"{target_id} process-tree end successes",
    )
    failure_start = _strict_counter_boundary(
        query,
        "crawler_runtime_process_tree_samples_total",
        selector=f'{selector},outcome="failure"',
        at=start_at,
        query_name=f"{target_id} process-tree start failures",
    )
    failure_end = _strict_counter_boundary(
        query,
        "crawler_runtime_process_tree_samples_total",
        selector=f'{selector},outcome="failure"',
        at=end_at,
        query_name=f"{target_id} process-tree end failures",
    )
    gap_start = _strict_counter_boundary(
        query,
        "crawler_runtime_process_tree_sampling_gaps_total",
        selector=selector,
        at=start_at,
        query_name=f"{target_id} process-tree start gaps",
    )
    gap_end = _strict_counter_boundary(
        query,
        "crawler_runtime_process_tree_sampling_gaps_total",
        selector=selector,
        at=end_at,
        query_name=f"{target_id} process-tree end gaps",
    )
    successful_samples = _counter_delta(success_start, success_end)
    failed_samples = _counter_delta(failure_start, failure_end)
    gap_samples = _counter_delta(gap_start, gap_end)
    sampler_starts_start = _strict_counter_boundary(
        query,
        "crawler_runtime_process_tree_sampler_starts_total",
        selector=selector,
        at=start_at,
        query_name=f"{target_id} process-tree start sampler starts",
    )
    sampler_starts_end = _strict_counter_boundary(
        query,
        "crawler_runtime_process_tree_sampler_starts_total",
        selector=selector,
        at=end_at,
        query_name=f"{target_id} process-tree end sampler starts",
    )
    sampler_restarts = _counter_delta(sampler_starts_start, sampler_starts_end)

    strict_timing, strict_timing_complete, strict_timing_resets = _capture_strict_sampler_timing(
        query,
        selector=selector,
        start_at=start_at,
        end_at=end_at,
        range_selector=range_selector,
    )

    reset_expressions = (
        f'crawler_runtime_process_tree_samples_total{{{selector},outcome="success"}}',
        f'crawler_runtime_process_tree_samples_total{{{selector},outcome="failure"}}',
        f"crawler_runtime_process_tree_sampling_gaps_total{{{selector}}}",
        f"crawler_runtime_process_tree_sampler_starts_total{{{selector}}}",
        f"crawler_runtime_process_root_cpu_seconds_total{{{selector}}}",
        f"crawler_runtime_process_tree_cpu_seconds_total{{{selector}}}",
        f"crawler_runtime_process_tree_observation_sequence{{{selector}}}",
    )
    reset_counts = [
        _strict_scalar(
            query,
            f"sum(resets({expression}[{range_selector}]))",
            end_at,
            f"{target_id} process-tree reset evidence",
            integer=True,
        )
        for expression in reset_expressions
    ]
    counter_resets = None
    if all(isinstance(value, int) for value in reset_counts) and isinstance(
        strict_timing_resets, int
    ):
        counter_resets = (
            sum(value for value in reset_counts if isinstance(value, int)) + strict_timing_resets
        )

    component_names = _PROCESS_TREE_OBSERVATION_COMPONENTS
    component_keys = {(component,) for component in component_names}

    def component_vector(metric: str, at: datetime, *, integer: bool) -> dict[str, float] | None:
        rows = query(f"max by (component) ({metric}{{{selector}}})", at)
        values: dict[str, float] = {}
        for row in rows:
            labels = row.get("metric")
            if not isinstance(labels, dict) or set(labels) != {"component"}:
                return None
            component = labels.get("component")
            if not isinstance(component, str) or component in values:
                return None
            try:
                value = float(row["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                return None
            if not math.isfinite(value) or value < 0 or (integer and not value.is_integer()):
                return None
            values[component] = value
        if {(component,) for component in values} != component_keys:
            return None
        return values

    sequence_start_values = component_vector(
        "crawler_runtime_process_tree_observation_sequence", start_at, integer=True
    )
    sequence_end_values = component_vector(
        "crawler_runtime_process_tree_observation_sequence", end_at, integer=True
    )
    observed_start_values = component_vector(
        "crawler_runtime_process_tree_observation_unixtime_seconds", start_at, integer=False
    )
    observed_end_values = component_vector(
        "crawler_runtime_process_tree_observation_unixtime_seconds", end_at, integer=False
    )

    def one_value(values: dict[str, float] | None) -> float | None:
        if values is None or len(set(values.values())) != 1:
            return None
        return next(iter(values.values()))

    sequence_start_raw = one_value(sequence_start_values)
    sequence_end_raw = one_value(sequence_end_values)
    sequence_start = _nonnegative_integer(sequence_start_raw)
    sequence_end = _nonnegative_integer(sequence_end_raw)
    observed_start = one_value(observed_start_values)
    observed_end = one_value(observed_end_values)
    paired_start = (
        sequence_start is not None
        and sequence_start > 0
        and observed_start is not None
        and _boundary_covered(observed_start, start_at)
    )
    paired_end = (
        sequence_end is not None
        and sequence_end > 0
        and observed_end is not None
        and _boundary_covered(observed_end, end_at)
    )
    start_covered = paired_start
    end_covered = paired_end

    coverage_ratio: float | None = None
    missing_samples: int | None = None
    if expected_samples is not None and expected_samples > 0 and successful_samples is not None:
        coverage_ratio = min(1.0, successful_samples / expected_samples)
        missing_samples = max(
            0,
            expected_samples - successful_samples - (failed_samples or 0),
        )

    root_cpu_start = _strict_numeric_boundary(
        query,
        "crawler_runtime_process_root_cpu_seconds_total",
        selector=selector,
        at=start_at,
        query_name=f"{target_id} process-root start CPU",
    )
    root_cpu_end = _strict_numeric_boundary(
        query,
        "crawler_runtime_process_root_cpu_seconds_total",
        selector=selector,
        at=end_at,
        query_name=f"{target_id} process-root end CPU",
    )
    tree_cpu_start = _strict_numeric_boundary(
        query,
        "crawler_runtime_process_tree_cpu_seconds_total",
        selector=selector,
        at=start_at,
        query_name=f"{target_id} process-tree start CPU",
    )
    tree_cpu_end = _strict_numeric_boundary(
        query,
        "crawler_runtime_process_tree_cpu_seconds_total",
        selector=selector,
        at=end_at,
        query_name=f"{target_id} process-tree end CPU",
    )
    root_cpu_seconds = (
        root_cpu_end - root_cpu_start
        if root_cpu_start is not None
        and root_cpu_end is not None
        and root_cpu_end >= root_cpu_start
        else None
    )
    tree_cpu_seconds = (
        tree_cpu_end - tree_cpu_start
        if tree_cpu_start is not None
        and tree_cpu_end is not None
        and tree_cpu_end >= tree_cpu_start
        else None
    )
    root_peak_rss_bytes = _strict_scalar(
        query,
        (
            "max(max_over_time(crawler_runtime_process_root_resident_memory_bytes"
            f"{{{selector}}}[{range_selector}]))"
        ),
        end_at,
        f"{target_id} process-root window peak RSS",
    )
    tree_peak_rss_bytes = _strict_scalar(
        query,
        (
            "max(max_over_time(crawler_runtime_process_tree_resident_memory_bytes"
            f"{{{selector}}}[{range_selector}]))"
        ),
        end_at,
        f"{target_id} process-tree window peak RSS",
    )
    min_cpu_margin = _strict_scalar(
        query,
        (
            "min(min_over_time((crawler_runtime_process_tree_cpu_seconds_total"
            f"{{{selector}}} - crawler_runtime_process_root_cpu_seconds_total{{{selector}}})"
            f"[{range_selector}:]))"
        ),
        end_at,
        f"{target_id} process-tree CPU margin",
    )
    min_rss_margin = _strict_scalar(
        query,
        (
            "min(min_over_time((crawler_runtime_process_tree_resident_memory_bytes"
            f"{{{selector}}} - crawler_runtime_process_root_resident_memory_bytes{{{selector}}})"
            f"[{range_selector}:]))"
        ),
        end_at,
        f"{target_id} process-tree RSS margin",
    )

    coverage = {
        "target_id": target_id,
        "sample_interval_seconds": interval_seconds,
        "expected_samples": expected_samples,
        "successful_samples": successful_samples,
        "failed_samples": failed_samples,
        "missing_samples": missing_samples,
        "counter_resets": counter_resets,
        "sampler_restarts": sampler_restarts,
        "gap_samples": gap_samples,
        "coverage_ratio": coverage_ratio,
        "required_coverage_ratio": PROCESS_TREE_MIN_COVERAGE_RATIO,
        "boundary_tolerance_seconds": PROCESS_TREE_BOUNDARY_TOLERANCE_SECONDS,
        "start_covered": start_covered,
        "end_covered": end_covered,
        "start_observation_sequence": sequence_start,
        "end_observation_sequence": sequence_end,
        "paired_start": paired_start,
        "paired_end": paired_end,
        "strict_timing": strict_timing,
    }
    complete = (
        interval_seconds is not None
        and expected_samples is not None
        and expected_samples > 1
        and successful_samples is not None
        and failed_samples == 0
        and missing_samples is not None
        and counter_resets == 0
        and strict_timing_complete
        and sampler_starts_start == 1
        and sampler_starts_end == 1
        and sampler_restarts == 0
        and gap_samples == 0
        and coverage_ratio is not None
        and coverage_ratio >= PROCESS_TREE_MIN_COVERAGE_RATIO
        and start_covered
        and end_covered
        and paired_start
        and paired_end
        and sequence_start is not None
        and sequence_end is not None
        and sequence_end >= sequence_start
        and successful_samples == sequence_end - sequence_start
        and tree_cpu_seconds is not None
        and root_cpu_seconds is not None
        and tree_cpu_seconds >= root_cpu_seconds
        and tree_peak_rss_bytes is not None
        and root_peak_rss_bytes is not None
        and tree_peak_rss_bytes >= root_peak_rss_bytes
        and min_cpu_margin is not None
        and min_cpu_margin >= 0
        and min_rss_margin is not None
        and min_rss_margin >= 0
    )
    coverage["complete"] = complete
    return (
        coverage,
        complete,
        root_cpu_seconds,
        float(root_peak_rss_bytes) if root_peak_rss_bytes is not None else None,
        tree_cpu_seconds,
        tree_peak_rss_bytes,
    )


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
    target_instances: list[str] = []
    for target in targets.get("targets", []):
        role = target.get("role")
        instance = target.get("instance")
        if not isinstance(role, str) or not role or not isinstance(instance, str) or not instance:
            raise ModelError("capture target role and instance must be non-empty strings")
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", instance) is None:
            raise ModelError(
                f"capture target instance {instance!r} contains unsupported characters"
            )
        target_instances.append(instance)
        grouped[role].append(target)

    if window_seconds == 86_400 and (
        len(target_instances) != len(EXACT_24H_TARGET_INSTANCES)
        or set(target_instances) != EXACT_24H_TARGET_INSTANCES
    ):
        raise ModelError("86,400-second capture requires the exact six-target fleet")

    role_measurements: list[dict[str, Any]] = []
    releases: set[str] = set()
    capture_evidence_gaps: list[str] = []
    for role, role_targets in sorted(grouped.items()):
        lane_totals: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"successful_cycles": 0.0, "attempted_cycles": 0.0, "task_active_seconds": 0.0}
        )
        role_cpu = 0.0
        role_peak_rss = 0.0
        role_root_cpu = 0.0
        role_root_peak_rss = 0.0
        role_process_tree_cpu = 0.0
        role_descendant_cpu = 0.0
        role_process_tree_peak_rss = 0.0
        role_process_tree_samples = 0
        role_process_tree_coverage: list[dict[str, Any]] = []
        role_process_tree_complete = True
        role_retries = 0.0
        role_retry_complete = True
        role_retry_coverage: list[dict[str, Any]] = []
        role_target_ids: list[str] = []
        cost_categories: set[str] = set()
        discovery_concurrencies: set[float | None] = set()
        monitor_concurrencies: set[float | None] = set()
        vcpu_limits: set[float | None] = set()
        memory_limits: set[int | None] = set()
        execution_classes: set[str] = set()
        role_egress_targets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        role_capability_targets: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for target in role_targets:
            instance = str(target["instance"])
            role_target_ids.append(str(target.get("id", instance)))
            label = _prom_label(instance)
            selector = f'job="crawler",instance="{label}"'
            target_id = str(target.get("id", instance))
            (
                coverage,
                target_tree_complete,
                root_cpu,
                root_peak_rss,
                process_tree_cpu,
                process_tree_peak_rss,
            ) = _capture_process_tree_target(
                query,
                selector=selector,
                target_id=target_id,
                start_at=start_at,
                end_at=end_at,
                window_seconds=window_seconds,
                range_selector=range_selector,
            )
            role_root_cpu += root_cpu or 0.0
            role_root_peak_rss = max(role_root_peak_rss, root_peak_rss or 0.0)
            role_process_tree_coverage.append(coverage)
            if target_tree_complete:
                assert root_cpu is not None
                assert process_tree_cpu is not None
                assert process_tree_peak_rss is not None
                role_process_tree_cpu += process_tree_cpu
                role_descendant_cpu += process_tree_cpu - root_cpu
                role_process_tree_peak_rss = max(role_process_tree_peak_rss, process_tree_peak_rss)
                successful_samples = coverage["successful_samples"]
                assert isinstance(successful_samples, int)
                role_process_tree_samples += successful_samples
            role_process_tree_complete = role_process_tree_complete and target_tree_complete
            role_retries += _instant_sum(
                query,
                f'sum(increase(crawler_http_retry_attempts_total{{{selector},outcome="retry"}}[{range_selector}]))',
                end_at,
                f"{instance} HTTP retries",
            )
            if target.get("execution_class") == "browser":
                retry_coverage, retry_complete, browser_retry_events = (
                    _capture_browser_retry_target(
                        query,
                        selector=selector,
                        target_id=target_id,
                        start_at=start_at,
                        end_at=end_at,
                        range_selector=range_selector,
                    )
                )
                role_retry_coverage.extend(retry_coverage)
                role_retry_complete = role_retry_complete and retry_complete
                if browser_retry_events is not None:
                    role_retries += browser_retry_events

            release_boundaries: list[str] = []
            for boundary in (start_at, end_at):
                rows = query(f"max by (version) (crawler_build_info{{{selector}}})", boundary)
                if len(rows) != 1 or set(rows[0].get("metric", {})) != {"version"}:
                    release_boundaries = []
                    break
                version = rows[0]["metric"].get("version")
                try:
                    raw_release_value = float(rows[0]["value"][1])
                except (KeyError, IndexError, TypeError, ValueError):
                    raw_release_value = float("nan")
                value = _nonnegative_integer(raw_release_value)
                if not isinstance(version, str) or not version or value != 1:
                    release_boundaries = []
                    break
                release_boundaries.append(version)
            if len(release_boundaries) == 2 and len(set(release_boundaries)) == 1:
                releases.add(release_boundaries[0])
            else:
                capture_evidence_gaps.append(f"release-boundary-incomplete:{target_id}")

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
            if execution_class not in {"http", "browser", "support"}:
                raise ModelError(f"unsupported execution class {execution_class!r}")
            execution_classes.add(str(execution_class))
            discovery = target.get("discovery_concurrency")
            monitor = target.get("monitor_concurrency")
            if execution_class == "support":
                if discovery is not None or monitor is not None:
                    raise ModelError(f"{instance} support concurrency must be null")
                discovery_concurrencies.add(None)
                monitor_concurrencies.add(None)
                continue
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
                for egress_entry in _capture_egress_target_stage(
                    query,
                    selector=selector,
                    target_id=target_id,
                    stage=stage,
                    execution_class=str(execution_class),
                    start_at=start_at,
                    end_at=end_at,
                    range_selector=range_selector,
                ):
                    role_egress_targets[(stage, str(egress_entry["egress"]))].append(egress_entry)
                role_capability_targets[stage].append(
                    _capture_capability_target_stage(
                        query,
                        selector=selector,
                        target_id=target_id,
                        metric_stage=metric_kind,
                        output_stage=stage,
                        start_at=start_at,
                        end_at=end_at,
                        range_selector=range_selector,
                    )
                )

        if (
            len(cost_categories) != 1
            or len(discovery_concurrencies) != 1
            or len(monitor_concurrencies) != 1
            or len(vcpu_limits) != 1
            or len(memory_limits) != 1
            or len(execution_classes) != 1
        ):
            raise ModelError(
                f"targets in role {role} must use identical limits, category, and execution class"
            )
        if role_process_tree_complete:
            role_cpu = role_process_tree_cpu
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
            role_process_tree_samples = 0
            resource_scope = "root-process"

        egress_coverage: list[dict[str, Any]] = []
        lane_egress_totals: dict[tuple[str, str], tuple[int, int]] = {}
        role_execution_class = next(iter(execution_classes))
        if role_execution_class in {"http", "browser"}:
            for (stage, egress), entries in sorted(role_egress_targets.items()):
                complete_targets = sum(1 for entry in entries if entry["complete"])
                complete = len(entries) == len(role_targets) and complete_targets == len(
                    role_targets
                )
                reset_values = [entry["counter_resets"] for entry in entries]
                egress_coverage.append(
                    {
                        "stage": stage,
                        "execution_class": role_execution_class,
                        "egress": egress,
                        "scope": "shared-http-transport",
                        "expected_targets": len(role_targets),
                        "complete_targets": complete_targets,
                        "complete": complete,
                        "counter_resets": (
                            sum(reset_values)
                            if all(value is not None for value in reset_values)
                            else None
                        ),
                        "origin_attempts": (
                            sum(entry["origin_attempts"] for entry in entries) if complete else None
                        ),
                        "responses": (
                            sum(entry["responses"] for entry in entries) if complete else None
                        ),
                        "transport_errors": (
                            sum(entry["transport_errors"] for entry in entries)
                            if complete
                            else None
                        ),
                        "response_bytes": (
                            sum(entry["response_bytes"] for entry in entries) if complete else None
                        ),
                    }
                )

            # HTTP workers have no browser page/subresource transport. Promote
            # a lane only when both actual routes cover every target exactly.
            if role_execution_class == "http":
                for stage in ("monitor", "detail"):
                    route_entries = [item for item in egress_coverage if item["stage"] == stage]
                    if len(route_entries) == 2 and all(item["complete"] for item in route_entries):
                        lane_egress_totals[(stage, "http")] = (
                            sum(int(item["origin_attempts"]) for item in route_entries),
                            sum(int(item["response_bytes"]) for item in route_entries),
                        )

        capability_coverage: list[dict[str, Any]] = []
        capability_totals: dict[tuple[str, str, str], int] = defaultdict(int)
        if role_execution_class in {"http", "browser"}:
            for stage, entries in sorted(role_capability_targets.items()):
                complete_targets = sum(1 for entry in entries if entry["complete"])
                complete = len(entries) == len(role_targets) and complete_targets == len(
                    role_targets
                )
                capability_coverage.append(
                    {
                        "stage": stage,
                        "expected_targets": len(role_targets),
                        "complete_targets": complete_targets,
                        "complete": complete,
                    }
                )
                if complete:
                    for entry in entries:
                        for execution in entry["executions"]:
                            key = (
                                stage,
                                str(execution["capability"]),
                                str(execution["outcome"]),
                            )
                            capability_totals[key] += int(execution["executions"])

        role_measurements.append(
            {
                "role": role,
                "execution_class": role_execution_class,
                "cost_category": next(iter(cost_categories)),
                "instance_count": len(role_targets),
                "target_ids": sorted(role_target_ids),
                "discovery_concurrency_per_instance": next(iter(discovery_concurrencies)),
                "monitor_concurrency_per_instance": next(iter(monitor_concurrencies)),
                "vcpu_limit_per_instance": next(iter(vcpu_limits)),
                "memory_limit_bytes_per_instance": next(iter(memory_limits)),
                "resource_scope": resource_scope,
                "process_tree_cpu_source": (
                    "container-cgroup-v2" if role_process_tree_complete else None
                ),
                "process_tree_cpu_scope": (
                    "one-crawler-role-container-per-target" if role_process_tree_complete else None
                ),
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
                "process_tree_coverage": role_process_tree_coverage,
                "retry_events": role_retries if role_retry_complete else None,
                "retry_coverage": role_retry_coverage,
                "egress_coverage": egress_coverage,
                "capability_mix": {
                    "implementation": "python",
                    "coverage": capability_coverage,
                    "executions": [
                        {
                            "stage": key[0],
                            "capability": key[1],
                            "outcome": key[2],
                            "executions": value,
                        }
                        for key, value in sorted(capability_totals.items())
                    ],
                },
                "lanes": [
                    {
                        "stage": key[0],
                        "execution_class": key[1],
                        **totals,
                        "origin_attempts": (
                            lane_egress_totals[key][0] if key in lane_egress_totals else None
                        ),
                        "response_bytes": (
                            lane_egress_totals[key][1] if key in lane_egress_totals else None
                        ),
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
    lane_measurements: dict[tuple[str, str], dict[str, Any]] = {}
    lane_owners: dict[tuple[str, str], str] = {}
    for role in role_measurements:
        for lane in role["lanes"]:
            key = (str(lane["stage"]), str(lane["execution_class"]))
            if key in lane_measurements:
                raise ModelError(
                    "duplicate workload lane "
                    f"{key[0]!r}/{key[1]!r} across roles "
                    f"{lane_owners[key]!r} and {role['role']!r}"
                )
            lane_measurements[key] = lane
            lane_owners[key] = str(role["role"])
    evidence_gaps = ["queue-and-redis-resource-use-requires-separate-capture"]
    for stage in ("monitor", "detail"):
        for execution_class in ("http", "browser"):
            lane = lane_measurements.get((stage, execution_class))
            if lane is None or lane["origin_attempts"] is None:
                evidence_gaps.append(f"origin-attempts-unmeasured:{stage}:{execution_class}")
            if lane is None or lane["response_bytes"] is None:
                evidence_gaps.append(f"response-bytes-unmeasured:{stage}:{execution_class}")
    evidence_gaps.extend(
        [
            "browser-transport-unmeasured:lightpanda",
            "browser-cgroup-cost-unmeasured:lightpanda",
            "browser-transport-unmeasured:chromium",
            "browser-cgroup-cost-unmeasured:chromium",
        ]
    )
    if not browser_tree_complete:
        evidence_gaps.insert(0, "browser-child-cpu-and-rss-not-in-process-metrics")
    for role in role_measurements:
        for coverage in role["process_tree_coverage"]:
            if not coverage["complete"]:
                evidence_gaps.append(f"process-tree-evidence-incomplete:{coverage['target_id']}")
        for coverage in role["retry_coverage"]:
            if not coverage["complete"]:
                evidence_gaps.append(
                    "browser-retry-evidence-incomplete:"
                    f"{coverage['target_id']}:{coverage['family']}"
                )
    evidence_gaps.extend(capture_evidence_gaps)
    if len(releases) != 1:
        evidence_gaps.append("release-fleet-incoherent")
    evidence_gaps = list(dict.fromkeys(evidence_gaps))

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
