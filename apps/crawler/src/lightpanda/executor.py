"""DB-only local executor for Go-owned Lightpanda B0 leases.

This resident service has no Redis or renderer credentials. Go supplies one
already-rendered runtime-v1 result, then keeps the Redis lease mutex held from
the ``authorized`` response through the ``committed`` acknowledgement.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
import os
import signal
import socket
import stat
import struct
import sys
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

import httpx

import src.queries.lookups as lightpanda_lookups
from src.db import close_local_pool, create_local_pool
from src.lightpanda.claimant import _epoch_milliseconds, read_authoritative_schedule
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda.runtime import LightpandaB0ScrapeRuntime
from src.lightpanda.write_fence import (
    LightpandaWriteFence,
    LightpandaWriteFenceRejected,
    activate_write_fence,
)
from src.lightpanda_queue import Lease, LightpandaB0Task, RouteIdentity
from src.processing.scrape import ScrapeItem, _process_one_scrape
from src.runtime.config import BoardRuntimeConfig

PROTOCOL: Final = "jobseek.lightpanda.executor/v1"
SOCKET_PATH: Final = Path("/run/jobseek-lightpanda-executor/executor.sock")
FRAME_LIMIT: Final = 3 * 1024 * 1024
CAPACITY: Final = 4
AUTHORIZATION_TIMEOUT: Final = 5.0
COMMIT_TIMEOUT: Final = 15.0
_REQUEST_FIELDS: Final = {
    "version",
    "task_payload",
    "payload_sha256",
    "claim_token",
    "lease_until_ms",
    "browser_result",
}
_FORBIDDEN_ENV: Final = {
    "REDIS_URL",
    "LIGHTPANDA_B0_CA_CERTIFICATE",
    "LIGHTPANDA_B0_CLIENT_CERTIFICATE",
    "LIGHTPANDA_B0_CLIENT_PRIVATE_KEY",
    "LIGHTPANDA_B0_CA_SHA256_FILE",
    "LIGHTPANDA_B0_SERVER_LEAF_SHA256_FILE",
    "LIGHTPANDA_B0_SERVER_SPKI_SHA256_FILE",
}

_CURRENT_DETAIL_SQL: Final = """
SELECT jp.board_id::text AS board_id,
       jp.source_url,
       jp.description_r2_hash,
       jp.is_active,
       jp.next_scrape_at,
       jb.board_slug,
       jb.is_enabled,
       jb.board_status,
       jb.metadata,
       jb.crawler_type,
       jb.scraper_needs_browser,
       jb.scrape_interval_hours
