"""Measure peak memory usage of one scheduler cycle.

Usage:
    uv run python scripts/measure_memory.py                  # full cycle
    uv run python scripts/measure_memory.py --monitor-only   # monitors only
    uv run python scripts/measure_memory.py --scrape-only    # scrapers only
"""

from __future__ import annotations

import argparse
import asyncio
import resource
import tracemalloc

import structlog

from src.batch import process_monitor_batch, process_scrape_batch
from src.config import settings
from src.db import close_pool, create_pool
from src.shared.http import create_http_client
from src.shared.logging import setup_logging


def _rss_mb() -> float:
    """Current RSS in MB (macOS returns bytes, Linux returns KB)."""
    import sys

    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return raw / (1024 * 1024)
    return raw / 1024


async def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitor-only", action="store_true")
    parser.add_argument("--scrape-only", action="store_true")
    args = parser.parse_args()

    setup_logging(settings.log_level)
    log = structlog.get_logger()

    tracemalloc.start()
    rss_before = _rss_mb()

    pool = await create_pool()
    http = create_http_client()
    limit = settings.crawler_batch_limit

    try:
        if not args.scrape_only:
            result = await process_monitor_batch(pool, http, limit=limit, worker_id="mem-test")
            log.info(
                "memory.monitor_batch",
                processed=result.processed,
                succeeded=result.succeeded,
                failed=result.failed,
            )

        snapshot_mid = tracemalloc.take_snapshot()
        current_mid, peak_mid = tracemalloc.get_traced_memory()

        if not args.monitor_only:
            result = await process_scrape_batch(pool, http, limit=limit, worker_id="mem-test")
            log.info(
                "memory.scrape_batch",
                processed=result.processed,
                succeeded=result.succeeded,
                failed=result.failed,
            )
    finally:
        await http.aclose()
        await close_pool()

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = _rss_mb()

    print("\n" + "=" * 60)
    print("MEMORY REPORT")
    print("=" * 60)
    print(f"  Python heap (traced):")
    print(f"    After monitors:  {current_mid / 1024 / 1024:8.1f} MB")
    print(f"    Peak (monitors): {peak_mid / 1024 / 1024:8.1f} MB")
    print(f"    Final:           {current / 1024 / 1024:8.1f} MB")
    print(f"    Peak (total):    {peak / 1024 / 1024:8.1f} MB")
    print(f"  Process RSS:")
    print(f"    Before:          {rss_before:8.1f} MB")
    print(f"    After (peak):    {rss_after:8.1f} MB")
    print(f"    Delta:           {rss_after - rss_before:8.1f} MB")
    print("=" * 60)

    # Top 10 allocations by size
    snapshot = tracemalloc.take_snapshot() if tracemalloc.is_tracing() else None
    stats = snapshot_mid.statistics("lineno")
    print("\nTop 10 allocations (after monitors):")
    for stat in stats[:10]:
        print(f"  {stat}")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
