"""Regression tests for the MAX_JOBS truncation guard (#3216).

Several monitors paginate against ATS APIs and silently capped collection
at ``MAX_JOBS`` (50,000 by default). The cap is a safety stop, but the
unseen tail beyond it would otherwise be tombstoned by
``_MARK_GONE_BY_TIMESTAMP`` on the next monitor cycle — the silent
data-loss shape #2722, #2737, #2748 already covered for fetch-failure-driven
truncation.

These tests pin the contract:

1. The :mod:`src.shared.truncation` helpers return a ``MonitorResult``
   with ``truncated=True`` so the board processor can distinguish a
   complete discovery from a capped one.
2. When **any** batch in a streamed monitor run carries ``truncated=True``,
   ``_process_one_board_streaming`` records the cycle as success but
   skips ``_MARK_GONE_BY_TIMESTAMP`` for the whole cycle and increments
   the ``crawler_monitor_truncated_total{board_id=...}`` counter.
3. URLs that ARE in the truncated batch are still inserted normally —
   only gone-detection is suppressed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.batch import (
    _DIFF_BATCH,
    _INSERT_RICH_JOB,
    _INSERT_URL_ONLY_JOBS,
    _RECORD_SUCCESS_NONEMPTY,
    _process_one_board_streaming,
)
from src.core.location_resolve import LocationResolver
from src.core.monitor import MonitorResult
from src.core.monitors import DiscoveredJob
from src.processing.board import DeadlineExtender
from src.queries.monitor import _MARK_GONE_BY_TIMESTAMP
from src.shared.truncation import truncated_rich_result, truncated_url_result

# ── Test-isolation fixtures (mirror conftest patterns from test_batch.py) ──


@pytest.fixture(autouse=True)
def _mock_location_resolver(monkeypatch):
    """Auto-mock the location resolver so tests don't hit the DB."""
    resolver = LocationResolver()
    resolver._init_db(":memory:")
    resolver._loaded = True

    async def _fake_get_resolver(pool):
        return resolver

    monkeypatch.setattr("src.batch._get_location_resolver", _fake_get_resolver)


@pytest.fixture(autouse=True)
def _mock_currency_rates(monkeypatch):
    rates = {"EUR": 1.0, "USD": 0.87, "GBP": 1.17, "CHF": 1.04}

    async def _fake_get_rates(pool):
        return rates

    monkeypatch.setattr("src.batch._get_currency_rates", _fake_get_rates)


@pytest.fixture(autouse=True)
def _mock_enqueue_scrapes(monkeypatch):
    monkeypatch.setattr("src.processing.board._enqueue_scrapes_for_new", AsyncMock())
    monkeypatch.setattr("src.processing.board._enqueue_scrapes_for_relisted", AsyncMock())


# ── Shared helpers (subset of test_batch.py helpers) ─────────────────────


def _mock_board(**overrides):
    """asyncpg.Record-shaped MagicMock."""
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


def _diff_row(action, row_id=None, url="https://example.com/job/1", r2_hash=None):
    data = {"action": action, "id": row_id, "url": url, "description_r2_hash": r2_hash}
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


def _inserted_row(row_id, source_url):
    data = {"id": row_id, "source_url": source_url}
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


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


def _mock_stream(*results):
    async def _gen(*args, **kwargs):
        for r in results:
            yield r

    return _gen


def _counter_value(metric, **labels):
    """Public-API counter read (avoids private ``_value`` access)."""
    expected = labels
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith("_total") and sample.labels == expected:
                return sample.value
    return 0.0


@pytest.fixture
def mock_pool():
    """Pool/conn pair matching the test_batch.py default — empty COUNT row
    so the blast-radius guard never fires on its own."""
    from unittest.mock import DEFAULT

    from src.queries.monitor import _COUNT_BOARD_ACTIVE_AND_MISSING

    pool = AsyncMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    async def _default_fetchrow(sql, *args, **kwargs):
        if sql == _COUNT_BOARD_ACTIVE_AND_MISSING:
            return {"active": 0, "missing": 0}
        return DEFAULT

    conn.fetchrow = AsyncMock(side_effect=_default_fetchrow)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value="2026-01-01T00:00:00+00:00")

    acq_cm = AsyncMock()
    acq_cm.__aenter__ = AsyncMock(return_value=conn)
    acq_cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq_cm)

    tx_cm = AsyncMock()
    tx_cm.__aenter__ = AsyncMock()
    tx_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_cm)

    return pool, conn


