"""Real PostgreSQL race and rollback proofs for the Lightpanda B0 fence."""

from __future__ import annotations

import asyncio
import importlib
import os
import uuid
from dataclasses import replace
from typing import cast

import asyncpg
import pytest

from src.lightpanda.write_fence import (
    _ACTIVATE,
    LightpandaWriteFence,
    LightpandaWriteFenceRejected,
    activate_write_fence,
    authoritative_write,
)

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


def _fence(
    posting_id: uuid.UUID,
    *,
    shard_id: str = "lightpanda-b0",
    epoch: int = 7,
    revision: int = 3,
    digest: str = "a" * 64,
    sequence: int = 31,
) -> LightpandaWriteFence:
    return LightpandaWriteFence(
        job_posting_id=posting_id,
        shard_id=shard_id,
        routing_epoch=epoch,
        engine_owner="python",
        config_revision=revision,
        payload_sha256=digest,
        claim_token=f"{epoch}:{sequence}",
    )


async def _wait_until_blocked(
    control: asyncpg.Connection,
    waiter_pid: int,
    blocker_pid: int,
) -> None:
    async with asyncio.timeout(3):
        while True:
            blockers = cast(
                list[int],
                await control.fetchval("SELECT pg_blocking_pids($1)", waiter_pid),
            )
            if blocker_pid in blockers:
                return
            await asyncio.sleep(0.005)


async def test_active_supersession_rejects_waiting_stale_writer() -> None:
    dsn = os.environ["LOCAL_DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    control = await asyncpg.connect(dsn)
    activator = await asyncpg.connect(dsn)
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    posting_id = uuid.uuid4()
    current = _fence(posting_id)
    replacement = _fence(posting_id, revision=4, digest="b" * 64, sequence=32)
    activation_transaction = activator.transaction()
    writing: asyncio.Task[None] | None = None

    try:
        await control.execute(
            "INSERT INTO company (id, name, slug) VALUES ($1, $2, $3)",
            company_id,
            "Lightpanda fence race",
            f"lightpanda-fence-race-{company_id.hex}",
        )
        await control.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            board_id,
            company_id,
            f"lightpanda-fence-race-{board_id.hex}",
            f"https://fence-race.invalid/{board_id}",
        )
        await control.execute(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            posting_id,
            company_id,
            board_id,
            f"https://fence-race.invalid/posting/{posting_id}",
        )
        await activate_write_fence(pool, current)

        await activation_transaction.start()
        await activator.execute(_ACTIVATE, *replacement.sql_args())

        async def stale_write() -> None:
            async with authoritative_write(pool, current, job_posting_id=str(posting_id)) as writer:
                await writer.execute(
                    "UPDATE job_posting SET titles = ARRAY['stale'] WHERE id = $1",
                    posting_id,
                )

        writing = asyncio.create_task(stale_write())
        writer_pid: int | None = None
        async with asyncio.timeout(3):
            while writer_pid is None:
                writer_pid = await control.fetchval(
                    "SELECT pid FROM pg_stat_activity "
                    "WHERE pid <> $1 AND query LIKE "
                    "'SELECT public.jobseek_lightpanda_b0_require_write_fence%' "
                    "ORDER BY query_start DESC LIMIT 1",
                    activator.get_server_pid(),
                )
                if writer_pid is None:
                    await asyncio.sleep(0.005)
        await _wait_until_blocked(
            control,
            writer_pid,
            activator.get_server_pid(),
        )
        await activation_transaction.commit()

        with pytest.raises(LightpandaWriteFenceRejected) as rejected:
            await asyncio.wait_for(writing, timeout=3)
        assert rejected.value.reason == "config_revision_mismatch"
        assert (
            await control.fetchval("SELECT titles FROM job_posting WHERE id = $1", posting_id) == []
        )

        async with authoritative_write(
            pool, replacement, job_posting_id=str(posting_id)
        ) as connection:
            await connection.execute(
                "UPDATE job_posting SET titles = ARRAY['current'] WHERE id = $1",
                posting_id,
            )
        assert await control.fetchval(
            "SELECT titles FROM job_posting WHERE id = $1", posting_id
        ) == ["current"]
    finally:
        if writing is not None and not writing.done():
            writing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await writing
        if activator.is_in_transaction():
            await activation_transaction.rollback()
        await control.execute("DELETE FROM job_posting WHERE id = $1", posting_id)
        await control.execute("DELETE FROM job_board WHERE id = $1", board_id)
        await control.execute("DELETE FROM company WHERE id = $1", company_id)
        await activator.close()
        await control.close()
        await pool.close()


