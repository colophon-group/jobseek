"""Scheduler — Layer 3.

Environment-specific entry point that calls the batch processor on a schedule.
Default: continuous worker pool. Also supports one-shot mode for CLI / CI
and the legacy poll loop (run_poll_loop, kept for rollback).
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import signal
import uuid

import dotenv
import structlog

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv(".env")

from src.batch import (  # noqa: E402
    WorkItem,
    claim_monitor_work,
    claim_scrape_work,
    process_monitor_batch,
    process_scrape_batch,
    run_single_board,
)
from src.config import settings  # noqa: E402
from src.db import close_pool, create_pool  # noqa: E402
from src.shared.http import create_http_client  # noqa: E402
from src.shared.logging import setup_logging  # noqa: E402

log = structlog.get_logger()

WORKER_ID = uuid.uuid4().hex[:8]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Jobseek crawler scheduler")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one batch and exit (instead of poll loop)",
    )
    parser.add_argument(
        "--monitor-only",
        action="store_true",
        help="Only run monitor batches (no scraping)",
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Only run scrape batches (no monitoring)",
    )
    parser.add_argument(
        "--board",
        type=str,
        help="Process a single board by slug (monitor + scrape, ignores schedule)",
    )
    parser.add_argument(
        "--force-rescrape",
        action="store_true",
        help="With --board: scrape all active jobs, not only due ones",
    )
    return parser.parse_args()


def _batch_log_kwargs(result) -> dict:
    """Build log kwargs from a BatchResult, adding p50/p99 when items were processed."""
    info: dict = {
        "processed": result.processed,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "duration_s": result.duration_s,
    }
    if result.slow_items:
        info["slow_items"] = result.slow_items
    if result.item_durations:
        durations = sorted(result.item_durations)
        info["p50_s"] = round(durations[len(durations) // 2], 2)
        info["p99_s"] = round(durations[int(len(durations) * 0.99)], 2)
        info["max_s"] = round(durations[-1], 2)
    return info


# ── Worker Pool ──────────────────────────────────────────────────────


class WorkerPool:
    """Bounded async worker pool with per-domain queuing.

    Items for the same domain run serially (politeness). Items for different
    domains run concurrently up to *max_concurrent*. When an item finishes,
    the next queued item for that domain starts immediately — no claim tick
    delay.

    Asyncio is single-threaded so set/deque operations between await points
    are atomic — no locks needed.
    """

    _ITEM_TIMEOUT = 300  # 5 minutes per job
    _QUEUE_PER_DOMAIN = 2  # max queued items behind a running domain

    def __init__(self, max_concurrent: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max = max_concurrent
        self._domains_inflight: set[str] = set()
        self._domain_queues: dict[str, collections.deque[WorkItem]] = {}
        self._tasks: set[asyncio.Task] = set()
        self.total_submitted = 0
        self.succeeded = 0
        self.failed = 0
        self.timed_out = 0

    @property
    def free_slots(self) -> int:
        return self._semaphore._value

    @property
    def inflight_domains(self) -> list[str]:
        return list(self._domains_inflight)

    @property
    def active_count(self) -> int:
        return self._max - self._semaphore._value

    @property
    def queued_count(self) -> int:
        return sum(len(q) for q in self._domain_queues.values())

    @property
    def claim_budget(self) -> int:
        """How many items the loop should claim this tick.

        Accounts for free semaphore slots (new domains) plus remaining
        queue capacity across in-flight domains.
        """
        queue_room = max(
            0,
            len(self._domains_inflight) * self._QUEUE_PER_DOMAIN - self.queued_count,
        )
        return self.free_slots + queue_room

    def submit(self, item: WorkItem) -> bool:
        """Schedule a work item. Returns False if the domain queue is full.

        If the domain already has an in-flight task, the item is queued and
        will start automatically when the current one finishes — without
        consuming an extra semaphore slot.
        """
        if item.domain in self._domains_inflight:
            queue = self._domain_queues.get(item.domain)
            if queue is not None and len(queue) >= self._QUEUE_PER_DOMAIN:
                return False
            if queue is None:
                queue = collections.deque()
                self._domain_queues[item.domain] = queue
            queue.append(item)
        else:
            self._domains_inflight.add(item.domain)
            task = asyncio.get_event_loop().create_task(self._run(item))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        self.total_submitted += 1
        return True

    async def _run(self, item: WorkItem) -> None:
        """Acquire a semaphore slot and process items for this domain serially."""
        await self._semaphore.acquire()
        current: WorkItem | None = item
        try:
            while current is not None:
                try:
                    ok, _elapsed = await asyncio.wait_for(current.run(), timeout=self._ITEM_TIMEOUT)
                    if ok:
                        self.succeeded += 1
                    else:
                        self.failed += 1
                except TimeoutError:
                    self.failed += 1
                    self.timed_out += 1
                    log.error(
                        "pool.task_timeout",
                        domain=current.domain,
                        kind=current.kind,
                        timeout_s=self._ITEM_TIMEOUT,
                    )
                except Exception:
                    self.failed += 1
                    log.exception("pool.task_error", domain=current.domain, kind=current.kind)
                # Pop next queued item for this domain (if any)
                current = None
                queue = self._domain_queues.get(item.domain)
                if queue:
                    current = queue.popleft()
                    if not queue:
                        del self._domain_queues[item.domain]
        finally:
            self._domains_inflight.discard(item.domain)
            self._semaphore.release()

    async def drain(self) -> None:
        """Wait for all in-flight tasks (including queued chains) to complete."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