@pytest.fixture
def mock_http():
    return AsyncMock()


async def _process_one_board(board, pool, http):
    """Backward-compat wrapper: calls streaming path with a DeadlineExtender."""
    return await _process_one_board_streaming(board, pool, http, DeadlineExtender())


# ── TestTruncationHelpers ──────────────────────────────────────────────────


class TestTruncationHelpers:
    """The :mod:`src.shared.truncation` helpers wrap discovery as partial."""

    def test_truncated_rich_result_preserves_jobs(self):
        """Rich monitors keep the full DiscoveredJob list — no slicing."""
        jobs = [
            _discovered_job(url="https://example.com/job/1"),
            _discovered_job(url="https://example.com/job/2", title="Designer"),
        ]
        result = truncated_rich_result(jobs)

        assert result.truncated is True
        assert result.urls == {"https://example.com/job/1", "https://example.com/job/2"}
        assert result.jobs_by_url is not None
        assert set(result.jobs_by_url) == result.urls
        # The full job data must be preserved — the cap is a safety stop,
        # not a quality signal.
        assert result.jobs_by_url["https://example.com/job/2"].title == "Designer"

    def test_truncated_url_result_preserves_urls(self):
        """URL-only monitors keep every URL — no truncation."""
        urls = {f"https://example.com/job/{i}" for i in range(10)}
        result = truncated_url_result(urls)

        assert result.truncated is True
        assert result.urls == urls
        assert result.jobs_by_url is None

    def test_truncated_rich_empty_list(self):
        """Edge case: zero jobs in a 'truncated' result. The flag still
        propagates so the pipeline knows to skip gone-detection — a
        pathological all-error monitor cycle is *not* a delist event."""
        result = truncated_rich_result([])

        assert result.truncated is True
        assert result.urls == set()
        assert result.jobs_by_url == {}


# ── TestTruncatedSuppressesGoneDetection ─────────────────────────────────


