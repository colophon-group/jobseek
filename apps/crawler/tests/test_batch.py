from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.batch import (
    _BATCH_UPDATE_RICH_CONTENT,
    _CREATE_RICH_UPDATES_TEMP,
    _DELIST_BOARD_POSTINGS,
    _DIFF_BATCH,
    _INSERT_RICH_JOB,
    _INSERT_RICH_JOB_ENRICH,
    _INSERT_URL_ONLY_JOBS,
    _RECORD_EMPTY_CHECK,
    _RECORD_FAILURE,
    _RECORD_SCRAPE_FAILURE,
    _RECORD_SCRAPE_SUCCESS,
    _UPDATE_ENRICH_CONTENT,
    _UPDATE_JOB_CONTENT,
    _UPDATE_METADATA,
    _UPSERT_DESCRIPTION,
    BatchResult,
    BoardScraperConfig,
    ScrapeItem,
    _board_has_enrich,
    _coerce_datetime,
    _coerce_text,
    _get_next_fallback,
    _get_scraper_at_step,
    _jsonb,
    _load_board_scrapers,
    _merge_fields,
    _monitor_pipeline,
    _PipelineResult,
    _process_one_board_streaming,
    _process_one_enrich_scrape,
    _process_one_scrape,
    _scrape_pipeline,
    _throttle_key,
    process_monitor_batch,
    process_scrape_batch,
)
from src.core.location_resolve import LocationResolver
from src.core.monitor import MonitorResult
from src.core.monitors import DiscoveredJob, api_monitor_types
from src.core.scrapers import JobContent
from src.processing.board import DeadlineExtender


@pytest.fixture(autouse=True)
def _mock_location_resolver(monkeypatch):
    """Auto-mock the location resolver so batch tests don't hit the DB."""
    resolver = LocationResolver()
    resolver._init_db(":memory:")
    resolver._loaded = True

    async def _fake_get_resolver(pool):
        return resolver

    monkeypatch.setattr("src.batch._get_location_resolver", _fake_get_resolver)


@pytest.fixture(autouse=True)
def _mock_currency_rates(monkeypatch):
    """Auto-mock currency rates so batch tests don't hit the DB."""
    rates = {"EUR": 1.0, "USD": 0.87, "GBP": 1.17, "CHF": 1.04}

    async def _fake_get_rates(pool):
        return rates

    monkeypatch.setattr("src.batch._get_currency_rates", _fake_get_rates)


@pytest.fixture(autouse=True)
def _mock_enqueue_scrapes(monkeypatch):
    """Auto-mock scrape enqueue so board tests don't hit Redis."""
    monkeypatch.setattr("src.processing.board._enqueue_scrapes_for_new", AsyncMock())
    monkeypatch.setattr("src.processing.board._enqueue_scrapes_for_relisted", AsyncMock())


class TestJsonb:
    def test_with_dict(self):
        assert _jsonb({"key": "value"}) == '{"key": "value"}'

    def test_with_none(self):
        assert _jsonb(None) is None

    def test_with_nested(self):
        result = _jsonb({"a": [1, 2, 3]})
        assert json.loads(result) == {"a": [1, 2, 3]}

    def test_with_empty_dict(self):
        assert _jsonb({}) == "{}"


class TestTypeCoercion:
    def test_coerce_datetime_from_iso_z(self):
        parsed = _coerce_datetime("2026-02-17T16:12:35Z")
        assert parsed is not None
        assert parsed.isoformat() == "2026-02-17T16:12:35+00:00"

    def test_coerce_datetime_from_rfc2822(self):
        parsed = _coerce_datetime("Fri, 06 Feb 2026 16:31:18 +0400")
        assert parsed is not None
        assert parsed.isoformat() == "2026-02-06T16:31:18+04:00"

    def test_coerce_text_list_to_csv(self):
        assert (
            _coerce_text(["Temporary positions", "Full-time"]) == "Temporary positions, Full-time"
        )


class TestBatchResult:
    def test_defaults(self):
        r = BatchResult()
        assert r.processed == 0
        assert r.succeeded == 0
        assert r.failed == 0

    def test_custom_values(self):
        r = BatchResult(processed=10, succeeded=8, failed=2)
        assert r.processed == 10
        assert r.succeeded == 8
        assert r.failed == 2


class TestThrottleKey:
    def _board(self, **kw):
        defaults = {"crawler_type": "sitemap", "board_url": "https://example.com/jobs"}
        defaults.update(kw)
        record = MagicMock()
        record.__getitem__ = lambda self, key: defaults[key]
        return record

    def test_api_monitor_returns_type(self):
        board = self._board(crawler_type="greenhouse")
        assert _throttle_key(board) == "greenhouse"

    def test_all_api_types(self):
        for api_type in api_monitor_types():
            board = self._board(crawler_type=api_type)
            assert _throttle_key(board) == api_type

    def test_url_monitor_returns_hostname(self):
        board = self._board(crawler_type="sitemap", board_url="https://acme.com/jobs")
        assert _throttle_key(board) == "acme.com"

    def test_dom_monitor_returns_hostname(self):
        board = self._board(crawler_type="dom", board_url="https://bigcorp.com/careers")
        assert _throttle_key(board) == "bigcorp.com"

    def test_no_hostname_fallback(self):
        board = self._board(crawler_type="sitemap", board_url="not-a-url")
        assert _throttle_key(board) == "not-a-url"


# ── Shared helpers ───────────────────────────────────────────────────


def _mock_board(**overrides):
    """Create a dict-like mock for asyncpg.Record."""
    defaults = {
        "id": "board-1",
        "company_id": "company-1",
        "board_url": "https://example.com/jobs",
        "crawler_type": "greenhouse",
        "metadata": None,
    }
    defaults.update(overrides)
    record = MagicMock()
    record.__getitem__ = lambda self, key: defaults[key]
    return record


async def _process_one_board(board, pool, http):
    """Backward-compat wrapper: calls streaming path with a DeadlineExtender."""
    return await _process_one_board_streaming(board, pool, http, DeadlineExtender())


@pytest.fixture
def mock_pool():
    """Return (pool, conn) where pool.acquire() yields conn inside a transaction."""
    pool = AsyncMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool.fetch = AsyncMock(return_value=[])
    # monitor_start_ts for timestamp-based gone detection
    pool.fetchval = AsyncMock(return_value="2026-01-01T00:00:00+00:00")

    # pool.acquire() must be a sync call returning an async context manager
    acq_cm = AsyncMock()
    acq_cm.__aenter__ = AsyncMock(return_value=conn)
    acq_cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq_cm)

    # conn.transaction() must be a sync call returning an async context manager
    tx_cm = AsyncMock()
    tx_cm.__aenter__ = AsyncMock()
    tx_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_cm)

    return pool, conn


@pytest.fixture
def mock_http():
    return AsyncMock()


def _discovered_job(url="https://example.com/job/1", **kw):
    defaults = {
        "url": url,
        "title": "Engineer",
        "description": "<p>Great role</p>",
        "locations": ["NYC"],
        "employment_type": "FULL_TIME",
        "job_location_type": "onsite",
        "date_posted": "2025-01-01",
        "base_salary": None,
        "extras": None,
        "metadata": None,
    }
    defaults.update(kw)
    return DiscoveredJob(**defaults)


def _job_content(**kw):
    defaults = {
        "title": "Engineer",
        "description": "<p>Great role</p>",
        "locations": ["NYC"],
        "employment_type": "FULL_TIME",
        "job_location_type": "onsite",
        "date_posted": "2025-01-01",
        "base_salary": None,
        "extras": None,
        "metadata": None,
    }
    defaults.update(kw)
    return JobContent(**defaults)


def _diff_row(action, row_id=None, url="https://example.com/job/1", r2_hash=None):
    """Create a dict-like mock for a DIFF_URLS result row."""
    data = {"action": action, "id": row_id, "url": url, "description_r2_hash": r2_hash}
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


def _inserted_row(row_id, source_url):
    """Create a dict-like mock for an INSERT_URL_ONLY_JOBS result row."""
    data = {"id": row_id, "source_url": source_url}
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


def _mock_stream(*results):
    """Create an async generator that yields MonitorResults for monitor_one_stream mock."""

    async def _gen(*args, **kwargs):
        for r in results:
            yield r

    return _gen


def _counter_value(metric, **labels):
    """Read a Prometheus counter's current value via the public collect() API.

    Avoids reaching into ``counter._value.get()`` (private API that may
    break across ``prometheus-client`` upgrades, and ``pyproject.toml``
    only pins ``>=0.21``).
    """
    expected = labels
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith("_total") and sample.labels == expected:
                return sample.value
    return 0.0


# ── TestProcessOneBoard ──────────────────────────────────────────────


