"""Tests for pipeline and drain pure functions (no async loops)."""

from __future__ import annotations

import asyncio
import json
import time

import fakeredis.aioredis
import pytest
import structlog

import src.redis_queue as rq
from src.redis_queue import ScrapeWork
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
        work = ScrapeWork(
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
        work = ScrapeWork(
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
        work = ScrapeWork(
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

    with pytest.raises(RuntimeError, match="boom"):
        async with _lease_heartbeat("scrape", domain, task_id, browser=False, worker_log=log):
            raise RuntimeError("boom")

    # Cleanup happened despite the exception.
    assert await r.zcard("inflight:simple") == 0


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
    work = ScrapeWork(
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
    work = ScrapeWork(
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
