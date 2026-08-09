"""Bounded repair for active postings whose owning board is terminal.

The crawler's source of truth is local PostgreSQL.  Posting updates made here
advance ``updated_at`` so the normal CDC exporter publishes every transition
to Typesense (and any other configured mirror).  The repair deliberately does
not touch recoverable ``quarantined`` or ``gone_pending`` boards: those must
first prove liveness through a provider-native monitor run.
"""

from __future__ import annotations

import csv
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from src.shared.constants import DATA_DIR

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger()

DEFAULT_CHUNK_SIZE = 1_000
DEFAULT_MAX_CHUNKS = 100
_LOCK_KEY = 6158

_FETCH_TERMINAL_BOARDS = """
SELECT
    jb.id::text AS board_id,
    jb.board_slug,
    jb.board_url,
    jb.board_status,
    jb.is_enabled,
    jb.gone_confirmation_count,
    jb.gone_at,
    count(jp.id) FILTER (WHERE jp.is_active = true)::bigint AS active_postings
FROM job_board jb
LEFT JOIN job_posting jp ON jp.board_id = jb.id
WHERE jb.board_status IN ('disabled', 'gone')
GROUP BY jb.id
ORDER BY jb.board_slug, jb.id
"""

_COUNT_ELIGIBLE = """
SELECT count(*)::bigint
FROM job_posting jp
JOIN job_board jb ON jb.id = jp.board_id
WHERE jp.is_active = true
  AND jb.id = ANY($1::uuid[])
  AND (
      (jb.board_status = 'disabled' AND jb.is_enabled = false)
      OR (
          jb.board_status = 'gone'
          AND jb.is_enabled = true
          AND jb.gone_at IS NOT NULL
          AND jb.gone_confirmation_count >= 2
      )
  )
"""

_SWEEP_CHUNK = """
WITH candidates AS MATERIALIZED (
    SELECT jp.id
    FROM job_posting jp
    JOIN job_board jb ON jb.id = jp.board_id
    WHERE jp.is_active = true
      AND jb.id = ANY($1::uuid[])
      AND (
          (jb.board_status = 'disabled' AND jb.is_enabled = false)
          OR (
              jb.board_status = 'gone'
              AND jb.is_enabled = true
              AND jb.gone_at IS NOT NULL
              AND jb.gone_confirmation_count >= 2
          )
      )
    ORDER BY jp.id
    LIMIT $2
    FOR UPDATE OF jp SKIP LOCKED
)
UPDATE job_posting jp
SET is_active = false,
    next_scrape_at = NULL,
    updated_at = clock_timestamp()
FROM candidates
WHERE jp.id = candidates.id
RETURNING jp.id::text AS posting_id, jp.board_id::text AS board_id
"""

_TRY_LOCK = "SELECT pg_try_advisory_lock($1)"
_UNLOCK = "SELECT pg_advisory_unlock($1)"


class PhantomSweepSafetyError(RuntimeError):
    """Raised when configured board state makes a mutation unsafe."""


class PhantomSweepAlreadyRunning(RuntimeError):
    """Raised when another repair process owns the session lock."""


@dataclass(frozen=True)
class BoardClassification:
    board_id: str
    board_slug: str
    board_url: str
    reason: str
    active_postings: int


@dataclass(frozen=True)
class SweepSummary:
    dry_run: bool
    eligible_boards: int
    configured_disabled_boards: int
    candidate_postings: int
    updated_postings: int
    remaining_postings: int
    chunks_committed: int

    @property
    def complete(self) -> bool:
        return self.remaining_postings == 0


def load_configured_board_urls(path: Path | None = None) -> frozenset[str]:
    """Return the exact board URLs owned by the deployed CSV contract."""

    csv_path = path or DATA_DIR / "boards.csv"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "board_url" not in reader.fieldnames:
            raise PhantomSweepSafetyError(f"missing board_url column in {csv_path}")
        urls = {row["board_url"].strip() for row in reader if row.get("board_url", "").strip()}
    if not urls:
        raise PhantomSweepSafetyError(f"no configured board URLs found in {csv_path}")
    return frozenset(urls)


