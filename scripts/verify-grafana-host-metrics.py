#!/usr/bin/env python3
"""Fail deployment until all Hetzner host textfile metrics reach Grafana."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import httpx

EXPECTED_ROLES = frozenset({"crawler", "postgresql", "typesense"})
EXPECTED_CRAWLER_INSTANCES = frozenset(
    {"worker-1", "worker-2", "worker-3", "browser-1", "exporter", "drain"}
)
CAPABILITY_PIPELINE_INSTANCES = frozenset({"worker-1", "worker-2", "worker-3", "browser-1"})
REQUIRED_BACKUPS = frozenset({("postgresql", "postgresql"), ("typesense", "typesense")})
OPTIONAL_BACKUPS = frozenset({("typesense", "web-postgresql")})
_SAFE_LABEL_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_EVIDENCE_LABEL_NAMES = frozenset({"host_role", "collector", "probe", "container", "service"})
SERIES_BUDGETS = {
    "active_series": 12_000,
    "crawler_series": 5_000,
    # The current registry deterministically seeds 2,056 fleet series. Keep
    # bounded growth room without weakening the independent crawler-wide cap.
    "crawler_capability_series": 2_200,
    "redis_series": 200,
    "unix_series": 2_000,
}
QUERIES = {
    "fresh_sampler": "jobseek_host_observability_last_collect_unixtime",
    "crawler_up": 'up{job="crawler"}',
    # Instant-vector results are stamped with query evaluation time. Ask for
    # the underlying scrape timestamp explicitly so cached samples cannot
    # approve a deployment.
    "crawler_up_timestamp": 'timestamp(up{job="crawler"})',
    "probe_series": "count by (host_role) (jobseek_host_observability_probe_success)",
    "failed_probes": "jobseek_host_observability_probe_success == 0",
    "container_series": "count by (host_role) (jobseek_container_running)",
    "stopped_containers": "jobseek_container_running == 0",
    "backup_series": "jobseek_backup_last_attempt_success",
    "failed_backups": "jobseek_backup_last_attempt_success == 0",
    "backup_helper_image_series": (
        'jobseek_backup_helper_image_available{service="web-postgresql"}'
    ),
    "backup_helper_image_gc_series": (
        'jobseek_backup_helper_image_gc_protected{service="web-postgresql"}'
    ),
    "postgresql_ready": "count(jobseek_postgresql_ready == 1)",
    "postgresql_shared_memory": "count(jobseek_postgresql_shared_memory_configured_bytes)",
    "postgresql_emergency_reserve": "count(jobseek_postgresql_emergency_reserve_bytes)",
    "postgresql_checkpoint_metrics": ("count(jobseek_postgresql_checkpoint_write_seconds_total)"),
    "postgresql_query_latency": ("count(jobseek_postgresql_stats_query_duration_seconds)"),
    "typesense_ready": "count(jobseek_typesense_healthy == 1)",
    "codex_review_series": ('count({__name__=~"jobseek_codex_daily_error_review_.*"})'),
    "alloy_series": "count by (host_role, collector) (jobseek_alloy_ready)",
    "alloy_unready": "jobseek_alloy_ready == 0",
    "alloy_rejections": "jobseek_alloy_remote_write_rejections_recent > 0",
    "alloy_stale": ("time() - jobseek_alloy_remote_write_highest_sent_timestamp_seconds > 180"),
    "alloy_backlog": "jobseek_alloy_remote_write_samples_pending > 3000",
    "active_series": 'count({job=~".+"})',
    "crawler_series": 'count({job="crawler"})',
    "crawler_capability_series": (
        'count({job="crawler",__name__="crawler_runtime_capability_executions_total"}) or vector(0)'
    ),
    "crawler_created_series": 'count({job="crawler",__name__=~".*_created"}) or vector(0)',
    "redis_series": 'count({job="integrations/redis"})',
    "unix_series": 'count({job="integrations/unix"})',
}


class FailedInvariant(NamedTuple):
    """One redacted, attributable Grafana verification failure."""

    query_name: str
    invariant: str
    observed_value: float | None
    sample_timestamp: float | None
    age_seconds: float | None
    host_role: str
    labels: dict[str, str]


class VerificationError(RuntimeError):
    """Required host textfile metrics are missing, stale, or unhealthy."""

    def __init__(self, failures: str | list[FailedInvariant]) -> None:
        if isinstance(failures, str):
            failures = [
                FailedInvariant(
                    query_name="verifier",
                    invariant=failures,
                    observed_value=None,
                    sample_timestamp=None,
                    age_seconds=None,
                    host_role="unknown",
                    labels={},
                )
            ]
        self.failures = tuple(failures)
        self.evidence: dict[str, Any] | None = None
        super().__init__("; ".join(_format_failure(failure) for failure in failures))


class Observation(NamedTuple):
    value: float
    sample_timestamp: float
    labels: dict[str, str]


def _error(name: str, invariant: str) -> VerificationError:
    return VerificationError(
        [
            FailedInvariant(
                query_name=name,
                invariant=invariant,
                observed_value=None,
                sample_timestamp=None,
                age_seconds=None,
                host_role="unknown",
                labels={},
            )
        ]
    )


def _format_failure(failure: FailedInvariant) -> str:
    value = "missing" if failure.observed_value is None else f"{failure.observed_value:g}"
    timestamp = "missing" if failure.sample_timestamp is None else f"{failure.sample_timestamp:.3f}"
    age = "unknown" if failure.age_seconds is None else f"{failure.age_seconds:.1f}s"
    return (
        f"{failure.invariant} [query={failure.query_name} role={failure.host_role} "
        f"observed={value} timestamp={timestamp} age={age}]"
    )


def _observation(row: dict[str, Any], name: str) -> Observation:
    try:
        raw_labels = row["metric"]
        if not isinstance(raw_labels, dict):
            raise TypeError
        labels = {key: raw_labels[key] for key in _EVIDENCE_LABEL_NAMES if key in raw_labels}
        timestamp = float(row["value"][0])
        value = float(row["value"][1])
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise _error(name, f"{name} returned an invalid observation") from exc
    if (
        any(not isinstance(label_value, str) for label_value in labels.values())
        or any(_SAFE_LABEL_VALUE.fullmatch(label_value) is None for label_value in labels.values())
        or not math.isfinite(timestamp)
        or not math.isfinite(value)
    ):
        raise _error(name, f"{name} returned an invalid observation")
    return Observation(value=value, sample_timestamp=timestamp, labels=labels)


def _failure(
    name: str,
    invariant: str,
    *,
    now: float,
    observation: Observation | None = None,
    host_role: str | None = None,
    labels: dict[str, str] | None = None,
    observed_value: float | None = None,
    observed_timestamp: float | None = None,
) -> FailedInvariant:
    timestamp = observation.sample_timestamp if observation is not None else None
    value = observation.value if observation is not None else None
    merged_labels = dict(labels or {})
    if observation is not None:
        merged_labels = observation.labels
    if observed_timestamp is not None:
        timestamp = observed_timestamp
    if observed_value is not None:
        value = observed_value
    role = host_role or merged_labels.get("host_role") or "unknown"
    age = None if timestamp is None else now - timestamp
    return FailedInvariant(
        query_name=name,
        invariant=invariant,
        observed_value=value,
        sample_timestamp=timestamp,
        age_seconds=age,
        host_role=role,
        labels=merged_labels,
    )


def _query_base(remote_write_url: str) -> str:
    base = remote_write_url.strip().rstrip("/")
    if not base.endswith("/api/prom/push"):
        raise VerificationError("Grafana remote-write URL has an unexpected shape")
    return base[: -len("/push")]


def _scalar_observation(results: dict[str, list[dict[str, Any]]], name: str) -> Observation | None:
    rows = results[name]
    if not rows:
        return None
    if len(rows) != 1:
        raise _error(name, f"{name} returned multiple scalar rows")
    return _observation(rows[0], name)


def _role_observations(
    results: dict[str, list[dict[str, Any]]], name: str
) -> dict[str, Observation]:
    values: dict[str, Observation] = {}
    for row in results[name]:
        observation = _observation(row, name)
        try:
            role = observation.labels["host_role"]
        except KeyError as exc:
            raise _error(name, f"{name} returned an invalid role row") from exc
        if role in values:
            raise _error(name, f"{name} returned duplicate role rows for {role}")
        values[role] = observation
    return values


def _series_keys(
    results: dict[str, list[dict[str, Any]]], name: str, labels: tuple[str, ...]
) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for row in results[name]:
        try:
            metric = _observation(row, name).labels
            key = tuple(str(metric[label]) for label in labels)
        except (KeyError, TypeError) as exc:
            raise _error(name, f"{name} returned invalid series labels") from exc
        if any(_SAFE_LABEL_VALUE.fullmatch(value) is None for value in key):
            raise _error(name, f"{name} returned invalid series labels")
        keys.add(key)
    return keys


def _format_series(keys: set[tuple[str, ...]]) -> str:
    ordered = sorted("/".join(key) for key in keys)
    rendered = ", ".join(ordered[:10])
    if len(ordered) > 10:
        rendered += f", +{len(ordered) - 10} more"
    return rendered


def validate_results(
    results: dict[str, list[dict[str, Any]]],
    *,
    now: float,
    max_age_seconds: int,
    minimum_collected_at: float | None = None,
) -> None:
    missing_results = set(QUERIES) - set(results)
    if missing_results:
        name = sorted(missing_results)[0]
        raise _error(name, f"{name} query result is missing")
    failures: list[FailedInvariant] = []
    fresh = _role_observations(results, "fresh_sampler")
    for role in sorted(EXPECTED_ROLES - set(fresh)):
        failures.append(
            _failure(
                "fresh_sampler",
                "sampler timestamp does not cover all expected host roles",
                now=now,
                host_role=role,
            )
        )
    for role in sorted(set(fresh) - EXPECTED_ROLES):
        failures.append(
            _failure(
                "fresh_sampler",
                "sampler timestamp contains an unexpected host role",
                now=now,
                observation=fresh[role],
            )
        )
    for role in sorted(EXPECTED_ROLES & set(fresh)):
        observation = fresh[role]
        collected_at = observation.value
        if minimum_collected_at is not None and collected_at < minimum_collected_at:
            failures.append(
                _failure(
                    "fresh_sampler",
                    "sampler timestamp predates the completed deployment",
                    now=now,
                    observation=observation,
                    observed_value=collected_at,
                    observed_timestamp=collected_at,
                )
            )
        elif collected_at <= 0 or now - collected_at > max_age_seconds or collected_at > now + 60:
            failures.append(
                _failure(
                    "fresh_sampler",
                    "sampler timestamp is stale or invalid",
                    now=now,
                    observation=observation,
                    observed_value=collected_at,
                    observed_timestamp=collected_at,
                )
            )

    crawler_observations: dict[str, dict[str, Observation]] = {}
    for name in ("crawler_up", "crawler_up_timestamp"):
        values: dict[str, Observation] = {}
        for row in results[name]:
            try:
                instance = row["metric"]["instance"]
                if not isinstance(instance, str) or instance not in EXPECTED_CRAWLER_INSTANCES:
                    raise TypeError
                if instance in values:
                    raise ValueError
                values[instance] = _observation(row, name)
            except (KeyError, TypeError, ValueError) as exc:
                raise _error(name, f"{name} returned invalid target labels") from exc
        crawler_observations[name] = values
    crawler_up = crawler_observations["crawler_up"]
    crawler_up_timestamp = crawler_observations["crawler_up_timestamp"]
    complete_instances = set(crawler_up) & set(crawler_up_timestamp)
    for instance in sorted(EXPECTED_CRAWLER_INSTANCES - complete_instances):
        failures.append(
            _failure(
                "crawler_up",
                f"crawler scrape target is missing, stale, or unhealthy: {instance}",
                now=now,
                host_role="crawler",
            )
        )
    for instance in sorted(complete_instances):
        value_observation = crawler_up[instance]
        timestamp_observation = crawler_up_timestamp[instance]
        observation = Observation(
            value=value_observation.value,
            sample_timestamp=timestamp_observation.value,
            labels=value_observation.labels,
        )
        sample_timestamp = observation.sample_timestamp
        predates_deployment = (
            minimum_collected_at is not None and sample_timestamp < minimum_collected_at
        )
        stale_or_invalid = (
            sample_timestamp <= 0
            or now - sample_timestamp > max_age_seconds
            or sample_timestamp > now + 60
        )
        if observation.value != 1 or predates_deployment or stale_or_invalid:
            failures.append(
                _failure(
                    "crawler_up",
                    f"crawler scrape target is missing, stale, or unhealthy: {instance}",
                    now=now,
                    observation=observation,
                    host_role="crawler",
                )
            )

    for name in ("probe_series", "container_series"):
        values = _role_observations(results, name)
        for role in sorted(EXPECTED_ROLES - set(values)):
            failures.append(
                _failure(
                    name,
                    f"{name} does not cover all expected host roles",
                    now=now,
                    host_role=role,
                )
            )
        for role, observation in sorted(values.items()):
            if role not in EXPECTED_ROLES or observation.value < 1:
                failures.append(
                    _failure(
                        name,
                        f"{name} does not cover all expected host roles",
                        now=now,
                        observation=observation,
                    )
                )
    unhealthy_series = (
        ("failed_probes", ("host_role", "probe")),
        ("stopped_containers", ("host_role", "container")),
        ("failed_backups", ("host_role", "service")),
    )
    for name, labels in unhealthy_series:
        for row in results[name]:
            observation = _observation(row, name)
            try:
                key = tuple(observation.labels[label] for label in labels)
            except KeyError as exc:
                raise _error(name, f"{name} returned invalid series labels") from exc
            failures.append(
                _failure(
                    name,
                    f"{name} is nonzero: {'/'.join(key)}",
                    now=now,
                    observation=observation,
                )
            )

    backups = _series_keys(results, "backup_series", ("host_role", "service"))
    missing_backups = REQUIRED_BACKUPS - backups
    unexpected_backups = backups - REQUIRED_BACKUPS - OPTIONAL_BACKUPS
    if missing_backups or unexpected_backups:
        for role, service in sorted(missing_backups):
            failures.append(
                _failure(
                    "backup_series",
                    f"application-data backup coverage invalid: missing={role}/{service}",
                    now=now,
                    host_role=role,
                    labels={"host_role": role, "service": service},
                )
            )
        for role, service in sorted(unexpected_backups):
            failures.append(
                _failure(
                    "backup_series",
                    f"application-data backup coverage invalid: unexpected={role}/{service}",
                    now=now,
                    host_role=role,
                    labels={"host_role": role, "service": service},
                )
            )
    expected_helper_series = (
        {("typesense", "web-postgresql")} if ("typesense", "web-postgresql") in backups else set()
    )
    for name in ("backup_helper_image_series", "backup_helper_image_gc_series"):
        helper_series = _series_keys(results, name, ("host_role", "service"))
        if helper_series != expected_helper_series:
            failures.append(
                _failure(
                    name,
                    f"{name} coverage does not match the activated web backup",
                    now=now,
                    host_role="typesense",
                )
            )

    scalar_invariants = (
        (
            "postgresql_ready",
            1,
            "PostgreSQL readiness metric is missing or unhealthy",
            "postgresql",
        ),
        ("postgresql_shared_memory", 1, "PostgreSQL shared-memory metric is missing", "postgresql"),
        (
            "postgresql_emergency_reserve",
            1,
            "PostgreSQL emergency-reserve metric is missing",
            "postgresql",
        ),
        (
            "postgresql_checkpoint_metrics",
            1,
            "PostgreSQL checkpoint-duration metric is missing",
            "postgresql",
        ),
        (
            "postgresql_query_latency",
            1,
            "PostgreSQL statistics-query latency metric is missing",
            "postgresql",
        ),
        ("typesense_ready", 1, "Typesense readiness metric is missing or unhealthy", "typesense"),
        ("codex_review_series", 4, "Codex daily-review deadman metrics are incomplete", "crawler"),
    )
    for name, expected, invariant, role in scalar_invariants:
        observation = _scalar_observation(results, name)
        if observation is None or observation.value != expected:
            failures.append(
                _failure(
                    name,
                    invariant,
                    now=now,
                    observation=observation,
                    host_role=role,
                )
            )

    expected_alloy = {
        ("crawler", "host"),
        ("crawler", "compose"),
        ("postgresql", "host"),
        ("typesense", "host"),
    }
    observed_alloy: dict[tuple[str, str], Observation] = {}
    for row in results["alloy_series"]:
        observation = _observation(row, "alloy_series")
        try:
            key = (observation.labels["host_role"], observation.labels["collector"])
        except KeyError as exc:
            raise _error("alloy_series", "alloy_series returned invalid series labels") from exc
        if key in observed_alloy:
            raise _error(
                "alloy_series",
                f"alloy_series returned duplicate series labels for {'/'.join(key)}",
            )
        observed_alloy[key] = observation
    for role, collector in sorted(expected_alloy - set(observed_alloy)):
        failures.append(
            _failure(
                "alloy_series",
                "Alloy self-monitoring does not cover three host and one compose collectors",
                now=now,
                host_role=role,
                labels={"host_role": role, "collector": collector},
            )
        )
    for key, observation in sorted(observed_alloy.items()):
        if key not in expected_alloy or observation.value != 1:
            failures.append(
                _failure(
                    "alloy_series",
                    "Alloy self-monitoring does not cover three host and one compose collectors",
                    now=now,
                    observation=observation,
                )
            )

    for name in ("alloy_unready", "alloy_rejections", "alloy_stale", "alloy_backlog"):
        for row in results[name]:
            observation = _observation(row, name)
            failures.append(
                _failure(
                    name,
                    f"{name} is nonzero",
                    now=now,
                    observation=observation,
                )
            )
    created_series = _scalar_observation(results, "crawler_created_series")
    if created_series is None or created_series.value != 0:
        failures.append(
            _failure(
                "crawler_created_series",
                "automatic crawler _created series are present",
                now=now,
                observation=created_series,
                host_role="crawler",
            )
        )
    for name, budget in SERIES_BUDGETS.items():
        observation = _scalar_observation(results, name)
        if observation is None or observation.value <= 0 or observation.value > budget:
            failures.append(
                _failure(
                    name,
                    f"{name} exceeds its {budget}-series budget or is missing",
                    now=now,
                    observation=observation,
                    host_role={
                        "crawler_series": "crawler",
                        "crawler_capability_series": "crawler",
                        "redis_series": "crawler",
                        "unix_series": "fleet",
                        "active_series": "tenant",
                    }[name],
                )
            )

    if failures:
        raise VerificationError(failures)


def _query_all(
    base_url: str, username: str, password: str, timeout_seconds: float
) -> dict[str, list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {}
    deadline = time.monotonic() + timeout_seconds
    with httpx.Client(auth=(username, password), timeout=30, follow_redirects=False) as client:
        for name, query in QUERIES.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _error(name, "Grafana query batch exceeded its convergence budget")
            try:
                response = client.get(
                    f"{base_url}/api/v1/query",
                    params={"query": query},
                    timeout=max(0.001, min(30.0, remaining)),
                )
            except httpx.HTTPError as exc:
                raise _error(
                    name, f"{name} Grafana query transport failed: {type(exc).__name__}"
                ) from exc
            if response.status_code != 200:
                raise _error(name, f"{name} returned HTTP {response.status_code}")
            try:
                payload = response.json()
                rows = payload["data"]["result"]
            except (KeyError, TypeError, ValueError) as exc:
                raise _error(name, f"{name} returned an invalid response") from exc
            if payload.get("status") != "success" or not isinstance(rows, list):
                raise _error(name, f"{name} did not report success")
            results[name] = rows
    return results


def _terminal_evidence(
    *,
    primary_error: VerificationError,
    deployment_completed_at: float,
    deadline_at: float,
    checked_at: float,
    attempts: int,
    latest_query_error: tuple[int, float, VerificationError] | None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "status": "failed",
        "deployment_completed_at": deployment_completed_at,
        "convergence_deadline_at": deadline_at,
        "checked_at": checked_at,
        "attempts": attempts,
        "failures": [failure._asdict() for failure in primary_error.failures],
    }
    if latest_query_error is not None:
        attempt, query_checked_at, error = latest_query_error
        evidence["latest_query_error"] = {
            "attempt": attempt,
            "checked_at": query_checked_at,
            "failures": [failure._asdict() for failure in error.failures],
        }
    return evidence


def verify(
    remote_write_url: str,
    username: str,
    password: str,
    *,
    deployment_completed_at: float,
    convergence_seconds: int,
    max_age_seconds: int,
    query_all: Callable[[str, str, str, float], dict[str, list[dict[str, Any]]]] = _query_all,
    wall_time: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    base_url = _query_base(remote_write_url)
    started_at = wall_time()
    if (
        not math.isfinite(deployment_completed_at)
        or deployment_completed_at <= 0
        or deployment_completed_at > started_at + 60
    ):
        raise VerificationError("deployment completion timestamp is invalid")
    if convergence_seconds <= 0:
        raise VerificationError("convergence window must be positive")
    if max_age_seconds <= 0:
        raise VerificationError("maximum sample age must be positive")
    deadline_at = deployment_completed_at + convergence_seconds
    deadline = monotonic() + max(0.0, deadline_at - started_at)
    last_completed_batch_error: VerificationError | None = None
    latest_query_error: tuple[int, float, VerificationError] | None = None
    attempts = 0
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            terminal = (
                last_completed_batch_error
                or (latest_query_error[2] if latest_query_error is not None else None)
                or _error(
                    "verifier",
                    "convergence window elapsed before a complete Grafana query batch",
                )
            )
            terminal.evidence = _terminal_evidence(
                primary_error=terminal,
                deployment_completed_at=deployment_completed_at,
                deadline_at=deadline_at,
                checked_at=wall_time(),
                attempts=attempts,
                latest_query_error=latest_query_error,
            )
            raise terminal
        attempts += 1
        try:
            results = query_all(base_url, username, password, remaining)
            if monotonic() > deadline:
                raise _error(
                    "verifier", "Grafana query batch completed after the convergence deadline"
                )
        except (httpx.HTTPError, VerificationError) as exc:
            query_error = (
                exc
                if isinstance(exc, VerificationError)
                else _error("verifier", f"Grafana query transport failed: {type(exc).__name__}")
            )
            latest_query_error = (attempts, wall_time(), query_error)
        else:
            checked_at = wall_time()
            try:
                validate_results(
                    results,
                    now=checked_at,
                    max_age_seconds=max_age_seconds,
                    minimum_collected_at=deployment_completed_at,
                )
            except VerificationError as exc:
                last_completed_batch_error = exc
            else:
                return {
                    "status": "passed",
                    "deployment_completed_at": deployment_completed_at,
                    "convergence_deadline_at": deadline_at,
                    "checked_at": checked_at,
                    "attempts": attempts,
                    "failures": [],
                }
        remaining = deadline - monotonic()
        if remaining <= 0:
            terminal = (
                last_completed_batch_error
                or (latest_query_error[2] if latest_query_error is not None else None)
                or VerificationError("host metrics verification timed out")
            )
            terminal.evidence = _terminal_evidence(
                primary_error=terminal,
                deployment_completed_at=deployment_completed_at,
                deadline_at=deadline_at,
                checked_at=wall_time(),
                attempts=attempts,
                latest_query_error=latest_query_error,
            )
            raise terminal
        sleep(min(10, remaining))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("GRAFANA_PROM_URL"))
    parser.add_argument("--username", default=os.environ.get("GRAFANA_PROM_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("GRAFANA_PROM_PASSWORD"))
    parser.add_argument("--deployment-completed-at", type=float)
    parser.add_argument("--convergence-seconds", type=int, default=300)
    parser.add_argument("--max-age-seconds", type=int, default=300)
    parser.add_argument("--evidence-file", type=Path)
    return parser


def _write_evidence(path: Path | None, evidence: dict[str, Any]) -> None:
    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if path is not None:
        path.write_text(rendered, encoding="utf-8")
        path.chmod(0o600)
    print(rendered, end="")


def main() -> int:
    args = _build_parser().parse_args()
    if not args.url or not args.username or not args.password:
        raise SystemExit("Grafana URL, username, and password are required")
    deployment_completed_at = args.deployment_completed_at or time.time()
    try:
        evidence = verify(
            args.url,
            args.username,
            args.password,
            deployment_completed_at=deployment_completed_at,
            convergence_seconds=args.convergence_seconds,
            max_age_seconds=args.max_age_seconds,
        )
    except VerificationError as exc:
        evidence = exc.evidence or {
            "status": "failed",
            "deployment_completed_at": deployment_completed_at,
            "checked_at": time.time(),
            "attempts": 0,
            "failures": [failure._asdict() for failure in exc.failures],
        }
        _write_evidence(args.evidence_file, evidence)
        return 1
    _write_evidence(args.evidence_file, evidence)
    print("verified host health, Alloy delivery, and bounded Grafana series budgets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
