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


# ---------------------------------------------------------------------------
# Issue #3016: claim_work scheduler priority-inversion regression test
# ---------------------------------------------------------------------------


async def test_claim_work_drains_scrapes_when_monitor_is_far_future(mock_redis):
    """Regression for #3016 — Tesla scenario.

    A domain with one recurring monitor scheduled far in the future and a
    large pile of due-now scrapes must drain the scrape backlog. Before
    the fix, claim_work re-added the domain to ``ready:browser:1`` at the
    monitor's far-future score after every scrape claim, parking the
    domain and starving the rest of the scrape queue.
    """
    r = mock_redis
    domain = "test.example.com"
    now = time.time()

    # Force rate-delay to 0 so we can drive the claim loop synchronously
    # without sleeping. This isolates the test to the tier-selection logic.
    await r.set(f"delay:{domain}", "0")

    # 1 recurring monitor, scheduled 1000s in the future.
    monitor_due = now + 1000
    await r.zadd(f"monitors_browser:{domain}", {"mon-1": monitor_due})
    # Board config so the claim path that hits a monitor doesn't crash.
    await r.hset("board:mon-1", mapping={"monitor": "dom", "domain": domain})

    # 100 scrapes, all due now (10s in the past, varying by 0.1s so ZSET ordering
    # is deterministic but they are all <= now).
    scrape_count = 100
    scrapes = {}
    for i in range(scrape_count):
        sid = f"scr-{i:03d}"
        scrapes[sid] = now - 10 - (i * 0.1)
        await r.hset(
            f"scrape:{sid}",
            mapping={
                "source_url": f"https://{domain}/jobs/{i}",
                "board_id": "b1",
                "scrape_interval_hours": "24",
            },
        )
    await r.zadd(f"scrapes_browser:{domain}", scrapes)

    # Place the domain in ready:browser:2 at score=now so claim_work sees it.
    await r.zadd("ready:browser:2", {domain: now - 1})
    # Also remove from any stale tier just in case.
    await r.zrem("ready:browser:0", domain)
    await r.zrem("ready:browser:1", domain)

    # Run claim_work many times. Each successful claim consumes one task
    # and the domain is re-parked. Without the fix, the domain bounces to
    # tier 1 at monitor_due (future) after the first claim and stays there.
    claimed_kinds: list[str] = []
    for _ in range(200):
        # Bypass shared rate limit between claims so we can drive the loop.
        await r.delete(f"ratelimit:{domain}")
        work = await rq.claim_work(browser=True)
        if work is None:
            break
        claimed_kinds.append(work.kind)

    scrape_claims = sum(1 for k in claimed_kinds if k == "scrape")
    monitor_claims = sum(1 for k in claimed_kinds if k == "monitor")

    # The whole scrape backlog must drain (the monitor is still in the future).
    assert scrape_claims >= scrape_count, (
        f"expected at least {scrape_count} scrape claims, got {scrape_claims} "
        f"(monitor_claims={monitor_claims}, total={len(claimed_kinds)})"
    )
    # Monitor is in the future — should not be claimed.
    assert monitor_claims == 0
    # All scrape tasks consumed from the per-domain queue.
    assert await r.zcard(f"scrapes_browser:{domain}") == 0
    # Monitor still pending.
    assert await r.zcard(f"monitors_browser:{domain}") == 1


