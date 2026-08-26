from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from conformance.python.contract import (
    ContractViolation,
    content_hash,
    decode_record,
    encode_record,
    fencing_digest,
    load_case,
    load_replay,
    project_frames,
    semantic_hash,
    uvarint_size,
    validate_case,
    validate_replay,
    validate_transcript,
)
from gen.python import runtime_pb2 as pb
from google.protobuf import descriptor_pb2
from google.protobuf.json_format import ParseError
from tools.check_proto_compat import check_compatibility

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
    projection = project_frames(list(replay.expected_frames), replay.execution_request)
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


def test_origin_is_predeclared_and_fingerprint_bound_before_ambiguous_dispatch() -> None:
    case = load_case(FIXTURES / "conformance/positive/disconnect-after-predeclared-dispatch.json")
    events = list(case.transcript.events)
    request = events[2].client.start
    declaration = request.origin_operations[1]
    fault_index = next(index for index, event in enumerate(events) if event.HasField("fault"))
    contact_index = next(
        index
        for index, event in enumerate(events)
        if index > fault_index
        and event.HasField("server")
        and event.server.HasField("frame")
        and event.server.frame.HasField("origin_contact")
    )
    fault = events[fault_index].fault
    contact = events[contact_index].server.frame.origin_contact
    assert 2 < fault_index < contact_index
    assert declaration.operation_sequence == 1
    assert declaration.parent_origin_request_id == (
        case.transcript.events[2].client.start.origin_request_id
    )
    assert fault.origin_request_id == declaration.origin_request_id
    assert fault.request_fingerprint == declaration.request_fingerprint
    assert contact.operation == declaration
    assert contact.request_fingerprint == declaration.request_fingerprint
    assert contact.disposition == pb.ORIGIN_CONTACT_DISPOSITION_DEDUPLICATED


def test_shared_bounded_varint_framing_vectors() -> None:
    vectors = json.loads((FIXTURES / "wire/framing-vectors.json").read_text())["vectors"]
    for vector in vectors:
        if "payload_hex" in vector or "payload_byte" in vector:
            payload = (
                bytes.fromhex(vector["payload_hex"])
                if "payload_hex" in vector
                else bytes.fromhex(vector["payload_byte"]) * vector["payload_length"]
            )
            wire = encode_record(payload, vector["maximum"])
            prefix = bytes.fromhex(vector.get("wire_prefix_hex", vector.get("wire_hex", "")))
            assert wire.startswith(prefix), vector["name"]
            assert len(wire) == len(payload) + uvarint_size(len(payload))
            assert decode_record(wire, vector["maximum"]) == payload
        else:
            with pytest.raises(ContractViolation) as caught:
                decode_record(bytes.fromhex(vector["wire_hex"]), vector["maximum"])
            assert caught.value.code == vector["expected_error"]


def test_framing_boundary_uses_exact_varint_not_ten_byte_reservation() -> None:
    payload = b"x" * 127
    assert len(encode_record(payload, 128)) == 128
    with pytest.raises(ContractViolation, match="frame_limit"):
        encode_record(payload, 127)


def test_content_hash_canonicalizes_unordered_repeated_fields() -> None:
    left = pb.JobContent(
        title="Engineer",
        description_html="<p>Build</p>",
        locations=pb.StringList(values=["Zurich", "Bern"]),
        localizations=[
            pb.LocalizedJobContent(locale="fr", title="Ingénieur"),
            pb.LocalizedJobContent(locale="de", title="Ingenieur"),
        ],
        skills=["Python", "Go"],
    )
    right = pb.JobContent(
        title="Engineer",
        description_html="<p>Build</p>",
        locations=pb.StringList(values=["Bern", "Zurich"]),
        localizations=list(reversed(left.localizations)),
        skills=["Go", "Python"],
    )
    assert content_hash(left) == content_hash(right)
    right.ClearField("locations")
    assert content_hash(left) != content_hash(right)


def test_scrape_projection_binds_source_url_to_content_hash() -> None:
    replay = load_replay(FIXTURES / "replay/representative-scrape.json")
    projection = project_frames(list(replay.expected_frames), replay.execution_request)
    assert len(projection.job_effects) == 1
    assert projection.job_effects[0].source_url == replay.execution_request.scrape.source_url
    assert projection.job_effects[0].content_sha256 == projection.content_hashes[0]


def test_breaking_change_gate_requires_name_and_number_reservations() -> None:
    baseline = descriptor_pb2.FileDescriptorProto.FromString(pb.DESCRIPTOR.serialized_pb)
    current = descriptor_pb2.FileDescriptorProto.FromString(pb.DESCRIPTOR.serialized_pb)
    message = next(value for value in current.message_type if value.name == "JobEffect")
    removed = message.field.pop()
    with pytest.raises(AssertionError, match="reserve both name and number"):
        check_compatibility(baseline, current)
    reserved = message.reserved_range.add()
    reserved.start = removed.number
    reserved.end = removed.number + 1
    message.reserved_name.append(removed.name)
    check_compatibility(baseline, current)


def test_breaking_change_gate_rejects_wire_type_changes() -> None:
    baseline = descriptor_pb2.FileDescriptorProto.FromString(pb.DESCRIPTOR.serialized_pb)
    current = descriptor_pb2.FileDescriptorProto.FromString(pb.DESCRIPTOR.serialized_pb)
    message = next(value for value in current.message_type if value.name == "JobEffect")
    message.field[0].type = descriptor_pb2.FieldDescriptorProto.TYPE_BYTES
    with pytest.raises(AssertionError, match="wire shape changed"):
        check_compatibility(baseline, current)
