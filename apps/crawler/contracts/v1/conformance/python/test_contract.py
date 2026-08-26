from __future__ import annotations

import socket
from pathlib import Path

import pytest
from conformance.python.contract import (
    ContractViolation,
    fencing_digest,
    load_case,
    load_replay,
    project_frames,
    semantic_hash,
    validate_case,
    validate_replay,
    validate_transcript,
)
from gen.python import runtime_pb2 as pb
from google.protobuf.json_format import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures"


@pytest.mark.parametrize("path", sorted((FIXTURES / "conformance/positive").glob("*.json")))
def test_shared_positive_conformance(path: Path) -> None:
    case = load_case(path)
    assert case.expected_valid
    validate_case(case)


@pytest.mark.parametrize("path", sorted((FIXTURES / "conformance/negative").glob("*.json")))
def test_shared_negative_conformance(path: Path) -> None:
    if path.name == "browser-union-partial-output.json":
        with pytest.raises(ParseError):
            load_case(path)
        return
    case = load_case(path)
    assert not case.expected_valid
    with pytest.raises(ContractViolation) as caught:
        validate_case(case)
    assert caught.value.code == case.expected_error


@pytest.mark.parametrize("path", sorted((FIXTURES / "replay").glob("*.json")))
def test_shared_replay_is_offline(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    def network_disabled(*_args, **_kwargs):
        raise AssertionError("offline replay attempted network access")

    monkeypatch.setattr(socket, "create_connection", network_disabled)
    monkeypatch.setattr(socket.socket, "connect", network_disabled)
    validate_replay(load_replay(path))


def test_nonzero_projection_and_semantic_hash_are_exact() -> None:
    replay = load_replay(FIXTURES / "replay/representative-paginated-monitor.json")
    projection = project_frames(list(replay.expected_frames))
    assert projection.filtered_count == 2
    assert projection.security_filtered_count == 1
    assert projection == replay.expected_projection
    assert semantic_hash(list(replay.expected_frames), projection) == (
        replay.expected_semantic_sha256
    )


def test_live_caller_fence_must_match_request() -> None:
    case = load_case(FIXTURES / "conformance/positive/artifact-handle.json")
    request = case.transcript.events[2].client.start
    live = type(request.fencing_context)()
    live.CopyFrom(request.fencing_context)
    live.routing_epoch -= 1
    live.fence_digest = fencing_digest(live)
    with pytest.raises(ContractViolation, match="stale against live caller"):
        validate_transcript(case.transcript, live)


def test_dynamic_origin_is_declared_before_ambiguous_dispatch() -> None:
    case = load_case(
        FIXTURES / "conformance/positive/disconnect-after-dynamic-dispatch-before-contact.json"
    )
    events = list(case.transcript.events)
    declaration_index = next(
        index
        for index, event in enumerate(events)
        if event.HasField("server")
        and event.server.HasField("frame")
        and event.server.frame.HasField("origin_operation_declared")
    )
    fault_index = next(index for index, event in enumerate(events) if event.HasField("fault"))
    contact_index = next(
        index
        for index, event in enumerate(events)
        if index > fault_index
        and event.HasField("server")
        and event.server.HasField("frame")
        and event.server.frame.HasField("origin_contact")
    )
    declaration = events[declaration_index].server.frame.origin_operation_declared.operation
    fault = events[fault_index].fault
    contact = events[contact_index].server.frame.origin_contact
    assert declaration_index < fault_index < contact_index
    assert declaration.operation_sequence == 1
    assert declaration.parent_origin_request_id == (
        case.transcript.events[2].client.start.origin_request_id
    )
    assert fault.origin_request_id == declaration.origin_request_id
    assert contact.operation == declaration
    assert contact.disposition == pb.ORIGIN_CONTACT_DISPOSITION_DEDUPLICATED
