"""PostgreSQL plan guard for the production-scale annotation sample shape."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.labeller.sampling import SAMPLE_CANDIDATES_SQL

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)

EXPECTED_INDEX = "idx_jp_active_first_seen"
FIXTURE_ROWS = 100_000


def _plan_nodes(plan: dict) -> list[dict]:
    nodes = [plan]
    for child in plan.get("Plans", []):
        nodes.extend(_plan_nodes(child))
    return nodes


async def test_recent_active_sampling_is_bounded_and_index_driven() -> None:
    """A large old active population must not be scanned or sorted."""

    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    end = datetime(2026, 7, 23, tzinfo=UTC)
    start = end - timedelta(hours=24)

    try:
        await connection.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            board_id,
            company_id,
            f"labeller-plan-{board_id}",
            f"https://labeller-plan.invalid/{board_id}",
        )
        await connection.execute(
            """
            INSERT INTO job_posting (
                id, company_id, board_id, source_url, first_seen_at, is_active
            )
            SELECT gen_random_uuid(),
                   $1,
                   $2,
                   'https://labeller-plan.invalid/posting/' || n,
                   $3 - interval '30 days' - n * interval '1 second',
                   true
            FROM generate_series(1, $4) AS n
            """,
            company_id,
            board_id,
            start,
            FIXTURE_ROWS,
        )
        await connection.executemany(
            """
            INSERT INTO job_posting (
                id, company_id, board_id, source_url, first_seen_at, is_active
            ) VALUES ($1, $2, $3, $4, $5, true)
            """,
            [
                (
                    uuid.uuid4(),
                    company_id,
                    board_id,
                    f"https://labeller-plan.invalid/recent/{offset}",
                    start + timedelta(minutes=offset),
                )
                for offset in range(10)
            ],
        )
        await connection.execute("ANALYZE job_posting")

        explained = await connection.fetchval(
            f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {SAMPLE_CANDIDATES_SQL}",
            start,
            end,
        )
        root = explained[0]["Plan"]
        nodes = _plan_nodes(root)

        assert EXPECTED_INDEX in {
            node.get("Index Name") for node in nodes if node.get("Index Name")
        }
        assert not {"Seq Scan", "Sort", "Gather Merge"}.intersection(
            node["Node Type"] for node in nodes
        )
        assert root["Actual Rows"] == 10
        assert sum(node.get("Actual Rows", 0) for node in nodes) <= 20
        assert root["Actual Total Time"] < 1_000
    finally:
        await connection.execute("DELETE FROM job_posting WHERE board_id = $1", board_id)
        await connection.execute("DELETE FROM job_board WHERE id = $1", board_id)
        await connection.close()
