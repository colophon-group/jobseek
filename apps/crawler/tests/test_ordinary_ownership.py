from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import asyncpg
import pytest
import redis.asyncio as redis
from redis.exceptions import ResponseError

from src import redis_queue as rq
from src.config import settings
from src.ordinary_ownership import (
    EPOCH_BARRIER,
    LEASE_BARRIER,
    LegacyOwnership,
    OrdinaryOwnershipError,
    legacy_ownership_barrier,
    prepare_legacy_ownership,
)


def expectation(epoch: int = 7) -> tuple[LegacyOwnership, str]:
    # Private legacy boundary fixture. Native canonical eligibility is separately
    # proven by the Go real-board tests; this does not grant a native writer.
    payload = json.dumps(
        {
            "version": "jobseek.ordinary.ownership/v1",
            "routing_epoch": epoch,
            "source_revision": "a" * 40,
            "members": [
                {
                    "board_id": "00000000-0000-4000-8000-000000000001",
                    "domain": "greenhouse",
                    "kind": "monitor",
                    "worker": "simple",
                    "profile": "greenhouse.token-skip/v1",
                }
            ],
        },
        separators=(",", ":"),
    )
    expected = LegacyOwnership(
        hashlib.sha256(payload.encode()).hexdigest(),
        hashlib.sha1(payload.encode()).hexdigest(),
        "a" * 40,
        str(epoch),
    )
    return expected, payload


def install_settings(monkeypatch, expected: LegacyOwnership | None) -> None:
    for setting, field in (
        ("ordinary_ownership_plan_sha256", "plan_sha256"),
        ("ordinary_ownership_projection_sha1", "projection_sha1"),
        ("ordinary_ownership_source_revision", "source_revision"),
        ("ordinary_ownership_routing_epoch", "routing_epoch"),
    ):
        monkeypatch.setattr(settings, setting, getattr(expected, field) if expected else "")


def fake_pool(rows):
    conn = AsyncMock(spec=asyncpg.Connection)
    conn.fetchrow.side_effect = rows
    conn.fetchval.return_value = False
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


async def test_legacy_startup_requires_explicit_active_identity(monkeypatch):
    expected, payload = expectation()
    row = dict(
        plan_sha256=expected.plan_sha256,
        routing_epoch=7,
        source_revision=expected.source_revision,
        payload=payload,
    )
    install_settings(monkeypatch, None)
    pool, _ = fake_pool([None])
    assert await prepare_legacy_ownership(pool) is None
    pool, _ = fake_pool([row])
    with pytest.raises(OrdinaryOwnershipError, match="^ordinary ownership rejected$"):
        await prepare_legacy_ownership(pool)
    install_settings(monkeypatch, expected)
    pool, conn = fake_pool([row, dict(last_value=7, is_called=True), None])
    assert await prepare_legacy_ownership(pool) == expected
    assert conn.execute.await_args_list[0].args == (
        "SELECT pg_advisory_xact_lock_shared($1)",
        LEASE_BARRIER,
    )
    assert conn.execute.await_args_list[1].args == (
        "SELECT pg_advisory_xact_lock_shared($1)",
        EPOCH_BARRIER,
    )


@pytest.mark.parametrize("mode", ["missing", "epoch", "revision", "payload", "allocator"])
async def test_legacy_barrier_rejects_stale_identity_before_claim(mode):
    expected, payload = expectation()
    row = dict(
        plan_sha256=expected.plan_sha256,
        routing_epoch=7,
        source_revision=expected.source_revision,
        payload=payload,
    )
    allocator = dict(last_value=7, is_called=True)
    if mode == "missing":
        row = None
    elif mode == "epoch":
        row["routing_epoch"] = 8
    elif mode == "revision":
        row["source_revision"] = "b" * 40
    elif mode == "payload":
        row["payload"] += " "
    else:
        allocator["last_value"] = 8
    pool, _ = fake_pool([row, allocator])
    called = False
    with pytest.raises(OrdinaryOwnershipError):
        async with legacy_ownership_barrier(pool, expected):
            called = True
    assert not called


async def test_legacy_barrier_redacts_errors_and_propagates_cancellation():
    expected, _ = expectation()
    pool, conn = fake_pool([])
    conn.execute.side_effect = RuntimeError("secret-connection-string")
    with pytest.raises(OrdinaryOwnershipError) as failure:
        async with legacy_ownership_barrier(pool, expected):
            pytest.fail("failed DB attestation entered claim")
    assert "secret" not in str(failure.value)
    conn.execute.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        async with legacy_ownership_barrier(pool, expected):
            pytest.fail("cancelled attestation entered claim")


