from __future__ import annotations

import copy
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

V1 = Path(__file__).resolve().parents[2]

_SPEC = importlib.util.spec_from_file_location(
    "runtime_v1_check_protocol", V1 / "tools" / "check_protocol.py"
)
assert _SPEC is not None and _SPEC.loader is not None
protocol = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = protocol
_SPEC.loader.exec_module(protocol)

REQUIRED_CASE_IDS = frozenset(
    {
        "accept_complete",
        "accept_deduplicated_bound_resume",
        "accept_identical_unacked_replay",
        "accept_resume_after_ambiguous_dispatch",
        "reject_actual_limit_exceeded",
        "reject_deadline_expired_history",
        "reject_divergent_sequence_reuse",
        "reject_duplicate_logical_dedup",
        "reject_duplicate_origin_dispatch",
        "reject_fault_metadata_mismatch",
        "reject_frame_after_terminal",
        "reject_late_frame_after_cancel",
        "reject_manifest_revision_changed",
        "reject_origin_dispatch_before_declaration",
        "reject_origin_identity_reused",
        "reject_origin_redeclaration_changed",
        "reject_request_binding_changed",
        "reject_reused_attempt",
        "reject_sequence_gap",
        "reject_sequence_rewind",
        "reject_stale_fence",
        "reject_terminal_count_mismatch",
        "reject_terminal_duplicate",
        "reject_terminal_missing",
        "reject_trace_binding_changed",
        "reject_unknown_checkpoint",
        "reject_unknown_origin_contact",
    }
)

LIMIT_FIELDS = frozenset(
    {
        "max_active_duration_ms",
        "max_artifact_chunk_bytes",
        "max_artifact_count",
        "max_artifact_total_bytes",
        "max_browser_actions",
        "max_browser_captures",
        "max_browser_evaluations",
        "max_browser_transfer_bytes",
        "max_execution_frames",
        "max_frame_bytes",
        "max_http_transfer_bytes",
        "max_in_flight_frames",
        "max_inline_body_bytes",
        "max_monitor_batches",
        "max_output_items",
        "max_retry_after_ms",
    }
)

REPLAY_PAYLOAD_CASES = {
    "artifact": "accept_unacked_artifact_replay",
    "browser_result": "accept_unacked_browser_result_replay",
    "error": "accept_unacked_error_replay",
    "monitor_batch": "accept_identical_unacked_replay",
    "origin_contact": "accept_deduplicated_bound_resume",
    "origin_operation_declared": "accept_unacked_dynamic_declaration_replay",
    "scrape_result": "accept_unacked_scrape_result_replay",
    "terminal": "accept_disconnect_after_terminal",
}


def _manifest() -> dict[str, Any]:
    return json.loads((V1 / "fixtures" / "control" / "manifest.json").read_text())


def _results() -> dict[str, Any]:
    return {result.case_id: result for result in protocol.validate_corpus(V1)}


def test_required_case_ids_are_independently_hard_coded_and_complete() -> None:
    manifest = _manifest()
    case_ids = [case["id"] for case in manifest["cases"]]
    assert protocol.MANDATORY_CASE_IDS == REQUIRED_CASE_IDS
    assert manifest["required_case_ids"] == sorted(REQUIRED_CASE_IDS)
    assert case_ids == sorted(case_ids)
    assert len(case_ids) == len(set(case_ids))
    assert set(case_ids) >= REQUIRED_CASE_IDS
    assert len(case_ids) >= 65


def test_every_fixture_matches_its_stable_result_and_closed_error_registry() -> None:
    manifest = _manifest()
    results = _results()
    assert len(results) == len(manifest["cases"])
    expected_codes = set()
    for case in manifest["cases"]:
        expected = case["expected"]
        result = results[case["id"]]
        assert (result.accepted, result.code) == (expected["accepted"], expected["code"])
        expected_codes.add(expected["code"])
        if result.accepted:
            assert result.binding_sha256
            assert result.terminal is not None
    assert expected_codes == protocol.ERROR_CODES


