"""PostgreSQL integration proof for the commit-safe posting CDC boundary."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from src.export_cursor_fence import capture_cdc_snapshot_cutoff
from src.reconciliation import reconcile_partition

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


async def _changed_rows(
    connection: asyncpg.Connection,
    cursor: tuple[datetime, uuid.UUID],
    cutoff: datetime,
) -> list[asyncpg.Record]:
    return await connection.fetch(
        "SELECT id, updated_at FROM job_posting "
        "WHERE (updated_at, id) > ($1, $2) AND updated_at < $3 "
        "ORDER BY updated_at, id",
        cursor[0],
        cursor[1],
        cutoff,
    )


async def test_uncommitted_old_stamp_cannot_fall_behind_export_cursor() -> None:
    """An open writer lowers the cutoff while older committed work progresses."""

    dsn = os.environ["LOCAL_DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    control = await asyncpg.connect(dsn)
    writer_a = await asyncpg.connect(dsn)
    writer_b = await asyncpg.connect(dsn)
    writer_c = await asyncpg.connect(dsn)
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    posting_ids = [uuid.uuid4() for _ in range(4)]

    try:
        await control.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            board_id,
            company_id,
            f"cdc-e2e-{board_id}",
            f"https://cdc-e2e.invalid/{board_id}",
        )
        for posting_id in posting_ids:
            await control.execute(
                "INSERT INTO job_posting (id, company_id, board_id, source_url) "
                "VALUES ($1, $2, $3, $4)",
                posting_id,
                company_id,
                board_id,
                f"https://cdc-e2e.invalid/posting/{posting_id}",
            )

        initial = await control.fetch(
            "SELECT id, updated_at FROM job_posting WHERE board_id = $1 "
            "ORDER BY updated_at DESC, id DESC LIMIT 1",
            board_id,
        )
        cursor = (initial[0]["updated_at"], initial[0]["id"])

        # A committed change older than the open writer remains exportable.
        await control.execute(
            "UPDATE job_posting SET is_active = false, updated_at = now() WHERE id = $1",
            posting_ids[0],
        )

        # Writer A explicitly supplies a stale transaction-era timestamp and
        # stays uncommitted. The trigger must replace it after acquiring the
        # shared transaction lock.
        await writer_a.execute("BEGIN")
        await writer_a.execute(
            "UPDATE job_posting SET is_active = false, updated_at = $2 WHERE id = $1",
            posting_ids[1],
            datetime(2000, 1, 1, tzinfo=UTC),
        )
        stamped_a = await writer_a.fetchval(
            "SELECT updated_at FROM job_posting WHERE id = $1", posting_ids[1]
        )
        assert stamped_a.year != 2000

        # A later writer may commit while A is still invisible.
        await writer_b.execute(
            "UPDATE job_posting SET is_active = false, updated_at = now() WHERE id = $1",
            posting_ids[2],
        )

        # The cutoff query returns the open writer's transaction-start floor
        # without waiting for a zero-writer gap. A remains open throughout.
        first_cutoff = await asyncio.wait_for(capture_cdc_snapshot_cutoff(pool), timeout=1)
        assert writer_a.is_in_transaction()
        assert first_cutoff <= stamped_a

        # A writer that starts after the acquired boundary stamps above the
        # cutoff and is intentionally deferred to the next idempotent batch.
        await writer_c.execute(
            "UPDATE job_posting SET is_active = false, updated_at = now() WHERE id = $1",
            posting_ids[3],
        )

        first_batch = await _changed_rows(control, cursor, first_cutoff)
        assert [row["id"] for row in first_batch] == [posting_ids[0]]
        first_cursor = (first_batch[-1]["updated_at"], first_batch[-1]["id"])

        await writer_a.execute("COMMIT")
        second_cutoff = await capture_cdc_snapshot_cutoff(pool)
        second_batch = await _changed_rows(control, first_cursor, second_cutoff)
        assert {row["id"] for row in second_batch} == set(posting_ids[1:])
        second_cursor = (second_batch[-1]["updated_at"], second_batch[-1]["id"])

        final_cutoff = await capture_cdc_snapshot_cutoff(pool)
        assert await _changed_rows(control, second_cursor, final_cutoff) == []
    finally:
        if writer_a.is_in_transaction():
            await writer_a.execute("ROLLBACK")
        await control.execute("DELETE FROM job_posting WHERE board_id = $1", board_id)
        await control.execute("DELETE FROM job_board WHERE id = $1", board_id)
        await writer_a.close()
        await writer_b.close()
        await writer_c.close()
        await control.close()
        await pool.close()


async def test_bidirectional_supabase_drift_is_repaired_from_locked_local_truth() -> None:
    """The real COPY/upsert path repairs both directions and verifies state."""

    dsn = os.environ["LOCAL_DATABASE_URL"]
    local_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    control = await asyncpg.connect(dsn)
    schema = f"reconciliation_e2e_{uuid.uuid4().hex}"
    remote_pool: asyncpg.Pool | None = None
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    prefix = 0xAB
    posting_ids = [uuid.UUID(hex=f"{prefix:02x}{suffix:030x}") for suffix in range(1, 6)]
    shared, mismatch, missing, remote_active, remote_inactive = posting_ids

    try:
        await control.execute(f'CREATE SCHEMA "{schema}"')
        await control.execute(
            f'CREATE TABLE "{schema}".job_posting (LIKE public.job_posting INCLUDING ALL)'
        )
        remote_pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=2,
            server_settings={"search_path": f"{schema},public"},
        )
        await control.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            board_id,
            company_id,
            f"reconciliation-e2e-{board_id}",
            f"https://reconciliation-e2e.invalid/{board_id}",
        )
        for posting_id in posting_ids:
            await control.execute(
                "INSERT INTO job_posting (id, company_id, board_id, source_url) "
                "VALUES ($1, $2, $3, $4)",
                posting_id,
                company_id,
                board_id,
                f"https://reconciliation-e2e.invalid/posting/{posting_id}",
            )

        await control.execute(
            "UPDATE job_posting SET is_active = false WHERE id = $1",
            mismatch,
        )
        await control.execute(
            f'INSERT INTO "{schema}".job_posting '
            "SELECT * FROM public.job_posting WHERE id = ANY($1::uuid[])",
            [shared, mismatch, remote_active, remote_inactive],
        )
        await control.execute(
            f'UPDATE "{schema}".job_posting SET is_active = true WHERE id = $1',
            mismatch,
        )
        await control.execute(
            f'UPDATE "{schema}".job_posting SET is_active = false WHERE id = $1',
            remote_inactive,
        )
        await control.execute(
            "DELETE FROM public.job_posting WHERE id = ANY($1::uuid[])",
            [remote_active, remote_inactive],
        )

        result = await reconcile_partition(
            local_pool,
            remote_pool,
            target="supabase",
            partition=prefix,
            repair=True,
        )

        assert result.detected == 3
        assert result.repaired == 3
        assert result.unresolved == 0
        rows = await remote_pool.fetch("SELECT id, is_active FROM job_posting ORDER BY id")
        assert {row["id"]: row["is_active"] for row in rows} == {
            shared: True,
            mismatch: False,
            missing: True,
            remote_active: False,
            remote_inactive: False,
        }
    finally:
        if remote_pool is not None:
            await remote_pool.close()
        await control.execute("DELETE FROM public.job_posting WHERE board_id = $1", board_id)
        await control.execute("DELETE FROM public.job_board WHERE id = $1", board_id)
        await control.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await control.close()
        await local_pool.close()
