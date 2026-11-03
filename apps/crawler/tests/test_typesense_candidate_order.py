from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime

import pytest

from src.typesense_candidate_order import (
    build_candidate_order_readiness_receipt,
    candidate_order_fields,
    candidate_order_key,
    candidate_order_words,
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


def test_candidate_order_words_preserve_exact_uuid_order() -> None:
    values = [
        uuid.UUID(int=number)
        for number in (
            0,
            1,
            (1 << 64) - 1,
            1 << 64,
            (1 << 127) - 1,
            1 << 127,
            (1 << 128) - 1,
        )
    ]
    words = [candidate_order_words(value) for value in values]
    assert words == sorted(words)
    assert words[0] == (-(1 << 63), -(1 << 63))
    assert words[-1] == ((1 << 63) - 1, (1 << 63) - 1)


def test_candidate_order_fields_only_index_active_postings() -> None:
    value = uuid.UUID(int=42)
    assert candidate_order_fields(value, active=True) == {
        "candidate_order_key": candidate_order_key(value),
        "candidate_order_hi": candidate_order_words(value)[0],
        "candidate_order_lo": candidate_order_words(value)[1],
    }
    assert candidate_order_fields(value, active=False) == {
        "candidate_order_key": None,
        "candidate_order_hi": None,
        "candidate_order_lo": None,
    }


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
        "keyVersion": "uuid-int64-active-v1",
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