class TestTruncatedSuppressesGoneDetection:
    """When the monitor flags a cycle as truncated, the board processor:

    1. Skips ``_MARK_GONE_BY_TIMESTAMP`` entirely (no tombstoning of the
       unseen tail beyond the cap).
    2. Still records the cycle as a clean success (``_RECORD_SUCCESS_NONEMPTY``)
       so the failure budget doesn't escalate on a working-but-large board.
    3. Increments ``crawler_monitor_truncated_total{board_id=...}`` so ops
       can spot a board that has outgrown the cap.
    4. Still inserts the URLs that ARE in the (capped) batch.
    """

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_truncated_url_only_skips_mark_gone(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """URL-only truncated batch: _MARK_GONE_BY_TIMESTAMP must not run."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        # Mirrors what the dom/sitemap/workable/workday monitors return on
        # truncation via ``truncated_url_result``.
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(urls={url1}, jobs_by_url=None, truncated=True)
        )
        conn.fetch.side_effect = [
            # _DIFF_BATCH
            [_diff_row("new", url=url1)],
            # _INSERT_URL_ONLY_JOBS
            [_inserted_row("jp-1", url1)],
            # If _MARK_GONE_BY_TIMESTAMP runs, it would consume this entry.
            [],
        ]
        # dom is a non-rich monitor so the URL-only insert path is used.
        board = _mock_board(crawler_type="dom")

        await _process_one_board(board, pool, mock_http)

        # Gone-detection must NOT run for the cycle.
        fetch_sqls = [c.args[0] for c in conn.fetch.await_args_list]
        assert _MARK_GONE_BY_TIMESTAMP not in fetch_sqls

        # The URL that IS in the batch is still inserted.
        assert _DIFF_BATCH in fetch_sqls
        assert _INSERT_URL_ONLY_JOBS in fetch_sqls

        # The cycle is still recorded as success — failure budget unchanged.
        execute_sqls = [c.args[0] for c in conn.execute.await_args_list]
        assert _RECORD_SUCCESS_NONEMPTY in execute_sqls

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_truncated_rich_skips_mark_gone(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Rich (greenhouse/lever/ashby/...) truncated batch: same contract."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        job1 = _discovered_job(url=url1)
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(
                urls={url1},
                jobs_by_url={url1: job1},
                truncated=True,
            )
        )
        conn.fetch.return_value = [_diff_row("new", url=url1)]
        board = _mock_board()

        await _process_one_board(board, pool, mock_http)

        fetch_sqls = [c.args[0] for c in conn.fetch.await_args_list]
        assert _MARK_GONE_BY_TIMESTAMP not in fetch_sqls
        # Rich job still inserted.
        fetchrow_sqls = [c.args[0] for c in conn.fetchrow.await_args_list]
        assert _INSERT_RICH_JOB in fetchrow_sqls
        # Success still recorded.
        execute_sqls = [c.args[0] for c in conn.execute.await_args_list]
        assert _RECORD_SUCCESS_NONEMPTY in execute_sqls

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_any_truncated_batch_flips_whole_cycle(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """One truncated batch in a multi-batch stream is enough — even if
        earlier batches were clean, the cycle is partial because we know
        we didn't see the full set."""
        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        url2 = "https://example.com/job/2"
        mock_monitor.side_effect = _mock_stream(
            # First batch: clean
            MonitorResult(urls={url1}, jobs_by_url=None, truncated=False),
            # Second batch: truncation flag flips the cycle
            MonitorResult(urls={url2}, jobs_by_url=None, truncated=True),
        )
        conn.fetch.side_effect = [
            # batch 1: DIFF_BATCH
            [_diff_row("new", url=url1)],
            # batch 1: INSERT_URL_ONLY_JOBS
            [_inserted_row("jp-1", url1)],
            # batch 2: DIFF_BATCH
            [_diff_row("new", url=url2)],
            # batch 2: INSERT_URL_ONLY_JOBS
            [_inserted_row("jp-2", url2)],
            # No fetch entry for _MARK_GONE_BY_TIMESTAMP because the cycle
            # is flagged as truncated.
        ]
        board = _mock_board(crawler_type="dom")

        await _process_one_board(board, pool, mock_http)

        fetch_sqls = [c.args[0] for c in conn.fetch.await_args_list]
        assert _MARK_GONE_BY_TIMESTAMP not in fetch_sqls

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_truncated_increments_metric(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """``crawler_monitor_truncated_total{board_id=...}`` increments once
        per cycle on truncation; not at all on a clean cycle. Counter is
        scoped by ``board_id`` so a noisy board is attributable in Grafana
        without grepping logs."""
        from src.metrics import monitor_truncated_total

        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(urls={url1}, jobs_by_url=None, truncated=True)
        )
        conn.fetch.side_effect = [
            [_diff_row("new", url=url1)],
            [_inserted_row("jp-1", url1)],
        ]
        board = _mock_board(id="board-truncated-test", crawler_type="dom")
        before = _counter_value(monitor_truncated_total, board_id="board-truncated-test")

        await _process_one_board(board, pool, mock_http)

        after = _counter_value(monitor_truncated_total, board_id="board-truncated-test")
        assert after - before == 1

    @patch("src.batch.get_redis")
    @patch("src.batch.monitor_one_stream")
    async def test_clean_run_runs_mark_gone_and_no_counter(
        self, mock_monitor, mock_get_redis, mock_pool, mock_http
    ):
        """Control: a normal (untruncated) cycle still runs gone-detection
        and does NOT increment the truncation counter."""
        from src.metrics import monitor_truncated_total

        pool, conn = mock_pool
        url1 = "https://example.com/job/1"
        mock_monitor.side_effect = _mock_stream(
            MonitorResult(urls={url1}, jobs_by_url=None, truncated=False)
        )
        conn.fetch.side_effect = [
            [_diff_row("new", url=url1)],
            [_inserted_row("jp-1", url1)],
            [],  # _MARK_GONE_BY_TIMESTAMP rows
        ]
        board = _mock_board(id="board-clean-test", crawler_type="dom")
        before = _counter_value(monitor_truncated_total, board_id="board-clean-test")

        await _process_one_board(board, pool, mock_http)

        # Clean cycle ran gone-detection.
        fetch_sqls = [c.args[0] for c in conn.fetch.await_args_list]
        assert _MARK_GONE_BY_TIMESTAMP in fetch_sqls
        # Counter must not have moved.
        after = _counter_value(monitor_truncated_total, board_id="board-clean-test")
        assert after == before
