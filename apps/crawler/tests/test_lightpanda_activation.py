"""Adversarial exclusivity tests for the legacy-to-Go B0 cutover."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import pytest
from redis.exceptions import ResponseError

import src.lightpanda.activation as activation
import src.lightpanda.producer as producer
import src.lightpanda_queue as queue_module
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda_queue import LightpandaB0Queue, LightpandaB0Task, RouteIdentity

LUA = Path(queue_module.__file__).parent / "lua"


@pytest.fixture(autouse=True)
def fakeredis_lua_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    script = queue_module._SCRIPT.replace(
        "redis.sha1hex(record.payload)", "record.payload_sha1"
    ).replace("redis.sha1hex(payload)", "payload_sha1")
    monkeypatch.setattr(queue_module, "_SCRIPT", script)


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)


def _task(
    *,
    ready_at_ms: int = 123_000,
    revision: int = 1,
    task_id: str = "00000000-0000-4000-8000-000000000001",
) -> LightpandaB0Task:
    assignment = resolve_render_assignment(
        "json-ld",
        {
            "browser_backend": "lightpanda",
            "render": True,
            "routing_revision": "go-b0-1",
            "timeout": 5_000,
            "wait": "load",
            "wait_fallback": None,
        },
    )
    assert assignment is not None
    return LightpandaB0Task.create(
        task_id=task_id,
        board_id="11111111-1111-4111-8111-111111111111",
        source_url="https://jobs.example.com/posting",
        policy_key="lightpanda-b0-v1",
        domain="jobs.example.com",
        route=RouteIdentity(shard_id="lightpanda-b0", routing_epoch=7, engine_owner="go"),
        config_revision=revision,
        initial_ready_at_ms=ready_at_ms,
        assignment=assignment,
    )


async def _seed_legacy_ready(redis: Any, task: LightpandaB0Task) -> None:
    score = task.initial_ready_at_ms / 1000
    await redis.hset(
        f"scrape:{task.task_id}",
        mapping={"board_id": task.board_id, "source_url": task.source_url, "domain": task.domain},
    )
    await redis.zadd(f"ft_scrapes_browser:{task.domain}", {task.task_id: score})
    await redis.zadd("ready:browser:0", {task.domain: score})


def _legacy_config(task: LightpandaB0Task) -> dict[str, str]:
    return {
        "board_id": task.board_id,
        "source_url": task.source_url,
        "scrape_step": "0",
        "scrape_interval_hours": "24",
        "description_r2_hash": "example",
    }


def _rollback_schedule(
    task: LightpandaB0Task, *, description_hash: str = "0", score: str = "999"
) -> dict[str, object]:
    return {
        "action": "schedule",
        "domain": task.domain,
        "worker_type": "browser",
        "first_time": description_hash == "",
        "score": "0" if description_hash == "" else score,
        "config": {
            "domain": task.domain,
            "board_id": task.board_id,
            "source_url": task.source_url,
            "description_r2_hash": description_hash,
            "scrape_step": "0",
            "scrape_interval_hours": "12",
        },
    }


async def test_activation_atomically_transfers_one_legacy_membership(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    assert (await queue.initialize(task.route)).accepted
    await _seed_legacy_ready(redis, task)

    activated = await queue.activate_legacy(task, legacy_config=_legacy_config(task))

    assert activated.accepted and activated.reason == "activated"
    assert (activated.value, activated.secondary_value) == (123_000, 1)
    assert await redis.zscore(f"ft_scrapes_browser:{task.domain}", task.task_id) is None
    assert await redis.zscore("ready:browser:0", task.domain) is None
    assert await redis.zscore(queue._keys.ready, task.task_id) == 123_000
    guard = await redis.hget("lightpanda-b0:legacy-guard", task.task_id)
    assert guard == (
        "production-b0|lightpanda-b0|7|11111111-1111-4111-8111-111111111111|"
        "jobs.example.com|ft_browser|123"
    )
    assert (await queue.audit_conservation(task.route)).accepted


@pytest.mark.parametrize("legacy_key", ["inflight:browser", "deadletter:browser"])
async def test_activation_refuses_legacy_authority_without_partial_mutation(
    redis: Any, legacy_key: str
) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    member = f"scrape|{task.domain}|{task.task_id}"
    await redis.zadd(legacy_key, {member: 999})

    rejected = await queue.activate_legacy(task, legacy_config=_legacy_config(task))

    expected = "legacy_inflight" if legacy_key.startswith("inflight") else "legacy_deadletter"
    assert not rejected.accepted and rejected.reason == expected
    assert await redis.hget(queue._keys.records, task.task_id) is None
    assert await redis.hget("lightpanda-b0:legacy-guard", task.task_id) is None
    assert await redis.zscore(legacy_key, member) == 999


async def test_activation_refuses_duplicate_legacy_memberships(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    await redis.zadd(f"scrapes_browser:{task.domain}", {task.task_id: 321})

    rejected = await queue.activate_legacy(task, legacy_config=_legacy_config(task))

    assert not rejected.accepted and rejected.reason == "legacy_membership_conflict"
    assert await redis.zscore(f"ft_scrapes_browser:{task.domain}", task.task_id) is not None
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is not None
    assert await redis.hget(queue._keys.records, task.task_id) is None


async def test_guard_quarantines_residual_claim_and_future_legacy_schedules(
    redis: Any,
) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    assert (await queue.activate_legacy(task, legacy_config=_legacy_config(task))).accepted

    # Model an old producer racing after the activation script serialized.
    await redis.zadd(f"scrapes_browser:{task.domain}", {task.task_id: 0})
    await redis.zadd("ready:browser:2", {task.domain: 0})
    claim_script = (LUA / "claim_work.lua").read_text(encoding="utf-8")
    claimed = await redis.eval(claim_script, 0, "browser", str(time.time()), "0", "10", "60")
    assert claimed is None
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is None
    assert await redis.zscore("inflight:browser", f"scrape|{task.domain}|{task.task_id}") is None

    enqueue_script = (LUA / "enqueue_task.lua").read_text(encoding="utf-8")
    added = await redis.eval(
        enqueue_script,
        0,
        "browser",
        task.domain,
        task.task_id,
        "1",
        "scrape",
        "0",
        str(time.time()),
        "board_id",
        task.board_id,
    )
    assert added == 0
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is None

    reschedule_script = (LUA / "reschedule_task.lua").read_text(encoding="utf-8")
    rescheduled = await redis.eval(
        reschedule_script,
        0,
        "browser",
        task.domain,
        task.task_id,
        "scrape",
        "2",
    )
    assert rescheduled == 0
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is None


async def test_guard_quarantines_stale_domain_reap_prune_and_completion(redis: Any) -> None:
    task = _task()
    stale_domain = "stale.example.com"
    stale_member = f"scrape|{stale_domain}|{task.task_id}"
    await redis.hset(
        f"scrape:{task.task_id}",
        mapping={"domain": task.domain, "board_id": task.board_id, "source_url": task.source_url},
    )
    await redis.hset("lightpanda-b0:legacy-guard", task.task_id, "owned-by-go")
    await redis.zadd("inflight:browser", {stale_member: 1})

    reaped = await redis.eval(
        (LUA / "reap_expired.lua").read_text(encoding="utf-8"),
        0,
        "browser",
        "2",
        "10",
        "1",
        "2",
    )

    assert reaped == [0, 0, 0]
    assert await redis.zscore("inflight:browser", stale_member) is None
    assert await redis.zscore(f"scrapes_browser:{stale_domain}", task.task_id) is None
    assert await redis.zscore("deadletter:browser", stale_member) is None

    pruned = await redis.eval(
        (LUA / "prune_orphan_scrape.lua").read_text(encoding="utf-8"),
        0,
        task.task_id,
        "1",
    )
    assert pruned == 0
    assert await redis.exists(f"scrape:{task.task_id}")

    await redis.zadd("inflight:browser", {stale_member: 3})
    completed = await redis.eval(
        (LUA / "complete_task.lua").read_text(encoding="utf-8"),
        0,
        "browser",
        "scrape",
        stale_domain,
        task.task_id,
    )
    assert completed == 0
    assert await redis.zscore("inflight:browser", stale_member) == 3
    assert await redis.exists(f"scrape:{task.task_id}")


async def test_legacy_reaper_wrongtype_guard_fails_before_any_mutation(redis: Any) -> None:
    monitor = "monitor|jobs.example.com|board-1"
    scrape = "scrape|jobs.example.com|posting-1"
    await redis.hset("board:board-1", mapping={"monitor": "inline"})
    await redis.hset(
        "scrape:posting-1",
        mapping={"domain": "jobs.example.com", "source_url": "https://jobs.example.com/1"},
    )
    await redis.zadd("inflight:browser", {monitor: 1, scrape: 2})
    await redis.set("lightpanda-b0:legacy-guard", "wrong-type")

    with pytest.raises(ResponseError, match="legacy guard is corrupt"):
        await redis.eval(
            (LUA / "reap_expired.lua").read_text(encoding="utf-8"),
            0,
            "browser",
            "3",
            "10",
            "3",
            "3",
        )

    assert await redis.zrange("inflight:browser", 0, -1, withscores=True) == [
        (monitor, 1.0),
        (scrape, 2.0),
    ]
    assert not await redis.exists("inflight_strikes:browser")
    assert not await redis.exists("monitors_browser:jobs.example.com")


async def test_operator_preflight_suffix_scans_stale_legacy_domains(redis: Any) -> None:
    task = _task()
    await redis.zadd("deadletter:simple", {f"scrape|stale.example.com|{task.task_id}": 1})

    with pytest.raises(activation.ActivationError, match="legacy inflight or dead-letter"):
        await activation._prove_no_suffix_authority(redis, {task.task_id})


async def test_route_fence_precedes_legacy_cutover(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    wrong = RouteIdentity(shard_id="lightpanda-b0", routing_epoch=8, engine_owner="go")
    await queue.initialize(wrong)
    await _seed_legacy_ready(redis, task)

    rejected = await queue.activate_legacy(task, legacy_config=_legacy_config(task))

    assert not rejected.accepted and rejected.reason == "routing_epoch_mismatch"
    assert await redis.hget("lightpanda-b0:legacy-guard", task.task_id) is None
    assert await redis.zscore(f"ft_scrapes_browser:{task.domain}", task.task_id) is not None


async def test_go_owner_cannot_bypass_exclusive_activation(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)

    rejected = await queue.register(task)

    assert not rejected.accepted and rejected.reason == "exclusive_activation_required"
    assert await redis.hget(queue._keys.records, task.task_id) is None


async def test_operational_failure_returns_lease_to_go_with_backoff(redis: Any) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await queue.activate_legacy(task, legacy_config=_legacy_config(task))
    claimed = await queue.claim_next(task.route, lease_ttl_ms=30_000)
    assert claimed.lease is not None and claimed.transition.server_time_ms is not None
    ready_at = claimed.transition.server_time_ms + 60_000

    failed = await queue.fail_at(claimed.lease, ready_at_ms=ready_at)

    assert failed.accepted and failed.reason == "failed_rescheduled"
    record = json.loads(await redis.hget(queue._keys.records, task.task_id))
    assert record["state"] == "ready"
    assert record["failures"] == 1
    assert await redis.zscore(queue._keys.ready, task.task_id) == ready_at
    assert await redis.hget(queue._keys.origin_holders, task.domain) is None
    assert (await queue.audit_conservation(task.route)).accepted


async def test_immediate_shutdown_reschedule_needs_no_advancing_heartbeat(redis: Any) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await queue.activate_legacy(task, legacy_config=_legacy_config(task))
    claimed = await queue.claim_next(task.route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    ready_at = claimed.lease.lease_until_ms - 30_000

    released = await queue.reschedule_at(claimed.lease, ready_at_ms=ready_at)

    assert released.accepted and released.reason == "rescheduled"
    record = json.loads(await redis.hget(queue._keys.records, task.task_id))
    assert record["state"] == "ready"
    assert record["failures"] == 0
    assert await redis.zscore(queue._keys.inflight, task.task_id) is None
    assert await redis.zscore(queue._keys.ready, task.task_id) == ready_at


async def test_missing_legacy_guard_blocks_go_claim_without_mutation(redis: Any) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await queue.activate_legacy(task, legacy_config=_legacy_config(task))
    await redis.hdel("lightpanda-b0:legacy-guard", task.task_id)

    claimed = await queue.claim_next(task.route, lease_ttl_ms=30_000)

    assert claimed.lease is None
    assert claimed.transition.reason == "guard_identity_mismatch"
    assert await redis.zscore(queue._keys.ready, task.task_id) == 0
    assert await redis.zcard(queue._keys.inflight) == 0


async def test_cold_rollback_atomically_restores_ready_and_drops_terminal(redis: Any) -> None:
    ready = _task(ready_at_ms=123_000)
    terminal = _task(
        ready_at_ms=456_000,
        task_id="00000000-0000-4000-8000-000000000002",
    )
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(ready.route)
    for task in (ready, terminal):
        await _seed_legacy_ready(redis, task)
        assert (await queue.activate_legacy(task, legacy_config=_legacy_config(task))).accepted
    await redis.set(f"delay:{ready.domain}", "0")
    claimed = await queue.claim_next(ready.route, lease_ttl_ms=30_000)
    assert claimed.lease is not None and claimed.lease.task.task_id == ready.task_id
    assert (await queue.complete(claimed.lease)).accepted
    await redis.set(f"ratelimit:{ready.domain}", "0")

    rolled_back = await queue.rollback_legacy(
        ready.route,
        plan={ready.task_id: {"action": "drop"}, terminal.task_id: _rollback_schedule(terminal)},
    )

    assert rolled_back.accepted and rolled_back.reason == "rolled_back"
    assert (rolled_back.value, rolled_back.secondary_value) == (1, 1)
    assert await redis.exists(*queue._keys.ordered()) == 0
    assert await redis.hget("lightpanda-b0:legacy-guard", ready.task_id) is None
    assert await redis.hget("lightpanda-b0:legacy-guard", terminal.task_id) is None
    assert await redis.zscore(f"scrapes_browser:{ready.domain}", terminal.task_id) == 999
    assert await redis.zscore(f"ft_scrapes_browser:{ready.domain}", ready.task_id) is None
    assert await redis.zscore("ready:browser:2", ready.domain) == 999
    assert await redis.hget(f"scrape:{terminal.task_id}", "description_r2_hash") == "0"
    assert not await redis.exists(f"scrape:{ready.task_id}")


async def test_cold_rollback_refuses_inflight_without_partial_mutation(redis: Any) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await queue.activate_legacy(task, legacy_config=_legacy_config(task))
    claimed = await queue.claim_next(task.route, lease_ttl_ms=30_000)
    assert claimed.lease is not None

    rejected = await queue.rollback_legacy(
        task.route, plan={task.task_id: _rollback_schedule(task)}
    )

    assert not rejected.accepted and rejected.reason == "rollback_inflight"
    assert await redis.exists(*queue._keys.ordered()) > 0
    assert await redis.hget("lightpanda-b0:legacy-guard", task.task_id) is not None
    assert await redis.zscore(f"ft_scrapes_browser:{task.domain}", task.task_id) is None


async def test_cold_recovery_reaps_forced_kill_then_allows_atomic_rollback(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await queue.activate_legacy(task, legacy_config=_legacy_config(task))
    claimed = await queue.claim_next(task.route, lease_ttl_ms=1)
    assert claimed.lease is not None
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    await asyncio.sleep(0.01)

    settled = await activation.settle_rollback_namespace(redis)

    assert settled == {"inflight_settled": 1, "expired_requeued": 1, "dead_preserved": 0}
    assert await redis.zcard(queue._keys.inflight) == 0
    assert await redis.zscore(queue._keys.ready, task.task_id) is not None
    rolled_back = await queue.rollback_legacy(
        task.route, plan={task.task_id: _rollback_schedule(task)}
    )
    assert rolled_back.accepted
    assert not await redis.exists(*queue._keys.ordered())


async def test_cold_recovery_never_reaps_a_live_lease(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await queue.activate_legacy(task, legacy_config=_legacy_config(task))
    claimed = await queue.claim_next(task.route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setattr(activation, "_ROLLBACK_SETTLE_MAX_SECONDS", 0.01)

    with pytest.raises(activation.ActivationError, match="bounded cold rollback wait"):
        await activation.settle_rollback_namespace(redis)

    stored = await queue.inspect(task.task_id, task.route)
    assert stored is not None and stored.state == "inflight" and stored.failures == 0
    assert await redis.zscore(queue._keys.inflight, task.task_id) is not None


async def test_cold_recovery_deterministically_rolls_back_a_dead_letter(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await queue.activate_legacy(task, legacy_config=_legacy_config(task))
    claimed = await queue.claim_next(task.route, lease_ttl_ms=1)
    assert claimed.lease is not None
    record = json.loads(await redis.hget(queue._keys.records, task.task_id))
    record["failures"] = 2
    await redis.hset(
        queue._keys.records,
        task.task_id,
        json.dumps(record, separators=(",", ":"), sort_keys=True),
    )
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    await asyncio.sleep(0.01)

    settled = await activation.settle_rollback_namespace(redis)

    assert settled == {"inflight_settled": 1, "expired_requeued": 0, "dead_preserved": 1}
    assert await redis.sismember(queue._keys.dead, task.task_id)
    rolled_back = await queue.rollback_legacy(
        task.route, plan={task.task_id: _rollback_schedule(task)}
    )
    assert rolled_back.accepted
    assert not await redis.exists(*queue._keys.ordered())


async def test_cold_recovery_hard_bounds_a_hung_redis_audit(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setattr(activation, "_ROLLBACK_SETTLE_HARD_TIMEOUT_SECONDS", 0.01)

    async def hung_audit(_self: LightpandaB0Queue, _route: RouteIdentity) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(LightpandaB0Queue, "audit_conservation", hung_audit)

    with pytest.raises(activation.ActivationError, match="hard timeout"):
        await activation.settle_rollback_namespace(redis)


async def test_cold_recovery_cli_does_not_open_a_postgresql_pool(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setattr(activation, "get_redis", lambda: redis)

    async def forbidden_pool() -> None:
        raise AssertionError("cold Redis settling must not open PostgreSQL")

    async def closed() -> None:
        return None

    monkeypatch.setattr(activation, "create_local_pool", forbidden_pool)
    monkeypatch.setattr(activation, "close_redis", closed)

    result = await activation._run(argparse.Namespace(command="settle-rollback", cohort="c1"))

    assert result == {
        "operation": "settle-rollback",
        "cohort": "c1",
        "inflight_settled": 0,
        "expired_requeued": 0,
        "dead_preserved": 0,
    }


async def test_cold_rollback_refuses_orphan_global_guard_without_mutation(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    assert (await queue.activate_legacy(task, legacy_config=_legacy_config(task))).accepted
    await redis.hset("lightpanda-b0:legacy-guard", "orphan-posting", "foreign")

    rejected = await queue.rollback_legacy(
        task.route, plan={task.task_id: _rollback_schedule(task)}
    )

    assert not rejected.accepted and rejected.reason == "guard_identity_mismatch"
    assert await redis.exists(*queue._keys.ordered()) > 0
    assert await redis.hget("lightpanda-b0:legacy-guard", task.task_id) is not None
    assert await redis.hget("lightpanda-b0:legacy-guard", "orphan-posting") == "foreign"
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is None


async def test_rollback_rebuilds_first_time_ready_as_exclusive_tier_zero(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    await _seed_legacy_ready(redis, task)
    assert (await queue.activate_legacy(task, legacy_config=_legacy_config(task))).accepted
    await redis.zadd(f"monitors_browser:{task.domain}", {"other-board": 5})
    await redis.zadd(f"scrapes_browser:{task.domain}", {"other-posting": 6})
    await redis.zadd("ready:browser:1", {task.domain: 5})
    await redis.zadd("ready:browser:2", {task.domain: 6})

    rolled_back = await queue.rollback_legacy(
        task.route,
        plan={task.task_id: _rollback_schedule(task, description_hash="")},
    )

    assert rolled_back.accepted
    assert await redis.zscore("ready:browser:0", task.domain) == 0
    assert await redis.zscore("ready:browser:1", task.domain) is None
    assert await redis.zscore("ready:browser:2", task.domain) is None


async def test_production_shaped_uuid_config_routes_by_authoritative_board_slug(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    parser_config = {
        "browser_backend": "lightpanda",
        "render": True,
        "routing_revision": "go-b0-1",
        "timeout": 5_000,
        "wait": "load",
        "wait_fallback": None,
    }
    await redis.hset(
        f"board:{task.board_id}",
        mapping={
            "board_slug": "browser-use-careers",
            "board_url": "https://www.ycombinator.com/companies/browser-use/jobs",
            "crawler_type": "dom",
            "metadata": json.dumps({"scraper_type": "json-ld", "scraper_config": parser_config}),
        },
    )
    await _seed_legacy_ready(redis, task)
    monkeypatch.setattr(producer.settings, "lightpanda_b0_producer_mode", "enabled")
    monkeypatch.setattr(producer.settings, "lightpanda_b0_producer_cohort", "c1")
    monkeypatch.setattr(producer.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(producer.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(producer.settings, "lightpanda_b0_routing_epoch", "7")

    added = await producer.enqueue_if_allowlisted(
        redis,
        domain=task.domain,
        posting_id=task.task_id,
        next_scrape_at=123,
        config={
            "board_id": task.board_id,
            "source_url": task.source_url,
            "scrape_step": "0",
        },
        browser=True,
    )

    assert added is True
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    stored = await queue.inspect(task.task_id, task.route)
    assert stored is not None
    assert stored.task.board_id == task.board_id
    assert stored.task.board_id != "browser-use-careers"
    assert await redis.zscore(f"ft_scrapes_browser:{task.domain}", task.task_id) is None
    assert await redis.zscore("ready:browser:0", task.domain) is None


async def test_operator_feeder_activates_authoritative_existing_schedule(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    parser_config = {
        "browser_backend": "lightpanda",
        "render": True,
        "routing_revision": "go-b0-1",
        "timeout": 5_000,
        "wait": "load",
        "wait_fallback": None,
    }
    await redis.hset(
        f"board:{task.board_id}",
        mapping={
            "board_slug": "browser-use-careers",
            "board_url": "https://www.ycombinator.com/companies/browser-use/jobs",
            "crawler_type": "dom",
            "scraper_needs_browser": "1",
            "scrape_interval_hours": "12",
            "metadata": json.dumps({"scraper_type": "json-ld", "scraper_config": parser_config}),
        },
    )
    await _seed_legacy_ready(redis, task)
    await redis.hset(
        f"scrape:{task.task_id}",
        mapping={
            "description_r2_hash": "-123",
            "scrape_step": "0",
        },
    )
    for target in (producer.settings, activation.settings):
        monkeypatch.setattr(target, "lightpanda_b0_producer_mode", "enabled")
        monkeypatch.setattr(target, "lightpanda_b0_producer_cohort", "c1")
        monkeypatch.setattr(target, "lightpanda_b0_queue_namespace", "production-b0")
        monkeypatch.setattr(target, "lightpanda_b0_shard_id", "lightpanda-b0")
        monkeypatch.setattr(target, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setenv("LIGHTPANDA_B0_SUPERVISOR_MODE", "dark")

    class Pool:
        async def fetch(self, query: str, cohort: list[str]) -> list[dict[str, Any]]:
            assert cohort == ["browser-use-careers"]
            if "FROM job_board" in query:
                return [
                    {
                        "board_id": task.board_id,
                        "board_slug": "browser-use-careers",
                        "board_url": "https://www.ycombinator.com/companies/browser-use/jobs",
                        "is_enabled": True,
                        "board_status": "active",
                        "metadata": {"scraper_type": "json-ld", "scraper_config": parser_config},
                        "crawler_type": "dom",
                        "scraper_needs_browser": True,
                        "scrape_interval_hours": 12,
                    }
                ]
            return [
                {
                    "posting_id": task.task_id,
                    "board_id": task.board_id,
                    "source_url": task.source_url,
                    "description_r2_hash": -123,
                    "next_scrape_at": datetime.fromtimestamp(123, UTC),
                    "lease_active": False,
                    "leased_until": None,
                    "is_active": True,
                    "board_slug": "browser-use-careers",
                    "scraper_needs_browser": True,
                    "scrape_interval_hours": 12,
                }
            ]

    plan = await activation.build_activation_plan(Pool(), redis, cohort="c1")
    summary = await activation.apply_activation_plan(
        Pool(), redis, cohort="c1", expect_digest=plan.digest
    )

    assert (summary["selected"], summary["activated"], summary["already_activated"]) == (
        1,
        1,
        0,
    )
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    stored = await queue.inspect(task.task_id, task.route)
    assert stored is not None
    assert "description_r2_hash" not in json.loads(stored.task.payload)
    assert "scrape_interval_hours" not in json.loads(stored.task.payload)
    assert await redis.hget(f"scrape:{task.task_id}", "domain") == task.domain
    assert await redis.hget(f"scrape:{task.task_id}", "scrape_interval_hours") == "12"


async def test_operator_feeder_refuses_active_legacy_lease_before_mutation(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    for target in (producer.settings, activation.settings):
        monkeypatch.setattr(target, "lightpanda_b0_producer_mode", "enabled")
        monkeypatch.setattr(target, "lightpanda_b0_producer_cohort", "c1")
        monkeypatch.setattr(target, "lightpanda_b0_queue_namespace", "production-b0")
        monkeypatch.setattr(target, "lightpanda_b0_shard_id", "lightpanda-b0")
        monkeypatch.setattr(target, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setenv("LIGHTPANDA_B0_SUPERVISOR_MODE", "dark")

    parser_config = {
        "browser_backend": "lightpanda",
        "render": True,
        "routing_revision": "go-b0-1",
        "timeout": 5_000,
        "wait": "load",
        "wait_fallback": None,
    }
    await redis.hset(
        f"board:{task.board_id}",
        mapping={
            "board_slug": "browser-use-careers",
            "board_url": "https://www.ycombinator.com/companies/browser-use/jobs",
            "crawler_type": "dom",
            "scraper_needs_browser": "1",
            "scrape_interval_hours": "24",
            "metadata": json.dumps({"scraper_type": "json-ld", "scraper_config": parser_config}),
        },
    )

    class Pool:
        async def fetch(self, query: str, _cohort: list[str]) -> list[dict[str, Any]]:
            if "FROM job_board" in query:
                return [
                    {
                        "board_id": task.board_id,
                        "board_slug": "browser-use-careers",
                        "board_url": "https://www.ycombinator.com/companies/browser-use/jobs",
                        "is_enabled": True,
                        "board_status": "active",
                        "metadata": {"scraper_type": "json-ld", "scraper_config": parser_config},
                        "crawler_type": "dom",
                        "scraper_needs_browser": True,
                        "scrape_interval_hours": 24,
                    }
                ]
            return [
                {
                    "posting_id": task.task_id,
                    "board_id": task.board_id,
                    "source_url": task.source_url,
                    "description_r2_hash": None,
                    "next_scrape_at": datetime.fromtimestamp(123, UTC),
                    "lease_active": True,
                    "leased_until": datetime.fromtimestamp(124, UTC),
                    "is_active": True,
                    "board_slug": "browser-use-careers",
                    "scraper_needs_browser": True,
                    "scrape_interval_hours": 24,
                }
            ]

    with pytest.raises(activation.ActivationError, match="PostgreSQL lease"):
        await activation.build_activation_plan(Pool(), redis, cohort="c1")
    assert not await redis.exists("lightpanda-b0:{production-b0}:route")


async def test_operator_rollback_rebuilds_hash_zero_and_cleans_go_fence(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    parser_config = {
        "browser_backend": "lightpanda",
        "render": True,
        "routing_revision": "go-b0-1",
        "timeout": 5_000,
        "wait": "load",
        "wait_fallback": None,
    }
    metadata = {"scraper_type": "json-ld", "scraper_config": parser_config}
    await redis.hset(
        f"board:{task.board_id}",
        mapping={
            "board_slug": "browser-use-careers",
            "board_url": "https://www.ycombinator.com/companies/browser-use/jobs",
            "crawler_type": "dom",
            "scraper_needs_browser": "1",
            "scrape_interval_hours": "12",
            "metadata": json.dumps(metadata),
        },
    )
    await _seed_legacy_ready(redis, task)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    assert (await queue.activate_legacy(task, legacy_config=_legacy_config(task))).accepted
    for target in (producer.settings, activation.settings):
        monkeypatch.setattr(target, "lightpanda_b0_producer_mode", "off")
        monkeypatch.setattr(target, "lightpanda_b0_queue_namespace", "production-b0")
        monkeypatch.setattr(target, "lightpanda_b0_shard_id", "lightpanda-b0")
        monkeypatch.setattr(target, "lightpanda_b0_routing_epoch", "7")

    board_row = {
        "board_id": task.board_id,
        "board_slug": "browser-use-careers",
        "board_url": "https://www.ycombinator.com/companies/browser-use/jobs",
        "is_enabled": True,
        "board_status": "active",
        "metadata": metadata,
        "crawler_type": "dom",
        "scraper_needs_browser": True,
        "scrape_interval_hours": 12,
    }
    posting_row = {
        "posting_id": task.task_id,
        "board_id": task.board_id,
        "source_url": task.source_url,
        "description_r2_hash": 0,
        "is_active": True,
        "next_scrape_at": datetime.fromtimestamp(120, UTC),
        "leased_until": None,
        "lease_active": False,
        "board_slug": "browser-use-careers",
        "is_enabled": True,
        "board_status": "active",
        "scraper_needs_browser": True,
        "scrape_interval_hours": 12,
    }

    class Pool:
        executed = False

        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            del args
            if "FROM job_board" in query:
                return [board_row]
            if "FROM lightpanda_b0_write_fence" in query:
                return [{"posting_id": task.task_id}]
            return [posting_row]

        async def execute(self, query: str, *args: object) -> str:
            assert "DELETE FROM lightpanda_b0_write_fence" in query
            assert isinstance(args[1], list)
            assert task.task_id in args[1]
            self.executed = True
            return "DELETE 1"

        async def fetchval(self, query: str, *args: object) -> int:
            assert "count(*)" in query
            assert isinstance(args[1], list)
            assert task.task_id in args[1]
            return 0

    pool = Pool()
    plan = await activation.build_rollback_plan(pool, redis, cohort="c1")
    entry = plan.document["redis_plan"][task.task_id]  # type: ignore[index]
    assert entry["first_time"] is False
    assert entry["score"] == "120"
    assert entry["config"]["description_r2_hash"] == "0"

    result = await activation.apply_rollback_plan(
        pool, redis, cohort="c1", expect_digest=plan.digest
    )

    assert result["write_fences_remaining"] == 0
    assert pool.executed
    assert await redis.hget(f"scrape:{task.task_id}", "description_r2_hash") == "0"
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) == 120


@pytest.mark.parametrize(
    ("is_enabled", "board_status"),
    [(False, "active"), (True, "retired")],
)
async def test_operator_rollback_drops_posting_when_current_board_is_inactive(
    redis: Any,
    monkeypatch: pytest.MonkeyPatch,
    is_enabled: bool,
    board_status: str,
) -> None:
    task = _task()
    await _seed_legacy_ready(redis, task)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    assert (await queue.activate_legacy(task, legacy_config=_legacy_config(task))).accepted
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    row = {
        "posting_id": task.task_id,
        "board_id": task.board_id,
        "source_url": task.source_url,
        "description_r2_hash": 123,
        "is_active": True,
        "next_scrape_at": datetime.fromtimestamp(120, UTC),
        "leased_until": None,
        "lease_active": False,
        "board_slug": "browser-use-careers",
        "is_enabled": is_enabled,
        "board_status": board_status,
        "scraper_needs_browser": True,
        "scrape_interval_hours": 12,
    }

    class Pool:
        async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
            del args
            if "FROM lightpanda_b0_write_fence" in query:
                return []
            return [row]

    plan = await activation.build_rollback_plan(Pool(), redis, cohort="c1")

    assert plan.document["redis_plan"][task.task_id] == {"action": "drop"}  # type: ignore[index]


async def test_operator_rollback_recovers_empty_initialized_namespace(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await queue.initialize(task.route)
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")

    class Pool:
        async def fetch(self, _query: str, *args: object) -> list[dict[str, Any]]:
            del args
            return []

        async def execute(self, _query: str, *args: object) -> str:
            del args
            return "DELETE 0"

        async def fetchval(self, _query: str, *args: object) -> int:
            del args
            return 0

    plan = await activation.build_rollback_plan(Pool(), redis, cohort="c1")
    assert plan.document["namespace_present"] is True

    await activation.apply_rollback_plan(Pool(), redis, cohort="c1", expect_digest=plan.digest)

    assert not await redis.exists(*queue._keys.ordered())
