"""Scheduler — Layer 3.

Environment-specific entry point that calls the batch processor on a schedule.
Default: continuous worker pool. Also supports one-shot mode for CLI / CI
and the legacy poll loop (run_poll_loop, kept for rollback).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import uuid

import asyncpg
import dotenv
import structlog

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv(".env")

from src.batch import (  # noqa: E402
    WorkItem,
    claim_monitor_work,
    claim_scrape_work,
    dry_run_single_board,
    process_monitor_batch,
    process_scrape_batch,
    run_single_board,
)
from src.config import settings  # noqa: E402
from src.db import close_pool, create_pool  # noqa: E402
from src.metrics import (  # noqa: E402
    db_pool_idle,
    db_pool_size,
    queue_depth,
    start_metrics_server,
    task_duration_seconds,
    tasks_active,
    tasks_queued,
    tasks_total,
    tick_skip_total,
)
from src.shared.http import create_http_client  # noqa: E402
from src.shared.logging import setup_logging  # noqa: E402

log = structlog.get_logger()

_rand = uuid.uuid4().hex[:8]
WORKER_ID = f"{settings.worker_id_prefix}-{_rand}" if settings.worker_id_prefix else _rand


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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --board: run monitor + scrape without DB writes (test config changes)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="With --dry-run: log all fields for each discovered/scraped job",
    )
    parser.add_argument(
        "--http-only",
        action="store_true",
        help="Only process HTTP work (skip browser/Playwright tasks)",
    )
    parser.add_argument(
        "--browser-only",
        action="store_true",
        help="Only process browser/Playwright work (skip HTTP tasks)",
    )
    args = parser.parse_args()
    if args.dry_run and not args.board:
        parser.error("--dry-run requires --board")
    if args.verbose and not args.dry_run:
        parser.error("--verbose requires --dry-run")
    if args.http_only and args.browser_only:
        parser.error("--http-only and --browser-only are mutually exclusive")
    return args


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
    """Bounded async worker pool.

    Every submitted item gets its own asyncio task. The semaphore controls
    overall concurrency — multiple items for the same domain run concurrently
    when slots are available. The claim query maximizes domain diversity via
    ``PARTITION BY throttle_key ORDER BY domain_rank``; same-domain items
    only fill remaining slots when no other domains have work.

    Two separate semaphores prevent browser (Playwright) work from starving
    lightweight HTTP work. Items are routed to the appropriate semaphore
    based on their ``needs_browser`` flag.

    Asyncio is single-threaded so dict/set operations between await points
    are atomic — no locks needed.
    """

    _ITEM_TIMEOUT = 300  # 5 minutes per job

    def __init__(self, max_concurrent: int, max_browser: int = 3, db_pool=None) -> None:
        self._http_sem = asyncio.Semaphore(max_concurrent)
        self._browser_sem = asyncio.Semaphore(max_browser)
        self._max_http = max_concurrent
        self._max_browser = max_browser
        self._db_pool = db_pool
        self._domains_inflight: dict[str, int] = {}
        self._tasks: set[asyncio.Task] = set()
        self.total_submitted = 0
        self.succeeded = 0
        self.failed = 0
        self.timed_out = 0

    def _sem_for(self, item: WorkItem) -> asyncio.Semaphore:
        return self._browser_sem if item.needs_browser else self._http_sem

    @property
    def free_slots(self) -> int:
        return self._http_sem._value + self._browser_sem._value

    @property
    def http_free(self) -> int:
        return self._http_sem._value

    @property
    def browser_free(self) -> int:
        return self._browser_sem._value

    @property
    def inflight_domains(self) -> list[str]:
        return list(self._domains_inflight.keys())

    @property
    def active_count(self) -> int:
        return (self._max_http - self._http_sem._value) + (
            self._max_browser - self._browser_sem._value
        )

    @property
    def http_active(self) -> int:
        return self._max_http - self._http_sem._value

    @property
    def browser_active(self) -> int:
        return self._max_browser - self._browser_sem._value

    @property
    def queued_count(self) -> int:
        """Tasks waiting on semaphore (submitted but not yet running)."""
        return max(0, len(self._tasks) - self.active_count)

    @property
    def claim_budget(self) -> int:
        """How many items the loop should claim this tick.

        Bounded by both free concurrency slots and idle DB connections.
        This prevents claiming work that would queue in memory waiting
        for a connection — the main cause of memory pressure on
        constrained machines.
        """
        budget = self.free_slots
        if self._db_pool is not None:
            budget = min(budget, self._db_pool.get_idle_size())
        return budget

    def submit(self, item: WorkItem) -> None:
        """Schedule a work item.

        Every item starts its own task immediately. The semaphore controls
        overall concurrency — items block on acquire until a slot is free.
        The claim query already maximizes domain diversity via
        ``PARTITION BY throttle_key ORDER BY domain_rank``, so items arrive
        interleaved across domains. This means different domains fill slots
        first, and same-domain items only share slots when no other domains
        have work.
        """
        self._domains_inflight[item.domain] = self._domains_inflight.get(item.domain, 0) + 1
        task = asyncio.create_task(self._run(item))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self.total_submitted += 1

    async def _run(self, item: WorkItem) -> None:
        """Acquire the appropriate semaphore slot and process one item.

        Browser items use ``_browser_sem``; HTTP items use ``_http_sem``.
        """
        sem = self._sem_for(item)
        try:
            await sem.acquire()
            try:
                ok, elapsed = await self._run_with_heartbeat(item)
                task_duration_seconds.labels(kind=item.kind).observe(elapsed)
                if ok:
                    self.succeeded += 1
                    tasks_total.labels(kind=item.kind, status="succeeded").inc()
                else:
                    self.failed += 1
                    tasks_total.labels(kind=item.kind, status="failed").inc()
            except TimeoutError:
                self.failed += 1
                self.timed_out += 1
                tasks_total.labels(kind=item.kind, status="timed_out").inc()
                log.error(
                    "pool.task_timeout",
                    domain=item.domain,
                    kind=item.kind,
                    timeout_s=self._ITEM_TIMEOUT,
                )
                if item.on_timeout is not None:
                    with contextlib.suppress(Exception):
                        await item.on_timeout()
            except Exception:
                self.failed += 1
                tasks_total.labels(kind=item.kind, status="failed").inc()
                log.exception("pool.task_error", domain=item.domain, kind=item.kind)
            finally:
                sem.release()
        finally:
            # Outer finally: always decrement even if cancelled during sem.acquire()
            count = self._domains_inflight.get(item.domain, 1) - 1
            if count <= 0:
                self._domains_inflight.pop(item.domain, None)
            else:
                self._domains_inflight[item.domain] = count

    async def _run_with_heartbeat(self, item: WorkItem) -> tuple[bool, float]:
        """Run a work item, using heartbeat-aware timeout if a DeadlineExtender is set."""
        extender = item.deadline_extender
        if extender is None:
            return await asyncio.wait_for(item.run(), timeout=self._ITEM_TIMEOUT)

        # Heartbeat-aware: renew deadline each time extender is pulsed
        task = asyncio.ensure_future(item.run())
        try:
            while not task.done():
                extender._event.clear()
                done, _ = await asyncio.wait({task}, timeout=self._ITEM_TIMEOUT)
                if done:
                    break
                # Task not done — check if we got a heartbeat
                if extender._event.is_set():
                    continue  # heartbeat received, renew deadline
                # No heartbeat — truly timed out
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise TimeoutError
            return task.result()
        except BaseException:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            raise

    async def drain(self, timeout: float = 50) -> None:
        """Wait for in-flight tasks, cancelling stragglers after *timeout* seconds."""
        if not self._tasks:
            return
        try:
            await asyncio.wait_for(self._drain_all(), timeout=timeout)
        except TimeoutError:
            log.warning("pool.drain_timeout", remaining=len(self._tasks), timeout_s=timeout)
            for t in list(self._tasks):
                t.cancel()
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _drain_all(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


# ── Continuous Loop ──────────────────────────────────────────────────


_QUEUE_DEPTH_SQL = """
SELECT kind, browser, initial, cnt FROM (
  SELECT 'monitor' AS kind, monitor_needs_browser AS browser,
         false AS initial, COUNT(*) AS cnt
  FROM job_board
  WHERE is_enabled = true
    AND board_status IN ('active', 'suspect')
    AND next_check_at <= now()
    AND (leased_until IS NULL OR leased_until < now())
  GROUP BY monitor_needs_browser
  UNION ALL
  SELECT 'scrape' AS kind, b.scraper_needs_browser AS browser,
         (jp.titles = '{}') AS initial, COUNT(*) AS cnt
  FROM job_posting jp
  JOIN job_board b ON b.id = jp.board_id
  WHERE jp.next_scrape_at <= now()
    AND jp.is_active = true
    AND (jp.leased_until IS NULL OR jp.leased_until < now())
  GROUP BY b.scraper_needs_browser, (jp.titles = '{}')
) q
"""


async def _update_queue_depth(pool) -> None:
    """Query DB for pending work counts and update Prometheus gauges."""
    try:
        rows = await pool.fetch(_QUEUE_DEPTH_SQL)
        # Reset all to 0 first, then set from query results
        for kind in ("monitor", "scrape"):
            for browser in ("true", "false"):
                for initial in ("true", "false"):
                    queue_depth.labels(kind=kind, browser=browser, initial=initial).set(0)
        for row in rows:
            queue_depth.labels(
                kind=row["kind"],
                browser=str(row["browser"]).lower(),
                initial=str(row["initial"]).lower(),
            ).set(row["cnt"])
    except Exception:
        pass  # non-critical — skip on error


async def run_continuous_loop(
    pool,
    http,
    shutdown_event: asyncio.Event,
    *,
    monitor: bool = True,
    scrape: bool = True,
    worker_id: str = "",
    max_concurrent: int = 0,
    max_browser: int = 0,
    http_only: bool = False,
    browser_only: bool = False,
) -> None:
    """Continuous worker pool scheduler.

    Claims items interleaved across domains (via SQL ``PARTITION BY
    throttle_key``), submits each to a bounded pool. Multiple items for
    the same domain run concurrently when slots are available. Monitors
    get priority; scrapes fill remaining capacity.

    Browser (Playwright) work is capped separately by *max_browser* to
    prevent Chromium instances from starving lightweight HTTP work.

    When *http_only* is set, browser phases (2 and 4) are skipped and
    ``max_browser`` is forced to 0.  When *browser_only* is set, HTTP
    phases (1 and 3) are skipped and ``max_concurrent`` is forced to 0.
    This allows running separate worker instances for HTTP and browser work.
    """
    worker_id = worker_id or WORKER_ID
    max_concurrent = max_concurrent or settings.crawler_max_concurrent
    max_browser = max_browser or settings.crawler_max_browser

    if http_only:
        max_browser = 0
    if browser_only:
        max_concurrent = 0
    max_interval = settings.crawler_poll_interval
    idle_interval = 1.0

    wp = WorkerPool(max_concurrent, max_browser=max_browser, db_pool=pool)
    log.info(
        "pool.starting",
        max_concurrent=max_concurrent,
        max_browser=max_browser,
        monitor=monitor,
        scrape=scrape,
        http_only=http_only,
        browser_only=browser_only,
    )

    # Start background R2 drain worker (auto-restart on crash)
    from src.r2_worker import drain_remaining, run_r2_drain_loop

    async def _r2_drain_supervised():
        while not shutdown_event.is_set():
            try:
                await run_r2_drain_loop(pool, shutdown_event)
            except Exception:
                log.exception("r2_drain.crashed")
                if not shutdown_event.is_set():
                    await asyncio.sleep(5)

    r2_drain_task = asyncio.create_task(_r2_drain_supervised())

    while not shutdown_event.is_set():
        work_found = False
        monitors_claimed = 0
        scrapes_claimed = 0

        # Skip claim queries when all slots are full — nothing can be submitted
        if wp.http_free == 0 and wp.browser_free == 0:
            tick_skip_total.labels(reason="slots_full").inc()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
            continue

        # Skip claim queries when no DB connections are idle
        if pool.get_idle_size() == 0:
            tick_skip_total.labels(reason="db_idle").inc()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
            continue

        try:
            skip = []  # no domain exclusion — semaphore controls concurrency

            # Phase 1: HTTP monitors (priority)
            if not browser_only:
                budget = wp.http_free
                if monitor and budget > 0:
                    items = await claim_monitor_work(pool, http, budget, worker_id, skip)
                    monitors_claimed += len(items)
                    for item in items:
                        wp.submit(item)
                        work_found = True

            # Phase 2: browser monitors
            if not http_only:
                budget = wp.browser_free
                if monitor and budget > 0:
                    items = await claim_monitor_work(
                        pool,
                        http,
                        budget,
                        worker_id,
                        skip,
                        browser=True,
                    )
                    monitors_claimed += len(items)
                    for item in items:
                        wp.submit(item)
                        work_found = True

            # Phase 3: HTTP scrapes (fill remaining)
            if not browser_only:
                budget = wp.http_free
                if scrape and budget > 0:
                    items = await claim_scrape_work(pool, http, budget, worker_id, skip)
                    scrapes_claimed += len(items)
                    for item in items:
                        wp.submit(item)
                        work_found = True

            # Phase 4: browser scrapes (fill remaining)
            if not http_only:
                budget = wp.browser_free
                if scrape and budget > 0:
                    items = await claim_scrape_work(
                        pool,
                        http,
                        budget,
                        worker_id,
                        skip,
                        browser=True,
                    )
                    scrapes_claimed += len(items)
                    for item in items:
                        wp.submit(item)
                        work_found = True
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            log.warning("pool.claim_error", error=str(exc))
            # Back off and retry on the next tick
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=5.0)
            continue

        tasks_active.set(wp.active_count)
        tasks_queued.set(wp.queued_count)
        db_pool_size.set(pool.get_size())
        db_pool_idle.set(pool.get_idle_size())

        # Update queue depth from DB (throttled — only when idle or every ~30s)
        if not work_found or wp.succeeded % 30 == 0:
            await _update_queue_depth(pool)

        if work_found or wp.active_count > 0:
            log.info(
                "pool.tick",
                monitors_claimed=monitors_claimed,
                scrapes_claimed=scrapes_claimed,
                active=wp.active_count,
                browser_active=wp.browser_active,
                queued=wp.queued_count,
                db_idle=pool.get_idle_size(),
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

    # Stop R2 drain loop and flush remaining pending uploads
    r2_drain_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await r2_drain_task
    await drain_remaining(pool)

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

    mode_label = "once" if args.once else "pool"
    if args.http_only:
        mode_label += " (http-only)"
    elif args.browser_only:
        mode_label += " (browser-only)"

    log.info(
        "scheduler.starting",
        mode=mode_label,
        monitor=do_monitor,
        scrape=do_scrape,
        batch_limit=settings.crawler_batch_limit,
        poll_interval=settings.crawler_poll_interval,
        max_concurrent=settings.crawler_max_concurrent,
        max_browser=settings.crawler_max_browser,
        http_only=args.http_only,
        browser_only=args.browser_only,
    )

    if not args.once and not args.board:
        start_metrics_server(settings.metrics_port)
        log.info("metrics.started", port=settings.metrics_port)

    pool = await create_pool()
    http = create_http_client()

    try:
        if args.board and args.dry_run:
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                await dry_run_single_board(pool, http, args.board, verbose=args.verbose, pw=pw)
        elif args.board:
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
                http_only=args.http_only,
                browser_only=args.browser_only,
            )
    finally:
        log.info("scheduler.shutting_down")
        await http.aclose()
        await close_pool()
        log.info("scheduler.stopped")


def main():
    asyncio.run(run())
