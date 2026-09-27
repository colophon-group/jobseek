"""Durable, monotonic mining restrictions. These never change listing visibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

from src.shared.tdm import TDMReservedError


async def record_reservation(
    conn: asyncpg.Connection | PoolConnectionProxy,
    *,
    board_id: str | UUID | None = None,
    posting_id: str | UUID | None = None,
    error: TDMReservedError,
) -> None:
    if (board_id is None) == (posting_id is None):
        raise ValueError("Exactly one TDM target is required")
    # Table names are fixed here, never supplied by source content.
    table, target = ("job_board", board_id) if board_id is not None else ("job_posting", posting_id)
    evidence = json.dumps(
        {
            "url": error.url,
            "source": error.source,
            "policy_url": error.policy_url,
            "observed_at": datetime.now(UTC).isoformat(),
        }
    )
    # Board trigger marks retained postings, including concurrent/new inserts.
    # Posting timestamps feed both Python and Go CDC exporters. No expiry or
    # missing-header response automatically grants permission again.
    await conn.execute(
        f"UPDATE {table} SET tdm_reserved = true, tdm_reservation = $2::jsonb"
        + (", updated_at = clock_timestamp()" if posting_id is not None else "")
        + " WHERE id = $1",
        target,
        evidence,
    )


async def posting_reserved(pool: asyncpg.Pool, posting_id: str) -> bool:
    return (
        await pool.fetchval(
            "SELECT tdm_reserved FROM job_posting WHERE id = $1",
            posting_id,
        )
    ) is True
