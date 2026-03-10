from __future__ import annotations

import json
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.batch import (
    _BATCH_UPDATE_RICH_CONTENT,
    _CLAIM_MONITORS,
    _CLAIM_SCRAPES,
    _CREATE_RICH_UPDATES_TEMP,
    _DELIST_BOARD_POSTINGS,
    _INSERT_RICH_JOB,
    _INSERT_URL_ONLY_JOBS,
    _RECORD_EMPTY_CHECK,
    _RECORD_FAILURE,
    _RECORD_SCRAPE_FAILURE,
    _RECORD_SCRAPE_SUCCESS,
    _UPDATE_JOB_CONTENT,
    _UPDATE_METADATA,
    BatchResult,
    BoardScraperConfig,
    ScrapeItem,
    _coerce_datetime,
    _coerce_text,
    _jsonb,
    _load_board_scrapers,
    _monitor_pipeline,
    _PipelineResult,
    _process_one_board,
    _process_one_scrape,
    _scrape_pipeline,
    _throttle_key,
    claim_monitor_work,
    claim_scrape_work,
    process_monitor_batch,
    process_scrape_batch,
)
from src.core.monitor import MonitorResult
from src.core.monitors import DiscoveredJob, api_monitor_types
from src.core.scrapers import JobContent


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


@pytest.fixture
def mock_pool():
    """Return (pool, conn) where pool.acquire() yields conn inside a transaction."""
    pool = AsyncMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool.fetch = AsyncMock(return_value=[])

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


def _diff_row(action, row_id=None, url="https://example.com/job/1"):
    """Create a dict-like mock for a DIFF_URLS result row."""
    data = {"action": action, "id": row_id, "url": url}
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


def _inserted_row(row_id, source_url):
    """Create a dict-like mock for an INSERT_URL_ONLY_JOBS result row."""
    data = {"id": row_id, "source_url": source_url}
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


# ── TestProcessOneBoard ──────────────────────────────────────────────


