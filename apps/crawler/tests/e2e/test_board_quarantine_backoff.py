"""PostgreSQL integration proof for capped board-failure backoff."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import asyncpg
import pytest

from src.queries.monitor import _RECORD_FAILURE

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


@pytest.mark.parametrize(
    ("consecutive_failures", "expected_stored_failures", "expected_delay"),
    [
        (0, 1, timedelta(minutes=5)),
        (8, 9, timedelta(minutes=1280)),
        (9, 10, timedelta(days=1)),
        (38, 39, timedelta(days=1)),
        (2_147_483_646, 2_147_483_647, timedelta(days=1)),
        (2_147_483_647, 2_147_483_647, timedelta(days=1)),
    ],
)
async def test_record_failure_caps_backoff_before_building_interval(
    consecutive_failures: int,
    expected_stored_failures: int,
    expected_delay: timedelta,
) -> None:
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    board_id = uuid.uuid4()

    try:
        await connection.execute(
            "INSERT INTO job_board "
            "(id, company_id, board_slug, board_url, consecutive_failures) "
            "VALUES ($1, $2, $3, $4, $5)",
            board_id,
            uuid.uuid4(),
            f"quarantine-backoff-e2e-{board_id}",
            f"https://quarantine-backoff-e2e.invalid/{board_id}",
            consecutive_failures,
        )

        await connection.fetchrow(_RECORD_FAILURE, board_id, "test failure")
        row = await connection.fetchrow(
            "SELECT consecutive_failures, next_check_at - updated_at AS delay "
            "FROM job_board WHERE id = $1",
            board_id,
        )

        assert row is not None
        assert row["consecutive_failures"] == expected_stored_failures
        assert row["delay"] == expected_delay
    finally:
        await connection.execute("DELETE FROM job_board WHERE id = $1", board_id)
        await connection.close()
