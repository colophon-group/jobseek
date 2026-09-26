"""Cheap socket and route attestation for the resident B0 executor."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

PROTOCOL = "jobseek.lightpanda.executor/v1"
SOCKET_PATH = Path("/run/jobseek-lightpanda-executor/executor.sock")
FRAME_LIMIT = 1024


class HealthcheckError(RuntimeError):
    """The resident executor did not attest its current route."""


async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    value = 0
    for index in range(10):
        byte = (await reader.readexactly(1))[0]
        if index == 9 and (byte > 1 or byte & 0x80):
            raise HealthcheckError("health frame prefix overflow")
        value |= (byte & 0x7F) << (7 * index)
        if byte < 0x80:
            if max(1, (value.bit_length() + 6) // 7) != index + 1:
                raise HealthcheckError("health frame prefix is noncanonical")
            if value > FRAME_LIMIT - index - 1:
                raise HealthcheckError("health frame exceeds its limit")
            return await reader.readexactly(value)
    raise HealthcheckError("health frame prefix overflow")


def _frame(message: dict[str, object]) -> bytes:
    payload = json.dumps(
        message, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    length = len(payload)
    prefix = bytearray()
    while length >= 0x80:
        prefix.append((length & 0x7F) | 0x80)
        length >>= 7
    prefix.append(length)
    if len(prefix) + len(payload) > FRAME_LIMIT:
        raise HealthcheckError("health request exceeds its limit")
    return bytes(prefix) + payload


async def check(socket_path: Path = SOCKET_PATH) -> None:
    try:
        info = socket_path.lstat()
    except OSError as exc:
        raise HealthcheckError("executor socket is unavailable") from exc
    if (
        not stat.S_ISSOCK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
    ):
        raise HealthcheckError("executor socket metadata is invalid")

    shard_id = os.environ.get("LIGHTPANDA_B0_SHARD_ID", "")
    epoch_text = os.environ.get("LIGHTPANDA_B0_ROUTING_EPOCH", "")
    if shard_id != "lightpanda-b0" or not epoch_text.isascii() or not epoch_text.isdecimal():
        raise HealthcheckError("executor health route identity is invalid")
    routing_epoch = int(epoch_text)
    reader, writer = await asyncio.open_unix_connection(socket_path, limit=FRAME_LIMIT + 1)
    try:
        writer.write(
            _frame(
                {
                    "version": PROTOCOL,
                    "type": "attest_route",
                    "shard_id": shard_id,
                    "routing_epoch": routing_epoch,
                }
            )
        )
        await writer.drain()
        response = json.loads(await _read_frame(reader))
        if (
            not isinstance(response, dict)
            or set(response) != {"type", "shard_id", "routing_epoch"}
            or response["type"] != "route_attested"
            or response["shard_id"] != shard_id
            or response["routing_epoch"] != routing_epoch
        ):
            raise HealthcheckError("executor health route attestation failed")
    finally:
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(check())
