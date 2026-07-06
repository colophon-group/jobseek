"""Tests for pipeline and drain pure functions (no async loops)."""

from __future__ import annotations

import asyncio
import json
import time

import fakeredis.aioredis
import pytest
import structlog

import src.redis_queue as rq
from src.workers.pipeline import (
    _BoardRecord,
    _lease_heartbeat,
    _resolve_scraper,
    _scrape_item_from_redis,
)

# ---------------------------------------------------------------------------
# _BoardRecord
# ---------------------------------------------------------------------------


class TestBoardRecord:
    def test_basic_fields(self):
        config = {
            "company_id": "comp-1",
            "board_url": "https://example.com/jobs",
            "crawler_type": "greenhouse",
            "check_interval_minutes": "30",
            "metadata": json.dumps({"scraper_type": "jsonld"}),
        }
        rec = _BoardRecord("board-1", config)
        assert rec["id"] == "board-1"
        assert rec["company_id"] == "comp-1"
        assert rec["board_url"] == "https://example.com/jobs"
        assert rec["crawler_type"] == "greenhouse"
        assert rec["check_interval_minutes"] == 30
        assert rec["metadata"]["scraper_type"] == "jsonld"

    def test_missing_metadata(self):
        rec = _BoardRecord("board-2", {"board_url": "https://x.com"})
        assert rec["metadata"] == {}

    def test_invalid_metadata_json(self):
        rec = _BoardRecord("board-3", {"metadata": "not-json{"})
        assert rec["metadata"] == {}

    def test_get_with_default(self):
        rec = _BoardRecord("board-4", {})
        assert rec.get("nonexistent", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# _scrape_item_from_redis
# ---------------------------------------------------------------------------


class TestScrapeItemFromRedis:
    def test_basic_conversion(self):
        work = rq.ScrapeWork(
            posting_id="post-1",
            source_url="https://example.com/job/123",
            board_id="board-1",
            description_r2_hash=12345,
            scraper_needs_browser=False,
            scrape_interval_hours=24,
        )
        item, step = _scrape_item_from_redis(work)
        assert item.job_posting_id == "post-1"
        assert item.url == "https://example.com/job/123"
        assert item.board_id == "board-1"
        assert item.description_r2_hash == 12345
        assert step == 0

    def test_with_scrape_step(self):
        work = rq.ScrapeWork(
            posting_id="post-2",
            source_url="https://example.com/job/456",
            board_id="board-2",
            description_r2_hash=None,
            scraper_needs_browser=True,
            scrape_interval_hours=12,
            scrape_step=2,
        )
        item, step = _scrape_item_from_redis(work)
        assert item.job_posting_id == "post-2"
        assert step == 2

    def test_null_hash(self):
        work = rq.ScrapeWork(
            posting_id="post-3",
            source_url="https://example.com/job/789",
            board_id="board-3",
            description_r2_hash=None,
            scraper_needs_browser=False,
            scrape_interval_hours=24,
        )
        item, _ = _scrape_item_from_redis(work)
        assert item.description_r2_hash is None


# ---------------------------------------------------------------------------
# _resolve_scraper
# ---------------------------------------------------------------------------


class TestResolveScraper:
    def test_explicit_scraper_wins(self):
        scraper_type, scraper_config = _resolve_scraper(
            {"scraper_type": "dom"}, crawler_type="sitemap", scraper_config={"render": True}
        )
        assert scraper_type == "dom"
        assert scraper_config == {"render": True}

    def test_auto_scraper_resolution(self):
        scraper_type, scraper_config = _resolve_scraper(
            {}, crawler_type="workday", scraper_config=None
        )
        assert scraper_type == "workday"
        assert scraper_config is None

    def test_personio_without_scraper_type_defaults_to_dom(self):
        """Regression guard for issue #2186.

        personio has no auto_scraper_type mapping, so when metadata has no
        explicit scraper_type we must NOT fall back to using ``crawler_type``
        as the scraper name (which crashes).  ``dom`` is the safe default —
        the validator rejects this configuration at CI anyway.
        """
        scraper_type, scraper_config = _resolve_scraper(
            {}, crawler_type="personio", scraper_config=None
        )
        assert scraper_type == "dom"
        assert scraper_config is None

    def test_rich_monitor_does_not_resolve_to_skip(self):
        """Rich monitors auto-resolve to ``skip``, but _is_skip_no_scrape is
        the real guard — this helper must never return ``skip`` itself, or
        the ``skip`` scraper would be invoked and raise."""
        scraper_type, _ = _resolve_scraper({}, crawler_type="greenhouse", scraper_config=None)
        assert scraper_type != "skip"
        assert scraper_type == "dom"

    def test_no_crawler_type_defaults_to_dom(self):
        scraper_type, scraper_config = _resolve_scraper({}, crawler_type=None, scraper_config=None)
        assert scraper_type == "dom"
        assert scraper_config is None

    def test_auto_config_applied_when_no_explicit_scraper_config(self):
        """breezy's auto config should flow through when no override is set."""
        scraper_type, scraper_config = _resolve_scraper(
            {}, crawler_type="breezy", scraper_config=None
        )
        assert scraper_type == "json-ld"
        assert isinstance(scraper_config, dict)
        assert "fallback" in scraper_config

    def test_explicit_scraper_config_overrides_auto(self):
        override = {"steps": [{"tag": "h1", "field": "title"}]}
        scraper_type, scraper_config = _resolve_scraper(
            {}, crawler_type="breezy", scraper_config=override
        )
        assert scraper_type == "json-ld"
        assert scraper_config is override


# ---------------------------------------------------------------------------
# _lease_heartbeat context manager (#3159 / #3173)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis(monkeypatch):
    """Replace get_redis with a fakeredis instance and reset script SHAs."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rq, "get_redis", lambda: fake)
    monkeypatch.setattr(rq, "_CLAIM_SHA", None)
    monkeypatch.setattr(rq, "_ENQUEUE_SHA", None)
    monkeypatch.setattr(rq, "_RESCHEDULE_SHA", None)
    monkeypatch.setattr(rq, "_COMPLETE_SHA", None)
    monkeypatch.setattr(rq, "_HEARTBEAT_SHA", None)
    monkeypatch.setattr(rq, "_REAP_SHA", None)
    return fake


@pytest.mark.asyncio
async def test_lease_heartbeat_clears_inflight_on_normal_exit(mock_redis):
    """Even if a processing function returns through an early-drop path
    without calling ``reschedule_task``, the heartbeat context manager
    must clear the inflight lease so the reaper doesn't re-enqueue.
    """
    r = mock_redis
    # Seed an inflight lease entry as if claim_work had just run.
    domain = "greenhouse"
    task_id = "board-drop"
    member = f"monitor|{domain}|{task_id}"
    await r.zadd("inflight:simple", {member: time.time() + 600})
    assert await r.zcard("inflight:simple") == 1

    log = structlog.get_logger()

    async with _lease_heartbeat("monitor", domain, task_id, browser=False, worker_log=log):
        # Simulate the processing function returning early without
        # calling reschedule_task (e.g. board status = disabled, rich
        # monitor drop, missing config). We don't call complete_task
        # explicitly — that's the safety-net behaviour we want to
        # verify.
        pass

    # Inflight entry must be cleared by the context manager.
    assert await r.zcard("inflight:simple") == 0


@pytest.mark.asyncio
async def test_lease_heartbeat_clears_inflight_on_exception(mock_redis):
    """If the body of the heartbeat context manager raises, the lease
    must still be cleaned up on the way out — otherwise an exception
    in processing would leave an orphan lease that the reaper would
    re-enqueue (potentially double-processing the task).
    """
    r = mock_redis
    domain = "lever"
    task_id = "p-1"
    member = f"scrape|{domain}|{task_id}"
    await r.zadd("inflight:simple", {member: time.time() + 600})

    log = structlog.get_logger()

    async def _raise_inside_lease():
        async with _lease_heartbeat("scrape", domain, task_id, browser=False, worker_log=log):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await _raise_inside_lease()

    # Cleanup happened despite the exception.
    assert await r.zcard("inflight:simple") == 0


@pytest.mark.asyncio
async def test_lease_heartbeat_leaves_cancelled_lease_for_reaper(mock_redis):
    """Cancellation means the task did not finish.

    The heartbeat cleanup must leave the inflight lease intact so the
    reaper can recover the work after the lease expires.
    """
    r = mock_redis
    domain = "workday"
    task_id = "board-cancel"
    member = f"monitor|{domain}|{task_id}"
    await r.zadd("inflight:simple", {member: time.time() + 600})

    log = structlog.get_logger()
    entered = asyncio.Event()
    blocker = asyncio.Event()

    async def _work():
        async with _lease_heartbeat("monitor", domain, task_id, browser=False, worker_log=log):
            entered.set()
            await blocker.wait()

    task = asyncio.create_task(_work())
    await asyncio.wait_for(entered.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert await r.zcard("inflight:simple") == 1
    assert await r.zscore("inflight:simple", member) is not None


@pytest.mark.asyncio
async def test_lease_heartbeat_does_not_disturb_a_completed_lease(mock_redis):
    """If the body called ``reschedule_task`` (which clears the inflight
    entry), the safety-net cleanup at exit must be a harmless no-op.
    """
    r = mock_redis
    domain = "ashby"
    task_id = "board-ok"
    member = f"monitor|{domain}|{task_id}"
    await r.zadd("inflight:simple", {member: time.time() + 600})

    log = structlog.get_logger()

    async with _lease_heartbeat("monitor", domain, task_id, browser=False, worker_log=log):
        # Simulate a successful reschedule that clears the lease
        # mid-flight.
        await rq.reschedule_task(domain, task_id, "monitor", time.time() + 3600, browser=False)
        assert await r.zcard("inflight:simple") == 0

    # On exit, complete_task is called as the safety net — still 0
    # entries; no error.
    assert await r.zcard("inflight:simple") == 0


@pytest.mark.asyncio
async def test_lease_heartbeat_pulses_extend_lease(mock_redis, monkeypatch):
    """The background heartbeat must extend the lease score forward.

    Forces a short heartbeat interval to exercise the loop without
    sleeping the whole test.
    """
    from src.config import settings

    monkeypatch.setattr(settings, "inflight_heartbeat_interval_seconds", 1)

    r = mock_redis
    domain = "lever"
    task_id = "board-beat"
    member = f"monitor|{domain}|{task_id}"
    initial_until = time.time() + 5  # short lease so we can observe a bump
    await r.zadd("inflight:simple", {member: initial_until})

    log = structlog.get_logger()

    async with _lease_heartbeat("monitor", domain, task_id, browser=False, worker_log=log):
        # Sleep long enough for at least one beat to fire.
        await asyncio.sleep(1.3)
        new_score = await r.zscore("inflight:simple", member)
        # New score is "now + lease_ttl", which is well past the
        # initial 5-second deadline.
        assert new_score is not None
        assert new_score > initial_until

    # Exit cleanup still wipes the entry.
    assert await r.zcard("inflight:simple") == 0


# ---------------------------------------------------------------------------
# Lifecycle anchor: posting.scraped (#3192)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_scrape_work_emits_posting_scraped_on_success(mock_redis):
    """``_process_scrape_work`` must emit ``posting.scraped`` with the
    posting_id when ``_process_one_scrape`` returns success — the
    lifecycle anchor that closes #3192.

    The error path already emits ``pipeline.scrape.error`` with
    ``posting_id``; the success path is the missing half.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.workers.pipeline import _process_scrape_work

    posting_id = "12345678-aaaa-bbbb-cccc-1234567890ab"
    work = rq.ScrapeWork(
        posting_id=posting_id,
        source_url="https://example.com/job/abc",
        board_id="board-xyz",
        description_r2_hash=None,
        scraper_needs_browser=False,
        scrape_interval_hours=24,
    )

    # Mock local pool: SELECT (is_active=true, next_scrape_at not null)
    local_pool = AsyncMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "is_active": True,
            "next_scrape_at": time.time() + 60,
        }
    )
    acq_ctx = MagicMock()
    acq_ctx.__aenter__ = AsyncMock(return_value=conn)
    acq_ctx.__aexit__ = AsyncMock(return_value=False)
    local_pool.acquire = MagicMock(return_value=acq_ctx)

    # Seed minimal board config in Redis so the worker proceeds to the
    # scrape branch instead of taking the fail-safe stale-config path.
    r = mock_redis
    await r.hset(
        f"board:{work.board_id}",
        mapping={
            "crawler_type": "greenhouse",
            "metadata": json.dumps({"scraper_type": "json-ld"}),
        },
    )

    http = AsyncMock()
    worker_log = structlog.get_logger().bind(worker_id=1)

    with (
        patch(
            "src.processing.scrape._process_one_scrape",
            new=AsyncMock(return_value=(True, 0.5)),
        ),
        structlog.testing.capture_logs() as logs,
    ):
        await _process_scrape_work(worker_log, work, local_pool, http, browser=False)

    events = [
        e for e in logs if e.get("event") == "posting.scraped" and e.get("posting_id") == posting_id
    ]
    assert events, (
        "_process_scrape_work must emit posting.scraped on success with "
        "the posting_id — without it operators cannot correlate the URL "
        "with the scrape completion event (#3192)"
    )
    assert events[0]["board_id"] == "board-xyz"
    assert events[0]["source_url"] == "https://example.com/job/abc"


