from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from conformance.python.contract import (
    ContractViolation,
    _url,
    fencing_digest,
    load_case,
    load_replay,
    project_frames,
    semantic_hash,
    validate_case,
    validate_error,
    validate_replay,
    validate_transcript,
)
from crawler_runtime_contracts.v1 import runtime_pb2 as pb
from crawler_runtime_contracts.v1.framing import (
    FramingError,
    decode_delimited,
    encode_delimited,
)
from google.protobuf.json_format import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures"


def test_shared_framing_vectors() -> None:
    cases = json.loads((FIXTURES / "framing/cases.json").read_text())
    for value in cases:
        wire = bytes.fromhex(value["hex"])
        if value["valid"]:
            message, remaining = decode_delimited(wire, pb.ClientMessage, value["max_frame_bytes"])
            assert isinstance(message, pb.ClientMessage)
            assert remaining == b""
        else:
            with pytest.raises(FramingError) as caught:
                decode_delimited(wire, pb.ClientMessage, value["max_frame_bytes"])
            assert caught.value.kind == value["error"]


def test_framing_round_trip_and_oversize_encode() -> None:
    message = pb.ClientMessage(hello=pb.ClientHello())
    wire = encode_delimited(message, 3)
    _, remaining = decode_delimited(wire, pb.ClientMessage, 3)
    assert remaining == b""
    with pytest.raises(FramingError):
        encode_delimited(message, 0)


def test_shared_canonical_url_vectors() -> None:
    cases = json.loads((FIXTURES / "url/cases.json").read_text())
    for value in cases:
        if value["valid"]:
            _url(value["url"], value["name"])
        else:
            with pytest.raises(ContractViolation) as caught:
                _url(value["url"], value["name"])
            assert caught.value.code == "url"


def test_every_error_code_has_a_shared_policy_vector() -> None:
    cases = json.loads((FIXTURES / "errors/cases.json").read_text())
    assert len(cases) == len(pb.ErrorCode.values()) - 1
    for value in cases:
        error = pb.RuntimeError(
            code=pb.ErrorCode.Value(value["code"]),
            disposition=pb.ErrorDisposition.Value(value["disposition"]),
            message="shared typed error vector",
        )
        if "http_status" in value:
            error.http_status = value["http_status"]
        validate_error(error)


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
    projection = project_frames(list(replay.expected_frames), replay.execution_request)
    assert projection.filtered_count == 2
    assert projection.security_filtered_count == 1
    assert not projection.gone_detection_allowed
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
