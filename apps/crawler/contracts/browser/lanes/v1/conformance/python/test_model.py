# ruff: noqa: E501

from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from contracts.browser.lanes.v1 import model
from contracts.browser.lanes.v1.tools import generate_corpus

V1_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = V1_ROOT / "fixtures" / "scenarios.json"
DIGEST_PATH = V1_ROOT / "fixtures" / "scenarios.sha256"


def _corpus() -> dict[str, Any]:
    document = model.load_json(CORPUS_PATH)
    assert frozenset(document) == {"format", "cases"}
    assert document["format"] == model.FORMAT
    return document


@pytest.mark.parametrize("case", _corpus()["cases"], ids=lambda case: case["id"])
def test_reference_matches_each_checked_in_case(case: dict[str, Any]) -> None:
    result = model.evaluate(case["input"])
    assert result == case["expected"]
    assert model.digest(result) == case["result_digest"]
    assert model.canonical_bytes(result) == model.canonical_bytes(case["expected"])


def test_corpus_is_deterministic_and_sidecar_hashes_exact_raw_bytes() -> None:
    corpus, sidecar = generate_corpus.rendered_files()
    assert CORPUS_PATH.read_bytes() == corpus
    assert DIGEST_PATH.read_bytes() == sidecar
    assert sidecar == f"{hashlib.sha256(corpus).hexdigest()}  scenarios.json\n".encode()
    subprocess.run(
        [sys.executable, str(V1_ROOT / "tools" / "generate_corpus.py"), "--check"],
        check=True,
        cwd=V1_ROOT.parents[4],
    )


def test_claims_are_lane_local_and_never_echo_work_identity() -> None:
    case = next(case for case in _corpus()["cases"] if case["id"] == "claim_lightpanda_only")
    result = case["expected"]["lanes"]
    assert result["lightpanda"]["selected_item_index"] == 0
    assert result["chromium"]["selected_item_index"] is None
    assert all(
        set(decision)
        == {"decision", "desired_concurrency", "lane", "reasons", "selected_item_index"}
        for decision in result.values()
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"now":0,"now":1}',
        b'{"x":0} trailing',
        b'{"x":NaN}',
        b'{"x":"https://private.invalid"}',
        b'{"x":"bearer marker"}',
        b'{"x":"alpha.example"}',
        b'{"x":"127.0.0.1"}',
        b"[" * (model.MAX_DEPTH + 1) + b"0" + b"]" * (model.MAX_DEPTH + 1),
    ],
)
def test_malformed_documents_are_a_fixed_non_reflecting_error(raw: bytes) -> None:
    result = model.evaluate_document(raw)
    assert result == b'{"error":"invalid_input"}'
    assert b"private" not in result and b"marker" not in result and b"alpha" not in result


def test_future_unknown_shape_fails_closed() -> None:
    value = generate_corpus.source_cases()[0]["input"]
    value["lanes"]["lightpanda"]["future"] = 1
    assert model.evaluate(value) == {"error": "invalid_input"}


def test_age_override_prevents_new_first_time_starvation() -> None:
    case = next(
        case for case in _corpus()["cases"] if case["id"] == "age_override_beats_new_first_time"
    )
    assert case["expected"]["lanes"]["lightpanda"]["selected_item_index"] == 0


def test_reference_is_network_free(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("browser-lane conformance attempted network access")

    monkeypatch.setattr(socket, "socket", blocked)
    for case in _corpus()["cases"]:
        model.evaluate(case["input"])
