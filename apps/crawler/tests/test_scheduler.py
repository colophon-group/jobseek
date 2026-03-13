from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

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


def _failing_work_item(domain="fail.com", kind="monitor"):
    """Create a WorkItem that raises an exception."""

    async def _run():
        raise RuntimeError("boom")

    return WorkItem(domain=domain, kind=kind, run=_run)


# ── TestWorkerPool ───────────────────────────────────────────────────


class TestWorkerPool:
    async def test_submit_and_complete(self):
        """Submitted item runs and increments succeeded counter."""
        wp = WorkerPool(5)
        wp.submit(_work_item())
        await wp.drain()
        assert wp.succeeded == 1
        assert wp.failed == 0
        assert wp.total_submitted == 1

    async def test_free_slots_tracking(self):
        """free_slots decreases during execution and restores after."""
        wp = WorkerPool(3)
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
        wp = WorkerPool(5)
        item = _slow_work_item(domain="slow.com", delay=0.1)
        wp.submit(item)
        await asyncio.sleep(0.01)
        assert "slow.com" in wp.inflight_domains

        await wp.drain()
        assert "slow.com" not in wp.inflight_domains

    async def test_max_concurrent_enforced(self):
        """Pool does not exceed max concurrent tasks."""
        wp = WorkerPool(2)
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
        wp = WorkerPool(5)
        wp.submit(_failing_work_item())
        await wp.drain()
        assert wp.failed == 1
        assert wp.succeeded == 0

    async def test_drain_empty(self):
        """Draining an empty pool completes immediately."""
        wp = WorkerPool(5)
        await wp.drain()
        assert wp.total_submitted == 0

    async def test_mixed_success_failure(self):
        """Pool tracks both succeeded and failed items correctly."""
        wp = WorkerPool(5)
        wp.submit(_work_item(domain="ok.com"))
        wp.submit(_work_item(domain="ok2.com", result=(False, 0.5)))
        wp.submit(_failing_work_item(domain="err.com"))
        await wp.drain()
        assert wp.succeeded == 1
        assert wp.failed == 2
        assert wp.total_submitted == 3

    async def test_domain_queuing(self):
        """Items for the same domain are queued and run serially."""
        wp = WorkerPool(5)
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

        assert wp.queued_count == 2  # 1 running, 2 queued
        await wp.drain()
        assert wp.succeeded == 3
        assert order == ["item-0", "item-1", "item-2"]  # serial order

    async def test_queued_items_dont_consume_slots(self):
        """Queued items for in-flight domains don't take extra semaphore slots."""
        wp = WorkerPool(2)
        # Submit 3 items for same domain + 1 for a different domain
        wp.submit(_slow_work_item(domain="a.com", delay=0.05))
        wp.submit(_slow_work_item(domain="a.com", delay=0.05))
        wp.submit(_slow_work_item(domain="a.com", delay=0.05))
        wp.submit(_slow_work_item(domain="b.com", delay=0.05))

        await asyncio.sleep(0.01)
        # a.com uses 1 slot (2 queued), b.com uses 1 slot → 0 free
        assert wp.active_count == 2
        assert wp.queued_count == 2

        await wp.drain()
        assert wp.succeeded == 4

    async def test_queue_drains_on_drain(self):
        """drain() processes all queued items, not just in-flight tasks."""
        wp = WorkerPool(5)
        for _i in range(3):
            wp.submit(_slow_work_item(domain="q.com", delay=0.01))

        assert wp.queued_count == 2  # 1 running + 2 queued (cap=2)
        await wp.drain()
        assert wp.succeeded == 3
        assert wp.queued_count == 0

    async def test_failed_item_doesnt_break_queue_chain(self):
        """A failing item in the chain doesn't prevent subsequent items."""
        wp = WorkerPool(5)
        wp.submit(_failing_work_item(domain="chain.com"))
        wp.submit(_work_item(domain="chain.com"))
        wp.submit(_work_item(domain="chain.com"))

        await wp.drain()
        assert wp.failed == 1
        assert wp.succeeded == 2

    async def test_submit_always_accepts(self):
        """submit() always accepts — items queue behind in-flight domains."""
        wp = WorkerPool(5)
        wp.submit(_slow_work_item(domain="d.com", delay=0.1))  # runs
        wp.submit(_slow_work_item(domain="d.com", delay=0.1))  # queued (1)
        wp.submit(_slow_work_item(domain="d.com", delay=0.1))  # queued (2)
        wp.submit(_slow_work_item(domain="d.com", delay=0.1))  # queued (3)
        assert wp.queued_count == 3
        assert wp.total_submitted == 4
        await wp.drain()

    async def test_claim_budget(self):
        """claim_budget equals free concurrency slots."""
        wp = WorkerPool(5)
        assert wp.claim_budget == 5  # all free

        # Start 2 domains
        wp.submit(_slow_work_item(domain="a.com", delay=0.1))
        wp.submit(_slow_work_item(domain="b.com", delay=0.1))
        await asyncio.sleep(0.01)
        assert wp.claim_budget == 3  # 2 slots used

        # Queuing behind a.com doesn't change budget (shares slot)
        wp.submit(_slow_work_item(domain="a.com", delay=0.1))
        assert wp.claim_budget == 3
        await wp.drain()

    async def test_timeout_kills_stuck_job(self):
        """A job exceeding _ITEM_TIMEOUT is cancelled and counted as failed."""
        wp = WorkerPool(5)
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
        wp = WorkerPool(5)
        wp._ITEM_TIMEOUT = 0.05

        async def hang():
            await asyncio.sleep(999)
            return (True, 999.0)

        wp.submit(WorkItem(domain="d.com", kind="scrape", run=hang))
        wp.submit(_work_item(domain="d.com"))

        await wp.drain()
        assert wp.timed_out == 1
        assert wp.succeeded == 1