async def test_claim_work_monitors_still_fire_when_due(mock_redis):
    """Recurring monitors must still fire on schedule after the fix.

    A domain with one due-now monitor and a far-future scrape claims the
    monitor first (ties go to monitor by tier semantics), then the domain
    rebounds to tier 2 at the future scrape score until the scrape is due.
    """
    r = mock_redis
    domain = "test2.example.com"
    now = time.time()

    # 1 recurring monitor due 5s ago.
    await r.zadd(f"monitors_browser:{domain}", {"mon-1": now - 5})
    await r.hset("board:mon-1", mapping={"monitor": "dom", "domain": domain})

    # 1 recurring scrape due in 1h.
    await r.zadd(f"scrapes_browser:{domain}", {"scr-1": now + 3600})
    await r.hset(
        "scrape:scr-1",
        mapping={"source_url": f"https://{domain}/j", "board_id": "b1"},
    )

    # Initial ready entry — tier 1 at monitor due time.
    await r.zadd("ready:browser:1", {domain: now - 5})

    work = await rq.claim_work(browser=True)
    assert work is not None
    assert work.kind == "monitor"

    # After the monitor claim with no remaining due-now monitors, the domain
    # should be re-parked at the next pending task — the future scrape.
    score_t2 = await r.zscore("ready:browser:2", domain)
    assert score_t2 is not None, "domain should re-park in tier 2 at scrape's future score"
    assert score_t2 > now  # future


async def test_enqueue_scrape_does_not_park_in_monitor_tier(mock_redis):
    """Enqueueing a due-now scrape on a domain with a far-future monitor
    must place the domain in tier 2 (scrapes), not tier 1 (monitors).

    Symmetric guard for the priority-inversion bug in enqueue_task.lua.
    """
    r = mock_redis
    domain = "test3.example.com"
    now = time.time()

    # Pre-existing far-future monitor.
    await r.zadd(f"monitors_browser:{domain}", {"mon-1": now + 1000})

    # Enqueue a scrape due 10s ago.
    config = {"source_url": f"https://{domain}/j", "board_id": "b1"}
    added = await rq.enqueue_scrape(domain, "p1", now - 10, config, browser=True)
    assert added is True

    # Domain should be in ready:browser:2 (scrapes), NOT tier 1.
    score_t2 = await r.zscore("ready:browser:2", domain)
    score_t1 = await r.zscore("ready:browser:1", domain)
    assert score_t2 is not None, "domain should land in tier 2 because scrape is due-now"
    assert score_t1 is None, "domain must not be in tier 1 — that's the priority-inversion bug"


# ---------------------------------------------------------------------------
# Issue #3019: first-time work keeps strict tier-0 priority
# ---------------------------------------------------------------------------


async def test_enqueue_first_time_scrape_overrides_overdue_recurring_monitor(mock_redis):
    """A new first-time task must move the domain back to tier 0.

    This guards enqueue_task.lua: an overdue recurring monitor may be due
    earlier by timestamp, but first-time work is the top inter-domain priority.
    """
    r = mock_redis
    domain = "ft-priority-enqueue.example"
    now = time.time()

    await rq.enqueue_monitor(
        domain,
        "mon-overdue",
        now - 3600,
        {"monitor": "greenhouse"},
        browser=False,
    )
    assert await r.zscore("ready:simple:1", domain) is not None

    added = await rq.enqueue_scrape(
        domain,
        "ft-scrape",
        now,
        {"source_url": f"https://{domain}/jobs/1", "board_id": "board-1"},
        browser=False,
        first_time=True,
    )
    assert added is True

    assert await r.zscore("ready:simple:0", domain) is not None
    assert await r.zscore("ready:simple:1", domain) is None


async def test_claim_readds_remaining_first_time_work_to_tier_zero(mock_redis):
    """claim_work.lua must keep the domain in tier 0 while ft work remains."""
    r = mock_redis
    domain = "ft-priority-claim.example"
    now = time.time()

    await r.set(f"delay:{domain}", "0")
    await r.zadd(f"ft_monitors_simple:{domain}", {"ft-monitor": now})
    await r.hset("board:ft-monitor", mapping={"monitor": "greenhouse", "domain": domain})
    await r.zadd(f"ft_scrapes_simple:{domain}", {"ft-scrape": now})
    await r.hset(
        "scrape:ft-scrape",
        mapping={"source_url": f"https://{domain}/jobs/1", "board_id": "board-1"},
    )
    await r.zadd(f"monitors_simple:{domain}", {"mon-overdue": now - 3600})
    await r.hset("board:mon-overdue", mapping={"monitor": "greenhouse", "domain": domain})
    await r.zadd("ready:simple:0", {domain: now - 1})

    work = await rq.claim_work(browser=False)

    assert work is not None
    assert work.kind == "monitor"
    assert work.board_work is not None
    assert work.board_work.board_id == "ft-monitor"
    assert await r.zcard(f"ft_scrapes_simple:{domain}") == 1
    assert await r.zscore("ready:simple:0", domain) is not None
    assert await r.zscore("ready:simple:1", domain) is None


