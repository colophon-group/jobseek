"""Deterministic contracts for cross-store posting reconciliation."""

from __future__ import annotations

import asyncio
import importlib
import json
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
    _PARTITION_TYPESENSE_SQL,
    _TYPESENSE_POSTINGS_BY_ID_SQL,
    PARTITION_COUNT,
    PartitionResult,
    ReconciliationError,
    RunSummary,
    StoreSnapshot,
    TypesenseReconciliationClient,
    _advance_state,
    _bootstrap_typesense_buckets,
    _ensure_cycle,
    _persist_run_progress,
    _start_run,
    _targets,
    _typesense_documents_snapshot,
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
        return _typesense_documents_snapshot(
            {
                "id": str(posting_id),
                "is_active": active,
            }
            for posting_id, active in self.states.items()
            if reconciliation_bucket(posting_id) == f"{partition:02x}"
        )

    async def delete_ids(self, posting_ids: Sequence[str]) -> None:
        for posting_id in posting_ids:
            self.states.pop(uuid.UUID(posting_id), None)


class _MemoryPayloadTypesense:
    def __init__(self, documents: Sequence[dict[str, object]]) -> None:
        self.documents = {uuid.UUID(str(document["id"])): dict(document) for document in documents}

    async def partition_snapshot(self, partition: int) -> StoreSnapshot:
        return _typesense_documents_snapshot(
            document
            for posting_id, document in self.documents.items()
            if reconciliation_bucket(posting_id) == f"{partition:02x}"
        )

    async def delete_ids(self, posting_ids: Sequence[str]) -> None:
        for posting_id in posting_ids:
            self.documents.pop(uuid.UUID(posting_id), None)


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


def test_typesense_local_snapshots_join_authoritative_company_metadata() -> None:
    for query in (_PARTITION_TYPESENSE_SQL, _TYPESENSE_POSTINGS_BY_ID_SQL):
        assert "JOIN company c ON c.id = jp.company_id" in query
        assert "c.name AS company_name" in query
        assert "c.slug AS company_slug" in query
        assert "c.icon AS company_icon" in query


def test_migration_persists_independent_target_cursors_and_run_history(monkeypatch) -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0013_add_cross_store_reconciliation_state"
    )
    execute = MagicMock()
    monkeypatch.setattr(migration, "op", MagicMock(execute=execute))
    migration.upgrade()

    statements = "\n".join(call.args[0] for call in execute.call_args_list)
    assert "cross_store_reconciliation_state" in statements
    assert "cross_store_reconciliation_run" in statements
    assert "CHECK (partition_count = 256)" in statements
    assert "('supabase', true)" in statements
    assert "('typesense', false)" in statements


def test_payload_drift_migration_adds_separate_durable_counters(monkeypatch) -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0018_track_reconciliation_payload_drift"
    )
    execute = MagicMock()
    monkeypatch.setattr(migration, "op", MagicMock(execute=execute))
    migration.upgrade()

    statements = "\n".join(call.args[0] for call in execute.call_args_list)
    assert migration.down_revision == "0017"
    assert "cycle_detected" in statements
    assert "last_detected" in statements
    assert "cycle_payload_mismatch" in statements
    assert "last_payload_mismatch" in statements
    assert "payload_mismatch" in statements
    assert "next_partition = 0" in statements
    assert "WHERE target = 'typesense'" in statements


def test_reconciliation_cli_defaults_to_bounded_read_only(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "reconcile"])

    args = parse_args()

    assert args.repair is False
    assert args.full is False
    assert args.fresh_cycle is False
    assert args.max_partitions == 16
    assert args.start_partition == 0
    assert args.target == "typesense"


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


def test_default_reconciliation_uses_typesense_when_mirror_is_absent() -> None:
    assert _targets("all", relational_mirror_available=False) == ("typesense",)
    with pytest.raises(ReconciliationError, match="requires DATABASE_URL"):
        _targets("supabase", relational_mirror_available=False)


def test_fresh_cycle_cli_requires_full_repair(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["crawler", "reconcile", "--fresh-cycle", "--target", "typesense"],
    )

    with pytest.raises(SystemExit) as exc_info:
        parse_args()

    assert exc_info.value.code == 2


