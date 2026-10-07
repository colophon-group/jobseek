from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
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
    ownership_projection,
    prepare_legacy_ownership,
)


def expectation(
    epoch: int = 7, *, details: bool = False, jsonld: bool = False, api: str = ""
) -> tuple[LegacyOwnership, str]:
    # Private legacy boundary fixture. Native canonical eligibility is separately
    # proven by the Go real-board tests; this does not grant a native writer.
    payload = json.dumps(
        {
            "version": "jobseek.ordinary.ownership/v1",
            "routing_epoch": epoch,
            "source_revision": "a" * 40,
            "routing_projection": "jobseek.ordinary.ownership-projection/v1",
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
    if details:
        doc = json.loads(payload)
        doc["details"] = [
            {
                "board_id": doc["members"][0]["board_id"],
                "domain": "fixture.wd1.myworkdayjobs.com",
                "profile": "workday.cxs-detail/v1",
                "worker": "simple",
            }
        ]
        payload = json.dumps(doc, separators=(",", ":"))
    if jsonld or api:
        doc = json.loads(payload)
        doc["details"] = [
            {
                "board_id": "00000000-0000-4000-8000-000000000098",
                "domain": "*",
                "profile": ("dom.direct-detail/v1" if api == "dom" else f"{api}.api-detail/v1")
                if api
                else "jsonld.direct-detail/v1",
                "worker": "simple",
                "company_id": "00000000-0000-4000-8000-000000000002",
                "effective_config_sha256": "a" * 64,
                "config": {"crawler_type": "dom", "metadata": '{"scraper_type":"json-ld"}'},
            }
        ]
        payload = json.dumps(doc, separators=(",", ":"))
    expected = LegacyOwnership(
        hashlib.sha256(payload.encode()).hexdigest(),
        hashlib.sha1(ownership_projection(payload).encode()).hexdigest(),
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
async def private_active_plan(*, details: bool = False, jsonld: bool = False, api: str = ""):
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
            expected, payload = expectation(epoch, details=details, jsonld=jsonld, api=api)
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
        await client.set("ordinary:ownership:active", ownership_projection(payload))
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
        await client.set("ordinary:ownership:active", ownership_projection(payload))
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
        await client.set("ordinary:ownership:active", ownership_projection(payload))
        await seed_private_queue(client)
        before = await queue_snapshot(client)
        with pytest.raises(ResponseError, match="ordinary ownership rejected"):
            await rq.claim_work()
        assert before == await queue_snapshot(client)


def test_compact_projection_matches_frozen_actual_python_codec():
    fixture = (
        Path(__file__).parents[1] / "go/ordinary-queue/testdata/ownership_projection_python.json"
    )
    capture = json.loads(fixture.read_text())
    for case in capture["cases"]:
        projection = ownership_projection(case["payload"])
        assert projection == case["projection"]
        assert hashlib.sha1(projection.encode()).hexdigest() == case["projection_sha1"]


def test_retained_legacy_plan_keeps_original_projection():
    _, payload = expectation()
    doc = json.loads(payload)
    del doc["routing_projection"]
    original = json.dumps(doc, separators=(",", ":"))
    assert ownership_projection(original) == original


def test_detail_projection_retains_actual_domain_and_rejects_foreign_members():
    _, payload = expectation(details=True)
    projection = json.loads(ownership_projection(payload))
    board = "00000000-0000-4000-8000-000000000001"
    assert projection["members"] == {board: "greenhouse"}
    assert projection["details"] == {board: "fixture.wd1.myworkdayjobs.com"}
    for change in ("foreign", "duplicate", "profile", "browser", "empty"):
        doc = json.loads(payload)
        if change == "foreign":
            doc["details"][0]["board_id"] = str(uuid.uuid4())
        elif change == "duplicate":
            doc["details"].append(doc["details"][0])
        elif change == "profile":
            doc["details"][0]["profile"] = "unknown"
        elif change == "browser":
            doc["details"][0]["worker"] = "browser"
        else:
            doc["details"] = []
        with pytest.raises(OrdinaryOwnershipError):
            ownership_projection(json.dumps(doc, separators=(",", ":")))


@pytest.mark.parametrize("profile", ["nextdata.rendered-items/v1", "nextdata.rendered-urls/v1"])
def test_nextdata_rendered_monitor_projection_preserves_browser_exclusion(profile):
    _, payload = expectation(details=True)
    doc = json.loads(payload)
    doc["members"][0]["profile"] = profile
    doc["members"][0]["worker"] = "browser"
    projected = json.loads(ownership_projection(json.dumps(doc, separators=(",", ":"))))
    assert projected["members"] == {doc["members"][0]["board_id"]: "greenhouse"}
    doc["members"][0]["worker"] = "simple"
    with pytest.raises(OrdinaryOwnershipError):
        ownership_projection(json.dumps(doc, separators=(",", ":")))


@pytest.mark.parametrize("profile", ["workday", "jsonld", "smartrecruiters", "workable", "dom"])
async def test_real_legacy_detail_write_excludes_actual_canonical_board(monkeypatch, profile):
    from src.lightpanda.write_fence import authoritative_write
    from src.ordinary_ownership import OrdinaryDetailWriteRejected

    async with private_active_plan(
        details=True,
        jsonld=profile == "jsonld",
        api=profile if profile in {"smartrecruiters", "workable", "dom"} else "",
    ) as (pool, expected, payload):
        install_settings(monkeypatch, expected)
        company, foreign, owned_posting, foreign_posting = (uuid.uuid4() for _ in range(4))
        owned = uuid.UUID(json.loads(payload)["details"][0]["board_id"])
        await pool.execute(
            "INSERT INTO company(id,slug,name) VALUES($1,$2,'Detail fixture')",
            company,
            str(company),
        )
        try:
            for board in (owned, foreign):
                await pool.execute(
                    "INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type) "
                    "VALUES($1,$2,$3,$4,'workday')",
                    board,
                    company,
                    str(board),
                    "https://fixture.invalid/board/" + str(board),
                )
            for posting, board in ((owned_posting, owned), (foreign_posting, foreign)):
                await pool.execute(
                    "INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales) "
                    "VALUES($1,$2,$3,$4,ARRAY['Original'],ARRAY['en'])",
                    posting,
                    company,
                    board,
                    "https://fixture.invalid/" + str(posting),
                )
            # The actual posting differs from its board. Cached routing metadata
            # is absent from this write gate and cannot authorize a native board.
            with pytest.raises(OrdinaryDetailWriteRejected):
                async with authoritative_write(pool, None, job_posting_id=str(owned_posting)):
                    pytest.fail("native-owned canonical detail reached legacy writer")
            async with authoritative_write(pool, None, job_posting_id=str(foreign_posting)) as conn:
                await conn.execute(
                    "UPDATE job_posting SET titles=ARRAY['Legacy detail'] WHERE id=$1",
                    foreign_posting,
                )
            assert (
                await pool.fetchval("SELECT titles[1] FROM job_posting WHERE id=$1", owned_posting)
                == "Original"
            )
            assert (
                await pool.fetchval(
                    "SELECT titles[1] FROM job_posting WHERE id=$1", foreign_posting
                )
                == "Legacy detail"
            )
            monkeypatch.setattr(settings, "ordinary_ownership_source_revision", "b" * 40)
            with pytest.raises(OrdinaryDetailWriteRejected):
                async with authoritative_write(pool, None, job_posting_id=str(foreign_posting)):
                    pytest.fail("changed source retained legacy write authority")
        finally:
            await pool.execute("DELETE FROM job_posting WHERE company_id=$1", company)
            await pool.execute("DELETE FROM job_board WHERE company_id=$1", company)
            await pool.execute("DELETE FROM company WHERE id=$1", company)


def _compiled_native_detail_profiles():
    source = (Path(__file__).parents[1] / "go/ordinary-worker/environment.go").read_text()
    profiles = sorted(set(re.findall(r'"([a-z0-9_.-]+detail/v1)"', source)))
    assert len(profiles) >= 23, "compiled detail capability evidence is incomplete"
    # Workday's retained monitor-membership requirement has dedicated tests.
    return [profile for profile in profiles if profile != "workday.cxs-detail/v1"]


@pytest.mark.parametrize("profile", _compiled_native_detail_profiles())
def test_detail_projection_is_independent_of_monitor_membership(profile):
    _, payload = expectation()
    doc = json.loads(payload)
    board = "00000000-0000-4000-8000-000000000098"
    doc["details"] = [
        {
            "board_id": board,
            "domain": "*",
            "profile": profile,
            "worker": "browser" if ".rendered-" in profile else "simple",
            "company_id": "00000000-0000-4000-8000-000000000002",
            "effective_config_sha256": "a" * 64,
            "config": {"crawler_type": "dom", "metadata": '{"scraper_type":"json-ld"}'},
        }
    ]
    projection = json.loads(ownership_projection(json.dumps(doc, separators=(",", ":"))))
    assert board not in projection["members"]
    assert projection["details"] == {board: "*"}
    for field, value in (
        ("domain", "jobs.example.net"),
        ("worker", "simple" if ".rendered-" in profile else "browser"),
        ("company_id", "invalid"),
        ("company_id", None),
        ("effective_config_sha256", "invalid"),
        ("effective_config_sha256", 42),
        ("config", None),
    ):
        changed = json.loads(json.dumps(doc))
        changed["details"][0][field] = value
        with pytest.raises(OrdinaryOwnershipError):
            ownership_projection(json.dumps(changed, separators=(",", ":")))


@pytest.mark.parametrize(
    "profile",
    [
        "dom.rendered-urls/v1",
        "dom.rendered-rows/v1",
        "dayforce.session-search/v1",
        "inline.rendered-items/v1",
        "nextdata.rendered-items/v1",
        "nextdata.rendered-urls/v1",
        "rss.rendered-generic-skip/v1",
        "rss.rendered-generic-items/v1",
        "rss.rendered-generic-summary-skip/v1",
        "rss.rendered-generic-summary-items/v1",
        "rss.rendered-wp_job_manager-skip/v1",
        "rss.rendered-wp_job_manager-items/v1",
    ],
)
def test_rendered_monitor_projection_preserves_exact_worker_boundary(profile):
    _, payload = expectation()
    doc = json.loads(payload)
    member = doc["members"][0]
    member["profile"], member["worker"] = profile, "browser"
    projection = json.loads(ownership_projection(json.dumps(doc, separators=(",", ":"))))
    assert projection["members"] == {member["board_id"]: member["domain"]}
    member["worker"] = "simple"
    with pytest.raises(OrdinaryOwnershipError):
        ownership_projection(json.dumps(doc, separators=(",", ":")))
    member["profile"] = "dom.direct-urls/v1"
    member["worker"] = "browser"
    with pytest.raises(OrdinaryOwnershipError):
        ownership_projection(json.dumps(doc, separators=(",", ":")))


@pytest.mark.parametrize(
    "profile", ["mokahr.encrypted-detail/v1", "eightfold.jsonld-api-detail/v1"]
)
def test_provider_batch_detail_projection_preserves_independent_legacy_exclusion(profile):
    _, payload = expectation(jsonld=True)
    doc = json.loads(payload)
    doc["details"][0]["profile"] = profile
    raw = json.dumps(doc, separators=(",", ":"))
    projection = json.loads(ownership_projection(raw))
    assert projection["details"] == {doc["details"][0]["board_id"]: "*"}
    assert projection["plan_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    doc["details"][0]["worker"] = "browser"
    with pytest.raises(OrdinaryOwnershipError):
        ownership_projection(json.dumps(doc, separators=(",", ":")))