@pytest.mark.asyncio
async def test_process_scrape_work_binds_posting_id_contextvar(mock_redis):
    """The scrape coroutine must bind ``posting_id`` to a structlog
    contextvar so downstream code (e.g. third-party libraries using
    structlog without explicit binding) inherits it on every log line.

    The contextvar must be unbound on the way out so the next claim
    doesn't inherit a stale posting_id (#3192).
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.workers.pipeline import _process_scrape_work

    posting_id = "abcdef01-2222-3333-4444-555555555555"
    work = rq.ScrapeWork(
        posting_id=posting_id,
        source_url="https://example.com/job/xyz",
        board_id="board-context",
        description_r2_hash=None,
        scraper_needs_browser=False,
        scrape_interval_hours=24,
    )

    # Tombstone the row so the worker takes the early-return path —
    # cheaper than mocking the full happy path; we only care about the
    # contextvar lifecycle.
    local_pool = AsyncMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"is_active": False, "next_scrape_at": None})
    acq_ctx = MagicMock()
    acq_ctx.__aenter__ = AsyncMock(return_value=conn)
    acq_ctx.__aexit__ = AsyncMock(return_value=False)
    local_pool.acquire = MagicMock(return_value=acq_ctx)

    http = AsyncMock()
    worker_log = structlog.get_logger().bind(worker_id=1)

    # Pre-condition: no posting_id contextvar bound.
    ctx_before = structlog.contextvars.get_contextvars()
    assert "posting_id" not in ctx_before

    captured_during_call: dict = {}

    async def fake_fetchrow(*_a, **_kw):
        captured_during_call.update(structlog.contextvars.get_contextvars())
        return {"is_active": False, "next_scrape_at": None}

    conn.fetchrow = AsyncMock(side_effect=fake_fetchrow)

    with patch(
        "src.processing.scrape._process_one_scrape",
        new=AsyncMock(return_value=(True, 0.5)),
    ):
        await _process_scrape_work(worker_log, work, local_pool, http, browser=False)

    # During the call the contextvar was bound.
    assert captured_during_call.get("posting_id") == posting_id

    # After return the contextvar is unbound — otherwise the next
    # claim on this worker would inherit it.
    ctx_after = structlog.contextvars.get_contextvars()
    assert "posting_id" not in ctx_after


# ---------------------------------------------------------------------------
# Monitor task rollup metric (#3200)
# ---------------------------------------------------------------------------
#
# The scrape path already emits ``tasks_total{kind="scrape", status=...}``
# in both the happy-path and exception handlers. The matching monitor
# sites were silent until #3200, so PromQL queries like
# ``sum by (status) (rate(crawler_tasks_total{kind="monitor"}[5m]))``
# undercounted — only skip statuses showed up, never success / failed /
# gone. Tests below pin the three sites so the asymmetry can't regress.


def _tasks_total_value(kind: str, status: str) -> float:
    """Read a single ``crawler_tasks_total`` sample by ``kind``+``status``.

    Returns ``0.0`` when the sample doesn't exist yet (Prometheus
    counters are lazily materialised on first ``inc()``).
    """
    from src.metrics import tasks_total

    for sample in list(tasks_total.collect())[0].samples:
        if (
            sample.name == "crawler_tasks_total"
            and sample.labels.get("kind") == kind
            and sample.labels.get("status") == status
        ):
            return sample.value
    return 0.0


def _make_monitor_local_pool():
    """Mock ``asyncpg.Pool`` whose SELECT returns a non-disabled board."""
    from unittest.mock import AsyncMock, MagicMock

    local_pool = AsyncMock()
    conn = AsyncMock()
    # ``board_status`` non-disabled so the self-heal early-return doesn't fire.
    conn.fetchval = AsyncMock(return_value="active")
    acq_ctx = MagicMock()
    acq_ctx.__aenter__ = AsyncMock(return_value=conn)
    acq_ctx.__aexit__ = AsyncMock(return_value=False)
    local_pool.acquire = MagicMock(return_value=acq_ctx)
    return local_pool


def _make_board_work():
    """Build a ``BoardWork`` whose config skips the browser-reroute branch.

    The pipeline checks ``monitor_needs_browser(crawler_type, metadata)``
    for slim workers; a recognised non-browser crawler type (``sitemap``)
    keeps the test in the happy path without needing to patch the
    registry.
    """
    return rq.BoardWork(
        board_id="board-3200",
        config={
            "crawler_type": "sitemap",
            "company_id": "comp-3200",
            "board_url": "https://example.com/jobs",
            "check_interval_minutes": "60",
            "metadata": json.dumps({}),
        },
        domain="example.com",
    )


@pytest.mark.asyncio
async def test_process_monitor_work_emits_tasks_total_succeeded():
    """Happy-path success must increment ``tasks_total{kind=monitor,
    status=succeeded}`` exactly once — mirrors the scrape path so the
    Grafana failure-rate panel has a non-zero denominator for monitor
    tasks (#3200).
    """
    from unittest.mock import AsyncMock, patch

    from src.workers.pipeline import _process_monitor_work

    work = _make_board_work()
    local_pool = _make_monitor_local_pool()
    http = AsyncMock()
    worker_log = structlog.get_logger().bind(worker_id=1)

    before = _tasks_total_value("monitor", "succeeded")
    failed_before = _tasks_total_value("monitor", "failed")
    gone_before = _tasks_total_value("monitor", "gone")

    with (
        patch(
            "src.processing.board._process_one_board_streaming",
            new=AsyncMock(return_value=(True, 1.23)),
        ),
        patch(
            "src.workers.pipeline.reschedule_task",
            new=AsyncMock(return_value=None),
        ),
    ):
        await _process_monitor_work(worker_log, work, local_pool, http, browser=False)

    after = _tasks_total_value("monitor", "succeeded")
    assert after - before == pytest.approx(1.0), (
        "monitor happy-path must increment tasks_total{kind=monitor,"
        "status=succeeded} exactly once (#3200)"
    )
    # Sibling labels must not move.
    assert _tasks_total_value("monitor", "failed") == failed_before
    assert _tasks_total_value("monitor", "gone") == gone_before


@pytest.mark.asyncio
async def test_process_monitor_work_emits_tasks_total_failed_on_success_false():
    """``_process_one_board_streaming`` may return ``success=False`` for
    a recoverable failure (e.g. partial fetch). That outcome must roll
    up as ``status=failed`` — the same status the exception path uses
    — so PromQL aggregates see both as monitor failures (#3200).
    """
    from unittest.mock import AsyncMock, patch

    from src.workers.pipeline import _process_monitor_work

    work = _make_board_work()
    local_pool = _make_monitor_local_pool()
    http = AsyncMock()
    worker_log = structlog.get_logger().bind(worker_id=1)

    before = _tasks_total_value("monitor", "failed")
    succeeded_before = _tasks_total_value("monitor", "succeeded")

    with (
        patch(
            "src.processing.board._process_one_board_streaming",
            new=AsyncMock(return_value=(False, 0.5)),
        ),
        patch(
            "src.workers.pipeline.reschedule_task",
            new=AsyncMock(return_value=None),
        ),
    ):
        await _process_monitor_work(worker_log, work, local_pool, http, browser=False)

    after = _tasks_total_value("monitor", "failed")
    assert after - before == pytest.approx(1.0)
    # Success counter must not have moved.
    assert _tasks_total_value("monitor", "succeeded") == succeeded_before


@pytest.mark.asyncio
async def test_process_monitor_work_emits_tasks_total_failed_on_exception():
    """When ``_process_one_board_streaming`` raises, both the
    per-board attribution counter (``monitor_failed_per_board_total``)
    AND the low-cardinality rollup (``tasks_total{kind=monitor,
    status=failed}``) must increment. The per-board counter is the
    high-cardinality attribution signal; the rollup is what the
    ``TaskFailureRateHigh`` alert sums over (#3200).
    """
    from unittest.mock import AsyncMock, patch

    from src.metrics import monitor_failed_per_board_total
    from src.workers.pipeline import _process_monitor_work

    work = _make_board_work()
    local_pool = _make_monitor_local_pool()
    http = AsyncMock()
    worker_log = structlog.get_logger().bind(worker_id=1)

    tasks_before = _tasks_total_value("monitor", "failed")

    def _per_board_value(board_id: str) -> float:
        for sample in list(monitor_failed_per_board_total.collect())[0].samples:
            if (
                sample.name == "crawler_monitor_failed_per_board_total"
                and sample.labels.get("board_id") == board_id
            ):
                return sample.value
        return 0.0

    per_board_before = _per_board_value(work.board_id)

    class _BoomError(RuntimeError):
        pass

    with (
        patch(
            "src.processing.board._process_one_board_streaming",
            new=AsyncMock(side_effect=_BoomError("upstream HTTP 500")),
        ),
        patch(
            "src.workers.pipeline.reschedule_task",
            new=AsyncMock(return_value=None),
        ),
    ):
        # _process_monitor_work swallows the exception via the outer
        # ``except Exception`` — must not propagate.
        await _process_monitor_work(worker_log, work, local_pool, http, browser=False)

    assert _tasks_total_value("monitor", "failed") - tasks_before == pytest.approx(1.0)
    assert _per_board_value(work.board_id) - per_board_before == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_process_monitor_work_emits_tasks_total_gone_on_board_gone():
    """``BoardGoneError`` from ``_process_one_board_streaming`` is a
    publisher signal (the board's URL list is empty / 404), not a
    crawler defect. Before #3200 this path was silent on
    ``tasks_total``; it must now emit ``status="gone"`` so operators
    have a separate rollup rather than diluting the failure metric.
    """
    from unittest.mock import AsyncMock, patch

    from src.processing.board import BoardGoneError
    from src.workers.pipeline import _process_monitor_work

    work = _make_board_work()
    local_pool = _make_monitor_local_pool()
    http = AsyncMock()
    worker_log = structlog.get_logger().bind(worker_id=1)

    gone_before = _tasks_total_value("monitor", "gone")
    failed_before = _tasks_total_value("monitor", "failed")
    succeeded_before = _tasks_total_value("monitor", "succeeded")

    with (
        patch(
            "src.processing.board._process_one_board_streaming",
            new=AsyncMock(side_effect=BoardGoneError("board returned 404")),
        ),
        patch(
            "src.workers.pipeline.reschedule_task",
            new=AsyncMock(return_value=None),
        ),
    ):
        await _process_monitor_work(worker_log, work, local_pool, http, browser=False)

    assert _tasks_total_value("monitor", "gone") - gone_before == pytest.approx(1.0)
    # BoardGoneError must NOT be conflated with failed / succeeded.
    assert _tasks_total_value("monitor", "failed") == failed_before
    assert _tasks_total_value("monitor", "succeeded") == succeeded_before


# ---------------------------------------------------------------------------
# Bounded graceful drain on shutdown (#3205)
# ---------------------------------------------------------------------------
#
# SIGTERM/SIGINT sets ``shutdown_event`` and ``run_pipeline`` waits up to
# ``settings.shutdown_grace_seconds`` for in-flight tasks before cancelling
# them. Anything cancelled at the timeout boundary is recovered by the
# reaper from the inflight lease (#3259). Tests below pin the timeout
# behaviour, the happy-path drain, and the metric emission.


def _shutdown_metric_value(name: str, **labels) -> float:
    """Read a single Counter sample by name + label match."""
    from src.metrics import shutdown_cancelled_total, shutdown_drain_total

    counter = {
        "crawler_shutdown_drain_total": shutdown_drain_total,
        "crawler_shutdown_cancelled_total": shutdown_cancelled_total,
    }[name]
    for sample in list(counter.collect())[0].samples:
        if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
            return sample.value
    return 0.0


@pytest.mark.asyncio
async def test_run_pipeline_drains_in_flight_within_grace(monkeypatch):
    """Happy-path drain: a worker mid-task that finishes before the
    grace window expires must NOT be cancelled, and the ``drained``
    counter must increment.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.config import settings
    from src.workers.pipeline import run_pipeline

    # 1 worker, no reaper churn, generous grace.
    monkeypatch.setattr(settings, "discovery_concurrency", 1)
    monkeypatch.setattr(settings, "shutdown_grace_seconds", 5)
    monkeypatch.setattr(settings, "reaper_interval_seconds", 3600)

    cancelled_before = _shutdown_metric_value("crawler_shutdown_cancelled_total", wtype="simple")
    drained_before = _shutdown_metric_value(
        "crawler_shutdown_drain_total", wtype="simple", outcome="drained"
    )

    finished = asyncio.Event()

    async def fake_worker(*_a, **_kw):
        # Simulate "in-flight task finishes promptly after shutdown".
        # The real worker checks shutdown_event between iterations;
        # this stub does the same end-condition (returns on shutdown).
        shutdown_event = _a[3] if len(_a) > 3 else _kw["shutdown_event"]
        try:
            await shutdown_event.wait()
            # Pretend cleanup is quick (well under grace_s=5).
            await asyncio.sleep(0.05)
        finally:
            finished.set()

    shutdown_event = asyncio.Event()
    local_pool = MagicMock()
    http = AsyncMock()

    async def trigger_shutdown():
        # Let the workers spin up before signalling.
        await asyncio.sleep(0.05)
        shutdown_event.set()

    with (
        patch("src.workers.pipeline._discovery_worker", new=fake_worker),
        patch(
            "src.workers.pipeline._reaper_loop",
            new=AsyncMock(return_value=None),
        ),
    ):
        await asyncio.gather(
            run_pipeline(local_pool, http, shutdown_event, browser=False),
            trigger_shutdown(),
        )

    assert finished.is_set(), "worker should complete its in-flight task"
    cancelled_after = _shutdown_metric_value("crawler_shutdown_cancelled_total", wtype="simple")
    drained_after = _shutdown_metric_value(
        "crawler_shutdown_drain_total", wtype="simple", outcome="drained"
    )
    assert cancelled_after == cancelled_before, (
        "no tasks should be cancelled when the worker exits in time"
    )
    assert drained_after - drained_before == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_run_pipeline_cancels_after_grace_timeout(monkeypatch):
    """Drain timeout: a worker that ignores ``shutdown_event`` (e.g. a
    blocked ``_process_one_board_streaming``) must be cancelled when
    the grace window expires. The reaper recovers its work from the
    inflight lease (#3259) — that's outside this test's scope.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.config import settings
    from src.workers.pipeline import run_pipeline

    # Tight grace so the test finishes quickly.
    monkeypatch.setattr(settings, "discovery_concurrency", 2)
    monkeypatch.setattr(settings, "shutdown_grace_seconds", 1)
    monkeypatch.setattr(settings, "reaper_interval_seconds", 3600)

    cancelled_before = _shutdown_metric_value("crawler_shutdown_cancelled_total", wtype="simple")
    timeout_before = _shutdown_metric_value(
        "crawler_shutdown_drain_total", wtype="simple", outcome="timeout"
    )

    cancellations: list[bool] = []

    async def stuck_worker(*_a, **_kw):
        try:
            # Simulate an in-flight task that doesn't observe
            # shutdown_event — exactly the failure mode #3205 fixes.
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancellations.append(True)
            raise

    shutdown_event = asyncio.Event()
    local_pool = MagicMock()
    http = AsyncMock()

    async def trigger_shutdown():
        await asyncio.sleep(0.05)
        shutdown_event.set()

    start = time.monotonic()
    with (
        patch("src.workers.pipeline._discovery_worker", new=stuck_worker),
        patch(
            "src.workers.pipeline._reaper_loop",
            new=AsyncMock(return_value=None),
        ),
    ):
        await asyncio.gather(
            run_pipeline(local_pool, http, shutdown_event, browser=False),
            trigger_shutdown(),
        )
    elapsed = time.monotonic() - start

    # The pipeline returned in roughly grace_s seconds — not 60s.
    assert elapsed < 5, f"pipeline should return within grace window; took {elapsed:.2f}s"
    # Both workers were cancelled.
    assert len(cancellations) == 2

    cancelled_after = _shutdown_metric_value("crawler_shutdown_cancelled_total", wtype="simple")
    timeout_after = _shutdown_metric_value(
        "crawler_shutdown_drain_total", wtype="simple", outcome="timeout"
    )
    assert cancelled_after - cancelled_before == pytest.approx(2.0)
    assert timeout_after - timeout_before == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_run_pipeline_browser_drain_outcome_label(monkeypatch):
    """The ``wtype`` label on the drain metric must reflect the
    pipeline's worker type so simple/browser shutdowns are
    distinguishable in dashboards.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.config import settings
    from src.workers.pipeline import run_pipeline

    monkeypatch.setattr(settings, "discovery_concurrency", 1)
    monkeypatch.setattr(settings, "shutdown_grace_seconds", 5)
    monkeypatch.setattr(settings, "reaper_interval_seconds", 3600)

    drained_before = _shutdown_metric_value(
        "crawler_shutdown_drain_total", wtype="browser", outcome="drained"
    )

    async def quick_worker(*_a, **_kw):
        shutdown_event = _a[3] if len(_a) > 3 else _kw["shutdown_event"]
        await shutdown_event.wait()

    shutdown_event = asyncio.Event()
    local_pool = MagicMock()
    http = AsyncMock()

    async def trigger_shutdown():
        await asyncio.sleep(0.05)
        shutdown_event.set()

    with (
        patch("src.workers.pipeline._discovery_worker", new=quick_worker),
        patch(
            "src.workers.pipeline._reaper_loop",
            new=AsyncMock(return_value=None),
        ),
    ):
        await asyncio.gather(
            run_pipeline(local_pool, http, shutdown_event, browser=True),
            trigger_shutdown(),
        )

    drained_after = _shutdown_metric_value(
        "crawler_shutdown_drain_total", wtype="browser", outcome="drained"
    )
    assert drained_after - drained_before == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_run_pipeline_zero_grace_cancels_immediately(monkeypatch):
    """``shutdown_grace_seconds=0`` is a valid escape hatch: skip the
    drain wait and cancel in-flight tasks immediately. The reaper
    still recovers them via the inflight lease.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.config import settings
    from src.workers.pipeline import run_pipeline

    monkeypatch.setattr(settings, "discovery_concurrency", 1)
    monkeypatch.setattr(settings, "shutdown_grace_seconds", 0)
    monkeypatch.setattr(settings, "reaper_interval_seconds", 3600)

    cancelled_before = _shutdown_metric_value("crawler_shutdown_cancelled_total", wtype="simple")

    cancellations: list[bool] = []

    async def stuck_worker(*_a, **_kw):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancellations.append(True)
            raise

    shutdown_event = asyncio.Event()
    local_pool = MagicMock()
    http = AsyncMock()

    async def trigger_shutdown():
        await asyncio.sleep(0.05)
        shutdown_event.set()

    start = time.monotonic()
    with (
        patch("src.workers.pipeline._discovery_worker", new=stuck_worker),
        patch(
            "src.workers.pipeline._reaper_loop",
            new=AsyncMock(return_value=None),
        ),
    ):
        await asyncio.gather(
            run_pipeline(local_pool, http, shutdown_event, browser=False),
            trigger_shutdown(),
        )
    elapsed = time.monotonic() - start

    # With grace=0 the cancel fires immediately — well under 1s.
    assert elapsed < 1.5, f"zero-grace path should return promptly; took {elapsed:.2f}s"
    assert len(cancellations) == 1
    cancelled_after = _shutdown_metric_value("crawler_shutdown_cancelled_total", wtype="simple")
    assert cancelled_after - cancelled_before == pytest.approx(1.0)
