"""Real-PostgreSQL conformance for the inactive queue-v2 write fence."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import asyncpg
import pytest

from .adapter import (
    MAX_INTEGER,
    WriteFence,
    WriteFenceRejected,
    WriteFenceTransactionRequired,
    activate_write_fence,
    install_contract,
    require_write_fence,
    revoke_write_fence,
    rotate_write_fence,
)

POSTGRES_URL = os.getenv("QUEUE_V2_POSTGRES_URL", "")
ISOLATED = os.getenv("QUEUE_V2_POSTGRES_ISOLATED") == "1"
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL or not ISOLATED,
    reason="requires an explicitly isolated queue-v2 PostgreSQL fixture",
)


@dataclass(frozen=True, slots=True)
class ContractDatabase:
    dsn: str

    async def connect(self) -> asyncpg.Connection:
        connection = await asyncpg.connect(self.dsn)
        await connection.execute("SET statement_timeout = '5s'")
        await connection.execute("SET lock_timeout = '4s'")
        return connection


@pytest.fixture
async def contract_database() -> AsyncIterator[ContractDatabase]:
    control = await asyncpg.connect(POSTGRES_URL)
    try:
        database_name = await control.fetchval("SELECT current_database()")
        if database_name != "queue_v2_contract":
            pytest.fail(
                "queue-v2 PostgreSQL tests require the dedicated "
                f"queue_v2_contract database, got {database_name!r}"
            )
        schema_exists = await control.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'queue_v2_contract')"
        )
        if schema_exists:
            pytest.fail("queue_v2_contract schema must be absent before installation")
        await install_contract(control)
        await control.execute(
            "CREATE TABLE queue_v2_contract.protected_effect ("
            "task_kind text NOT NULL, "
            "task_id text NOT NULL, "
            "effect_kind text NOT NULL CHECK (effect_kind IN ('first', 'second')), "
            "payload text NOT NULL, "
            "PRIMARY KEY (task_kind, task_id, effect_kind)"
            ")"
        )
        yield ContractDatabase(POSTGRES_URL)
    finally:
        await control.execute("DROP SCHEMA IF EXISTS queue_v2_contract CASCADE")
        remaining = await control.fetchval(
            "SELECT count(*) FROM pg_namespace WHERE nspname = 'queue_v2_contract'"
        )
        assert remaining == 0
        await control.close()


def fence(
    task_id: str,
    *,
    task_kind: str = "board",
    shard_id: str = "sitemap-0",
    epoch: int = 7,
    owner: str = "go",
    revision: int = 11,
    sequence: int = 31,
) -> WriteFence:
    return WriteFence(
        task_kind=task_kind,
        task_id=task_id,
        shard_id=shard_id,
        routing_epoch=epoch,
        engine_owner=owner,
        config_revision=revision,
        claim_token=f"{epoch}:{sequence}",
    )


async def activate(connection: asyncpg.Connection, value: WriteFence) -> None:
    async with connection.transaction():
        await activate_write_fence(connection, value)


async def add_effects(connection: asyncpg.Connection, value: WriteFence) -> None:
    await connection.executemany(
        "INSERT INTO queue_v2_contract.protected_effect "
        "(task_kind, task_id, effect_kind, payload) VALUES ($1, $2, $3, $4)",
        [
            (value.task_kind, value.task_id, "first", "posting-like"),
            (value.task_kind, value.task_id, "second", "watermark-like"),
        ],
    )


async def effect_count(connection: asyncpg.Connection, value: WriteFence) -> int:
    return await connection.fetchval(
        "SELECT count(*) FROM queue_v2_contract.protected_effect "
        "WHERE task_kind = $1 AND task_id = $2",
        value.task_kind,
        value.task_id,
    )


async def wait_until_blocked(
    control: asyncpg.Connection,
    waiter_pid: int,
    blocker_pid: int,
) -> None:
    async with asyncio.timeout(3):
        while True:
            blockers = await control.fetchval("SELECT pg_blocking_pids($1)", waiter_pid)
            if blocker_pid in blockers:
                return
            await asyncio.sleep(0.005)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("routing_epoch", True),
        ("routing_epoch", False),
        ("routing_epoch", 7.0),
        ("routing_epoch", "7"),
        ("routing_epoch", 0),
        ("routing_epoch", MAX_INTEGER + 1),
        ("config_revision", True),
        ("config_revision", False),
        ("config_revision", 11.0),
        ("config_revision", "11"),
        ("config_revision", 0),
        ("config_revision", MAX_INTEGER + 1),
    ],
)
def test_write_fence_rejects_noncanonical_numeric_values(
    field_name: str,
    invalid_value: object,
) -> None:
    arguments: dict[str, object] = {
        "task_kind": "board",
        "task_id": "typed-integers",
        "shard_id": "sitemap-0",
        "routing_epoch": 7,
        "engine_owner": "go",
        "config_revision": 11,
        "claim_token": "7:31",
    }
    arguments[field_name] = invalid_value
    with pytest.raises(ValueError, match="invalid queue-v2 write fence"):
        WriteFence(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("task_kind", ""),
        ("task_kind", 3),
        ("task_id", ""),
        ("task_id", 3),
        ("shard_id", ""),
        ("shard_id", 3),
        ("engine_owner", ""),
        ("engine_owner", 3),
        ("claim_token", ""),
        ("claim_token", 731),
    ],
)
def test_write_fence_rejects_empty_and_non_string_identity_values(
    field_name: str,
    invalid_value: object,
) -> None:
    arguments: dict[str, object] = {
        "task_kind": "board",
        "task_id": "typed-strings",
        "shard_id": "sitemap-0",
        "routing_epoch": 7,
        "engine_owner": "go",
        "config_revision": 11,
        "claim_token": "7:31",
    }
    arguments[field_name] = invalid_value
    with pytest.raises(ValueError, match="invalid queue-v2 write fence"):
        WriteFence(**arguments)  # type: ignore[arg-type]


async def test_adapter_requires_explicit_caller_transaction(
    contract_database: ContractDatabase,
) -> None:
    connection = await contract_database.connect()
    value = fence("explicit-transaction")
    try:
        with pytest.raises(WriteFenceTransactionRequired):
            await activate_write_fence(connection, value)
        exists = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM queue_v2_contract.queue_v2_write_fence "
            "WHERE task_kind = $1 AND task_id = $2)",
            value.task_kind,
            value.task_id,
        )
        assert not exists
    finally:
        await connection.close()


async def test_exact_fence_and_both_effects_commit_atomically(
    contract_database: ContractDatabase,
) -> None:
    connection = await contract_database.connect()
    value = fence("exact-commit")
    try:
        async with connection.transaction():
            await activate_write_fence(connection, value)
        async with connection.transaction():
            await require_write_fence(connection, value)
            await add_effects(connection, value)
        assert await effect_count(connection, value) == 2

        # Authorization is a current-fence check, not exactly-once execution.
        async with connection.transaction():
            await require_write_fence(connection, value)
    finally:
        await connection.close()


async def test_missing_inactive_and_each_field_mismatch_reject_before_mutation(
    contract_database: ContractDatabase,
) -> None:
    connection = await contract_database.connect()
    value = fence("mismatch-matrix")
    missing = fence("mismatch-missing")
    mismatches = [
        replace(value, task_kind="scrape"),
        replace(value, task_id="mismatch-other"),
        replace(value, shard_id="sitemap-1"),
        fence(value.task_id, epoch=8, sequence=1),
        replace(value, engine_owner="python"),
        replace(value, config_revision=12),
        fence(value.task_id, sequence=32),
    ]
    try:
        await activate(connection, value)
        for candidate in [missing, *mismatches]:
            with pytest.raises(WriteFenceRejected):
                async with connection.transaction():
                    await require_write_fence(connection, candidate)
                    await add_effects(connection, value)
        assert await effect_count(connection, value) == 0

        async with connection.transaction():
            await revoke_write_fence(connection, value)
        with pytest.raises(WriteFenceRejected) as rejected:
            async with connection.transaction():
                await require_write_fence(connection, value)
        assert rejected.value.reason == "inactive"
        assert await effect_count(connection, value) == 0
    finally:
        await connection.close()


@pytest.mark.parametrize(
    ("epoch", "token"),
    [
        (7, ""),
        (7, "0:1"),
        (7, "07:1"),
        (7, "7:0"),
        (7, "7:01"),
        (7, "7:1:2"),
        (7, "8:1"),
        (7, f"7:{MAX_INTEGER + 1}"),
        (MAX_INTEGER + 1, f"{MAX_INTEGER + 1}:1"),
    ],
)
async def test_sql_rejects_noncanonical_tokens_without_leaking_values(
    contract_database: ContractDatabase,
    epoch: int,
    token: str,
) -> None:
    connection = await contract_database.connect()
    try:
        with pytest.raises(asyncpg.RaiseError) as rejected:
            async with connection.transaction():
                await connection.execute(
                    "SELECT queue_v2_contract."
                    "jobseek_queue_v2_activate_write_fence("
                    "$1, $2, $3, $4, $5, $6, $7)",
                    "board",
                    f"invalid-token-{epoch}-{len(token)}",
                    "sitemap-0",
                    epoch,
                    "go",
                    11,
                    token,
                )
        assert rejected.value.message == "queue_v2_write_fence_rejected"
        if token:
            assert token not in str(rejected.value)
        assert POSTGRES_URL not in str(rejected.value)
    finally:
        await connection.close()


async def test_generation_order_route_changes_and_reactivation_fail_closed(
    contract_database: ContractDatabase,
) -> None:
    connection = await contract_database.connect()
    current = fence("generation-order", sequence=31)
    higher = fence(current.task_id, sequence=32)
    try:
        await activate(connection, current)
        for rejected_next in [
            current,
            fence(current.task_id, sequence=30),
            fence(current.task_id, epoch=6, sequence=MAX_INTEGER),
            fence(current.task_id, shard_id="sitemap-1", sequence=32),
            fence(current.task_id, owner="python", sequence=32),
        ]:
            with pytest.raises(WriteFenceRejected):
                async with connection.transaction():
                    await rotate_write_fence(connection, current, rejected_next)

        async with connection.transaction():
            await rotate_write_fence(connection, current, higher)
        async with connection.transaction():
            await revoke_write_fence(connection, higher)
        for rejected_next in [higher, current]:
            with pytest.raises(WriteFenceRejected):
                async with connection.transaction():
                    await activate_write_fence(connection, rejected_next)

        next_epoch = fence(
            current.task_id,
            shard_id="sitemap-1",
            epoch=8,
            owner="python",
            sequence=1,
        )
        async with connection.transaction():
            await activate_write_fence(connection, next_epoch)
        async with connection.transaction():
            await require_write_fence(connection, next_epoch)
    finally:
        await connection.close()


async def test_simultaneous_duplicate_route_generation_is_sanitized(
    contract_database: ContractDatabase,
) -> None:
    connection = await contract_database.connect()
    first = fence("duplicate-generation-a", sequence=100)
    duplicate = fence("duplicate-generation-b", sequence=100)
    try:
        await activate(connection, first)
        with pytest.raises(WriteFenceRejected) as rejected:
            async with connection.transaction():
                await activate_write_fence(connection, duplicate)
        assert rejected.value.reason == "generation_in_use"
        assert first.claim_token not in str(rejected.value)
    finally:
        await connection.close()


async def test_activation_lock_first_rejects_waiting_competitor(
    contract_database: ContractDatabase,
) -> None:
    control = await contract_database.connect()
    winner = await contract_database.connect()
    competitor = await contract_database.connect()
    first = fence("activation-race", sequence=101)
    second = fence(first.task_id, sequence=102)
    winner_transaction = winner.transaction()
    try:
        await winner_transaction.start()
        await activate_write_fence(winner, first)

        async def competing_activation() -> None:
            async with competitor.transaction():
                await activate_write_fence(competitor, second)

        competing = asyncio.create_task(competing_activation())
        await wait_until_blocked(
            control,
            competitor.get_server_pid(),
            winner.get_server_pid(),
        )
        await winner_transaction.commit()
        with pytest.raises(WriteFenceRejected) as rejected:
            await asyncio.wait_for(competing, timeout=3)
        assert rejected.value.reason == "already_active"
        async with control.transaction():
            await require_write_fence(control, first)
    finally:
        if winner.is_in_transaction():
            await winner_transaction.rollback()
        await asyncio.gather(control.close(), winner.close(), competitor.close())


async def test_reactivation_lock_first_rejects_waiting_stale_writer(
    contract_database: ContractDatabase,
) -> None:
    control = await contract_database.connect()
    activator = await contract_database.connect()
    writer = await contract_database.connect()
    current = fence("reactivation-first", sequence=111)
    replacement = fence(current.task_id, sequence=112)
    activation_transaction = activator.transaction()
    try:
        await activate(control, current)
        async with control.transaction():
            await revoke_write_fence(control, current)
        await activation_transaction.start()
        await activate_write_fence(activator, replacement)

        async def stale_write() -> None:
            async with writer.transaction():
                await require_write_fence(writer, current)
                await add_effects(writer, current)

        writing = asyncio.create_task(stale_write())
        await wait_until_blocked(
            control,
            writer.get_server_pid(),
            activator.get_server_pid(),
        )
        await activation_transaction.commit()
        with pytest.raises(WriteFenceRejected):
            await asyncio.wait_for(writing, timeout=3)
        assert await effect_count(control, current) == 0
        async with control.transaction():
            await require_write_fence(control, replacement)
    finally:
        if activator.is_in_transaction():
            await activation_transaction.rollback()
        await asyncio.gather(control.close(), activator.close(), writer.close())


async def test_mutation_and_rotation_errors_roll_back_every_effect(
    contract_database: ContractDatabase,
) -> None:
    connection = await contract_database.connect()
    current = fence("rollback-effects", sequence=31)
    replacement = fence(current.task_id, sequence=32)
    try:
        await activate(connection, current)
        with pytest.raises(RuntimeError, match="synthetic mutation failure"):
            async with connection.transaction():
                await require_write_fence(connection, current)
                await add_effects(connection, current)
                raise RuntimeError("synthetic mutation failure")
        assert await effect_count(connection, current) == 0

        with pytest.raises(RuntimeError, match="synthetic rotation failure"):
            async with connection.transaction():
                await rotate_write_fence(connection, current, replacement)
                await add_effects(connection, current)
                raise RuntimeError("synthetic rotation failure")
        assert await effect_count(connection, current) == 0
        async with connection.transaction():
            await require_write_fence(connection, current)
    finally:
        await connection.close()


async def test_savepoint_rollback_requires_fresh_authorization(
    contract_database: ContractDatabase,
) -> None:
    connection = await contract_database.connect()
    current = fence("savepoint-rollback", sequence=121)
    try:
        await activate(connection, current)
        async with connection.transaction():
            savepoint = connection.transaction()
            await savepoint.start()
            await require_write_fence(connection, current)
            await add_effects(connection, current)
            await savepoint.rollback()
            assert await effect_count(connection, current) == 0

            # The rolled-back check is not a reusable capability. Reauthorize
            # inside the transaction scope that performs the replacement writes.
            async with connection.transaction():
                await require_write_fence(connection, current)
                await add_effects(connection, current)
        assert await effect_count(connection, current) == 2
    finally:
        await connection.close()


async def test_writer_lock_first_commits_before_rotation(
    contract_database: ContractDatabase,
) -> None:
    control = await contract_database.connect()
    writer = await contract_database.connect()
    rotator = await contract_database.connect()
    current = fence("writer-first", sequence=31)
    replacement = fence(current.task_id, sequence=32)
    writer_transaction = writer.transaction()
    try:
        await activate(control, current)
        await writer_transaction.start()
        await require_write_fence(writer, current)
        await add_effects(writer, current)

        async def rotate() -> None:
            async with rotator.transaction():
                await rotate_write_fence(rotator, current, replacement)

        rotation = asyncio.create_task(rotate())
        await wait_until_blocked(
            control,
            rotator.get_server_pid(),
            writer.get_server_pid(),
        )
        await writer_transaction.commit()
        await asyncio.wait_for(rotation, timeout=3)
        assert await effect_count(control, current) == 2
        with pytest.raises(WriteFenceRejected):
            async with writer.transaction():
                await require_write_fence(writer, current)
    finally:
        if writer.is_in_transaction():
            await writer_transaction.rollback()
        await asyncio.gather(control.close(), writer.close(), rotator.close())


async def test_rotation_lock_first_rejects_waiting_stale_writer(
    contract_database: ContractDatabase,
) -> None:
    control = await contract_database.connect()
    writer = await contract_database.connect()
    rotator = await contract_database.connect()
    current = fence("rotation-first", sequence=31)
    replacement = fence(current.task_id, sequence=32)
    rotation_transaction = rotator.transaction()
    try:
        await activate(control, current)
        await rotation_transaction.start()
        await rotate_write_fence(rotator, current, replacement)

        async def stale_write() -> None:
            async with writer.transaction():
                await require_write_fence(writer, current)
                await add_effects(writer, current)

        stale = asyncio.create_task(stale_write())
        await wait_until_blocked(
            control,
            writer.get_server_pid(),
            rotator.get_server_pid(),
        )
        await rotation_transaction.commit()
        with pytest.raises(WriteFenceRejected):
            await asyncio.wait_for(stale, timeout=3)
        assert await effect_count(control, current) == 0
    finally:
        if rotator.is_in_transaction():
            await rotation_transaction.rollback()
        await asyncio.gather(control.close(), writer.close(), rotator.close())


async def test_different_task_rows_do_not_serialize(
    contract_database: ContractDatabase,
) -> None:
    first_connection = await contract_database.connect()
    second_connection = await contract_database.connect()
    first = fence("concurrency-a", sequence=201)
    second = fence("concurrency-b", sequence=202)
    first_transaction = first_connection.transaction()
    try:
        await activate(first_connection, first)
        await activate(second_connection, second)
        await first_transaction.start()
        await require_write_fence(first_connection, first)

        async def write_second() -> None:
            async with second_connection.transaction():
                await require_write_fence(second_connection, second)
                await add_effects(second_connection, second)

        await asyncio.wait_for(write_second(), timeout=1)
        assert await effect_count(second_connection, second) == 2
        await first_transaction.rollback()
    finally:
        if first_connection.is_in_transaction():
            await first_transaction.rollback()
        await asyncio.gather(first_connection.close(), second_connection.close())


async def test_task_cancellation_rolls_back_and_unblocks_rotation(
    contract_database: ContractDatabase,
) -> None:
    control = await contract_database.connect()
    writer = await contract_database.connect()
    rotator = await contract_database.connect()
    current = fence("cancel-rollback", sequence=31)
    replacement = fence(current.task_id, sequence=32)
    writer_ready = asyncio.Event()
    try:
        await activate(control, current)

        async def cancellable_write() -> None:
            async with writer.transaction():
                await require_write_fence(writer, current)
                await add_effects(writer, current)
                writer_ready.set()
                await writer.execute("SELECT pg_sleep(30)")

        writing = asyncio.create_task(cancellable_write())
        await asyncio.wait_for(writer_ready.wait(), timeout=2)

        async def rotate() -> None:
            async with rotator.transaction():
                await rotate_write_fence(rotator, current, replacement)

        rotation = asyncio.create_task(rotate())
        await wait_until_blocked(
            control,
            rotator.get_server_pid(),
            writer.get_server_pid(),
        )
        writing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await writing
        await asyncio.wait_for(rotation, timeout=3)
        assert await effect_count(control, current) == 0
        async with control.transaction():
            await require_write_fence(control, replacement)
    finally:
        await asyncio.gather(control.close(), writer.close(), rotator.close())