class TestProcessOneBoard:
    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_empty_result_records_empty_check(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Monitor returns empty urls -> _RECORD_EMPTY_CHECK called, no transaction."""
        pool, conn = mock_pool
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls=set()))
        # _RECORD_EMPTY_CHECK now uses RETURNING, so conn.fetch returns rows
        conn.fetch.return_value = [{"board_status": "active"}]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        conn.fetch.assert_awaited_once_with(_RECORD_EMPTY_CHECK, "board-1")
        mock_get_redis.assert_not_called()

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_empty_result_board_gone_delists_postings(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Board transitions to 'gone' after repeated empties -> delist all postings."""
        pool, conn = mock_pool
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls=set()))
        # Simulate board transitioning to gone
        conn.fetch.return_value = [{"board_status": "gone"}]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # Both _RECORD_EMPTY_CHECK (via fetch) and _DELIST_BOARD_POSTINGS (via execute) called
        conn.fetch.assert_awaited_once_with(_RECORD_EMPTY_CHECK, "board-1")
        conn.execute.assert_awaited_once_with(_DELIST_BOARD_POSTINGS, "board-1")

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_rich_data_inserts_new_jobs(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Monitor returns DiscoveredJobs -> executemany with _INSERT_RICH_JOB."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        url2 = "https://example.com/job/2"
        job1 = _discovered_job(url=url1)
        job2 = _discovered_job(url=url2, title="Designer")
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1, url2},
                jobs_by_url={url1: job1, url2: job2},
            )
        )
        # DIFF returns both as new
        conn.fetch.return_value = [
            _diff_row("new", url=url1),
            _diff_row("new", url=url2),
        ]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # fetchrow called with _INSERT_RICH_JOB for each new job
        conn.fetchrow.assert_awaited()
        call_args = conn.fetchrow.await_args_list
        assert any(c.args[0] == _INSERT_RICH_JOB for c in call_args)

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_rich_data_normalizes_escaped_description(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Escaped HTML from rich monitors is normalized before DB insert."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        job1 = _discovered_job(
            url=url1,
            description="&lt;p class=&quot;Lexical__paragraph&quot;&gt;Hello&lt;/p&gt;",
        )
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url={url1: job1},
            )
        )
        conn.fetch.return_value = [_diff_row("new", url=url1)]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        insert_calls = [c for c in conn.fetchrow.await_args_list if c.args[0] == _INSERT_RICH_JOB]
        assert len(insert_calls) == 1
        # Description is no longer in the INSERT (moved to R2).
        # Verify the job's description was normalized in-place for R2 upload.
        assert job1.description == "<p>Hello</p>"

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_url_only_inserts_stubs_for_scrape(
        self,
        mock_monitor,
        mock_get_redis,
        mock_pool,
        mock_http,
    ):
        """Monitor returns urls only (jobs_by_url=None) -> INSERT_URL_ONLY_JOBS."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls={url1}, jobs_by_url=None))
        conn.fetch.side_effect = [
            # First fetch call: DIFF_BATCH
            [_diff_row("new", url=url1)],
            # Second fetch call: INSERT_URL_ONLY_JOBS
            [_inserted_row("jp-1", url1)],
            # Third fetch call: MARK_GONE_BY_TIMESTAMP
            [],
        ]
        # Use a genuinely non-rich crawler_type. ``_mock_board()`` defaults to
        # greenhouse, which the new classifier correctly treats as implicit
        # rich-no-scrape.
        board = _mock_board(crawler_type="dom")

        await _process_one_board(board, pool, mock_http)

        # INSERT_URL_ONLY_JOBS was called (3 fetches: DIFF_BATCH, INSERT, MARK_GONE)
        assert conn.fetch.await_count == 3
        second_fetch = conn.fetch.await_args_list[1]
        assert second_fetch.args[0] == _INSERT_URL_ONLY_JOBS
        # Non-rich monitor → never_scrape flag is False, so next_scrape_at is set.
        assert second_fetch.args[4] is False

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_url_only_on_rich_crawler_type_keeps_next_scrape_null(
        self,
        mock_monitor,
        mock_get_redis,
        mock_pool,
        mock_http,
    ):
        """Rich crawler_type falling back to URL-only must NOT set next_scrape_at.

        This is the scenario the comment in ``_process_one_board_streaming``
        called out: a greenhouse/lever/etc monitor emits URLs-only for a
        cycle (e.g. transient API degradation). Before the fix,
        ``is_rich_no_scrape = is_rich and not enrich_fields`` was False
        because ``is_rich`` is False, so ``_INSERT_URL_ONLY_JOBS`` set
        ``next_scrape_at = now()`` and the postings re-entered the stuck
        cohort. After the fix, the metadata/crawler-type classifier kicks
        in and the insert keeps ``next_scrape_at = NULL``.
        """
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls={url1}, jobs_by_url=None))
        conn.fetch.side_effect = [
            [_diff_row("new", url=url1)],
            [_inserted_row("jp-1", url1)],
            [],
        ]
        # Rich crawler_type, no explicit scraper_type in metadata.
        board = _mock_board(crawler_type="greenhouse")

        await _process_one_board(board, pool, mock_http)

        second_fetch = conn.fetch.await_args_list[1]
        assert second_fetch.args[0] == _INSERT_URL_ONLY_JOBS
        # never_scrape must be True even though the runtime is_rich flag was False.
        assert second_fetch.args[4] is True

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_gone_jobs_in_diff(self, mock_monitor, mock_get_redis, mock_pool, mock_http):
        """DIFF_URLS returns 'gone' rows -> they trigger cache invalidation."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls={url1}, jobs_by_url=None))
        conn.fetch.return_value = [
            _diff_row("gone", row_id="jp-gone", url="https://example.com/old"),
        ]
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # Cache invalidation because there are gone jobs
        mock_redis.delete.assert_awaited_with("cache:platform-stats")

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_relisted_jobs_content_update(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Rich data with relisted rows -> bulk update via temp table."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        job1 = _discovered_job(url=url1)
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url={url1: job1},
            )
        )
        conn.fetch.return_value = [
            _diff_row("relisted", row_id="jp-relisted", url=url1),
        ]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        execute_calls = conn.execute.await_args_list
        assert any(c.args[0] == _CREATE_RICH_UPDATES_TEMP for c in execute_calls)
        assert any(c.args[0] == _BATCH_UPDATE_RICH_CONTENT for c in execute_calls)
        conn.copy_records_to_table.assert_awaited()

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_new_sitemap_url_updates_metadata(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """result.new_sitemap_url set -> _UPDATE_METADATA called."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url=None,
                new_sitemap_url="https://example.com/sitemap-jobs.xml",
            )
        )
        conn.fetch.return_value = []  # No diff changes
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # _UPDATE_METADATA was called in the transaction
        execute_calls = conn.execute.await_args_list
        metadata_calls = [c for c in execute_calls if c.args[0] == _UPDATE_METADATA]
        assert len(metadata_calls) == 1
        assert "sitemap-jobs.xml" in metadata_calls[0].args[2]

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_hybrid_partial_rich_falls_through_to_url_only(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Hybrid monitors return jobs_by_url with rich data for only some URLs.

        URLs in new_urls but NOT in jobs_by_url must fall through to
        _INSERT_URL_ONLY_JOBS so the scraper picks them up. URLs in
        jobs_by_url go through the rich insert path as usual.
        """
        pool, conn = mock_pool
        rich_url = "https://example.com/job/rich-1"
        stub_url = "https://example.com/job/stub-2"
        rich_job = _discovered_job(url=rich_url, title="Rich Job")
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={rich_url, stub_url},
                jobs_by_url={rich_url: rich_job},  # partial — stub_url absent
                hybrid=True,
            )
        )
        conn.fetch.side_effect = [
            # DIFF_BATCH: both new
            [_diff_row("new", url=rich_url), _diff_row("new", url=stub_url)],
            # _INSERT_URL_ONLY_JOBS for stub_url
            [_inserted_row("jp-stub", stub_url)],
            # _MARK_GONE_BY_TIMESTAMP
            [],
        ]
        conn.fetchrow.return_value = _inserted_row("jp-rich", rich_url)
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # Rich path: fetchrow called with _INSERT_RICH_JOB for rich_url
        rich_calls = [c for c in conn.fetchrow.await_args_list if c.args[0] == _INSERT_RICH_JOB]
        assert len(rich_calls) == 1
        assert rich_calls[0].args[4] == rich_url  # 4th positional arg is source_url

        # URL-only path: fetch called with _INSERT_URL_ONLY_JOBS for stub_url only
        url_only_calls = [
            c for c in conn.fetch.await_args_list if c.args[0] == _INSERT_URL_ONLY_JOBS
        ]
        assert len(url_only_calls) == 1
        assert url_only_calls[0].args[3] == [stub_url]  # 3rd positional is urls list

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_metadata_updates_merged_with_sitemap_url(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Both new_sitemap_url and metadata_updates go in a SINGLE _UPDATE_METADATA call."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url=None,
                new_sitemap_url="https://example.com/sitemap.xml",
                metadata_updates={"pcsx_watermark": {"max_ts": 12345, "enabled": True}},
            )
        )
        conn.fetch.return_value = []
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        metadata_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_METADATA]
        assert len(metadata_calls) == 1
        patch_json = metadata_calls[0].args[2]
        patch_dict = json.loads(patch_json)
        assert patch_dict["sitemap_url"] == "https://example.com/sitemap.xml"
        assert patch_dict["pcsx_watermark"]["max_ts"] == 12345
        assert patch_dict["pcsx_watermark"]["enabled"] is True

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_hybrid_skips_touched_content_update(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Hybrid monitors must NOT feed 'touched' jobs to _BATCH_UPDATE_RICH_CONTENT.

        That SQL uses plain SET (not COALESCE) for core fields, so feeding
        partial rich data would null out previously-scraped fields.
        Relisted jobs still flow through because they need fresh content.
        """
        pool, conn = mock_pool
        touched_url = "https://example.com/job/touched"
        touched_job = _discovered_job(url=touched_url, title="Partial Data")
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={touched_url},
                jobs_by_url={touched_url: touched_job},
                hybrid=True,
            )
        )
        conn.fetch.return_value = [
            _diff_row("touched", row_id="jp-touched", url=touched_url),
        ]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # _BATCH_UPDATE_RICH_CONTENT must NOT be called (nothing to update because
        # touched jobs are excluded when hybrid=True).
        rich_update_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _BATCH_UPDATE_RICH_CONTENT
        ]
        assert len(rich_update_calls) == 0
        create_temp_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _CREATE_RICH_UPDATES_TEMP
        ]
        assert len(create_temp_calls) == 0

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_hybrid_skips_relisted_content_update(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Hybrid monitors must NOT feed 'relisted' jobs to _BATCH_UPDATE_RICH_CONTENT
        either. That SQL uses plain SET (not COALESCE) for core fields, so PCSX's
        partial data (no employment_type, salary, experience) would null out the
        previously-scraped values. Relisted jobs get fresh content via the
        enrichment re-scrape path instead, which uses COALESCE-safe semantics."""
        pool, conn = mock_pool
        relisted_url = "https://example.com/job/back"
        relisted_job = _discovered_job(url=relisted_url)
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={relisted_url},
                jobs_by_url={relisted_url: relisted_job},
                hybrid=True,
            )
        )
        conn.fetch.return_value = [
            _diff_row("relisted", row_id="jp-relisted", url=relisted_url),
        ]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # Neither the temp table nor the bulk update runs for hybrid relisted.
        execute_calls = conn.execute.await_args_list
        rich_update_calls = [c for c in execute_calls if c.args[0] == _BATCH_UPDATE_RICH_CONTENT]
        create_temp_calls = [c for c in execute_calls if c.args[0] == _CREATE_RICH_UPDATES_TEMP]
        assert len(rich_update_calls) == 0, "hybrid must not touch relisted content"
        assert len(create_temp_calls) == 0

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_nonhybrid_relisted_still_updates_content(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Sanity check: non-hybrid rich monitors (greenhouse, lever, etc.) still
        go through the update path for relisted — they always return full rich
        data, so SET-based _BATCH_UPDATE_RICH_CONTENT is safe for them."""
        pool, conn = mock_pool
        relisted_url = "https://example.com/job/back"
        relisted_job = _discovered_job(url=relisted_url)
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={relisted_url},
                jobs_by_url={relisted_url: relisted_job},
                hybrid=False,  # traditional rich monitor
            )
        )
        conn.fetch.return_value = [
            _diff_row("relisted", row_id="jp-relisted", url=relisted_url),
        ]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        execute_calls = conn.execute.await_args_list
        assert any(c.args[0] == _BATCH_UPDATE_RICH_CONTENT for c in execute_calls)

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_error_records_failure(self, mock_monitor, mock_get_redis, mock_pool, mock_http):
        """monitor_one raises -> _RECORD_FAILURE called with truncated error."""
        pool, conn = mock_pool
        long_error = "x" * 1000
        mock_monitor.side_effect = RuntimeError(long_error)
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        failure_calls = [c for c in conn.execute.await_args_list if c.args[0] == _RECORD_FAILURE]
        assert len(failure_calls) == 1
        error_arg = failure_calls[0].args[2]
        assert len(error_arg) <= 500

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_error_records_exception_type_when_message_is_blank(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Exceptions with empty str() should still record useful error text."""
        pool, conn = mock_pool
        mock_monitor.side_effect = RuntimeError()
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        failure_calls = [c for c in conn.execute.await_args_list if c.args[0] == _RECORD_FAILURE]
        assert len(failure_calls) == 1
        assert failure_calls[0].args[2] == "RuntimeError"

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_stats_cache_invalidated_on_changes(
        self,
        mock_monitor,
        mock_get_redis,
        mock_pool,
        mock_http,
    ):
        """New or gone jobs -> get_redis().delete called."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls={url1}, jobs_by_url=None))
        conn.fetch.side_effect = [
            [_diff_row("new", url=url1)],
            [_inserted_row("jp-1", url1)],
            # MARK_GONE_BY_TIMESTAMP
            [],
        ]
        board = _mock_board()

        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis

        await _process_one_board(board, pool, mock_http)

        mock_redis.delete.assert_awaited_with("cache:platform-stats")

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_no_cache_invalidation_when_no_changes(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """No new/gone jobs -> get_redis().delete NOT called."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url={url1: _discovered_job(url=url1)},
            )
        )
        # Only existing active jobs, no new/gone/relisted
        conn.fetch.return_value = []
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        mock_get_redis.assert_not_called()


