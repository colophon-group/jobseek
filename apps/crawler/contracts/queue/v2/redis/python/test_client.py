from __future__ import annotations

import asyncio
import json
import math
import os
import uuid
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import pytest
import redis.asyncio as aioredis
from redis.exceptions import ConnectionError

from contracts.queue.v2.redis.python import (
    Decision,
    Fence,
    LeaseHandle,
    QueueV2Candidate,
    RouteIdentity,
    TransitionResult,
)
from contracts.queue.v2.redis.python.client import MAX_INTEGER, _decode_result

FIXTURE = Path(__file__).parents[1] / "fixtures" / "lifecycle_scenarios.json"
SCENARIOS = json.loads(FIXTURE.read_text(encoding="utf-8"))
ROUTE = RouteIdentity(shard_id="shard-03", routing_epoch=7, engine_owner="go")


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(
        decode_responses=True,
        protocol=2,
        socket_timeout=None,
        socket_connect_timeout=None,
    )


@pytest.fixture
def queue(fake_redis):
    return QueueV2Candidate(fake_redis, namespace=f"test-{uuid.uuid4().hex}")


async def _snapshot(redis: Any, keys: tuple[str, ...]) -> list[tuple[str, str, bytes | None]]:
    return [(key, await redis.type(key), await redis.dump(key)) for key in keys]


async def _registered_claim(
    queue: QueueV2Candidate, task_id: str = "monitor-a", *, lease_ttl_ms: int = 10_000
) -> LeaseHandle:
    assert (await queue.initialize(ROUTE)).accepted
    assert (await queue.register(task_id, route=ROUTE, config_revision=4)).accepted
    claimed = await queue.claim(task_id, route=ROUTE, config_revision=4, lease_ttl_ms=lease_ttl_ms)
    assert claimed.transition.accepted
    assert claimed.lease is not None
    return claimed.lease


async def _lifecycle_members(redis: Any, queue: QueueV2Candidate, task_id: str) -> list[str]:
    members: list[str] = []
    if await redis.zscore(queue.keys[3], task_id) is not None:
        members.append("ready")
    if await redis.zscore(queue.keys[4], task_id) is not None:
        members.append("inflight")
    if await redis.sismember(queue.keys[5], task_id):
        members.append("dead_letter")
    if await redis.sismember(queue.keys[6], task_id):
        members.append("terminal")
    return members


def _fixture_route(step: dict[str, Any] | None = None) -> RouteIdentity:
    source = dict(SCENARIOS["route"])
    if step:
        source.update(step.get("route_override", {}))
    return RouteIdentity(**source)


async def _run_shared_scenarios(redis: Any, namespace: str) -> None:
    assert SCENARIOS["format"] == "jobseek.queue.v2.redis-lifecycle/v1"
    assert SCENARIOS["numeric_max"] == MAX_INTEGER
    queue = QueueV2Candidate(redis, namespace=namespace)
    tokens: dict[str, str] = {}
    leases: dict[str, int] = {}
    try:
        for step in SCENARIOS["steps"]:
            action = step["action"]
            if action == "sleep":
                await asyncio.sleep(step["duration_ms"] / 1000)
                continue
            if action == "restart_client":
                queue = QueueV2Candidate(redis, namespace=namespace)
                continue
            route = _fixture_route(step)
            if action == "concurrent_claim":
                clients = [
                    QueueV2Candidate(redis, namespace=namespace),
                    QueueV2Candidate(redis, namespace=namespace),
                ]
                results = await asyncio.gather(
                    *[
                        client._execute(
                            "claim",
                            task_id=step["task_id"],
                            route=route,
                            config_revision=step["revision"],
                            lease_ttl_ms=step["lease_ttl_ms"],
                        )
                        for client in clients
                    ]
                )
                counts: dict[str, int] = {}
                for result in results:
                    key = f"{result.decision.value}/{result.reason}"
                    counts[key] = counts.get(key, 0) + 1
                assert counts == step["expect_counts"], step["id"]
                assert await _lifecycle_members(redis, queue, step["task_id"]) == ["inflight"]
                continue

            token_spec = step.get("token", "")
            token = tokens[token_spec[1:]] if token_spec.startswith("$") else token_spec
            previous_lease = leases.get(token_spec[1:]) if token_spec.startswith("$") else None
            result = await queue._execute(
                action,
                task_id=step.get("task_id", ""),
                route=route,
                config_revision=step.get("revision", 0),
                claim_token=token,
                lease_ttl_ms=step.get("lease_ttl_ms", 0),
                reschedule_delay_ms=step.get("delay_ms", 0),
                max_failures=step.get("max_failures", 0),
                previous_lease_until=previous_lease,
            )
            assert [result.decision.value, result.reason] == step["expect"], step["id"]
            if alias := step.get("save_token"):
                assert result.claim_token is not None and result.value is not None
                tokens[alias] = result.claim_token
                leases[alias] = result.value
            if action == "heartbeat" and result.accepted and token_spec.startswith("$"):
                assert result.value is not None
                leases[token_spec[1:]] = result.value

        route_state = await redis.hgetall(queue.keys[0])
        # Four sequential claims plus the one accepted concurrent claim.
        assert route_state == {
            "claim_sequence": "5",
            "engine_owner": "go",
            "routing_epoch": "7000000000001",
            "shard_id": "shared-shard",
        }
    finally:
        await redis.delete(*queue.keys)


