"""Tests for backfill_descriptions (#2996).

The helper resets ``next_scrape_at = now()`` for rich-monitor postings
whose description is missing because the board's enrich config flipped
AFTER the rows were first inserted. Without this backfill, scraper-config
fixes shipped in PRs #2947, #2953, #2954, #2961, #2962, #2964, #2967,
#2968, #2970, #2971, #2972 only affect future inserts — existing rows
stay stuck behind ``_INSERT_RICH_JOB``'s NULL ``next_scrape_at`` and
never re-enter the scrape pipeline.

The complementary self-heal in ``_DIFF_BATCH`` (also #2996) prevents the
bug for FUTURE config flips.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.backfill import (
    _COUNT_DESCRIPTIONS_CANDIDATES,
    _DEFAULT_STUCK_DESCRIPTION_SLUGS,
    _PROMOTE_DESCRIPTIONS_BATCH,
    backfill_descriptions,
)


def _row(id_, source_url="https://example.com/job/1", board_id="board-1", r2_hash=None):
    """Mock asyncpg.Record-like dict for the promote query result."""
    data = {
        "id": id_,
        "source_url": source_url,
        "board_id": board_id,
        "description_r2_hash": r2_hash,
    }
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


@pytest.fixture
def mock_pool():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


# ── SQL constants ────────────────────────────────────────────────────


class TestSqlConstants:
    """Pin the SQL shape so refactors don't silently widen the WHERE clause."""

    def test_promote_filters_stuck_rows(self):
        sql_compact = " ".join(_PROMOTE_DESCRIPTIONS_BATCH.split())
        # The whole point of the helper: only stuck rows.
        assert "is_active = true" in sql_compact
        assert "description_r2_hash IS NULL" in sql_compact
        assert "next_scrape_at IS NULL" in sql_compact

    def test_promote_supports_slug_filter(self):
        sql_compact = " ".join(_PROMOTE_DESCRIPTIONS_BATCH.split())
        # Optional ANY filter — empty/NULL falls through to "all stuck".
        assert "$1::text[] IS NULL OR c.slug = ANY($1::text[])" in sql_compact

    def test_promote_returns_enqueue_columns(self):
        sql_compact = " ".join(_PROMOTE_DESCRIPTIONS_BATCH.split())
        # Caller needs id, source_url, board_id, description_r2_hash to
        # construct the Redis enqueue payload.
        assert "RETURNING jp.id::text, jp.source_url, jp.board_id::text" in sql_compact
        assert "jp.description_r2_hash" in sql_compact

    def test_promote_orders_by_id_for_progress(self):
        # ORDER BY id keeps each batch deterministic so the WHERE clause
        # narrows monotonically as rows get next_scrape_at set.
        sql_compact = " ".join(_PROMOTE_DESCRIPTIONS_BATCH.split())
        assert "ORDER BY jp.id" in sql_compact

    def test_count_query_mirrors_promote_filter(self):
        promote_compact = " ".join(_PROMOTE_DESCRIPTIONS_BATCH.split())
        count_compact = " ".join(_COUNT_DESCRIPTIONS_CANDIDATES.split())
        # The count and the promote query must share the same WHERE
        # clause — otherwise dry_run's number is a lie.
        for clause in (
            "is_active = true",
            "description_r2_hash IS NULL",
            "next_scrape_at IS NULL",
            "$1::text[] IS NULL OR c.slug = ANY($1::text[])",
        ):
            assert clause in promote_compact
            assert clause in count_compact


# ── Default slug list ────────────────────────────────────────────────


class TestDefaultSlugList:
    """Default 20 slugs match #2996's audit — DiDi (#2997) and NEURA
    (#2998) are excluded. A regression in this list silently changes the
    operator-side semantic (the CLI defaults to ``--slug``-less
    invocation)."""

    def test_default_slug_count(self):
        assert len(_DEFAULT_STUCK_DESCRIPTION_SLUGS) == 20

    def test_default_excludes_didi_and_neura(self):
        # Per #2996: didi-global has a disabled board, NEURA's scrape-
        # side is already healthy — both must be excluded from default.
        assert "didi-global" not in _DEFAULT_STUCK_DESCRIPTION_SLUGS
        assert "neura-robotics" not in _DEFAULT_STUCK_DESCRIPTION_SLUGS

    def test_default_includes_all_in_scope_slugs(self):
        expected = {
            "alibaba",
            "ayuda-en-accion",
            "bajaj-finserv",
            "barcelona-activa",
            "continental",
            "decathlon",
            "goldman-sachs",
            "haier-group",
            "hitachi-energy",
            "infineon",
            "itau-unibanco",
            "larsen-toubro",
            "loreal",
            "meta",
            "netflix",
            "nokia",
            "terveystalo",
            "tesla",
            "texas-instruments",
            "zte",
        }
        assert set(_DEFAULT_STUCK_DESCRIPTION_SLUGS) == expected


# ── dry_run path ─────────────────────────────────────────────────────