# ── TestIsPlausibleJobUrl ────────────────────────────────────────────


class TestIsPlausibleJobUrl:
    """Unit tests for the URL sanity check that drops site-root / bare-hash URLs."""

    def setup_method(self):
        from src.processing.board import _is_plausible_job_url

        self.is_plausible = _is_plausible_job_url

    def test_real_job_url_accepted(self):
        assert self.is_plausible("https://example.com/jobs/engineer-123")

    def test_real_job_url_with_query_accepted(self):
        assert self.is_plausible("https://apply.example.com/index.php?ac=jobad&id=130858")

    def test_bare_host_rejected(self):
        assert not self.is_plausible("https://krb-sjobs.brassring.com")

    def test_bare_host_with_slash_rejected(self):
        assert not self.is_plausible("https://krb-sjobs.brassring.com/")

    def test_bare_hash_rejected(self):
        # Fragments are stripped by urlparse from path; path = "/" → rejected.
        assert not self.is_plausible("https://krb-sjobs.brassring.com/#")

    def test_hash_with_fragment_rejected(self):
        assert not self.is_plausible("https://krb-sjobs.brassring.com/#0")

    def test_matches_board_homepage_path_rejected(self):
        assert not self.is_plausible(
            "https://example.com/careers",
            board_url="https://example.com/careers",
        )

    def test_matches_board_homepage_with_trailing_slash_rejected(self):
        assert not self.is_plausible(
            "https://example.com/careers/",
            board_url="https://example.com/careers",
        )

    def test_different_host_accepted_even_when_path_matches(self):
        assert self.is_plausible(
            "https://jobs.example.com/careers",
            board_url="https://example.com/careers",
        )

    def test_empty_string_rejected(self):
        assert not self.is_plausible("")

    def test_missing_scheme_rejected(self):
        assert not self.is_plausible("example.com/jobs/123")

    def test_invalid_url_rejected(self):
        assert not self.is_plausible("not a url at all")

    def test_case_insensitive_host_match(self):
        # Upper/lower mismatch in host must still trigger the homepage-path
        # rejection; real DOM extractors sometimes emit mixed-case hosts.
        assert not self.is_plausible(
            "https://Example.COM/careers",
            board_url="https://example.com/careers",
        )

    def test_query_string_on_board_path_accepted(self):
        # Query-keyed job URLs that share the board's own listing path
        # (e.g. Lufthansa ``index.php?ac=jobad&id=...``) must NOT be rejected.
        assert self.is_plausible(
            "https://apply.example.com/index.php?ac=jobad&id=130858",
            board_url="https://apply.example.com/index.php?ac=joblist",
        )

    def test_mailto_scheme_rejected(self):
        assert not self.is_plausible("mailto:jobs@example.com")


# ── TestClassifyJobUrl ───────────────────────────────────────────────


class TestClassifyJobUrl:
    """Reason codes feed the ``monitor_url_filtered_total`` Prometheus label,
    so their exact string values are a contract."""

    def setup_method(self):
        from src.processing.board import _classify_job_url

        self.classify = _classify_job_url

    def test_plausible_returns_none(self):
        assert self.classify("https://example.com/jobs/123") is None

    def test_invalid_reason(self):
        assert self.classify("") == "invalid"
        assert self.classify("not-a-url") == "invalid"

    def test_bare_host_reason(self):
        assert self.classify("https://example.com/") == "bare_host"
        assert self.classify("https://example.com/#0") == "bare_host"

    def test_board_homepage_reason(self):
        assert (
            self.classify(
                "https://example.com/careers",
                board_url="https://example.com/careers",
            )
            == "board_homepage"
        )


# ── TestInsertSqlContract ────────────────────────────────────────────


class TestInsertSqlContract:
    """String-level pins on the SQL constants so refactors can't silently
    drop the duplicate-handling clauses without triggering a test failure.
    Mock-based integration tests exercise the Python branches but never
    run the actual SQL — these pins are the only layer that catches
    'someone deleted ON CONFLICT from _INSERT_RICH_JOB_ENRICH' in CI."""

    def test_insert_rich_job_has_on_conflict(self):
        assert "ON CONFLICT (source_url) DO NOTHING" in _INSERT_RICH_JOB

    def test_insert_rich_job_enrich_has_on_conflict(self):
        assert "ON CONFLICT (source_url) DO NOTHING" in _INSERT_RICH_JOB_ENRICH

    def test_insert_url_only_jobs_has_on_conflict(self):
        assert "ON CONFLICT (source_url) DO NOTHING" in _INSERT_URL_ONLY_JOBS

    def test_diff_batch_has_foreign_touched_cte(self):
        # Pin the cross-board handling added in the follow-up commit:
        # without these clauses, the infinite-retry loop and the
        # last_seen_at ghost-tombstoning both come back.
        assert "foreign_touched" in _DIFF_BATCH
        assert "board_id != $2" in _DIFF_BATCH
        # new_urls must check "any board", not "this board only".
        assert "NOT EXISTS" in _DIFF_BATCH


# ── TestDuplicateSourceUrl ───────────────────────────────────────────


