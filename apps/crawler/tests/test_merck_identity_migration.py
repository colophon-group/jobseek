"""One-shot safety contracts for the Merck provider-identity migration."""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.processing.board import (
    _IDENTITY_MIGRATION_MAX_ROWS,
    _MERCK_CANONICAL_URL_PATTERN,
    _MERCK_IDENTITY_MIGRATION,
    _MERCK_IDENTITY_MIGRATION_CONTRACTS,
    _MERCK_IDENTITY_MIGRATION_VERSION,
    _MERCK_LEGACY_URL_PATTERN,
    _retire_canonicalized_provider_identities,
)
from src.queries.monitor import _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES

_BOARD_SLUG = "merck-de-de"
_BOARD_URL, _CRAWLER_TYPE, _FINGERPRINT = _MERCK_IDENTITY_MIGRATION_CONTRACTS[_BOARD_SLUG]


def _metadata(**overrides) -> dict:
    metadata = {
        "identity_migration": _MERCK_IDENTITY_MIGRATION,
        "_monitor_config_fingerprint": _FINGERPRINT,
        "recent_discovered_counts": [826, 829, 825],
    }
    metadata.update(overrides)
    return metadata


def _receipt(**overrides) -> dict:
    receipt = {
        "id": _MERCK_IDENTITY_MIGRATION,
        "version": _MERCK_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
        "completed_at": "2026-08-25T12:00:00+00:00",
        "retired_count": 1_221,
    }
    receipt.update(overrides)
    return receipt


def _row(**overrides) -> dict:
    row = {
        "active": 1_221,
        "legacy": 1_221,
        "canonical": 0,
        "unknown": 0,
        "discovered": 826,
        "validated": 826,
        "retired": 1_221,
        "receipt_written": True,
        "existing_receipt": None,
    }
    row.update(overrides)
    return row


def _canonical_urls(count: int = 826) -> set[str]:
    return {f"https://careers.emdgroup.com/us/en/job/{300_000 + index}" for index in range(count)}


async def _run(conn: AsyncMock, **overrides) -> tuple[int, MagicMock]:
    kwargs = {
        "board_id": "board-id",
        "company_id": "company-id",
        "board_slug": _BOARD_SLUG,
        "board_url": _BOARD_URL,
        "crawler_type": _CRAWLER_TYPE,
        "monitor_start_ts": "2026-08-25T12:00:00+00:00",
        "metadata": _metadata(),
        "discovered": 826,
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


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.merckgroup.com/de/de/job/300535/Original-title",
        "https://careers.merckgroup.com/global/en/job/300535/",
        "https://careers.merckgroup.com/jp/ja/job/303138",
    ],
)
def test_legacy_contract_accepts_only_reviewed_locale_provider_urls(url):
    match = re.fullmatch(_MERCK_LEGACY_URL_PATTERN, url)
    assert match is not None
    assert match.group(2).isdigit()


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.emdgroup.com/us/en/job/300535",
        "https://jobs.merck.com/us/en/job/300535/MSD-role",
        "https://evil.example/de/de/job/300535/Injected",
        "https://careers.merckgroup.com/us/en/job/300535/Bad-locale",
        "https://careers.merckgroup.com/de/fr/job/300535/Bad-pair",
        "https://careers.merckgroup.com/de/de/job/not-numeric/Role",
        "https://careers.merckgroup.com/de/de/job/300535/Role?query=bad",
        "https://careers.merckgroup.com/de/de/job/300535/Role#fragment",
        "https://careers.merckgroup.com/de/de/job/300535/Role/extra",
    ],
)
def test_legacy_contract_rejects_canonical_msd_evil_and_malformed_urls(url):
    assert re.fullmatch(_MERCK_LEGACY_URL_PATTERN, url) is None


def test_canonical_contract_is_exact_origin_path_and_numeric_id():
    assert re.fullmatch(
        _MERCK_CANONICAL_URL_PATTERN,
        "https://careers.emdgroup.com/us/en/job/300535",
    )
    for invalid in (
        "https://evil.example/us/en/job/300535",
        "https://careers.emdgroup.com/us/en/job/not-numeric",
        "https://careers.emdgroup.com/us/en/job/300535/slug",
        "https://careers.emdgroup.com/us/en/job/300535?query=bad",
    ):
        assert re.fullmatch(_MERCK_CANONICAL_URL_PATTERN, invalid) is None


def test_atomic_sql_binds_company_board_current_cycle_classification_cap_and_receipt():
    sql = " ".join(_RETIRE_CANONICALIZED_PROVIDER_IDENTITIES.split()).lower()

    assert "from company where id = $2 and slug = $9" in sql
    assert "($10::boolean or posting.board_id = $1)" in sql
    assert "posting.company_id = $2" in sql
    assert "from unnest($5::text[])" in sql
    assert "posting.company_id = $2" in sql
    assert "posting.last_seen_at >= $3" in sql
    assert "posting.source_url ~ $7" in sql
    assert "discovered_input_state.unique_count = discovered_input_state.input_count" in sql
    assert "count(validated_discoveries.id) = discovered_input_state.input_count" in sql
    assert "classified.unknown_count = 0" in sql
    assert "classified.legacy_count <= $4" in sql
    assert "active_sources.source_kind = 'legacy'" in sql
    assert "'_identity_migration_receipt'" in sql
    assert "retired_state.retired_count = retired_state.legacy_count" in sql


