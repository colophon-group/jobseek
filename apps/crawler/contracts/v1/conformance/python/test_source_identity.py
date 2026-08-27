from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema

V1 = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = V1 / "fixtures" / "source_identity"
VECTORS_PATH = FIXTURE_ROOT / "vectors.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def _varint(wire: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(wire) and shift < 70:
        byte = wire[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise AssertionError("malformed fixture varint")


def _fields(wire: bytes) -> list[tuple[int, int, object, bytes]]:
    fields: list[tuple[int, int, object, bytes]] = []
    offset = 0
    while offset < len(wire):
        start = offset
        tag, offset = _varint(wire, offset)
        number, wire_type = tag >> 3, tag & 7
        assert number > 0
        if wire_type == 0:
            value, offset = _varint(wire, offset)
        elif wire_type == 1:
            value = wire[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            size, offset = _varint(wire, offset)
            value = wire[offset : offset + size]
            offset += size
        elif wire_type == 5:
            value = wire[offset : offset + 4]
            offset += 4
        else:
            raise AssertionError(f"unsupported fixture wire type: {wire_type}")
        assert offset <= len(wire)
        fields.append((number, wire_type, value, wire[start:offset]))
    return fields


def _encode_varint(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _schema(version: str, message: str) -> dict[str, Any]:
    return VECTORS["schemas"][version]["messages"][message]


def _decode_message(wire: bytes, schema: dict[str, Any]) -> dict[str, Any]:
    fields_by_number = {field["number"]: field for field in schema["fields"]}
    known: list[tuple[dict[str, Any], object]] = []
    unknown: list[tuple[int, bytes]] = []
    for number, wire_type, value, raw in _fields(wire):
        field = fields_by_number.get(number)
        if field is None:
            unknown.append((number, raw))
            continue
        assert wire_type == field["wire_type"]
        if field["value_type"] == "string":
            decoded: object = bytes(value).decode("utf-8")
        elif field["value_type"] == "message":
            decoded = bytes(value)
        else:
            raise AssertionError(f"unsupported descriptor value type: {field['value_type']}")
        known.append((field, decoded))
    return {"known": known, "unknown": unknown}


def _encode_message(message: dict[str, Any]) -> bytes:
    encoded = bytearray()
    for field, value in message["known"]:
        payload = str(value).encode("utf-8") if field["value_type"] == "string" else bytes(value)
        encoded.extend(_encode_varint((field["number"] << 3) | field["wire_type"]))
        encoded.extend(_encode_varint(len(payload)))
        encoded.extend(payload)
    for _, raw in message["unknown"]:
        encoded.extend(raw)
    return bytes(encoded)


def _known_value(message: dict[str, Any], name: str) -> object | None:
    values = [value for field, value in message["known"] if field["name"] == name]
    assert len(values) <= 1
    return values[0] if values else None


def test_shared_wire_vectors_preserve_absence_presence_and_unknown_fields() -> None:
    assert VECTORS["format"] == 1
    assert {vector["message"] for vector in VECTORS["protobuf"]} == {
        "DiscoveredJob",
        "JobEffect",
    }
    for vector in VECTORS["protobuf"]:
        frozen_schema = _schema("frozen", vector["message"])
        current_schema = _schema("current", vector["message"])
        assert "source_identity" not in {field["name"] for field in frozen_schema["fields"]}
        identity_field = next(
            field for field in current_schema["fields"] if field["name"] == "source_identity"
        )
        assert identity_field == {
            "json_name": "sourceIdentity",
            "name": "source_identity",
            "number": 3,
            "proto3_optional": True,
            "value_type": "string",
            "wire_type": 2,
        }
        absent = bytes.fromhex(vector["absent_wire_hex"])
        present = bytes.fromhex(vector["present_wire_hex"])
        future = bytes.fromhex(vector["future_unknown_wire_hex"])

        old_absent = _decode_message(absent, frozen_schema)
        current_absent = _decode_message(absent, current_schema)
        assert _known_value(old_absent, "source_identity") is None
        assert _known_value(current_absent, "source_identity") is None
        assert _encode_message(old_absent) == absent
        assert _encode_message(current_absent) == absent

        current_present = _decode_message(present, current_schema)
        assert _known_value(current_present, "source_identity") == vector["source_identity"]
        assert _encode_message(current_present) == present

        # A pre-amendment reader treats tag 3 as unknown but must forward it.
        old_present = _decode_message(present, frozen_schema)
        assert _known_value(old_present, "source_identity") is None
        assert {number for number, _ in old_present["unknown"]} == {3}
        assert _encode_message(old_present) == present

        # An amended reader likewise forwards a later field it does not know.
        unknown = vector["future_unknown_field"]
        current_future = _decode_message(future, current_schema)
        assert unknown not in {field["number"] for field in current_schema["fields"]}
        assert {number for number, _ in current_future["unknown"]} == {unknown}
        assert _known_value(current_future, "source_identity") == vector["source_identity"]
        assert _encode_message(current_future) == future


def _monitor_payload(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": "crawler.runtime/v1",
        "urls": [job["url"]],
        "jobs": [job],
        "new_sitemap_url": None,
        "filtered_count": 0,
        "metadata_updates": None,
        "hybrid": False,
        "truncated": False,
    }


def test_json_property_is_optional_and_old_payload_bytes_are_stable() -> None:
    schema = json.loads((V1 / "monitor-result.schema.json").read_text(encoding="utf-8"))
    job_schema = schema["$defs"]["job"]
    assert "source_identity" in job_schema["properties"]
    assert "source_identity" not in job_schema["required"]
    validator = jsonschema.Draft202012Validator(schema)

    cases = {case["id"]: case for case in VECTORS["json_jobs"]}
    assert "source_identity" not in cases["absent"]["job"]
    assert cases["explicit-null"]["job"]["source_identity"] is None
    assert cases["present"]["job"]["source_identity"] == "smartrecruiters:example:42"
    for case in cases.values():
        canonical = json.dumps(case["job"], sort_keys=True, separators=(",", ":")).encode("ascii")
        assert canonical.hex() == case["canonical_json_hex"]
        validator.validate(_monitor_payload(case["job"]))

    invalid = dict(cases["present"]["job"], source_identity="not-namespaced")
    errors = list(validator.iter_errors(_monitor_payload(invalid)))
    assert errors


def test_frozen_vectors_regenerate_and_match_their_digest() -> None:
    subprocess.run(
        [sys.executable, str(FIXTURE_ROOT / "generate.py"), "--check"],
        check=True,
    )
    expected = (FIXTURE_ROOT / "vectors.sha256").read_text().split()[0]
    assert hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest() == expected