class TestDuplicateSourceUrl:
    """Fix for issue 02: duplicate source_url must not abort the batch."""

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_url_only_duplicate_skipped_by_on_conflict(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """URL-only batch where ON CONFLICT drops one of two rows still succeeds.

        The monitor yields two new URLs. DIFF_BATCH reports both as new,
        but _INSERT_URL_ONLY_JOBS returns only one row (the other was a
        duplicate via another board and silently no-ops). The run must
        complete without raising, and only the inserted row should get
        enqueued for scraping.
        """
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        url2 = "https://example.com/job/2"
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls={url1, url2}, jobs_by_url=None))
        conn.fetch.side_effect = [
            # 1. DIFF_BATCH -> both new
            [_diff_row("new", url=url1), _diff_row("new", url=url2)],
            # 2. INSERT_URL_ONLY_JOBS with ON CONFLICT -> only url1 inserted
            [_inserted_row("jp-1", url1)],
            # 3. MARK_GONE_BY_TIMESTAMP
            [],
        ]
        board = _mock_board()

        # Must not raise.
        await _process_one_board(board, pool, mock_http)

        # The INSERT call went through with both URLs in the payload.
        insert_call = conn.fetch.await_args_list[1]
        assert insert_call.args[0] == _INSERT_URL_ONLY_JOBS
        assert set(insert_call.args[3]) == {url1, url2}

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_rich_duplicate_keeps_description_aligned(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Rich-insert path: when the first fetchrow returns None (ON CONFLICT
        no-op), the description UPSERT must be written against the SECOND
        job's id AND carry the SECOND job's body — never the first's. This
        guards against the zip-misalignment bug where dropping inserted_ids[0]
        silently shifted every description by one row.
        """
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        url2 = "https://example.com/job/2"
        job1 = _discovered_job(url=url1, description="<p>Job one body</p>")
        job2 = _discovered_job(url=url2, title="Designer", description="<p>Job two body</p>")
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(urls={url1, url2}, jobs_by_url={url1: job1, url2: job2})
        )
        conn.fetch.side_effect = [
            # 1. DIFF_BATCH -> both new
            [_diff_row("new", url=url1), _diff_row("new", url=url2)],
            # 2. MARK_GONE_BY_TIMESTAMP
            [],
        ]

        # First insert → conflict (None), second insert → success.
        # DiscoveredJob sets don't preserve insertion order, so we have to
        # key the fetchrow responses on the source_url that's in the record.
        insert_order: list[str] = []

        async def _fake_fetchrow(sql, *args):
            # _INSERT_RICH_JOB uses $4 for source_url (1-indexed → args[3]).
            source_url = args[3]
            insert_order.append(source_url)
            if len(insert_order) == 1:
                return None  # duplicate collides via another board
            return {"id": f"jp-for-{source_url}"}

        conn.fetchrow.side_effect = _fake_fetchrow
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # Two inserts attempted, first no-op, second success.
        assert len(insert_order) == 2
        survivor_url = insert_order[1]
        survivor_body = "<p>Job one body</p>" if survivor_url == url1 else "<p>Job two body</p>"

        # Exactly one description upsert, and it must be BOTH
        # (a) targeting the surviving row's id, AND
        # (b) carrying the surviving job's description body.
        # Asserting only (a) is insufficient — with the pre-fix
        # zip(r2_staging, inserted_ids, strict=False), the id column would
        # still match since there's only ever one surviving id, but the
        # description body would belong to job1. Asserting (b) catches that.
        desc_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPSERT_DESCRIPTION]
        assert len(desc_calls) == 1, "exactly one description write expected"
        assert desc_calls[0].args[1] == f"jp-for-{survivor_url}"
        assert desc_calls[0].args[3] == survivor_body, (
            f"description body should belong to {survivor_url} but got {desc_calls[0].args[3]!r}"
        )

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_implausible_urls_filtered_before_insert(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Site-root URL yielded by a DOM monitor is dropped pre-insert."""
        pool, conn = mock_pool
        garbage = "https://krb-sjobs.brassring.com/"
        real = "https://krb-sjobs.brassring.com/TGnewUI/job/123"
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(urls={garbage, real}, jobs_by_url=None)
        )
        conn.fetch.side_effect = [
            [_diff_row("new", url=real)],
            [_inserted_row("jp-1", real)],
            [],  # MARK_GONE
        ]
        board = _mock_board(board_url="https://krb-sjobs.brassring.com/TGnewUI")

        await _process_one_board(board, pool, mock_http)

        # DIFF_BATCH should only see the real URL — garbage was filtered out.
        diff_call = conn.fetch.await_args_list[0]
        assert diff_call.args[0] == _DIFF_BATCH
        assert garbage not in diff_call.args[1]
        assert real in diff_call.args[1]

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_all_urls_filtered_treated_as_empty_check(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """If every URL is filtered out, fall back to the empty-check path
        rather than running _MARK_GONE_BY_TIMESTAMP (which would wrongly
        mark every active posting as gone)."""
        pool, conn = mock_pool
        garbage = "https://example.com/"
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls={garbage}, jobs_by_url=None))
        conn.fetch.return_value = [{"board_status": "active"}]
        board = _mock_board(board_url="https://example.com/careers")

        await _process_one_board(board, pool, mock_http)

        # Only _RECORD_EMPTY_CHECK was called — not _DIFF_BATCH or MARK_GONE.
        assert conn.fetch.await_count == 1
        conn.fetch.assert_awaited_with(_RECORD_EMPTY_CHECK, "board-1")

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_rich_duplicate_uses_enrich_insert_when_enrich_configured(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """ON CONFLICT must also apply to the ENRICH insert variant — a
        regression here would re-introduce crashes for boards that have
        scraper_config.enrich set (the common Workday-plus-description case).
        """
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        job1 = _discovered_job(url=url1, description="<p>body</p>")
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(urls={url1}, jobs_by_url={url1: job1})
        )
        conn.fetch.side_effect = [
            [_diff_row("new", url=url1)],
            [],  # MARK_GONE
        ]
        conn.fetchrow.return_value = None  # simulate global conflict
        board = _mock_board(metadata={"scraper_config": {"enrich": ["description"]}})

        await _process_one_board(board, pool, mock_http)

        # Selected the ENRICH variant (not the base _INSERT_RICH_JOB).
        insert_sqls = [c.args[0] for c in conn.fetchrow.await_args_list]
        assert _INSERT_RICH_JOB_ENRICH in insert_sqls
        assert _INSERT_RICH_JOB not in insert_sqls

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_rich_all_inserts_conflict_no_crash(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """All-rows-deduped edge case: every fetchrow returns None. The loop
        must complete, no description upserts should fire, and nothing
        should be enqueued for scraping."""
        import src.processing.board as board_mod
        from src.metrics import monitor_dedup_total

        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        url2 = "https://example.com/job/2"
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1, url2},
                jobs_by_url={
                    url1: _discovered_job(url=url1),
                    url2: _discovered_job(url=url2),
                },
            )
        )
        conn.fetch.side_effect = [
            [_diff_row("new", url=url1), _diff_row("new", url=url2)],
            [],  # MARK_GONE
        ]
        conn.fetchrow.return_value = None  # every insert conflicts
        board = _mock_board()

        # Ensure the label set is registered before we read it.
        monitor_dedup_total.labels(path="rich")
        before = _counter_value(monitor_dedup_total, path="rich")
        enqueue_spy = board_mod._enqueue_scrapes_for_new
        enqueue_spy.reset_mock()

        await _process_one_board(board, pool, mock_http)

        # No descriptions written.
        desc_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPSERT_DESCRIPTION]
        assert len(desc_calls) == 0
        # Dedup counter bumped by exactly two.
        assert _counter_value(monitor_dedup_total, path="rich") - before == 2
        # Nothing enqueued for scraping (the board has no enrich, so the
        # enqueue branch wouldn't fire anyway; this is just belt-and-braces).
        enqueue_spy.assert_not_awaited()

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_rich_enrich_all_inserts_conflict_does_not_enqueue(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Enrich path + all-conflict: the `if enrich_fields and inserted_rich`
        branch must NOT fire, so no deduped URLs leak into the scrape queue.
        This is the branch the base all-conflict test can't reach."""
        import src.processing.board as board_mod

        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(urls={url1}, jobs_by_url={url1: _discovered_job(url=url1)})
        )
        conn.fetch.side_effect = [
            [_diff_row("new", url=url1)],
            [],  # MARK_GONE
        ]
        conn.fetchrow.return_value = None  # conflict
        board = _mock_board(metadata={"scraper_config": {"enrich": ["description"]}})

        enqueue_spy = board_mod._enqueue_scrapes_for_new
        enqueue_spy.reset_mock()

        await _process_one_board(board, pool, mock_http)

        # The enrich-path enqueue branch must not fire when every insert
        # conflicted — nothing to enrich, nothing to scrape.
        enqueue_spy.assert_not_awaited()

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_url_only_dedup_does_not_enqueue_scrape(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Silent behaviour contract: when _INSERT_URL_ONLY_JOBS drops a row
        via ON CONFLICT, that URL must NOT be enqueued for scraping. Only
        the surviving inserted row should be passed to
        _enqueue_scrapes_for_new."""
        import src.processing.board as board_mod

        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        url2 = "https://example.com/job/2"
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls={url1, url2}, jobs_by_url=None))
        conn.fetch.side_effect = [
            [_diff_row("new", url=url1), _diff_row("new", url=url2)],
            [_inserted_row("jp-1", url1)],  # only url1 survives ON CONFLICT
            [],  # MARK_GONE
        ]

        # _enqueue_scrapes_for_new is already AsyncMock() via the autouse
        # _mock_enqueue_scrapes fixture; inspect its calls directly.
        enqueue_spy = board_mod._enqueue_scrapes_for_new
        enqueue_spy.reset_mock()
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # Called once with the survivor list — NOT with url2.
        assert enqueue_spy.await_count == 1
        passed_rows = enqueue_spy.await_args.args[0]
        passed_urls = {r["source_url"] for r in passed_rows}
        assert passed_urls == {url1}, f"deduped url2 leaked into scrape queue: {passed_urls}"

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_foreign_action_does_not_insert_or_enqueue(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """_DIFF_BATCH can emit the ``foreign`` action when the URL is
        already owned by another board (cross-tenant Workday etc.). The
        application layer must:
        - NOT call _INSERT_URL_ONLY_JOBS or _INSERT_RICH_JOB for it
        - NOT enqueue it for scraping
        - bump monitor_dedup_total{path="cross_board"}
        The owning row's last_seen_at is refreshed inside _DIFF_BATCH
        itself, so nothing else is needed from the Python side.
        """
        import src.processing.board as board_mod
        from src.metrics import monitor_dedup_total

        pool, conn = mock_pool
        foreign = "https://jobs.example.com/foreign/role"
        mock_monitor.side_effect = _mock_stream(MonitorResult(urls={foreign}, jobs_by_url=None))
        conn.fetch.side_effect = [
            [_diff_row("foreign", url=foreign)],
            [],  # MARK_GONE
        ]

        enqueue_spy = board_mod._enqueue_scrapes_for_new
        enqueue_spy.reset_mock()
        # Ensure label set is registered before reading.
        monitor_dedup_total.labels(path="cross_board")
        before = _counter_value(monitor_dedup_total, path="cross_board")
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # No insert attempted (neither URL-only nor rich).
        for call in conn.fetch.await_args_list:
            assert call.args[0] != _INSERT_URL_ONLY_JOBS
        conn.fetchrow.assert_not_awaited()

        # Nothing enqueued for scraping.
        enqueue_spy.assert_not_awaited()

        # Metric contract: cross-board dedup counter bumped exactly once.
        assert _counter_value(monitor_dedup_total, path="cross_board") - before == 1


# ── TestMonitorPipeline ──────────────────────────────────────────────


class TestMonitorPipeline:
    @patch("src.processing.board._process_one_board_streaming", new_callable=AsyncMock)
    async def test_all_succeed(self, mock_process, mock_pool, mock_http):
        """3 boards, all succeed -> returns 3."""
        pool, _ = mock_pool
        boards = [_mock_board(id=f"b-{i}") for i in range(3)]
        mock_process.return_value = (True, 1.0)

        result = await _monitor_pipeline(boards, pool, mock_http)

        assert result.succeeded == 3
        assert len(result.durations) == 3
        assert mock_process.await_count == 3

    @patch("src.processing.board._process_one_board_streaming", new_callable=AsyncMock)
    async def test_partial_failure(self, mock_process, mock_pool, mock_http):
        """3 boards, 1 raises in _process_one_board -> returns 2."""
        pool, _ = mock_pool
        boards = [_mock_board(id=f"b-{i}") for i in range(3)]
        mock_process.side_effect = [(True, 1.0), RuntimeError("fail"), (True, 1.0)]

        result = await _monitor_pipeline(boards, pool, mock_http)

        assert result.succeeded == 2

    @patch("src.processing.board._process_one_board_streaming", new_callable=AsyncMock)
    async def test_counts_false_return_as_failure(self, mock_process, mock_pool, mock_http):
        """_process_one_board False result should count as failed."""
        pool, _ = mock_pool
        boards = [_mock_board(id=f"b-{i}") for i in range(3)]
        mock_process.side_effect = [(True, 1.0), (False, 2.0), (True, 1.0)]

        result = await _monitor_pipeline(boards, pool, mock_http)

        assert result.succeeded == 2

    @patch("src.processing.board._process_one_board_streaming", new_callable=AsyncMock)
    async def test_empty_boards(self, mock_process, mock_pool, mock_http):
        """Empty list -> returns 0."""
        pool, _ = mock_pool

        result = await _monitor_pipeline([], pool, mock_http)

        assert result.succeeded == 0
        mock_process.assert_not_awaited()


# ── TestProcessMonitorBatch ──────────────────────────────────────────


class TestProcessMonitorBatch:
    @patch("src.batch._monitor_pipeline", new_callable=AsyncMock)
    async def test_no_due_boards(self, mock_pipeline, mock_pool, mock_http):
        """pool.fetch returns [] -> BatchResult(0,0,0)."""
        pool, _ = mock_pool
        pool.fetch.return_value = []

        result = await process_monitor_batch(pool, mock_http)

        assert result.processed == 0
        assert result.succeeded == 0
        assert result.failed == 0
        mock_pipeline.assert_not_awaited()

    @patch("src.batch._monitor_pipeline", new_callable=AsyncMock)
    async def test_single_domain(self, mock_pipeline, mock_pool, mock_http):
        """All boards same throttle key -> one pipeline call."""
        pool, _ = mock_pool
        boards = [
            _mock_board(id="b-1", crawler_type="greenhouse"),
            _mock_board(id="b-2", crawler_type="greenhouse"),
        ]
        pool.fetch.return_value = boards
        mock_pipeline.return_value = _PipelineResult(succeeded=2, durations=[1.0, 1.0])

        result = await process_monitor_batch(pool, mock_http)

        assert result.processed == 2
        assert result.succeeded == 2
        assert result.failed == 0
        mock_pipeline.assert_awaited_once()

    @patch("src.batch._monitor_pipeline", new_callable=AsyncMock)
    async def test_multiple_domains(self, mock_pipeline, mock_pool, mock_http):
        """Boards with different keys -> parallel pipelines -> correct counts."""
        pool, _ = mock_pool
        boards = [
            _mock_board(id="b-1", crawler_type="greenhouse"),
            _mock_board(id="b-2", crawler_type="lever"),
            _mock_board(id="b-3", crawler_type="greenhouse"),
        ]
        pool.fetch.return_value = boards
        # Two groups: greenhouse (2 boards) and lever (1 board)
        mock_pipeline.side_effect = [
            _PipelineResult(succeeded=2, durations=[1.0, 1.0]),
            _PipelineResult(succeeded=1, durations=[1.0]),
        ]

        result = await process_monitor_batch(pool, mock_http)

        assert result.processed == 3
        assert result.succeeded == 3
        assert result.failed == 0
        assert mock_pipeline.await_count == 2

    @patch("src.batch._monitor_pipeline", new_callable=AsyncMock)
    async def test_grouping_correctness(self, mock_pipeline, mock_pool, mock_http):
        """2 greenhouse + 1 sitemap -> 2 groups with correct board lists."""
        pool, _ = mock_pool
        gh1 = _mock_board(id="b-1", crawler_type="greenhouse")
        gh2 = _mock_board(id="b-2", crawler_type="greenhouse")
        sm1 = _mock_board(
            id="b-3", crawler_type="sitemap", board_url="https://acme.com/sitemap.xml"
        )
        pool.fetch.return_value = [gh1, gh2, sm1]
        mock_pipeline.side_effect = [
            _PipelineResult(succeeded=2, durations=[1.0, 1.0]),
            _PipelineResult(succeeded=1, durations=[1.0]),
        ]

        result = await process_monitor_batch(pool, mock_http)

        assert result.processed == 3
        assert result.succeeded == 3
        # Verify two pipeline calls were made
        assert mock_pipeline.await_count == 2
        # Collect the board lists passed to each pipeline call
        call_board_lists = [c.args[0] for c in mock_pipeline.await_args_list]
        call_sizes = sorted(len(bl) for bl in call_board_lists)
        assert call_sizes == [1, 2]

    @patch("src.batch._monitor_pipeline", new_callable=AsyncMock)
    async def test_worker_id_passed_to_fetch(self, mock_pipeline, mock_pool, mock_http):
        """worker_id is passed as $2 to the claim query."""
        pool, _ = mock_pool
        pool.fetch.return_value = []

        await process_monitor_batch(pool, mock_http, worker_id="test-w")

        pool.fetch.assert_awaited_once()
        call_args = pool.fetch.await_args.args
        assert call_args[1] == 200  # limit
        assert call_args[2] == "test-w"  # worker_id


# ── TestProcessOneScrape ─────────────────────────────────────────────


class TestProcessOneScrape:
    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_success_updates_and_records(self, mock_scrape, mock_pool, mock_http):
        """scrape_one returns content -> UPDATE + _RECORD_SCRAPE_SUCCESS -> True."""
        pool, conn = mock_pool
        content = _job_content()
        mock_scrape.return_value = content
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        assert ok is True
        # Verify UPDATE_JOB_CONTENT and RECORD_SCRAPE_SUCCESS were called
        execute_calls = conn.execute.await_args_list
        assert any(c.args[0] == _UPDATE_JOB_CONTENT for c in execute_calls)
        assert any(c.args[0] == _RECORD_SCRAPE_SUCCESS for c in execute_calls)

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_missing_job_posting_id_records_failure(self, mock_scrape, mock_pool, mock_http):
        """UPDATE 0 must record scrape failure instead of silently dropping it."""
        pool, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        mock_scrape.return_value = _job_content()
        item = ScrapeItem(
            job_posting_id="jp-missing", url="https://example.com/job/1", board_id="b-1"
        )

        ok, _duration = await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        assert ok is False
        execute_calls = conn.execute.await_args_list
        failure_calls = [c for c in execute_calls if c.args[0] == _RECORD_SCRAPE_FAILURE]
        assert len(failure_calls) == 1
        assert failure_calls[0].args[1] == "jp-missing"

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_failure_records_scrape_failure(self, mock_scrape, mock_pool, mock_http):
        """scrape_one raises -> _RECORD_SCRAPE_FAILURE -> False."""
        pool, conn = mock_pool
        mock_scrape.side_effect = RuntimeError("scrape error")
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        assert ok is False
        execute_calls = conn.execute.await_args_list
        failure_calls = [c for c in execute_calls if c.args[0] == _RECORD_SCRAPE_FAILURE]
        assert len(failure_calls) == 1
        assert failure_calls[0].args[1] == "jp-1"

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_failure_uses_exception_type_on_blank_error(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Scrape failure records failure for the correct job posting ID."""
        pool, conn = mock_pool
        mock_scrape.side_effect = RuntimeError()
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        assert ok is False
        execute_calls = conn.execute.await_args_list
        failure_calls = [c for c in execute_calls if c.args[0] == _RECORD_SCRAPE_FAILURE]
        assert len(failure_calls) == 1
        assert failure_calls[0].args[1] == "jp-1"

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_content_fields_passed_correctly(self, mock_scrape, mock_pool, mock_http):
        """Verify all JobContent fields are passed to UPDATE_JOB_CONTENT."""
        pool, conn = mock_pool
        content = _job_content(
            title="Senior Engineer",
            description="<p>Description</p>",
            locations=["London", "Remote"],
            employment_type="FULL_TIME",
            job_location_type="hybrid",
            date_posted="2025-06-01",
            base_salary={"currency": "GBP", "min": 80000, "max": 120000, "unit": "YEAR"},
            extras={"skills": ["Python", "SQL"], "responsibilities": ["Lead team"]},
            metadata={"team": "Platform"},
        )
        mock_scrape.return_value = content
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        # Find the UPDATE_JOB_CONTENT call
        # Param order: $1=id, $2=employment_type, $3=titles, $4=locales,
        #   $5=location_ids, $6=location_types, $7=description_r2_hash
        update_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_JOB_CONTENT]
        assert len(update_calls) == 1
        call_args = update_calls[0].args
        assert call_args[1] == "jp-1"  # job_posting_id
        assert call_args[2] == "full_time"  # employment_type (normalized)
        assert call_args[3] == ["Senior Engineer"]  # titles
        assert isinstance(call_args[4], list)  # locales (language-detected)
        # location_ids and location_types: $5, $6 (may be None if resolver has no data loaded)
        assert call_args[5] is None  # location_ids (no resolver data)
        assert call_args[6] is None  # location_types (no resolver data)

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_normalizes_escaped_description_before_update(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Escaped description markup is normalized before SQL update."""
        pool, conn = mock_pool
        content = _job_content(
            description="&lt;p class=&quot;Lexical__paragraph&quot;&gt;Hi&lt;/p&gt;"
        )
        mock_scrape.return_value = content
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        update_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_JOB_CONTENT]
        assert len(update_calls) == 1
        # Description is no longer in the DB update (moved to R2).
        # Verify it was normalized in-place for R2 upload.
        assert content.description == "<p>Hi</p>"

    @patch("src.redis_queue.enqueue_scrape", new_callable=AsyncMock)
    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_step0_no_title_enqueues_fallback(
        self, mock_scrape, mock_enqueue, mock_pool, mock_http
    ):
        """Step 0 returns no title -> enqueues next fallback step, returns False."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title=None)
        mock_enqueue.return_value = True
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")
        config = {"fallback": {"type": "dom", "config": {"render": True}}}

        ok, _duration = await _process_one_scrape(
            item, pool, mock_http, "json-ld", config, scrape_step=0
        )

        assert ok is False
        # Only primary scraper called (no inline fallback)
        assert mock_scrape.await_count == 1
        # Next step enqueued
        mock_enqueue.assert_awaited_once()
        enqueue_args = mock_enqueue.await_args
        assert enqueue_args.args[0] == "example.com"  # domain
        assert enqueue_args.args[1] == "jp-1"  # posting_id
        assert enqueue_args.kwargs.get("browser") is True  # dom+render needs browser
        assert enqueue_args.args[3]["scrape_step"] == "1"

    @patch("src.redis_queue.enqueue_scrape", new_callable=AsyncMock)
    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_step0_success_enqueues_next(
        self, mock_scrape, mock_enqueue, mock_pool, mock_http
    ):
        """Step 0 succeeds with title -> saves and enqueues next fallback step."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title="Primary Title")
        mock_enqueue.return_value = True
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")
        config = {"fallback": {"type": "dom", "config": {"render": True}}}

        ok, _duration = await _process_one_scrape(
            item, pool, mock_http, "json-ld", config, scrape_step=0
        )

        assert ok is True
        assert mock_scrape.await_count == 1
        # Next fallback step should be enqueued
        mock_enqueue.assert_awaited_once()

    @patch("src.redis_queue.enqueue_scrape", new_callable=AsyncMock)
    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_no_fallback_no_enqueue(self, mock_scrape, mock_enqueue, mock_pool, mock_http):
        """No fallback configured -> no enqueue after success."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title="Title")
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")
        config = {}  # no fallback

        ok, _duration = await _process_one_scrape(
            item, pool, mock_http, "json-ld", config, scrape_step=0
        )

        assert ok is True
        mock_enqueue.assert_not_awaited()

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_step0_no_title_no_fallback_records_failure(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Step 0, no title, no fallback -> records failure."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title=None)
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")
        config = {}  # no fallback

        ok, _duration = await _process_one_scrape(
            item, pool, mock_http, "json-ld", config, scrape_step=0
        )

        assert ok is False
        execute_calls = conn.execute.await_args_list
        failure_calls = [c for c in execute_calls if c.args[0] == _RECORD_SCRAPE_FAILURE]
        assert len(failure_calls) == 1

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_no_fallback_without_config(self, mock_scrape, mock_pool, mock_http):
        """No fallback configured + empty title -> failure (backoff)."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title=None)
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        assert ok is False
        assert mock_scrape.await_count == 1
        execute_calls = conn.execute.await_args_list
        failure_calls = [c for c in execute_calls if c.args[0] == _RECORD_SCRAPE_FAILURE]
        assert len(failure_calls) == 1

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_step1_fallback_with_no_title_must_pass_none_not_empty_list(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Regression: dom fallback at step 1 returns no title, MUST pass
        SQL ``NULL`` (not ``[]``) so COALESCE preserves the title that
        step 0 wrote. ``COALESCE($3, titles)`` only treats SQL NULL as
        null — empty arrays pass through and overwrite. This was the
        Migros/Galaxus/KPMG/L'Oreal title-wipe bug (~800 affected rows).
        """
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(
            title=None,  # dom fallback only extracts description
            description="<p>Long body the dom step extracted</p>",
            locations=None,
            employment_type=None,
            language=None,
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(
            item,
            pool,
            mock_http,
            "dom",
            {"steps": [{"tag": "h3", "field": "description"}]},
            scrape_step=1,
        )

        assert ok is True
        update_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        assert len(update_calls) == 1
        call_args = update_calls[0].args
        # $3 = titles -> MUST be None so COALESCE preserves existing
        assert call_args[3] is None, (
            f"titles param must be None to preserve step-0 title, got {call_args[3]!r}"
        )
        # Description-derived language detection still picks 'en' from
        # the body, so locales may be set legitimately. The bug was
        # specifically that titles got wiped.

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_step1_fallback_with_no_language_signal_passes_none_locales(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Same shape as the title bug but for locales: when the fallback
        returns no language and no description (so detect_language has
        nothing to work with), ``_build_locales`` would default to
        ``["en"]`` and clobber any real language stored on the row.
        Pass None instead so COALESCE preserves existing locales.
        """
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(
            title=None,
            description=None,  # nothing to detect language from
            locations=None,
            employment_type=None,
            language=None,
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(
            item,
            pool,
            mock_http,
            "dom",
            {"steps": [{"tag": "h3", "field": "description"}]},
            scrape_step=1,
        )

        assert ok is True
        update_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        assert len(update_calls) == 1
        call_args = update_calls[0].args
        # $4 = locales -> MUST be None so COALESCE preserves existing
        assert call_args[4] is None, (
            f"locales param must be None to preserve step-0 locales, got {call_args[4]!r}"
        )

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_garbage_title_treated_as_empty(self, mock_scrape, mock_pool, mock_http):
        """Garbage titles (auth walls, etc.) -> failure (backoff), no content write."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title="Not Logged In", description="<p>junk</p>")
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        assert ok is False
        execute_calls = conn.execute.await_args_list
        # Should record failure, NOT write content
        failure_calls = [c for c in execute_calls if c.args[0] == _RECORD_SCRAPE_FAILURE]
        content_calls = [c for c in execute_calls if c.args[0] == _UPDATE_JOB_CONTENT]
        assert len(failure_calls) == 1
        assert len(content_calls) == 0

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_non_scalar_fields_are_normalized(self, mock_scrape, mock_pool, mock_http):
        """List-based fields are normalized before SQL writes."""
        pool, conn = mock_pool
        content = _job_content(
            employment_type=["Temporary positions", "Full-time"],
            date_posted="Fri, 06 Feb 2026 16:31:18 +0400",
        )
        mock_scrape.return_value = content
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        update_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_JOB_CONTENT]
        assert len(update_calls) == 1
        call_args = update_calls[0].args
        # $2 = employment_type (normalized)
        assert call_args[2] == "full_or_part"  # normalized from "Temporary positions, Full-time"


# ── Field-Level Fallback ─────────────────────────────────────────────


class TestMergeFields:
    def test_overrides_specified_fields(self):
        primary = _job_content(title="Primary", description="<p>short</p>")
        fallback = _job_content(title="FB Title", description="<p>long desc</p>")
        merged = _merge_fields(primary, fallback, ["description"])
        assert merged.title == "Primary"  # not overridden
        assert merged.description == "<p>long desc</p>"  # overridden

    def test_preserves_primary_when_fallback_none(self):
        primary = _job_content(description="<p>keep me</p>")
        fallback = JobContent(description=None)
        merged = _merge_fields(primary, fallback, ["description"])
        assert merged.description == "<p>keep me</p>"

    def test_unknown_field_warns_no_crash(self):
        primary = _job_content()
        fallback = _job_content()
        # Should not raise
        merged = _merge_fields(primary, fallback, ["nonexistent_field"])
        assert merged.title == primary.title

    def test_multiple_fields(self):
        primary = _job_content(title="P", description="<p>pd</p>", locations=["NYC"])
        fallback = _job_content(title="F", description="<p>fd</p>", locations=["London"])
        merged = _merge_fields(primary, fallback, ["description", "locations"])
        assert merged.title == "P"
        assert merged.description == "<p>fd</p>"
        assert merged.locations == ["London"]


class TestGetScraperAtStep:
    def test_step_zero_returns_primary(self):
        """Step 0 returns the primary scraper type and config."""
        typ, cfg = _get_scraper_at_step("json-ld", {"selector": "h1"}, 0)
        assert typ == "json-ld"
        assert cfg == {"selector": "h1"}

    def test_step_one_returns_fallback(self):
        """Step 1 walks to the first fallback."""
        config = {"fallback": {"type": "dom", "config": {"render": True}}}
        typ, cfg = _get_scraper_at_step("json-ld", config, 1)
        assert typ == "dom"
        assert cfg == {"render": True}

    def test_step_two_nested_fallback(self):
        """Step 2 walks through two levels of fallback."""
        config = {
            "fallback": {
                "type": "dom",
                "config": {
                    "render": False,
                    "fallback": {"type": "embedded", "config": {"path": "$.x"}},
                },
            }
        }
        typ, cfg = _get_scraper_at_step("json-ld", config, 2)
        assert typ == "embedded"
        assert cfg == {"path": "$.x"}

    def test_step_beyond_chain_returns_last(self):
        """Step beyond chain depth returns the last scraper."""
        config = {"fallback": {"type": "dom", "config": {}}}
        typ, cfg = _get_scraper_at_step("json-ld", config, 5)
        assert typ == "dom"
        assert cfg == {}

    def test_none_config_step_zero(self):
        """None config at step 0 returns empty dict."""
        typ, cfg = _get_scraper_at_step("json-ld", None, 0)
        assert typ == "json-ld"
        assert cfg == {}


class TestGetNextFallback:
    def test_returns_next_when_exists(self):
        """Returns the next fallback at step+1."""
        config = {
            "fallback": {
                "type": "dom",
                "config": {"render": True},
                "fields": ["description"],
            }
        }
        result = _get_next_fallback("json-ld", config, 0)
        assert result is not None
        fb_type, fb_cfg, fb_fields = result
        assert fb_type == "dom"
        assert fb_cfg == {"render": True}
        assert fb_fields == ["description"]

    def test_returns_none_when_no_fallback(self):
        """Returns None when there is no next fallback."""
        config = {"selector": "h1"}
        result = _get_next_fallback("json-ld", config, 0)
        assert result is None

    def test_nested_fallback_at_step_1(self):
        """Returns the fallback at step 2 when asked at step 1."""
        config = {
            "fallback": {
                "type": "dom",
                "config": {
                    "fallback": {"type": "embedded", "config": {"path": "$.x"}},
                },
            }
        }
        result = _get_next_fallback("json-ld", config, 1)
        assert result is not None
        fb_type, fb_cfg, fb_fields = result
        assert fb_type == "embedded"
        assert fb_cfg == {"path": "$.x"}
        assert fb_fields is None

    def test_returns_none_beyond_chain(self):
        """Returns None when step is already past the chain end."""
        config = {"fallback": {"type": "dom", "config": {}}}
        result = _get_next_fallback("json-ld", config, 5)
        assert result is None


# ── TestScrapePipeline ───────────────────────────────────────────────


class TestScrapePipeline:
    @patch("src.batch._process_one_scrape", new_callable=AsyncMock)
    async def test_all_succeed(self, mock_process, mock_pool, mock_http):
        """3 items succeed -> returns 3."""
        pool, _ = mock_pool
        mock_process.return_value = (True, 1.0)
        items = [
            ScrapeItem(job_posting_id=f"jp-{i}", url=f"https://example.com/job/{i}", board_id="b-1")
            for i in range(3)
        ]

        result = await _scrape_pipeline(items, pool, mock_http)

        assert result.succeeded == 3
        assert len(result.durations) == 3
        assert mock_process.await_count == 3

    @patch("src.batch._process_one_scrape", new_callable=AsyncMock)
    async def test_partial_failure(self, mock_process, mock_pool, mock_http):
        """1 fails -> returns 2."""
        pool, _ = mock_pool
        mock_process.side_effect = [(True, 1.0), (False, 2.0), (True, 1.0)]
        items = [
            ScrapeItem(job_posting_id=f"jp-{i}", url=f"https://example.com/job/{i}", board_id="b-1")
            for i in range(3)
        ]

        result = await _scrape_pipeline(items, pool, mock_http)

        assert result.succeeded == 2

    @patch("src.batch._process_one_scrape", new_callable=AsyncMock)
    async def test_empty_items(self, mock_process, mock_pool, mock_http):
        """Empty list -> returns 0."""
        pool, _ = mock_pool

        result = await _scrape_pipeline([], pool, mock_http)

        assert result.succeeded == 0
        mock_process.assert_not_awaited()

    @patch("src.batch._process_one_scrape", new_callable=AsyncMock)
    async def test_uses_board_specific_scraper(self, mock_process, mock_pool, mock_http):
        """Board-specific scraper settings should be passed through."""
        pool, _ = mock_pool
        mock_process.return_value = (True, 1.0)
        items = [ScrapeItem(job_posting_id="jp-1", url="https://alpha.com/job/1", board_id="b-1")]
        board_scrapers = {
            "b-1": BoardScraperConfig(scraper_type="dom", scraper_config={"render": False})
        }

        result = await _scrape_pipeline(items, pool, mock_http, board_scrapers)

        assert result.succeeded == 1
        call_args = mock_process.await_args.args
        assert call_args[3] == "dom"
        assert call_args[4] == {"render": False}

    @patch("src.batch._process_one_scrape", new_callable=AsyncMock)
    async def test_passes_fallback_in_config(self, mock_process, mock_pool, mock_http):
        """Fallback chain inside scraper_config should be passed through."""
        pool, _ = mock_pool
        mock_process.return_value = (True, 1.0)
        items = [ScrapeItem(job_posting_id="jp-1", url="https://alpha.com/job/1", board_id="b-1")]
        board_scrapers = {
            "b-1": BoardScraperConfig(
                scraper_type="json-ld",
                scraper_config={"fallback": {"type": "dom", "config": {"render": True}}},
            )
        }

        result = await _scrape_pipeline(items, pool, mock_http, board_scrapers)

        assert result.succeeded == 1
        call_args = mock_process.await_args
        assert call_args.args[3] == "json-ld"
        assert call_args.args[4] == {"fallback": {"type": "dom", "config": {"render": True}}}


class TestLoadBoardScrapers:
    async def test_loads_scraper_from_metadata(self, mock_pool):
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {"scraper_type": "dom", "scraper_config": {"render": False}},
                "crawler_type": "sitemap",
            }
        ]

        info = await _load_board_scrapers(pool, {"b-1"})

        cfg = info.scrapers["b-1"]
        assert cfg.scraper_type == "dom"
        assert cfg.scraper_config == {"render": False}
        assert "b-1" not in info.rich_board_ids

    async def test_falls_back_on_invalid_scraper(self, mock_pool):
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {"scraper_type": "nope", "scraper_config": {"x": 1}},
                "crawler_type": "sitemap",
            }
        ]

        info = await _load_board_scrapers(pool, {"b-1"})

        cfg = info.scrapers["b-1"]
        assert cfg.scraper_type == "json-ld"
        assert cfg.scraper_config is None

    async def test_loads_fallback_from_scraper_config(self, mock_pool):
        """Fallback chain is preserved inside scraper_config."""
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "crawler_type": "sitemap",
                "metadata": {
                    "scraper_type": "json-ld",
                    "scraper_config": {
                        "fallback": {"type": "dom", "config": {"render": True}},
                    },
                },
            }
        ]

        info = await _load_board_scrapers(pool, {"b-1"})

        cfg = info.scrapers["b-1"]
        assert cfg.scraper_type == "json-ld"
        assert cfg.scraper_config["fallback"]["type"] == "dom"
        assert cfg.scraper_config["fallback"]["config"] == {"render": True}

    async def test_rich_monitor_skipped(self, mock_pool):
        """Rich monitors without explicit scraper are classified as rich."""
        pool, _ = mock_pool
        pool.fetch.return_value = [{"id": "b-1", "metadata": {}, "crawler_type": "greenhouse"}]

        info = await _load_board_scrapers(pool, {"b-1"})

        assert "b-1" in info.rich_board_ids
        assert "b-1" not in info.scrapers


