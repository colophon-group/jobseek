"""Go-supervisor to DB-only Python executor boundary contracts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import struct
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from jobseek_runtime_v1 import runtime_pb2

import src.lightpanda.executor as executor
from src.lightpanda.claimant import ScheduleState
from src.lightpanda.client import _encode_record
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda_queue import LightpandaB0Task, RouteIdentity


def _task(*, owner: str = "go") -> tuple[LightpandaB0Task, dict[str, object]]:
    config: dict[str, object] = {
        "browser_backend": "lightpanda",
        "render": True,
        "routing_revision": "go-b0-1",
        "timeout": 5_000,
        "wait": "load",
        "wait_fallback": None,
    }
    assignment = resolve_render_assignment("json-ld", config)
    assert assignment is not None
    return (
        LightpandaB0Task.create(
            task_id=str(uuid.uuid4()),
            board_id="board-1",
            source_url="https://jobs.example.test/posting",
            policy_key="lightpanda-b0-v1",
            domain="jobs.example.test",
            route=RouteIdentity(shard_id="lightpanda-b0", routing_epoch=7, engine_owner=owner),
            config_revision=3,
            initial_ready_at_ms=1,
            assignment=assignment,
        ),
        config,
    )


def _result() -> Any:
    runtime = cast(Any, runtime_pb2)
    body = b'<script type="application/ld+json">{"@type":"JobPosting","title":"Go"}</script>'
    digest = hashlib.sha256(body).hexdigest()
    return runtime.BrowserResult(
        contract_version="crawler.runtime/v1",
        backend=runtime.BROWSER_BACKEND_LIGHTPANDA,
        success=runtime.BrowserSuccess(
            final_url="https://jobs.example.test/posting",
            status=200,
            html=runtime.ChunkManifest(
                chunks=[
                    runtime.DataChunk(
                        sequence=0,
                        size_bytes=len(body),
                        sha256=digest,
                        inline_body=body,
                    )
                ],
                total_size_bytes=len(body),
                total_sha256=digest,
                complete=True,
            ),
        ),
    )


def _request(task: LightpandaB0Task, result: Any | None = None) -> dict[str, object]:
    result = (result if result is not None else _result()).SerializeToString(deterministic=True)
    return {
        "version": executor.PROTOCOL,
        "task_payload": task.payload,
        "payload_sha256": task.payload_sha256,
        "claim_token": "7:11",
        "lease_until_ms": 10_000,
        "browser_result": base64.b64encode(result).decode("ascii"),
    }


def _frame(message: dict[str, object]) -> bytes:
    payload = json.dumps(message, separators=(",", ":"), sort_keys=True).encode("ascii")
    return _encode_record(payload, executor.FRAME_LIMIT)


def test_decode_task_accepts_only_canonical_go_owned_identity() -> None:
    task, _config = _task()
    decoded, lease, result = executor._decode_task(_request(task))

    assert decoded == task
    assert lease.claim_token == "7:11"
    assert executor._decode_result(result).WhichOneof("outcome") == "success"

    python_task, _config = _task(owner="python")
    with pytest.raises(executor.ExecutorProtocolError, match="Go-owned"):
        executor._decode_task(_request(python_task))
    changed = _request(task)
    changed["browser_result"] = f"{changed['browser_result']}="
    with pytest.raises(executor.ExecutorProtocolError, match="base64"):
        executor._decode_task(changed)


async def test_executor_reads_mutable_detail_inputs_from_current_postgres() -> None:
    task, config = _task()

    class Pool:
        async def fetchrow(self, query: str, posting_id: str) -> dict[str, object]:
            assert "description_r2_hash" in query
            assert posting_id == task.task_id
            return {
                "board_id": task.board_id,
                "source_url": task.source_url,
                "description_r2_hash": 0,
                "is_active": True,
                "next_scrape_at": datetime.now(UTC),
                "board_slug": "browser-use-careers",
                "is_enabled": True,
                "board_status": "active",
                "metadata": {"scraper_type": "json-ld", "scraper_config": config},
                "crawler_type": "dom",
                "scraper_needs_browser": True,
                "scrape_interval_hours": 37,
            }

    detail = await executor._read_current_detail(Pool(), task)

    assert detail == executor._CurrentDetail(
        description_r2_hash=0,
        scrape_interval_hours=37,
        schedulable=True,
    )


async def test_executor_authorizes_before_db_and_commits_before_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, config = _task()
    events: list[str] = []

    async def activate(_pool: object, _fence: object) -> None:
        events.append("activate")

    async def process(*args: object, **kwargs: object) -> None:
        events.append("process")
        item = cast(Any, args[0])
        assert item.description_r2_hash == -123
        assert kwargs["scrape_interval"] == 12
        scrape_runtime = cast(Any, kwargs["scrape_runtime"])
        content = await scrape_runtime.scrape(
            task.source_url,
            "json-ld",
            config,
            AsyncMock(),
        )
        assert content.title == "Go"

    async def schedule(_pool: object, posting_id: str) -> ScheduleState:
        assert posting_id == task.task_id
        events.append("schedule")
        return ScheduleState(
            is_active=True,
            next_scrape_at=datetime(2026, 1, 2, 3, 4, 5, 678900, tzinfo=UTC),
        )

    monkeypatch.setattr(executor, "activate_write_fence", activate)
    monkeypatch.setattr(
        executor,
        "_read_current_detail",
        AsyncMock(return_value=executor._CurrentDetail(-123, 12, True)),
    )
    monkeypatch.setattr(executor, "_process_one_scrape", process)
    monkeypatch.setattr(executor, "read_authoritative_schedule", schedule)
    completed: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await executor._execute(reader, writer, object())
        except BaseException as exc:  # pragma: no cover - surfaced by the future
            completed.set_exception(exc)
        else:
            completed.set_result(None)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(_frame(_request(task)))
        await writer.drain()
        authorize = executor._object(
            await executor._read_frame(reader),
            {"type", "claim_token", "lease_until_ms"},
        )
        assert authorize == {
            "type": "authorize",
            "claim_token": "7:11",
            "lease_until_ms": 10_000,
        }
        assert events == []
        writer.write(
            _frame({"type": "authorized", "claim_token": "7:11", "lease_until_ms": 70_000})
        )
        await writer.drain()
        committed_payload = await executor._read_frame(reader)
        committed = json.loads(committed_payload)
        assert events == ["activate", "process", "schedule"]
        assert committed["type"] == "committed"
        assert committed["lease_until_ms"] == 70_000
        assert committed["next_ready_at_ms"] == 1_767_323_045_678
        await completed
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


async def test_typed_render_failure_reaches_existing_scrape_failure_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, config = _task()
    runtime = cast(Any, runtime_pb2)
    failed = runtime.BrowserResult(
        contract_version="crawler.runtime/v1",
        backend=runtime.BROWSER_BACKEND_LIGHTPANDA,
        error=runtime.BrowserFailure(
            error=runtime.RuntimeError(
                code=runtime.ERROR_CODE_TIMEOUT,
                disposition=runtime.ERROR_DISPOSITION_RETRY_POLICY,
                message="timeout",
            )
        ),
    )
    processed: list[bool] = []

    async def process(*args: object, **kwargs: object) -> tuple[bool, float]:
        del args
        scrape_runtime = cast(Any, kwargs["scrape_runtime"])
        with pytest.raises(Exception, match="did not succeed"):
            await scrape_runtime.scrape(task.source_url, "json-ld", config, AsyncMock())
        processed.append(True)
        return False, 0.0

    monkeypatch.setattr(executor, "activate_write_fence", AsyncMock())
    monkeypatch.setattr(
        executor,
        "_read_current_detail",
        AsyncMock(return_value=executor._CurrentDetail(-123, 12, True)),
    )
    monkeypatch.setattr(executor, "_process_one_scrape", process)
    monkeypatch.setattr(executor, "read_authoritative_schedule", AsyncMock(return_value=None))

    reader = asyncio.StreamReader()
    reader.feed_data(_frame(_request(task, failed)))
    reader.feed_data(
        _frame({"type": "authorized", "claim_token": "7:11", "lease_until_ms": 70_000})
    )
    reader.feed_eof()

    class Writer:
        def __init__(self) -> None:
            self.payload = bytearray()

        def write(self, data: bytes) -> None:
            self.payload.extend(data)

        async def drain(self) -> None:
            return None

    writer = Writer()
    await executor._execute(reader, cast(Any, writer), object())
    assert processed == [True]


async def test_postgres_fence_rejection_is_a_typed_authority_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, _config = _task()
    monkeypatch.setattr(
        executor,
        "activate_write_fence",
        AsyncMock(side_effect=executor.LightpandaWriteFenceRejected("missing")),
    )
    reader = asyncio.StreamReader()
    reader.feed_data(_frame(_request(task)))
    reader.feed_data(
        _frame({"type": "authorized", "claim_token": "7:11", "lease_until_ms": 70_000})
    )
    reader.feed_eof()

    class Writer:
        def __init__(self) -> None:
            self.payload = bytearray()

        def write(self, data: bytes) -> None:
            self.payload.extend(data)

        async def drain(self) -> None:
            return None

    writer = Writer()
    await executor._execute(reader, cast(Any, writer), object())
    responses = asyncio.StreamReader()
    responses.feed_data(writer.payload)
    responses.feed_eof()
    assert json.loads(await executor._read_frame(responses)) == {
        "type": "authorize",
        "claim_token": "7:11",
        "lease_until_ms": 10_000,
    }
    assert json.loads(await executor._read_frame(responses)) == {
        "type": "authority_lost",
        "error": "postgres_write_fence_rejected",
    }


async def test_executor_refuses_pool_above_one_before_database_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIGHTPANDA_B0_EXECUTOR_MODE", "enabled")
    monkeypatch.setenv("CRAWLER_DB_POOL_MIN", "1")
    monkeypatch.setenv("CRAWLER_DB_POOL_MAX", "2")
    create_pool = AsyncMock()
    monkeypatch.setattr(executor, "create_local_pool", create_pool)

    with pytest.raises(executor.ExecutorProtocolError, match="exactly one"):
        await executor._serve()

    create_pool.assert_not_awaited()


def test_executor_socket_directory_and_socket_must_be_private_owned(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path
    cleanup = tempfile.TemporaryDirectory(prefix="lp-exec-", dir="/tmp")
    directory = executor.Path(cleanup.name)
    directory.chmod(0o700)
    executor._validate_socket_directory(directory)
    directory.chmod(0o755)
    with pytest.raises(executor.ExecutorProtocolError, match="not private"):
        executor._validate_socket_directory(directory)

    directory.chmod(0o700)
    path = directory / "executor.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        path.chmod(0o600)
        executor._validate_socket(path)
        path.chmod(0o666)
        with pytest.raises(executor.ExecutorProtocolError, match="metadata"):
            executor._validate_socket(path)
        path.chmod(0o600)
    finally:
        listener.close()

    original_lstat = executor.Path.lstat

    def foreign_owner(target: Any) -> Any:
        info = original_lstat(target)
        values = list(info)
        values[4] = os.getuid() + 1
        return os.stat_result(values)

    monkeypatch.setattr(executor.Path, "lstat", foreign_owner)
    with pytest.raises(executor.ExecutorProtocolError, match="metadata"):
        executor._validate_socket(path)
    cleanup.cleanup()


def test_executor_requires_exact_peer_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    class Peer:
        def getsockopt(self, level: int, option: int, size: int) -> bytes:
            assert level == socket.SOL_SOCKET
            assert size == struct.calcsize("3i")
            return struct.pack("3i", 42, os.getuid(), os.getgid())

    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    assert executor._peer_uid(cast(Any, Peer())) == os.getuid()
    monkeypatch.delattr(socket, "SO_PEERCRED")
    with pytest.raises(executor.ExecutorProtocolError, match="unavailable"):
        executor._peer_uid(cast(Any, Peer()))