async def test_reschedule_keeps_first_time_scrape_ahead_of_overdue_monitor(mock_redis):
    """reschedule_task.lua must not demote domains that still have ft work."""
    r = mock_redis
    domain = "ft-priority-reschedule.example"
    now = time.time()

    await r.zadd(f"ft_scrapes_simple:{domain}", {"ft-scrape": now})
    await r.hset(
        "scrape:ft-scrape",
        mapping={"source_url": f"https://{domain}/jobs/1", "board_id": "board-1"},
    )

    await rq.reschedule_task(
        domain,
        "mon-overdue",
        "monitor",
        now - 3600,
        browser=False,
    )

    assert await r.zscore("ready:simple:0", domain) is not None
    assert await r.zscore("ready:simple:1", domain) is None


# ---------------------------------------------------------------------------
# Issues #3159 + #3173: lease / heartbeat / reaper regression tests
# ---------------------------------------------------------------------------


async def test_claim_writes_inflight_lease(mock_redis):
    """claim_work must atomically add an entry to ``inflight:<wtype>``."""
    r = mock_redis
    domain = "greenhouse"
    config = {"monitor": "greenhouse"}
    await rq.enqueue_monitor(
        domain, "board-lease-1", time.time() - 10, config, browser=False, first_time=True
    )

    # No lease before claim.
    assert await r.zcard("inflight:simple") == 0

    work = await rq.claim_work(browser=False)
    assert work is not None
    assert work.kind == "monitor"

    # Inflight ZSET now contains the leased member.
    assert await r.zcard("inflight:simple") == 1
    members = await r.zrange("inflight:simple", 0, -1)
    assert members == [f"monitor|{domain}|board-lease-1"]
    # Score is roughly now + lease_ttl (default 600).
    score = await r.zscore("inflight:simple", members[0])
    assert score is not None
    assert time.time() < score < time.time() + 700  # lease_ttl + tolerance


async def test_reschedule_clears_inflight_lease(mock_redis):
    """reschedule_task must remove the inflight lease entry."""
    r = mock_redis
    domain = "greenhouse"
    config = {"monitor": "greenhouse"}
    await rq.enqueue_monitor(
        domain, "board-lease-2", time.time() - 10, config, browser=False, first_time=True
    )

    await rq.claim_work(browser=False)
    assert await r.zcard("inflight:simple") == 1

    await rq.reschedule_task(domain, "board-lease-2", "monitor", time.time() + 3600, browser=False)

    # Lease cleared.
    assert await r.zcard("inflight:simple") == 0


async def test_complete_task_clears_inflight_lease(mock_redis):
    """complete_task is the idempotent drop-path cleanup primitive."""
    r = mock_redis
    domain = "lever"
    config = {"monitor": "lever"}
    await rq.enqueue_monitor(
        domain, "board-lease-3", time.time() - 10, config, browser=False, first_time=True
    )
    await rq.claim_work(browser=False)
    assert await r.zcard("inflight:simple") == 1

    removed = await rq.complete_task(domain, "board-lease-3", "monitor", browser=False)
    assert removed == 1
    assert await r.zcard("inflight:simple") == 0

    # Second call is a no-op (idempotent).
    removed = await rq.complete_task(domain, "board-lease-3", "monitor", browser=False)
    assert removed == 0


