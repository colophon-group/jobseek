"""Backfill helpers for re-scraping postings that missed an enrichment step.

* ``backfill_locations`` — jobs scraped while GeoNames tables were empty.
* ``backfill_descriptions`` (#2996) — rich-monitor postings stuck with
  ``description_r2_hash IS NULL AND next_scrape_at IS NULL`` because the
  board's enrich config was added AFTER the rows were first inserted via
  ``_INSERT_RICH_JOB`` (which leaves ``next_scrape_at = NULL``). These
  rows never re-enter the scrape queue without operator intervention.
  ``_DIFF_BATCH``'s touched-branch self-heal (also in #2996) prevents
  the bug for FUTURE config flips, but already-stuck rows still need
  a one-shot reset.

Both helpers enqueue re-scrape tasks into Redis at low priority (tier 2).
The existing scrape pipeline handles location resolution and description
fetching. R2 uploads are avoided because ``description_r2_hash`` is
passed through, so ``_stage_r2_pending`` skips unchanged descriptions.

Usage::

    uv run crawler backfill-locations
    uv run crawler backfill-descriptions [--slug <slug> ...] [--dry-run]
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import structlog

from src.core.experience_extract import ExperienceRequirement, extract_experience
from src.redis_queue import enqueue_scrape, get_redis

log = structlog.get_logger()

# Batched two-pass design: the prior single-shot UPDATE-RETURNING over the
# whole result set held row locks on potentially 100k+ rows in one
# transaction, blocking concurrent ``crawler sync`` and exporter writes
# until commit. Both passes use a small ``LIMIT`` so each transaction is
# short-lived; the operator may need to re-run if more candidates arrive.
#
# Pass 1 (PROMOTE): ``next_scrape_at IS NULL`` rows. The scrape worker's
# self-heal in ``pipeline._process_scrape_work`` short-circuits any claim
# whose Postgres row has ``next_scrape_at IS NULL``. Boards with
# ``rescrape_policy = "never"`` (Starbucks, Uber, every paid-proxy board)
# clear ``next_scrape_at`` after the first successful scrape, so a plain
# SELECT-and-enqueue would silently no-op for the largest backfill
# targets. Promote ``next_scrape_at`` to ``now()`` atomically with the
# fetch to open a one-shot scrape window.
#
# Pass 2 (FETCH-ONLY): ``next_scrape_at IS NOT NULL`` rows. Already
# scrape-eligible; just enqueue them. ``OFFSET`` walks the table.
_BACKFILL_BATCH_SIZE = 5000
_EXPERIENCE_REPROCESS_BATCH_SIZE = 1000

_PROMOTE_NEXT_SCRAPE_BATCH = """
WITH targets AS (
    SELECT id FROM job_posting
    WHERE is_active = true
      AND location_ids IS NULL
      AND description_r2_hash IS NOT NULL
      AND next_scrape_at IS NULL
    ORDER BY id
    LIMIT $1
)
UPDATE job_posting jp
SET next_scrape_at = now()
FROM targets t
WHERE jp.id = t.id
RETURNING jp.id::text, jp.source_url, jp.board_id::text, jp.description_r2_hash
"""

_FETCH_ALREADY_DUE_BATCH = """
SELECT jp.id::text, jp.source_url, jp.board_id::text, jp.description_r2_hash
FROM job_posting jp
WHERE jp.is_active = true
  AND jp.location_ids IS NULL
  AND jp.description_r2_hash IS NOT NULL
  AND jp.next_scrape_at IS NOT NULL
