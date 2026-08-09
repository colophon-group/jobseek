from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import asyncpg

from src.labeller.sampling import SAMPLE_CANDIDATES_SQL, sample_postings


class _Pool:
    def __init__(self, rows: list[dict[str, str]]):
        self.rows = rows
        self.calls: list[tuple[str, tuple[datetime, datetime]]] = []

    async def fetch(
        self,
        query: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, str]]:
        self.calls.append((query, (start, end)))
        return self.rows


async def test_sample_is_seeded_diverse_and_uses_a_complete_database_order() -> None:
    rows = [
        {"posting_id": "a-2", "company_id": "a", "source_url": "https://example/a-2"},
        {"posting_id": "a-1", "company_id": "a", "source_url": "https://example/a-1"},
        {"posting_id": "b-1", "company_id": "b", "source_url": "https://example/b-1"},
        {"posting_id": "c-1", "company_id": "c", "source_url": "https://example/c-1"},
    ]
    end = datetime(2026, 7, 23, tzinfo=UTC)
    first_pool = _Pool(rows)
    second_pool = _Pool(rows)

    first = await sample_postings(
        cast(asyncpg.Pool, first_pool),
        end_time_utc=end,
        count=3,
        seed=5929,
    )
    second = await sample_postings(
        cast(asyncpg.Pool, second_pool),
        end_time_utc=end,
        count=3,
        seed=5929,
    )

    assert first == second
    assert {sample.company_id for sample in first} == {"a", "b", "c"}
    assert "ORDER BY p.first_seen_at DESC, p.id" in SAMPLE_CANDIDATES_SQL
    assert first_pool.calls == second_pool.calls
