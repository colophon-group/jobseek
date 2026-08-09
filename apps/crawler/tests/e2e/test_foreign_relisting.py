"""PostgreSQL integration proof for deterministic cross-board relisting."""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from src.queries.monitor import _DIFF_BATCH

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


async def _insert_board(
    connection: asyncpg.Connection,
    *,
    board_id: uuid.UUID,
    company_id: uuid.UUID,
) -> None:
    await connection.execute(
        "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
        board_id,
        company_id,
        f"foreign-relisting-e2e-{board_id}",
        f"https://foreign-relisting-e2e.invalid/{board_id}",
    )


async def _insert_inactive_posting(
    connection: asyncpg.Connection,
    *,
    posting_id: uuid.UUID,
    company_id: uuid.UUID,
    board_id: uuid.UUID,
) -> tuple[str, object]:
    source_url = f"https://foreign-relisting-e2e.invalid/posting/{posting_id}"
    await connection.execute(
        "INSERT INTO job_posting (id, company_id, board_id, source_url) VALUES ($1, $2, $3, $4)",
        posting_id,
        company_id,
        board_id,
        source_url,
    )
    row = await connection.fetchrow(
        "UPDATE job_posting "
        "SET is_active = false, missing_count = 4, scrape_failures = 3, "
        "    next_scrape_at = NULL, updated_at = now() "
        "WHERE id = $1 "
        "RETURNING updated_at",
        posting_id,
    )
    assert row is not None
    return source_url, row["updated_at"]


@pytest.mark.parametrize("shared_company", [True, False])
async def test_foreign_rediscovery_recovers_without_transferring_owner(
    shared_company: bool,
) -> None:
    """Sibling and cross-company finders recover the stable canonical row."""
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    owner_company_id = uuid.uuid4()
    finder_company_id = owner_company_id if shared_company else uuid.uuid4()
    owner_board_id = uuid.uuid4()
    finder_board_id = uuid.uuid4()
    posting_id = uuid.uuid4()

    try:
        await _insert_board(
            connection,
            board_id=owner_board_id,
            company_id=owner_company_id,
        )
        await _insert_board(
            connection,
            board_id=finder_board_id,
            company_id=finder_company_id,
        )
        source_url, tombstone_updated_at = await _insert_inactive_posting(
            connection,
            posting_id=posting_id,
            company_id=owner_company_id,
            board_id=owner_board_id,
        )

        result = await connection.fetch(
            _DIFF_BATCH,
            [source_url],
            finder_board_id,
            False,
        )
        assert [(row["action"], row["id"]) for row in result] == [
            ("foreign_relisted", str(posting_id))
        ]

        recovered = await connection.fetchrow(
            "SELECT company_id, board_id, is_active, missing_count, "
            "       scrape_failures, next_scrape_at, last_seen_at, updated_at "
            "FROM job_posting WHERE id = $1",
            posting_id,
        )
        assert recovered is not None
        assert recovered["company_id"] == owner_company_id
        assert recovered["board_id"] == owner_board_id
        assert recovered["is_active"] is True
        assert recovered["missing_count"] == 0
        assert recovered["scrape_failures"] == 0
        assert recovered["next_scrape_at"] is not None
        assert recovered["last_seen_at"] >= tombstone_updated_at
        assert recovered["updated_at"] > tombstone_updated_at

        await connection.execute(
            "UPDATE job_posting SET missing_count = 2 WHERE id = $1",
            posting_id,
        )
        cdc_timestamp = recovered["updated_at"]
        active_result = await connection.fetch(
            _DIFF_BATCH,
            [source_url],
            finder_board_id,
            False,
        )
        assert [row["action"] for row in active_result] == ["foreign"]
        active_touch = await connection.fetchrow(
            "SELECT company_id, board_id, is_active, missing_count, updated_at "
            "FROM job_posting WHERE id = $1",
            posting_id,
        )
        assert active_touch is not None
        assert active_touch["company_id"] == owner_company_id
        assert active_touch["board_id"] == owner_board_id
        assert active_touch["is_active"] is True
        assert active_touch["missing_count"] == 0
        assert active_touch["updated_at"] == cdc_timestamp
    finally:
        await connection.execute("DELETE FROM job_posting WHERE id = $1", posting_id)
        await connection.execute(
            "DELETE FROM job_board WHERE id = ANY($1::uuid[])",
            [owner_board_id, finder_board_id],
        )
        await connection.close()


async def test_concurrent_cross_board_discovery_keeps_global_lock_order() -> None:
    """Opposing board cycles finish without deadlock and preserve both owners."""
    control = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    connection_a = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    connection_b = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    company_a = uuid.uuid4()
    company_b = uuid.uuid4()
    board_a = uuid.uuid4()
    board_b = uuid.uuid4()
    posting_a = uuid.uuid4()
    posting_b = uuid.uuid4()

    try:
        await _insert_board(control, board_id=board_a, company_id=company_a)
        await _insert_board(control, board_id=board_b, company_id=company_b)
        url_a, _ = await _insert_inactive_posting(
            control,
            posting_id=posting_a,
            company_id=company_a,
            board_id=board_a,
        )
        url_b, _ = await _insert_inactive_posting(
            control,
            posting_id=posting_b,
            company_id=company_b,
            board_id=board_b,
        )

        async def _discover(
            connection: asyncpg.Connection,
            board_id: uuid.UUID,
        ) -> list[asyncpg.Record]:
            async with connection.transaction():
                return await connection.fetch(
                    _DIFF_BATCH,
                    [url_a, url_b],
                    board_id,
                    False,
                )

        await asyncio.wait_for(
            asyncio.gather(
                _discover(connection_a, board_a),
                _discover(connection_b, board_b),
            ),
            timeout=5,
        )

        rows = await control.fetch(
            "SELECT id, company_id, board_id, is_active, missing_count, scrape_failures "
            "FROM job_posting WHERE id = ANY($1::uuid[]) ORDER BY id",
            [posting_a, posting_b],
        )
        by_id = {row["id"]: row for row in rows}
        assert by_id[posting_a]["company_id"] == company_a
        assert by_id[posting_a]["board_id"] == board_a
        assert by_id[posting_b]["company_id"] == company_b
        assert by_id[posting_b]["board_id"] == board_b
        assert all(row["is_active"] for row in rows)
        assert all(row["missing_count"] == 0 for row in rows)
        assert all(row["scrape_failures"] == 0 for row in rows)
    finally:
        await control.execute(
            "DELETE FROM job_posting WHERE id = ANY($1::uuid[])",
            [posting_a, posting_b],
        )
        await control.execute(
            "DELETE FROM job_board WHERE id = ANY($1::uuid[])",
            [board_a, board_b],
        )
        await connection_a.close()
        await connection_b.close()
        await control.close()