def test_fresh_cycle_cli_is_explicit(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crawler",
            "reconcile",
            "--repair",
            "--full",
            "--fresh-cycle",
            "--target",
            "typesense",
        ],
    )

    args = parse_args()

    assert args.repair is True
    assert args.full is True
    assert args.fresh_cycle is True
    assert args.target == "typesense"


async def test_fresh_cycle_replaces_midcycle_cursor_at_partition_zero() -> None:
    connection = MagicMock()
    connection.transaction.return_value = _AsyncContext()
    connection.fetchrow = AsyncMock(
        return_value={
            "next_partition": 173,
            "cycle_id": uuid.uuid4(),
        }
    )
    connection.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value = _AsyncContext(connection)

    partition = await _ensure_cycle(pool, "typesense", fresh=True)

    assert partition == 0
    update = connection.execute.await_args
    assert "next_partition = 0" in update.args[0]
    assert "cycle_runtime_seconds = 0" in update.args[0]
    assert update.args[1] == "typesense"
    assert isinstance(update.args[2], uuid.UUID)


async def test_default_repair_cycle_still_resumes_midcycle_cursor() -> None:
    connection = MagicMock()
    connection.transaction.return_value = _AsyncContext()
    connection.fetchrow = AsyncMock(
        return_value={
            "next_partition": 173,
            "cycle_id": uuid.uuid4(),
        }
    )
    connection.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value = _AsyncContext(connection)

    partition = await _ensure_cycle(pool, "typesense")

    assert partition == 173
    update = connection.execute.await_args.args[0]
    assert "next_partition = 0" not in update
    assert "last_attempt_at = clock_timestamp()" in update


async def test_fresh_full_repair_audits_all_partitions_from_midcycle_state(
    monkeypatch,
) -> None:
    durable_state = {"next_partition": 173}
    lock_connection = MagicMock()
    lock_connection.fetchval = AsyncMock(side_effect=[True, True])
    lock_connection.transaction.return_value = _AsyncContext()
    lock_connection.fetchrow = AsyncMock(
        return_value={
            "next_partition": durable_state["next_partition"],
            "cycle_id": uuid.uuid4(),
        }
    )

    async def reset_cycle(query: str, *_args: object) -> str:
        assert "next_partition = 0" in query
        durable_state["next_partition"] = 0
        return "UPDATE 1"

    lock_connection.execute = AsyncMock(side_effect=reset_cycle)
    local_pool = MagicMock()
    local_pool.acquire.return_value = _AsyncContext(lock_connection)
    examined: list[int] = []

    async def reconcile(
        *_args: object,
        target: str,
        partition: int,
        **_kwargs: object,
    ) -> PartitionResult:
        assert target == "typesense"
        examined.append(partition)
        return PartitionResult(
            target="typesense",
            partition=partition,
            local_rows=0,
            local_active=0,
            remote_rows=0,
            remote_active=0,
            missing_remote=0,
            state_mismatch=0,
            payload_mismatch=0,
            remote_only_active=0,
            remote_only_inactive=0,
            detected=0,
            repaired=0,
            unresolved=0,
            duration_seconds=0,
        )

    async def advance(
        _pool: object,
        result: PartitionResult,
        **_kwargs: object,
    ) -> bool:
        assert result.partition == durable_state["next_partition"]
        completed = result.partition == PARTITION_COUNT - 1
        durable_state["next_partition"] = 0 if completed else result.partition + 1
        return completed

    class FakeTypesense:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("src.reconciliation.reconcile_partition", reconcile)
    monkeypatch.setattr("src.reconciliation._advance_state", advance)
    monkeypatch.setattr("src.reconciliation._start_run", AsyncMock())
    monkeypatch.setattr("src.reconciliation._persist_run_progress", AsyncMock())
    monkeypatch.setattr("src.reconciliation._finish_run", AsyncMock())
    monkeypatch.setattr(
        "src.reconciliation._state_bootstrap_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "src.reconciliation._get_taxonomy_maps",
        AsyncMock(return_value=TaxonomyMaps()),
    )
    monkeypatch.setattr("src.reconciliation.TypesenseReconciliationClient", FakeTypesense)

    summary = await run_reconciliation(
        local_pool,
        MagicMock(),
        repair=True,
        full=True,
        fresh_cycle=True,
        target_scope="typesense",
    )

    assert examined == list(range(PARTITION_COUNT))
    assert summary.partitions_completed == PARTITION_COUNT
    assert durable_state["next_partition"] == 0


