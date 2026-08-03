from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/postgresql-operational-preflight.py"
SPEC = importlib.util.spec_from_file_location("postgresql_operational_preflight", SCRIPT)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def healthy() -> dict[str, float]:
    return {
        "sampler_age_seconds": 30,
        "ready": 1,
        "data_free_ratio": 0.40,
        "repository_free_ratio": 0.60,
        "emergency_reserve_bytes": 2_147_483_648,
        "backup_attempt_success": 1,
        "backup_age_seconds": 3_600,
        "archive_failures_1h": 0,
    }


def test_validate_accepts_recovered_postgresql_contract() -> None:
    preflight.validate(healthy())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("ready", 0, "not ready"),
        ("data_free_ratio", 0.14, "data Volume"),
        ("repository_free_ratio", 0.19, "backup repository"),
        ("emergency_reserve_bytes", 0, "emergency recovery reserve"),
        ("backup_attempt_success", 0, "backup attempt failed"),
        ("backup_age_seconds", 129_601, "backup is stale"),
        ("archive_failures_1h", 1, "archive failure"),
    ),
)
def test_validate_blocks_unsafe_mutation(field: str, value: float, message: str) -> None:
    values = healthy()
    values[field] = value

    with pytest.raises(preflight.PreflightError, match=message):
        preflight.validate(values)
