"""Tests for experience field reprocessing (#3289)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.backfill import (
    _FETCH_EXPERIENCE_REPROCESS_CANDIDATES,
    _SUSPECT_EXPERIENCE_REPROCESS_RE,
    _UPDATE_EXPERIENCE_REPROCESS_BATCH,
    reprocess_experience,
)


def _row(
    id_: str,
    *,
    experience_min=None,
    experience_max=None,
    descriptions: list[str] | None = None,
):
    data = {
        "id": id_,
        "experience_min": experience_min,
        "experience_max": experience_max,
        "descriptions": descriptions or [],
    }
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


@pytest.fixture
def mock_pool():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock(return_value="UPDATE 0")
    return pool


class TestExperienceReprocessSql:
    def test_fetch_query_scopes_to_active_described_postings(self):
        sql = " ".join(_FETCH_EXPERIENCE_REPROCESS_CANDIDATES.split())
        assert "jp.is_active = true" in sql
        assert "EXISTS ( SELECT 1 FROM descriptions d" in sql
        assert "JOIN descriptions d ON d.posting_id = candidate.id" in sql
        assert "d.html IS NOT NULL" in sql
        assert "length(trim(d.html)) > 0" in sql

    def test_fetch_query_uses_keyset_pagination(self):
        sql = " ".join(_FETCH_EXPERIENCE_REPROCESS_CANDIDATES.split())
        assert "$1::uuid IS NULL OR jp.id > $1::uuid" in sql
        assert "ORDER BY jp.id" in sql
        assert "LIMIT $4" in sql
        assert "OFFSET" not in sql

    def test_fetch_query_supports_slug_and_suspect_filters(self):
        sql = " ".join(_FETCH_EXPERIENCE_REPROCESS_CANDIDATES.split())
        assert "$2::text[] IS NULL OR c.slug = ANY($2::text[])" in sql
        assert "$3::boolean = false" in sql
        assert "jp.experience_min IS NULL" in sql
        assert "jp.experience_min = 5" in sql

    def test_suspect_regex_covers_months_and_decimal_years(self):
        assert _SUSPECT_EXPERIENCE_REPROCESS_RE.search("8 months of experience")
        assert _SUSPECT_EXPERIENCE_REPROCESS_RE.search("1.5 years of experience")
        assert _SUSPECT_EXPERIENCE_REPROCESS_RE.search("1.5+ years of experience")
        assert _SUSPECT_EXPERIENCE_REPROCESS_RE.search("1,5 Jahre Erfahrung")
        assert not _SUSPECT_EXPERIENCE_REPROCESS_RE.search("5 years of experience")

    def test_update_query_only_touches_changed_rows(self):
        sql = " ".join(_UPDATE_EXPERIENCE_REPROCESS_BATCH.split())
        assert "experience_min = u.experience_min" in sql
        assert "experience_max = u.experience_max" in sql
        assert "updated_at = now()" in sql
        assert "IS DISTINCT FROM" in sql


class TestExperienceReprocess:
    async def test_dry_run_counts_actual_month_changes_without_writes(self, mock_pool):
        row = _row(
            "00000000-0000-0000-0000-000000000001",
            experience_min=None,
            descriptions=["<li>8 months of professional experience</li>"],
        )
        mock_pool.fetch = AsyncMock(side_effect=[[row], []])

        summary = await reprocess_experience(mock_pool, dry_run=True)

        assert summary.scanned_postings == 1
        assert summary.changed_postings == 1
        assert summary.updated_postings == 0
        mock_pool.execute.assert_not_called()

    async def test_skips_rows_without_concrete_extracted_requirement(self, mock_pool):
        row = _row(
            "00000000-0000-0000-0000-000000000001",
            experience_min=None,
            descriptions=[
                "<p>The internship lasts 3 months.</p><p>Experience with Python is useful.</p>"
            ],
        )
        mock_pool.fetch = AsyncMock(side_effect=[[row], []])

        summary = await reprocess_experience(mock_pool, dry_run=False)

        assert summary.scanned_postings == 1
        assert summary.changed_postings == 0
        assert summary.updated_postings == 0
        mock_pool.execute.assert_not_called()

    async def test_skips_rows_when_stored_value_already_matches(self, mock_pool):
        row = _row(
            "00000000-0000-0000-0000-000000000001",
            experience_min=0.7,
            descriptions=["<li>8 months of professional experience</li>"],
        )
        mock_pool.fetch = AsyncMock(side_effect=[[row], []])

        summary = await reprocess_experience(mock_pool, dry_run=False)

        assert summary.changed_postings == 0
        mock_pool.execute.assert_not_called()

    async def test_updates_decimal_year_requirement(self, mock_pool):
        row = _row(
            "00000000-0000-0000-0000-000000000001",
            experience_min=5,
            descriptions=["<li>At least 1.5+ years of engineering experience</li>"],
        )
        mock_pool.fetch = AsyncMock(side_effect=[[row], []])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        summary = await reprocess_experience(mock_pool, dry_run=False)

        assert summary.changed_postings == 1
        assert summary.updated_postings == 1
        sql, ids, mins, maxes = mock_pool.execute.await_args.args
        assert sql == _UPDATE_EXPERIENCE_REPROCESS_BATCH
        assert ids == [UUID("00000000-0000-0000-0000-000000000001")]
        assert mins == [Decimal("1.5")]
        assert maxes == [None]

    async def test_default_suspect_mode_skips_non_month_integer_years(self, mock_pool):
        row = _row(
            "00000000-0000-0000-0000-000000000001",
            experience_min=None,
            descriptions=["<li>5 years of engineering experience</li>"],
        )
        mock_pool.fetch = AsyncMock(side_effect=[[row], []])

        summary = await reprocess_experience(mock_pool, dry_run=False)

        assert summary.changed_postings == 0
        mock_pool.execute.assert_not_called()

    async def test_all_candidates_mode_reprocesses_integer_years(self, mock_pool):
        row = _row(
            "00000000-0000-0000-0000-000000000001",
            experience_min=None,
            descriptions=["<li>5 years of engineering experience</li>"],
        )
        mock_pool.fetch = AsyncMock(side_effect=[[row], []])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        summary = await reprocess_experience(mock_pool, only_suspect=False, dry_run=False)

        assert summary.changed_postings == 1
        assert summary.updated_postings == 1

    async def test_uses_highest_requirement_across_descriptions(self, mock_pool):
        row = _row(
            "00000000-0000-0000-0000-000000000001",
            experience_min=None,
            descriptions=[
                "<li>6 months of professional experience</li>",
                "<li>1.5 years of engineering experience</li>",
            ],
        )
        mock_pool.fetch = AsyncMock(side_effect=[[row], []])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        await reprocess_experience(mock_pool, dry_run=False)

        _, _, mins, _ = mock_pool.execute.await_args.args
        assert mins == [Decimal("1.5")]

    async def test_limit_caps_changed_rows(self, mock_pool):
        rows = [
            _row(
                "00000000-0000-0000-0000-000000000001",
                descriptions=["<li>8 months of professional experience</li>"],
            ),
            _row(
                "00000000-0000-0000-0000-000000000002",
                descriptions=["<li>18 months of professional experience</li>"],
            ),
        ]
        mock_pool.fetch = AsyncMock(side_effect=[rows, []])
        mock_pool.execute = AsyncMock(return_value="UPDATE 1")

        summary = await reprocess_experience(mock_pool, dry_run=False, limit=1)

        assert summary.changed_postings == 1
        assert summary.updated_postings == 1
        _, ids, mins, _ = mock_pool.execute.await_args.args
        assert ids == [UUID("00000000-0000-0000-0000-000000000001")]
        assert mins == [Decimal("0.7")]

    async def test_fetch_passes_slugs_suspect_flag_and_keyset_id(self, mock_pool):
        row = _row(
            "00000000-0000-0000-0000-000000000001",
            descriptions=["<li>8 months of professional experience</li>"],
        )
        mock_pool.fetch = AsyncMock(side_effect=[[row], []])

        await reprocess_experience(
            mock_pool,
            company_slugs=["acme"],
            only_suspect=False,
            dry_run=True,
            batch_size=25,
        )

        first_call = mock_pool.fetch.await_args_list[0].args
        second_call = mock_pool.fetch.await_args_list[1].args
        assert first_call == (
            _FETCH_EXPERIENCE_REPROCESS_CANDIDATES,
            None,
            ["acme"],
            False,
            25,
        )
        assert second_call == (
            _FETCH_EXPERIENCE_REPROCESS_CANDIDATES,
            "00000000-0000-0000-0000-000000000001",
            ["acme"],
            False,
            25,
        )
