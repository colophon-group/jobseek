"""Unsigned-varint length-delimited protobuf framing for crawler.runtime/v1."""

from __future__ import annotations

from google.protobuf.message import Message


class FramingError(ValueError):
    """Malformed, truncated, non-canonical, or oversized wire record."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind


def _encode_varint(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _decode_varint(data: bytes) -> tuple[int, int]:
    value = 0
    for index in range(min(len(data), 10)):
        byte = data[index]
        if index == 9 and byte > 1:
            raise FramingError("malformed", "length varint overflows uint64")
        value |= (byte & 0x7F) << (7 * index)
        if byte < 0x80:
            consumed = index + 1
            if _encode_varint(value) != data[:consumed]:
                raise FramingError("malformed", "length varint is not minimally encoded")
            if value == 0:
                raise FramingError("malformed", "zero-length protobuf records are forbidden")
            return value, consumed
    if len(data) < 10:
        raise FramingError("ambiguous_eof", "length varint is truncated")
    raise FramingError("malformed", "length varint exceeds 10 bytes")


def encode_delimited(message: Message, max_frame_bytes: int) -> bytes:
    if max_frame_bytes <= 0:
        raise FramingError("oversize", "max_frame_bytes must be positive")
    payload = message.SerializeToString(deterministic=True)
    if not payload:
        raise FramingError("malformed", "zero-length protobuf records are forbidden")
    prefix = _encode_varint(len(payload))
    if len(prefix) + len(payload) > max_frame_bytes:
        raise FramingError("oversize", "length-delimited record exceeds max_frame_bytes")
    return prefix + payload


def decode_delimited[MessageT: Message](
    data: bytes, message_type: type[MessageT], max_frame_bytes: int
) -> tuple[MessageT, bytes]:
    if max_frame_bytes <= 0:
        raise FramingError("oversize", "max_frame_bytes must be positive")
    size, prefix_size = _decode_varint(data)
    record_size = prefix_size + size
    if record_size > max_frame_bytes:
        raise FramingError("oversize", "length-delimited record exceeds max_frame_bytes")
    if len(data) < record_size:
        raise FramingError("ambiguous_eof", "protobuf payload is truncated")
    message = message_type()
    try:
        message.ParseFromString(data[prefix_size:record_size])
    except Exception as exc:
        raise FramingError("malformed", "protobuf payload is malformed") from exc
    return message, data[record_size:]
