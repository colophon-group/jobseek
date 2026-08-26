from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import random
import subprocess
import sys
import tracemalloc
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[2]
CODEC_PATH = ROOT / "framing/codec.py"
VECTORS_PATH = ROOT / "fixtures/framing/vectors.json"


def _load_codec() -> Any:
    spec = importlib.util.spec_from_file_location("candidate_raw_framing", CODEC_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


codec = _load_codec()
VECTORS = json.loads(VECTORS_PATH.read_text())


def _payload(vector: dict[str, Any]) -> bytes:
    if "payload_hex" in vector:
        return bytes.fromhex(cast(str, vector["payload_hex"]))
    return bytes.fromhex(cast(str, vector["payload_repeat_hex"])) * cast(
        int, vector["payload_length"]
    )


def _wire(vector: dict[str, Any]) -> bytes:
    wire = bytes.fromhex(cast(str, vector["wire_hex"]))
    if "wire_trailing_repeat_hex" in vector:
        wire += bytes.fromhex(cast(str, vector["wire_trailing_repeat_hex"])) * cast(
            int, vector["wire_trailing_length"]
        )
    return wire


def _assert_code(caught: pytest.ExceptionInfo[Any], expected: object) -> None:
    assert isinstance(caught.value, codec.FramingError)
    assert caught.value.code == expected


def test_shared_roundtrip_vectors() -> None:
    for vector in VECTORS["roundtrip"]:
        payload = _payload(vector)
        wire = codec.encode_record(payload, vector["maximum"])
        assert wire.startswith(bytes.fromhex(vector["wire_prefix_hex"])), vector["name"]
        assert hashlib.sha256(wire).hexdigest() == vector["wire_sha256"], vector["name"]
        assert codec.decode_record(wire, vector["maximum"]) == payload, vector["name"]


def test_shared_encode_error_vectors() -> None:
    for vector in VECTORS["encode_errors"]:
        with pytest.raises(codec.FramingError) as caught:
            codec.encode_record(_payload(vector), vector["maximum"])
        _assert_code(caught, vector["expected_error"])


def test_shared_exact_decode_error_vectors() -> None:
    for vector in VECTORS["decode"]:
        with pytest.raises(codec.FramingError) as caught:
            codec.decode_record(_wire(vector), vector["maximum"])
        _assert_code(caught, vector["expected_error"])


class FragmentedReader:
    def __init__(self, data: bytes, fragment_size: int) -> None:
        self._data = data
        self._fragment_size = fragment_size
        self._offset = 0

    def read(self, count: int = -1) -> bytes:
        if self._offset == len(self._data):
            return b""
        limit = self._fragment_size if count < 0 else min(count, self._fragment_size)
        end = min(len(self._data), self._offset + limit)
        value = self._data[self._offset : end]
        self._offset = end
        return value


def test_shared_stream_vectors() -> None:
    for vector in VECTORS["streams"]:
        reader = FragmentedReader(bytes.fromhex(vector["wire_hex"]), vector["fragment_size"])
        payloads: list[bytes] = []
        expected_error = vector.get("expected_error")
        if expected_error:
            with pytest.raises(codec.FramingError) as caught:
                payloads.extend(codec.iter_records(reader, vector["maximum"]))
            _assert_code(caught, expected_error)
            continue
        payloads.extend(codec.iter_records(reader, vector["maximum"]))
        if "payloads_hex" in vector:
            assert [value.hex() for value in payloads] == vector["payloads_hex"], vector["name"]
        else:
            assert [hashlib.sha256(value).hexdigest() for value in payloads] == vector[
                "payloads_sha256"
            ], vector["name"]


def test_exact_buffer_and_stream_eof_have_distinct_contracts() -> None:
    with pytest.raises(codec.FramingError) as caught:
        codec.decode_record(b"", 1)
    _assert_code(caught, "truncated_prefix")
    assert codec.read_record(io.BytesIO(b""), 1) is None
    assert codec.decode_record(b"\x00", 1) == b""
    with pytest.raises(codec.FramingError) as caught:
        codec.decode_record(b"\x00\x00", 1)
    _assert_code(caught, "trailing_bytes")


def test_decode_next_leaves_concatenated_records_unconsumed() -> None:
    source = b"\x01a\x01b\x00"
    remainder: bytes | memoryview = source
    payloads: list[bytes] = []
    while remainder:
        payload, remainder = codec.decode_next(remainder, 2)
        assert isinstance(remainder, memoryview)
        assert remainder.obj is source
        payloads.append(payload)
    assert payloads == [b"a", b"b", b""]


def test_exact_decode_rejects_large_trailing_data_without_copying_it() -> None:
    vector = next(
        item for item in VECTORS["decode"] if item["name"] == "large-trailing-after-empty"
    )
    wire = _wire(vector)

    tracemalloc.start()
    try:
        with pytest.raises(codec.FramingError) as caught:
            codec.decode_record(wire, vector["maximum"])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    _assert_code(caught, "trailing_bytes")
    assert peak < 1024 * 1024


def test_decode_next_many_records_keeps_one_backing_buffer() -> None:
    record_count = 50_000
    wire = b"\x00" * record_count
    remainder: bytes | memoryview = wire

    for remaining_count in range(record_count - 1, -1, -1):
        payload, remainder = codec.decode_next(remainder, 1)
        assert payload == b""
        assert isinstance(remainder, memoryview)
        assert remainder.obj is wire
        assert len(remainder) == remaining_count


def _released_view() -> memoryview:
    view = memoryview(b"\x00")
    view.release()
    return view


def _assert_released_view_failure(caught: pytest.ExceptionInfo[Any]) -> None:
    _assert_code(caught, "invalid_buffer")
    assert isinstance(caught.value.__cause__, ValueError)


def test_encode_record_normalizes_released_memoryview() -> None:
    with pytest.raises(codec.FramingError) as caught:
        codec.encode_record(_released_view(), 1)
    _assert_released_view_failure(caught)


def test_decode_record_normalizes_released_memoryview() -> None:
    with pytest.raises(codec.FramingError) as caught:
        codec.decode_record(_released_view(), 1)
    _assert_released_view_failure(caught)


def test_decode_next_normalizes_released_memoryview() -> None:
    with pytest.raises(codec.FramingError) as caught:
        codec.decode_next(_released_view(), 1)
    _assert_released_view_failure(caught)


def test_prefix_inclusive_cap_property() -> None:
    rng = random.Random(7937)
    lengths = list(VECTORS["property_lengths"]) + [rng.randrange(0, 100_000) for _ in range(200)]
    for length in lengths:
        payload = bytes([length % 251]) * length
        exact = codec.uvarint_size(length) + length
        wire = codec.encode_record(payload, exact)
        assert codec.decode_record(wire, exact) == payload
        with pytest.raises(codec.FramingError) as caught:
            codec.encode_record(payload, exact - 1)
        _assert_code(caught, "frame_limit")


@pytest.mark.parametrize("maximum", [True, False, -1, 2**64, 1.5, "1", None])
def test_python_maximum_is_a_non_bool_uint64(maximum: object) -> None:
    with pytest.raises(codec.FramingError) as caught:
        codec.decode_record(b"\x00", maximum)
    _assert_code(caught, "invalid_maximum")


def test_reader_never_prefetches_the_next_record() -> None:
    reader = FragmentedReader(b"\x01a\x01b", fragment_size=8)
    assert codec.read_record(reader, 2) == b"a"
    assert codec.read_record(reader, 2) == b"b"
    assert codec.read_record(reader, 2) is None


def test_shared_corpus_generation_and_digest_are_deterministic() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "framing/generate_vectors.py"), "--check"], check=True
    )
    expected = (ROOT / "fixtures/framing/vectors.sha256").read_text().split()[0]
    assert hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest() == expected