async def test_fresh_cycle_lock_contention_fails_instead_of_false_success() -> None:
    lock_connection = MagicMock()
    lock_connection.fetchval = AsyncMock(return_value=False)
    local_pool = MagicMock()
    local_pool.acquire.return_value = _AsyncContext(lock_connection)

    with pytest.raises(ReconciliationError, match="could not acquire the advisory lock"):
        await run_reconciliation(
            local_pool,
            MagicMock(),
            repair=True,
            full=True,
            fresh_cycle=True,
            target_scope="typesense",
        )

    lock_connection.fetchval.assert_awaited_once()


async def test_fresh_cycle_rejects_partial_partition_summary(monkeypatch) -> None:
    lock_connection = MagicMock()
    lock_connection.fetchval = AsyncMock(side_effect=[True, True])
    local_pool = MagicMock()
    local_pool.acquire.return_value = _AsyncContext(lock_connection)

    monkeypatch.setattr("src.reconciliation._start_run", AsyncMock())
    monkeypatch.setattr("src.reconciliation._finish_run", AsyncMock())
    monkeypatch.setattr("src.reconciliation._ensure_cycle", AsyncMock(return_value=255))
    monkeypatch.setattr(
        "src.reconciliation.reconcile_partition",
        AsyncMock(
            return_value=PartitionResult(
                target="typesense",
                partition=255,
                local_rows=0,
                local_active=0,
                remote_rows=0,
                remote_active=0,
                missing_remote=0,
                state_mismatch=0,
                payload_mismatch=0,
                remote_only_active=0,
                remote_only_inactive=0,
                detected=0,
                repaired=0,
                unresolved=0,
                duration_seconds=0,
            )
        ),
    )
    monkeypatch.setattr("src.reconciliation._advance_state", AsyncMock(return_value=True))
    monkeypatch.setattr("src.reconciliation._persist_run_progress", AsyncMock())
    monkeypatch.setattr(
        "src.reconciliation._state_bootstrap_complete",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "src.reconciliation.TypesenseReconciliationClient",
        lambda: MagicMock(aclose=AsyncMock()),
    )
    monkeypatch.setattr(
        "src.reconciliation._get_taxonomy_maps",
        AsyncMock(return_value=TaxonomyMaps()),
    )

    with pytest.raises(ReconciliationError, match="did not inspect every partition"):
        await run_reconciliation(
            local_pool,
            MagicMock(),
            repair=True,
            full=True,
            fresh_cycle=True,
            target_scope="typesense",
        )


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


async def test_run_progress_persists_payload_drift_separately() -> None:
    pool = MagicMock()
    pool.execute = AsyncMock()
    summary = RunSummary(
        run_id=uuid.uuid4(),
        mode="repair",
        target_scope="typesense",
        payload_mismatch=4,
    )

    await _persist_run_progress(pool, summary)

    query, *args = pool.execute.await_args.args
    assert "payload_mismatch = $6" in query
    assert args[5] == 4


