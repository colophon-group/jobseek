from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError, replace
from typing import Any

import fakeredis.aioredis
import pytest

import src.lightpanda_queue as queue_module
from src.lightpanda.routing import RenderAssignment, resolve_render_assignment
from src.lightpanda_queue import (
    Decision,
    Lease,
    LightpandaB0Queue,
    LightpandaB0Task,
    RouteIdentity,
)


@pytest.fixture(autouse=True)
def fakeredis_lua_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only replace a Redis primitive absent from FakeRedis's Lua runtime.

    Real Redis supplies ``redis.sha1hex``. The production script retains and
    separately tests both calls; this substitution lets FakeRedis exercise the
    state machine without pretending to validate its Lua compatibility.
    """

    script = queue_module._SCRIPT.replace(
        "redis.sha1hex(record.payload)", "record.payload_sha1"
    ).replace("redis.sha1hex(payload)", "payload_sha1")
    monkeypatch.setattr(queue_module, "_SCRIPT", script)


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)


@pytest.fixture
def route() -> RouteIdentity:
    return RouteIdentity(shard_id="lightpanda-b0", routing_epoch=7)


def assignment(*, revision: str = "b0+1") -> RenderAssignment:
    resolved = resolve_render_assignment(
        "json-ld",
        {
            "browser_backend": "lightpanda",
            "render": True,
            "routing_revision": revision,
            "timeout": 5_000,
            "wait": "load",
            "wait_fallback": None,
        },
    )
    assert resolved is not None
    return resolved


def task(
    route: RouteIdentity,
    task_id: str = "posting-1",
    *,
    domain: str = "jobs.example.com",
    ready_at_ms: int = 0,
    config_revision: int = 3,
) -> LightpandaB0Task:
    return LightpandaB0Task.create(
        task_id=task_id,
        board_id="board-1",
        source_url=f"https://{domain}/jobs/{task_id}",
        policy_key="lightpanda-b0-v1",
        domain=domain,
        route=route,
        config_revision=config_revision,
        initial_ready_at_ms=ready_at_ms,
        assignment=assignment(),
    )


async def expire_lease(redis: Any, queue: LightpandaB0Queue, lease: Lease) -> None:
    record = json.loads(await redis.hget(queue._keys.records, lease.task.task_id))
    holder = json.loads(await redis.hget(queue._keys.origin_holders, lease.task.domain))
    record["lease_until_ms"] = 1
    holder["lease_until_ms"] = 1
    await redis.hset(queue._keys.records, lease.task.task_id, json.dumps(record))
    await redis.hset(queue._keys.origin_holders, lease.task.domain, json.dumps(holder))
    await redis.zadd(queue._keys.inflight, {lease.task.task_id: 1})


def test_production_script_keeps_real_redis_payload_hash_checks() -> None:
    source = (
        queue_module.Path(queue_module.__file__).parent / "lua" / "lightpanda_b0_queue.lua"
    ).read_text(encoding="utf-8")
    assert "redis.sha1hex(record.payload)" in source
    assert "redis.sha1hex(payload)" in source


def test_task_is_canonical_b0_and_deeply_immutable(route: RouteIdentity) -> None:
    original = assignment()
    created = task(route)
    envelope = json.loads(created.payload)

    assert envelope["browser_backend"] == "lightpanda"
    assert envelope["routing_revision"] == "b0+1"
    assert envelope["scraper_type"] == "json-ld"
    assert envelope["scraper_step"] == 0
    assert envelope["render"] is True
    assert envelope["wait"] == "load"
    assert envelope["wait_fallback"] is None
    assert envelope["assignment_digest_sha256"] == original.config_digest_sha256
    assert envelope["initial_ready_at_ms"] == 0
    with pytest.raises((FrozenInstanceError, TypeError)):
        created.assignment.config["timeout"] = 1  # type: ignore[index]


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("http://jobs.example.com/1", "jobs.example.com"),
        ("https://user@jobs.example.com/1", "jobs.example.com"),
        ("https://jobs.example.com:444/1", "jobs.example.com"),
        ("https://jobs.example.com/1#fragment", "jobs.example.com"),
        ("https://jobs.example.com/1", "other.example.com"),
        ("\nhttps://jobs.example.com/1", "jobs.example.com"),
        ("https://jobs.example.com/a b", "jobs.example.com"),
        ("https://jobs.\ud800.example.com/1", "jobs.example.com"),
    ],
)
def test_task_rejects_unsafe_or_mismatched_url(route: RouteIdentity, url: str, domain: str) -> None:
    with pytest.raises(ValueError):
        LightpandaB0Task.create(
            task_id="posting-1",
            board_id="board-1",
            source_url=url,
            policy_key="lightpanda-b0-v1",
            domain=domain,
            route=route,
            config_revision=1,
            initial_ready_at_ms=0,
            assignment=assignment(),
        )


async def test_initialize_register_claim_and_audit(redis: Any, route: RouteIdentity) -> None:
    queue = LightpandaB0Queue(redis, namespace="roundtrip")
    queued = task(route)

    assert (await queue.initialize(route)).reason == "initialized"
    assert (await queue.initialize(route)).reason == "already_initialized"
    registered = await queue.register(queued)
    assert registered.accepted and registered.value == queued.initial_ready_at_ms
    retried = await queue.register(queued)
    assert retried.accepted and retried.reason == "already_registered"
    assert await redis.hlen(queue._keys.records) == 1

    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.transition.accepted
    assert claimed.lease is not None
    assert claimed.transition.server_time_ms is not None
    assert claimed.lease.task == queued
    assert claimed.lease.lease_until_ms == claimed.transition.server_time_ms + 30_000
    audit = await queue.audit_conservation(route)
    assert audit.accepted and (audit.value, audit.secondary_value) == (1, 1)


async def test_register_recomputes_public_task_identity(redis: Any, route: RouteIdentity) -> None:
    queue = LightpandaB0Queue(redis, namespace="forged-register")
    await queue.initialize(route)
    forged = replace(task(route), payload_sha256="0" * 64)

    with pytest.raises(ValueError, match="task identity mismatch"):
        await queue.register(forged)
    assert await redis.hlen(queue._keys.records) == 0
    assert await redis.zcard(queue._keys.ready) == 0


async def test_adapter_accepts_exact_utf8_byte_replies(route: RouteIdentity) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False, protocol=2)
    queue = LightpandaB0Queue(redis, namespace="bytes")
    queued = task(route)

    assert (await queue.initialize(route)).accepted
    assert (await queue.register(queued)).accepted
    claimed = await queue.claim_next(route, lease_ttl_ms=10_000)
    assert claimed.transition.accepted and claimed.lease is not None


async def test_sequential_claim_enforces_one_holder_per_origin(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="origins")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    await redis.set("delay:careers.example.net", "0")
    first = task(route, "a-same")
    second = task(route, "b-same")
    other = task(route, "c-other", domain="careers.example.net")
    for queued in (first, second, other):
        assert (await queue.register(queued)).accepted

    first_claim = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert first_claim.lease is not None and first_claim.lease.task.task_id == "a-same"
    assert await redis.zcard(queue._keys.inflight) == 1

    other_claim = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert other_claim.lease is not None and other_claim.lease.task.task_id == "c-other"
    assert await redis.zcard(queue._keys.inflight) == 2
    assert await redis.hlen(queue._keys.origin_holders) == 2

    assert (await queue.complete(first_claim.lease)).accepted
    assert (await queue.complete(other_claim.lease)).accepted
    await asyncio.sleep(1.05)
    final_claim = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert final_claim.lease is not None and final_claim.lease.task.task_id == "b-same"


async def test_shared_delay_is_authoritative_and_never_rewritten(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="politeness")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0.5")
    first = task(route, "posting-a")
    second = task(route, "posting-b")
    await queue.register(first)
    await queue.register(second)

    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    assert claimed.transition.server_time_ms is not None
    assert await redis.get("delay:jobs.example.com") == "0.5"
    rate_until = float(await redis.get("ratelimit:jobs.example.com"))
    assert 0.49 <= rate_until - (claimed.transition.server_time_ms / 1000) <= 0.51
    assert 1 <= await redis.ttl("ratelimit:jobs.example.com") <= 2
    assert (await queue.complete(claimed.lease)).accepted

    blocked = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert blocked.lease is None and blocked.transition.reason == "no_work"
    assert await redis.get("delay:jobs.example.com") == "0.5"


async def test_missing_delay_uses_current_crawler_setting(
    redis: Any, route: RouteIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = LightpandaB0Queue(redis, namespace="default-politeness")
    await queue.initialize(route)
    monkeypatch.setattr(queue_module.settings, "throttle_delay_default", 0.75)
    await queue.register(task(route))

    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    assert claimed.transition.server_time_ms is not None
    rate_until = float(await redis.get("ratelimit:jobs.example.com"))
    assert 0.74 <= rate_until - (claimed.transition.server_time_ms / 1000) <= 0.76


async def test_v1_future_rate_limit_reparks_and_scans_another_origin(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="future-rate")
    await queue.initialize(route)
    redis_time = await redis.time()
    future_value = f"{redis_time[0] + 30}.{redis_time[1]:06d}"
    await redis.set("ratelimit:jobs.example.com", future_value)
    await redis.set("delay:careers.example.net", "0")
    blocked = task(route, "a-blocked")
    eligible = task(route, "z-eligible", domain="careers.example.net")
    await queue.register(blocked)
    await queue.register(eligible)

    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    assert claimed.lease.task.task_id == eligible.task_id
    assert await redis.get("ratelimit:jobs.example.com") == future_value
    assert (
        float(await redis.zscore(queue._keys.ready, blocked.task_id)) >= (redis_time[0] + 30) * 1000
    )


async def test_expired_holder_does_not_block_another_origin(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="expired-holder")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    await redis.set("delay:careers.example.net", "0")
    active = task(route, "a-active")
    blocked = task(route, "b-same")
    eligible = task(route, "c-other", domain="careers.example.net")
    for queued in (active, blocked, eligible):
        await queue.register(queued)
    first = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert first.lease is not None and first.lease.task.task_id == active.task_id
    await expire_lease(redis, queue, first.lease)

    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is not None and claimed.lease.task.task_id == eligible.task_id
    assert await redis.zcard(queue._keys.inflight) == 2


async def test_bounded_origin_parking_exposes_later_origin_on_retry(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="bounded-scan")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    await redis.set("delay:careers.example.net", "0")
    active = task(route, "a-active")
    await queue.register(active)
    first = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert first.lease is not None
    for index in range(queue_module.SCAN_LIMIT + 1):
        await queue.register(task(route, f"b-same-{index:02d}"))
    eligible = task(route, "z-other", domain="careers.example.net")
    await queue.register(eligible)

    parked_batch = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert parked_batch.lease is None and parked_batch.transition.reason == "no_work"
    later_claim = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert later_claim.lease is not None and later_claim.lease.task.task_id == eligible.task_id


async def test_heartbeat_updates_handle_and_stale_copy_is_fenced(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="heartbeat")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    await queue.register(task(route))
    claimed = await queue.claim_next(route, lease_ttl_ms=1_000)
    assert claimed.lease is not None
    stale = Lease(
        task=claimed.lease.task,
        claim_token=claimed.lease.claim_token,
        lease_until_ms=claimed.lease.lease_until_ms,
    )

    extended = await queue.heartbeat(claimed.lease, lease_ttl_ms=5_000)
    assert extended.accepted
    assert extended.server_time_ms is not None
    assert claimed.lease.lease_until_ms == extended.server_time_ms + 5_000
    fenced = await queue.reschedule_at(stale, ready_at_ms=0)
    assert fenced.decision is Decision.FENCED
    assert fenced.reason == "lease_deadline_mismatch"
    assert (await queue.complete(claimed.lease)).accepted


async def test_malformed_origin_holder_fails_without_mutation(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="holder-corrupt")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    await queue.register(task(route))
    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    await redis.hset(
        queue._keys.origin_holders,
        claimed.lease.task.domain,
        json.dumps({"task_id": claimed.lease.task.task_id}),
    )

    audit = await queue.audit_conservation(route)
    assert (audit.decision, audit.reason) == (
        Decision.NOT_CURRENT,
        "origin_holder_corrupt",
    )
    assert await redis.zcard(queue._keys.inflight) == 1
    assert await redis.hlen(queue._keys.records) == 1


async def test_digest_and_token_fences_do_not_mutate(redis: Any, route: RouteIdentity) -> None:
    queue = LightpandaB0Queue(redis, namespace="fences")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    await queue.register(task(route))
    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    assert claimed.transition.server_time_ms is not None

    wrong_digest = Lease(
        task=replace(claimed.lease.task, payload_sha256="0" * 64),
        claim_token=claimed.lease.claim_token,
        lease_until_ms=claimed.lease.lease_until_ms,
    )
    digest_result = await queue.complete(wrong_digest)
    assert (digest_result.decision, digest_result.reason) == (
        Decision.FENCED,
        "payload_digest_mismatch",
    )
    wrong_token = Lease(
        task=claimed.lease.task,
        claim_token=f"{route.routing_epoch}:999",
        lease_until_ms=claimed.lease.lease_until_ms,
    )
    token_result = await queue.complete(wrong_token)
    assert (token_result.decision, token_result.reason) == (
        Decision.FENCED,
        "claim_token_mismatch",
    )
    assert await redis.zcard(queue._keys.inflight) == 1


async def test_absolute_reschedule_resets_failures(redis: Any, route: RouteIdentity) -> None:
    queue = LightpandaB0Queue(redis, namespace="reschedule")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    queued = task(route)
    await queue.register(queued)
    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    assert claimed.transition.server_time_ms is not None
    record = json.loads(await redis.hget(queue._keys.records, queued.task_id))
    record["failures"] = 2
    await redis.hset(queue._keys.records, queued.task_id, json.dumps(record))

    ready_at = claimed.transition.server_time_ms + 60_000
    outcome = await queue.reschedule_at(claimed.lease, ready_at_ms=ready_at)
    assert outcome.accepted and outcome.value == ready_at
    stored = json.loads(await redis.hget(queue._keys.records, queued.task_id))
    assert stored["ready_at_ms"] == ready_at
    assert stored["failures"] == 0
    assert (await queue.audit_conservation(route)).accepted


async def test_reaper_requeues_then_dead_letters_boundedly(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="reaper")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    queued = task(route)
    await queue.register(queued)
    first = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert first.lease is not None
    await expire_lease(redis, queue, first.lease)

    requeued = await queue.reap_expired(route, max_failures=2)
    assert requeued.accepted and (requeued.value, requeued.secondary_value) == (1, 0)
    await redis.set("ratelimit:jobs.example.com", "0")
    second = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert second.lease is not None
    await expire_lease(redis, queue, second.lease)
    dead = await queue.reap_expired(route, max_failures=2)
    assert dead.accepted and (dead.value, dead.secondary_value) == (0, 1)
    assert await redis.sismember(queue._keys.dead, queued.task_id)
    assert (await queue.audit_conservation(route)).accepted


async def test_old_token_is_fenced_after_reap_and_reclaim(redis: Any, route: RouteIdentity) -> None:
    queue = LightpandaB0Queue(redis, namespace="reclaim-token")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    await queue.register(task(route))
    first = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert first.lease is not None
    stale = Lease(
        task=first.lease.task,
        claim_token=first.lease.claim_token,
        lease_until_ms=first.lease.lease_until_ms,
    )
    await expire_lease(redis, queue, first.lease)
    assert (await queue.reap_expired(route, max_failures=3)).accepted
    await redis.set("ratelimit:jobs.example.com", "0")
    second = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert second.lease is not None
    assert second.lease.claim_token != stale.claim_token

    outcome = await queue.complete(stale)
    assert (outcome.decision, outcome.reason) == (
        Decision.FENCED,
        "claim_token_mismatch",
    )
    assert await redis.zcard(queue._keys.inflight) == 1


async def test_complete_and_digest_fenced_reactivate(redis: Any, route: RouteIdentity) -> None:
    queue = LightpandaB0Queue(redis, namespace="reactivate")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    original = task(route)
    await queue.register(original)
    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    assert (await queue.complete(claimed.lease)).accepted
    assert await redis.sismember(queue._keys.terminal, original.task_id)

    replacement = task(route, ready_at_ms=123, config_revision=4)
    stale = await queue.reactivate(replacement, previous_payload_sha256="0" * 64)
    assert (stale.decision, stale.reason) == (Decision.FENCED, "payload_digest_mismatch")
    same_revision = await queue.reactivate(
        task(route, ready_at_ms=123, config_revision=original.config_revision),
        previous_payload_sha256=original.payload_sha256,
    )
    assert (same_revision.decision, same_revision.reason) == (
        Decision.FENCED,
        "config_revision_not_advanced",
    )
    outcome = await queue.reactivate(replacement, previous_payload_sha256=original.payload_sha256)
    assert outcome.accepted and outcome.value == 123
    assert not await redis.sismember(queue._keys.terminal, original.task_id)
    assert (await queue.audit_conservation(route)).accepted


async def test_corrupt_payload_never_reaches_executor(redis: Any, route: RouteIdentity) -> None:
    queue = LightpandaB0Queue(redis, namespace="payload-corrupt")
    await queue.initialize(route)
    await redis.set("delay:jobs.example.com", "0")
    queued = task(route)
    await queue.register(queued)
    record = json.loads(await redis.hget(queue._keys.records, queued.task_id))
    record["payload"] = record["payload"].replace("/posting-1", "/tampered")
    record["payload_sha1"] = queue_module.hashlib.sha1(
        record["payload"].encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    await redis.hset(queue._keys.records, queued.task_id, json.dumps(record))

    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is None
    assert (claimed.transition.decision, claimed.transition.reason) == (
        Decision.TRANSPORT_ERROR,
        "payload_integrity_failure",
    )


@pytest.mark.parametrize("dynamic_key", ["delay:jobs.example.com", "ratelimit:jobs.example.com"])
async def test_wrong_dynamic_redis_type_fails_without_claim(
    redis: Any, route: RouteIdentity, dynamic_key: str
) -> None:
    queue = LightpandaB0Queue(redis, namespace="dynamic-type")
    await queue.initialize(route)
    queued = task(route)
    await queue.register(queued)
    await redis.rpush(dynamic_key, "wrong-type")

    claimed = await queue.claim_next(route, lease_ttl_ms=30_000)
    assert claimed.lease is None
    assert claimed.transition.reason in {"delay_corrupt", "rate_limit_corrupt"}
    assert await redis.zcard(queue._keys.ready) == 1
    assert await redis.zcard(queue._keys.inflight) == 0


async def test_wrong_namespace_type_fails_without_route_mutation(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="namespace-type")
    await queue.initialize(route)
    route_before = await redis.hgetall(queue._keys.route)
    await redis.set(queue._keys.ready, "wrong-type")

    audit = await queue.audit_conservation(route)
    assert (audit.decision, audit.reason) == (Decision.NOT_CURRENT, "namespace_corrupt")
    assert await redis.hgetall(queue._keys.route) == route_before


async def test_conservation_and_route_corruption_fail_closed(
    redis: Any, route: RouteIdentity
) -> None:
    queue = LightpandaB0Queue(redis, namespace="corrupt")
    await queue.initialize(route)
    queued = task(route)
    await queue.register(queued)
    await redis.sadd(queue._keys.dead, "orphan")
    audit = await queue.audit_conservation(route)
    assert (audit.decision, audit.reason) == (
        Decision.NOT_CURRENT,
        "conservation_violation",
    )

    wrong_route = RouteIdentity(shard_id=route.shard_id, routing_epoch=8)
    fenced = await queue.audit_conservation(wrong_route)
    assert (fenced.decision, fenced.reason) == (
        Decision.FENCED,
        "routing_epoch_mismatch",
    )


async def test_audit_rejects_untracked_ready_score_change(redis: Any, route: RouteIdentity) -> None:
    queue = LightpandaB0Queue(redis, namespace="score-corrupt")
    await queue.initialize(route)
    queued = task(route)
    await queue.register(queued)
    await redis.zadd(queue._keys.ready, {queued.task_id: queue_module.MAX_INTEGER})

    audit = await queue.audit_conservation(route)
    assert (audit.decision, audit.reason) == (
        Decision.NOT_CURRENT,
        "conservation_violation",
    )


def test_decoder_rejects_cross_operation_and_non_text_fields() -> None:
    cross_operation: list[Any] = [
        "accepted",
        "rescheduled",
        "1",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "0",
        "",
    ]
    decoded = LightpandaB0Queue._decode_transition("heartbeat", cross_operation)
    assert (decoded.decision, decoded.reason) == (Decision.TRANSPORT_ERROR, "invalid_reply")
    cross_operation[0] = 1
    decoded = LightpandaB0Queue._decode_transition("heartbeat", cross_operation)
    assert (decoded.decision, decoded.reason) == (Decision.TRANSPORT_ERROR, "invalid_reply")


def test_claim_token_sequence_is_bounded(route: RouteIdentity) -> None:
    with pytest.raises(ValueError, match="invalid claim token"):
        Lease(
            task=task(route),
            claim_token=f"{route.routing_epoch}:" + ("9" * 100),
            lease_until_ms=1,
        )