ORDER BY jp.id
LIMIT $1
OFFSET $2
"""


async def backfill_locations(pool: asyncpg.Pool) -> int:
    """Enqueue re-scrapes for active jobs missing location_ids.

    Returns the number of tasks enqueued.
    """
    r = get_redis()
    board_cache: dict[str, bool] = {}  # board_id -> needs_browser

    async def _enqueue_rows(rows: list) -> int:
        """Enqueue a batch of rows; return the count actually added to Redis."""
        added_count = 0
        now = time.time()
        for row in rows:
            posting_id = row["id"]
            url = row["source_url"]
            board_id = row["board_id"] or ""
            r2_hash = row["description_r2_hash"]
            domain = urlparse(url).hostname or ""

            if board_id and board_id not in board_cache:
                board_config = await r.hgetall(f"board:{board_id}")
                board_cache[board_id] = (
                    board_config.get("scraper_needs_browser", "0") == "1" if board_config else False
                )
            needs_browser = board_cache.get(board_id, False)

            added = await enqueue_scrape(
                domain,
                posting_id,
                now,
                {
                    "source_url": url,
                    "board_id": board_id,
                    "description_r2_hash": str(r2_hash) if r2_hash is not None else "",
                    "scrape_step": "0",
                },
                browser=needs_browser,
                first_time=False,  # tier 2 = lowest priority
            )
            if added:
                added_count += 1
        return added_count

    enqueued = 0

    # Pass 1: promote next_scrape_at=NULL rows in small batches. Loop
    # until empty — each batch makes progress because the UPDATE flips
    # next_scrape_at to non-NULL, dropping the row out of the WHERE.
    while True:
        rows = await pool.fetch(_PROMOTE_NEXT_SCRAPE_BATCH, _BACKFILL_BATCH_SIZE)
        if not rows:
            break
        log.info("backfill.locations.promote.batch", count=len(rows))
        enqueued += await _enqueue_rows(list(rows))

    # Pass 2: rows already scrape-eligible — read-only, walk via OFFSET.
    # The criteria don't change as we enqueue, so OFFSET pagination is
    # required to avoid re-enqueueing the same rows.
    #
    # Concurrent-write race: if the monitor's ``relisted`` CTE flips a
    # row's ``next_scrape_at`` from NULL to non-NULL during this loop —
    # for an id sorting before the current ``offset`` — the row appears
    # behind us and we re-enqueue it on the next iteration. The Lua
    # ``enqueue_scrape`` dedup absorbs the duplicate (returns False),
    # so the operational effect is just an inflated ``enqueued`` count
    # in the log. Acceptable; documented here so a future maintainer
    # doesn't try to "fix" the count drift by adding row-level locks.
    offset = 0
    while True:
        rows = await pool.fetch(_FETCH_ALREADY_DUE_BATCH, _BACKFILL_BATCH_SIZE, offset)
        if not rows:
            break
        log.info("backfill.locations.fetch.batch", count=len(rows), offset=offset)
        enqueued += await _enqueue_rows(list(rows))
        offset += len(rows)

    if enqueued == 0:
        log.info("backfill.locations.none_needed")
    else:
        log.info("backfill.locations.enqueued", enqueued=enqueued)
    return enqueued


# ── backfill_descriptions (#2996) ────────────────────────────────────
#
# The 20 rich-monitor companies whose scraper-config fixes shipped via
# the #2963 cluster (PRs #2947, #2953, #2954, #2961, #2962, #2964, #2967,
# #2968, #2970, #2971, #2972) but whose existing rows still have
# ``next_scrape_at IS NULL`` because they were inserted via
# ``_INSERT_RICH_JOB`` (no-enrich path) before the config flip.
#
# Slugs match the issue body of #2996 verbatim (excludes DiDi + NEURA —
# tracked separately in #2997, #2998).
_DEFAULT_STUCK_DESCRIPTION_SLUGS: tuple[str, ...] = (
    "alibaba",
    "ayuda-en-accion",
    "bajaj-finserv",
    "barcelona-activa",
    "continental",
    "decathlon",
    "goldman-sachs",
    "haier-group",
    "hitachi-energy",
    "infineon",
    "itau-unibanco",
    "larsen-toubro",
    "loreal",
    "meta",
    "netflix",
    "nokia",
    "terveystalo",
    "tesla",
    "texas-instruments",
    "zte",
)


# Promote ``next_scrape_at = now()`` for postings stuck with NULL because
# the board now has enrich (description_r2_hash IS NULL AND
# next_scrape_at IS NULL). Mirrors ``_PROMOTE_NEXT_SCRAPE_BATCH``: a
# small batch with explicit ORDER BY id so each iteration progresses.
_PROMOTE_DESCRIPTIONS_BATCH = """
WITH targets AS (
    SELECT jp.id FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    WHERE jp.is_active = true
      AND jp.description_r2_hash IS NULL
      AND jp.next_scrape_at IS NULL
      AND ($1::text[] IS NULL OR c.slug = ANY($1::text[]))
    ORDER BY jp.id
    LIMIT $2
)
UPDATE job_posting jp
SET next_scrape_at = now()
FROM targets t
WHERE jp.id = t.id
RETURNING jp.id::text, jp.source_url, jp.board_id::text, jp.description_r2_hash
"""

# Pass 2 — read-only fetch for stuck rows whose ``next_scrape_at`` is
# already non-NULL (typically set by an earlier ad-hoc operator UPDATE
# from the #2996 issue body, OR by a previous invocation of this helper
# that ran Pass 1 and set ``next_scrape_at = now()`` but whose Redis
# enqueue was lost — Redis eviction, container restart between SQL
# UPDATE and HSET, etc.). Without Pass 2 such rows are stranded: Pass
# 1's WHERE excludes them (next_scrape_at is no longer NULL) and
# workers can't pick them up because the per-domain Redis ZSET has no
# entry for them and ``scrape:<id>`` is missing — production workers
# claim from Redis only, not Postgres ``next_scrape_at``.
#
# Walks via OFFSET because the criteria are read-only: every candidate
# row stays a candidate across iterations, so a no-OFFSET LIMIT loop
# would re-enqueue the same first-N rows forever. ``enqueue_scrape``'s
# Lua ZADD-NX dedup absorbs any concurrent re-enqueue from a monitor
# ``relisted`` cycle that lands on a row sorting before ``offset``;
# the cost is an inflated ``enqueued`` count in the log, not duplicate
# scrapes (a deduped enqueue returns added=0 and is not counted).
_FETCH_STUCK_DESCRIPTIONS_BATCH = """
SELECT jp.id::text, jp.source_url, jp.board_id::text, jp.description_r2_hash
FROM job_posting jp
JOIN company c ON c.id = jp.company_id
WHERE jp.is_active = true
  AND jp.description_r2_hash IS NULL
  AND jp.next_scrape_at IS NOT NULL
  AND ($1::text[] IS NULL OR c.slug = ANY($1::text[]))
