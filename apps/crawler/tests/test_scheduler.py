from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.batch import WorkItem
from src.scheduler import WorkerPool, run_continuous_loop

# ── Helpers ──────────────────────────────────────────────────────────


def _work_item(domain="example.com", kind="monitor", result=(True, 1.0)):
    """Create a WorkItem with a preset async return value."""

    async def _run():
        return result

    return WorkItem(domain=domain, kind=kind, run=_run)


def _slow_work_item(domain="slow.com", kind="monitor", delay=0.05, result=(True, 1.0)):
    """Create a WorkItem that takes some time to complete."""

    async def _run():
        await asyncio.sleep(delay)
        return result

    return WorkItem(domain=domain, kind=kind, run=_run)


def _mock_pool():
    """Create a mock asyncpg pool with metrics-compatible stubs."""
    pool = MagicMock()
    pool.get_size.return_value = 10
    pool.get_idle_size.return_value = 10
    # Default fetch/fetchval return empty for R2 drain queries
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _failing_work_item(domain="fail.com", kind="monitor"):
    """Create a WorkItem that raises an exception."""

    async def _run():
        raise RuntimeError("boom")

    return WorkItem(domain=domain, kind=kind, run=_run)


# ── TestWorkerPool ───────────────────────────────────────────────────


class TestWorkerPool:
    async def test_submit_and_complete(self):
        """Submitted item runs and increments succeeded counter."""
        wp = WorkerPool(5, max_browser=0)
        wp.submit(_work_item())
        await wp.drain()
        assert wp.succeeded == 1
        assert wp.failed == 0
        assert wp.total_submitted == 1

    async def test_free_slots_tracking(self):
        """free_slots decreases during execution and restores after."""
        wp = WorkerPool(3, max_browser=0)
        assert wp.free_slots == 3

        item = _slow_work_item(delay=0.1)
        wp.submit(item)
        # After submit, semaphore acquired inside the task — give it a tick
        await asyncio.sleep(0.01)
        assert wp.free_slots == 2
        assert wp.active_count == 1

        await wp.drain()
        assert wp.free_slots == 3
        assert wp.active_count == 0

    async def test_domain_tracking(self):
        """In-flight domains tracked during execution."""
        wp = WorkerPool(5, max_browser=0)
        item = _slow_work_item(domain="slow.com", delay=0.1)
        wp.submit(item)
        await asyncio.sleep(0.01)
        assert "slow.com" in wp.inflight_domains

        await wp.drain()
        assert "slow.com" not in wp.inflight_domains

    async def test_max_concurrent_enforced(self):
        """Pool does not exceed max concurrent tasks."""
        wp = WorkerPool(2, max_browser=0)
        items = [_slow_work_item(domain=f"d{i}.com", delay=0.1) for i in range(4)]
        for item in items:
            wp.submit(item)

        await asyncio.sleep(0.01)
        # Only 2 should have acquired the semaphore
        assert wp.free_slots == 0
        assert wp.active_count == 2

        await wp.drain()
        assert wp.succeeded == 4
        assert wp.free_slots == 2

    async def test_exception_handling(self):
        """Failed items increment failed counter, don't crash the pool."""
        wp = WorkerPool(5, max_browser=0)
        wp.submit(_failing_work_item())
        await wp.drain()
        assert wp.failed == 1
        assert wp.succeeded == 0

    async def test_drain_empty(self):
        """Draining an empty pool completes immediately."""
        wp = WorkerPool(5, max_browser=0)
        await wp.drain()
        assert wp.total_submitted == 0

    async def test_mixed_success_failure(self):
        """Pool tracks both succeeded and failed items correctly."""
        wp = WorkerPool(5, max_browser=0)
        wp.submit(_work_item(domain="ok.com"))
        wp.submit(_work_item(domain="ok2.com", result=(False, 0.5)))
        wp.submit(_failing_work_item(domain="err.com"))
        await wp.drain()
        assert wp.succeeded == 1
        assert wp.failed == 2
        assert wp.total_submitted == 3

    async def test_same_domain_concurrent(self):
        """Items for the same domain run concurrently when slots are available."""
        wp = WorkerPool(5, max_browser=0)
        order = []

        async def make_run(label):
            async def _run():
                order.append(label)
                await asyncio.sleep(0.01)
                return (True, 0.01)

            return _run

        for i in range(3):
            run_fn = await make_run(f"item-{i}")
            wp.submit(WorkItem(domain="same.com", kind="scrape", run=run_fn))

        await asyncio.sleep(0.001)
        assert wp.active_count == 3  # all 3 running concurrently
        await wp.drain()
        assert wp.succeeded == 3

    async def test_semaphore_limits_concurrency(self):
        """Semaphore caps concurrent items even for the same domain."""
        wp = WorkerPool(2, max_browser=0)
        # Submit 3 items for same domain + 1 for a different domain
        wp.submit(_slow_work_item(domain="a.com", delay=0.05))
        wp.submit(_slow_work_item(domain="a.com", delay=0.05))
        wp.submit(_slow_work_item(domain="a.com", delay=0.05))
        wp.submit(_slow_work_item(domain="b.com", delay=0.05))

        await asyncio.sleep(0.01)
        # 2 slots total, all 4 submitted but only 2 can run
        assert wp.active_count == 2

        await wp.drain()
        assert wp.succeeded == 4

    async def test_drain_completes_all(self):
        """drain() waits for all concurrent items to finish."""
        wp = WorkerPool(5, max_browser=0)
        for _i in range(3):
            wp.submit(_slow_work_item(domain="q.com", delay=0.01))

        await wp.drain()
        assert wp.succeeded == 3

    async def test_failed_item_doesnt_affect_others(self):
        """A failing item doesn't prevent other same-domain items."""
        wp = WorkerPool(5, max_browser=0)
        wp.submit(_failing_work_item(domain="chain.com"))
        wp.submit(_work_item(domain="chain.com"))
        wp.submit(_work_item(domain="chain.com"))

        await wp.drain()
        assert wp.failed == 1
        assert wp.succeeded == 2

    async def test_submit_always_accepts(self):
        """submit() always accepts — all items get tasks immediately."""
        wp = WorkerPool(5, max_browser=0)
        wp.submit(_slow_work_item(domain="d.com", delay=0.1))
        wp.submit(_slow_work_item(domain="d.com", delay=0.1))
        wp.submit(_slow_work_item(domain="d.com", delay=0.1))
        wp.submit(_slow_work_item(domain="d.com", delay=0.1))
        assert wp.total_submitted == 4
        await asyncio.sleep(0.01)
        assert wp.active_count == 4  # all running concurrently (5 slots)
        await wp.drain()

    async def test_claim_budget(self):
        """claim_budget equals free concurrency slots."""
        wp = WorkerPool(5, max_browser=0)
        assert wp.claim_budget == 5  # all free

        # Start 2 items (same or different domain both consume slots)
        wp.submit(_slow_work_item(domain="a.com", delay=0.1))
        wp.submit(_slow_work_item(domain="b.com", delay=0.1))
        await asyncio.sleep(0.01)
        assert wp.claim_budget == 3  # 2 slots used

        # Same domain also consumes a slot
        wp.submit(_slow_work_item(domain="a.com", delay=0.1))
        await asyncio.sleep(0.01)
        assert wp.claim_budget == 2  # 3 slots used
        await wp.drain()

    async def test_timeout_kills_stuck_job(self):
        """A job exceeding _ITEM_TIMEOUT is cancelled and counted as failed."""
        wp = WorkerPool(5, max_browser=0)
        wp._ITEM_TIMEOUT = 0.05  # 50ms for test speed

        async def hang():
            await asyncio.sleep(999)
            return (True, 999.0)

        wp.submit(WorkItem(domain="stuck.com", kind="scrape", run=hang))
        await wp.drain()
        assert wp.failed == 1
        assert wp.timed_out == 1
        assert wp.succeeded == 0

    async def test_timeout_doesnt_break_queue_chain(self):
        """A timed-out item doesn't prevent subsequent queued items."""
        wp = WorkerPool(5, max_browser=0)
        wp._ITEM_TIMEOUT = 0.05

        async def hang():
            await asyncio.sleep(999)
            return (True, 999.0)

        wp.submit(WorkItem(domain="d.com", kind="scrape", run=hang))
        wp.submit(_work_item(domain="d.com"))

        await wp.drain()
        assert wp.timed_out == 1
        assert wp.succeeded == 1

    async def test_browser_semaphore_separate(self):
        """Browser items use separate semaphore from HTTP items."""
        wp = WorkerPool(2, max_browser=1)
        assert wp.free_slots == 3  # 2 http + 1 browser
        assert wp.http_free == 2
        assert wp.browser_free == 1

        # Submit 2 HTTP items + 1 browser item for different domains
        wp.submit(_slow_work_item(domain="a.com", delay=0.1))
        wp.submit(_slow_work_item(domain="b.com", delay=0.1))
        wp.submit(
            WorkItem(
                domain="c.com",
                kind="scrape",
                run=_slow_work_item(domain="c.com", delay=0.1).run,
                needs_browser=True,
            )
        )
        await asyncio.sleep(0.01)

        assert wp.http_active == 2
        assert wp.browser_active == 1
        assert wp.active_count == 3

        await wp.drain()
        assert wp.succeeded == 3

    async def test_browser_cap_enforced(self):
        """Browser concurrency is capped independently of HTTP."""
        wp = WorkerPool(5, max_browser=1)

        def _browser_item(domain):
            return WorkItem(
                domain=domain,
                kind="scrape",
                run=_slow_work_item(domain=domain, delay=0.1).run,
                needs_browser=True,
            )

        # Submit 3 browser items for different domains
        wp.submit(_browser_item("x.com"))
        wp.submit(_browser_item("y.com"))
        wp.submit(_browser_item("z.com"))
        await asyncio.sleep(0.01)

        # Only 1 browser slot — 1 active, 2 waiting for semaphore
        assert wp.browser_active == 1
        assert wp.http_active == 0

        await wp.drain()
        assert wp.succeeded == 3

    async def test_mixed_browser_http_same_domain(self):
        """HTTP and browser items for the same domain use separate semaphores."""
        wp = WorkerPool(2, max_browser=1)

        order = []

        async def http_run():
            order.append("http")
            return (True, 0.01)

        async def browser_run():
            order.append("browser")
            return (True, 0.01)

        wp.submit(WorkItem(domain="mix.com", kind="scrape", run=http_run))
        wp.submit(WorkItem(domain="mix.com", kind="scrape", run=browser_run, needs_browser=True))

        await wp.drain()
        assert wp.succeeded == 2
        assert set(order) == {"http", "browser"}


# ── TestRunContinuousLoop ────────────────────────────────────────────


class TestRunContinuousLoop:
    async def test_shutdown_stops_cleanly(self):
        """Immediate shutdown returns without error."""
        shutdown = asyncio.Event()
        pool = _mock_pool()
        http = AsyncMock()
        shutdown.set()
        await run_continuous_loop(pool, http, shutdown, max_concurrent=5, worker_id="t")