async def test_partition_progress_accumulates_payload_drift_separately() -> None:
    connection = MagicMock()
    connection.transaction.return_value = _AsyncContext()
    connection.fetchrow = AsyncMock(
        return_value={
            "cycle_id": uuid.uuid4(),
            "next_partition": 0,
            "bootstrap_complete": True,
            "cycle_runtime_seconds": 1.0,
            "cycle_local_rows": 10,
            "cycle_local_active": 8,
            "cycle_remote_rows": 10,
            "cycle_remote_active": 8,
            "cycle_detected": 9,
            "cycle_missing_remote": 1,
            "cycle_state_mismatch": 2,
            "cycle_payload_mismatch": 3,
            "cycle_remote_only_active": 4,
            "cycle_remote_only_inactive": 5,
            "cycle_repaired": 6,
        }
    )
    connection.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value = _AsyncContext(connection)
    result = PartitionResult(
        target="typesense",
        partition=0,
        local_rows=1,
        local_active=1,
        remote_rows=1,
        remote_active=1,
        missing_remote=0,
        state_mismatch=0,
        payload_mismatch=7,
        remote_only_active=0,
        remote_only_inactive=0,
        detected=1,
        repaired=1,
        unresolved=0,
        duration_seconds=0.5,
    )

    completed = await _advance_state(pool, result)

    assert completed is False
    query, *args = connection.execute.await_args.args
    assert "cycle_detected = $9" in query
    assert args[8] == 10
    assert "cycle_payload_mismatch = $12" in query
    assert args[11] == 10


async def test_partition_progress_counts_overlapping_state_and_payload_drift_once() -> None:
    connection = MagicMock()
    connection.transaction.return_value = _AsyncContext()
    connection.fetchrow = AsyncMock(
        return_value={
            "cycle_id": uuid.uuid4(),
            "next_partition": 0,
            "bootstrap_complete": True,
            "cycle_runtime_seconds": 0,
            "cycle_local_rows": 0,
            "cycle_local_active": 0,
            "cycle_remote_rows": 0,
            "cycle_remote_active": 0,
            "cycle_detected": 0,
            "cycle_missing_remote": 0,
            "cycle_state_mismatch": 0,
            "cycle_payload_mismatch": 0,
            "cycle_remote_only_active": 0,
            "cycle_remote_only_inactive": 0,
            "cycle_repaired": 0,
        }
    )
    connection.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value = _AsyncContext(connection)
    result = PartitionResult(
        target="typesense",
        partition=0,
        local_rows=1,
        local_active=1,
        remote_rows=1,
        remote_active=0,
        missing_remote=0,
        state_mismatch=1,
        payload_mismatch=1,
        remote_only_active=0,
        remote_only_inactive=0,
        detected=1,
        repaired=0,
        unresolved=1,
        duration_seconds=0.1,
    )

    await _advance_state(pool, result)

    query, *args = connection.execute.await_args.args
    assert "cycle_detected = $9" in query
    assert args[8] == 1
    assert args[10] == 1
    assert args[11] == 1


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
    assert finish_run.await_args is not None
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
    assert diff.payload_mismatch == set()
    assert diff.remote_only_active == {remote_active}
    assert diff.remote_only_inactive == {remote_inactive}
    assert diff.actionable_ids("supabase") == {missing, mismatch, remote_active}
    assert diff.actionable_ids("typesense") == {
        missing,
        mismatch,
        remote_active,
        remote_inactive,
    }


def test_snapshot_diff_detects_same_id_same_state_payload_drift_separately() -> None:
    posting_id = _id(0xAA, 9)
    local = _typesense_documents_snapshot(
        [{"id": str(posting_id), "is_active": True, "title": "Current title"}]
    )
    remote = _typesense_documents_snapshot(
        [{"id": str(posting_id), "is_active": True, "title": "Stale title"}]
    )

    diff = compare_snapshots(local, remote)

    assert diff.state_mismatch == set()
    assert diff.payload_mismatch == {posting_id}
    assert diff.detected("typesense") == 1
    assert diff.actionable_ids("typesense") == {posting_id}


def test_payload_comparison_detects_mispaired_location_arrays() -> None:
    posting_id = _id(0xAA, 10)
    local = _typesense_documents_snapshot(
        [
            {
                "id": str(posting_id),
                "is_active": True,
                "location_ids": [100, 20],
                "location_direct_ids": [100, 20],
                "location_names": ["Athens", "Paris"],
                "location_types": ["onsite", "remote"],
                "location_geo_types": ["city", "city"],
            }
        ]
    )
    remote = _typesense_documents_snapshot(
        [
            {
                "id": str(posting_id),
                "is_active": True,
                "location_ids": [100, 20],
                "location_direct_ids": [100, 20],
                "location_names": ["Paris", "Athens"],
                "location_types": ["onsite", "remote"],
                "location_geo_types": ["city", "city"],
            }
        ]
    )

    assert compare_snapshots(local, remote).payload_mismatch == {posting_id}