async def test_healthy_exact_transition_retires_before_writing_receipt():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row()

    retired, log = await _run(conn)

    assert retired == 1_221
    args = conn.fetchrow.await_args.args
    assert args[:5] == (
        _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES,
        "board-id",
        "company-id",
        "2026-08-25T12:00:00+00:00",
        _IDENTITY_MIGRATION_MAX_ROWS,
    )
    assert set(args[5]) == _canonical_urls()
    assert args[6] == _MERCK_LEGACY_URL_PATTERN
    assert args[7] == _MERCK_CANONICAL_URL_PATTERN
    assert json.loads(args[8]) == {
        "id": _MERCK_IDENTITY_MIGRATION,
        "version": _MERCK_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
    }
    assert args[9:] == ("merck", False)
    log.info.assert_called_once_with(
        "batch.monitor.identity_migration_completed",
        migration=_MERCK_IDENTITY_MIGRATION,
        retired=1_221,
    )


async def test_three_live_canonicals_validate_before_all_five_legacy_rows_retire():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row(
        active=5,
        legacy=5,
        discovered=3,
        validated=3,
        retired=5,
    )

    retired, _ = await _run(
        conn,
        discovered=3,
        canonical_urls=_canonical_urls(3),
        metadata=_metadata(recent_discovered_counts=[3, 3, 3]),
    )

    assert retired == 5


@pytest.mark.parametrize(
    ("overrides", "metadata"),
    [
        ({"board_slug": "merck-us-en"}, _metadata()),
        ({"board_slug": "msd-north-america"}, _metadata()),
        ({"board_slug": "another-board"}, _metadata()),
        ({"board_url": "https://evil.example/jobs"}, _metadata()),
        ({"crawler_type": "dom"}, _metadata()),
        ({}, _metadata(_monitor_config_fingerprint="wrong")),
        ({}, _metadata(identity_migration="merck-phenom-stable-id-v2")),
    ],
)
async def test_copied_marker_or_wrong_contract_never_enters_sql(overrides, metadata):
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
        ({"discovered": 827}, "nonunique_canonical_output"),
        (
            {
                "discovered": _IDENTITY_MIGRATION_MAX_ROWS + 1,
                "canonical_urls": _canonical_urls(_IDENTITY_MIGRATION_MAX_ROWS + 1),
            },
            "canonical_set_over_cap",
        ),
        ({"metadata": _metadata(recent_discovered_counts=[826, 829])}, "insufficient_history"),
        ({"discovered": 100, "canonical_urls": _canonical_urls(100)}, "drop"),
    ],
)
async def test_unhealthy_or_partial_cycle_never_enters_sql(overrides, reason):
    conn = AsyncMock()

    retired, log = await _run(conn, **overrides)

    assert retired == 0
    conn.fetchrow.assert_not_awaited()
    assert log.warning.call_args.kwargs["reason"] == reason


async def test_expected_nonjob_extraction_noise_does_not_block_migration():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row()

    retired, _ = await _run(conn, extraction_filtered=63, processing_filtered=0)

    assert retired == 1_221


@pytest.mark.parametrize(
    "row",
    [
        _row(legacy=2_001, retired=0, receipt_written=False),
        _row(unknown=1, retired=0, receipt_written=False),
        _row(validated=825, retired=0, receipt_written=False),
    ],
)
async def test_over_cap_unclassified_or_canonical_absent_fails_closed(row):
    conn = AsyncMock()
    conn.fetchrow.return_value = row

    retired, log = await _run(conn)

    assert retired == 0
    log.warning.assert_called_once()
    assert log.warning.call_args.args[0] == "batch.monitor.identity_migration_blocked"


async def test_exact_metadata_receipt_is_permanent_noop_even_if_new_legacy_row_appears():
    conn = AsyncMock()
    metadata = _metadata(_identity_migration_receipt=_receipt())

    first, _ = await _run(conn, metadata=metadata)
    second, _ = await _run(conn, metadata=metadata)

    assert (first, second) == (0, 0)
    conn.fetchrow.assert_not_awaited()


@pytest.mark.parametrize(
    "receipt",
    [
        _receipt(id="other-migration"),
        _receipt(version=2),
        _receipt(config_fingerprint="wrong"),
        _receipt(retired_count=_IDENTITY_MIGRATION_MAX_ROWS + 1),
        {"id": _MERCK_IDENTITY_MIGRATION},
    ],
)
async def test_mismatched_metadata_receipt_fails_closed(receipt):
    conn = AsyncMock()

    retired, log = await _run(
        conn,
        metadata=_metadata(_identity_migration_receipt=receipt),
    )

    assert retired == 0
    conn.fetchrow.assert_not_awaited()
    assert log.warning.call_args.args[0] == "batch.monitor.identity_migration_receipt_mismatch"


async def test_stale_schedule_replay_honors_exact_database_receipt():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row(
        active=0,
        legacy=0,
        discovered=0,
        validated=0,
        retired=0,
        receipt_written=False,
        existing_receipt=_receipt(),
    )

    retired, _ = await _run(conn)

    assert retired == 0


async def test_mismatched_database_receipt_fails_closed():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row(
        active=0,
        legacy=0,
        discovered=0,
        validated=0,
        retired=0,
        receipt_written=False,
        existing_receipt=_receipt(version=2),
    )

    retired, log = await _run(conn)

    assert retired == 0
    assert log.warning.call_args.args[0] == "batch.monitor.identity_migration_receipt_mismatch"


async def test_receipt_write_with_inexact_retirement_raises_for_transaction_rollback():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row(retired=1_220)

    with pytest.raises(RuntimeError, match="without exact retirement"):
        await _run(conn)