ORDER BY jp.id
LIMIT $2
OFFSET $3
"""

# Dry-run COUNT — sums Pass 1 + Pass 2 buckets so the dry_run number
# reflects the ACTUAL work the helper would do, not just the legacy
# ``next_scrape_at IS NULL`` bucket. The two buckets are mutually
# exclusive (next_scrape_at is NULL or NOT NULL, never both) so a
# straight UNION ALL is safe.
_COUNT_DESCRIPTIONS_CANDIDATES = """
SELECT COUNT(*)::int FROM job_posting jp
JOIN company c ON c.id = jp.company_id
WHERE jp.is_active = true
  AND jp.description_r2_hash IS NULL
  AND ($1::text[] IS NULL OR c.slug = ANY($1::text[]))
"""


async def backfill_descriptions(
    pool: asyncpg.Pool,
    company_slugs: list[str] | None = None,
    only_missing: bool = True,
    dry_run: bool = False,
) -> int:
    """Enqueue Redis scrape tasks for stuck rich-monitor postings whose
    description is missing.

    Two-pass design mirroring :func:`backfill_locations`:

    1. **PROMOTE** — rows with ``next_scrape_at IS NULL``. Atomically
       sets ``next_scrape_at = now()`` and enqueues into Redis. This is
       the original #2996 path: rows inserted via the no-enrich
       ``_INSERT_RICH_JOB`` before the board's enrich config landed.

    2. **FETCH** — rows with ``next_scrape_at IS NOT NULL AND
       description_r2_hash IS NULL``. Read-only enqueue. Catches rows
       stranded by:
         - the ad-hoc operator UPDATE from #2996's issue body (sets
           ``next_scrape_at`` in DB but does not touch Redis — workers
           claim from Redis in production, never from Postgres);
         - a prior invocation whose Redis enqueue was lost (eviction,
           container restart between SQL UPDATE and HSET).

    Without Pass 2 this helper became a no-op for the affected
    companies once the operator UPDATE landed, since the legacy
    ``next_scrape_at IS NULL`` predicate excluded the already-promoted
    rows.

    Scoped by ``company_slugs`` if provided; otherwise the default 20
    stuck companies from #2996. Returns the number of rows enqueued
    (or, in ``dry_run`` mode, the number that WOULD be considered).

    ``only_missing`` is reserved for future expansion (currently always
    True — the helper exists specifically to clear the missing-description
    bucket). The flag is plumbed through to keep the signature stable
    against later widening.
    """
    if not only_missing:
        # Defensive: callers asking for a wider scope would silently
        # re-scrape healthy rows. Force a NotImplementedError until that
        # mode has its own unit tests.
        raise NotImplementedError("backfill_descriptions only supports only_missing=True today")

    slugs: list[str] | None
    if company_slugs is None:
        slugs = list(_DEFAULT_STUCK_DESCRIPTION_SLUGS)
    elif company_slugs:
        slugs = list(company_slugs)
    else:
        # Empty list explicitly passed → fall through to "all rows"
        # (slugs=None means no slug filter in the SQL).
        slugs = None

    if dry_run:
        count = await pool.fetchval(_COUNT_DESCRIPTIONS_CANDIDATES, slugs)
        log.info(
            "backfill.descriptions.dry_run",
            candidates=int(count or 0),
            slugs=slugs,
        )
        return int(count or 0)

    r = get_redis()
    board_cache: dict[str, bool] = {}  # board_id -> needs_browser

    async def _enqueue_rows(rows: list) -> int:
        """Enqueue a batch of rows; return the count actually added to Redis."""
        added_count = 0
        now = time.time()
        for row in rows:
            posting_id = row["id"]
            url = row["source_url"]
            board_id = row["board_id"] or ""
            r2_hash = row["description_r2_hash"]
            domain = urlparse(url).hostname or ""

            if board_id and board_id not in board_cache:
                board_config = await r.hgetall(f"board:{board_id}")
                board_cache[board_id] = (
                    board_config.get("scraper_needs_browser", "0") == "1" if board_config else False
                )
            needs_browser = board_cache.get(board_id, False)

            added = await enqueue_scrape(
                domain,
                posting_id,
                now,
                {
                    "source_url": url,
                    "board_id": board_id,
                    "description_r2_hash": str(r2_hash) if r2_hash is not None else "",
                    "scrape_step": "0",
                },
                browser=needs_browser,
                first_time=False,  # tier 2 = lowest priority
            )
            if added:
                added_count += 1
        return added_count

    enqueued = 0

    # Pass 1 (PROMOTE): rows with ``next_scrape_at IS NULL``.
    # Each iteration UPDATEs up to N rows and the WHERE clause excludes
    # already-promoted ids on the next pass (the row's next_scrape_at
    # is no longer NULL). Loop until empty.
    while True:
        rows = await pool.fetch(_PROMOTE_DESCRIPTIONS_BATCH, slugs, _BACKFILL_BATCH_SIZE)
        if not rows:
            break
        log.info("backfill.descriptions.promote.batch", count=len(rows), slugs=slugs)
        enqueued += await _enqueue_rows(list(rows))

    # Pass 2 (FETCH-ONLY): rows with ``next_scrape_at IS NOT NULL`` —
    # already promoted by an earlier ad-hoc operator UPDATE or a prior
    # invocation whose Redis enqueue was lost. The criteria stay true
    # across iterations (we're not flipping next_scrape_at this time
    # — the row already has a non-NULL value), so OFFSET pagination
    # is required to walk the table. Concurrent-write race: if
    # ``_DIFF_BATCH``'s touched-self-heal flips a row's
    # next_scrape_at from NULL to non-NULL during this loop on an
    # ``id`` sorting before ``offset``, the row appears behind us and
    # we'd re-enqueue it on the next iteration. ``enqueue_scrape``'s
    # Lua ZADD-NX dedup absorbs the duplicate (returns added=0, not
    # counted), so the operational effect is just a slightly inflated
    # log count. Acceptable; documented here so a future maintainer
    # doesn't try to "fix" the count drift by adding row-level locks.
    offset = 0
    while True:
        rows = await pool.fetch(
            _FETCH_STUCK_DESCRIPTIONS_BATCH, slugs, _BACKFILL_BATCH_SIZE, offset
        )
        if not rows:
            break
        log.info(
            "backfill.descriptions.fetch.batch",
            count=len(rows),
            offset=offset,
            slugs=slugs,
        )
        enqueued += await _enqueue_rows(list(rows))
        offset += len(rows)

    if enqueued == 0:
        log.info("backfill.descriptions.none_needed", slugs=slugs)
    else:
        log.info("backfill.descriptions.enqueued", enqueued=enqueued, slugs=slugs)
    return enqueued


# ── reprocess_experience (#3289) ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExperienceReprocessSummary:
    """Operator-facing counts for ``crawler reprocess-experience``."""

    scanned_postings: int
    changed_postings: int
    updated_postings: int


_FETCH_EXPERIENCE_REPROCESS_CANDIDATES = r"""
WITH candidates AS (
    SELECT
        jp.id,
        jp.experience_min::float8 AS experience_min,
        jp.experience_max::float8 AS experience_max
    FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    WHERE jp.is_active = true
      AND ($1::uuid IS NULL OR jp.id > $1::uuid)
      AND ($2::text[] IS NULL OR c.slug = ANY($2::text[]))
      AND (
        $3::boolean = false
        OR jp.experience_min IS NULL
        OR jp.experience_min = 5
      )
      AND EXISTS (
        SELECT 1
        FROM descriptions d
        WHERE d.posting_id = jp.id
          AND d.html IS NOT NULL
          AND length(trim(d.html)) > 0
      )
    ORDER BY jp.id
    LIMIT $4
)
SELECT
    candidate.id::text AS id,
    candidate.experience_min,
    candidate.experience_max,
    array_agg(
        d.html
        ORDER BY CASE WHEN d.locale = 'en' THEN 0 ELSE 1 END,
                 d.updated_at DESC NULLS LAST
    ) AS descriptions
