"""Tests for the post-deploy host textfile ingestion gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify-grafana-host-metrics.py"
SPEC = importlib.util.spec_from_file_location("verify_grafana_host_metrics", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def _row(value: float, *, role: str | None = None) -> dict:
    labels = {"host_role": role} if role else {}
    return {"metric": labels, "value": [1_800_000_000, str(value)]}


def _healthy_results(now: float) -> dict:
    roles = sorted(verify.EXPECTED_ROLES)
    return {
        "fresh_sampler": [_row(now - 30, role=role) for role in roles],
        "probe_series": [_row(4, role=role) for role in roles],
        "failed_probes": [],
        "container_series": [_row(1, role=role) for role in roles],
        "stopped_containers": [],
        "backup_series": [_row(2)],
        "failed_backups": [],
        "postgresql_ready": [_row(1)],
        "typesense_ready": [_row(1)],
    }


def test_validate_results_requires_fresh_complete_healthy_fleet() -> None:
    now = 1_800_000_000.0

    verify.validate_results(_healthy_results(now), now=now, max_age_seconds=300)


def test_validate_results_rejects_missing_role_and_stale_sampler() -> None:
    now = 1_800_000_000.0
    missing = _healthy_results(now)
    missing["fresh_sampler"].pop()
    with pytest.raises(verify.VerificationError, match="all expected host roles"):
        verify.validate_results(missing, now=now, max_age_seconds=300)

    stale = _healthy_results(now)
    stale["fresh_sampler"][0]["value"][1] = str(now - 301)
    with pytest.raises(verify.VerificationError, match="stale or invalid"):
        verify.validate_results(stale, now=now, max_age_seconds=300)


def test_validate_results_rejects_silent_probe_or_backup_failure() -> None:
    now = 1_800_000_000.0
    probe_failure = _healthy_results(now)
    probe_failure["failed_probes"] = [_row(1)]
    with pytest.raises(verify.VerificationError, match="failed_probes is nonzero"):
        verify.validate_results(probe_failure, now=now, max_age_seconds=300)

    missing_backup = _healthy_results(now)
    missing_backup["backup_series"] = [_row(1)]
    with pytest.raises(verify.VerificationError, match="PostgreSQL and Typesense"):
        verify.validate_results(missing_backup, now=now, max_age_seconds=300)
