"""Enricher — batch LLM enrichment of job posting descriptions.

Usage:
    uv run enricher                          # continuous loop
    uv run enricher --once                   # one submit + collect cycle, then exit
    uv run enricher --limit 1000             # process at most N items, then exit
    uv run enricher --dry-run                # build prompts, estimate cost, no LLM calls
    uv run enricher --reprocess              # re-queue items below current ENRICH_VERSION
    uv run enricher --collect-only           # only check for completed batches
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
from time import monotonic

import structlog

from src.config import settings
from src.core.enrich.batch import (
    check_daily_budget,
    collect_completed_batches,
    prepare_batch,
    submit_batch,
)
from src.core.enrich.job import ENRICH_VERSION
from src.core.enrich.providers import create_provider
from src.db import close_pool, create_pool
from src.shared.logging import setup_logging

log = structlog.get_logger()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM enrichment of job postings")
    parser.add_argument("--once", action="store_true", help="One cycle then exit")
    parser.add_argument("--limit", type=int, help="Max items to process then exit")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts only, no API calls")
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-queue items below current version",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Only collect completed batches",
    )
    return parser.parse_args()


async def _reprocess(pool) -> int:
    """Re-queue postings below current ENRICH_VERSION."""
    result = await pool.execute(
        """
        UPDATE job_posting SET to_be_enriched = true
        WHERE enrich_version < $1
          AND is_active = true
          AND description_r2_hash IS NOT NULL
        """,
        ENRICH_VERSION,
    )
    count = int(result.split()[-1]) if result else 0
    log.info("enricher.reprocessed", count=count, target_version=ENRICH_VERSION)
    return count


async def run_loop(
    pool,
    provider,
    shutdown_event: asyncio.Event,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    collect_only: bool = False,
) -> None:
    total_processed = 0
    last_submit = 0.0

    while not shutdown_event.is_set():
        # Phase A: Collect completed batches
        completed = await collect_completed_batches(pool, provider)
        if completed:
            log.info("enricher.collected", batches=completed)

        if collect_only:
            if completed == 0:
                break  # nothing left to collect
            continue

        # Phase B: Submit new batch if conditions met
        pending = await pool.fetchval(
            "SELECT count(*) FROM job_posting WHERE is_active AND to_be_enriched"
        )
        elapsed = monotonic() - last_submit

        should_submit = pending >= settings.enrich_min_batch_size or (
            pending > 0 and elapsed > settings.enrich_max_wait_minutes * 60
        )

        if should_submit and await check_daily_budget(pool):
            batch_size = min(pending, settings.enrich_batch_size)
            if limit is not None:
                batch_size = min(batch_size, limit - total_processed)

            if batch_size <= 0:
                log.info("enricher.limit_reached", total=total_processed)
                break

            result = await prepare_batch(pool, batch_size)
            if result:
                requests, posting_ids = result
                if dry_run:
                    avg_chars = sum(len(r.user_content) for r in requests) // len(requests)
                    est_tokens = sum(len(r.user_content) // 4 for r in requests)
                    log.info(
                        "enricher.dry_run",
                        items=len(requests),
                        avg_input_chars=avg_chars,
                        est_input_tokens=est_tokens,
                    )
                    # Re-queue since we claimed but won't submit
                    await pool.execute(
                        "UPDATE job_posting SET to_be_enriched = true WHERE id = ANY($1::uuid[])",
                        posting_ids,
                    )
                else:
                    batch_id = await submit_batch(pool, provider, requests, posting_ids)
                    log.info("enricher.submitted", batch_id=batch_id, items=len(requests))
                    last_submit = monotonic()

                total_processed += len(requests)

                if limit is not None and total_processed >= limit:
                    log.info("enricher.limit_reached", total=total_processed)
                    break
        elif pending > 0 and not await check_daily_budget(pool):
            log.info("enricher.budget_paused", pending=pending)

        if shutdown_event.is_set():
            break

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=settings.enrich_poll_interval,
            )


async def run() -> None:
    args = parse_args()
    setup_logging(settings.log_level)

    if not settings.enrich_provider:
        log.info("enricher.disabled", reason="enrich_provider not configured")
        return

    log.info(
        "enricher.starting",
        provider=settings.enrich_provider,
        model=settings.enrich_model,
        batch_size=settings.enrich_batch_size,
        version=ENRICH_VERSION,
    )

    pool = await create_pool()
    provider = create_provider(
        settings.enrich_provider,
        settings.enrich_model,
        settings.enrich_api_key,
    )

    try:
        if args.reprocess:
            await _reprocess(pool)
            if args.once:
                return

        shutdown_event = asyncio.Event()

        if args.once:
            shutdown_event.set()  # will exit after one iteration

        if not args.once:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: shutdown_event.set())

        await run_loop(
            pool,
            provider,
            shutdown_event,
            limit=args.limit,
            dry_run=args.dry_run,
            collect_only=args.collect_only,
        )
    finally:
        log.info("enricher.shutting_down")
        await close_pool()
        log.info("enricher.stopped")


def main():
    asyncio.run(run())
