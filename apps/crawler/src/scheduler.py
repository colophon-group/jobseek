"""Scheduler — Layer 3.

Environment-specific entry point that calls the batch processor on a schedule.
Default: Fly.io poll loop. Also supports one-shot mode for CLI / CI.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import uuid

import structlog

from src.batch import process_monitor_batch, process_scrape_batch, run_single_board
from src.config import settings
from src.db import close_pool, create_pool
from src.shared.http import create_http_client
from src.shared.logging import setup_logging

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


async def run() -> None:
    args = parse_args()
    setup_logging(settings.log_level)

    do_monitor = not args.scrape_only
    do_scrape = not args.monitor_only

    log.info(
        "scheduler.starting",
        mode="once" if args.once else "poll",
        monitor=do_monitor,
        scrape=do_scrape,
        batch_limit=settings.crawler_batch_limit,
        poll_interval=settings.crawler_poll_interval,
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

            await run_poll_loop(
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
