"""Installable crawler.runtime/v1 protobuf binding and framing codec."""

from __future__ import annotations

from src.runtime_contract.v1 import runtime_pb2
from src.runtime_contract.v1.framing import (
    FramingError,
    decode_message,
    decode_record,
    encode_message,
    encode_record,
    uvarint_size,
)

__all__ = [
    "FramingError",
    "decode_message",
    "decode_record",
    "encode_message",
    "encode_record",
    "runtime_pb2",
    "uvarint_size",
]
