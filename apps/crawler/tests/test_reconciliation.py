"""Deterministic contracts for cross-store posting reconciliation."""

from __future__ import annotations

import asyncio
import importlib
import sys
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.cli import parse_args
from src.exporter import TaxonomyMaps
from src.reconciliation import (
    PARTITION_COUNT,
    PartitionResult,
    ReconciliationError,
    RunSummary,
    StoreSnapshot,
    TypesenseReconciliationClient,
    _bootstrap_typesense_buckets,
    _start_run,
    compare_snapshots,
    partition_bounds,
    reconcile_partition,
    reconciliation_bucket,
    run_reconciliation,
)


def _id(prefix: int, suffix: int) -> uuid.UUID:
    return uuid.UUID(hex=f"{prefix:02x}{suffix:030x}")


class _AsyncContext:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_args: object) -> None:
        return None


class _MemoryConnection:
    def __init__(self, pool: _MemoryPool) -> None:
        self.pool = pool

    def transaction(self) -> _AsyncContext:
        return _AsyncContext()

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        return await self.pool.fetch(query, *args)

    async def execute(self, query: str, *args: object) -> str:
        return await self.pool.execute(query, *args)


class _MemoryPool:
    """Small asyncpg-shaped store used to exercise the real repair path."""

    def __init__(self, states: dict[uuid.UUID, bool]) -> None:
        self.states = dict(states)
        self.connection = _MemoryConnection(self)

    def acquire(self) -> _AsyncContext:
        return _AsyncContext(self.connection)

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        if "id >= $1" in query:
            lower = args[0]
            upper = args[1]
            assert isinstance(lower, uuid.UUID)
            assert upper is None or isinstance(upper, uuid.UUID)
            return [
                {"id": posting_id, "is_active": active}
                for posting_id, active in sorted(self.states.items())
                if posting_id >= lower and (upper is None or posting_id < upper)
            ]
        if "id = ANY($1::uuid[])" in query:
            posting_ids = args[0]
            assert isinstance(posting_ids, list)
            return [
                {"id": posting_id, "is_active": self.states[posting_id]}
                for posting_id in posting_ids
                if posting_id in self.states
            ]
        raise AssertionError(f"unexpected query: {query}")

    async def execute(self, query: str, *args: object) -> str:
        if query.startswith("UPDATE job_posting SET is_active = false"):
            posting_ids = args[0]
            assert isinstance(posting_ids, list)
            for posting_id in posting_ids:
                if posting_id in self.states:
                    self.states[posting_id] = False
            return "UPDATE"
        raise AssertionError(f"unexpected query: {query}")


class _MemoryTypesense:
    def __init__(self, states: dict[uuid.UUID, bool]) -> None:
        self.states = dict(states)

    async def partition_snapshot(self, partition: int) -> StoreSnapshot:
        return StoreSnapshot(
            {
                posting_id: active
                for posting_id, active in self.states.items()
                if reconciliation_bucket(posting_id) == f"{partition:02x}"
            }
        )

    async def delete_ids(self, posting_ids: Sequence[str]) -> None:
        for posting_id in posting_ids:
            self.states.pop(uuid.UUID(posting_id), None)


@asynccontextmanager
async def _noop_fence(_pool: object) -> AsyncIterator[None]:
    yield


def test_uuid_partitions_are_contiguous_and_cover_the_keyspace() -> None:
    previous_upper: uuid.UUID | None = None
    for partition in range(PARTITION_COUNT):
        lower, upper = partition_bounds(partition)
        if previous_upper is not None:
            assert lower == previous_upper
        assert reconciliation_bucket(lower) == f"{partition:02x}"
        previous_upper = upper
    assert partition_bounds(0)[0].int == 0
    assert partition_bounds(PARTITION_COUNT - 1)[1] is None


def test_migration_persists_independent_target_cursors_and_run_history() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0013_add_cross_store_reconciliation_state"
    )
    execute = MagicMock()
    original_op = migration.op
    migration.op = MagicMock(execute=execute)
    try:
        migration.upgrade()
    finally:
        migration.op = original_op

    statements = "\n".join(call.args[0] for call in execute.call_args_list)
    assert "cross_store_reconciliation_state" in statements
    assert "cross_store_reconciliation_run" in statements
    assert "CHECK (partition_count = 256)" in statements
    assert "('supabase', true)" in statements
    assert "('typesense', false)" in statements


