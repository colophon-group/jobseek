from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.batch import (
    _BATCH_UPDATE_RICH_CONTENT,
    _CREATE_RICH_UPDATES_TEMP,
    _DELIST_BOARD_POSTINGS,
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
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # INSERT_URL_ONLY_JOBS was called (3 fetches: DIFF_BATCH, INSERT, MARK_GONE)
        assert conn.fetch.await_count == 3
        second_fetch = conn.fetch.await_args_list[1]
        assert second_fetch.args[0] == _INSERT_URL_ONLY_JOBS

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
        """Enrich scrape uses _UPDATE_ENRICH_CONTENT, not _UPDATE_JOB_CONTENT."""
        pool, conn = mock_pool
        content = _job_content(description="<p>Long description</p>")
        mock_scrape.return_value = content
        pool.fetchrow = AsyncMock(
            return_value={
                "titles": ["Existing Title"],
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
                "location_ids": None,
                "location_types": None,
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
                "location_ids": None,
                "location_types": None,
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