@asynccontextmanager
async def private_redis(monkeypatch):
    binary = shutil.which("redis-server")
    if binary is None:
        if os.environ.get("JOBSEEK_ORDINARY_QUEUE_REQUIRE_REDIS") == "1":
            pytest.fail("required owned Redis fixture unavailable")
        pytest.skip("owned Redis fixture unavailable")
    directory = Path(tempfile.mkdtemp(prefix="jol-", dir="/tmp"))
    directory.chmod(0o700)
    socket = directory / "redis.sock"
    log = (directory / "redis.log").open("wb")
    (directory / "redis.log").chmod(0o600)
    process = subprocess.Popen(
        [
            binary,
            "--port",
            "0",
            "--unixsocket",
            str(socket),
            "--unixsocketperm",
            "700",
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(directory),
        ],
        env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", "LANG": "C"},
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    client = redis.Redis(unix_socket_path=str(socket), decode_responses=True, protocol=2)
    try:
        async with asyncio.timeout(3):
            while True:
                if process.poll() is not None:
                    pytest.fail("owned Redis fixture startup failed")
                try:
                    if await client.ping():
                        break
                except (redis.ConnectionError, FileNotFoundError):
                    pass
                await asyncio.sleep(0.01)
        monkeypatch.setattr(rq, "get_redis", lambda: client)
        monkeypatch.setattr(rq, "_CLAIM_SHA", None)
        yield client
    finally:
        await client.aclose()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        log.close()
        shutil.rmtree(directory)


@asynccontextmanager
async def private_active_plan():
    dsn = os.environ.get("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL", "")
    if not dsn:
        if os.environ.get("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1":
            pytest.fail("required migrated owned PostgreSQL fixture unavailable")
        pytest.skip("migrated owned PostgreSQL fixture unavailable")
    url = urlparse(dsn)
    assert url.hostname in {"127.0.0.1", "localhost"}
    assert url.path.endswith("_ordinary_worker_test")
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=4,
        command_timeout=10,
        server_settings={"application_name": "jobseek:fixture:ordinary-legacy"},
    )
    expected = None
    try:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", LEASE_BARRIER)
            await conn.execute("SELECT pg_advisory_xact_lock($1)", EPOCH_BARRIER)
            assert not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM ordinary_worker_ownership_plan WHERE state='active')"
            )
            epoch = await conn.fetchval("SELECT nextval('public.lightpanda_b0_routing_epoch_seq')")
            expected, payload = expectation(epoch)
            # Private SQL fixture only; production still has no activation endpoint.
            await conn.execute(
                "INSERT INTO ordinary_worker_ownership_plan"
                "(plan_sha256,routing_epoch,source_revision,payload) VALUES($1,$2,$3,$4)",
                expected.plan_sha256,
                epoch,
                expected.source_revision,
                payload,
            )
            await conn.execute(
                "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1",
                expected.plan_sha256,
            )
        yield pool, expected, payload
    finally:
        if expected:
            await pool.execute(
                "UPDATE ordinary_worker_ownership_plan SET state='retired' "
                "WHERE plan_sha256=$1 AND state='active'",
                expected.plan_sha256,
            )
            await pool.execute(
                "DELETE FROM ordinary_worker_ownership_plan "
                "WHERE plan_sha256=$1 AND state='staged'",
                expected.plan_sha256,
            )
        await pool.close()


async def seed_private_queue(client):
    selected = "00000000-0000-4000-8000-000000000001"
    foreign = str(uuid.uuid4())
    await client.hset(
        "board:" + selected, mapping={"domain": "greenhouse", "crawler_type": "changed"}
    )
    await client.hset(
        "board:" + foreign, mapping={"domain": "greenhouse", "crawler_type": "fixture"}
    )
    await client.zadd("monitors_simple:greenhouse", {selected: 1, foreign: 2})
    await client.zadd("ready:simple:1", {"greenhouse": 1})
    return selected, foreign


async def queue_snapshot(client):
    # DUMP returns opaque bytes; capture neither credentials nor their contents.
    return {key: await client.dump(key) for key in await client.keys("*")}


async def test_real_legacy_attestation_projection_loss_and_retirement(monkeypatch):
    async with (
        private_active_plan() as (pool, expected, payload),
        private_redis(monkeypatch) as client,
    ):
        install_settings(monkeypatch, None)
        with pytest.raises(OrdinaryOwnershipError):
            await prepare_legacy_ownership(pool)
        install_settings(monkeypatch, expected)
        assert await prepare_legacy_ownership(pool) == expected
        await client.set("ordinary:ownership:active", payload)
        selected, foreign = await seed_private_queue(client)
        async with legacy_ownership_barrier(pool, expected):
            work = await rq.claim_work(ownership=expected)
        assert work is not None and work.board_work is not None
        assert work.board_work.board_id == foreign
        assert await client.zscore("monitors_simple:greenhouse", selected) == 1

        # Lose this owned private database completely, then recreate ordinary
        # queue data without ownership. The planned caller must still fail closed.
        await client.flushdb()
        await seed_private_queue(client)
        before = await queue_snapshot(client)
        with pytest.raises(OrdinaryOwnershipError):
            async with legacy_ownership_barrier(pool, expected):
                await rq.claim_work(ownership=expected)
        assert before == await queue_snapshot(client)
        await client.set("ordinary:ownership:active", payload)
        await pool.execute(
            "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1",
            expected.plan_sha256,
        )
        before = await queue_snapshot(client)
        with pytest.raises(OrdinaryOwnershipError):
            async with legacy_ownership_barrier(pool, expected):
                await rq.claim_work(ownership=expected)
        assert before == await queue_snapshot(client)


async def test_real_projection_blocks_unaware_legacy_claim(monkeypatch):
    async with private_redis(monkeypatch) as client:
        _, payload = expectation()
        await client.set("ordinary:ownership:active", payload)
        await seed_private_queue(client)
        before = await queue_snapshot(client)
        with pytest.raises(ResponseError, match="ordinary ownership rejected"):
            await rq.claim_work()
        assert before == await queue_snapshot(client)
