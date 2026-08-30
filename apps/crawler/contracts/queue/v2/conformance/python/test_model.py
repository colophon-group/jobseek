from __future__ import annotations

import copy
import hashlib
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from contracts.queue.v2 import model
from contracts.queue.v2.tools import generate_corpus

V2_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = V2_ROOT / "fixtures" / "scenarios.json"
DIGEST_PATH = V2_ROOT / "fixtures" / "scenarios.sha256"


def _corpus() -> dict[str, Any]:
    corpus = model.load_json(CORPUS_PATH)
    assert set(corpus) == {"cases", "format"}
    assert corpus["format"] == model.FORMAT
    return corpus


def _cases() -> dict[str, dict[str, Any]]:
    return {case["id"]: case for case in _corpus()["cases"]}


@pytest.mark.parametrize("case", _corpus()["cases"], ids=lambda case: case["id"])
def test_python_reference_matches_every_checked_in_case(case: dict[str, Any]) -> None:
    executable = {key: case[key] for key in ("id", "initial", "operations")}
    result = model.run_case(executable)
    assert result == case["expected"]
    assert model.digest(result) == case["result_digest"]


def test_corpus_generation_and_full_file_digest_are_deterministic() -> None:
    corpus_bytes, digest_bytes = generate_corpus.rendered_files()
    assert CORPUS_PATH.read_bytes() == corpus_bytes
    assert DIGEST_PATH.read_bytes() == digest_bytes
    assert hashlib.sha256(corpus_bytes).hexdigest() in digest_bytes.decode()
    subprocess.run(
        [sys.executable, str(V2_ROOT / "tools" / "generate_corpus.py"), "--check"],
        check=True,
        cwd=V2_ROOT.parents[2],
    )


def test_reclaimed_task_fences_all_old_claim_transitions_without_mutation() -> None:
    case = _cases()["reap_reclaim_fences_every_stale_transition"]
    trace = case["expected"]["trace"]
    current_claim_digest = trace[2]["snapshot_digest"]
    stale = trace[3:9]
    assert [entry["kind"] for entry in stale] == [
        "heartbeat",
        "authorize_write",
        "complete",
        "reschedule",
        "fail",
        "reap",
    ]
    assert all(entry["decision"] == "fenced" for entry in stale)
    assert all(entry["reason"] == "claim_mismatch" for entry in stale)
    assert all(entry["snapshot_digest"] == current_claim_digest for entry in stale)
    assert trace[4]["write_authorized"] is False
    final = case["expected"]["final"]["records"][0]
    assert final["state"] == "terminal"
    assert final["failures"] == 1


def test_stale_failure_does_not_consume_new_claim_budget() -> None:
    case = _cases()["stale_failure_cannot_consume_budget"]
    trace = case["expected"]["trace"]
    assert trace[3]["decision"] == "fenced"
    assert trace[3]["snapshot_digest"] == trace[2]["snapshot_digest"]
    final = case["expected"]["final"]["records"][0]
    assert final["state"] == "dead_letter"
    assert final["failures"] == 2


@pytest.mark.parametrize(
    ("case_id", "codes"),
    [
        ("conservation_detects_loss", {"loss"}),
        ("conservation_detects_duplication", {"duplication"}),
        ("conservation_detects_orphan_config", {"orphan_config"}),
        (
            "conservation_detects_route_mismatches",
            {"shard_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch"},
        ),
        ("conservation_detects_config_revision_mismatch", {"config_revision_mismatch"}),
        ("conservation_detects_token_collision", {"token_collision"}),
        (
            "conservation_detects_invalid_partition_shape",
            {"invalid_inflight", "invalid_non_inflight"},
        ),
        ("conservation_detects_unregistered_inflight_token", {"unregistered_claim_token"}),
        (
            "conservation_detects_issued_token_ledger_duplication",
            {"issued_token_duplication"},
        ),
    ],
)
def test_conservation_reports_required_violation_classes(case_id: str, codes: set[str]) -> None:
    audit = _cases()[case_id]["expected"]["audit"]
    assert audit["ok"] is False
    assert {violation["code"] for violation in audit["violations"]} == codes


def test_accepted_write_requires_exact_current_fence() -> None:
    case = _cases()["happy_complete_with_authoritative_write"]
    write = case["expected"]["trace"][2]
    assert write == {
        "decision": "accepted",
        "index": 2,
        "kind": "authorize_write",
        "reason": "write_authorized",
        "snapshot_digest": case["expected"]["trace"][1]["snapshot_digest"],
        "write_authorized": True,
    }


def test_unknown_operation_field_is_rejected_fail_closed() -> None:
    case = copy.deepcopy(generate_corpus.source_cases()[0])
    case["operations"][0]["future_field"] = "must-not-be-ignored"
    with pytest.raises(model.ContractError, match="unknown=.*future_field"):
        model.run_case(case)


def test_reference_model_does_not_access_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("queue-v2 conformance attempted network access")

    monkeypatch.setattr(socket, "socket", blocked)
    for case in generate_corpus.source_cases():
        model.run_case(case)