async def test_heartbeat_extends_lease(mock_redis):
    """heartbeat_task pushes leased_until forward."""
    r = mock_redis
    domain = "ashby"
    config = {"monitor": "ashby"}
    await rq.enqueue_monitor(
        domain, "board-hb", time.time() - 10, config, browser=False, first_time=True
    )
    await rq.claim_work(browser=False)
    members = await r.zrange("inflight:simple", 0, -1)
    assert len(members) == 1
    initial_score = await r.zscore("inflight:simple", members[0])
    assert initial_score is not None

    # Force the heartbeat extension large enough that fakeredis time
    # advancement isn't needed to see a delta.
    extended = await rq.heartbeat_task(
        domain, "board-hb", "monitor", browser=False, extension_seconds=99999
    )
    assert extended == 1
    new_score = await r.zscore("inflight:simple", members[0])
    assert new_score is not None
    assert new_score > initial_score


async def test_heartbeat_after_lease_lost_returns_zero(mock_redis):
    """heartbeat_task returns 0 when the inflight entry no longer exists.

    Uses ZADD XX semantics — a stale heartbeat must NOT recreate a
    swept lease entry.
    """
    domain = "lever"
    # No claim made — inflight entry does not exist.
    extended = await rq.heartbeat_task(domain, "fake-id", "scrape", browser=False)
    assert extended == 0


async def test_reaper_reenqueues_expired_lease(mock_redis):
    """Regression test for #3159 / #3173 — worker death between claim
    and reschedule must NOT permanently lose the task.

    Simulates: claim → worker SIGKILL (no reschedule) → reaper runs.
    Asserts: task is back on the per-domain ZSET and inflight entry is
    cleared.
    """
    r = mock_redis
    domain = "lever"
    config = {"monitor": "lever"}
    await rq.enqueue_monitor(
        domain, "board-die", time.time() - 10, config, browser=False, first_time=True
    )

    # Worker claims work…
    work = await rq.claim_work(browser=False)
    assert work is not None
    assert work.board_work.board_id == "board-die"

    # …then SIGKILL. No reschedule_task call. The per-domain queue is
    # empty (the claim popped it) and the inflight ZSET holds the lease.
    assert await r.zcard("ft_monitors_simple:lever") == 0
    assert await r.zcard("monitors_simple:lever") == 0
    assert await r.zcard("inflight:simple") == 1

    # Forge the lease's leased_until into the past so the reaper picks
    # it up without us having to wait for the real TTL.
    member = (await r.zrange("inflight:simple", 0, -1))[0]
    await r.zadd("inflight:simple", {member: time.time() - 60})

    result = await rq.reap_expired(browser=False)
    assert result["reenqueued"] == 1
    assert result["dead_lettered"] == 0
    assert result["missing_config"] == 0

    # Task is back on the per-domain queue (recurring monitor variant).
    assert await r.zcard("monitors_simple:lever") == 1
    members = await r.zrange("monitors_simple:lever", 0, -1)
    assert members == ["board-die"]
    # Inflight entry was cleared.
    assert await r.zcard("inflight:simple") == 0
    # The strike counter persists across re-enqueues (only cleared on
    # successful complete_task / reschedule_task or dead-letter move) —
    # this is how the reaper detects a genuinely poison task.
    assert await r.hget("inflight_strikes:simple", member) == "1"

    # After a successful complete_task, the strike counter is cleared.
    await rq.complete_task("lever", "board-die", "monitor", browser=False)
    assert await r.hexists("inflight_strikes:simple", member) == 0


