"""Compact, order-preserving Typesense key for canonical posting UUIDs."""

from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import UTC, datetime

CANDIDATE_ORDER_KEY_FIELD = "candidate_order_key"
CANDIDATE_ORDER_KEY_VERSION = "uuid-b64lex-v1"
CANDIDATE_ORDER_READINESS_SCHEMA = "typesense-stable-candidate-order-readiness-v1"
CANDIDATE_ORDER_PARTITION_COUNT = 256

# ASCII/code-point sorted. Fixed-width base-64 digits therefore preserve the
# unsigned 128-bit UUID order used by canonical lowercase UUID strings.
_SORTABLE_BASE64_ALPHABET = "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz"
_KEY_LENGTH = 22
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def candidate_order_key(value: uuid.UUID) -> str:
    if not isinstance(value, uuid.UUID):
        raise TypeError("candidate order key input must be a UUID")
    number = value.int
    digits = ["-"] * _KEY_LENGTH
    for index in range(_KEY_LENGTH - 1, -1, -1):
        number, digit = divmod(number, 64)
        digits[index] = _SORTABLE_BASE64_ALPHABET[digit]
    if number:
        raise ValueError("posting UUID exceeds candidate order key width")
    return "".join(digits)


def build_candidate_order_readiness_receipt(
    *,
    reconciliation_run_id: uuid.UUID,
    completed_at: datetime,
    authoritative_count: int,
    partitions: int,
    unresolved: int,
    benchmark_sha256: str,
) -> str:
    """Encode reviewed rollout evidence for the web reader activation gate.

    The durable reconciliation row is the proof source. The benchmark digest
    binds the separate, human-reviewed production-shaped memory/headroom
    artifact without turning this narrow rollout contract into a control plane.
    """

    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise ValueError("completed_at must be timezone-aware")
    if (
        isinstance(authoritative_count, bool)
        or not isinstance(authoritative_count, int)
        or authoritative_count < 0
        or authoritative_count > (1 << 53) - 1
    ):
        raise ValueError("authoritative_count must be a non-negative safe integer")
    if partitions != CANDIDATE_ORDER_PARTITION_COUNT:
        raise ValueError("candidate order readiness requires all 256 partitions")
    if unresolved != 0:
        raise ValueError("candidate order readiness requires zero unresolved rows")
    if not _SHA256.fullmatch(benchmark_sha256):
        raise ValueError("benchmark_sha256 must be a lowercase SHA-256 digest")

    payload = {
        "authoritativeCount": authoritative_count,
        "benchmarkSha256": benchmark_sha256,
        "completedAt": completed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "keyVersion": CANDIDATE_ORDER_KEY_VERSION,
        "partitions": partitions,
        "reconciliationRunId": str(reconciliation_run_id),
        "schemaVersion": CANDIDATE_ORDER_READINESS_SCHEMA,
        "unresolved": unresolved,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    return encoded.rstrip(b"=").decode("ascii")