# ── TestProcessScrapeBatch ───────────────────────────────────────────


class TestProcessScrapeBatch:
    @patch("src.batch._scrape_pipeline", new_callable=AsyncMock)
    async def test_empty_queue(self, mock_pipeline, mock_pool, mock_http):
        """No due postings -> BatchResult(0,0,0)."""
        pool, _ = mock_pool
        pool.fetch.return_value = []

        result = await process_scrape_batch(pool, mock_http)

        assert result.processed == 0
        assert result.succeeded == 0
        assert result.failed == 0
        mock_pipeline.assert_not_awaited()

    @patch("src.batch._scrape_pipeline", new_callable=AsyncMock)
    async def test_groups_by_hostname(self, mock_pipeline, mock_pool, mock_http):
        """Items with different hostnames -> multiple pipelines."""
        pool, _ = mock_pool

        def _row(id, url, board_id, scrape_domain=None):
            data = {
                "id": id,
                "source_url": url,
                "board_id": board_id,
                "scrape_domain": scrape_domain,
                "description_r2_hash": None,
            }
            row = MagicMock()
            row.__getitem__ = lambda self, key: data[key]
            return row

        # First fetch: claim job postings; Second fetch: load board scrapers
        pool.fetch.side_effect = [
            [
                _row("jp-1", "https://alpha.com/job/1", "b-1", "alpha.com"),
                _row("jp-2", "https://beta.com/job/1", "b-2", "beta.com"),
                _row("jp-3", "https://alpha.com/job/2", "b-1", "alpha.com"),
            ],
            [],  # no board scraper overrides
        ]
        mock_pipeline.side_effect = [
            _PipelineResult(succeeded=2, durations=[1.0, 1.0]),
            _PipelineResult(succeeded=1, durations=[1.0]),
        ]

        result = await process_scrape_batch(pool, mock_http)

        assert result.processed == 3
        assert result.succeeded == 3
        assert result.failed == 0
        assert mock_pipeline.await_count == 2

    @patch("src.batch._scrape_pipeline", new_callable=AsyncMock)
    async def test_returns_correct_counts(self, mock_pipeline, mock_pool, mock_http):
        """Mix of success/failure -> correct BatchResult."""
        pool, _ = mock_pool

        def _row(id, url, board_id, scrape_domain=None):
            data = {
                "id": id,
                "source_url": url,
                "board_id": board_id,
                "scrape_domain": scrape_domain,
                "description_r2_hash": None,
            }
            row = MagicMock()
            row.__getitem__ = lambda self, key: data[key]
            return row

        # First fetch: claim; Second fetch: board scrapers
        pool.fetch.side_effect = [
            [
                _row("jp-1", "https://alpha.com/job/1", "b-1", "alpha.com"),
                _row("jp-2", "https://alpha.com/job/2", "b-1", "alpha.com"),
                _row("jp-3", "https://beta.com/job/1", "b-2", "beta.com"),
            ],
            [],  # no board scraper overrides
        ]
        # alpha group: 1 of 2 succeed; beta group: 0 of 1 succeed
        mock_pipeline.side_effect = [
            _PipelineResult(succeeded=1, durations=[1.0, 2.0]),
            _PipelineResult(succeeded=0, durations=[3.0]),
        ]

        result = await process_scrape_batch(pool, mock_http)

        assert result.processed == 3
        assert result.succeeded == 1
        assert result.failed == 2

    @patch("src.batch._scrape_pipeline", new_callable=AsyncMock)
    async def test_limit_passed_to_fetch(self, mock_pipeline, mock_pool, mock_http):
        """limit is passed as $1 to the job claim query."""
        pool, _ = mock_pool
        pool.fetch.return_value = []

        await process_scrape_batch(pool, mock_http, limit=50)

        pool.fetch.assert_awaited_once()
        call_args = pool.fetch.await_args.args
        assert call_args[1] == 50  # limit


