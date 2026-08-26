from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime_contract.v1.framing import FramingError, decode_record, encode_record

FIXTURES = Path(__file__).parents[1] / "contracts/v1/fixtures"


def test_production_framing_uses_shared_wire_vectors() -> None:
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
            assert decode_record(wire, vector["maximum"]) == payload
        else:
            with pytest.raises(FramingError) as caught:
                decode_record(bytes.fromhex(vector["wire_hex"]), vector["maximum"])
            assert caught.value.code == vector["expected_error"]