class TestDryRun:
    @patch("src.backfill.get_redis")
    async def test_dry_run_returns_count_without_writes(self, mock_get_redis, mock_pool):
        """dry_run=True must call fetchval (COUNT) and not pool.fetch (UPDATE)."""
        mock_pool.fetchval = AsyncMock(return_value=42)
        n = await backfill_descriptions(mock_pool, dry_run=True)
        assert n == 42
        # The UPDATE must not run in dry_run.
        mock_pool.fetch.assert_not_called()
        # And we must not have touched Redis (no enqueues).
        mock_get_redis.assert_not_called()

    @patch("src.backfill.get_redis")
    async def test_dry_run_passes_default_slugs(self, mock_get_redis, mock_pool):
        """When ``company_slugs`` is None, the COUNT query gets the default 20."""
        mock_pool.fetchval = AsyncMock(return_value=0)
        await backfill_descriptions(mock_pool, dry_run=True)
        mock_pool.fetchval.assert_called_once()
        sql, slugs = mock_pool.fetchval.call_args.args
        assert sql == _COUNT_DESCRIPTIONS_CANDIDATES
        assert slugs == list(_DEFAULT_STUCK_DESCRIPTION_SLUGS)

    @patch("src.backfill.get_redis")
    async def test_dry_run_with_explicit_slugs(self, mock_get_redis, mock_pool):
        mock_pool.fetchval = AsyncMock(return_value=7)
        n = await backfill_descriptions(mock_pool, company_slugs=["tesla", "nokia"], dry_run=True)
        assert n == 7
        sql, slugs = mock_pool.fetchval.call_args.args
        assert slugs == ["tesla", "nokia"]

    @patch("src.backfill.get_redis")
    async def test_dry_run_with_empty_list_means_all_stuck(self, mock_get_redis, mock_pool):
        """An empty list is the documented escape hatch for 'all rows'.

        ``slugs=None`` in the SQL bypasses the slug filter — useful for
        operator one-shots that want to clean every stuck row regardless
        of company. This is intentionally separate from the default
        20-company semantic that ``company_slugs=None`` selects.
        """
        mock_pool.fetchval = AsyncMock(return_value=99)
        await backfill_descriptions(mock_pool, company_slugs=[], dry_run=True)
        sql, slugs = mock_pool.fetchval.call_args.args
        assert slugs is None  # SQL falls through to "all rows"

    async def test_only_missing_false_raises(self, mock_pool):
        """Future-proof guard: only_missing=False is reserved for later."""
        with pytest.raises(NotImplementedError):
            await backfill_descriptions(mock_pool, only_missing=False)


# ── enqueue path ─────────────────────────────────────────────────────


class TestEnqueuePath:
    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_enqueues_each_promoted_row(self, mock_enqueue, mock_get_redis, mock_pool):
        """A non-dry_run call must enqueue once per row returned from the UPDATE."""
        # First call returns 2 rows, second call returns empty (loop exits).
        rows = [
            _row("id-1", "https://example.com/job/1", "b-1"),
            _row("id-2", "https://example.com/job/2", "b-1"),
        ]
        mock_pool.fetch = AsyncMock(side_effect=[rows, []])

        # Redis hgetall for board:b-1 — empty hash means needs_browser=False.
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        mock_get_redis.return_value = redis

        # enqueue_scrape returns truthy → counts toward the total.
        mock_enqueue.return_value = True

        n = await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert n == 2
        assert mock_enqueue.await_count == 2

    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_dedup_does_not_inflate_count(self, mock_enqueue, mock_get_redis, mock_pool):
        """enqueue_scrape returning False (dedup'd) must not be counted."""
        rows = [
            _row("id-1", "https://example.com/job/1", "b-1"),
            _row("id-2", "https://example.com/job/2", "b-1"),
        ]
        mock_pool.fetch = AsyncMock(side_effect=[rows, []])
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        mock_get_redis.return_value = redis
        # First enqueue succeeds, second is dedup'd.
        mock_enqueue.side_effect = [True, False]

        n = await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert n == 1

    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_browser_board_routes_to_browser_queue(
        self, mock_enqueue, mock_get_redis, mock_pool
    ):
        """Board hash with scraper_needs_browser=1 → enqueue with browser=True."""
        rows = [_row("id-1", "https://example.com/job/1", "b-browser")]
        mock_pool.fetch = AsyncMock(side_effect=[rows, []])
        redis = AsyncMock()
        # Redis returns "1" for needs_browser.
        redis.hgetall = AsyncMock(return_value={"scraper_needs_browser": "1"})
        mock_get_redis.return_value = redis
        mock_enqueue.return_value = True

        await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert mock_enqueue.await_count == 1
        # browser kwarg should be True
        kwargs = mock_enqueue.await_args.kwargs
        assert kwargs.get("browser") is True
        # first_time False → tier-2 priority (lowest).
        assert kwargs.get("first_time") is False

    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_loops_until_fetch_returns_empty(self, mock_enqueue, mock_get_redis, mock_pool):
        """The promote loop must keep going until pool.fetch returns []."""
        # Three batches: 2, 1, 0 rows.
        batch1 = [
            _row("id-1", "https://example.com/job/1", "b-1"),
            _row("id-2", "https://example.com/job/2", "b-1"),
        ]
        batch2 = [_row("id-3", "https://example.com/job/3", "b-1")]
        mock_pool.fetch = AsyncMock(side_effect=[batch1, batch2, []])
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        mock_get_redis.return_value = redis
        mock_enqueue.return_value = True

        n = await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert n == 3
        assert mock_pool.fetch.await_count == 3