def test_payload_comparison_detects_mispaired_technology_arrays() -> None:
    posting_id = _id(0xAA, 11)
    local = _typesense_documents_snapshot(
        [
            {
                "id": str(posting_id),
                "is_active": True,
                "technology_ids": [1, 2],
                "technology_names": ["Python", "PostgreSQL"],
            }
        ]
    )
    remote = _typesense_documents_snapshot(
        [
            {
                "id": str(posting_id),
                "is_active": True,
                "technology_ids": [1, 2],
                "technology_names": ["PostgreSQL", "Python"],
            }
        ]
    )

    assert compare_snapshots(local, remote).payload_mismatch == {posting_id}


def test_payload_comparison_canonicalizes_integral_floats() -> None:
    posting_id = _id(0xAA, 12)
    local = _typesense_documents_snapshot(
        [{"id": str(posting_id), "is_active": True, "salary_min": 1}]
    )
    remote = _typesense_documents_snapshot(
        [{"id": str(posting_id), "is_active": True, "salary_min": 1.0}]
    )

    assert compare_snapshots(local, remote).payload_mismatch == set()


def test_payload_comparison_canonicalizes_unordered_arrays() -> None:
    posting_id = _id(0xAA, 13)
    local = _typesense_documents_snapshot(
        [
            {
                "id": str(posting_id),
                "is_active": True,
                "occupation_ids": [30, 10, 20],
                "locales": ["it", "en", "de"],
            }
        ]
    )
    remote = _typesense_documents_snapshot(
        [
            {
                "id": str(posting_id),
                "is_active": True,
                "occupation_ids": [10, 20, 30],
                "locales": ["de", "it", "en"],
            }
        ]
    )

    assert compare_snapshots(local, remote).payload_mismatch == set()


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
        None,
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


async def test_same_state_typesense_payload_drift_is_repaired_and_verified(monkeypatch) -> None:
    prefix = 0xBC
    posting_id = _id(prefix, 1)
    local = _MemoryPool({posting_id: True})
    remote = _MemoryPayloadTypesense(
        [{"id": str(posting_id), "is_active": True, "title": "Stale title"}]
    )

    def build_docs(rows: list[dict[str, object]], _maps: TaxonomyMaps) -> list[dict]:
        return [
            {
                "id": str(row["id"]),
                "is_active": row["is_active"],
                "reconciliation_bucket": reconciliation_bucket(str(row["id"])),
                "title": "Current title",
                "technology_ids": [7, 3],
            }
            for row in rows
        ]

    async def upsert(docs: list[dict[str, object]]) -> set[str]:
        for document in docs:
            remote.documents[uuid.UUID(str(document["id"]))] = dict(document)
        return set()

    monkeypatch.setattr("src.reconciliation.export_cursor_fence", _noop_fence)
    monkeypatch.setattr("src.reconciliation._build_typesense_docs", build_docs)
    monkeypatch.setattr("src.reconciliation._upsert_to_typesense", upsert)

    result = await reconcile_partition(
        local,  # type: ignore[arg-type]
        None,
        target="typesense",
        partition=prefix,
        repair=True,
        typesense=remote,  # type: ignore[arg-type]
        maps=TaxonomyMaps(),
    )

    assert result.state_mismatch == 0
    assert result.payload_mismatch == 1
    assert result.detected == 1
    assert result.repaired == 1
    assert result.unresolved == 0
    assert remote.documents[posting_id]["title"] == "Current title"


