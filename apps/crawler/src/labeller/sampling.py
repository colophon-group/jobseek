"""Sample diverse job postings from the local Postgres for daily labelling.

Diversity objective: one posting per company first, then fill the remaining
slots with a deterministic weighted draw.  The pre-label inputs use resolved
occupation IDs as the observable profession proxy and normalized primary
locales; rarer values inside the candidate window receive more weight.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

CANDIDATE_LIMIT_FLOOR = 10_000
CANDIDATES_PER_REQUESTED_SAMPLE = 250
CANDIDATE_LIMIT_CEILING = 50_000
MAX_RARITY_MULTIPLIER = 4.0

SAMPLE_CANDIDATES_SQL = """
    SELECT p.id::text AS posting_id,
           p.company_id::text AS company_id,
           p.source_url,
           p.occupation_id,
           p.locales[1] AS primary_locale
    FROM job_posting p
    WHERE p.first_seen_at >= $1 AND p.first_seen_at < $2
      AND p.is_active = true
    ORDER BY p.first_seen_at DESC, p.id
    LIMIT $3
"""


@dataclass(frozen=True)
class Sample:
    posting_id: str
    company_id: str
    source_url: str
    occupation_id: int | None = None
    primary_locale: str | None = None


def _candidate_limit(count: int) -> int:
    """Bound the indexed candidate read while scaling for larger requested batches."""

    return min(
        CANDIDATE_LIMIT_CEILING,
        max(CANDIDATE_LIMIT_FLOOR, count * CANDIDATES_PER_REQUESTED_SAMPLE),
    )


def _normalize_locale(value: str | None) -> str | None:
    """Reduce a crawler locale such as ``de_CH`` or ``de-CH`` to ``de``."""

    if not value:
        return None
    primary = re.split(r"[-_]", value.strip().lower(), maxsplit=1)[0]
    return primary or None


def _rarity_weights(candidates: list[Sample]) -> dict[str, float]:
    """Return bounded inverse-frequency weights for observable strata.

    Frequencies come from the same bounded lookback as the candidate draw.
    Missing occupation/locale values are neutral instead of being treated as
    rare, because absence is not a useful representation target.  The square
    root and cap prevent a singleton stratum from monopolizing the tail.
    """

    occupations = Counter(
        sample.occupation_id for sample in candidates if sample.occupation_id is not None
    )
    locales = Counter(sample.primary_locale for sample in candidates if sample.primary_locale)
    largest_occupation = max(occupations.values(), default=0)
    largest_locale = max(locales.values(), default=0)

    weights: dict[str, float] = {}
    for sample in candidates:
        components: list[float] = []
        if sample.occupation_id is not None and largest_occupation:
            components.append(
                min(
                    MAX_RARITY_MULTIPLIER,
                    math.sqrt(largest_occupation / occupations[sample.occupation_id]),
                )
            )
        if sample.primary_locale and largest_locale:
            components.append(
                min(
                    MAX_RARITY_MULTIPLIER,
                    math.sqrt(largest_locale / locales[sample.primary_locale]),
                )
            )
        weights[sample.posting_id] = sum(components) / len(components) if components else 1.0
    return weights


def _weighted_without_replacement(
    candidates: list[Sample],
    *,
    count: int,
    weights: dict[str, float],
    rng: random.Random,
) -> list[Sample]:
    """Build a deterministic size-biased ordering using exponential clocks."""

    ranked: list[tuple[float, int, Sample]] = []
    for position, sample in enumerate(candidates):
        # random() is in [0, 1), so log1p remains finite.  Stable position is
        # an explicit tie-breaker for the vanishingly unlikely equal clock.
        clock = -math.log1p(-rng.random()) / weights[sample.posting_id]
        ranked.append((clock, position, sample))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [sample for _, _, sample in ranked[:count]]


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
    are exhausted before reaching ``count``, fills the remainder using
    bounded inverse-frequency occupation/locale weights from the same window.
    """
    if count <= 0:
        return []
    if window_hours <= 0:
        raise ValueError("window_hours must be positive")

    start_time = end_time_utc - timedelta(hours=window_hours)
    candidate_limit = _candidate_limit(count)

    rows = await pool.fetch(SAMPLE_CANDIDATES_SQL, start_time, end_time_utc, candidate_limit)

    rng = random.Random(seed)
    per_company: dict[str, list[Sample]] = {}
    for row in rows:
        s = Sample(
            posting_id=row["posting_id"],
            company_id=row["company_id"],
            source_url=row["source_url"],
            occupation_id=row["occupation_id"],
            primary_locale=_normalize_locale(row["primary_locale"]),
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

    # Second pass: fill the tail using representation weights derived from the
    # complete bounded candidate set, not from the already-picked subset.
    remaining = count - len(first_pass)
    leftover: list[Sample] = []
    picked = {s.posting_id for s in first_pass}
    for pool_for_company in per_company.values():
        for s in pool_for_company:
            if s.posting_id not in picked:
                leftover.append(s)
    weights = _rarity_weights([sample for samples in per_company.values() for sample in samples])
    return first_pass + _weighted_without_replacement(
        leftover,
        count=remaining,
        weights=weights,
        rng=rng,
    )


def utc_now_minute_floor() -> datetime:
    now = datetime.now(tz=UTC)
    return now.replace(second=0, microsecond=0)