def classify_terminal_boards(
    rows: Sequence[Any],
    configured_urls: frozenset[str],
) -> tuple[list[BoardClassification], list[BoardClassification]]:
    """Split terminal rows into safe sweep candidates and recovery blockers."""

    eligible: list[BoardClassification] = []
    blocked: list[BoardClassification] = []
    for row in rows:
        status = str(row["board_status"])
        enabled = bool(row["is_enabled"])
        url = str(row["board_url"])
        active = int(row["active_postings"] or 0)

        if status == "disabled":
            if url in configured_urls:
                blocked.append(
                    BoardClassification(
                        board_id=str(row["board_id"]),
                        board_slug=str(row["board_slug"]),
                        board_url=url,
                        reason="configured_disabled_requires_recovery",
                        active_postings=active,
                    )
                )
            elif not enabled:
                eligible.append(
                    BoardClassification(
                        board_id=str(row["board_id"]),
                        board_slug=str(row["board_slug"]),
                        board_url=url,
                        reason="removed_from_configuration",
                        active_postings=active,
                    )
                )
            continue

        if (
            status == "gone"
            and enabled
            and row["gone_at"] is not None
            and int(row["gone_confirmation_count"] or 0) >= 2
        ):
            eligible.append(
                BoardClassification(
                    board_id=str(row["board_id"]),
                    board_slug=str(row["board_slug"]),
                    board_url=url,
                    reason="provider_gone_confirmed",
                    active_postings=active,
                )
            )

    return eligible, blocked


async def sweep_phantom_postings(
    pool: Any,
    *,
    dry_run: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    configured_urls: frozenset[str] | None = None,
) -> SweepSummary:
    """Classify terminal boards, then commit at most ``max_chunks`` repairs.

    A session advisory lock serializes repair invocations while per-chunk
    transactions and ``SKIP LOCKED`` coexist with crawler workers.  Rerunning
    is the resume mechanism: only still-active rows qualify.
    """

    if chunk_size < 1 or chunk_size > 10_000:
        raise ValueError("chunk_size must be between 1 and 10000")
    if max_chunks < 1 or max_chunks > 10_000:
        raise ValueError("max_chunks must be between 1 and 10000")

    csv_urls = configured_urls if configured_urls is not None else load_configured_board_urls()

    async with pool.acquire() as conn:
        terminal_rows = await conn.fetch(_FETCH_TERMINAL_BOARDS)
        eligible, blocked = classify_terminal_boards(terminal_rows, csv_urls)
        configured_blockers = [row for row in blocked if row.active_postings > 0]
        if configured_blockers:
            details = ", ".join(
                f"{row.board_slug}({row.active_postings})" for row in configured_blockers[:20]
            )
            raise PhantomSweepSafetyError(
                "configured disabled boards still own active postings; "
                f"recover/classify them before sweeping: {details}"
            )

        board_ids = [uuid.UUID(row.board_id) for row in eligible]
        candidate_count = int(await conn.fetchval(_COUNT_ELIGIBLE, board_ids)) if board_ids else 0
        if dry_run or candidate_count == 0:
            return SweepSummary(
                dry_run=dry_run,
                eligible_boards=len(eligible),
                configured_disabled_boards=len(blocked),
                candidate_postings=candidate_count,
                updated_postings=0,
                remaining_postings=candidate_count,
                chunks_committed=0,
            )

        if not await conn.fetchval(_TRY_LOCK, _LOCK_KEY):
            raise PhantomSweepAlreadyRunning("another phantom-posting sweep owns the lock")

        updated = 0
        chunks = 0
        try:
            for _ in range(max_chunks):
                async with conn.transaction():
                    rows = await conn.fetch(_SWEEP_CHUNK, board_ids, chunk_size)
                if not rows:
                    break
                chunks += 1
                updated += len(rows)
                log.info(
                    "phantom_sweep.chunk_committed",
                    chunk=chunks,
                    updated=len(rows),
                    updated_total=updated,
                )
                if len(rows) < chunk_size:
                    break

            remaining = int(await conn.fetchval(_COUNT_ELIGIBLE, board_ids))
        finally:
            await conn.fetchval(_UNLOCK, _LOCK_KEY)

    return SweepSummary(
        dry_run=False,
        eligible_boards=len(eligible),
        configured_disabled_boards=len(blocked),
        candidate_postings=candidate_count,
        updated_postings=updated,
        remaining_postings=remaining,
        chunks_committed=chunks,
    )


async def refresh_derived_surfaces(pool: Any) -> None:
    """Invalidate local caches and recompute Typesense count documents.

    Posting documents themselves stay on the normal ordered CDC path; this
    refresh handles the derived company/taxonomy counts that are not CDC
    documents.  Failures are intentionally surfaced so an operator can rerun
    the idempotent command and finish the post-repair gate.
    """

    from src.redis_queue import get_redis
    from src.sync import refresh_typesense_counts
    from src.typesense_client import get_typesense_client

    await get_redis().delete("cache:platform-stats")
    client = get_typesense_client()
    if client is None:
        raise RuntimeError("Typesense is not configured; derived counts were not refreshed")
    async with pool.acquire() as conn:
        await refresh_typesense_counts(cast("asyncpg.Connection", conn), client)
