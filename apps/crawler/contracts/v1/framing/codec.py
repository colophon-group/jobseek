"""Checked unsigned-varint framing for raw byte records.

This module intentionally knows nothing about protobuf or a runtime-contract
version. It provides the framing primitive that a later binding may consume.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import BinaryIO, Final, NoReturn

MAX_UINT64: Final = 2**64 - 1
_READ_CHUNK_BYTES: Final = 64 * 1024


class FramingError(ValueError):
    """A stable, typed framing failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> NoReturn:
    raise FramingError(code, detail)


def _validate_maximum(maximum: int) -> None:
    if isinstance(maximum, bool) or not isinstance(maximum, int):
        _fail("invalid_maximum", "maximum must be a non-bool uint64 integer")
    if maximum < 0 or maximum > MAX_UINT64:
        _fail("invalid_maximum", "maximum must be within uint64")


def _view(value: bytes | bytearray | memoryview, field: str) -> memoryview:
    try:
        view = memoryview(value)
    except TypeError:
        _fail("invalid_buffer", f"{field} must support the buffer protocol")
    if view.ndim != 1 or not view.contiguous:
        _fail("invalid_buffer", f"{field} must be a contiguous one-dimensional buffer")
    try:
        return view.cast("B")
    except TypeError:
        _fail("invalid_buffer", f"{field} cannot be viewed as bytes")


def uvarint_size(value: int) -> int:
    """Return the canonical base-128 little-endian uint64 prefix length."""

    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_UINT64:
        _fail("prefix_overflow", "unsigned-varint value must be within uint64")
    return max(1, (value.bit_length() + 6) // 7)


def _encode_prefix(value: int) -> bytes:
    prefix = bytearray()
    remaining = value
    while remaining >= 0x80:
        prefix.append((remaining & 0x7F) | 0x80)
        remaining >>= 7
    prefix.append(remaining)
    return bytes(prefix)


def _decode_prefix(data: memoryview) -> tuple[int, int]:
    if not data:
        _fail("truncated_prefix", "record ended before the unsigned-varint prefix")

    value = 0
    for index in range(min(len(data), 10)):
        byte = int(data[index])
        if index == 9 and (byte > 1 or byte & 0x80):
            _fail("prefix_overflow", "unsigned-varint prefix exceeds uint64")
        value |= (byte & 0x7F) << (7 * index)
        if byte < 0x80:
            prefix_length = index + 1
            if prefix_length != uvarint_size(value):
                _fail("nonminimal_prefix", "unsigned-varint prefix is not canonical")
            return value, prefix_length

    if len(data) < 10:
        _fail("truncated_prefix", "record ended inside the unsigned-varint prefix")
    _fail("prefix_overflow", "unsigned-varint prefix has more than ten bytes")


def _check_cap(length: int, prefix_length: int, maximum: int) -> None:
    # Keep this subtraction form: adding a hostile uint64 length to the prefix
    # is exactly the overflow bug the boundary must avoid.
    if maximum < prefix_length or length > maximum - prefix_length:
        _fail("frame_limit", "prefix plus payload exceeds maximum")


def encode_record(payload: bytes | bytearray | memoryview, maximum: int) -> bytes:
    """Encode exactly one raw record after checking the prefix-inclusive cap."""

    _validate_maximum(maximum)
    view = _view(payload, "payload")
    length = len(view)
    prefix = _encode_prefix(length)
    _check_cap(length, len(prefix), maximum)
    return prefix + view.tobytes()


def decode_next(data: bytes | bytearray | memoryview, maximum: int) -> tuple[bytes, bytes]:
    """Decode the first record, returning its payload and untouched remainder."""

    _validate_maximum(maximum)
    view = _view(data, "data")
    length, prefix_length = _decode_prefix(view)
    _check_cap(length, prefix_length, maximum)

    available = len(view) - prefix_length
    if available < length:
        _fail("truncated_payload", "record ended before its declared payload length")

    # length is now no greater than an existing Python buffer length, so the
    # conversion and addition cannot overflow or trigger a giant allocation.
    end = prefix_length + length
    return bytes(view[prefix_length:end]), bytes(view[end:])


def decode_record(data: bytes | bytearray | memoryview, maximum: int) -> bytes:
    """Decode exactly one record and reject any trailing bytes."""

    payload, remainder = decode_next(data, maximum)
    if remainder:
        _fail("trailing_bytes", "bytes remain after the exact record")
    return payload


def _read(reader: BinaryIO, count: int) -> bytes:
    chunk = reader.read(count)
    if not isinstance(chunk, bytes):
        _fail("reader_contract", "reader.read() must return bytes")
    if len(chunk) > count:
        _fail("reader_contract", "reader returned more bytes than requested")
    return chunk


def read_record(reader: BinaryIO, maximum: int) -> bytes | None:
    """Read one record without prefetching the next.

    ``None`` means clean EOF before a prefix. EOF after any prefix or payload
    byte is ambiguous because the peer may have dispatched semantic work.
    Memory is bounded by one accepted record, and the caller provides
    backpressure by deciding when to request the next record.
    """

    _validate_maximum(maximum)
    prefix = bytearray()
    while len(prefix) < 10:
        chunk = _read(reader, 1)
        if not chunk:
            if not prefix:
                return None
            _fail("ambiguous_eof", "EOF occurred inside the record prefix")
        prefix.extend(chunk)
        byte = chunk[0]
        if len(prefix) == 10 and (byte > 1 or byte & 0x80):
            _fail("prefix_overflow", "unsigned-varint prefix exceeds uint64")
        if byte < 0x80:
            length, prefix_length = _decode_prefix(memoryview(prefix))
            _check_cap(length, prefix_length, maximum)
            break
    else:  # pragma: no cover - the byte-ten overflow branch is exhaustive.
        _fail("prefix_overflow", "unsigned-varint prefix has more than ten bytes")

    payload = bytearray()
    remaining = length
    while remaining:
        chunk = _read(reader, min(remaining, _READ_CHUNK_BYTES))
        if not chunk:
            _fail("ambiguous_eof", "EOF occurred inside the record payload")
        payload.extend(chunk)
        remaining -= len(chunk)
    return bytes(payload)


def iter_records(reader: BinaryIO, maximum: int) -> Iterator[bytes]:
    """Yield successive records, preserving one-record-at-a-time backpressure."""

    while True:
        payload = read_record(reader, maximum)
        if payload is None:
            return
        yield payload