# ── TestBoardHasEnrich ─────────────────────────────────────────────


class TestBoardHasEnrich:
    def test_with_enrich_list(self):
        metadata = {"scraper_config": {"enrich": ["description"]}}
        assert _board_has_enrich(metadata) == ["description"]

    def test_without_enrich(self):
        metadata = {"scraper_config": {"fallback": {"type": "dom"}}}
        assert _board_has_enrich(metadata) is None

    def test_empty_enrich(self):
        metadata = {"scraper_config": {"enrich": []}}
        assert _board_has_enrich(metadata) is None

    def test_no_scraper_config(self):
        metadata = {}
        assert _board_has_enrich(metadata) is None

    def test_scraper_config_not_dict(self):
        metadata = {"scraper_config": "not a dict"}
        assert _board_has_enrich(metadata) is None

    def test_enrich_string_rejected(self):
        """Non-list enrich (string) is rejected by _board_has_enrich."""
        metadata = {"scraper_config": {"enrich": "description"}}
        assert _board_has_enrich(metadata) is None

    def test_multiple_fields(self):
        metadata = {"scraper_config": {"enrich": ["description", "title"]}}
        assert _board_has_enrich(metadata) == ["description", "title"]


# ── TestEnrichmentScrape ───────────────────────────────────────────


