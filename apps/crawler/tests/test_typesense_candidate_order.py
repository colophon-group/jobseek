from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.reconciliation import (
    ReconciliationError,
    RunSummary,
    issue_candidate_order_readiness_receipt,
)
from src.typesense_candidate_order import (
    build_candidate_order_readiness_receipt,
    candidate_order_key,
)


def _decode_receipt(receipt: str) -> dict[str, object]:
    padding = "=" * (-len(receipt) % 4)
    return json.loads(base64.urlsafe_b64decode(receipt + padding))


def test_candidate_order_key_is_compact_fixed_width_and_preserves_uuid_order() -> None:
    values = [
        uuid.UUID(int=0),
        uuid.UUID(int=1),
        uuid.UUID("7fffffff-ffff-ffff-ffff-ffffffffffff"),
        uuid.UUID("80000000-0000-0000-0000-000000000000"),
        uuid.UUID(int=(1 << 128) - 1),
    ]
    keys = [candidate_order_key(value) for value in values]

    assert keys == [
        "----------------------",
        "---------------------0",
        "0zzzzzzzzzzzzzzzzzzzzz",
        "1---------------------",
        "2zzzzzzzzzzzzzzzzzzzzz",
    ]
    assert sorted(keys) == keys
    assert len(set(keys)) == len(values)


def test_candidate_order_key_rejects_non_uuid_input() -> None:
    with pytest.raises(TypeError, match="must be a UUID"):
        candidate_order_key("00000000-0000-0000-0000-000000000000")  # type: ignore[arg-type]


def test_readiness_receipt_binds_all_reviewed_evidence() -> None:
    run_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    receipt = build_candidate_order_readiness_receipt(
        reconciliation_run_id=run_id,
        completed_at=datetime(2026, 9, 11, 10, 0, tzinfo=UTC),
        authoritative_count=123_456,
        partitions=256,
        unresolved=0,
        benchmark_sha256="a" * 64,
    )

    assert _decode_receipt(receipt) == {
        "authoritativeCount": 123_456,
        "benchmarkSha256": "a" * 64,
        "completedAt": "2026-09-11T10:00:00Z",
        "keyVersion": "uuid-b64lex-v1",
        "partitions": 256,
        "reconciliationRunId": str(run_id),
        "schemaVersion": "typesense-stable-candidate-order-readiness-v1",
        "unresolved": 0,
    }


@pytest.mark.parametrize(
    ("partitions", "unresolved", "benchmark_sha256"),
    [(255, 0, "a" * 64), (256, 1, "a" * 64), (256, 0, "A" * 64)],
)
def test_readiness_receipt_rejects_incomplete_or_unreviewed_evidence(
    partitions: int,
    unresolved: int,
    benchmark_sha256: str,
) -> None:
    with pytest.raises(ValueError):
        build_candidate_order_readiness_receipt(
            reconciliation_run_id=uuid.uuid4(),
            completed_at=datetime.now(UTC),
            authoritative_count=1,
            partitions=partitions,
            unresolved=unresolved,
            benchmark_sha256=benchmark_sha256,
        )


async def test_receipt_issuer_rereads_the_successful_durable_ledger_row() -> None:
    run_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    summary = RunSummary(
        run_id=run_id,
        mode="repair",
        target_scope="typesense",
        partitions_completed=256,
        checked_local=123_456,
        unresolved=0,
    )
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "completed_at": datetime(2026, 9, 11, 10, 0, tzinfo=UTC),
        "status": "success",
        "mode": "repair",
        "target_scope": "typesense",
        "partitions_completed": 256,
        "checked_local": 123_456,
        "unresolved": 0,
    }

    receipt = await issue_candidate_order_readiness_receipt(
        pool,
        summary,
        benchmark_sha256="b" * 64,
    )

    assert _decode_receipt(receipt)["reconciliationRunId"] == str(run_id)
    pool.fetchrow.assert_awaited_once()


async def test_receipt_issuer_rejects_a_non_success_ledger_row() -> None:
    summary = RunSummary(
        run_id=uuid.uuid4(),
        mode="repair",
        target_scope="typesense",
        partitions_completed=256,
        checked_local=10,
        unresolved=0,
    )
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "completed_at": datetime.now(UTC),
        "status": "interrupted",
        "mode": "repair",
        "target_scope": "typesense",
        "partitions_completed": 256,
        "checked_local": 10,
        "unresolved": 0,
    }

    with pytest.raises(ReconciliationError, match="does not prove readiness"):
        await issue_candidate_order_readiness_receipt(
            pool,
            summary,
            benchmark_sha256="b" * 64,
        )