async def test_shared_language_neutral_scenarios_on_fakeredis(fake_redis):
    await _run_shared_scenarios(fake_redis, f"shared-fake-{uuid.uuid4().hex}")


@pytest.mark.parametrize("kind", ["partial", "extra", "wrong_type", "route_less"])
async def test_initialize_rejects_nonempty_corrupt_namespace_without_mutation(
    fake_redis, queue, kind
):
    if kind == "partial":
        await fake_redis.hset(queue.keys[0], mapping={"shard_id": "shard-03"})
    elif kind == "extra":
        await fake_redis.hset(
            queue.keys[0],
            mapping={
                "shard_id": "shard-03",
                "routing_epoch": "7",
                "engine_owner": "go",
                "claim_sequence": "0",
                "extra": "bad",
            },
        )
    elif kind == "wrong_type":
        await fake_redis.set(queue.keys[0], "not-a-hash")
    else:
        await fake_redis.hset(queue.keys[1], "orphan", "1")
    before = await _snapshot(fake_redis, queue.keys)

    result = await queue.initialize(ROUTE)

    assert result.decision is Decision.NOT_CURRENT
    assert result.reason == "namespace_corrupt"
    assert await _snapshot(fake_redis, queue.keys) == before


async def test_initialize_is_idempotent_only_for_exact_valid_route(fake_redis, queue):
    assert (await queue.initialize(ROUTE)).reason == "initialized"
    before = await _snapshot(fake_redis, queue.keys)
    assert (await queue.initialize(ROUTE)).reason == "already_initialized"
    assert await fake_redis.hgetall(queue.keys[0]) == {
        "shard_id": "shard-03",
        "routing_epoch": "7",
        "engine_owner": "go",
        "claim_sequence": "0",
    }
    assert await _snapshot(fake_redis, queue.keys) == before


@pytest.mark.parametrize("value", [True, False, 1.0, "1", MAX_INTEGER + 1])
def test_python_numeric_apis_reject_bool_noninteger_and_out_of_domain(value):
    with pytest.raises(ValueError):
        RouteIdentity("shard", value, "go")  # type: ignore[arg-type]