class TestEnrichmentScrape:
    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_only_updates_enriched_fields(self, mock_scrape, mock_pool, mock_http):
        """Enrich scrape uses _UPDATE_ENRICH_CONTENT, not _UPDATE_JOB_CONTENT.

        When the row is fully populated (title/locations/employment_type all
        set), backfill-on-empty is a no-op and non-enriched fields stay NULL.
        """
        pool, conn = mock_pool
        content = _job_content(description="<p>Long description</p>")
        mock_scrape.return_value = content
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": ["Existing Title"],
                "locales": ["en"],
                "location_ids": [1, 2],
                "location_types": ["physical"],
                "employment_type": "part_time",
            }
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is True
        execute_calls = conn.execute.await_args_list
        enrich_calls = [c for c in execute_calls if c.args[0] == _UPDATE_ENRICH_CONTENT]
        assert len(enrich_calls) == 1
        # Verify non-enriched fields are NULL (COALESCE preserves existing)
        call_args = enrich_calls[0].args
        assert call_args[2] is None  # employment_type
        assert call_args[3] is None  # titles
        assert call_args[4] is None  # locales

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_description_enrich_stages_r2_pending(self, mock_scrape, mock_pool, mock_http):
        """Description enrichment stages pending R2 upload with monitor metadata."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(description="<p>Rich desc</p>")
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": ["Monitor Title"],
                "locales": ["en"],
                "location_ids": None,
                "location_types": None,
            }
        )
        item = ScrapeItem(
            job_posting_id="jp-1",
            url="https://example.com/job/1",
            board_id="b-1",
            description_r2_hash=None,
        )

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is True
        # Verify the UPDATE was called
        enrich_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        assert len(enrich_calls) == 1
        # Verify description was written to descriptions table
        desc_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPSERT_DESCRIPTION]
        assert len(desc_calls) == 1
        assert desc_calls[0].args[1] == "jp-1"  # posting_id
        assert desc_calls[0].args[3] is not None  # html

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_description_enrich_populates_r2_hash_and_tech(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Description enrichment sets r2_hash and tech_ids in UPDATE params."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(description="<p>Build Python APIs</p>")
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": ["Eng"],
                "locales": ["en"],
                "location_ids": [1],
                "location_types": ["physical"],
                "employment_type": "full_time",
            }
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is True
        enrich_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        assert len(enrich_calls) == 1
        call_args = enrich_calls[0].args
        # Non-enriched fields remain NULL
        assert call_args[2] is None  # employment_type
        assert call_args[3] is None  # titles
        # Description should be written to descriptions table
        desc_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPSERT_DESCRIPTION]
        assert len(desc_calls) == 1
        assert desc_calls[0].args[3] is not None  # html

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_title_enrich_derives_occupation_seniority(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Title enrichment re-derives occupation + seniority."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title="Senior Software Engineer", description=None)
        pool.fetchrow = AsyncMock(return_value=None)
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(item, pool, mock_http, "json-ld", None, ["title"])

        assert ok is True
        enrich_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        assert len(enrich_calls) == 1
        call_args = enrich_calls[0].args
        # titles should be set
        assert call_args[3] == ["Senior Software Engineer"]
        # description-derived fields should be None
        assert call_args[7] is None  # technology_ids
        # No description write since there's no description
        desc_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPSERT_DESCRIPTION]
        assert len(desc_calls) == 0

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_title_enrich_does_not_overwrite_locales_with_default(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Title enrichment without language evidence must not overwrite locales with ["en"]."""
        pool, conn = mock_pool
        # Scraper returns a title but no language and no description
        mock_scrape.return_value = _job_content(title="Ingenieur", description=None, language=None)
        pool.fetchrow = AsyncMock(return_value=None)
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(item, pool, mock_http, "json-ld", None, ["title"])

        assert ok is True
        enrich_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        call_args = enrich_calls[0].args
        # locales ($4) must be None so COALESCE preserves monitor's richer locale data
        assert call_args[4] is None

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_title_enrich_sets_locales_when_language_detected(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Title enrichment with explicit language sets locales."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title="Ingenieur", description=None, language="de")
        pool.fetchrow = AsyncMock(return_value=None)
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(item, pool, mock_http, "json-ld", None, ["title"])

        assert ok is True
        enrich_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        call_args = enrich_calls[0].args
        assert call_args[4] == ["de"]  # locales set from explicit language

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_succeeds_without_scraper_title(self, mock_scrape, mock_pool, mock_http):
        """Enrichment succeeds even when scraper returns no title (unlike normal scrape)."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title=None, description="<p>Some desc</p>")
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": ["Monitor Title"],
                "locales": ["en"],
                "location_ids": None,
                "location_types": None,
            }
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is True
        success_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _RECORD_SCRAPE_SUCCESS
        ]
        assert len(success_calls) == 1

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_fails_when_no_enriched_data(self, mock_scrape, mock_pool, mock_http):
        """Enrichment fails when scraper returns no data for any enriched field."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title="Title", description=None, locations=None)
        pool.fetchrow = AsyncMock(return_value=None)
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is False
        failure_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _RECORD_SCRAPE_FAILURE
        ]
        assert len(failure_calls) == 1

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_fails_when_description_normalized_to_none(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Degenerate description (normalize strips to None) → failure, not infinite success."""
        pool, conn = mock_pool
        # Whitespace-only description passes raw check but normalize returns None
        mock_scrape.return_value = _job_content(title="Title", description="   ")
        pool.fetchrow = AsyncMock(return_value=None)
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is False
        failure_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _RECORD_SCRAPE_FAILURE
        ]
        assert len(failure_calls) == 1

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_no_fallback_chain(self, mock_scrape, mock_pool, mock_http):
        """Enrich runs only the primary scraper (fallback is a separate step)."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title="T", description="<p>primary desc</p>")
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": ["T"],
                "locales": ["en"],
                "location_ids": None,
                "location_types": None,
            }
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")
        config = {
            "enrich": ["description"],
            "fallback": {"type": "dom", "config": {}, "fields": ["description"]},
        }

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", config, ["description"]
        )

        assert ok is True
        # Only one scrape call (primary), no inline fallback
        assert mock_scrape.await_count == 1

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_multiple_fields(self, mock_scrape, mock_pool, mock_http):
        """Multiple enrich fields populate their derived columns, others stay NULL."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(
            title="Lead Engineer",
            description="<p>Build things</p>",
            locations=["Berlin"],
            employment_type="FULL_TIME",
        )
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": ["Old"],
                "locales": ["en"],
                "location_ids": [1],
                "location_types": ["physical"],
                "employment_type": "part_time",
            }
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description", "title"]
        )

        assert ok is True
        enrich_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        call_args = enrich_calls[0].args
        # employment_type NOT enriched → None
        assert call_args[2] is None
        # titles enriched → set
        assert call_args[3] == ["Lead Engineer"]
        # description_r2_hash enriched → may be set (R2 upload runs)

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_backfills_empty_stub_row(self, mock_scrape, mock_pool, mock_http):
        """Stub row (all core fields empty) → enrich writes title/locations/employment_type.

        Regression test for the Starbucks empty-posting bug: the hybrid
        eightfold monitor can insert URL-only stubs when PCSX hasn't run,
        and ``scraper_config: {"enrich":["description"]}`` would previously
        leave those stubs with empty title forever. Now json-ld values are
        opportunistically backfilled when the row is empty.
        """
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(
            title="Software Engineer",
            description="<p>Build things</p>",
            locations=["Berlin"],
            employment_type="FULL_TIME",
        )
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": None,
                "locales": None,
                "location_ids": None,
                "location_types": None,
                "employment_type": None,
            }
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is True
        enrich_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        assert len(enrich_calls) == 1
        call_args = enrich_calls[0].args
        # All backfilled from the scraper result
        assert call_args[2] == "full_time"  # employment_type
        assert call_args[3] == ["Software Engineer"]  # titles
        # locations resolve to ids even if the resolver returns empty (at
        # minimum the call is made — we don't assert the exact ids here
        # because the resolver is not mocked tightly)
        # description-derived tech_ids column is still populated
        # (description is in the enrich list)

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_does_not_backfill_when_row_is_populated(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Populated row → backfill is a no-op (PCSX-sourced values preserved).

        COALESCE in _UPDATE_ENRICH_CONTENT would already protect existing
        values, but we explicitly pass None so the UPDATE is a pure no-op
        for those columns and updated_at doesn't flip.
        """
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(
            title="Stale Scraper Title",
            description="<p>Fresh desc</p>",
            locations=["Wrong City"],
            employment_type="PART_TIME",
        )
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": ["PCSX Title"],
                "locales": ["en"],
                "location_ids": [42],
                "location_types": ["physical"],
                "employment_type": "full_time",
            }
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is True
        enrich_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        call_args = enrich_calls[0].args
        assert call_args[2] is None  # employment_type: not overwritten
        assert call_args[3] is None  # titles: not overwritten
        assert call_args[5] is None  # location_ids: not overwritten
        assert call_args[6] is None  # location_types: not overwritten

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_skips_backfill_for_garbage_scraped_title(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Garbage scraped title → do not backfill, leave row for PCSX to fill later."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(
            title="Page Not Found",  # in _GARBAGE_TITLES
            description="<p>real desc</p>",
            locations=None,
            employment_type=None,
        )
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": None,
                "locales": None,
                "location_ids": None,
                "location_types": None,
                "employment_type": None,
            }
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is True
        enrich_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_ENRICH_CONTENT
        ]
        call_args = enrich_calls[0].args
        # Garbage title must not be persisted
        assert call_args[3] is None  # titles

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_enrich_exception_records_failure(self, mock_scrape, mock_pool, mock_http):
        """Exception during enrich → _RECORD_SCRAPE_FAILURE, returns False."""
        pool, conn = mock_pool
        mock_scrape.side_effect = RuntimeError("network error")
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _ = await _process_one_enrich_scrape(
            item, pool, mock_http, "json-ld", None, ["description"]
        )

        assert ok is False
        failure_calls = [
            c for c in conn.execute.await_args_list if c.args[0] == _RECORD_SCRAPE_FAILURE
        ]
        assert len(failure_calls) == 1
        assert failure_calls[0].args[1] == "jp-1"

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_dispatch_from_process_one_scrape(self, mock_scrape, mock_pool, mock_http):
        """_process_one_scrape dispatches to enrich path when config has enrich."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(description="<p>desc</p>")
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": ["T"],
                "locales": ["en"],
                "location_ids": None,
                "location_types": None,
            }
        )
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")
        config = {"enrich": ["description"]}

        ok, _ = await _process_one_scrape(item, pool, mock_http, "json-ld", config)

        assert ok is True
        execute_calls = conn.execute.await_args_list
        # Both paths now use COALESCE update (_UPDATE_ENRICH_CONTENT pattern)
        update_calls = [c for c in execute_calls if c.args[0] == _UPDATE_ENRICH_CONTENT]
        assert len(update_calls) == 1

    @patch("src.redis_queue.enqueue_scrape", new_callable=AsyncMock)
    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_no_dispatch_without_enrich(
        self, mock_scrape, mock_enqueue, mock_pool, mock_http
    ):
        """_process_one_scrape uses normal (COALESCE) path when config has no enrich."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content()
        mock_enqueue.return_value = True
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")
        config = {"fallback": {"type": "dom", "config": {}}}

        ok, _ = await _process_one_scrape(item, pool, mock_http, "json-ld", config)

        assert ok is True
        execute_calls = conn.execute.await_args_list
        # All scrape saves now use COALESCE pattern
        update_calls = [c for c in execute_calls if c.args[0] == _UPDATE_ENRICH_CONTENT]
        assert len(update_calls) == 1