async def test_reaper_reenqueues_expired_scrape_lease(mock_redis):
    """Same as monitor lease test, for scrape tasks."""
    r = mock_redis
    domain = "example.com"
    config = {
        "source_url": "https://example.com/jobs/die",
        "board_id": "b1",
        "scrape_interval_hours": "24",
    }
    await rq.enqueue_scrape(
        domain, "posting-die", time.time() - 10, config, browser=False, first_time=True
    )

    work = await rq.claim_work(browser=False)
    assert work is not None
    assert work.kind == "scrape"

    # SIGKILL — no reschedule.
    assert await r.zcard("ft_scrapes_simple:example.com") == 0
    assert await r.zcard("scrapes_simple:example.com") == 0
    assert await r.zcard("inflight:simple") == 1

    # Force lease into the past.
    member = (await r.zrange("inflight:simple", 0, -1))[0]
    await r.zadd("inflight:simple", {member: time.time() - 60})

    result = await rq.reap_expired(browser=False)
    assert result["reenqueued"] == 1

    # Scrape is back on the recurring per-domain ZSET, and the domain
    # is re-parked in the scrape tier so a worker will see it.
    assert await r.zcard("scrapes_simple:example.com") == 1
    assert await r.zscore("ready:simple:2", domain) is not None
    assert await r.zcard("inflight:simple") == 0


async def test_reaper_dead_letters_after_max_strikes(mock_redis):
    """A task that keeps timing out must eventually be parked in the
    dead-letter ZSET instead of being re-enqueued forever.

    Simulates the steady-state behaviour: each reap strikes the task
    once, and after ``reaper_max_strikes`` strikes the task is moved
    to ``deadletter:simple`` so the operator can investigate instead
    of letting the loop continue indefinitely.

    The simplest reproduction is to put the lease entry there directly
    and rerun the reaper — claim+reschedule are not the unit under
    test here.
    """
    from src.config import settings as _settings

    r = mock_redis
    domain = "poison"
    member = f"monitor|{domain}|board-poison"
    # Ensure the board hash exists so the missing_config path doesn't fire.
    await r.hset("board:board-poison", "monitor", "greenhouse")

    # Reap N times where N = max_strikes. Each iteration we forge the
    # lease into the past so the reaper picks it up.
    max_strikes = _settings.reaper_max_strikes
    last_result = None
    for _i in range(max_strikes):
        await r.zadd("inflight:simple", {member: time.time() - 60})
        last_result = await rq.reap_expired(browser=False)
        if last_result["dead_lettered"]:
            break

    assert last_result is not None
    assert last_result["dead_lettered"] == 1
    # Dead-letter ZSET has the poison task.
    assert await r.zcard("deadletter:simple") == 1
    # Inflight ZSET is empty (poison task moved out).
    assert await r.zcard("inflight:simple") == 0
    # Strikes cleaned up after dead-letter.
    assert await r.hexists("inflight_strikes:simple", member) == 0


async def test_reaper_drops_entry_when_config_missing(mock_redis):
    """A reap whose ``board:<id>`` / ``scrape:<id>`` hash is gone
    (sync deleted it while the worker was crashed) must be dropped,
    not re-enqueued.

    Otherwise we'd recreate phantom tasks that can never be claimed
    (no config to load) and that loop the reaper forever.
    """
    r = mock_redis
    domain = "greenhouse"
    # Place an inflight lease directly, with an EXPIRED score, and
    # no corresponding ``board:`` hash.
    await r.zadd("inflight:simple", {f"monitor|{domain}|orphan-1": time.time() - 60})

    result = await rq.reap_expired(browser=False)
    assert result["reenqueued"] == 0
    assert result["dead_lettered"] == 0
    assert result["missing_config"] == 1

    # The per-domain queue stays empty — no phantom task created.
    assert await r.zcard("monitors_simple:greenhouse") == 0
    assert await r.zcard("ft_monitors_simple:greenhouse") == 0
    # Inflight entry cleared.
    assert await r.zcard("inflight:simple") == 0


