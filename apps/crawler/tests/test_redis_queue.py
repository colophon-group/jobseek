from __future__ import annotations

import time

import fakeredis.aioredis
import pytest

import src.redis_queue as rq


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    """Replace get_redis with a fakeredis instance for all tests."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rq, "get_redis", lambda: fake)
    # Reset cached Lua script SHAs so they're re-loaded on each test
    monkeypatch.setattr(rq, "_CLAIM_SHA", None)
    monkeypatch.setattr(rq, "_ENQUEUE_SHA", None)
    monkeypatch.setattr(rq, "_RESCHEDULE_SHA", None)
    return fake


# ---------------------------------------------------------------------------
# Monitor queue: enqueue + claim round-trip
# ---------------------------------------------------------------------------


async def test_enqueue_monitor_and_claim_roundtrip():
    config = {"monitor": "greenhouse", "check_interval_minutes": "30"}
    added = await rq.enqueue_monitor(
        "greenhouse", "board-1", time.time() - 10, config, browser=False, first_time=True
    )
    assert added is True

    work = await rq.claim_work(browser=False)
    assert work is not None
    assert work.kind == "monitor"
    assert work.board_work is not None
    assert work.board_work.board_id == "board-1"
    assert work.board_work.config["monitor"] == "greenhouse"
    assert work.board_work.domain == "greenhouse"


async def test_enqueue_monitor_nx_prevents_duplicate():
    config = {"monitor": "lever"}
    t = time.time() - 10
    assert await rq.enqueue_monitor("lever", "board-dup", t, config, browser=False) is True
    # Second enqueue should return False (already exists)
    assert await rq.enqueue_monitor("lever", "board-dup", t - 100, config, browser=False) is False


async def test_enqueue_monitor_first_time_flag():
    config = {"monitor": "ashby"}
    await rq.enqueue_monitor(
        "ashby", "board-ft", time.time() - 10, config, browser=False, first_time=True
    )

    r = rq.get_redis()
    # First-time tasks should be in the first-time domain queue
    assert await r.zcard("ready:simple:0") >= 1  # tier 0 = first-time


# ---------------------------------------------------------------------------
# Monitor queue: empty + future items
# ---------------------------------------------------------------------------


async def test_claim_work_returns_none_on_empty():
    work = await rq.claim_work(browser=False)
    assert work is None

    work = await rq.claim_work(browser=True)
    assert work is None


async def test_claim_work_respects_due_time():
    """Items scheduled in the future should not be claimed."""
    config = {"monitor": "greenhouse"}
    future = time.time() + 3600  # 1 hour from now
    await rq.enqueue_monitor("greenhouse", "board-future", future, config, browser=False)

    work = await rq.claim_work(browser=False)
    assert work is None


# ---------------------------------------------------------------------------
# Monitor queue: reschedule
# ---------------------------------------------------------------------------


async def test_reschedule_monitor():
    config = {"monitor": "greenhouse"}
    await rq.enqueue_monitor(
        "greenhouse", "board-r", time.time() - 10, config, browser=False, first_time=True
    )

    work = await rq.claim_work(browser=False)
    assert work is not None

    # Reschedule for the future
    future = time.time() + 3600
    await rq.reschedule_task("greenhouse", "board-r", "monitor", future, browser=False)

    # Should not be claimable (future)
    work2 = await rq.claim_work(browser=False)
    assert work2 is None


# ---------------------------------------------------------------------------
# Monitor queue: browser mode
# ---------------------------------------------------------------------------


async def test_enqueue_monitor_browser():
    config = {"monitor": "dom"}
    await rq.enqueue_monitor(
        "example.com", "board-br", time.time() - 10, config, browser=True, first_time=True
    )

    # Should be claimable in browser mode
    work = await rq.claim_work(browser=True)
    assert work is not None
    assert work.kind == "monitor"
    assert work.board_work.board_id == "board-br"

    # Should NOT be claimable in non-browser mode
    await rq.enqueue_monitor(
        "example2.com", "board-br2", time.time() - 10, config, browser=True, first_time=True
    )
    work = await rq.claim_work(browser=False)
    assert work is None


# ---------------------------------------------------------------------------
# Monitor queue: remove_monitor
# ---------------------------------------------------------------------------


async def test_remove_monitor_drops_board_from_all_queues():
    """remove_monitor clears both first-time and recurring monitor queues
    (simple + browser) and deletes the board config hash."""
    config = {"monitor": "greenhouse"}
    await rq.enqueue_monitor(
        "greenhouse", "board-gone", time.time() - 10, config, browser=False, first_time=True
    )
    r = rq.get_redis()
    assert await r.zcard("ft_monitors_simple:greenhouse") == 1
    assert await r.exists("board:board-gone") == 1

    await rq.remove_monitor("greenhouse", "board-gone")

    assert await r.zcard("ft_monitors_simple:greenhouse") == 0
    assert await r.zcard("monitors_simple:greenhouse") == 0
    assert await r.zcard("ft_monitors_browser:greenhouse") == 0
    assert await r.zcard("monitors_browser:greenhouse") == 0
    assert await r.exists("board:board-gone") == 0


async def test_remove_monitor_is_idempotent_on_missing_board():
    """remove_monitor on a board that was never enqueued is a no-op."""
    r = rq.get_redis()
    await rq.remove_monitor("lever", "never-existed")
    assert await r.exists("board:never-existed") == 0


async def test_remove_monitor_after_claim_clears_domain_on_next_claim():
    """After remove_monitor, the next claim_work on the emptied domain returns
    None and the domain is dropped from the ready queue by the claim Lua."""
    config = {"monitor": "lever"}
    await rq.enqueue_monitor(
        "lever", "board-x", time.time() - 10, config, browser=False, first_time=True
    )

    await rq.remove_monitor("lever", "board-x")
    r = rq.get_redis()
    # Domain still present in ready queue until the next claim attempt
    assert await r.zcard("ready:simple:0") == 1

    work = await rq.claim_work(browser=False)
    assert work is None
    # Claim script removed the empty domain from the ready queue
    assert await r.zcard("ready:simple:0") == 0


# ---------------------------------------------------------------------------
# Scrape queue: enqueue + claim round-trip
# ---------------------------------------------------------------------------


async def test_enqueue_scrape_and_claim_roundtrip():
    config = {
        "source_url": "https://example.com/jobs/123",
        "board_id": "board-1",
        "description_r2_hash": "12345",
        "scraper_needs_browser": "false",
        "scrape_interval_hours": "24",
    }
    added = await rq.enqueue_scrape(
        "example.com", "posting-1", time.time() - 10, config, browser=False, first_time=True
    )
    assert added is True

    work = await rq.claim_work(browser=False)
    assert work is not None
    assert work.kind == "scrape"
    assert work.scrape_work is not None
    assert work.scrape_work.posting_id == "posting-1"
    assert work.scrape_work.source_url == "https://example.com/jobs/123"
    assert work.scrape_work.board_id == "board-1"
    assert work.scrape_work.description_r2_hash == 12345
    assert work.scrape_work.scrape_interval_hours == 24
    assert work.scrape_work.domain == "example.com"


async def test_enqueue_scrape_first_time():
    config = {"source_url": "https://example.com/jobs/1", "board_id": "b1"}
    await rq.enqueue_scrape(
        "example.com", "p-ft", time.time() - 10, config, browser=False, first_time=True
    )

    r = rq.get_redis()
    # First-time scrapes land in tier 0 ready queue
    assert await r.zcard("ready:simple:0") >= 1


async def test_enqueue_scrape_browser():
    config = {"source_url": "https://example.com/jobs/1", "board_id": "b1"}
    await rq.enqueue_scrape(
        "example.com", "p-br", time.time() - 10, config, browser=True, first_time=True
    )

    r = rq.get_redis()
    assert await r.zcard("ready:browser:0") >= 1


async def test_claim_scrape_returns_none_on_empty():
    work = await rq.claim_work(browser=False)
    assert work is None


async def test_claim_scrape_respects_due_time():
    config = {"source_url": "https://example.com/jobs/1", "board_id": "b1"}
    future = time.time() + 3600
    await rq.enqueue_scrape("example.com", "p-future", future, config, browser=False)

    work = await rq.claim_work(browser=False)
    assert work is None


async def test_reschedule_scrape():
    config = {
        "source_url": "https://example.com/jobs/1",
        "board_id": "b1",
        "scrape_interval_hours": "24",
    }
    await rq.enqueue_scrape(
        "example.com", "p-rs", time.time() - 10, config, browser=False, first_time=True
    )

    work = await rq.claim_work(browser=False)
    assert work is not None

    await rq.reschedule_task("example.com", "p-rs", "scrape", time.time() + 3600, browser=False)

    # Should not be claimable (future)
    work2 = await rq.claim_work(browser=False)
    assert work2 is None


async def test_claim_scrape_null_hash():
    """description_r2_hash should be None when empty/missing."""
    config = {
        "source_url": "https://example.com/jobs/1",
        "board_id": "b1",
        "scraper_needs_browser": "true",
        "scrape_interval_hours": "12",
    }
    await rq.enqueue_scrape(
        "example.com", "p-null", time.time() - 10, config, browser=False, first_time=True
    )

    work = await rq.claim_work(browser=False)
    assert work is not None
    assert work.scrape_work.description_r2_hash is None
    assert work.scrape_work.scrape_interval_hours == 12


# ---------------------------------------------------------------------------
# claim_work: multi-domain
# ---------------------------------------------------------------------------


async def test_claim_work_claims_monitor():
    """claim_work should return a monitor WorkItem when a monitor is available."""
    config = {"monitor": "greenhouse"}
    await rq.enqueue_monitor(
        "greenhouse", "board-cw", time.time() - 10, config, browser=False, first_time=True
    )

    work = await rq.claim_work(browser=False)
    assert work is not None
    assert work.kind == "monitor"
    assert work.board_work is not None
    assert work.board_work.board_id == "board-cw"


async def test_claim_work_claims_scrape():
    """claim_work should return a scrape WorkItem when a scrape is available."""
    config = {
        "source_url": "https://example.com/jobs/1",
        "board_id": "b1",
        "scrape_interval_hours": "24",
    }
    await rq.enqueue_scrape(
        "example.com", "posting-cw", time.time() - 10, config, browser=False, first_time=True
    )

    work = await rq.claim_work(browser=False)
    assert work is not None
    assert work.kind == "scrape"
    assert work.scrape_work is not None
    assert work.scrape_work.posting_id == "posting-cw"


async def test_claim_work_browser_mode():
    """Browser mode should only try browser queues."""
    config = {"monitor": "dom"}
    await rq.enqueue_monitor(
        "example.com", "board-br", time.time() - 10, config, browser=True, first_time=True
    )

    work = await rq.claim_work(browser=True)
    assert work is not None
    assert work.kind == "monitor"
    assert work.board_work.board_id == "board-br"


async def test_claim_work_browser_ignores_http():
    """Browser mode should NOT claim from non-browser queues."""
    config = {"monitor": "greenhouse"}
    await rq.enqueue_monitor(
        "greenhouse", "board-http-only", time.time() - 10, config, browser=False, first_time=True
    )

    work = await rq.claim_work(browser=True)
    assert work is None


# ---------------------------------------------------------------------------
# Metrics: get_queue_depths
# ---------------------------------------------------------------------------


async def test_get_queue_depths_empty():
    """All depths should be 0 when queues are empty."""
    depths = await rq.get_queue_depths()
    assert all(v == 0 for v in depths.values())
    assert "ready:simple:0:ready" in depths
    assert "ready:simple:0:total" in depths
    assert "ready:browser:0:ready" in depths


async def test_get_queue_depths_counts():
    """Queue depths should reflect enqueued items."""
    now = time.time() - 10
    await rq.enqueue_monitor("greenhouse", "b1", now, {"m": "gh"}, browser=False, first_time=True)
    await rq.enqueue_monitor("lever", "b2", now, {"m": "lv"}, browser=False, first_time=True)

    config = {"source_url": "u", "board_id": "b"}
    await rq.enqueue_scrape("example.com", "p1", now, config, browser=True, first_time=True)

    depths = await rq.get_queue_depths()
    # Two first-time monitors in simple tier 0 (ready now since score is in the past)
    assert depths["ready:simple:0:ready"] >= 2
    assert depths["ready:simple:0:total"] >= 2
    # One first-time scrape in browser tier 0
    assert depths["ready:browser:0:ready"] >= 1


# ---------------------------------------------------------------------------
# prune_stale_scrape_queues
# ---------------------------------------------------------------------------


async def test_prune_removes_old_scrape_entries(mock_redis):
    """Entries whose score is older than the cutoff should be removed
    from the zset, and their ``scrape:<id>`` hashes deleted alongside.
    Entries inside the cutoff stay."""
    r = mock_redis
    now = time.time()
    old = now - 20 * 86400  # 20 days old
    fresh = now - 1 * 86400  # 1 day old

    await r.zadd("scrapes_browser:old.example", {"task-old-1": old, "task-old-2": old})
    await r.zadd("scrapes_browser:old.example", {"task-fresh-1": fresh})
    await r.hset("scrape:task-old-1", "source_url", "https://old.example/1")
    await r.hset("scrape:task-old-2", "source_url", "https://old.example/2")
    await r.hset("scrape:task-fresh-1", "source_url", "https://old.example/3")

    result = await rq.prune_stale_scrape_queues(older_than_days=7, dry_run=False)

    assert result["zset_entries"] == 2
    assert result["hashes"] == 2
    # Fresh entry and its hash survived.
    assert await r.zscore("scrapes_browser:old.example", "task-fresh-1") is not None
    assert await r.hget("scrape:task-fresh-1", "source_url") == "https://old.example/3"
    # Old entries gone.
    assert await r.zscore("scrapes_browser:old.example", "task-old-1") is None
    assert await r.exists("scrape:task-old-1") == 0


async def test_prune_dry_run_does_not_write(mock_redis):
    """``dry_run=True`` reports the counts but makes no writes."""
    r = mock_redis
    now = time.time()
    old = now - 30 * 86400

    await r.zadd("scrapes_simple:stale.example", {"t1": old, "t2": old, "t3": old})
    await r.hset("scrape:t1", "source_url", "u1")
    await r.hset("scrape:t2", "source_url", "u2")
    # t3 is a zset-only ghost with no scrape:<id> hash.

    result = await rq.prune_stale_scrape_queues(older_than_days=7, dry_run=True)

    assert result["zset_entries"] == 3
    # Only t1 and t2 have existing hashes.
    assert result["hashes"] == 2
    # Dry run: nothing actually removed.
    assert await r.zcard("scrapes_simple:stale.example") == 3
    assert await r.exists("scrape:t1") == 1


async def test_prune_covers_all_four_patterns(mock_redis):
    """Ordinary and first-time scrape queues for both worker types."""
    r = mock_redis
    old = time.time() - 30 * 86400
    for key in (
        "scrapes_simple:a.com",
        "scrapes_browser:b.com",
        "ft_scrapes_simple:c.com",
        "ft_scrapes_browser:d.com",
    ):
        await r.zadd(key, {"t": old})
        await r.hset("scrape:t", "source_url", "u")  # intentionally shared id for brevity

    result = await rq.prune_stale_scrape_queues(older_than_days=1, dry_run=False)

    # 4 zset entries removed (one per pattern). Only one scrape hash exists
    # because all four zsets share the id ``t``.
    assert result["zset_entries"] == 4
    assert result["keys_scanned"] == 4
    # At least one hash delete fired (the others are idempotent no-ops
    # because the key was already removed).
    assert result["hashes"] >= 1
