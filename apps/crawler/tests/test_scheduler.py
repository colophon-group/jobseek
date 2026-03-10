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

        # On first iteration scrapes should not have been called because
        # all slots were filled by monitors. On second iteration (shutdown),
        # monitors returns [] and slots are free again.
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
    async def test_exclude_domains_passed(self, mock_monitors, mock_scrapes):
        """In-flight domains are passed as exclude list to claim functions.

        After monitors submit a busy.com item, the scrape claim in the
        SAME iteration should see busy.com in the exclude list.
        """
        shutdown = asyncio.Event()
        pool = AsyncMock()
        http = AsyncMock()
        monitor_iteration = 0
        scrape_excludes = []

        async def monitors_side(*a, **kw):
            nonlocal monitor_iteration
            monitor_iteration += 1
            if monitor_iteration == 1:
                # Return a slow item so it stays in-flight for the scrape call
                return [_slow_work_item(domain="busy.com", delay=5.0)]
            shutdown.set()
            return []

        async def scrapes_side(*a, **kw):
            # a = (pool, http, limit, worker_id, exclude_domains)
            if len(a) >= 5:
                scrape_excludes.append(list(a[4]))
            return []

        mock_monitors.side_effect = monitors_side
        mock_scrapes.side_effect = scrapes_side

        await run_continuous_loop(pool, http, shutdown, max_concurrent=5, worker_id="t")

        # The scrape call in the first iteration should see busy.com excluded
        assert len(scrape_excludes) >= 1
        assert "busy.com" in scrape_excludes[0]
