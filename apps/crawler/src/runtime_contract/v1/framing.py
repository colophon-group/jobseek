"""Bounded unsigned-varint framing for crawler.runtime/v1 protobuf records."""

from __future__ import annotations

from google.protobuf.message import Message


class FramingError(ValueError):
    """A malformed, truncated, or oversized length-delimited record."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def uvarint_size(value: int) -> int:
    if value < 0 or value > 2**64 - 1:
        raise FramingError("framing", "unsigned varint value is outside uint64")
    return max(1, (value.bit_length() + 6) // 7)


def encode_record(payload: bytes, maximum: int) -> bytes:
    size = len(payload)
    prefix = bytearray()
    remaining = size
    while remaining >= 0x80:
        prefix.append((remaining & 0x7F) | 0x80)
        remaining >>= 7
    prefix.append(remaining)
    if maximum < len(prefix) or size > maximum - len(prefix):
        raise FramingError("frame_limit", "record exceeds max_frame_bytes")
    return bytes(prefix) + payload


def decode_record(data: bytes, maximum: int) -> bytes:
    value = 0
    prefix_size = 0
    for index, byte in enumerate(data[:10]):
        prefix_size = index + 1
        if index == 9 and byte > 1:
            raise FramingError("framing", "unsigned varint overflows uint64")
        value |= (byte & 0x7F) << (7 * index)
        if byte < 0x80:
            break
    else:
        raise FramingError("framing", "truncated or overlong unsigned varint")
    if prefix_size != uvarint_size(value):
        raise FramingError("framing", "unsigned varint uses a non-minimal encoding")
    if maximum < prefix_size or value > maximum - prefix_size:
        raise FramingError("frame_limit", "record exceeds max_frame_bytes")
    record_size = prefix_size + value
    if len(data) < record_size:
        raise FramingError("framing", "truncated length-delimited payload")
    if len(data) != record_size:
        raise FramingError("framing", "trailing bytes after one record")
    return data[prefix_size:]


def encode_message(message: Message, maximum: int) -> bytes:
    return encode_record(message.SerializeToString(deterministic=True), maximum)


def decode_message[MessageT: Message](
    data: bytes, message_type: type[MessageT], maximum: int
) -> MessageT:
    message = message_type()
    try:
        message.ParseFromString(decode_record(data, maximum))
    except FramingError:
        raise
    except Exception as exc:
        raise FramingError("framing", "protobuf payload is malformed") from exc
    return message