# ── TestMonitorEnrichInsert ────────────────────────────────────────


class TestMonitorEnrichInsert:
    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_rich_enrich_new_jobs_get_next_scrape_at(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Rich monitor + enrich → new jobs use _INSERT_RICH_JOB_ENRICH (next_scrape_at = now())."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        job1 = _discovered_job(url=url1)
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url={url1: job1},
            )
        )
        conn.fetch.return_value = [_diff_row("new", url=url1)]
        board = _mock_board(metadata={"scraper_config": {"enrich": ["description"]}})

        await _process_one_board(board, pool, mock_http)

        call_args = conn.fetchrow.await_args_list
        insert_sqls = [c.args[0] for c in call_args]
        assert _INSERT_RICH_JOB_ENRICH in insert_sqls
        assert _INSERT_RICH_JOB not in insert_sqls

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_rich_no_enrich_uses_standard_insert(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Rich monitor, no enrich → standard _INSERT_RICH_JOB (next_scrape_at = NULL)."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        job1 = _discovered_job(url=url1)
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url={url1: job1},
            )
        )
        conn.fetch.return_value = [_diff_row("new", url=url1)]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        call_args = conn.fetchrow.await_args_list
        insert_sqls = [c.args[0] for c in call_args]
        assert _INSERT_RICH_JOB in insert_sqls
        assert _INSERT_RICH_JOB_ENRICH not in insert_sqls

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_relisted_on_enrich_board_passes_false_to_diff(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Enrich board → DIFF_URLS $4 (is_rich_no_scrape) = False."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        job1 = _discovered_job(url=url1)
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url={url1: job1},
            )
        )
        conn.fetch.return_value = [
            _diff_row("relisted", row_id="jp-relisted", url=url1),
        ]
        board = _mock_board(metadata={"scraper_config": {"enrich": ["description"]}})

        await _process_one_board(board, pool, mock_http)

        # Find the _DIFF_BATCH call — it's the one with 4 args (query, urls, board_id,
        # is_rich_no_scrape)
        diff_call = None
        for c in conn.fetch.await_args_list:
            if len(c.args) >= 4 and isinstance(c.args[3], bool):
                diff_call = c
                break
        assert diff_call is not None, "No _DIFF_BATCH call found"
        assert diff_call.args[3] is False  # is_rich_no_scrape = False for enrich boards

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_non_enrich_board_passes_true_to_diff(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Non-enrich rich board → DIFF_URLS $4 (is_rich_no_scrape) = True."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        job1 = _discovered_job(url=url1)
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url={url1: job1},
            )
        )
        conn.fetch.return_value = [
            _diff_row("relisted", row_id="jp-relisted", url=url1),
        ]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        diff_call = None
        for c in conn.fetch.await_args_list:
            if len(c.args) >= 4 and isinstance(c.args[3], bool):
                diff_call = c
                break
        assert diff_call is not None, "No _DIFF_BATCH call found"
        assert diff_call.args[3] is True  # is_rich_no_scrape = True


# ── TestLoadBoardScrapersEnrich ────────────────────────────────────


class TestLoadBoardScrapersEnrich:
    async def test_enrich_board_not_in_rich_ids(self, mock_pool):
        """Board with enrich is NOT added to rich_board_ids."""
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {"scraper_config": {"enrich": ["description"]}},
                "crawler_type": "greenhouse",
            }
        ]

        info = await _load_board_scrapers(pool, {"b-1"})

        assert "b-1" not in info.rich_board_ids
        assert "b-1" in info.scrapers
        assert info.scrapers["b-1"].scraper_type == "json-ld"

    async def test_explicit_skip_with_enrich_not_in_rich_ids(self, mock_pool):
        """Explicit scraper_type=skip with enrich → not rich, gets json-ld scraper."""
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {
                    "scraper_type": "skip",
                    "scraper_config": {"enrich": ["description"]},
                },
                "crawler_type": "greenhouse",
            }
        ]

        info = await _load_board_scrapers(pool, {"b-1"})

        assert "b-1" not in info.rich_board_ids
        assert "b-1" in info.scrapers
        assert info.scrapers["b-1"].scraper_type == "json-ld"

    async def test_rich_monitor_without_enrich_still_skipped(self, mock_pool):
        """Rich monitor without enrich → still in rich_board_ids."""
        pool, _ = mock_pool
        pool.fetch.return_value = [{"id": "b-1", "metadata": {}, "crawler_type": "greenhouse"}]

        info = await _load_board_scrapers(pool, {"b-1"})

        assert "b-1" in info.rich_board_ids
        assert "b-1" not in info.scrapers

    async def test_explicit_scraper_type_with_enrich(self, mock_pool):
        """Explicit scraper_type (non-skip) + enrich → uses that scraper type."""
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {
                    "scraper_type": "dom",
                    "scraper_config": {"enrich": ["description"], "render": False},
                },
                "crawler_type": "greenhouse",
            }
        ]

        info = await _load_board_scrapers(pool, {"b-1"})

        assert "b-1" not in info.rich_board_ids
        assert "b-1" in info.scrapers
        assert info.scrapers["b-1"].scraper_type == "dom"
        assert info.scrapers["b-1"].scraper_config["enrich"] == ["description"]

    async def test_enrich_config_preserved_in_scraper_config(self, mock_pool):
        """The enrich key inside scraper_config is preserved for dispatch."""
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {
                    "scraper_config": {
                        "enrich": ["description"],
                        "fallback": {"type": "dom", "config": {}},
                    }
                },
                "crawler_type": "greenhouse",
            }
        ]

        info = await _load_board_scrapers(pool, {"b-1"})

        cfg = info.scrapers["b-1"]
        assert cfg.scraper_config["enrich"] == ["description"]
        assert cfg.scraper_config["fallback"]["type"] == "dom"

    async def test_use_proxy_derived_from_scraper_config(self, mock_pool):
        """``scraper_config.proxy = true`` lifts to ``BoardScraperConfig.use_proxy``."""
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {
                    "scraper_type": "json-ld",
                    "scraper_config": {"enrich": ["description"], "proxy": True},
                },
                "crawler_type": "eightfold",
            }
        ]
        info = await _load_board_scrapers(pool, {"b-1"})
        assert info.scrapers["b-1"].use_proxy is True

    async def test_use_proxy_false_by_default(self, mock_pool):
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {
                    "scraper_type": "json-ld",
                    "scraper_config": {"enrich": ["description"]},
                },
                "crawler_type": "greenhouse",
            }
        ]
        info = await _load_board_scrapers(pool, {"b-1"})
        assert info.scrapers["b-1"].use_proxy is False

    async def test_use_proxy_ignored_at_top_level_metadata(self, mock_pool):
        """Top-level ``metadata.proxy`` is for the monitor, not the scraper.

        The scraper only reads ``scraper_config.proxy``. If the operator
        sets it at the flattened top level by mistake, the scraper must
        stay direct (and the validator + docs guide them to fix it).
        """
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {
                    "scraper_type": "json-ld",
                    "scraper_config": {"enrich": ["description"]},
                    "proxy": True,  # wrong level — scraper must not pick it up
                },
                "crawler_type": "eightfold",
            }
        ]
        info = await _load_board_scrapers(pool, {"b-1"})
        assert info.scrapers["b-1"].use_proxy is False


# ── TestEnrichValidation ───────────────────────────────────────────


class TestEnrichValidation:
    def test_valid_enrich_fields_exist(self):
        from src.inspect import _JOBCONTENT_FIELD_NAMES

        assert "description" in _JOBCONTENT_FIELD_NAMES
        assert "title" in _JOBCONTENT_FIELD_NAMES
        assert "locations" in _JOBCONTENT_FIELD_NAMES
        assert "employment_type" in _JOBCONTENT_FIELD_NAMES

    def test_invalid_field_not_in_set(self):
        from src.inspect import _JOBCONTENT_FIELD_NAMES

        assert "nonexistent" not in _JOBCONTENT_FIELD_NAMES
        assert "salary_min" not in _JOBCONTENT_FIELD_NAMES  # derived, not a JobContent field

    def test_enrich_not_list_rejected(self):
        """Non-list enrich is rejected by _board_has_enrich."""
        assert _board_has_enrich({"scraper_config": {"enrich": "description"}}) is None
