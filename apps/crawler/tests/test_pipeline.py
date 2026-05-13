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