def test_reconciliation_cli_defaults_to_bounded_read_only(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "reconcile"])

    args = parse_args()

    assert args.repair is False
    assert args.full is False
    assert args.max_partitions == 16
    assert args.start_partition == 0
    assert args.target == "all"


def test_full_reconciliation_still_requires_explicit_repair(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["crawler", "reconcile", "--full", "--target", "typesense"],
    )

    args = parse_args()

    assert args.full is True
    assert args.repair is False
    assert args.target == "typesense"


async def test_new_lock_holder_marks_prior_running_ledgers_interrupted() -> None:
    pool = MagicMock()
    pool.execute = AsyncMock()
    summary = RunSummary(
        run_id=uuid.uuid4(),
        mode="repair",
        target_scope="typesense",
    )

    await _start_run(pool, summary)

    orphan_update = pool.execute.await_args_list[0].args[0]
    assert "SET status = 'interrupted'" in orphan_update
    assert "error_class = 'InterruptedRun'" in orphan_update
    assert "WHERE status = 'running'" in orphan_update
    assert "interval '2 hours'" not in orphan_update


async def test_cancelled_reconciliation_persists_interruption_and_unlocks(
    monkeypatch,
) -> None:
    lock_connection = MagicMock()
    lock_connection.fetchval = AsyncMock(side_effect=[True, True])
    local_pool = MagicMock()
    local_pool.acquire.return_value = _AsyncContext(lock_connection)
    started = asyncio.Event()

    async def blocked_partition(*_args: object, **_kwargs: object) -> PartitionResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    start_run = AsyncMock()
    finish_run = AsyncMock()
    monkeypatch.setattr("src.reconciliation._start_run", start_run)
    monkeypatch.setattr("src.reconciliation._finish_run", finish_run)
    monkeypatch.setattr("src.reconciliation.reconcile_partition", blocked_partition)

    task = asyncio.create_task(
        run_reconciliation(
            local_pool,
            MagicMock(),
            repair=False,
            max_partitions=1,
            target_scope="supabase",
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    finish_run.assert_awaited_once()
    assert finish_run.await_args.kwargs == {
        "status": "interrupted",
        "error_class": "InterruptedRun",
    }
    assert (
        lock_connection.fetchval.await_args_list[-1].args[0].startswith("SELECT pg_advisory_unlock")
    )


def test_snapshot_diff_is_bidirectional_and_preserves_remote_inactive_history() -> None:
    shared = _id(0xAA, 1)
    mismatch = _id(0xAA, 2)
    missing = _id(0xAA, 3)
    remote_active = _id(0xAA, 4)
    remote_inactive = _id(0xAA, 5)
    diff = compare_snapshots(
        StoreSnapshot({shared: True, mismatch: False, missing: True}),
        StoreSnapshot(
            {
                shared: True,
                mismatch: True,
                remote_active: True,
                remote_inactive: False,
            }
        ),
    )

    assert diff.missing_remote == {missing}
    assert diff.state_mismatch == {mismatch}
    assert diff.remote_only_active == {remote_active}
    assert diff.remote_only_inactive == {remote_inactive}
    assert diff.actionable_ids("supabase") == {missing, mismatch, remote_active}
    assert diff.actionable_ids("typesense") == {
        missing,
        mismatch,
        remote_active,
        remote_inactive,
    }


async def test_injected_supabase_drift_is_repaired_and_verified(monkeypatch) -> None:
    prefix = 0xAA
    shared = _id(prefix, 1)
    mismatch = _id(prefix, 2)
    missing = _id(prefix, 3)
    remote_active = _id(prefix, 4)
    remote_inactive = _id(prefix, 5)
    local = _MemoryPool({shared: True, mismatch: False, missing: True})
    remote = _MemoryPool(
        {
            shared: True,
            mismatch: True,
            remote_active: True,
            remote_inactive: False,
        }
    )

    async def upsert(_pool: object, rows: list[dict[str, object]]) -> set[uuid.UUID]:
        for row in rows:
            posting_id = row["id"]
            assert isinstance(posting_id, uuid.UUID)
            remote.states[posting_id] = bool(row["is_active"])
        return set()

    monkeypatch.setattr("src.reconciliation.export_cursor_fence", _noop_fence)
    monkeypatch.setattr("src.reconciliation._upsert_to_supabase", upsert)

    result = await reconcile_partition(
        local,  # type: ignore[arg-type]
        remote,  # type: ignore[arg-type]
        target="supabase",
        partition=prefix,
        repair=True,
    )

    assert result.detected == 3
    assert result.repaired == 3
    assert result.unresolved == 0
    assert remote.states == {
        shared: True,
        mismatch: False,
        missing: True,
        remote_active: False,
        remote_inactive: False,
    }


async def test_injected_typesense_drift_is_repaired_to_exact_set(monkeypatch) -> None:
    prefix = 0xBB
    shared = _id(prefix, 1)
    mismatch = _id(prefix, 2)
    missing = _id(prefix, 3)
    remote_active = _id(prefix, 4)
    remote_inactive = _id(prefix, 5)
    local = _MemoryPool({shared: True, mismatch: False, missing: True})
    supabase = _MemoryPool({})
    remote = _MemoryTypesense(
        {
            shared: True,
            mismatch: True,
            remote_active: True,
            remote_inactive: False,
        }
    )

    def build_docs(rows: list[dict[str, object]], _maps: TaxonomyMaps) -> list[dict]:
        return [
            {
                "id": str(row["id"]),
                "is_active": row["is_active"],
                "reconciliation_bucket": reconciliation_bucket(str(row["id"])),
            }
            for row in rows
        ]

    async def upsert(docs: list[dict]) -> set[uuid.UUID]:
        for document in docs:
            remote.states[uuid.UUID(document["id"])] = document["is_active"]
        return set()

    monkeypatch.setattr("src.reconciliation.export_cursor_fence", _noop_fence)
    monkeypatch.setattr("src.reconciliation._build_typesense_docs", build_docs)
    monkeypatch.setattr("src.reconciliation._upsert_to_typesense", upsert)

    result = await reconcile_partition(
        local,  # type: ignore[arg-type]
        supabase,  # type: ignore[arg-type]
        target="typesense",
        partition=prefix,
        repair=True,
        typesense=remote,  # type: ignore[arg-type]
        maps=TaxonomyMaps(),
    )

    assert result.detected == 4
    assert result.repaired == 4
    assert result.unresolved == 0
    assert remote.states == {shared: True, mismatch: False, missing: True}


async def test_repair_fails_closed_when_downstream_does_not_converge(monkeypatch) -> None:
    prefix = 0xCC
    posting_id = _id(prefix, 1)
    local = _MemoryPool({posting_id: True})
    remote = _MemoryPool({})

    monkeypatch.setattr("src.reconciliation.export_cursor_fence", _noop_fence)
    monkeypatch.setattr(
        "src.reconciliation._upsert_to_supabase",
        AsyncMock(return_value=set()),
    )

    with pytest.raises(ReconciliationError, match="verification left 1 unresolved"):
        await reconcile_partition(
            local,  # type: ignore[arg-type]
            remote,  # type: ignore[arg-type]
            target="supabase",
            partition=prefix,
            repair=True,
        )


async def test_typesense_document_delete_url_encodes_untrusted_legacy_ids() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request)

    client = TypesenseReconciliationClient.__new__(TypesenseReconciliationClient)
    client._base_url = "https://typesense.invalid/collections/job_posting/documents"
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await client.delete_ids(["legacy/id ?#"])
    finally:
        await client.aclose()

    assert requests[0].url.raw_path.endswith(b"/legacy%2Fid%20%3F%23")


async def test_typesense_bootstrap_fails_closed_for_unbucketed_local_document() -> None:
    posting_id = _id(0xDD, 1)
    local = _MemoryPool({posting_id: True})

    class BootstrapTypesense:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def unbucketed_batches(self) -> AsyncIterator[list[tuple[str, bool]]]:
            yield [(str(posting_id), True)]

        async def delete_ids(self, posting_ids: Sequence[str]) -> None:
            self.deleted.extend(posting_ids)

    remote = BootstrapTypesense()
    with pytest.raises(ReconciliationError, match="local documents without buckets"):
        await _bootstrap_typesense_buckets(
            local,  # type: ignore[arg-type]
            remote,  # type: ignore[arg-type]
        )
    assert remote.deleted == []