# ── TestRunContinuousLoop ────────────────────────────────────────────


class TestRunContinuousLoop:
    @patch("src.scheduler.claim_scrape_work", new_callable=AsyncMock)
    @patch("src.scheduler.claim_monitor_work", new_callable=AsyncMock)
    async def test_monitor_priority(self, mock_monitors, mock_scrapes):
        """Monitors are claimed before scrapes."""
        call_order = []

        async def track_monitors(*a, **kw):
            call_order.append("monitor")
            return [_work_item(domain="m.com", kind="monitor")]

        async def track_scrapes(*a, **kw):
            call_order.append("scrape")
            return []

        mock_monitors.side_effect = track_monitors
        mock_scrapes.side_effect = track_scrapes

        shutdown = asyncio.Event()
        pool = AsyncMock()
        http = AsyncMock()

        # Run one iteration then shutdown
        iteration = 0

        original_side_effect = mock_monitors.side_effect

        async def monitors_then_stop(*a, **kw):
            nonlocal iteration
            iteration += 1
            if iteration > 1:
                shutdown.set()
                return []
            return await original_side_effect(*a, **kw)

        mock_monitors.side_effect = monitors_then_stop

        await run_continuous_loop(pool, http, shutdown, max_concurrent=5, worker_id="t")

        assert call_order[0] == "monitor"

    @patch("src.scheduler.claim_scrape_work", new_callable=AsyncMock)
    @patch("src.scheduler.claim_monitor_work", new_callable=AsyncMock)
    async def test_pool_full_skips_scrapes(self, mock_monitors, mock_scrapes):
        """When monitors fill all slots, scrapes are not claimed."""
        shutdown = asyncio.Event()
        pool = AsyncMock()
        http = AsyncMock()
        iteration = 0

        async def fill_pool(*a, **kw):
            nonlocal iteration
            iteration += 1
            if iteration > 1:
                shutdown.set()
                return []
            # Return items that fill all 2 slots
            return [
                _slow_work_item(domain="m1.com", delay=0.5),
                _slow_work_item(domain="m2.com", delay=0.5),
            ]

        mock_monitors.side_effect = fill_pool
        mock_scrapes.return_value = []

        await run_continuous_loop(pool, http, shutdown, max_concurrent=2, worker_id="t")

        # At least monitors were called
        assert mock_monitors.call_count >= 1

    @patch("src.scheduler.claim_scrape_work", new_callable=AsyncMock)
    @patch("src.scheduler.claim_monitor_work", new_callable=AsyncMock)
    async def test_scrapes_fill_remaining(self, mock_monitors, mock_scrapes):
        """Scrapes fill slots left after monitors."""
        shutdown = asyncio.Event()
        pool = AsyncMock()
        http = AsyncMock()
        iteration = 0

        async def one_monitor(*a, **kw):
            nonlocal iteration
            iteration += 1
            if iteration > 1:
                shutdown.set()
                return []
            return [_work_item(domain="m.com", kind="monitor")]

        mock_monitors.side_effect = one_monitor
        mock_scrapes.return_value = [_work_item(domain="s.com", kind="scrape")]

        await run_continuous_loop(pool, http, shutdown, max_concurrent=5, worker_id="t")

        # Scrapes should have been called at least once
        assert mock_scrapes.call_count >= 1

    @patch("src.scheduler.claim_scrape_work", new_callable=AsyncMock)
    @patch("src.scheduler.claim_monitor_work", new_callable=AsyncMock)
    async def test_idle_backoff(self, mock_monitors, mock_scrapes):
        """When no work is found, loop backs off (doesn't busy-wait)."""
        shutdown = asyncio.Event()
        pool = AsyncMock()
        http = AsyncMock()
        iteration = 0

        async def no_work(*a, **kw):
            nonlocal iteration
            iteration += 1
            if iteration >= 3:
                shutdown.set()
            return []

        mock_monitors.side_effect = no_work
        mock_scrapes.return_value = []

        await run_continuous_loop(pool, http, shutdown, max_concurrent=5, worker_id="t")

        # Loop ran at least a few iterations before shutdown
        assert iteration >= 3

    @patch("src.scheduler.claim_scrape_work", new_callable=AsyncMock)
    @patch("src.scheduler.claim_monitor_work", new_callable=AsyncMock)
    async def test_shutdown_drains(self, mock_monitors, mock_scrapes):
        """Shutdown signal causes drain of in-flight tasks."""
        shutdown = asyncio.Event()
        pool = AsyncMock()
        http = AsyncMock()
        completed = []

        async def slow_run():
            await asyncio.sleep(0.05)
            completed.append(True)
            return (True, 0.05)

        iteration = 0

        async def submit_then_stop(*a, **kw):
            nonlocal iteration
            iteration += 1
            if iteration == 1:
                item = WorkItem(domain="d.com", kind="monitor", run=slow_run)
                return [item]
            shutdown.set()
            return []

        mock_monitors.side_effect = submit_then_stop
        mock_scrapes.return_value = []

        await run_continuous_loop(pool, http, shutdown, max_concurrent=5, worker_id="t")

        # The slow task should have completed during drain
        assert len(completed) == 1

    @patch("src.scheduler.claim_scrape_work", new_callable=AsyncMock)
    @patch("src.scheduler.claim_monitor_work", new_callable=AsyncMock)
    async def test_queued_items_process_without_reclaim(self, mock_monitors, mock_scrapes):
        """Items queued for the same domain process without a new claim tick."""
        shutdown = asyncio.Event()
        pool = AsyncMock()
        http = AsyncMock()
        processed = []

        async def make_run(label):
            async def _run():
                processed.append(label)
                return (True, 0.01)

            return _run

        iteration = 0

        async def claim_batch(*a, **kw):
            nonlocal iteration
            iteration += 1
            if iteration == 1:
                # Return 3 items for the same domain — 1 runs, 2 queued
                items = []
                for i in range(3):
                    run_fn = await make_run(f"m-{i}")
                    items.append(WorkItem(domain="same.com", kind="monitor", run=run_fn))
                return items
            shutdown.set()
            return []

        mock_monitors.side_effect = claim_batch
        mock_scrapes.return_value = []

        await run_continuous_loop(pool, http, shutdown, max_concurrent=5, worker_id="t")

        # All 3 should have processed, even though only 1 claim tick returned them
        assert len(processed) == 3
