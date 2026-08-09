#!/usr/bin/env python3
"""Block crawler mutations unless PostgreSQL has verified recovery headroom."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

QUERIES = {
    "sampler_age_seconds": (
        'time() - max(jobseek_host_observability_last_collect_unixtime{host_role="postgresql"})'
    ),
    "ready": 'max(jobseek_postgresql_ready{host_role="postgresql"})',
    "data_free_ratio": (
        'max(node_filesystem_avail_bytes{job="integrations/unix",host_role="postgresql",'
        'fstype="xfs",mountpoint=~"/mnt/.*"}) / '
        'max(node_filesystem_size_bytes{job="integrations/unix",host_role="postgresql",'
        'fstype="xfs",mountpoint=~"/mnt/.*"})'
    ),
    "repository_free_ratio": (
        'max(node_filesystem_avail_bytes{job="integrations/unix",host_role="postgresql",'
        'fstype="cifs",mountpoint=~"/mnt/.*"}) / '
        'max(node_filesystem_size_bytes{job="integrations/unix",host_role="postgresql",'
        'fstype="cifs",mountpoint=~"/mnt/.*"})'
    ),
    "emergency_reserve_bytes": (
        'max(jobseek_postgresql_emergency_reserve_bytes{host_role="postgresql"})'
    ),
    "backup_attempt_success": (
        'max(jobseek_backup_last_attempt_success{host_role="postgresql",service="postgresql"})'
    ),
    "backup_age_seconds": (
        'time() - max(jobseek_backup_last_success_unixtime{host_role="postgresql",'
        'service="postgresql"})'
    ),
    "archive_failures_1h": (
        'max(increase(jobseek_postgresql_archive_failed_total{host_role="postgresql"}[1h]))'
    ),
}

MIN_DATA_FREE_RATIO = 0.15
MIN_REPOSITORY_FREE_RATIO = 0.20
MIN_EMERGENCY_RESERVE_BYTES = 2_147_483_648
MAX_SAMPLER_AGE_SECONDS = 300
MAX_BACKUP_AGE_SECONDS = 36 * 60 * 60


class PreflightError(RuntimeError):
    """The PostgreSQL production safety contract is unavailable or unhealthy."""


def _query_base(remote_write_url: str) -> str:
    base = remote_write_url.strip().rstrip("/")
    if not base.endswith("/api/prom/push"):
        raise PreflightError("Grafana remote-write URL has an unexpected shape")
    return base[: -len("/push")]


def _scalar(payload: dict[str, Any], name: str) -> float:
    try:
        rows = payload["data"]["result"]
        if payload.get("status") != "success" or len(rows) != 1:
            raise PreflightError(f"{name} did not return exactly one result")
        return float(rows[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise PreflightError(f"{name} returned an invalid result") from exc


def validate(values: dict[str, float]) -> None:
    if not 0 <= values["sampler_age_seconds"] <= MAX_SAMPLER_AGE_SECONDS:
        raise PreflightError("PostgreSQL host telemetry is stale")
    if values["ready"] != 1:
        raise PreflightError("PostgreSQL is not ready")
    if values["data_free_ratio"] < MIN_DATA_FREE_RATIO:
        raise PreflightError("PostgreSQL data Volume lacks deploy headroom")
    if values["repository_free_ratio"] < MIN_REPOSITORY_FREE_RATIO:
        raise PreflightError("PostgreSQL backup repository lacks deploy headroom")
    if values["emergency_reserve_bytes"] < MIN_EMERGENCY_RESERVE_BYTES:
        raise PreflightError("PostgreSQL emergency recovery reserve is missing")
    if values["backup_attempt_success"] != 1:
        raise PreflightError("the latest PostgreSQL backup attempt failed")
    if not 0 <= values["backup_age_seconds"] <= MAX_BACKUP_AGE_SECONDS:
        raise PreflightError("the latest PostgreSQL backup is stale")
    if values["archive_failures_1h"] > 0:
        raise PreflightError("PostgreSQL recorded a recent WAL archive failure")


def verify(url: str, username: str, password: str) -> dict[str, float]:
    base = _query_base(url)
    authorization = base64.b64encode(f"{username}:{password}".encode()).decode()
    values: dict[str, float] = {}
    for name, query in QUERIES.items():
        endpoint = f"{base}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
        request = urllib.request.Request(
            endpoint, headers={"Authorization": f"Basic {authorization}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                if response.status != 200:
                    raise PreflightError(f"{name} returned HTTP {response.status}")
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise PreflightError(f"{name} query failed: {type(exc).__name__}") from exc
        values[name] = _scalar(payload, name)
    validate(values)
    return values


def main() -> int:
    url = os.environ.get("GRAFANA_PROM_URL")
    username = os.environ.get("GRAFANA_PROM_USERNAME")
    password = os.environ.get("GRAFANA_PROM_PASSWORD")
    if not url or not username or not password:
        raise SystemExit("Grafana URL, username, and password are required")
    started = time.monotonic()
    verify(url, username, password)
    print(f"PostgreSQL operational preflight passed in {time.monotonic() - started:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