def test_python_identity_apis_reject_non_string_values():
    with pytest.raises(ValueError):
        RouteIdentity(3, 1, "go")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Fence(3, ROUTE, 1, "7:1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Fence("task", ROUTE, 1, 71)  # type: ignore[arg-type]


async def test_numeric_max_large_fence_round_trips_through_lua_and_cjson(fake_redis):
    route = RouteIdentity("max-shard", MAX_INTEGER, "go")
    queue = QueueV2Candidate(fake_redis, namespace=f"max-{uuid.uuid4().hex}")
    assert (await queue.initialize(route)).accepted
    assert (await queue.register("max-task", route=route, config_revision=MAX_INTEGER)).accepted
    claimed = await queue.claim(
        "max-task", route=route, config_revision=MAX_INTEGER, lease_ttl_ms=1
    )
    assert claimed.transition.accepted
    assert claimed.transition.claim_token == f"{MAX_INTEGER}:1"
    record = json.loads(await fake_redis.hget(queue.keys[2], "max-task"))
    assert record["routing_epoch"] == MAX_INTEGER
    assert record["config_revision"] == MAX_INTEGER


@pytest.mark.parametrize("sequence", ["-1", str(MAX_INTEGER + 1), "1e3", "inf"])
async def test_corrupt_sequence_fails_without_mutation(fake_redis, queue, sequence):
    assert (await queue.initialize(ROUTE)).accepted
    assert (await queue.register("task", route=ROUTE, config_revision=1)).accepted
    await fake_redis.hset(queue.keys[0], "claim_sequence", sequence)
    before = await _snapshot(fake_redis, queue.keys)

    result = await queue.claim("task", route=ROUTE, config_revision=1, lease_ttl_ms=1)

    assert result.transition.decision is Decision.NOT_CURRENT
    assert result.transition.reason == "namespace_corrupt"
    assert await _snapshot(fake_redis, queue.keys) == before


async def test_sequence_max_is_exhausted_without_mutation(fake_redis, queue):
    assert (await queue.initialize(ROUTE)).accepted
    assert (await queue.register("task", route=ROUTE, config_revision=1)).accepted
    await fake_redis.hset(queue.keys[0], "claim_sequence", str(MAX_INTEGER))
    before = await _snapshot(fake_redis, queue.keys)

    result = await queue.claim("task", route=ROUTE, config_revision=1, lease_ttl_ms=1)

    assert result.transition.reason == "claim_sequence_exhausted"
    assert await _snapshot(fake_redis, queue.keys) == before


async def test_checked_add_overflow_precedes_claim_sequence_mutation(fake_redis, queue):
    assert (await queue.initialize(ROUTE)).accepted
    assert (await queue.register("task", route=ROUTE, config_revision=1)).accepted
    before = await _snapshot(fake_redis, queue.keys)

    result = await queue.claim("task", route=ROUTE, config_revision=1, lease_ttl_ms=MAX_INTEGER)

    assert result.transition.reason == "numeric_overflow"
    assert await _snapshot(fake_redis, queue.keys) == before


@pytest.mark.parametrize("score", [-1.0, math.inf, 1e20])
async def test_noncanonical_ready_score_is_rejected_without_mutation(fake_redis, queue, score):
    assert (await queue.initialize(ROUTE)).accepted
    assert (await queue.register("task", route=ROUTE, config_revision=1)).accepted
    await fake_redis.zadd(queue.keys[3], {"task": score})
    before = await _snapshot(fake_redis, queue.keys)

    result = await queue.claim("task", route=ROUTE, config_revision=1, lease_ttl_ms=1)

    assert result.transition.reason == "conservation_violation"
    assert await _snapshot(fake_redis, queue.keys) == before


@pytest.mark.parametrize("corrupt", [math.inf, 1e20, -1])
async def test_infinite_huge_negative_record_number_is_rejected_without_mutation(
    fake_redis, queue, corrupt
):
    assert (await queue.initialize(ROUTE)).accepted
    assert (await queue.register("task", route=ROUTE, config_revision=1)).accepted
    record = json.loads(await fake_redis.hget(queue.keys[2], "task"))
    record["failures"] = corrupt
    await fake_redis.hset(queue.keys[2], "task", json.dumps(record))
    before = await _snapshot(fake_redis, queue.keys)

    result = await queue.claim("task", route=ROUTE, config_revision=1, lease_ttl_ms=1)

    assert result.transition.reason == "record_corrupt"
    assert await _snapshot(fake_redis, queue.keys) == before


@pytest.mark.parametrize(
    "raw",
    [
        ["accepted", "completed", "100", "", ""],
        ["accepted", "claimed", "100", "7:1e3", "200"],
        ["accepted", "lease_extended", "100", "7:1", "150"],
        ["accepted", "registered", "0100", "", "100"],
        ["accepted", "registered", str(MAX_INTEGER + 1), "", str(MAX_INTEGER + 1)],
        ["fenced", "lease_expired", "100", "", ""],
        ["not_current", "not_due", "100", "7:1", "200"],
        ["transport-error", "redis_error", "0", "", ""],
    ],
)
def test_operation_aware_decoder_rejects_impossible_replies(raw):
    assert _decode_result(
        "heartbeat",
        raw,
        route=ROUTE,
        expected_token="7:1",
        previous_lease_until=150,
    ) == TransitionResult(Decision.TRANSPORT_ERROR, "invalid_redis_reply")


@pytest.mark.parametrize("token", ["", "7:2"])
def test_complete_success_requires_exact_expected_token(token):
    assert _decode_result(
        "complete",
        ["accepted", "completed", "100", token, ""],
        route=ROUTE,
        expected_token="7:1",
    ) == TransitionResult(Decision.TRANSPORT_ERROR, "invalid_redis_reply")


class _BrokenRedis:
    async def script_load(self, _script: str):
        raise ConnectionError("synthetic pre-execution loss")


class _InvalidReplyRedis:
    async def script_load(self, _script: str) -> str:
        return "sha"

    async def evalsha(self, *_args: Any) -> list[str]:
        return ["accepted", "completed", "100", "", ""]


class _AmbiguousRedis:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate

    async def script_load(self, script: str) -> str:
        return await self.delegate.script_load(script)

    async def evalsha(self, *args: Any) -> Any:
        await self.delegate.evalsha(*args)
        raise ConnectionError("synthetic post-execution loss")


def _fault(fault_id: str) -> dict[str, Any]:
    return next(item for item in SCENARIOS["client_faults"] if item["id"] == fault_id)


async def test_transport_before_and_invalid_reply_signal_lease_loss():
    for redis, fault_id in [
        (_BrokenRedis(), "transport_before_execution"),
        (_InvalidReplyRedis(), "invalid_success_reply"),
    ]:
        queue = QueueV2Candidate(redis, namespace=fault_id)  # type: ignore[arg-type]
        lease = LeaseHandle(Fence("task", ROUTE, 1, "7:1"), lease_until_ms=200)
        result = await queue.complete(lease)
        expected = _fault(fault_id)
        assert [result.decision.value, result.reason] == expected["transition"]
        assert lease.lost.is_set() is expected["lease_lost"]


async def test_ambiguous_transport_after_execution_signals_loss_but_server_mutated(
    fake_redis,
):
    namespace = f"ambiguous-{uuid.uuid4().hex}"
    base = QueueV2Candidate(fake_redis, namespace=namespace)
    lease = await _registered_claim(base)
    ambiguous = QueueV2Candidate(_AmbiguousRedis(fake_redis), namespace=namespace)  # type: ignore[arg-type]

    result = await ambiguous.complete(lease)

    expected = _fault("ambiguous_transport_after_execution")
    assert [result.decision.value, result.reason] == expected["transition"]
    assert lease.lost.is_set() is expected["lease_lost"]
    assert await _lifecycle_members(fake_redis, base, "monitor-a") == ["terminal"]
    assert expected["server_mutated"] is True


@pytest.mark.skipif(
    not os.environ.get("QUEUE_V2_REDIS_URL") or os.environ.get("QUEUE_V2_REDIS_ISOLATED") != "1",
    reason="requires an explicitly isolated real Redis",
)
async def test_real_redis_runs_full_shared_lifecycle_scenarios():
    redis = aioredis.Redis.from_url(
        os.environ["QUEUE_V2_REDIS_URL"], decode_responses=True, protocol=2
    )
    try:
        assert await redis.dbsize() == 0
        await _run_shared_scenarios(redis, f"real-python-{uuid.uuid4().hex}")
        assert await redis.dbsize() == 0
    finally:
        await redis.aclose()