FROM job_posting AS jp
JOIN job_board AS jb ON jb.id = jp.board_id
WHERE jp.id = $1::uuid
"""


class ExecutorProtocolError(RuntimeError):
    """The local executor request or authority conversation is invalid."""


@dataclass(frozen=True, slots=True)
class _CurrentDetail:
    description_r2_hash: int | None
    scrape_interval_hours: int
    schedulable: bool


async def _read_current_detail(pool: Any, task: LightpandaB0Task) -> _CurrentDetail | None:
    """Read mutable detail inputs from PostgreSQL at the fenced attempt boundary."""

    row = await pool.fetchrow(_CURRENT_DETAIL_SQL, task.task_id)
    if row is None:
        return None
    if row.get("is_active") is not True or row.get("next_scrape_at") is None:
        return _CurrentDetail(
            description_r2_hash=None,
            scrape_interval_hours=1,
            schedulable=False,
        )
    snapshot = BoardRuntimeConfig.from_mapping(row)
    parser_config = snapshot.scraper_config
    if parser_config is None:
        raise ExecutorProtocolError("current PostgreSQL parser assignment is missing")
    try:
        assignment = resolve_render_assignment(
            str(snapshot.metadata.get("scraper_type", "")),
            parser_config,
            scraper_step=0,
        )
    except ValueError as exc:
        raise ExecutorProtocolError("current PostgreSQL parser assignment is invalid") from exc
    if (
        str(row.get("board_id") or "") != task.board_id
        or str(row.get("source_url") or "") != task.source_url
        or row.get("is_enabled") is not True
        or str(row.get("board_status") or "") != "active"
        or not snapshot.scraper_needs_browser
        or assignment != task.assignment
    ):
        raise ExecutorProtocolError("current PostgreSQL task identity left the admitted B0 lane")
    raw_hash = row.get("description_r2_hash")
    if raw_hash is not None and (
        isinstance(raw_hash, bool)
        or not isinstance(raw_hash, int)
        or not -(2**63) <= raw_hash < 2**63
    ):
        raise ExecutorProtocolError("current PostgreSQL description hash is invalid")
    interval = snapshot.scrape_interval_hours
    if isinstance(interval, bool) or not 1 <= interval <= 8_760:
        raise ExecutorProtocolError("current PostgreSQL scrape interval is invalid")
    return _CurrentDetail(
        description_r2_hash=raw_hash,
        scrape_interval_hours=interval,
        schedulable=True,
    )


class _RejectingOriginTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        del request
        raise ExecutorProtocolError("origin HTTP is forbidden in the DB-only executor")


class _PreRenderedReservation:
    """One-shot result source satisfying the existing frozen B0 runtime."""

    def __init__(self, task: LightpandaB0Task, result: Any) -> None:
        self._task = task
        self._result = result
        self._used = False

    async def execute(self, task: LightpandaB0Task) -> Any:
        if self._used or task != self._task:
            raise ExecutorProtocolError("pre-rendered reservation identity changed")
        self._used = True
        return self._result


async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    prefix = bytearray()
    value = 0
    for index in range(10):
        byte = (await reader.readexactly(1))[0]
        prefix.append(byte)
        if index == 9 and (byte > 1 or byte & 0x80):
            raise ExecutorProtocolError("frame prefix overflow")
        value |= (byte & 0x7F) << (7 * index)
        if byte < 0x80:
            if max(1, (value.bit_length() + 6) // 7) != len(prefix):
                raise ExecutorProtocolError("frame prefix is noncanonical")
            if len(prefix) > FRAME_LIMIT or value > FRAME_LIMIT - len(prefix):
                raise ExecutorProtocolError("frame exceeds its limit")
            return await reader.readexactly(value)
    raise ExecutorProtocolError("frame prefix overflow")


async def _write_message(writer: asyncio.StreamWriter, message: Mapping[str, object]) -> None:
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
        raise ExecutorProtocolError("outgoing frame exceeds its limit")
    writer.write(prefix + payload)
    await writer.drain()


def _object(payload: bytes, fields: set[str]) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutorProtocolError("message is not valid JSON") from exc
    if not isinstance(value, dict) or set(value) != fields:
        raise ExecutorProtocolError("message fields are invalid")
    return value


def _decode_task(request: Mapping[str, Any]) -> tuple[LightpandaB0Task, Lease, bytes]:
    payload = request["task_payload"]
    digest = request["payload_sha256"]
    claim_token = request["claim_token"]
    lease_until_ms = request["lease_until_ms"]
    encoded_result = request["browser_result"]
    if (
        request["version"] != PROTOCOL
        or not isinstance(payload, str)
        or not isinstance(digest, str)
        or hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest
        or not isinstance(claim_token, str)
        or isinstance(lease_until_ms, bool)
        or not isinstance(lease_until_ms, int)
        or not isinstance(encoded_result, str)
    ):
        raise ExecutorProtocolError("executor request identity is invalid")
    try:
        envelope = json.loads(payload)
        parser_config = envelope["parser_config"]
        assignment = resolve_render_assignment("json-ld", parser_config, scraper_step=0)
        route = RouteIdentity(
            shard_id=envelope["shard_id"],
            routing_epoch=envelope["routing_epoch"],
            engine_owner=envelope["engine_owner"],
        )
        if assignment is None:
            raise ValueError("missing assignment")
        task = LightpandaB0Task.create(
            task_id=envelope["task_id"],
            board_id=envelope["board_id"],
            source_url=envelope["source_url"],
            policy_key=envelope["policy_key"],
            domain=envelope["domain"],
            route=route,
            config_revision=envelope["config_revision"],
            initial_ready_at_ms=envelope["initial_ready_at_ms"],
            assignment=assignment,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ExecutorProtocolError("task payload is invalid") from exc
    if task.payload != payload or task.payload_sha256 != digest or route.engine_owner != "go":
        raise ExecutorProtocolError("task payload is not canonical Go-owned B0")
    lease = Lease(task=task, claim_token=claim_token, lease_until_ms=lease_until_ms)
    try:
        result = base64.b64decode(encoded_result, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ExecutorProtocolError("browser result is not canonical base64") from exc
    if (
        base64.b64encode(result).decode("ascii") != encoded_result
        or not 0 < len(result) <= 2 * 1024 * 1024
    ):
        raise ExecutorProtocolError("browser result is invalid")
    return task, lease, result


def _decode_result(payload: bytes) -> Any:
    from google.protobuf.message import DecodeError
    from jobseek_runtime_v1 import runtime_pb2

    runtime = cast(Any, runtime_pb2)
    result = runtime.BrowserResult()
    try:
        result.ParseFromString(payload)
    except DecodeError as exc:
        raise ExecutorProtocolError("browser result is not runtime-v1") from exc
    without_unknown = runtime.BrowserResult()
    without_unknown.CopyFrom(result)
    without_unknown.DiscardUnknownFields()
    if (
        without_unknown.SerializeToString(deterministic=True) != payload
        or result.contract_version != "crawler.runtime/v1"
        or result.backend != runtime.BROWSER_BACKEND_LIGHTPANDA
        or result.WhichOneof("outcome") not in {"success", "error", "unsupported"}
    ):
        raise ExecutorProtocolError("browser result violates runtime-v1")
    return result


def _validate_socket_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ExecutorProtocolError("executor socket directory is unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
    ):
        raise ExecutorProtocolError("executor socket directory is not private")


def _validate_socket(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ExecutorProtocolError("executor socket is unavailable") from exc
    if (
        not stat.S_ISSOCK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
    ):
        raise ExecutorProtocolError("executor socket metadata is invalid")


def _peer_uid(raw_socket: socket.socket) -> int:
    peer_credential_option = getattr(socket, "SO_PEERCRED", None)
    if peer_credential_option is None:
        raise ExecutorProtocolError("SO_PEERCRED is unavailable")
    try:
        credentials = raw_socket.getsockopt(
            socket.SOL_SOCKET, peer_credential_option, struct.calcsize("3i")
        )
        _pid, uid, _gid = struct.unpack("3i", credentials)
    except (OSError, struct.error) as exc:
        raise ExecutorProtocolError("executor peer credentials are unavailable") from exc
    return uid


@contextlib.asynccontextmanager
async def _authorized_guard() -> AsyncIterator[None]:
    yield


async def _execute(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    pool: Any,
) -> None:
    request = _object(await _read_frame(reader), _REQUEST_FIELDS)
    task, lease, result_payload = _decode_task(request)
    result = _decode_result(result_payload)
    await _write_message(
        writer,
        {
            "type": "authorize",
            "claim_token": lease.claim_token,
            "lease_until_ms": lease.lease_until_ms,
        },
    )
    async with asyncio.timeout(AUTHORIZATION_TIMEOUT):
        authorization = _object(
            await _read_frame(reader), {"type", "claim_token", "lease_until_ms"}
        )
    if (
        authorization["type"] != "authorized"
        or authorization["claim_token"] != lease.claim_token
        or isinstance(authorization["lease_until_ms"], bool)
        or not isinstance(authorization["lease_until_ms"], int)
        or authorization["lease_until_ms"] <= lease.lease_until_ms
    ):
        raise ExecutorProtocolError("write authorization is invalid")
    lease.lease_until_ms = authorization["lease_until_ms"]
    fence = LightpandaWriteFence.from_lease(lease)
    parser_config = json.loads(task.payload)["parser_config"]
    try:
        async with asyncio.timeout(COMMIT_TIMEOUT):
            await activate_write_fence(pool, fence)
            detail = await _read_current_detail(pool, task)
            if detail is not None and detail.schedulable:
                async with httpx.AsyncClient(
                    transport=_RejectingOriginTransport()
                ) as no_origin_http:
                    await _process_one_scrape(
                        ScrapeItem(
                            job_posting_id=task.task_id,
                            url=task.source_url,
                            board_id=task.board_id,
                            description_r2_hash=detail.description_r2_hash,
                        ),
                        pool,
                        no_origin_http,
                        "json-ld",
                        parser_config,
                        scrape_step=0,
                        scrape_interval=detail.scrape_interval_hours,
                        scrape_runtime=LightpandaB0ScrapeRuntime(
                            task,
                            _PreRenderedReservation(task, result),  # type: ignore[arg-type]
                        ),
                        write_fence=fence,
                        recover_browser_target=False,
                        authority_guard=_authorized_guard,
                        lookup_provider=lightpanda_lookups,
                    )
            schedule = await read_authoritative_schedule(pool, task.task_id)
    except LightpandaWriteFenceRejected:
        await _write_message(
            writer,
            {
                "type": "authority_lost",
                "error": "postgres_write_fence_rejected",
            },
        )
        return
    response: dict[str, object] = {
        "type": "committed",
        "claim_token": lease.claim_token,
        "lease_until_ms": lease.lease_until_ms,
    }
    if schedule is not None and schedule.is_active and schedule.next_scrape_at is not None:
        response["next_ready_at_ms"] = _epoch_milliseconds(schedule.next_scrape_at)
    await _write_message(writer, response)


async def _serve() -> None:
    if os.environ.get("LIGHTPANDA_B0_EXECUTOR_MODE") != "enabled":
        raise ExecutorProtocolError("executor mode must be exactly enabled")
    if any(os.environ.get(name) for name in _FORBIDDEN_ENV):
        raise ExecutorProtocolError("DB-only executor received a forbidden authority credential")
    if os.environ.get("CRAWLER_DB_POOL_MIN") != "1" or os.environ.get("CRAWLER_DB_POOL_MAX") != "1":
        raise ExecutorProtocolError("executor database pool must be exactly one connection")
    configured_socket = Path(os.environ.get("LIGHTPANDA_B0_EXECUTOR_SOCKET", str(SOCKET_PATH)))
    if configured_socket != SOCKET_PATH:
        raise ExecutorProtocolError("executor socket path must be exact")
    _validate_socket_directory(configured_socket.parent)
    if configured_socket.exists() or configured_socket.is_symlink():
        _validate_socket(configured_socket)
        configured_socket.unlink()
    if getattr(socket, "SO_PEERCRED", None) is None:
        raise ExecutorProtocolError("SO_PEERCRED is required")
    pool = await create_local_pool()
    active = 0
    handlers: set[asyncio.Task[None]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal active
        handler = asyncio.current_task()
        if handler is None:
            writer.close()
            return
        handlers.add(handler)
        raw_socket = writer.get_extra_info("socket")
        try:
            accepted_peer = raw_socket is not None and _peer_uid(raw_socket) == os.getuid()
        except ExecutorProtocolError:
            accepted_peer = False
        if not accepted_peer:
            handlers.discard(handler)
            writer.close()
            await writer.wait_closed()
            return
        if active >= CAPACITY:
            handlers.discard(handler)
            writer.close()
            await writer.wait_closed()
            return
        active += 1
        try:
            await _execute(reader, writer, pool)
        except (Exception, asyncio.CancelledError):
            with contextlib.suppress(Exception):
                await _write_message(writer, {"type": "error", "error": "executor_failed"})
        finally:
            active -= 1
            handlers.discard(handler)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_unix_server(
        handle, path=configured_socket, limit=FRAME_LIMIT + 1, backlog=CAPACITY
    )
    os.chmod(configured_socket, 0o600)
    _validate_socket(configured_socket)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        async with server:
            await stop.wait()
    finally:
        server.close()
        await server.wait_closed()
        shutdown_failed = False
        if handlers:
            done, pending = await asyncio.wait(handlers, timeout=COMMIT_TIMEOUT)
            del done
            if pending:
                shutdown_failed = True
                for handler in pending:
                    handler.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        configured_socket.unlink(missing_ok=True)
        await close_local_pool()
        if shutdown_failed:
            raise ExecutorProtocolError("executor handlers exceeded shutdown grace")


def main() -> None:
    if sys.argv[1:] == ["--healthcheck"]:
        _validate_socket(SOCKET_PATH)
        return
    if sys.argv[1:]:
        raise SystemExit("usage: python -m src.lightpanda.executor [--healthcheck]")
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
