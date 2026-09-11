"""Real-Postgres/FakeRedis end-to-end proof for one B0 claimant lease."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import fakeredis.aioredis
import httpx
import pytest
from jobseek_runtime_v1 import runtime_pb2

import src.lightpanda.claimant as claimant_module
import src.lightpanda_queue as queue_module
import src.processing.scrape as scrape_module
from src.lightpanda.claimant import (
    LightpandaClaimantDependencies,
    _RejectingOriginTransport,
    _run_claim,
    read_authoritative_schedule,
)
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda.write_fence import (
    LightpandaWriteFence,
    LightpandaWriteFenceRejected,
    activate_write_fence,
    authoritative_write,
)
from src.lightpanda_queue import LightpandaB0Queue, LightpandaB0Task, RouteIdentity

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


async def test_go_write_fence_is_bound_to_current_postgres_high_water() -> None:
    dsn = os.environ["LOCAL_DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    posting_id = uuid.uuid4()
    try:
        await pool.execute(
            "INSERT INTO company (id, name, slug) VALUES ($1, $2, $3)",
            company_id,
            "Lightpanda epoch fence E2E",
            f"lightpanda-epoch-e2e-{company_id.hex}",
        )
        await pool.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            board_id,
            company_id,
            f"lightpanda-epoch-e2e-{board_id.hex}",
            f"https://epoch-e2e.invalid/board/{board_id}",
        )
        await pool.execute(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            posting_id,
            company_id,
            board_id,
            f"https://epoch-e2e.invalid/posting/{posting_id}",
        )
        current = await pool.fetchval("SELECT nextval('public.lightpanda_b0_routing_epoch_seq')")
        assert isinstance(current, int) and current > 1

        def fence(epoch: int, sequence: int) -> LightpandaWriteFence:
            return LightpandaWriteFence(
                job_posting_id=posting_id,
                shard_id="lightpanda-b0",
                routing_epoch=epoch,
                engine_owner="go",
                config_revision=1,
                payload_sha256="a" * 64,
                claim_token=f"{epoch}:{sequence}",
            )

        with pytest.raises(LightpandaWriteFenceRejected) as stale:
            await activate_write_fence(pool, fence(current - 1, 1))
        assert stale.value.reason == "routing_epoch_not_current"
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.lightpanda_b0_write_fence WHERE job_posting_id = $1",
                posting_id,
            )
            == 0
        )

        await activate_write_fence(pool, fence(current, 1))
        assert (
            await pool.fetchval(
                "SELECT routing_epoch FROM public.lightpanda_b0_write_fence "
                "WHERE job_posting_id = $1",
                posting_id,
            )
            == current
        )
        newer = await pool.fetchval("SELECT nextval('public.lightpanda_b0_routing_epoch_seq')")
        assert newer == current + 1
        titles_before = await pool.fetchval(
            "SELECT titles FROM job_posting WHERE id = $1",
            posting_id,
        )
        with pytest.raises(LightpandaWriteFenceRejected) as stale_transaction:
            async with authoritative_write(
                pool,
                fence(current, 1),
                job_posting_id=str(posting_id),
            ) as connection:
                await connection.execute(
                    "UPDATE job_posting SET titles = ARRAY['must roll back'] WHERE id = $1",
                    posting_id,
                )
        assert stale_transaction.value.reason == "routing_epoch_not_current"
        assert (
            await pool.fetchval(
                "SELECT titles FROM job_posting WHERE id = $1",
                posting_id,
            )
            == titles_before
        )
        with pytest.raises(asyncpg.RaiseError) as stale_update:
            await pool.execute(
                "UPDATE public.lightpanda_b0_write_fence "
                "SET state = state WHERE job_posting_id = $1",
                posting_id,
            )
        assert stale_update.value.sqlstate == "P0001"
        assert stale_update.value.message == "lightpanda_b0_write_fence_rejected"
        assert stale_update.value.detail == "routing_epoch_not_current"
        assert (
            await pool.fetchval(
                "SELECT state FROM public.lightpanda_b0_write_fence WHERE job_posting_id = $1",
                posting_id,
            )
            == "active"
        )
    finally:
        await pool.execute("DELETE FROM job_posting WHERE id = $1", posting_id)
        await pool.execute("DELETE FROM job_board WHERE id = $1", board_id)
        await pool.execute("DELETE FROM company WHERE id = $1", company_id)
        await pool.close()


class _RenderedReservation:
    def __init__(self, html: bytes) -> None:
        self.cancelled = False
        digest = hashlib.sha256(html).hexdigest()
        self.result = runtime_pb2.BrowserResult(
            contract_version="crawler.runtime/v1",
            backend=runtime_pb2.BROWSER_BACKEND_LIGHTPANDA,
            success=runtime_pb2.BrowserSuccess(
                final_url="https://claimant-e2e.invalid/job",
                status=200,
                html=runtime_pb2.ChunkManifest(
                    chunks=[
                        runtime_pb2.DataChunk(
                            sequence=0,
                            size_bytes=len(html),
                            sha256=digest,
                            inline_body=html,
                        )
                    ],
                    total_size_bytes=len(html),
                    total_sha256=digest,
                    complete=True,
                ),
            ),
        )

    async def execute(self, task: LightpandaB0Task) -> runtime_pb2.BrowserResult:
        assert task.source_url == "https://claimant-e2e.invalid/job"
        return self.result

    def cancel(self) -> None:
        self.cancelled = True


async def test_claim_render_parse_fenced_commit_and_redis_reschedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = os.environ["LOCAL_DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)
    script = queue_module._SCRIPT.replace(
        "redis.sha1hex(record.payload)", "record.payload_sha1"
    ).replace("redis.sha1hex(payload)", "payload_sha1")
    monkeypatch.setattr(queue_module, "_SCRIPT", script)
    location_resolver = MagicMock()
    monkeypatch.setattr(
        claimant_module,
        "lightpanda_lookups",
        SimpleNamespace(
            _get_location_resolver=AsyncMock(return_value=location_resolver),
            _resolve_locations=AsyncMock(return_value=([], [])),
            _get_technology_ids=AsyncMock(return_value={}),
            _get_occupation_ids=AsyncMock(return_value={}),
            _get_seniority_ids=AsyncMock(return_value={}),
            _get_currency_rates=AsyncMock(return_value={"EUR": 1.0}),
        ),
    )
    monkeypatch.setattr(scrape_module, "_stage_r2_pending", MagicMock(return_value=None))
    queue = LightpandaB0Queue(redis, namespace=f"claimant-e2e-{uuid.uuid4().hex}")
    route = RouteIdentity(shard_id="lightpanda-b0-e2e", routing_epoch=1)
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    posting_id = uuid.uuid4()
    config = {
        "browser_backend": "lightpanda",
        "render": True,
        "routing_revision": "claimant-e2e-1",
        "timeout": 5_000,
        "wait": "load",
        "wait_fallback": None,
    }
    assignment = resolve_render_assignment("json-ld", config)
    assert assignment is not None
    task = LightpandaB0Task.create(
        task_id=str(posting_id),
        board_id=str(board_id),
        source_url="https://claimant-e2e.invalid/job",
        policy_key="lightpanda-b0-v1",
        domain="claimant-e2e.invalid",
        route=route,
        config_revision=1,
        initial_ready_at_ms=0,
        assignment=assignment,
    )
    html = b"""<script type="application/ld+json">
    {"@type":"JobPosting","title":"Claimant E2E Engineer"}
    </script>"""
    reservation = _RenderedReservation(html)
    blocked = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await blocked.wait()

    try:
        await pool.execute(
            "INSERT INTO company (id, name, slug) VALUES ($1, $2, $3)",
            company_id,
            "Lightpanda claimant E2E",
            f"lightpanda-claimant-e2e-{company_id.hex}",
        )
        await pool.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            board_id,
            company_id,
            f"lightpanda-claimant-e2e-{board_id.hex}",
            f"https://claimant-e2e.invalid/board/{board_id}",
        )
        await pool.execute(
            "INSERT INTO job_posting "
            "(id, company_id, board_id, source_url, next_scrape_at) "
            "VALUES ($1, $2, $3, $4, now())",
            posting_id,
            company_id,
            board_id,
            task.source_url,
        )
        assert (await queue.initialize(route)).accepted
        assert (await queue.register(task)).accepted
        claim = await queue.claim_next(route, lease_ttl_ms=60_000)
        assert claim.transition.accepted and claim.lease is not None

        dependencies = LightpandaClaimantDependencies(
            client=object(),  # type: ignore[arg-type]
            queue=queue,
            pool=pool,
            route=route,
            schedule_reader=read_authoritative_schedule,
            sleep=sleep,
        )
        async with httpx.AsyncClient(transport=_RejectingOriginTransport()) as no_origin_http:
            await _run_claim(
                dependencies,
                reservation,  # type: ignore[arg-type]
                claim.lease,
                no_origin_http,
            )

        posting = await pool.fetchrow(
            "SELECT titles, next_scrape_at, last_scraped_at FROM job_posting WHERE id = $1",
            posting_id,
        )
        assert posting is not None
        assert posting["titles"] == ["Claimant E2E Engineer"]
        assert isinstance(posting["next_scrape_at"], datetime)
        assert posting["next_scrape_at"].tzinfo is not None
        assert isinstance(posting["last_scraped_at"], datetime)
        expected_ready_ms = int(posting["next_scrape_at"].astimezone(UTC).timestamp() * 1000)
        assert await redis.zscore(queue._keys.ready, str(posting_id)) == expected_ready_ms
        record = json.loads(await redis.hget(queue._keys.records, str(posting_id)))
        assert record["state"] == "ready"
        assert (
            await pool.fetchval(
                "SELECT state FROM lightpanda_b0_write_fence WHERE job_posting_id = $1",
                posting_id,
            )
            == "revoked"
        )
        assert (await queue.audit_conservation(route)).accepted
        assert reservation.cancelled is False
    finally:
        await pool.execute("DELETE FROM job_posting WHERE id = $1", posting_id)
        await pool.execute("DELETE FROM job_board WHERE id = $1", board_id)
        await pool.execute("DELETE FROM company WHERE id = $1", company_id)
        await redis.aclose()
        await pool.close()
