from __future__ import annotations

import sys

import pytest

from src.cli import parse_args


def test_reconciliation_cli_defaults_to_bounded_read_only(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "reconcile"])

    args = parse_args()

    assert args.repair is False
    assert args.full is False
    assert args.fresh_cycle is False
    assert args.max_partitions == 16
    assert args.start_partition == 0
    assert args.target == "typesense"
    assert args.candidate_order_benchmark_sha256 is None


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


def test_candidate_order_receipt_cli_requires_the_full_proof_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crawler",
            "reconcile",
            "--repair",
            "--full",
            "--candidate-order-benchmark-sha256",
            "a" * 64,
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        parse_args()

    assert exc_info.value.code == 2
