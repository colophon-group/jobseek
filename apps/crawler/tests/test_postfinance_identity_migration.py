"""Safety contracts for the PostFinance multi-board identity cutover."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.processing.board import (
    _IDENTITY_MIGRATION_MAX_ROWS,
    _POSTFINANCE_CANONICAL_URL_PATTERN,
    _POSTFINANCE_IDENTITY_MIGRATION,
    _POSTFINANCE_IDENTITY_MIGRATION_BOARD_SLUG,
    _POSTFINANCE_IDENTITY_MIGRATION_CONTRACT,
    _POSTFINANCE_IDENTITY_MIGRATION_VERSION,
    _POSTFINANCE_LEGACY_URL_PATTERN,
    _retire_canonicalized_provider_identities,
)
from src.queries.monitor import _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES
from src.sync import _monitor_config_fingerprint

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"
_BOARD_URL, _CRAWLER_TYPE, _FINGERPRINT = _POSTFINANCE_IDENTITY_MIGRATION_CONTRACT


def _metadata(**overrides) -> dict:
    metadata = {
        "identity_migration": _POSTFINANCE_IDENTITY_MIGRATION,
        "_monitor_config_fingerprint": _FINGERPRINT,
        "recent_discovered_counts": [26, 26, 26],
    }
    metadata.update(overrides)
    return metadata


def _canonical_urls(count: int = 26) -> set[str]:
    return {f"https://jobs.postfinance.ch/job/_/{1_420_000_000 + index}/" for index in range(count)}


def _row(**overrides) -> dict:
    row = {
        "active": 266,
        "legacy": 240,
        "canonical": 26,
        "unknown": 0,
        "discovered": 26,
        "validated": 26,
        "retired": 240,
        "receipt_written": True,
        "existing_receipt": None,
    }
    row.update(overrides)
    return row


async def _run(conn: AsyncMock, **overrides) -> tuple[int, MagicMock]:
    kwargs = {
        "board_id": "board-id",
        "company_id": "company-id",
        "board_slug": _POSTFINANCE_IDENTITY_MIGRATION_BOARD_SLUG,
        "board_url": _BOARD_URL,
        "crawler_type": _CRAWLER_TYPE,
        "monitor_start_ts": "2026-08-26T12:00:00+00:00",
        "metadata": _metadata(),
        "discovered": 26,
        "canonical_urls": _canonical_urls(),
        "truncated": False,
        "extraction_filtered": 283,
        "security_filtered": 0,
        "processing_filtered": 0,
        "all_canonical": True,
        "board_log": MagicMock(),
    }
    kwargs.update(overrides)
    return await _retire_canonicalized_provider_identities(conn, **kwargs), kwargs["board_log"]


def test_config_fingerprint_is_bound_to_reviewed_stable_feed_contract():
    with _BOARDS.open(newline="") as handle:
        row = next(
            row
            for row in csv.DictReader(handle)
            if row["board_slug"] == _POSTFINANCE_IDENTITY_MIGRATION_BOARD_SLUG
        )
    config = json.loads(row["monitor_config"])

    assert config["identity_migration"] == _POSTFINANCE_IDENTITY_MIGRATION
    assert (
        _monitor_config_fingerprint(row["board_url"], row["monitor_type"], config) == _FINGERPRINT
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://job.post.ch/default/job/Analyst/74398-de_DE",
        "https://job.post.ch/PostFinance/job/Analyste/74398-fr_FR",
        "https://job.post.ch/PostKG/job/Analista/74398-it_IT",
        "https://job.post.ch/search?locale=en_US&shortcut=redirect-jobs#searchResults",
        "https://career.post.ch/de",
        "https://www.post.ch/en/pages/footer/privacy-policy-for-job-applicants",
    ],
)
def test_legacy_contract_covers_reviewed_production_shapes(url):
    assert re.fullmatch(_POSTFINANCE_LEGACY_URL_PATTERN, url)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/default/job/Analyst/74398-de_DE",
        "https://job.post.ch/default/job/Analyst/not-numeric-de_DE",
        "https://job.post.ch/default/job/Analyst/74398-de_DE?locale=de_DE",
        "https://career.post.ch/fr",
        "https://www.post.ch/en/jobs/jobs",
        "https://jobs.postfinance.ch/job/_/1426471633/",
    ],
)
def test_legacy_contract_rejects_foreign_unknown_and_canonical_shapes(url):
    assert re.fullmatch(_POSTFINANCE_LEGACY_URL_PATTERN, url) is None


def test_atomic_sql_supports_explicit_company_scope_without_weakening_owner_binding():
    sql = " ".join(_RETIRE_CANONICALIZED_PROVIDER_IDENTITIES.split()).lower()

    assert "from company where id = $2 and slug = $9" in sql
    assert "posting.company_id = $2 and ($10::boolean or posting.board_id = $1)" in sql
    assert "classified.unknown_count = 0" in sql
    assert "count(validated_discoveries.id) = discovered_input_state.input_count" in sql
    assert "retired_state.retired_count = retired_state.legacy_count" in sql
    assert "'_identity_migration_receipt'" in sql


async def test_healthy_replacement_retires_all_removed_board_rows_company_wide():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row()

    retired, log = await _run(conn)

    assert retired == 240
    args = conn.fetchrow.await_args.args
    assert args[:5] == (
        _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES,
        "board-id",
        "company-id",
        "2026-08-26T12:00:00+00:00",
        _IDENTITY_MIGRATION_MAX_ROWS,
    )
    assert set(args[5]) == _canonical_urls()
    assert args[6:8] == (
        _POSTFINANCE_LEGACY_URL_PATTERN,
        _POSTFINANCE_CANONICAL_URL_PATTERN,
    )
    assert json.loads(args[8]) == {
        "id": _POSTFINANCE_IDENTITY_MIGRATION,
        "version": _POSTFINANCE_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
    }
    assert args[9:] == ("postfinance", True)
    log.info.assert_called_once_with(
        "batch.monitor.identity_migration_completed",
        migration=_POSTFINANCE_IDENTITY_MIGRATION,
        retired=240,
    )


@pytest.mark.parametrize(
    ("overrides", "metadata"),
    [
        ({"board_slug": "postfinance-careers-fr"}, _metadata()),
        ({"board_slug": "swiss-post-main"}, _metadata()),
        ({"board_url": "https://evil.example/jobs"}, _metadata()),
        ({"crawler_type": "dom"}, _metadata()),
        ({}, _metadata(_monitor_config_fingerprint="wrong")),
        ({}, _metadata(identity_migration="postfinance-swiss-post-stable-id-v2")),
    ],
)
async def test_copied_marker_or_wrong_contract_never_enters_sql(overrides, metadata):
    conn = AsyncMock()

    retired, _ = await _run(conn, metadata=metadata, **overrides)

    assert retired == 0
    conn.fetchrow.assert_not_awaited()


async def test_unknown_company_row_blocks_cleanup_and_receipt():
    conn = AsyncMock()
    conn.fetchrow.return_value = _row(
        unknown=1,
        retired=0,
        receipt_written=False,
    )

    retired, log = await _run(conn)

    assert retired == 0
    log.warning.assert_called_once_with(
        "batch.monitor.identity_migration_blocked",
        migration=_POSTFINANCE_IDENTITY_MIGRATION,
        active=266,
        legacy=240,
        canonical=26,
        unknown=1,
        discovered=26,
        validated=26,
        cap=_IDENTITY_MIGRATION_MAX_ROWS,
    )
