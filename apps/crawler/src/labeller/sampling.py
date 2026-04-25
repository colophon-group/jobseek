"""Sample diverse job postings from the local Postgres for daily labelling.

Diversity objective: one posting per company first, then fill the remaining
slots by weighted sampling under-represented professions/locales.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg


@dataclass(frozen=True)
class Sample:
    posting_id: str
    company_id: str
    source_url: str


async def sample_postings(
    pool: asyncpg.Pool,
    *,
    end_time_utc: datetime,
    window_hours: int = 24,
    count: int,
    seed: int | None = None,
) -> list[Sample]:
    """Select ``count`` postings first-seen within the window ending at ``end_time_utc``.

    Returns at most ``count``. Prefers one posting per company; if companies
    are exhausted before reaching ``count``, fills the remainder by random
    selection from the same window.
    """
    start_time = end_time_utc - timedelta(hours=window_hours)

    rows = await pool.fetch(
        """
        SELECT p.id::text AS posting_id,
               p.company_id::text AS company_id,
               p.source_url
        FROM job_posting p
        WHERE p.first_seen_at >= $1 AND p.first_seen_at < $2
          AND p.is_active = true
        ORDER BY p.first_seen_at DESC
        """,
        start_time,
        end_time_utc,
    )

    rng = random.Random(seed)
    per_company: dict[str, list[Sample]] = {}
    for row in rows:
        s = Sample(
            posting_id=row["posting_id"],
            company_id=row["company_id"],
            source_url=row["source_url"],
        )
        per_company.setdefault(s.company_id, []).append(s)

    # First pass: one per company (randomized order, randomized per-company choice)
    company_ids = list(per_company.keys())
    rng.shuffle(company_ids)
    first_pass: list[Sample] = []
    for cid in company_ids:
        pool_for_company = per_company[cid]
        first_pass.append(rng.choice(pool_for_company))
        if len(first_pass) >= count:
            return first_pass

    # Second pass: fill remainder by random draw from the leftover postings
    remaining = count - len(first_pass)
    leftover: list[Sample] = []
    picked = {s.posting_id for s in first_pass}
    for pool_for_company in per_company.values():
        for s in pool_for_company:
            if s.posting_id not in picked:
                leftover.append(s)
    rng.shuffle(leftover)
    return first_pass + leftover[:remaining]


def utc_now_minute_floor() -> datetime:
    now = datetime.now(tz=UTC)
    return now.replace(second=0, microsecond=0)