async def test_reaper_leaves_fresh_leases_alone(mock_redis):
    """A lease that hasn't expired yet must NOT be reaped — the worker
    is still legitimately processing the task."""
    r = mock_redis
    domain = "lever"
    config = {"monitor": "lever"}
    await rq.enqueue_monitor(
        domain, "board-alive", time.time() - 10, config, browser=False, first_time=True
    )
    await rq.claim_work(browser=False)
    assert await r.zcard("inflight:simple") == 1

    # Don't tamper with the lease score — it's set to now + 600 by
    # default. Reaper should find nothing to do.
    result = await rq.reap_expired(browser=False)
    assert result["reenqueued"] == 0
    assert result["dead_lettered"] == 0
    assert result["missing_config"] == 0
    # Lease entry still present.
    assert await r.zcard("inflight:simple") == 1


async def test_reaper_re_enqueue_uses_nx_against_concurrent_enqueue(mock_redis):
    """If the monitor's relisted CTE / sync re-enqueued the task while
    the worker was dead, the reaper's ZADD NX must not overwrite the
    fresher score.

    This guards against the reaper inadvertently rescheduling a
    higher-priority enqueue (e.g. monitor side just discovered the
    URL again at next_check_at=now) to retry_score=now.
    """
    r = mock_redis
    domain = "lever"
    config = {"monitor": "lever"}
    # Step 1: enqueue + claim (lease entry created).
    await rq.enqueue_monitor(
        domain, "board-concurrent", time.time() - 10, config, browser=False, first_time=True
    )
    await rq.claim_work(browser=False)

    # Step 2: simulate a parallel re-enqueue from a different code path
    # at a much earlier score (monitor relisted, say). Use ZADD NX
    # directly so we know the score the reaper would later see.
    earlier_score = time.time() - 1000
    await r.zadd("monitors_simple:lever", {"board-concurrent": earlier_score})

    # Step 3: Force the lease expired and run the reaper.
    member = (await r.zrange("inflight:simple", 0, -1))[0]
    await r.zadd("inflight:simple", {member: time.time() - 60})
    result = await rq.reap_expired(browser=False)
    assert result["reenqueued"] == 1

    # The earlier-score entry must have survived (ZADD NX on the
    # reaper's retry_score didn't overwrite it).
    final_score = await r.zscore("monitors_simple:lever", "board-concurrent")
    assert final_score == earlier_score, (
        "reaper must use ZADD NX so it doesn't overwrite an earlier "
        "score put there by a concurrent monitor relisted enqueue"
    )


async def test_complete_task_clears_inflight_strikes(mock_redis):
    """A task that completes successfully must reset its strike counter
    so future flapping doesn't accumulate across days."""
    r = mock_redis
    domain = "greenhouse"
    member = f"monitor|{domain}|board-strike"
    # Seed a strike count and an inflight entry.
    await r.hset("inflight_strikes:simple", member, "2")
    await r.zadd("inflight:simple", {member: time.time() + 600})

    await rq.complete_task(domain, "board-strike", "monitor", browser=False)
    assert await r.hexists("inflight_strikes:simple", member) == 0
    assert await r.zcard("inflight:simple") == 0


async def test_get_inflight_depth_metric_helper(mock_redis):
    """get_inflight_depth returns the ZCARD of inflight:<wtype>."""
    assert await rq.get_inflight_depth(browser=False) == 0
    assert await rq.get_inflight_depth(browser=True) == 0

    domain = "lever"
    config = {"monitor": "lever"}
    await rq.enqueue_monitor(
        domain, "board-depth", time.time() - 10, config, browser=False, first_time=True
    )
    await rq.claim_work(browser=False)

    assert await rq.get_inflight_depth(browser=False) == 1
    assert await rq.get_inflight_depth(browser=True) == 0


async def test_get_deadletter_depth_metric_helper(mock_redis):
    r = mock_redis
    assert await rq.get_deadletter_depth(browser=False) == 0
    await r.zadd("deadletter:simple", {"monitor|x|y": time.time()})
    assert await rq.get_deadletter_depth(browser=False) == 1
