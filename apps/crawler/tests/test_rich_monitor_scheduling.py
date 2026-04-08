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

    def test_missing_scraper_type_no_crawler_type(self):
        # No explicit type, no crawler_type hint → classifier cannot tell.
        assert _is_skip_no_scrape({}) is False

    def test_scraper_config_not_dict(self):
        """Non-dict scraper_config should not raise."""
        metadata = {"scraper_type": "skip", "scraper_config": "bogus"}
        assert _is_skip_no_scrape(metadata) is True

    # Implicit rich-monitor cases (no explicit scraper_type in metadata, but
    # crawler_type is in _RICH_MONITORS / _AUTO_SKIP_CRAWLER_TYPES).
    def test_implicit_greenhouse_is_skip(self):
        assert _is_skip_no_scrape({}, crawler_type="greenhouse") is True

    def test_implicit_lever_is_skip(self):
        assert _is_skip_no_scrape({}, crawler_type="lever") is True

    def test_implicit_rss_is_skip(self):
        assert _is_skip_no_scrape({}, crawler_type="rss") is True

    def test_implicit_amazon_is_skip(self):
        assert _is_skip_no_scrape({}, crawler_type="amazon") is True

    def test_implicit_oracle_hcm_is_NOT_skip(self):
        """oracle_hcm is in _RICH_MONITORS but auto-resolves to the oracle_hcm
        scraper with an enrich config — it DOES need scraping."""
        assert _is_skip_no_scrape({}, crawler_type="oracle_hcm") is False

    def test_implicit_workday_is_NOT_skip(self):
        """workday is URL-only: monitor returns URLs, workday scraper extracts."""
        assert _is_skip_no_scrape({}, crawler_type="workday") is False

    def test_implicit_dom_is_NOT_skip(self):
        assert _is_skip_no_scrape({}, crawler_type="dom") is False

    def test_implicit_rich_with_enrich_is_NOT_skip(self):
        """Enrich config overrides implicit rich classification."""
        metadata = {"scraper_config": {"enrich": ["description"]}}
        assert _is_skip_no_scrape(metadata, crawler_type="greenhouse") is False

    def test_explicit_non_skip_overrides_implicit(self):
        """If a rich board has an explicit non-skip scraper_type, honor it."""
        metadata = {"scraper_type": "dom"}
        assert _is_skip_no_scrape(metadata, crawler_type="greenhouse") is False


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

    def _mock_redis(self, hgetall_return):
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value=hgetall_return)
        redis.delete = AsyncMock(return_value=1)
        return redis

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
        redis = self._mock_redis(
            {
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

        # Redis scrape hash is also deleted (no orphan key left behind).
        redis.delete.assert_awaited_once_with("scrape:jp-1")

        # No Redis reschedule — the task is dropped, draining the loop.
        mock_reschedule.assert_not_awaited()

    @patch("src.workers.pipeline.reschedule_task", new_callable=AsyncMock)
    @patch("src.workers.pipeline.claim_work", new_callable=AsyncMock)
    @patch("src.redis_queue.get_redis")
    async def test_implicit_rich_board_is_dropped(
        self, mock_get_redis, _mock_claim, mock_reschedule
    ):
        """Board with rich crawler_type but no explicit scraper_type is dropped."""
        pool, conn = self._mock_pool()
        http = AsyncMock()

        redis = self._mock_redis(
            {
                # Metadata has NO scraper_type — relies on auto_scraper_type.
                "metadata": json.dumps({}),
                "crawler_type": "lever",
            }
        )
        mock_get_redis.return_value = redis

        with patch(
            "src.processing.scrape._process_one_scrape", new_callable=AsyncMock
        ) as mock_scrape:
            log = MagicMock()
            await _process_scrape_work(log, self._scrape_work(), pool, http, browser=False)

            mock_scrape.assert_not_awaited()

        clear_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args and c.args[0] == _CLEAR_SCRAPE_FOR_RICH
        ]
        assert len(clear_calls) == 1
        mock_reschedule.assert_not_awaited()

    @patch("src.workers.pipeline.reschedule_task", new_callable=AsyncMock)
    @patch("src.workers.pipeline.claim_work", new_callable=AsyncMock)
    @patch("src.redis_queue.get_redis")
    async def test_empty_board_config_drops_task(
        self, mock_get_redis, _mock_claim, mock_reschedule
    ):
        """Missing Redis board hash → drop the task (used to fail open).

        Previously, an empty ``board_config`` set ``scraper_type = "dom"`` and
        scraped with no config. Worse, the legacy fallback passed
        ``crawler_type`` as the scraper name, which raised ``KeyError`` for
        names like ``greenhouse`` that aren't registered scrapers. Now we
        drop the task the same way we drop rich stragglers.
        """
        pool, conn = self._mock_pool()
        http = AsyncMock()

        # Redis returns nothing for the board key.
        redis = self._mock_redis({})
        mock_get_redis.return_value = redis

        with patch(
            "src.processing.scrape._process_one_scrape", new_callable=AsyncMock
        ) as mock_scrape:
            log = MagicMock()
            await _process_scrape_work(log, self._scrape_work(), pool, http, browser=False)

            mock_scrape.assert_not_awaited()

        clear_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args and c.args[0] == _CLEAR_SCRAPE_FOR_RICH
        ]
        assert len(clear_calls) == 1
        redis.delete.assert_awaited_once_with("scrape:jp-1")
        mock_reschedule.assert_not_awaited()

    @patch("src.workers.pipeline.reschedule_task", new_callable=AsyncMock)
    @patch("src.workers.pipeline.claim_work", new_callable=AsyncMock)
    @patch("src.redis_queue.get_redis")
    async def test_malformed_metadata_json_treated_as_empty(
        self, mock_get_redis, _mock_claim, mock_reschedule
    ):
        """Corrupt ``metadata`` JSON should fall through to classifier with {}.

        Under the fix, a corrupt metadata combined with a rich ``crawler_type``
        still results in the task being dropped by the implicit-rich branch.
        """
        pool, conn = self._mock_pool()
        http = AsyncMock()

        redis = self._mock_redis(
            {
                "metadata": "{not json",
                "crawler_type": "greenhouse",
            }
        )
        mock_get_redis.return_value = redis

        with patch(
            "src.processing.scrape._process_one_scrape", new_callable=AsyncMock
        ) as mock_scrape:
            log = MagicMock()
            await _process_scrape_work(log, self._scrape_work(), pool, http, browser=False)

            mock_scrape.assert_not_awaited()

        clear_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args and c.args[0] == _CLEAR_SCRAPE_FOR_RICH
        ]
        assert len(clear_calls) == 1
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

        redis = self._mock_redis(
            {
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

        redis = self._mock_redis(
            {
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

    def test_query_covers_implicit_rich_crawler_types(self):
        """Implicit rich monitors (no scraper_type, rich crawler_type) are excluded."""
        from src.queries.scrape import _FETCH_DUE_JOB_POSTINGS
        from src.workspace._compat import auto_skip_crawler_types

        # Every crawler type that auto-resolves to skip must be in the SQL.
        for t in auto_skip_crawler_types():
            assert f"'{t}'" in _FETCH_DUE_JOB_POSTINGS, f"missing {t} in fetch filter"
        # Oracle HCM (rich monitor with enrich) must NOT be in the list.
        assert "'oracle_hcm'" not in _FETCH_DUE_JOB_POSTINGS


# ── _CLEAR_SCRAPE_FOR_RICH predicate scoping ──────────────────────────


class TestClearScrapeForRichPredicate:
    def test_clear_query_joins_job_board(self):
        """The UPDATE must join job_board and scope to rich-no-scrape boards."""
        from src.queries.scrape import _CLEAR_SCRAPE_FOR_RICH

        assert "FROM job_board jb" in _CLEAR_SCRAPE_FOR_RICH
        assert "jb.metadata->>'scraper_type' = 'skip'" in _CLEAR_SCRAPE_FOR_RICH
        # The predicate must use the shared builder so all guards stay in sync.
        assert "COALESCE(jb.metadata->'scraper_config' ? 'enrich', false)" in _CLEAR_SCRAPE_FOR_RICH

    def test_clear_query_respects_board_id_predicate(self):
        """The UPDATE must still be keyed by jp.id = ANY($1)."""
        from src.queries.scrape import _CLEAR_SCRAPE_FOR_RICH

        assert "jp.id = ANY($1::uuid[])" in _CLEAR_SCRAPE_FOR_RICH
        assert "jb.id = jp.board_id" in _CLEAR_SCRAPE_FOR_RICH


# ── Build info metric ─────────────────────────────────────────────────


class TestBuildInfoMetric:
    def test_version_read_matches_file(self):
        """``_read_version()`` returns the contents of ``apps/crawler/VERSION``.

        Added so SREs can confirm which VERSION each container is running
        from Grafana (``crawler_build_info{version="X"}``) without SSH-ing
        in. See SRE critic finding on the rich-monitor scheduling PR.
        """
        import pathlib

        from src.metrics import _read_version

        version_file = pathlib.Path(__file__).resolve().parent.parent / "VERSION"
        expected = version_file.read_text().strip()
        assert _read_version() == expected
        assert expected  # non-empty

    def test_build_info_metric_registered(self):
        """The gauge must exist in the prometheus registry before startup."""
        from src.metrics import build_info

        # Set + read back. The exact numeric value is 1; we're verifying
        # the label is accepted and the sample is emitted.
        build_info.labels(version="test").set(1)
        samples = list(build_info.collect())[0].samples
        assert any(s.labels.get("version") == "test" and s.value == 1.0 for s in samples)


# ── Predicate sync across Python, SQL filter, and backfill script ──────


class TestPredicateSyncAcrossLayers:
    def test_backfill_auto_skip_types_matches_compat(self):
        """The backfill script hardcodes the rich-monitor list; it must match
        the canonical ``workspace._compat._AUTO_SKIP_CRAWLER_TYPES``.
        """
        import importlib.util
        import pathlib

        script_path = (
            pathlib.Path(__file__).resolve().parent.parent.parent.parent
            / "scripts"
            / "backfill-clear-rich-scrape.py"
        )
        spec = importlib.util.spec_from_file_location("_backfill_rich", script_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        from src.workspace._compat import auto_skip_crawler_types

        assert frozenset(mod._AUTO_SKIP_CRAWLER_TYPES) == auto_skip_crawler_types()
