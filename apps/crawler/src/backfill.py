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

import time
from urllib.parse import urlparse

import asyncpg
import structlog

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

# Dry-run COUNT — same WHERE clause as the UPDATE above, no writes.
_COUNT_DESCRIPTIONS_CANDIDATES = """
SELECT COUNT(*)::int FROM job_posting jp
JOIN company c ON c.id = jp.company_id
WHERE jp.is_active = true
  AND jp.description_r2_hash IS NULL
  AND jp.next_scrape_at IS NULL
  AND ($1::text[] IS NULL OR c.slug = ANY($1::text[]))
"""


async def backfill_descriptions(
    pool: asyncpg.Pool,
    company_slugs: list[str] | None = None,
    only_missing: bool = True,
    dry_run: bool = False,
) -> int:
    """Set ``next_scrape_at = now()`` on rich-monitor postings whose
    description is missing.

    Scoped by ``company_slugs`` if provided; otherwise the default 20
    stuck companies from #2996. Returns the number of rows touched
    (or, in ``dry_run`` mode, the number that WOULD be touched).

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

    # Single pass: each iteration UPDATEs up to N rows and the WHERE
    # clause excludes already-promoted ids on the next pass (the row's
    # next_scrape_at is no longer NULL). Loop until empty.
    while True:
        rows = await pool.fetch(_PROMOTE_DESCRIPTIONS_BATCH, slugs, _BACKFILL_BATCH_SIZE)
        if not rows:
            break
        log.info("backfill.descriptions.promote.batch", count=len(rows), slugs=slugs)
        enqueued += await _enqueue_rows(list(rows))

    if enqueued == 0:
        log.info("backfill.descriptions.none_needed", slugs=slugs)
    else:
        log.info("backfill.descriptions.enqueued", enqueued=enqueued, slugs=slugs)
    return enqueued
