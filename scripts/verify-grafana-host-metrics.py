#!/usr/bin/env python3
"""Fail deployment until all Hetzner host textfile metrics reach Grafana."""

from __future__ import annotations

import argparse
import os
import time
from typing import Any

import httpx

EXPECTED_ROLES = frozenset({"crawler", "postgresql", "typesense"})
QUERIES = {
    "fresh_sampler": "jobseek_host_observability_last_collect_unixtime",
    "probe_series": "count by (host_role) (jobseek_host_observability_probe_success)",
    "failed_probes": "count(jobseek_host_observability_probe_success == 0)",
    "container_series": "count by (host_role) (jobseek_container_running)",
    "stopped_containers": "count(jobseek_container_running == 0)",
    "backup_series": "count(jobseek_backup_last_attempt_success)",
    "failed_backups": "count(jobseek_backup_last_attempt_success == 0)",
    "postgresql_ready": "count(jobseek_postgresql_ready == 1)",
    "postgresql_shared_memory": "count(jobseek_postgresql_shared_memory_configured_bytes)",
    "postgresql_checkpoint_metrics": ("count(jobseek_postgresql_checkpoint_write_seconds_total)"),
    "postgresql_query_latency": ("count(jobseek_postgresql_stats_query_duration_seconds)"),
    "typesense_ready": "count(jobseek_typesense_healthy == 1)",
}


class VerificationError(RuntimeError):
    """Required host textfile metrics are missing, stale, or unhealthy."""


def _query_base(remote_write_url: str) -> str:
    base = remote_write_url.strip().rstrip("/")
    if not base.endswith("/api/prom/push"):
        raise VerificationError("Grafana remote-write URL has an unexpected shape")
    return base[: -len("/push")]


def _scalar(results: dict[str, list[dict[str, Any]]], name: str) -> float:
    rows = results[name]
    if not rows:
        return 0.0
    if len(rows) != 1:
        raise VerificationError(f"{name} returned multiple scalar rows")
    try:
        return float(rows[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise VerificationError(f"{name} returned an invalid scalar") from exc


def _role_values(results: dict[str, list[dict[str, Any]]], name: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for row in results[name]:
        try:
            role = str(row["metric"]["host_role"])
            value = float(row["value"][1])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise VerificationError(f"{name} returned an invalid role row") from exc
        values[role] = value
    return values


def validate_results(
    results: dict[str, list[dict[str, Any]]], *, now: float, max_age_seconds: int
) -> None:
    fresh = _role_values(results, "fresh_sampler")
    if set(fresh) != EXPECTED_ROLES:
        raise VerificationError("sampler timestamp does not cover all expected host roles")
    if any(
        value <= 0 or now - value > max_age_seconds or value > now + 60 for value in fresh.values()
    ):
        raise VerificationError("sampler timestamp is stale or invalid")

    for name in ("probe_series", "container_series"):
        values = _role_values(results, name)
        if set(values) != EXPECTED_ROLES or any(value < 1 for value in values.values()):
            raise VerificationError(f"{name} does not cover all expected host roles")
    for name in ("failed_probes", "stopped_containers", "failed_backups"):
        if _scalar(results, name) != 0:
            raise VerificationError(f"{name} is nonzero")
    if _scalar(results, "backup_series") != 2:
        raise VerificationError(
            "application-data backup metrics do not cover PostgreSQL and Typesense"
        )
    if _scalar(results, "postgresql_ready") != 1:
        raise VerificationError("PostgreSQL readiness metric is missing or unhealthy")
    if _scalar(results, "postgresql_shared_memory") != 1:
        raise VerificationError("PostgreSQL shared-memory metric is missing")
    if _scalar(results, "postgresql_checkpoint_metrics") != 1:
        raise VerificationError("PostgreSQL checkpoint-duration metric is missing")
    if _scalar(results, "postgresql_query_latency") != 1:
        raise VerificationError("PostgreSQL statistics-query latency metric is missing")
    if _scalar(results, "typesense_ready") != 1:
        raise VerificationError("Typesense readiness metric is missing or unhealthy")


def _query_all(base_url: str, username: str, password: str) -> dict[str, list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {}
    with httpx.Client(auth=(username, password), timeout=30, follow_redirects=False) as client:
        for name, query in QUERIES.items():
            response = client.get(f"{base_url}/api/v1/query", params={"query": query})
            if response.status_code != 200:
                raise VerificationError(f"{name} returned HTTP {response.status_code}")
            try:
                payload = response.json()
                rows = payload["data"]["result"]
            except (KeyError, TypeError, ValueError) as exc:
                raise VerificationError(f"{name} returned an invalid response") from exc
            if payload.get("status") != "success" or not isinstance(rows, list):
                raise VerificationError(f"{name} did not report success")
            results[name] = rows
    return results


def verify(
    remote_write_url: str,
    username: str,
    password: str,
    *,
    wait_seconds: int,
    max_age_seconds: int,
) -> None:
    base_url = _query_base(remote_write_url)
    deadline = time.monotonic() + wait_seconds
    last_error: VerificationError | None = None
    while True:
        try:
            results = _query_all(base_url, username, password)
            validate_results(results, now=time.time(), max_age_seconds=max_age_seconds)
            return
        except (httpx.HTTPError, VerificationError) as exc:
            last_error = (
                exc
                if isinstance(exc, VerificationError)
                else VerificationError(f"Grafana query transport failed: {type(exc).__name__}")
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise last_error or VerificationError("host metrics verification timed out")
        time.sleep(min(10, remaining))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("GRAFANA_PROM_URL"))
    parser.add_argument("--username", default=os.environ.get("GRAFANA_PROM_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("GRAFANA_PROM_PASSWORD"))
    parser.add_argument("--wait-seconds", type=int, default=240)
    parser.add_argument("--max-age-seconds", type=int, default=300)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if not args.url or not args.username or not args.password:
        raise SystemExit("Grafana URL, username, and password are required")
    verify(
        args.url,
        args.username,
        args.password,
        wait_seconds=args.wait_seconds,
        max_age_seconds=args.max_age_seconds,
    )
    print("verified fresh host textfile metrics for all expected roles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
