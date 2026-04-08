"""Regression tests for the rich-monitor scheduling bug.

Postings whose board has ``metadata.scraper_type = 'skip'`` (rich monitor,
no enrichment) must never be scheduled for scraping — otherwise the
placeholder ``skip`` scraper raises ``RuntimeError``. This test module
covers each of the guards that prevent those postings from re-entering
the scrape loop:

1. ``_is_skip_no_scrape`` — the shared classifier.
2. ``_enqueue_scrapes_for_new`` / ``_enqueue_scrapes_for_relisted`` —
   enqueue sites in ``processing/board.py``.
3. ``_INSERT_URL_ONLY_JOBS`` — the SQL now takes an ``is_rich_no_scrape``
   flag and leaves ``next_scrape_at`` NULL for rich boards.
4. ``_process_scrape_work`` — the Redis-driven worker in
   ``workers/pipeline.py`` clears Postgres and drops the task instead of
   invoking the skip scraper when stale tasks arrive.

See ``dev/browser-errors/01-rich-monitor-scheduling.md``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.processing.board import (
    _enqueue_scrapes_for_new,
    _enqueue_scrapes_for_relisted,
)
from src.processing.scrape import _is_skip_no_scrape
from src.queries.monitor import _INSERT_URL_ONLY_JOBS
from src.queries.scrape import _CLEAR_SCRAPE_FOR_RICH
from src.redis_queue import ScrapeWork
from src.workers.pipeline import _process_scrape_work

# ── _is_skip_no_scrape ──────────────────────────────────────────────────


class TestIsSkipNoScrape:
    def test_explicit_skip_without_enrich(self):
        assert _is_skip_no_scrape({"scraper_type": "skip"}) is True

    def test_explicit_skip_with_enrich(self):
        metadata = {
            "scraper_type": "skip",
            "scraper_config": {"enrich": ["description"]},
        }
        assert _is_skip_no_scrape(metadata) is False

    def test_explicit_skip_empty_enrich_list(self):
        """Empty enrich list still counts as no-enrich (board delivers everything)."""
        metadata = {
            "scraper_type": "skip",
            "scraper_config": {"enrich": []},
        }
        assert _is_skip_no_scrape(metadata) is True

    def test_other_scraper_type(self):
        assert _is_skip_no_scrape({"scraper_type": "json-ld"}) is False

    def test_missing_scraper_type(self):
        # No explicit type → caller should fall through to auto-resolution.
        assert _is_skip_no_scrape({}) is False

    def test_scraper_config_not_dict(self):
        """Non-dict scraper_config should not raise."""
        metadata = {"scraper_type": "skip", "scraper_config": "bogus"}
        assert _is_skip_no_scrape(metadata) is True


# ── _enqueue_scrapes_for_* guards ───────────────────────────────────────


class TestEnqueueGuards:
    @patch("src.processing.board._enqueue_scrape", new_callable=AsyncMock)
    async def test_skip_no_scrape_does_not_enqueue_new(self, mock_enqueue):
        """Rich monitor (skip, no enrich) → no Redis enqueue for new postings."""
        inserted = [
            {"id": "jp-1", "source_url": "https://example.com/job/1"},
            {"id": "jp-2", "source_url": "https://example.com/job/2"},
        ]
        metadata = {"scraper_type": "skip"}
        log = MagicMock()

        await _enqueue_scrapes_for_new(inserted, "b-1", metadata, log)

        mock_enqueue.assert_not_awaited()

    @patch("src.processing.board._enqueue_scrape", new_callable=AsyncMock)
    async def test_skip_no_scrape_does_not_enqueue_relisted(self, mock_enqueue):
        """Rich monitor (skip, no enrich) → no Redis enqueue for relisted postings."""
        relisted = [
            {"id": "jp-3", "url": "https://example.com/job/3", "r2_hash": None},
        ]
        metadata = {"scraper_type": "skip"}
        log = MagicMock()

        await _enqueue_scrapes_for_relisted(relisted, "b-1", metadata, log)

        mock_enqueue.assert_not_awaited()

    @patch("src.processing.board._enqueue_scrape", new_callable=AsyncMock)
    async def test_skip_with_enrich_still_enqueues(self, mock_enqueue):
        """Enrich boards still need scrapes, even with scraper_type=skip."""
        inserted = [{"id": "jp-1", "source_url": "https://example.com/job/1"}]
        metadata = {
            "scraper_type": "skip",
            "scraper_config": {"enrich": ["description"]},
        }
        log = MagicMock()

        await _enqueue_scrapes_for_new(inserted, "b-1", metadata, log)

        mock_enqueue.assert_awaited_once()

    @patch("src.processing.board._enqueue_scrape", new_callable=AsyncMock)
    async def test_non_skip_board_enqueues(self, mock_enqueue):
        """Normal boards (json-ld, dom, …) still enqueue."""
        inserted = [{"id": "jp-1", "source_url": "https://example.com/job/1"}]
        metadata = {"scraper_type": "json-ld"}
        log = MagicMock()

        await _enqueue_scrapes_for_new(inserted, "b-1", metadata, log)

        mock_enqueue.assert_awaited_once()


# ── _INSERT_URL_ONLY_JOBS parameter ─────────────────────────────────────


class TestInsertUrlOnlyJobsSql:
    def test_sql_uses_case_for_next_scrape_at(self):
        """The SQL must gate ``next_scrape_at`` on the ``is_rich_no_scrape`` flag."""
        # Guard the exact shape: $4::boolean controls the CASE.
        assert "$4::boolean" in _INSERT_URL_ONLY_JOBS
        assert "CASE WHEN $4::boolean THEN NULL ELSE now() END" in _INSERT_URL_ONLY_JOBS


# ── _process_scrape_work defense in depth ──────────────────────────────


class TestProcessScrapeWorkSkipGuard:
    def _scrape_work(self) -> ScrapeWork:
        return ScrapeWork(
            posting_id="jp-1",
            source_url="https://example.com/job/1",
            board_id="b-1",
            description_r2_hash=None,
            scraper_needs_browser=False,
            scrape_interval_hours=24,
            scrape_step=0,
            domain="example.com",
        )

    def _mock_pool(self):
        pool = AsyncMock()
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 1")
        acq_cm = AsyncMock()
        acq_cm.__aenter__ = AsyncMock(return_value=conn)
        acq_cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq_cm)
        return pool, conn

    @patch("src.workers.pipeline.reschedule_task", new_callable=AsyncMock)
    @patch("src.workers.pipeline.claim_work", new_callable=AsyncMock)
    @patch("src.redis_queue.get_redis")
    async def test_skip_board_clears_postgres_and_drops_task(
        self, mock_get_redis, _mock_claim, mock_reschedule
    ):
        """A stale scrape task for a skip board clears Postgres and is dropped."""
        pool, conn = self._mock_pool()
        http = AsyncMock()

        # Redis returns a board config whose metadata says scraper_type=skip.
        redis = AsyncMock()
        redis.hgetall = AsyncMock(
            return_value={
                "metadata": json.dumps({"scraper_type": "skip"}),
                "crawler_type": "greenhouse",
            }
        )
        mock_get_redis.return_value = redis

        with patch(
            "src.processing.scrape._process_one_scrape", new_callable=AsyncMock
        ) as mock_scrape:
            log = MagicMock()
            await _process_scrape_work(log, self._scrape_work(), pool, http, browser=False)

            # The scraper must NOT be invoked.
            mock_scrape.assert_not_awaited()

        # Postgres next_scrape_at cleared for this posting.
        clear_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args and c.args[0] == _CLEAR_SCRAPE_FOR_RICH
        ]
        assert len(clear_calls) == 1
        assert clear_calls[0].args[1] == ["jp-1"]

        # No Redis reschedule — the task is dropped, draining the loop.
        mock_reschedule.assert_not_awaited()

    @patch("src.workers.pipeline.reschedule_task", new_callable=AsyncMock)
    @patch("src.workers.pipeline.claim_work", new_callable=AsyncMock)
    @patch("src.redis_queue.get_redis")
    async def test_skip_with_enrich_still_scrapes(
        self, mock_get_redis, _mock_claim, mock_reschedule
    ):
        """Enrich boards (scraper_type=skip + enrich config) still run the scraper."""
        pool, _conn = self._mock_pool()
        http = AsyncMock()

        redis = AsyncMock()
        redis.hgetall = AsyncMock(
            return_value={
                "metadata": json.dumps(
                    {
                        "scraper_type": "skip",
                        "scraper_config": {"enrich": ["description"]},
                    }
                ),
                "crawler_type": "greenhouse",
            }
        )
        mock_get_redis.return_value = redis

        with patch(
            "src.processing.scrape._process_one_scrape", new_callable=AsyncMock
        ) as mock_scrape:
            mock_scrape.return_value = (True, 0.1)
            log = MagicMock()
            await _process_scrape_work(log, self._scrape_work(), pool, http, browser=False)

            mock_scrape.assert_awaited_once()

        mock_reschedule.assert_awaited_once()

    @patch("src.workers.pipeline.reschedule_task", new_callable=AsyncMock)
    @patch("src.workers.pipeline.claim_work", new_callable=AsyncMock)
    @patch("src.redis_queue.get_redis")
    async def test_normal_board_scrapes_as_before(
        self, mock_get_redis, _mock_claim, mock_reschedule
    ):
        """Non-skip boards continue through the normal scrape path."""
        pool, _conn = self._mock_pool()
        http = AsyncMock()

        redis = AsyncMock()
        redis.hgetall = AsyncMock(
            return_value={
                "metadata": json.dumps({"scraper_type": "json-ld"}),
                "crawler_type": "dom",
            }
        )
        mock_get_redis.return_value = redis

        with patch(
            "src.processing.scrape._process_one_scrape", new_callable=AsyncMock
        ) as mock_scrape:
            mock_scrape.return_value = (True, 0.1)
            log = MagicMock()
            await _process_scrape_work(log, self._scrape_work(), pool, http, browser=False)

            mock_scrape.assert_awaited_once()

        mock_reschedule.assert_awaited_once()


# ── _FETCH_DUE_JOB_POSTINGS filter shape ───────────────────────────────


class TestFetchDuePostingsFilter:
    def test_query_excludes_skip_boards(self):
        """The fetch query must join job_board and skip 'skip' boards."""
        from src.queries.scrape import _FETCH_DUE_JOB_POSTINGS

        assert "JOIN job_board" in _FETCH_DUE_JOB_POSTINGS
        assert "jb.metadata->>'scraper_type' = 'skip'" in _FETCH_DUE_JOB_POSTINGS
        # COALESCE handles NULL scraper_config (jsonb ? returns NULL, not false).
        assert (
            "COALESCE(jb.metadata->'scraper_config' ? 'enrich', false)" in _FETCH_DUE_JOB_POSTINGS
        )
