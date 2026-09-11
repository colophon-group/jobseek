"""Thin fail-closed client for the Go-owned B0 producer authority."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import socket
import stat
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

PROTOCOL: Final = "jobseek.lightpanda.producer/v1"
SOCKET: Final = Path("/run/jobseek-lightpanda-producer/control.sock")
PRODUCER_UID: Final = 10001
FRAME_LIMIT: Final = 256 * 1024
TIMEOUT: Final = 3.0
_FIELDS = frozenset(
    {
        "version",
        "outcome",
        "reason",
        "preparation_digest",
        "payload_sha256",
        "existing_state",
        "existing_payload_sha256",
        "activated",
        "cohort",
        "board_slugs",
    }
)


class ProducerClientError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProducerResult:
    outcome: str
    preparation_digest: str = ""
    payload_sha256: str = ""
    existing_state: str = ""
    existing_payload_sha256: str = ""
    activated: bool = False
    cohort: str = ""
    board_slugs: tuple[str, ...] = ()

    @property
    def is_legacy(self) -> bool:
        return self.outcome == "legacy"


def _canonical(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _encode_record(payload: bytes) -> bytes:
    try:
        from contracts.v1.framing import encode_record
    except ModuleNotFoundError:
        try:
            from jobseek_runtime_v1.framing import (  # pyright: ignore[reportMissingImports]
                encode_record,
            )
        except ModuleNotFoundError as exc:
            raise ProducerClientError("Go producer framing contract is unavailable") from exc
    return encode_record(payload, FRAME_LIMIT)


def _identity() -> tuple[int, int]:
    directory, info = SOCKET.parent.lstat(), SOCKET.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or stat.S_IMODE(directory.st_mode) != 0o700
        or directory.st_uid != PRODUCER_UID
        or not stat.S_ISSOCK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != PRODUCER_UID
    ):
        raise ProducerClientError("Go producer socket metadata is invalid")
    return info.st_dev, info.st_ino


def _peer_uid(raw: Any) -> int:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise ProducerClientError("SO_PEERCRED is unavailable")
    try:
        credentials = raw.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        return struct.unpack("3i", credentials)[1]
    except (AttributeError, OSError, struct.error) as exc:
        raise ProducerClientError("Go producer peer identity is unavailable") from exc


async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    value = 0
    for index in range(10):
        byte = (await reader.readexactly(1))[0]
        if index == 9 and (byte > 1 or byte & 0x80):
            break
        value |= (byte & 0x7F) << (7 * index)
        if byte < 0x80:
            if max(1, (value.bit_length() + 6) // 7) != index + 1:
                break
            if index + 1 > FRAME_LIMIT or value > FRAME_LIMIT - index - 1:
                raise ProducerClientError("Go producer frame exceeds its limit")
            return await reader.readexactly(value)
    raise ProducerClientError("Go producer frame prefix is invalid")


def _digest(value: str) -> bool:
    return len(value) == 64 and set(value) <= set("0123456789abcdef")


def _decode(payload: bytes) -> ProducerResult:
    try:
        value = json.loads(payload)
        canonical = _canonical(value)
    except (TypeError, ValueError) as exc:
        raise ProducerClientError("Go producer response is invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != _FIELDS or canonical != payload:
        raise ProducerClientError("Go producer response is not strict canonical JSON")
    if (
        value["version"] != PROTOCOL
        or not isinstance(value["activated"], bool)
        or not isinstance(value["cohort"], str)
        or not isinstance(value["board_slugs"], list)
        or not all(isinstance(slug, str) and 0 < len(slug) <= 128 for slug in value["board_slugs"])
    ):
        raise ProducerClientError("Go producer response identity is invalid")
    text = [value[key] for key in _FIELDS - {"version", "activated", "board_slugs"}]
    if not all(isinstance(item, str) for item in text):
        raise ProducerClientError("Go producer response fields are invalid")
    outcome, reason = value["outcome"], value["reason"]
    prep, payload_digest = value["preparation_digest"], value["payload_sha256"]
    state, existing = value["existing_state"], value["existing_payload_sha256"]
    if outcome == "error":
        if reason not in {"request_invalid", "authority_lost", "digest_mismatch"}:
            raise ProducerClientError("Go producer returned an unknown failure")
        raise ProducerClientError(f"Go producer refused authority: {reason}")
    if outcome == "legacy":
        valid = (
            reason == "literal_legacy"
            and not value["activated"]
            and not any((prep, payload_digest, state, existing, value["cohort"]))
            and not value["board_slugs"]
        )
    elif outcome == "manifest":
        valid = (
            reason == "manifest"
            and value["cohort"] in {"c1", "c4"}
            and value["board_slugs"] == sorted(set(value["board_slugs"]))
            and bool(value["board_slugs"])
            and not value["activated"]
            and not any((prep, payload_digest, state, existing))
        )
    else:
        reasons = (
            {"prepared"}
            if outcome == "prepared"
            else {
                "activated",
                "reactivated",
                "already_activated",
            }
        )
        valid = (
            outcome in {"prepared", "activated"}
            and reason in reasons
            and _digest(prep)
            and _digest(payload_digest)
            and state in {"", "ready", "inflight", "dead", "terminal"}
            and bool(state) == bool(existing)
            and (not existing or _digest(existing))
            and value["activated"] == (reason in {"activated", "reactivated"})
            and not value["cohort"]
            and not value["board_slugs"]
        )
    if not valid:
        raise ProducerClientError("Go producer decision is invalid")
    return ProducerResult(
        outcome,
        prep,
        payload_digest,
        state,
        existing,
        value["activated"],
        value["cohort"],
        tuple(value["board_slugs"]),
    )


async def _exchange(request: Mapping[str, object]) -> ProducerResult:
    try:
        async with asyncio.timeout(TIMEOUT):
            before = _identity()
            reader, writer = await asyncio.open_unix_connection(SOCKET)
            try:
                raw = writer.get_extra_info("socket")
                if raw is None or _peer_uid(raw) != PRODUCER_UID or _identity() != before:
                    raise ProducerClientError("Go producer socket identity is invalid")
                writer.write(_encode_record(_canonical(request)))
                await writer.drain()
                result = _decode(await _read_frame(reader))
                if await reader.read(1):
                    raise ProducerClientError("Go producer sent trailing bytes")
                return result
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
    except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
        raise ProducerClientError("Go producer authority is unavailable") from exc


def _enabled() -> bool:
    try:
        from src.config import settings
    except ModuleNotFoundError:
        return False
    return (
        settings.lightpanda_b0_producer_mode == "enabled"
        and settings.lightpanda_b0_producer_socket == str(SOCKET)
    )


async def request_manifest(cohort: str) -> ProducerResult:
    if not _enabled() or cohort not in {"c1", "c4"}:
        raise ProducerClientError("Go producer mode or cohort is invalid")
    return await _exchange(
        {
            "browser": False,
            "cohort": cohort,
            "config": {},
            "domain": "",
            "expected_digest": "",
            "next_scrape_at_ms": 0,
            "operation": "manifest",
            "operator_transfer": True,
            "posting_id": "",
            "version": PROTOCOL,
        }
    )


async def request_task(
    *,
    operation: str,
    domain: str,
    posting_id: str,
    next_scrape_at: float,
    config: Mapping[str, object],
    browser: bool,
    operator_transfer: bool = False,
    expected_digest: str = "",
) -> ProducerResult:
    if (
        not _enabled()
        or not math.isfinite(next_scrape_at)
        or next_scrape_at < 0
        or next_scrape_at > 9_999_999_999.999
    ):
        raise ProducerClientError("Go producer mode or schedule is invalid")
    request = {
        "browser": browser,
        "cohort": "",
        "config": {str(k): str(v) for k, v in config.items()},
        "domain": domain,
        "expected_digest": expected_digest,
        "next_scrape_at_ms": int(next_scrape_at * 1000),
        "operation": operation,
        "operator_transfer": operator_transfer,
        "posting_id": posting_id,
        "version": PROTOCOL,
    }
    return await _exchange(request)