FROM candidates candidate
JOIN descriptions d ON d.posting_id = candidate.id
WHERE d.html IS NOT NULL
  AND length(trim(d.html)) > 0
GROUP BY candidate.id, candidate.experience_min, candidate.experience_max
ORDER BY candidate.id
"""


_UPDATE_EXPERIENCE_REPROCESS_BATCH = """
UPDATE job_posting AS jp
SET
    experience_min = u.experience_min,
    experience_max = u.experience_max,
    updated_at = now()
FROM unnest($1::uuid[], $2::numeric[], $3::numeric[]) AS u(id, experience_min, experience_max)
WHERE jp.id = u.id
  AND (
    jp.experience_min IS DISTINCT FROM u.experience_min
    OR jp.experience_max IS DISTINCT FROM u.experience_max
  )
"""


_SUSPECT_EXPERIENCE_REPROCESS_RE = re.compile(
    r"\d{1,2}\s*(?:months?|Monate?|mois|mesi|meses|maanden|månader|måneder)"
    r"|"
    r"\d{1,2}[\.,]\d\s*[+＋]?\s*(?:years?|Jahre?|ans?|anni?|años?|jaar|år)",
    re.IGNORECASE,
)


def _year_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.1"))


def _stored_year(value: object | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(float(value))).quantize(Decimal("0.1"))


def _best_experience(descriptions: list[str]) -> ExperienceRequirement | None:
    best: ExperienceRequirement | None = None
    for html in descriptions:
        result = extract_experience(html)
        if result is None:
            continue
        if best is None or result.min_years > best.min_years:
            best = result
    return best


def _experience_changed(row: asyncpg.Record, extracted: ExperienceRequirement) -> bool:
    return _stored_year(row["experience_min"]) != _year_decimal(
        extracted.min_years
    ) or _stored_year(row["experience_max"]) != _year_decimal(extracted.max_years)


def _parse_update_count(status: str) -> int:
    try:
        return int(status.rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        return 0


async def reprocess_experience(
    pool: asyncpg.Pool,
    *,
    company_slugs: list[str] | None = None,
    only_suspect: bool = True,
    dry_run: bool = False,
    batch_size: int = _EXPERIENCE_REPROCESS_BATCH_SIZE,
    limit: int | None = None,
) -> ExperienceReprocessSummary:
    """Recompute experience fields from stored descriptions.

    The default scope is intentionally narrow for #3289 cleanup:
    active postings whose stored descriptions mention months or decimal
    years and whose current ``experience_min`` is either NULL (missed
    months) or 5 (old decimal-year regex starting after the decimal
    point). The helper only updates rows where the extractor now finds a
    concrete value, so descriptions with internship durations such as
    "3 months internship" stay untouched.

    ``limit`` caps the number of changed postings considered, which is
    useful for staged production runs. ``dry_run`` still performs the
    extractor pass so the reported count is the number of rows that
    would actually change, not just the SQL candidate count.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive when provided")

    slugs = list(company_slugs) if company_slugs else None
    last_id: str | None = None
    scanned = 0
    changed = 0
    updated = 0

    while True:
        rows = await pool.fetch(
            _FETCH_EXPERIENCE_REPROCESS_CANDIDATES,
            last_id,
            slugs,
            only_suspect,
            batch_size,
        )
        if not rows:
            break

        last_id = rows[-1]["id"]
        updates: list[tuple[str, Decimal, Decimal | None]] = []
        for row in rows:
            scanned += 1
            descriptions = [html for html in row["descriptions"] if html]
            if only_suspect:
                descriptions = [
                    html for html in descriptions if _SUSPECT_EXPERIENCE_REPROCESS_RE.search(html)
                ]
            extracted = _best_experience(descriptions)
            if extracted is None or not _experience_changed(row, extracted):
                continue
            min_years = _year_decimal(extracted.min_years)
            if min_years is None:
                continue
            updates.append(
                (
                    row["id"],
                    min_years,
                    _year_decimal(extracted.max_years),
                )
            )

        if limit is not None:
            remaining = limit - changed
            if remaining <= 0:
                break
            updates = updates[:remaining]

        changed += len(updates)

        if updates and not dry_run:
            status = await pool.execute(
                _UPDATE_EXPERIENCE_REPROCESS_BATCH,
                [UUID(row_id) for row_id, _, _ in updates],
                [min_years for _, min_years, _ in updates],
                [max_years for _, _, max_years in updates],
            )
            updated += _parse_update_count(status)

        log.info(
            "backfill.experience.batch",
            scanned=scanned,
            changed=changed,
            updated=updated,
            dry_run=dry_run,
            last_id=last_id,
            only_suspect=only_suspect,
            slugs=slugs,
        )

        if limit is not None and changed >= limit:
            break

    summary = ExperienceReprocessSummary(
        scanned_postings=scanned,
        changed_postings=changed,
        updated_postings=updated,
    )
    log.info(
        "backfill.experience.done",
        scanned_postings=summary.scanned_postings,
        changed_postings=summary.changed_postings,
        updated_postings=summary.updated_postings,
        dry_run=dry_run,
        only_suspect=only_suspect,
        slugs=slugs,
    )
    return summary
