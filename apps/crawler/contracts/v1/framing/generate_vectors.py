#!/usr/bin/env python3
"""Generate the deterministic shared framing corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import cast

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "fixtures/framing/vectors.json"
DIGEST = ROOT / "fixtures/framing/vectors.sha256"
MAX_UINT64 = 2**64 - 1


def encode_prefix(value: int) -> bytes:
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def payload(vector: dict[str, object]) -> bytes:
    if "payload_hex" in vector:
        return bytes.fromhex(str(vector["payload_hex"]))
    return bytes.fromhex(str(vector["payload_repeat_hex"])) * cast(int, vector["payload_length"])


def roundtrip(
    name: str,
    *,
    maximum: int,
    payload_hex: str | None = None,
    payload_repeat_hex: str | None = None,
    payload_length: int | None = None,
) -> dict[str, object]:
    vector: dict[str, object] = {"name": name, "maximum": maximum}
    if payload_hex is not None:
        vector["payload_hex"] = payload_hex
    else:
        vector["payload_repeat_hex"] = payload_repeat_hex
        vector["payload_length"] = payload_length
    raw = payload(vector)
    wire = encode_prefix(len(raw)) + raw
    vector["wire_prefix_hex"] = encode_prefix(len(raw)).hex()
    vector["wire_sha256"] = hashlib.sha256(wire).hexdigest()
    return vector


def corpus() -> dict[str, object]:
    return {
        "schema": "jobseek.raw-record-framing/v1",
        "roundtrip": [
            roundtrip("empty", maximum=1, payload_hex=""),
            roundtrip("one-byte", maximum=2, payload_hex="ff"),
            roundtrip("length-127", maximum=128, payload_repeat_hex="a5", payload_length=127),
            roundtrip("length-128", maximum=130, payload_repeat_hex="5a", payload_length=128),
            roundtrip(
                "length-16383",
                maximum=16385,
                payload_repeat_hex="3c",
                payload_length=16383,
            ),
            roundtrip(
                "length-16384",
                maximum=16387,
                payload_repeat_hex="c3",
                payload_length=16384,
            ),
            roundtrip(
                "maximum-permitted-record",
                maximum=1023,
                payload_repeat_hex="6d",
                payload_length=1021,
            ),
        ],
        "encode_errors": [
            {
                "name": "prefix-inclusive-over-limit",
                "payload_repeat_hex": "6d",
                "payload_length": 1021,
                "maximum": 1022,
                "expected_error": "frame_limit",
            },
            {
                "name": "empty-record-with-zero-cap",
                "payload_hex": "",
                "maximum": 0,
                "expected_error": "frame_limit",
            },
        ],
        "decode": [
            {
                "name": "empty-buffer",
                "wire_hex": "",
                "maximum": 1,
                "expected_error": "truncated_prefix",
            },
            {
                "name": "partial-prefix",
                "wire_hex": "80",
                "maximum": 16,
                "expected_error": "truncated_prefix",
            },
            {
                "name": "nonminimal-zero",
                "wire_hex": "8000",
                "maximum": 2,
                "expected_error": "nonminimal_prefix",
            },
            {
                "name": "nonminimal-one",
                "wire_hex": "8100",
                "maximum": 3,
                "expected_error": "nonminimal_prefix",
            },
            {
                "name": "byte-ten-continuation",
                "wire_hex": "80808080808080808080",
                "maximum": MAX_UINT64,
                "expected_error": "prefix_overflow",
            },
            {
                "name": "eleven-byte-prefix",
                "wire_hex": "8080808080808080808000",
                "maximum": MAX_UINT64,
                "expected_error": "prefix_overflow",
            },
            {
                "name": "byte-ten-greater-than-one",
                "wire_hex": "ffffffffffffffffff02",
                "maximum": MAX_UINT64,
                "expected_error": "prefix_overflow",
            },
            {
                "name": "max-uint64-length",
                "wire_hex": "ffffffffffffffffff01",
                "maximum": MAX_UINT64,
                "expected_error": "frame_limit",
            },
            {
                "name": "near-max-overflowing-total",
                "wire_hex": "f7ffffffffffffffff01",
                "maximum": MAX_UINT64,
                "expected_error": "frame_limit",
            },
            {
                "name": "max-minus-ten-exact-total-fit-no-body",
                "wire_hex": "f5ffffffffffffffff01",
                "maximum": MAX_UINT64,
                "expected_error": "truncated_payload",
            },
            {
                "name": "cap-includes-prefix",
                "wire_hex": "03616263",
                "maximum": 3,
                "expected_error": "frame_limit",
            },
            {
                "name": "truncated-payload",
                "wire_hex": "036162",
                "maximum": 4,
                "expected_error": "truncated_payload",
            },
            {
                "name": "trailing-byte-after-empty",
                "wire_hex": "0000",
                "maximum": 1,
                "expected_error": "trailing_bytes",
            },
            {
                "name": "trailing-byte-after-one",
                "wire_hex": "016162",
                "maximum": 2,
                "expected_error": "trailing_bytes",
            },
            {
                "name": "large-trailing-after-empty",
                "wire_hex": "00",
                "wire_trailing_repeat_hex": "aa",
                "wire_trailing_length": 16 * 1024 * 1024,
                "maximum": 1,
                "expected_error": "trailing_bytes",
            },
        ],
        "streams": [
            {
                "name": "clean-pre-prefix-eof",
                "wire_hex": "",
                "maximum": 2,
                "fragment_size": 1,
                "payloads_hex": [],
            },
            {
                "name": "partial-prefix-eof",
                "wire_hex": "80",
                "maximum": 16,
                "fragment_size": 1,
                "payloads_hex": [],
                "expected_error": "ambiguous_eof",
            },
            {
                "name": "partial-payload-eof",
                "wire_hex": "036162",
                "maximum": 4,
                "fragment_size": 1,
                "payloads_hex": [],
                "expected_error": "ambiguous_eof",
            },
            {
                "name": "fragmented-empty-and-two-records",
                "wire_hex": "000161026263",
                "maximum": 3,
                "fragment_size": 1,
                "payloads_hex": ["", "61", "6263"],
            },
            {
                "name": "fragmented-length-128",
                "wire_hex": (encode_prefix(128) + bytes.fromhex("7e") * 128).hex(),
                "maximum": 130,
                "fragment_size": 7,
                "payloads_sha256": [hashlib.sha256(bytes.fromhex("7e") * 128).hexdigest()],
            },
            {
                "name": "exact-total-fit-giant-stream-eof",
                "wire_hex": "f5ffffffffffffffff01",
                "maximum": MAX_UINT64,
                "fragment_size": 10,
                "payloads_hex": [],
                "expected_error": "ambiguous_eof",
            },
        ],
        "property_lengths": [0, 1, 2, 126, 127, 128, 129, 255, 16383, 16384, 65535],
    }


def render() -> tuple[bytes, bytes]:
    raw = (json.dumps(corpus(), indent=2, sort_keys=True) + "\n").encode()
    digest = f"{hashlib.sha256(raw).hexdigest()}  vectors.json\n".encode()
    return raw, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw, digest = render()
    if args.check:
        return 0 if OUTPUT.read_bytes() == raw and DIGEST.read_bytes() == digest else 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(raw)
    DIGEST.write_bytes(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
