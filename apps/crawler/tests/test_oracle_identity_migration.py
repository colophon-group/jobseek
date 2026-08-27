"""Safety contracts for Oracle's in-place provider identity cutover."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.processing.board import (
    _IDENTITY_MIGRATION_MAX_ROWS,
    _ORACLE_CANONICAL_URL_PATTERN,
    _ORACLE_IDENTITY_MIGRATION,
    _ORACLE_IDENTITY_MIGRATION_BOARD_SLUG,
    _ORACLE_IDENTITY_MIGRATION_CONTRACT,
    _ORACLE_IDENTITY_MIGRATION_MAX_ROWS,
    _ORACLE_IDENTITY_MIGRATION_VERSION,
    _ORACLE_LEGACY_URL_PATTERN,
    _retire_canonicalized_provider_identities,
)
from src.queries.monitor import _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES
from src.sync import _monitor_config_fingerprint

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"
_BOARD_URL, _CRAWLER_TYPE, _FINGERPRINT = _ORACLE_IDENTITY_MIGRATION_CONTRACT
_DISCOVERED = 2_166
_LEGACY = 2_162


def _metadata(**overrides) -> dict:
    metadata = {
        "identity_migration": _ORACLE_IDENTITY_MIGRATION,
        "_monitor_config_fingerprint": _FINGERPRINT,
        "recent_discovered_counts": [_LEGACY, _LEGACY, _LEGACY],
    }
    metadata.update(overrides)
    return metadata


def _canonical_urls(count: int = _DISCOVERED) -> set[str]:
    return {
        "https://eeho.fa.us2.oraclecloud.com/hcmUI/"
        f"CandidateExperience/en/sites/CX_45001/job/{330_000 + index}"
        for index in range(count)
    }


def _receipt(**overrides) -> dict:
    receipt = {
        "id": _ORACLE_IDENTITY_MIGRATION,
        "version": _ORACLE_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
        "completed_at": "2026-08-27T12:00:00+00:00",
        "retired_count": _LEGACY,
    }
    receipt.update(overrides)
    return receipt


def _row(**overrides) -> dict:
    row = {
        "active": _DISCOVERED + _LEGACY,
        "legacy": _LEGACY,
        "canonical": _DISCOVERED,
        "unknown": 0,
        "discovered": _DISCOVERED,
        "validated": _DISCOVERED,
        "retired": _LEGACY,
        "receipt_written": True,
        "existing_receipt": None,
    }
    row.update(overrides)
    return row


async def _run(conn: AsyncMock, **overrides) -> tuple[int, MagicMock]:
    kwargs = {
        "board_id": "oracle-board-id",
        "company_id": "oracle-company-id",
        "board_slug": _ORACLE_IDENTITY_MIGRATION_BOARD_SLUG,
        "board_url": _BOARD_URL,
        "crawler_type": _CRAWLER_TYPE,
        "monitor_start_ts": "2026-08-27T12:00:00+00:00",
        "metadata": _metadata(),
        "discovered": _DISCOVERED,
        "canonical_urls": _canonical_urls(),
        "truncated": False,
        "extraction_filtered": 0,
        "security_filtered": 0,
        "processing_filtered": 0,
        "all_canonical": True,
        "board_log": MagicMock(),
    }
    kwargs.update(overrides)
    return await _retire_canonicalized_provider_identities(conn, **kwargs), kwargs["board_log"]


def test_config_fingerprint_is_bound_to_exact_reviewed_oracle_hcm_contract():
    with _BOARDS.open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "oracle"]

    assert len(rows) == 1
    row = rows[0]
    config = json.loads(row["monitor_config"])
    assert row["board_slug"] == _ORACLE_IDENTITY_MIGRATION_BOARD_SLUG
    assert config["identity_migration"] == _ORACLE_IDENTITY_MIGRATION
    assert (
        _monitor_config_fingerprint(row["board_url"], row["monitor_type"], config) == _FINGERPRINT
    )


def test_only_exact_oracle_board_can_copy_the_marker():
    with _BOARDS.open(newline="") as handle:
        marked = {
            (row["company_slug"], row["board_slug"])
            for row in csv.DictReader(handle)
            if json.loads(row["monitor_config"] or "{}").get("identity_migration")
            == _ORACLE_IDENTITY_MIGRATION
        }

    assert marked == {("oracle", _ORACLE_IDENTITY_MIGRATION_BOARD_SLUG)}


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.oracle.com/en/job/342458",
        "https://careers.oracle.com/en/job/1",
    ],
)
def test_legacy_contract_accepts_exact_old_catalogue_shape(url):
    assert re.fullmatch(_ORACLE_LEGACY_URL_PATTERN, url)


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.oracle.com/en/job/not-numeric",
        "https://careers.oracle.com/fr/job/342458",
        "https://careers.oracle.com/en/job/342458?utm_source=google",
        "https://careers.oracle.com/en/job/342458/extra",
        "https://evil.example/en/job/342458",
        (
            "https://eeho.fa.us2.oraclecloud.com/hcmUI/"
            "CandidateExperience/en/sites/CX_45001/job/342458"
        ),
    ],
)
def test_legacy_contract_rejects_canonical_foreign_and_malformed_shapes(url):
    assert re.fullmatch(_ORACLE_LEGACY_URL_PATTERN, url) is None


def test_canonical_contract_is_exact_tenant_site_path_and_numeric_id():
    valid = (
        "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_45001/job/342458"
    )
    assert re.fullmatch(_ORACLE_CANONICAL_URL_PATTERN, valid)
    for invalid in (
        valid + "?source=oracle",
        valid + "/extra",
        valid.replace("/en/", "/fr/"),
        valid.replace("CX_45001", "CX_45002"),
        valid.replace("eeho.fa.us2", "evil.fa.us2"),
        valid.replace("342458", "not-numeric"),
    ):
        assert re.fullmatch(_ORACLE_CANONICAL_URL_PATTERN, invalid) is None


def test_oracle_gets_a_spec_owned_cap_without_weakening_existing_migrations():
    assert _IDENTITY_MIGRATION_MAX_ROWS == 2_000
    assert _LEGACY < _ORACLE_IDENTITY_MIGRATION_MAX_ROWS == 2_500


async def test_healthy_exact_transition_retires_all_legacy_rows_and_writes_receipt():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row()

    retired, log = await _run(conn)

    assert retired == _LEGACY
    args = conn.fetchrow.await_args.args
    assert args[:5] == (
        _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES,
        "oracle-board-id",
        "oracle-company-id",
        "2026-08-27T12:00:00+00:00",
        _ORACLE_IDENTITY_MIGRATION_MAX_ROWS,
    )
    assert set(args[5]) == _canonical_urls()
    assert args[6:8] == (_ORACLE_LEGACY_URL_PATTERN, _ORACLE_CANONICAL_URL_PATTERN)
    assert json.loads(args[8]) == {
        "id": _ORACLE_IDENTITY_MIGRATION,
        "version": _ORACLE_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
    }
    assert args[9:] == ("oracle", False)
    log.info.assert_called_once_with(
        "batch.monitor.identity_migration_completed",
        migration=_ORACLE_IDENTITY_MIGRATION,
        retired=_LEGACY,
    )


@pytest.mark.parametrize(
    ("overrides", "metadata"),
    [
        ({"board_slug": "oracle-careers-copy"}, _metadata()),
        ({"board_slug": "another-company-oracle"}, _metadata()),
        ({"board_url": "https://evil.example/jobs"}, _metadata()),
        ({"crawler_type": "sitemap"}, _metadata()),
        ({}, _metadata(_monitor_config_fingerprint="wrong")),
        ({}, _metadata(identity_migration="oracle-hcm-stable-id-v2")),
    ],
)
async def test_copied_marker_or_wrong_exact_contract_never_enters_sql(overrides, metadata):
    conn = AsyncMock()

    retired, _ = await _run(conn, metadata=metadata, **overrides)

    assert retired == 0
    conn.fetchrow.assert_not_awaited()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"discovered": 0}, "empty"),
        ({"truncated": True}, "truncated"),
        ({"security_filtered": 1}, "security_filtered"),
        ({"processing_filtered": 1}, "processing_filtered"),
        ({"all_canonical": False}, "noncanonical_output"),
        ({"canonical_urls": set()}, "empty_canonical_set"),
        ({"discovered": _DISCOVERED + 1}, "nonunique_canonical_output"),
        (
            {
                "discovered": _ORACLE_IDENTITY_MIGRATION_MAX_ROWS + 1,
                "canonical_urls": _canonical_urls(_ORACLE_IDENTITY_MIGRATION_MAX_ROWS + 1),
            },
            "canonical_set_over_cap",
        ),
        (
            {"metadata": _metadata(recent_discovered_counts=[_LEGACY, _LEGACY])},
            "insufficient_history",
        ),
        ({"discovered": 100, "canonical_urls": _canonical_urls(100)}, "drop"),
    ],
)
async def test_unhealthy_or_partial_cycle_never_enters_sql(overrides, reason):
    conn = AsyncMock()

    retired, log = await _run(conn, **overrides)

    assert retired == 0
    conn.fetchrow.assert_not_awaited()
    assert log.warning.call_args.kwargs["reason"] == reason


@pytest.mark.parametrize(
    "row",
    [
        _row(legacy=_ORACLE_IDENTITY_MIGRATION_MAX_ROWS + 1, retired=0, receipt_written=False),
        _row(unknown=1, retired=0, receipt_written=False),
        _row(validated=_DISCOVERED - 1, retired=0, receipt_written=False),
    ],
)
async def test_over_cap_unknown_or_unvalidated_database_state_fails_closed(row):
    conn = AsyncMock()
    conn.fetchrow.return_value = row

    retired, log = await _run(conn)

    assert retired == 0
    assert log.warning.call_args.args[0] == "batch.monitor.identity_migration_blocked"
    assert log.warning.call_args.kwargs["cap"] == _ORACLE_IDENTITY_MIGRATION_MAX_ROWS


async def test_exact_receipt_is_permanent_noop_but_mismatched_receipt_fails_closed():
    conn = AsyncMock()

    retired, _ = await _run(conn, metadata=_metadata(_identity_migration_receipt=_receipt()))
    assert retired == 0
    conn.fetchrow.assert_not_awaited()

    retired, log = await _run(
        conn,
        metadata=_metadata(_identity_migration_receipt=_receipt(config_fingerprint="wrong")),
    )
    assert retired == 0
    conn.fetchrow.assert_not_awaited()
    assert log.warning.call_args.args[0] == "batch.monitor.identity_migration_receipt_mismatch"


async def test_receipt_write_with_inexact_retirement_raises_for_transaction_rollback():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row(retired=_LEGACY - 1)

    with pytest.raises(RuntimeError, match="without exact retirement"):
        await _run(conn)
