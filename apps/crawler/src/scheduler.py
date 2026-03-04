"""Scheduler — Layer 3.

Environment-specific entry point that calls the batch processor on a schedule.
Default: Fly.io poll loop. Also supports one-shot mode for CLI / CI.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal

import structlog

from src.batch import process_monitor_batch, process_scrape_batch
from src.config import settings
from src.db import close_pool, create_pool
from src.shared.http import create_http_client
from src.shared.logging import setup_logging

log = structlog.get_logger()


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
    return parser.parse_args()


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
        result = await process_monitor_batch(pool, http, limit=limit)
        log.info("scheduler.monitor_batch", **vars(result))

    if scrape:
        result = await process_scrape_batch(pool, http, limit=limit)
        log.info("scheduler.scrape_batch", **vars(result))


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
            result = await process_monitor_batch(pool, http, limit=limit)
            if result.processed > 0:
                did_work = True
                log.info("scheduler.monitor_batch", **vars(result))

        if scrape:
            result = await process_scrape_batch(pool, http, limit=limit)
            if result.processed > 0:
                did_work = True
                log.info("scheduler.scrape_batch", **vars(result))

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
        if args.once:
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
