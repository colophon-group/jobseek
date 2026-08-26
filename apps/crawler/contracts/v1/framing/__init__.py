"""Candidate, version-neutral unsigned-varint record framing."""

from __future__ import annotations

from .codec import (
    MAX_UINT64,
    FramingError,
    decode_next,
    decode_record,
    encode_record,
    iter_records,
    read_record,
    uvarint_size,
)

__all__ = [
    "MAX_UINT64",
    "FramingError",
    "decode_next",
    "decode_record",
    "encode_record",
    "iter_records",
    "read_record",
    "uvarint_size",
]