def test_fixture_events_use_only_descriptor_fields_and_fixture_metadata() -> None:
    manifest = _manifest()
    encoded = json.dumps(manifest)
    assert "max_origin_operations" not in encoded
    assert "max_errors" not in encoded
    assert "error_count" not in encoded
    assert "observed_at_rfc3339" not in encoded
    assert "durable_checkpoint" not in encoded
    assert "candidate_start" not in encoded

    for case in manifest["cases"]:
        assert set(case["metadata"]) == {
            "durable_cut_event_index",
            "injection_phase",
            "logical_time_rfc3339",
        }
        for event in case["events"]:
            if "frame" in event:
                assert "wire_size_bytes" not in event
                assert set(event["measurements"]) <= {
                    "output_items",
                    "wire_size_bytes",
                }
                if event["frame"]["payload"]["type"] == "monitor_batch":
                    assert "output_items" not in event["frame"]["payload"]
            if "client_hello" in event:
                assert set(event["client_hello"]["requested_limits"]) == LIMIT_FIELDS
            if "server_hello" in event:
                assert set(event["server_hello"]["accepted_limits"]) == LIMIT_FIELDS
            if "resume" in event:
                assert set(event["resume"]) <= {
                    "after_sequence",
                    "attempt_id",
                    "contract_version",
                    "fencing_context",
                    "origin_request_id",
                    "request_id",
                }
    assert "plan_id" not in encoded


def test_every_resume_has_a_fresh_handshake() -> None:
    for case in _manifest()["cases"]:
        for index, event in enumerate(case["events"]):
            if "resume" not in event:
                continue
            if case["id"] == "reject_resume_without_reconnect_handshake":
                assert "server_hello" not in case["events"][index - 1]
                continue
            assert "client_hello" in case["events"][index - 2]
            assert "server_hello" in case["events"][index - 1]


@pytest.mark.parametrize(("payload_type", "case_id"), REPLAY_PAYLOAD_CASES.items())
def test_every_execution_frame_arm_has_an_identical_unacked_replay(
    payload_type: str, case_id: str
) -> None:
    case = next(case for case in _manifest()["cases"] if case["id"] == case_id)
    payloads = [event["frame"]["payload"]["type"] for event in case["events"] if "frame" in event]
    assert payloads.count(payload_type) >= 2
    assert _results()[case_id].counts["replayed_frames"] >= 1


def test_replay_is_logical_once_but_physical_credit_is_always_consumed() -> None:
    results = _results()
    deduplicated = results["accept_deduplicated_bound_resume"]
    assert deduplicated.counts["replayed_frames"] == 1
    assert deduplicated.counts["dispatched"] == 1
    assert deduplicated.counts["deduplicated"] == 1
    assert deduplicated.terminal["frame_count"] == deduplicated.counts["frames"]
    assert results["reject_physical_replay_credit_exhausted"].code == "credit_exceeded"
    assert results["accept_window_replenishment"].accepted
    assert results["accept_partial_replay_fault_resume"].accepted


def test_checkpoint_cancel_and_fixture_history_invariants_are_exercised() -> None:
    results = _results()
    assert results["reject_resume_checkpoint_rewind"].code == "sequence_rewind"
    assert results["reject_resume_without_reconnect_handshake"].code == ("resume_handshake_missing")
    assert results["reject_old_attempt_terminal_after_fault"].code == ("transport_invalidated")
    assert results["accept_cancelled_terminal_after_cancel"].accepted
    assert results["reject_cancelled_terminal_without_cancel"].code == ("terminal_count_mismatch")
    assert results["reject_fixture_cut_mismatch"].code == "fixture_cut_mismatch"
    assert results["reject_fixture_injection_phase_mismatch"].code == (
        "fixture_injection_phase_mismatch"
    )
    deadline = results["reject_deadline_after_durable_prefix"]
    assert deadline.code == "deadline_exceeded"
    assert deadline.counts["frames"] == 1


def test_python_rejects_boolean_terminal_counters_like_typed_go() -> None:
    case = copy.deepcopy(
        next(case for case in _manifest()["cases"] if case["id"] == "accept_complete")
    )
    terminal = next(
        event["frame"]["payload"]
        for event in case["events"]
        if event.get("frame", {}).get("payload", {}).get("type") == "terminal"
    )
    terminal["frame_count"] = True
    result = protocol.validate_case(case)
    assert not result.accepted
    assert result.code == "invalid_corpus"


def test_control_fixture_generation_is_deterministic() -> None:
    subprocess.run(
        [sys.executable, V1 / "fixtures" / "control" / "generate.py", "--check"],
        check=True,
        cwd=V1,
    )


def test_protocol_cli_json_is_deterministic() -> None:
    command = [sys.executable, V1 / "tools" / "check_protocol.py", "--root", V1, "--json"]
    first = subprocess.run(command, check=True, capture_output=True).stdout
    second = subprocess.run(command, check=True, capture_output=True).stdout
    assert first == second
    assert len(json.loads(first)) == len(_manifest()["cases"])


def test_validation_performs_zero_origin_traffic(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("control conformance attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    assert len(protocol.validate_corpus(V1)) == len(_manifest()["cases"])
