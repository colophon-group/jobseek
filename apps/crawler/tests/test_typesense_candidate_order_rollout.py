from __future__ import annotations

import runpy
import uuid
from pathlib import Path

from src.typesense_candidate_order import candidate_order_key, candidate_order_words

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/typesense-candidate-order-rollout.py"
document_update = runpy.run_path(str(SCRIPT))["document_update"]


def test_rollout_payload_matches_steady_producer_at_int64_boundaries() -> None:
    for number in (0, 1, (1 << 64) - 1, 1 << 64, 1 << 127, (1 << 128) - 1):
        value = uuid.UUID(int=number)
        hi, lo = candidate_order_words(value)
        assert document_update(str(value), clear=False) == {
            "id": str(value),
            "candidate_order_key": candidate_order_key(value),
            "candidate_order_hi": hi,
            "candidate_order_lo": lo,
        }


def test_rollout_clear_removes_all_three_optional_values() -> None:
    value = str(uuid.UUID(int=1))
    assert document_update(value, clear=True) == {
        "id": value,
        "candidate_order_key": None,
        "candidate_order_hi": None,
        "candidate_order_lo": None,
    }
