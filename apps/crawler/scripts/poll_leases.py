"""Poll database lease activity over a time window.

Usage:
    uv run python scripts/poll_leases.py                  # 60s window, poll every 5s
    uv run python scripts/poll_leases.py --duration 300    # 5 min window
    uv run python scripts/poll_leases.py --interval 2      # poll every 2s
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from time import monotonic

import asyncpg

from src.config import settings


async def poll(duration: int, interval: int) -> None:
    pool = await asyncpg.create_pool(
        settings.database_url, statement_cache_size=0, min_size=1, max_size=2
    )
    start = monotonic()
    tick = 0
    history: list[dict] = []

    print(
        f"{'t':>4s}  {'boards':>6s}  {'posts':>6s}  "
        f"{'expired':>7s}  {'eligible':>8s}  {'owners':20s}  {'top domain (leased)':30s}  "
        f"{'delta':>6s}"
    )
    print("-" * 120)

    prev_leased = 0

    try:
        while monotonic() - start < duration:
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT
                        (SELECT count(*) FROM job_board
                         WHERE leased_until IS NOT NULL AND leased_until > now()
                        ) AS boards_leased,
                        (SELECT count(*) FROM job_posting
                         WHERE leased_until IS NOT NULL AND leased_until > now()
                        ) AS posts_leased,
                        (SELECT count(*) FROM job_posting
                         WHERE leased_until IS NOT NULL AND leased_until <= now()
                        ) AS posts_expired,
                        (SELECT count(*) FROM job_posting
                         WHERE is_active = true
                           AND next_scrape_at IS NOT NULL
                           AND next_scrape_at <= now()
                           AND (leased_until IS NULL OR leased_until < now())
                        ) AS scrape_eligible
                """)

                top = await conn.fetch("""
                    SELECT split_part(split_part(source_url, '://', 2), '/', 1) AS domain,
                           count(*) AS cnt
                    FROM job_posting
                    WHERE leased_until IS NOT NULL AND leased_until > now()
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 3
                """)

                owners = await conn.fetch("""
                    SELECT COALESCE(jp.lease_owner, '?') AS owner,
                           count(*) AS cnt
                    FROM job_posting jp
                    WHERE jp.leased_until IS NOT NULL AND jp.leased_until > now()
                    GROUP BY 1 ORDER BY 2 DESC
                """)

            boards = row["boards_leased"]
            posts = row["posts_leased"]
            expired = row["posts_expired"]
            eligible = row["scrape_eligible"]
            delta = posts - prev_leased
            prev_leased = posts

            top_str = ", ".join(f"{r['domain']}:{r['cnt']}" for r in top) if top else "-"
            owners_str = ", ".join(f"{r['owner']}:{r['cnt']}" for r in owners) if owners else "-"

            now = datetime.now(UTC).strftime("%H:%M:%S")
            print(
                f"{tick:4d}  {boards:6d}  {posts:6d}  "
                f"{expired:7d}  {eligible:8d}  {owners_str:20s}  {top_str:30s}  "
                f"{delta:+6d}"
            )

            history.append(
                {
                    "tick": tick,
                    "time": now,
                    "boards": boards,
                    "posts": posts,
                    "expired": expired,
                    "eligible": eligible,
                }
            )

            tick += 1
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        pass
    finally:
        # Summary
        if len(history) > 1:
            peak = max(h["posts"] for h in history)
            avg = sum(h["posts"] for h in history) / len(history)
            print("\n" + "=" * 100)
            print(f"Ticks: {len(history)}  |  Peak leased posts: {peak}  |  Avg: {avg:.0f}")
            print(f"Final expired (not cleaned): {history[-1]['expired']}")
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description="Poll lease activity")
    parser.add_argument("--duration", type=int, default=60, help="Window in seconds (default: 60)")
    parser.add_argument("--interval", type=int, default=5, help="Poll interval in seconds")
    args = parser.parse_args()
    asyncio.run(poll(args.duration, args.interval))


if __name__ == "__main__":
    main()