class TestProcessOneBoard:
    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one")
    async def test_empty_result_records_empty_check(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Monitor returns empty urls -> _RECORD_EMPTY_CHECK called, no transaction."""
        pool, conn = mock_pool
        mock_monitor.return_value = MonitorResult(urls=set())
        # _RECORD_EMPTY_CHECK now uses RETURNING, so conn.fetch returns rows
        conn.fetch.return_value = [{"board_status": "active"}]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        conn.fetch.assert_awaited_once_with(_RECORD_EMPTY_CHECK, "board-1")
        mock_get_redis.assert_not_called()

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one")
    async def test_empty_result_board_gone_delists_postings(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Board transitions to 'gone' after repeated empties -> delist all postings."""
        pool, conn = mock_pool
        mock_monitor.return_value = MonitorResult(urls=set())
        # Simulate board transitioning to gone
        conn.fetch.return_value = [{"board_status": "gone"}]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # Both _RECORD_EMPTY_CHECK (via fetch) and _DELIST_BOARD_POSTINGS (via execute) called
        conn.fetch.assert_awaited_once_with(_RECORD_EMPTY_CHECK, "board-1")
        conn.execute.assert_awaited_once_with(_DELIST_BOARD_POSTINGS, "board-1")

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one")
    async def test_rich_data_inserts_new_jobs(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Monitor returns DiscoveredJobs -> executemany with _INSERT_RICH_JOB."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        url2 = "https://example.com/job/2"
        job1 = _discovered_job(url=url1)
        job2 = _discovered_job(url=url2, title="Designer")
        mock_monitor.return_value = MonitorResult(
            urls={url1, url2},
            jobs_by_url={url1: job1, url2: job2},
        )
        # DIFF returns both as new
        conn.fetch.return_value = [
            _diff_row("new", url=url1),
            _diff_row("new", url=url2),
        ]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # executemany called with _INSERT_RICH_JOB
        conn.executemany.assert_awaited()
        call_args = conn.executemany.await_args_list
        assert any(c.args[0] == _INSERT_RICH_JOB for c in call_args)

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one")
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
        mock_monitor.return_value = MonitorResult(
            urls={url1},
            jobs_by_url={url1: job1},
        )
        conn.fetch.return_value = [_diff_row("new", url=url1)]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        insert_calls = [
            c for c in conn.executemany.await_args_list if c.args[0] == _INSERT_RICH_JOB
        ]
        assert len(insert_calls) == 1
        payload = insert_calls[0].args[1]
        assert len(payload) == 1
        assert payload[0][3] == "<p>Hello</p>"

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one")
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
        mock_monitor.return_value = MonitorResult(urls={url1}, jobs_by_url=None)
        conn.fetch.side_effect = [
            # First fetch call: DIFF_URLS
            [_diff_row("new", url=url1)],
            # Second fetch call: INSERT_URL_ONLY_JOBS
            [_inserted_row("jp-1", url1)],
        ]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        # INSERT_URL_ONLY_JOBS was called
        assert conn.fetch.await_count == 2
        second_fetch = conn.fetch.await_args_list[1]
        assert second_fetch.args[0] == _INSERT_URL_ONLY_JOBS

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one")
    async def test_gone_jobs_in_diff(self, mock_monitor, mock_get_redis, mock_pool, mock_http):
        """DIFF_URLS returns 'gone' rows -> they trigger cache invalidation."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.return_value = MonitorResult(urls={url1}, jobs_by_url=None)
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
    @patch("src.batch.monitor_one")
    async def test_relisted_jobs_content_update(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Rich data with relisted rows -> bulk update via temp table."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        job1 = _discovered_job(url=url1)
        mock_monitor.return_value = MonitorResult(
            urls={url1},
            jobs_by_url={url1: job1},
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
    @patch("src.batch.monitor_one")
    async def test_new_sitemap_url_updates_metadata(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """result.new_sitemap_url set -> _UPDATE_METADATA called."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.return_value = MonitorResult(
            urls={url1},
            jobs_by_url=None,
            new_sitemap_url="https://example.com/sitemap-jobs.xml",
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
    @patch("src.batch.monitor_one")
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
    @patch("src.batch.monitor_one")
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
    @patch("src.batch.monitor_one")
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
        mock_monitor.return_value = MonitorResult(urls={url1}, jobs_by_url=None)
        conn.fetch.side_effect = [
            [_diff_row("new", url=url1)],
            [_inserted_row("jp-1", url1)],
        ]
        board = _mock_board()

        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis

        await _process_one_board(board, pool, mock_http)

        mock_redis.delete.assert_awaited_with("cache:platform-stats")

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one")
    async def test_no_cache_invalidation_when_no_changes(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """No new/gone jobs -> get_redis().delete NOT called."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.return_value = MonitorResult(
            urls={url1},
            jobs_by_url={url1: _discovered_job(url=url1)},
        )
        # Only existing active jobs, no new/gone/relisted
        conn.fetch.return_value = []
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        mock_get_redis.assert_not_called()


# ── TestMonitorPipeline ──────────────────────────────────────────────


class TestMonitorPipeline:
    @patch("src.batch._process_one_board", new_callable=AsyncMock)
    async def test_all_succeed(self, mock_process, mock_pool, mock_http):
        """3 boards, all succeed -> returns 3."""
        pool, _ = mock_pool
        boards = [_mock_board(id=f"b-{i}") for i in range(3)]
        mock_process.return_value = (True, 1.0)

        result = await _monitor_pipeline(boards, pool, mock_http)

        assert result.succeeded == 3
        assert len(result.durations) == 3
        assert mock_process.await_count == 3

    @patch("src.batch._process_one_board", new_callable=AsyncMock)
    async def test_partial_failure(self, mock_process, mock_pool, mock_http):
        """3 boards, 1 raises in _process_one_board -> returns 2."""
        pool, _ = mock_pool
        boards = [_mock_board(id=f"b-{i}") for i in range(3)]
        mock_process.side_effect = [(True, 1.0), RuntimeError("fail"), (True, 1.0)]

        result = await _monitor_pipeline(boards, pool, mock_http)

        assert result.succeeded == 2

    @patch("src.batch._process_one_board", new_callable=AsyncMock)
    async def test_counts_false_return_as_failure(self, mock_process, mock_pool, mock_http):
        """_process_one_board False result should count as failed."""
        pool, _ = mock_pool
        boards = [_mock_board(id=f"b-{i}") for i in range(3)]
        mock_process.side_effect = [(True, 1.0), (False, 2.0), (True, 1.0)]

        result = await _monitor_pipeline(boards, pool, mock_http)

        assert result.succeeded == 2

    @patch("src.batch._process_one_board", new_callable=AsyncMock)
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
        assert "job_posting_not_found:jp-missing" in failure_calls[0].args[2]

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
        assert "scrape error" in failure_calls[0].args[2]

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_failure_uses_exception_type_on_blank_error(
        self, mock_scrape, mock_pool, mock_http
    ):
        """Scrape failure should receive non-empty fallback text when str(exc) is blank."""
        pool, conn = mock_pool
        mock_scrape.side_effect = RuntimeError()
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        assert ok is False
        execute_calls = conn.execute.await_args_list
        failure_calls = [c for c in execute_calls if c.args[0] == _RECORD_SCRAPE_FAILURE]
        assert len(failure_calls) == 1
        assert failure_calls[0].args[2] == "RuntimeError"

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
        update_calls = [c for c in conn.execute.await_args_list if c.args[0] == _UPDATE_JOB_CONTENT]
        assert len(update_calls) == 1
        call_args = update_calls[0].args
        assert call_args[1] == "jp-1"  # job_posting_id
        assert call_args[2] == "Senior Engineer"  # title
        assert call_args[3] == "<p>Description</p>"  # description
        assert call_args[4] == ["London", "Remote"]  # locations
        assert call_args[5] == "FULL_TIME"  # employment_type
        assert call_args[6] == "hybrid"  # job_location_type
        # base_salary is passed through _jsonb
        expected_salary = {"currency": "GBP", "min": 80000, "max": 120000, "unit": "YEAR"}
        assert json.loads(call_args[7]) == expected_salary
        assert call_args[8] is not None
        assert call_args[8].isoformat() == "2025-06-01T00:00:00+00:00"  # date_posted
        # language (detected or None)
        # call_args[9] is language
        expected_extras = {"skills": ["Python", "SQL"], "responsibilities": ["Lead team"]}
        assert json.loads(call_args[10]) == expected_extras  # extras
        assert json.loads(call_args[11]) == {"team": "Platform"}  # metadata

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
        assert update_calls[0].args[3] == "<p>Hi</p>"

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_fallback_on_empty_primary(self, mock_scrape, mock_pool, mock_http):
        """Primary returns no title, fallback succeeds -> True."""
        pool, conn = mock_pool
        mock_scrape.side_effect = [_job_content(title=None), _job_content(title="Fallback Title")]
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(
            item, pool, mock_http, "json-ld", None, "dom", {"render": True}
        )

        assert ok is True
        assert mock_scrape.await_count == 2
        # First call: primary scraper
        assert mock_scrape.await_args_list[0].args[1] == "json-ld"
        # Second call: fallback scraper
        assert mock_scrape.await_args_list[1].args[1] == "dom"

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_no_fallback_when_primary_succeeds(self, mock_scrape, mock_pool, mock_http):
        """Primary has title -> fallback is not called."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title="Primary Title")
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(
            item, pool, mock_http, "json-ld", None, "dom", {"render": True}
        )

        assert ok is True
        assert mock_scrape.await_count == 1

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_both_scrapers_fail(self, mock_scrape, mock_pool, mock_http):
        """Primary and fallback both return empty -> records failure."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title=None)
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(
            item, pool, mock_http, "json-ld", None, "dom", {"render": True}
        )

        assert ok is False
        assert mock_scrape.await_count == 2
        execute_calls = conn.execute.await_args_list
        failure_calls = [c for c in execute_calls if c.args[0] == _RECORD_SCRAPE_FAILURE]
        assert len(failure_calls) == 1

    @patch("src.batch.scrape_one", new_callable=AsyncMock)
    async def test_no_fallback_without_config(self, mock_scrape, mock_pool, mock_http):
        """No fallback configured -> does not retry on empty primary."""
        pool, conn = mock_pool
        mock_scrape.return_value = _job_content(title=None)
        item = ScrapeItem(job_posting_id="jp-1", url="https://example.com/job/1", board_id="b-1")

        ok, _duration = await _process_one_scrape(item, pool, mock_http, "json-ld", None)

        assert ok is False
        assert mock_scrape.await_count == 1

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
        assert call_args[5] == "Temporary positions, Full-time"
        assert call_args[8] is not None
        assert call_args[8].tzinfo is not None
        assert call_args[8].astimezone(UTC).isoformat() == "2026-02-06T12:31:18+00:00"


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
    async def test_passes_fallback_config(self, mock_process, mock_pool, mock_http):
        """Fallback scraper settings should be passed through to _process_one_scrape."""
        pool, _ = mock_pool
        mock_process.return_value = (True, 1.0)
        items = [ScrapeItem(job_posting_id="jp-1", url="https://alpha.com/job/1", board_id="b-1")]
        board_scrapers = {
            "b-1": BoardScraperConfig(
                scraper_type="json-ld",
                scraper_config=None,
                fallback_scraper_type="dom",
                fallback_scraper_config={"render": True},
            )
        }

        result = await _scrape_pipeline(items, pool, mock_http, board_scrapers)

        assert result.succeeded == 1
        call_args = mock_process.await_args
        assert call_args.args[3] == "json-ld"
        assert call_args.args[4] is None
        assert call_args.args[5] == "dom"
        assert call_args.args[6] == {"render": True}


class TestLoadBoardScrapers:
    async def test_loads_scraper_from_metadata(self, mock_pool):
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {"id": "b-1", "metadata": {"scraper_type": "dom", "scraper_config": {"render": False}}}
        ]

        result = await _load_board_scrapers(pool, {"b-1"})

        cfg = result["b-1"]
        assert cfg.scraper_type == "dom"
        assert cfg.scraper_config == {"render": False}
        assert cfg.fallback_scraper_type is None
        assert cfg.fallback_scraper_config is None

    async def test_falls_back_on_invalid_scraper(self, mock_pool):
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {"id": "b-1", "metadata": {"scraper_type": "nope", "scraper_config": {"x": 1}}}
        ]

        result = await _load_board_scrapers(pool, {"b-1"})

        cfg = result["b-1"]
        assert cfg.scraper_type == "json-ld"
        assert cfg.scraper_config is None

    async def test_loads_fallback_from_metadata(self, mock_pool):
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {
                    "scraper_type": "json-ld",
                    "fallback_scraper_type": "dom",
                    "fallback_scraper_config": {"render": True},
                },
            }
        ]

        result = await _load_board_scrapers(pool, {"b-1"})

        cfg = result["b-1"]
        assert cfg.scraper_type == "json-ld"
        assert cfg.fallback_scraper_type == "dom"
        assert cfg.fallback_scraper_config == {"render": True}

    async def test_ignores_invalid_fallback_scraper(self, mock_pool):
        pool, _ = mock_pool
        pool.fetch.return_value = [
            {
                "id": "b-1",
                "metadata": {
                    "scraper_type": "json-ld",
                    "fallback_scraper_type": "nope",
                    "fallback_scraper_config": {"x": 1},
                },
            }
        ]

        result = await _load_board_scrapers(pool, {"b-1"})

        cfg = result["b-1"]
        assert cfg.scraper_type == "json-ld"
        assert cfg.fallback_scraper_type is None
        assert cfg.fallback_scraper_config is None


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
    async def test_worker_id_passed_to_fetch(self, mock_pipeline, mock_pool, mock_http):
        """worker_id is passed as $2 to the job claim query."""
        pool, _ = mock_pool
        pool.fetch.return_value = []

        await process_scrape_batch(pool, mock_http, worker_id="test-w")

        pool.fetch.assert_awaited_once()
        call_args = pool.fetch.await_args.args
        assert call_args[1] == 200  # limit
        assert call_args[2] == "test-w"  # worker_id


# ── TestClaimMonitorWork ─────────────────────────────────────────────


def _mock_board_row(**overrides):
    """Create a dict-like mock for a CLAIM_MONITORS result row."""
    defaults = {
        "id": "board-1",
        "company_id": "company-1",
        "board_url": "https://example.com/jobs",
        "crawler_type": "greenhouse",
        "throttle_key": "greenhouse",
        "metadata": None,
    }
    defaults.update(overrides)
    record = MagicMock()
    record.__getitem__ = lambda self, key: defaults[key]
    return record


class TestClaimMonitorWork:
    async def test_empty_result(self, mock_pool, mock_http):
        """No due boards → empty list."""
        pool, _ = mock_pool
        pool.fetch.return_value = []
        items = await claim_monitor_work(pool, mock_http, 10, "w", [])
        assert items == []

    async def test_correct_domain(self, mock_pool, mock_http):
        """WorkItem.domain comes from throttle_key."""
        pool, _ = mock_pool
        pool.fetch.return_value = [_mock_board_row(throttle_key="greenhouse")]
        items = await claim_monitor_work(pool, mock_http, 10, "w", [])
        assert len(items) == 1
        assert items[0].domain == "greenhouse"
        assert items[0].kind == "monitor"

    async def test_exclude_domains_passed(self, mock_pool, mock_http):
        """Exclude domains list is passed as $3 to the query."""
        pool, _ = mock_pool
        pool.fetch.return_value = []
        await claim_monitor_work(pool, mock_http, 5, "w1", ["greenhouse", "lever"])
        call_args = pool.fetch.await_args.args
        assert call_args[0] == _CLAIM_MONITORS
        assert call_args[1] == 5
        assert call_args[2] == "w1"
        assert call_args[3] == ["greenhouse", "lever"]

    async def test_limit_zero_noop(self, mock_pool, mock_http):
        """limit=0 returns empty without querying."""
        pool, _ = mock_pool
        items = await claim_monitor_work(pool, mock_http, 0, "w", [])
        assert items == []
        pool.fetch.assert_not_awaited()

    @patch("src.batch._process_one_board", new_callable=AsyncMock)
    async def test_run_calls_process_one_board(self, mock_process, mock_pool, mock_http):
        """WorkItem.run() calls _process_one_board with correct args."""
        pool, _ = mock_pool
        board_row = _mock_board_row()
        pool.fetch.return_value = [board_row]
        mock_process.return_value = (True, 1.0)

        items = await claim_monitor_work(pool, mock_http, 10, "w", [])
        result = await items[0].run()

        assert result == (True, 1.0)
        mock_process.assert_awaited_once_with(board_row, pool, mock_http)


# ── TestClaimScrapeWork ──────────────────────────────────────────────


def _mock_scrape_row(**overrides):
    """Create a dict-like mock for a CLAIM_SCRAPES result row."""
    defaults = {
        "id": "jp-1",
        "source_url": "https://example.com/jobs/1",
        "board_id": "board-1",
        "scrape_domain": "example.com",
    }
    defaults.update(overrides)
    record = MagicMock()
    record.__getitem__ = lambda self, key: defaults[key]
    return record


class TestClaimScrapeWork:
    @patch("src.batch._load_board_scrapers", new_callable=AsyncMock)
    async def test_empty_result(self, mock_scrapers, mock_pool, mock_http):
        """No due postings → empty list."""
        pool, _ = mock_pool
        pool.fetch.return_value = []
        items = await claim_scrape_work(pool, mock_http, 10, "w", [])
        assert items == []
        mock_scrapers.assert_not_awaited()

    @patch("src.batch._load_board_scrapers", new_callable=AsyncMock)
    async def test_correct_domain(self, mock_scrapers, mock_pool, mock_http):
        """WorkItem.domain comes from scrape_domain."""
        pool, _ = mock_pool
        pool.fetch.return_value = [_mock_scrape_row(scrape_domain="example.com")]
        mock_scrapers.return_value = {}
        items = await claim_scrape_work(pool, mock_http, 10, "w", [])
        assert len(items) == 1
        assert items[0].domain == "example.com"
        assert items[0].kind == "scrape"

    @patch("src.batch._load_board_scrapers", new_callable=AsyncMock)
    async def test_null_scrape_domain_fallback(self, mock_scrapers, mock_pool, mock_http):
        """NULL scrape_domain falls back to urlparse hostname."""
        pool, _ = mock_pool
        pool.fetch.return_value = [
            _mock_scrape_row(
                scrape_domain=None,
                source_url="https://careers.acme.com/job/42",
            )
        ]
        mock_scrapers.return_value = {}
        items = await claim_scrape_work(pool, mock_http, 10, "w", [])
        assert items[0].domain == "careers.acme.com"

    @patch("src.batch._load_board_scrapers", new_callable=AsyncMock)
    async def test_exclude_domains_passed(self, mock_scrapers, mock_pool, mock_http):
        """Exclude domains list is passed as $3 to the query."""
        pool, _ = mock_pool
        pool.fetch.return_value = []
        await claim_scrape_work(pool, mock_http, 5, "w1", ["example.com"])
        call_args = pool.fetch.await_args.args
        assert call_args[0] == _CLAIM_SCRAPES
        assert call_args[1] == 5
        assert call_args[2] == "w1"
        assert call_args[3] == ["example.com"]

    @patch("src.batch._load_board_scrapers", new_callable=AsyncMock)
    async def test_limit_zero_noop(self, mock_scrapers, mock_pool, mock_http):
        """limit=0 returns empty without querying."""
        pool, _ = mock_pool
        items = await claim_scrape_work(pool, mock_http, 0, "w", [])
        assert items == []
        pool.fetch.assert_not_awaited()
        mock_scrapers.assert_not_awaited()

    @patch("src.batch._load_board_scrapers", new_callable=AsyncMock)
    async def test_board_scrapers_loaded(self, mock_scrapers, mock_pool, mock_http):
        """Board scrapers are loaded for all claimed items."""
        pool, _ = mock_pool
        pool.fetch.return_value = [
            _mock_scrape_row(board_id="b1"),
            _mock_scrape_row(id="jp-2", board_id="b2", source_url="https://other.com/j"),
        ]
        mock_scrapers.return_value = {
            "b1": BoardScraperConfig(scraper_type="dom", scraper_config={"sel": "h1"}),
        }
        items = await claim_scrape_work(pool, mock_http, 10, "w", [])
        assert len(items) == 2
        mock_scrapers.assert_awaited_once_with(pool, {"b1", "b2"})