async def test_cancellation_rolls_back_update_schedule_and_revoke_together() -> None:
    dsn = os.environ["LOCAL_DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    posting_id = uuid.uuid4()
    fence = _fence(posting_id, sequence=41)

    try:
        await pool.execute(
            "INSERT INTO company (id, name, slug) VALUES ($1, $2, $3)",
            company_id,
            "Lightpanda fence rollback",
            f"lightpanda-fence-rollback-{company_id.hex}",
        )
        await pool.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            board_id,
            company_id,
            f"lightpanda-fence-rollback-{board_id.hex}",
            f"https://fence-rollback.invalid/{board_id}",
        )
        await pool.execute(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            posting_id,
            company_id,
            board_id,
            f"https://fence-rollback.invalid/posting/{posting_id}",
        )
        await activate_write_fence(pool, fence)

        cancellation = asyncio.CancelledError("lease lost")
        with pytest.raises(asyncio.CancelledError) as cancelled:
            async with authoritative_write(
                pool, fence, job_posting_id=str(posting_id)
            ) as connection:
                await connection.execute(
                    "UPDATE job_posting "
                    "SET titles = ARRAY['rolled back'], next_scrape_at = 'infinity' "
                    "WHERE id = $1",
                    posting_id,
                )
                raise cancellation
        assert cancelled.value is cancellation

        rolled_back = await pool.fetchrow(
            "SELECT titles, next_scrape_at FROM job_posting WHERE id = $1",
            posting_id,
        )
        assert rolled_back is not None
        assert rolled_back["titles"] == []
        assert rolled_back["next_scrape_at"] is None
        assert (
            await pool.fetchval(
                "SELECT state FROM lightpanda_b0_write_fence WHERE job_posting_id = $1",
                posting_id,
            )
            == "active"
        )

        async with authoritative_write(pool, fence, job_posting_id=str(posting_id)) as connection:
            await connection.execute(
                "UPDATE job_posting "
                "SET titles = ARRAY['committed'], next_scrape_at = NULL "
                "WHERE id = $1",
                posting_id,
            )
        committed = await pool.fetchrow(
            "SELECT posting.titles, posting.next_scrape_at, fence.state "
            "FROM job_posting AS posting "
            "JOIN lightpanda_b0_write_fence AS fence "
            "ON fence.job_posting_id = posting.id "
            "WHERE posting.id = $1",
            posting_id,
        )
        assert committed is not None
        assert tuple(committed) == (["committed"], None, "revoked")

        with pytest.raises(LightpandaWriteFenceRejected) as rejected:
            await activate_write_fence(pool, fence)
        assert rejected.value.reason == "revoked_replay"

        migration = importlib.import_module(
            "src.migrations.versions.0024_add_lightpanda_b0_write_fence"
        )
        async with pool.acquire() as connection, connection.transaction():
            with pytest.raises(asyncpg.RaiseError, match="rollback refused"):
                await connection.execute(migration._REMOVE_WRITE_FENCE)
    finally:
        await pool.execute("DELETE FROM job_posting WHERE id = $1", posting_id)
        await pool.execute("DELETE FROM job_board WHERE id = $1", board_id)
        await pool.execute("DELETE FROM company WHERE id = $1", company_id)
        await pool.close()


async def test_generation_and_config_progression_fail_closed() -> None:
    dsn = os.environ["LOCAL_DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    posting_id = uuid.uuid4()
    current = _fence(posting_id, revision=5, digest="c" * 64, sequence=51)

    try:
        await pool.execute(
            "INSERT INTO company (id, name, slug) VALUES ($1, $2, $3)",
            company_id,
            "Lightpanda fence generations",
            f"lightpanda-fence-generations-{company_id.hex}",
        )
        await pool.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            board_id,
            company_id,
            f"lightpanda-fence-generations-{board_id.hex}",
            f"https://fence-generations.invalid/{board_id}",
        )
        await pool.execute(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            posting_id,
            company_id,
            board_id,
            f"https://fence-generations.invalid/posting/{posting_id}",
        )
        await activate_write_fence(pool, current)
        await activate_write_fence(pool, current)

        rejected = [
            replace(current, claim_token="7:50"),
            replace(current, claim_token="7:52", shard_id="other-shard"),
            replace(current, claim_token="7:52", config_revision=4),
            replace(current, claim_token="7:52", payload_sha256="d" * 64),
        ]
        for candidate in rejected:
            with pytest.raises(LightpandaWriteFenceRejected):
                await activate_write_fence(pool, candidate)

        replacement = replace(
            current,
            claim_token="7:52",
            config_revision=6,
            payload_sha256="d" * 64,
        )
        await activate_write_fence(pool, replacement)
        row = await pool.fetchrow(
            "SELECT claim_sequence, config_revision, payload_sha256, state "
            "FROM lightpanda_b0_write_fence WHERE job_posting_id = $1",
            posting_id,
        )
        assert row is not None
        assert tuple(row) == (52, 6, "d" * 64, "active")
    finally:
        await pool.execute("DELETE FROM job_posting WHERE id = $1", posting_id)
        await pool.execute("DELETE FROM job_board WHERE id = $1", board_id)
        await pool.execute("DELETE FROM company WHERE id = $1", company_id)
        await pool.close()
