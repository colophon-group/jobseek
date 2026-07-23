"""Repair relisted posting activity that never crossed the CDC cursor.

Before #6016's follow-up, the monitor ``relisted`` CTE changed
``job_posting.is_active`` from false to true without advancing ``updated_at``.
The exporter uses ``(updated_at, id)`` keyset cursors, so Supabase and
Typesense could remain inactive forever even though local PostgreSQL had
recovered the posting.

The runtime SQL now advances ``updated_at`` on every actual relist.  This
operator command repairs rows relisted before that fix by comparing a bounded
local candidate window with both downstream activity states and touching only
confirmed mismatches.  Touching routes the repair through the normal dual
exporter instead of duplicating its write/error/cursor logic.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

import asyncpg
import structlog

from src.export_cursor_fence import CursorFenceFactory, export_cursor_fence
from src.typesense_client import get_typesense_client

log = structlog.get_logger()

_ZERO_UUID = uuid.UUID(int=0)
_DEFAULT_BATCH_SIZE = 2000

_FETCH_CANDIDATES = """
SELECT id
FROM job_posting
WHERE id > $1::uuid
  AND is_active = true
  AND last_seen_at >= $2::timestamptz
ORDER BY id
LIMIT $3
"""

_FETCH_SUPABASE_ACTIVE = """
SELECT id
FROM job_posting
WHERE id = ANY($1::uuid[])
  AND is_active = true
"""

_TOUCH_MISMATCHES = """
UPDATE job_posting
SET updated_at = now()
WHERE id = ANY($1::uuid[])
  AND is_active = true
RETURNING id
"""


@dataclass(frozen=True)
class RepairResult:
    scanned: int
    mismatched: int
    supabase_mismatched: int
    typesense_mismatched: int
    touched: int


def parse_aware_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and require an explicit timezone."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--since must include a timezone (for example +00:00 or Z)")
    return parsed


async def _load_typesense_active_ids(client: object) -> set[str]:
    """Export only active document IDs from Typesense into a membership set."""

    def _export() -> str:
        return client.collections["job_posting"].documents.export(  # type: ignore[attr-defined]
            {
                "filter_by": "is_active:=true",
                "include_fields": "id",
            }
        )

    raw = await asyncio.to_thread(_export)
    active_ids: set[str] = set()
    for line in raw.splitlines():
        if not line:
            continue
        document = json.loads(line)
        posting_id = document.get("id")
        if isinstance(posting_id, str):
            active_ids.add(posting_id)
    return active_ids


async def repair_relisted_cdc(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    *,
    since: datetime,
    dry_run: bool = False,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    typesense_client: object | None = None,
    cursor_fence_factory: CursorFenceFactory = export_cursor_fence,
) -> RepairResult:
    """Touch locally-active rows missing from either downstream active set.

    Candidates are bounded by ``last_seen_at >= since``.  Already-touched rows
    remain eligible so an interrupted or previously unfenced repair can be
    retried until both downstreams converge.

    The database advisory fence serializes this entire snapshot/compare/write
    section with exporter fetch-and-cursor-save ticks.  That prevents a bulk
    UPDATE from choosing its ``now()`` timestamp before an exporter snapshot,
    committing after the snapshot, and being stranded behind the newly saved
    cursor.  Typesense activity is loaded only after the fence is held.  If
    that export fails or Typesense is not configured, the command fails closed
    and touches no rows.  Supabase is checked in bounded batches.  The final
    UPDATE also rechecks ``is_active=true`` against concurrent monitor changes.
    """
    if since.tzinfo is None or since.utcoffset() is None:
        raise ValueError("since must be timezone-aware")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    client = typesense_client if typesense_client is not None else get_typesense_client()
    if client is None:
        raise RuntimeError("Typesense must be configured for downstream activity repair")

    async with cursor_fence_factory(local_pool):
        log.info("repair_relisted_cdc.cursor_fence_acquired")
        typesense_active = await _load_typesense_active_ids(client)
        log.info("repair_relisted_cdc.typesense_snapshot", active=len(typesense_active))

        cursor = _ZERO_UUID
        scanned = 0
        mismatched = 0
        supabase_mismatched = 0
        typesense_mismatched = 0
        touched = 0

        while True:
            rows = await local_pool.fetch(_FETCH_CANDIDATES, cursor, since, batch_size)
            if not rows:
                break
            ids = [row["id"] for row in rows]
            cursor = ids[-1]
            scanned += len(ids)

            supa_rows = await supa_pool.fetch(_FETCH_SUPABASE_ACTIVE, ids)
            supa_active = {row["id"] for row in supa_rows}

            supa_missing = {posting_id for posting_id in ids if posting_id not in supa_active}
            typesense_missing = {
                posting_id for posting_id in ids if str(posting_id) not in typesense_active
            }
            mismatch_set = supa_missing | typesense_missing
            # Preserve the local keyset order instead of relying on UUID
            # implementations from two asyncpg connections being orderable.
            mismatch_ids = [posting_id for posting_id in ids if posting_id in mismatch_set]

            supabase_mismatched += len(supa_missing)
            typesense_mismatched += len(typesense_missing)
            mismatched += len(mismatch_ids)

            batch_touched = 0
            if mismatch_ids and not dry_run:
                touched_rows = await local_pool.fetch(_TOUCH_MISMATCHES, mismatch_ids)
                batch_touched = len(touched_rows)
                touched += batch_touched

            log.info(
                "repair_relisted_cdc.batch",
                scanned=len(ids),
                mismatched=len(mismatch_ids),
                touched=batch_touched,
                dry_run=dry_run,
            )

    result = RepairResult(
        scanned=scanned,
        mismatched=mismatched,
        supabase_mismatched=supabase_mismatched,
        typesense_mismatched=typesense_mismatched,
        touched=touched,
    )
    log.info("repair_relisted_cdc.completed", dry_run=dry_run, **result.__dict__)
    return result