async def test_typesense_payload_repair_fails_closed_without_verified_convergence(
    monkeypatch,
) -> None:
    prefix = 0xBD
    posting_id = _id(prefix, 1)
    local = _MemoryPool({posting_id: True})
    remote = _MemoryPayloadTypesense(
        [{"id": str(posting_id), "is_active": True, "title": "Stale title"}]
    )

    def build_docs(rows: list[dict[str, object]], _maps: TaxonomyMaps) -> list[dict]:
        return [
            {
                "id": str(row["id"]),
                "is_active": row["is_active"],
                "reconciliation_bucket": reconciliation_bucket(str(row["id"])),
                "title": "Current title",
            }
            for row in rows
        ]

    monkeypatch.setattr("src.reconciliation.export_cursor_fence", _noop_fence)
    monkeypatch.setattr("src.reconciliation._build_typesense_docs", build_docs)
    monkeypatch.setattr(
        "src.reconciliation._upsert_to_typesense",
        AsyncMock(return_value=set()),
    )

    with pytest.raises(ReconciliationError, match="verification left 1 unresolved"):
        await reconcile_partition(
            local,  # type: ignore[arg-type]
            None,
            target="typesense",
            partition=prefix,
            repair=True,
            typesense=remote,  # type: ignore[arg-type]
            maps=TaxonomyMaps(),
        )


async def test_typesense_repair_verifies_fresh_local_truth_without_open_transaction(
    monkeypatch,
) -> None:
    prefix = 0xBE
    posting_id = _id(prefix, 1)
    local = _MemoryPool({posting_id: True})
    remote = _MemoryPayloadTypesense(
        [{"id": str(posting_id), "is_active": True, "title": "Stale title"}]
    )
    transaction_active = False
    partition_reads = 0

    class TrackingTransaction(_AsyncContext):
        async def __aenter__(self) -> None:
            nonlocal transaction_active
            transaction_active = True

        async def __aexit__(self, *_args: object) -> None:
            nonlocal transaction_active
            transaction_active = False

    local.connection.transaction = MagicMock(return_value=TrackingTransaction())  # type: ignore[method-assign]
    original_fetch = local.fetch

    async def fetch(query: str, *args: object) -> list[dict[str, object]]:
        nonlocal partition_reads
        if "jp.id >= $1" in query:
            partition_reads += 1
        return await original_fetch(query, *args)

    local.fetch = fetch  # type: ignore[method-assign]

    def build_docs(rows: list[dict[str, object]], _maps: TaxonomyMaps) -> list[dict]:
        return [
            {
                "id": str(row["id"]),
                "is_active": row["is_active"],
                "reconciliation_bucket": reconciliation_bucket(str(row["id"])),
                "title": "Current title",
            }
            for row in rows
        ]

    async def upsert(docs: list[dict[str, object]]) -> set[str]:
        assert transaction_active is False
        for document in docs:
            remote.documents[uuid.UUID(str(document["id"]))] = dict(document)
        return set()

    monkeypatch.setattr("src.reconciliation.export_cursor_fence", _noop_fence)
    monkeypatch.setattr("src.reconciliation._build_typesense_docs", build_docs)
    monkeypatch.setattr("src.reconciliation._upsert_to_typesense", upsert)

    await reconcile_partition(
        local,  # type: ignore[arg-type]
        None,
        target="typesense",
        partition=prefix,
        repair=True,
        typesense=remote,  # type: ignore[arg-type]
        maps=TaxonomyMaps(),
    )

    assert partition_reads == 2
    local.connection.transaction.assert_not_called()  # type: ignore[attr-defined]


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


async def test_typesense_partition_export_requests_and_hashes_payload_fields() -> None:
    posting_id = _id(0xCE, 1)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        document = {
            "id": str(posting_id),
            "is_active": True,
            "reconciliation_bucket": "ce",
            "title": "Visible title",
        }
        return httpx.Response(200, text=f"{json.dumps(document)}\n", request=request)

    client = TypesenseReconciliationClient.__new__(TypesenseReconciliationClient)
    client._base_url = "https://typesense.invalid/collections/job_posting/documents"
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        snapshot = await client.partition_snapshot(0xCE)
    finally:
        await client.aclose()

    assert snapshot.states == {posting_id: True}
    assert snapshot.payload_fingerprints is not None
    assert posting_id in snapshot.payload_fingerprints
    include_fields = requests[0].url.params["include_fields"].split(",")
    assert "title" in include_fields
    assert "salary_currency" in include_fields
    assert requests[0].url.params["filter_by"] == "reconciliation_bucket:=ce"


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