# ── Continuous Loop ──────────────────────────────────────────────────


async def run_continuous_loop(
    pool,
    http,
    shutdown_event: asyncio.Event,
    *,
    monitor: bool = True,
    scrape: bool = True,
    worker_id: str = "",
    max_concurrent: int = 0,
) -> None:
    """Continuous worker pool scheduler.

    Claims items interleaved across domains, submits to a bounded pool with
    per-domain queuing. When a domain's current item finishes, the next
    queued item starts immediately. Monitors get priority; scrapes fill
    remaining capacity.
    """
    worker_id = worker_id or WORKER_ID
    max_concurrent = max_concurrent or settings.crawler_max_concurrent
    max_interval = settings.crawler_poll_interval
    idle_interval = 1.0

    wp = WorkerPool(max_concurrent)
    log.info(
        "pool.starting",
        max_concurrent=max_concurrent,
        monitor=monitor,
        scrape=scrape,
    )

    while not shutdown_event.is_set():
        work_found = False
        monitors_claimed = 0
        scrapes_claimed = 0

        # Phase 1: monitors (priority)
        budget = wp.claim_budget
        if monitor and budget > 0:
            items = await claim_monitor_work(pool, http, budget, worker_id)
            monitors_claimed = len(items)
            for item in items:
                if wp.submit(item):
                    work_found = True

        # Phase 2: scrapes (fill remaining)
        budget = wp.claim_budget
        if scrape and budget > 0:
            items = await claim_scrape_work(pool, http, budget, worker_id)
            scrapes_claimed = len(items)
            for item in items:
                if wp.submit(item):
                    work_found = True

        if work_found or wp.active_count > 0:
            log.info(
                "pool.tick",
                monitors_claimed=monitors_claimed,
                scrapes_claimed=scrapes_claimed,
                active=wp.active_count,
                queued=wp.queued_count,
                succeeded=wp.succeeded,
                failed=wp.failed,
            )
            idle_interval = 1.0
        else:
            idle_interval = min(idle_interval * 2, max_interval)

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=idle_interval)

    log.info("pool.draining", active=wp.active_count, queued=wp.queued_count)
    await wp.drain()
    log.info(
        "pool.stopped",
        total_submitted=wp.total_submitted,
        succeeded=wp.succeeded,
        failed=wp.failed,
    )


# ── One-shot mode ────────────────────────────────────────────────────


async def run_once(
    pool,
    http,
    *,
    monitor: bool = True,
    scrape: bool = True,
) -> None:
    """Process one batch and return."""
    limit = settings.crawler_batch_limit

    if monitor:
        result = await process_monitor_batch(pool, http, limit=limit, worker_id=WORKER_ID)
        log.info("scheduler.monitor_batch", **_batch_log_kwargs(result))

    if scrape:
        result = await process_scrape_batch(pool, http, limit=limit, worker_id=WORKER_ID)
        log.info("scheduler.scrape_batch", **_batch_log_kwargs(result))


# ── Legacy poll loop (kept for rollback) ─────────────────────────────


async def run_poll_loop(
    pool,
    http,
    shutdown_event: asyncio.Event,
    *,
    monitor: bool = True,
    scrape: bool = True,
) -> None:
    """Long-running poll loop with adaptive polling.

    When work is found, checks again quickly (1s). When idle, backs off
    exponentially up to poll_interval. This reduces latency for new work
    while avoiding busy-waiting when idle.
    """
    max_interval = settings.crawler_poll_interval
    limit = settings.crawler_batch_limit
    idle_interval = 1.0  # Start responsive

    while not shutdown_event.is_set():
        did_work = False

        if monitor:
            result = await process_monitor_batch(pool, http, limit=limit, worker_id=WORKER_ID)
            if result.processed > 0:
                did_work = True
                log.info("scheduler.monitor_batch", **_batch_log_kwargs(result))

        if scrape:
            result = await process_scrape_batch(pool, http, limit=limit, worker_id=WORKER_ID)
            if result.processed > 0:
                did_work = True
                log.info("scheduler.scrape_batch", **_batch_log_kwargs(result))

        idle_interval = 1.0 if did_work else min(idle_interval * 2, max_interval)

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=idle_interval)


# ── Entry point ──────────────────────────────────────────────────────


async def run() -> None:
    args = parse_args()
    setup_logging(settings.log_level)

    do_monitor = not args.scrape_only
    do_scrape = not args.monitor_only

    log.info(
        "scheduler.starting",
        mode="once" if args.once else "pool",
        monitor=do_monitor,
        scrape=do_scrape,
        batch_limit=settings.crawler_batch_limit,
        poll_interval=settings.crawler_poll_interval,
        max_concurrent=settings.crawler_max_concurrent,
    )

    pool = await create_pool()
    http = create_http_client()

    try:
        if args.board:
            await run_single_board(pool, http, args.board, force_rescrape=args.force_rescrape)
        elif args.once:
            await run_once(pool, http, monitor=do_monitor, scrape=do_scrape)
        else:
            shutdown_event = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: shutdown_event.set())

            await run_continuous_loop(
                pool,
                http,
                shutdown_event,
                monitor=do_monitor,
                scrape=do_scrape,
            )
    finally:
        log.info("scheduler.shutting_down")
        await http.aclose()
        await close_pool()
        log.info("scheduler.stopped")


def main():
    asyncio.run(run())
