"""Tests for the retry-stalled-scrapes operator CLI (#2738)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.retry_stalled import (
    _COUNT_STALLED,
    _PROMOTE_STALLED_BATCH,
    count_stalled_scrapes,
    retry_stalled_scrapes,
)


def _row(
    posting_id: str,
    source_url: str,
    board_id: str = "board-1",
    description_r2_hash: str | None = "abc123",
):
    """Build a fake asyncpg Record-like row."""
    return {
        "id": posting_id,
        "source_url": source_url,
        "board_id": board_id,
        "description_r2_hash": description_r2_hash,
    }


def _make_pool(batches: list[list[dict]]) -> AsyncMock:
    """Build an AsyncMock pool whose ``fetch`` returns one batch per call.

    Empty list signals end-of-loop in the production code.
    """
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=batches + [[]])
    return pool


class TestCountStalled:
    @pytest.mark.asyncio
    async def test_dry_run_returns_count(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=42)
        out = await count_stalled_scrapes(pool, max_age_days=7)
        assert out == 42
        pool.fetchval.assert_awaited_once_with(_COUNT_STALLED, 7)

    @pytest.mark.asyncio
    async def test_dry_run_zero(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=0)
        out = await count_stalled_scrapes(pool, max_age_days=14)
        assert out == 0


class TestRetryStalledScrapes:
    @pytest.mark.asyncio
    async def test_no_candidates_returns_zero(self, monkeypatch):
        """Empty batch -> loop exits, no enqueues."""
        pool = _make_pool([])

        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value={})
        monkeypatch.setattr("src.retry_stalled.get_redis", lambda: mock_redis)

        enqueue_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("src.retry_stalled.enqueue_scrape", enqueue_mock)

        out = await retry_stalled_scrapes(pool, max_age_days=7)
        assert out == 0
        assert enqueue_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_enqueues_each_row(self, monkeypatch):
        """Each row in a batch produces one ``enqueue_scrape`` call.

        The dedup return value (True for new, False for already-queued)
        controls the final ``enqueued`` counter.
        """
        rows = [
            _row("p1", "https://example.com/jobs/1"),
            _row("p2", "https://example.com/jobs/2"),
            _row("p3", "https://example.com/jobs/3"),
        ]
        pool = _make_pool([rows])

        mock_redis = AsyncMock()
        # No board cache hit — empty config → not a browser board.
        mock_redis.hgetall = AsyncMock(return_value={})
        monkeypatch.setattr("src.retry_stalled.get_redis", lambda: mock_redis)

        enqueue_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("src.retry_stalled.enqueue_scrape", enqueue_mock)

        out = await retry_stalled_scrapes(pool, max_age_days=7)
        assert out == 3
        assert enqueue_mock.await_count == 3

        # First call: positional args + kwargs match the production
        # contract (domain, id, ts, payload, browser=False, first_time=False).
        first_call = enqueue_mock.await_args_list[0]
        assert first_call.args[0] == "example.com"  # domain
        assert first_call.args[1] == "p1"
        assert first_call.kwargs["browser"] is False
        assert first_call.kwargs["first_time"] is False
        payload = first_call.args[3]
        assert payload["source_url"] == "https://example.com/jobs/1"
        assert payload["board_id"] == "board-1"
        assert payload["description_r2_hash"] == "abc123"
        assert payload["scrape_step"] == "0"

    @pytest.mark.asyncio
    async def test_already_queued_does_not_count(self, monkeypatch):
        """``enqueue_scrape`` returns False on dedup (already queued);
        those rows don't increment the ``enqueued`` counter even though
        the UPDATE has already flipped ``next_scrape_at``."""
        rows = [_row("p1", "https://x/1"), _row("p2", "https://x/2")]
        pool = _make_pool([rows])

        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value={})
        monkeypatch.setattr("src.retry_stalled.get_redis", lambda: mock_redis)

        # Both calls return False — already queued.
        enqueue_mock = AsyncMock(return_value=False)
        monkeypatch.setattr("src.retry_stalled.enqueue_scrape", enqueue_mock)

        out = await retry_stalled_scrapes(pool, max_age_days=7)
        assert out == 0
        assert enqueue_mock.await_count == 2  # both attempted

    @pytest.mark.asyncio
    async def test_loops_until_empty(self, monkeypatch):
        """Multi-batch run: keep fetching until an empty batch arrives."""
        batch_1 = [_row(f"p{i}", f"https://x/{i}") for i in range(3)]
        batch_2 = [_row(f"p{i}", f"https://x/{i}") for i in range(3, 5)]
        pool = _make_pool([batch_1, batch_2])

        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value={})
        monkeypatch.setattr("src.retry_stalled.get_redis", lambda: mock_redis)

        enqueue_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("src.retry_stalled.enqueue_scrape", enqueue_mock)

        out = await retry_stalled_scrapes(pool, max_age_days=7)
        assert out == 5
        # 3 fetches: batch_1, batch_2, empty.
        assert pool.fetch.await_count == 3

    @pytest.mark.asyncio
    async def test_browser_board_routing(self, monkeypatch):
        """A board whose ``board:<id>`` Redis hash has
        ``scraper_needs_browser=1`` routes the scrape to the browser
        tier — verified by inspecting the ``browser=True`` kwarg."""
        rows = [_row("p1", "https://x/1", board_id="board-browser")]
        pool = _make_pool([rows])

        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value={"scraper_needs_browser": "1"})
        monkeypatch.setattr("src.retry_stalled.get_redis", lambda: mock_redis)

        enqueue_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("src.retry_stalled.enqueue_scrape", enqueue_mock)

        await retry_stalled_scrapes(pool, max_age_days=7)
        first_call = enqueue_mock.await_args_list[0]
        assert first_call.kwargs["browser"] is True

    @pytest.mark.asyncio
    async def test_board_cache_avoids_repeated_redis_hgetall(self, monkeypatch):
        """Multiple postings on the same board hit Redis once, not N times."""
        rows = [_row(f"p{i}", f"https://x/{i}", board_id="board-1") for i in range(5)]
        pool = _make_pool([rows])

        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value={})
        monkeypatch.setattr("src.retry_stalled.get_redis", lambda: mock_redis)

        enqueue_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("src.retry_stalled.enqueue_scrape", enqueue_mock)

        await retry_stalled_scrapes(pool, max_age_days=7)
        # Five postings on one board -> one hgetall.
        assert mock_redis.hgetall.await_count == 1

    @pytest.mark.asyncio
    async def test_passes_max_age_days_to_query(self, monkeypatch):
        """``max_age_days`` is the first positional arg to ``pool.fetch``."""
        pool = _make_pool([])

        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value={})
        monkeypatch.setattr("src.retry_stalled.get_redis", lambda: mock_redis)
        monkeypatch.setattr("src.retry_stalled.enqueue_scrape", AsyncMock())

        await retry_stalled_scrapes(pool, max_age_days=30)
        first_call = pool.fetch.await_args_list[0]
        # _PROMOTE_STALLED_BATCH is positional arg 0; max_age_days is 1.
        assert first_call.args[1] == 30

    @pytest.mark.asyncio
    async def test_null_r2_hash_passes_empty_string(self, monkeypatch):
        """``description_r2_hash`` may be NULL (transient-3-strike where
        every scrape failed before the first successful R2 upload). The
        enqueue payload normalises NULL to empty string so downstream
        ``_stage_r2_pending`` doesn't see the literal ``"None"`` string.
        """
        rows = [_row("p1", "https://x/1", description_r2_hash=None)]
        pool = _make_pool([rows])

        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value={})
        monkeypatch.setattr("src.retry_stalled.get_redis", lambda: mock_redis)

        enqueue_mock = AsyncMock(return_value=True)
        monkeypatch.setattr("src.retry_stalled.enqueue_scrape", enqueue_mock)

        await retry_stalled_scrapes(pool, max_age_days=7)
        payload = enqueue_mock.await_args_list[0].args[3]
        assert payload["description_r2_hash"] == ""


class TestSqlContract:
    """Lock the SQL predicates that distinguish transient-3-strike from
    other ``next_scrape_at IS NULL`` states. A future maintainer changing
    the criteria should update the test deliberately."""

    def test_promote_query_targets_transient_3_strike(self):
        sql = _PROMOTE_STALLED_BATCH
        assert "is_active = true" in sql
        assert "next_scrape_at IS NULL" in sql
        assert "scrape_failures >= 3" in sql
        assert "last_scraped_at IS NOT NULL" in sql
        # Age cutoff is parameterised — paranoia against accidentally
        # hardcoding the 7-day default into the query.
        assert "$1::int * interval '1 day'" in sql
        # UPDATE-RETURNING shape: emit the columns enqueue_scrape needs.
        assert "RETURNING" in sql
        assert "jp.id" in sql
        assert "jp.source_url" in sql
        assert "jp.board_id" in sql
        assert "jp.description_r2_hash" in sql

    def test_count_query_uses_same_predicates(self):
        """Dry-run count must report exactly what the UPDATE would touch."""
        promote = _PROMOTE_STALLED_BATCH
        count = _COUNT_STALLED
        for predicate in (
            "is_active = true",
            "next_scrape_at IS NULL",
            "scrape_failures >= 3",
            "last_scraped_at IS NOT NULL",
            "$1::int * interval '1 day'",
        ):
            assert predicate in promote, f"PROMOTE missing {predicate!r}"
            assert predicate in count, f"COUNT missing {predicate!r}"
