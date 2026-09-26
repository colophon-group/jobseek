"""Adversarial exclusivity tests for the legacy-to-Go B0 cutover."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
import pytest
from redis.exceptions import ResponseError

import src.lightpanda.activation as activation
import src.lightpanda_queue as queue_module
from src.lightpanda.producer_client import ProducerResult
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda_queue import (
    LightpandaB0Queue,
    LightpandaB0Task,
    RouteIdentity,
    StoredTask,
    TransitionResult,
)

LUA = Path(queue_module.__file__).parent / "lua"
ROLLBACK_DIGEST = "a" * 64
SOURCE_RECEIPT_SHA256 = "b" * 64


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


async def _seed_legacy_ready(
    redis: Any, task: LightpandaB0Task, *, first_time: bool = False
) -> None:
    score = task.initial_ready_at_ms / 1000
    await redis.hset(
        f"scrape:{task.task_id}",
        mapping={
            "board_id": task.board_id,
            "source_url": task.source_url,
            "domain": task.domain,
            "scrape_step": "0",
            "description_r2_hash": "example",
        },
    )
    prefix = "ft_scrapes" if first_time else "scrapes"
    tier = 0 if first_time else 2
    await redis.zadd(f"{prefix}_browser:{task.domain}", {task.task_id: score})
    await redis.zadd(f"ready:browser:{tier}", {task.domain: score})


def _legacy_config(task: LightpandaB0Task) -> dict[str, str]:
    return {
        "board_id": task.board_id,
        "source_url": task.source_url,
        "scrape_step": "0",
        "scrape_interval_hours": "24",
        "description_r2_hash": "example",
    }


async def _initialize_producer(
    queue: LightpandaB0Queue, route: RouteIdentity, *, cohort: str = "c1"
) -> None:
    raw = await queue._invoke(
        "initialize_producer",
        route=route,
        producer_cohort=cohort,
        producer_board_slugs=(
            {
                "c1": ("browser-use-careers",),
                "c2": ("browser-use-careers", "kandou-ai-careers"),
                "c3": ("browser-use-careers", "eclypsium-careers", "kandou-ai-careers"),
            }[cohort]
        ),
    )
    initialized = queue._decode_transition("initialize_producer", raw, route=route)
    assert initialized.accepted


@pytest.mark.parametrize(
    ("kind", "expected_first_time"),
    [("ft_browser", True), ("recurring_browser", False)],
)
async def test_existing_ready_activation_preserves_bound_guard_schedule_kind(
    redis: Any,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected_first_time: bool,
) -> None:
    task = _task()
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    await redis.hset(
        "lightpanda-b0:legacy-guard",
        task.task_id,
        f"production-b0|lightpanda-b0|7|{task.board_id}|{task.domain}|{kind}|42",
    )
    result = await activation._legacy_preflight(
        redis,
        [
            {
                "posting_id": task.task_id,
                "domain": task.domain,
                "legacy_config": {
                    "domain": task.domain,
                    "board_id": task.board_id,
                    "source_url": task.source_url,
                    "description_r2_hash": "",
                    "scrape_step": "0",
                    "scrape_interval_hours": "12",
                },
            }
        ],
        {task.task_id: StoredTask(task=task, state="ready", failures=0)},
    )

    assert result == {task.task_id: expected_first_time}


async def test_existing_record_snapshot_uses_one_batch_inspection(
    redis: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    assert (await _activate_legacy(queue, task, legacy_config=_legacy_config(task))).accepted
    stored = await queue.inspect(task.task_id, task.route)
    assert stored is not None
    calls = 0

    async def inspect_many(route: RouteIdentity) -> dict[str, StoredTask]:
        nonlocal calls
        assert route == task.route
        calls += 1
        return {task.task_id: stored}

    async def inspect_one(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("activation performed per-record inspection")

    monkeypatch.setattr(queue, "inspect_many", inspect_many, raising=False)
    monkeypatch.setattr(queue, "inspect", inspect_one)

    result = await activation._existing_records(redis, queue, task.route)

    assert result == {task.task_id: stored}
    assert calls == 1


@pytest.mark.parametrize("stored_state", ["terminal", "dead"])
@pytest.mark.parametrize(("description_hash", "expected"), [("", True), ("17", False)])
async def test_terminal_and_dead_reactivation_use_current_postgres_first_time_intent(
    redis: Any,
    monkeypatch: pytest.MonkeyPatch,
    stored_state: str,
    description_hash: str,
    expected: bool,
) -> None:
    task = _task()
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    await redis.hset(
        "lightpanda-b0:legacy-guard",
        task.task_id,
        f"production-b0|lightpanda-b0|7|{task.board_id}|{task.domain}|ft_browser|1",
    )
    result = await activation._legacy_preflight(
        redis,
        [
            {
                "posting_id": task.task_id,
                "domain": task.domain,
                "legacy_config": {
                    "domain": task.domain,
                    "board_id": task.board_id,
                    "source_url": task.source_url,
                    "description_r2_hash": description_hash,
                    "scrape_step": "0",
                    "scrape_interval_hours": "12",
                },
            }
        ],
        {task.task_id: StoredTask(task=task, state=stored_state, failures=0)},
    )

    assert result == {task.task_id: expected}
    assert activation._go_ready_at(
        datetime.fromtimestamp(123, UTC), first_time=result[task.task_id]
    ) == ("0" if expected else "123")


async def _activate_legacy(
    queue: LightpandaB0Queue,
    task: LightpandaB0Task,
    *,
    legacy_config: dict[str, str],
    previous_payload_sha256: str = "",
    operator_transfer: bool = True,
    first_time: bool = False,
    cohort: str = "c1",
) -> TransitionResult:
    raw = await queue._invoke(
        "activate_legacy",
        route=task.route,
        task=task,
        previous_payload_sha256=previous_payload_sha256,
        legacy_config=queue_module._canonical_legacy_config(legacy_config, task),
        operator_transfer=operator_transfer,
        first_time=first_time,
        producer_cohort=cohort,
        producer_board_slugs=(
            {
                "c1": ("browser-use-careers",),
                "c2": ("browser-use-careers", "kandou-ai-careers"),
                "c3": ("browser-use-careers", "eclypsium-careers", "kandou-ai-careers"),
            }[cohort]
        ),
    )
    return queue._decode_transition("activate_legacy", raw, task=task)


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


async def _rollback_schedule_for_bound_guard(
    redis: Any, task: LightpandaB0Task, *, description_hash: str = "0"
) -> dict[str, object]:
    raw = await redis.hget("lightpanda-b0:legacy-guard", task.task_id)
    assert raw is not None
    parts = activation._wire_text(raw).split("|")
    assert len(parts) == 7
    kind, score = parts[-2:]
    assert kind in {"ft_browser", "recurring_browser"}
    schedule = _rollback_schedule(task, description_hash=description_hash, score=score)
    schedule["first_time"] = kind == "ft_browser"
    schedule["score"] = score
    return schedule


async def test_activation_atomically_transfers_one_legacy_membership(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)

    activated = await _activate_legacy(queue, task, legacy_config=_legacy_config(task))

    assert activated.accepted and activated.reason == "activated"
    assert (activated.value, activated.secondary_value) == (123_000, 1)
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is None
    assert await redis.zscore("ready:browser:2", task.domain) is None
    assert await redis.zscore(queue._keys.ready, task.task_id) == 123_000
    guard = await redis.hget("lightpanda-b0:legacy-guard", task.task_id)
    assert guard == (
        "production-b0|lightpanda-b0|7|11111111-1111-4111-8111-111111111111|"
        "jobs.example.com|recurring_browser|123"
    )
    assert (await queue.audit_conservation(task.route)).accepted


async def test_activation_preserves_legacy_domain_rotation_floor(redis: Any) -> None:
    """Cutover cannot move a remaining legacy detail backlog to the front."""
    task = _task(ready_at_ms=100_000)
    remaining_id = "00000000-0000-4000-8000-000000000099"
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await redis.hset(
        f"scrape:{task.task_id}",
        mapping={
            "board_id": task.board_id,
            "source_url": task.source_url,
            "domain": task.domain,
            "description_r2_hash": "example",
            "scrape_step": "0",
            "scrape_interval_hours": "24",
        },
    )
    await redis.hset(
        f"scrape:{remaining_id}",
        mapping={
            "board_id": task.board_id,
            "source_url": f"{task.source_url}/remaining",
            "domain": task.domain,
        },
    )
    await redis.zadd(
        f"scrapes_browser:{task.domain}",
        {remaining_id: 50, task.task_id: 100},
    )
    await redis.zadd("ready:rotation:browser", {task.domain: 500})
    await redis.zadd("ready:browser:2", {task.domain: 1_000})

    activated = await _activate_legacy(queue, task, legacy_config=_legacy_config(task))

    assert activated.accepted and activated.reason == "activated"
    assert await redis.zscore(f"scrapes_browser:{task.domain}", remaining_id) == 50
    assert await redis.zscore("ready:rotation:browser", task.domain) == 500
    assert await redis.zscore("ready:browser:2", task.domain) == 500


@pytest.mark.parametrize("legacy_key", ["inflight:browser", "deadletter:browser"])
async def test_activation_refuses_legacy_authority_without_partial_mutation(
    redis: Any, legacy_key: str
) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    member = f"scrape|{task.domain}|{task.task_id}"
    await redis.zadd(legacy_key, {member: 999})

    rejected = await _activate_legacy(queue, task, legacy_config=_legacy_config(task))

    expected = "legacy_inflight" if legacy_key.startswith("inflight") else "legacy_deadletter"
    assert not rejected.accepted and rejected.reason == expected
    assert await redis.hget(queue._keys.records, task.task_id) is None
    assert await redis.hget("lightpanda-b0:legacy-guard", task.task_id) is None
    assert await redis.zscore(legacy_key, member) == 999


async def test_activation_refuses_duplicate_legacy_memberships(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    await redis.zadd(f"ft_scrapes_browser:{task.domain}", {task.task_id: 321})

    rejected = await _activate_legacy(queue, task, legacy_config=_legacy_config(task))

    assert not rejected.accepted and rejected.reason == "legacy_membership_conflict"
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is not None
    assert await redis.zscore(f"ft_scrapes_browser:{task.domain}", task.task_id) is not None
    assert await redis.hget(queue._keys.records, task.task_id) is None


async def test_guard_quarantines_residual_claim_and_future_legacy_schedules(
    redis: Any,
) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    assert (await _activate_legacy(queue, task, legacy_config=_legacy_config(task))).accepted

    # Model an old producer racing after the activation script serialized.
    await redis.zadd(f"scrapes_browser:{task.domain}", {task.task_id: 0})
    await redis.zadd("ready:browser:2", {task.domain: 0})
    claim_script = (LUA / "claim_work.lua").read_text(encoding="utf-8")
    claimed = await redis.eval(claim_script, 0, "browser", str(time.time()), "0", "10", "60")
    assert claimed is None
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is None
    assert await redis.zscore("inflight:browser", f"scrape|{task.domain}|{task.task_id}") is None

    enqueue_script = (LUA / "enqueue_task.lua").read_text(encoding="utf-8")
    await redis.hset(f"board:{task.board_id}", "board_slug", "browser-use-careers")
    with pytest.raises(ResponseError, match="Go owner covers board"):
        await redis.eval(
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
    await _initialize_producer(queue, wrong)
    await _seed_legacy_ready(redis, task)

    rejected = await _activate_legacy(queue, task, legacy_config=_legacy_config(task))

    assert not rejected.accepted and rejected.reason == "routing_epoch_mismatch"
    assert await redis.hget("lightpanda-b0:legacy-guard", task.task_id) is None
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is not None


async def test_go_owner_cannot_bypass_exclusive_activation(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)

    rejected = await queue.register(task)

    assert not rejected.accepted and rejected.reason == "exclusive_activation_required"
    assert await redis.hget(queue._keys.records, task.task_id) is None


async def test_operational_failure_returns_lease_to_go_with_backoff(redis: Any) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await _activate_legacy(queue, task, legacy_config=_legacy_config(task))
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
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await _activate_legacy(queue, task, legacy_config=_legacy_config(task))
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
    await _initialize_producer(queue, task.route)
    await _activate_legacy(
        queue,
        task,
        legacy_config=_legacy_config(task),
        operator_transfer=False,
    )
    await redis.hdel("lightpanda-b0:legacy-guard", task.task_id)

    claimed = await queue.claim_next(task.route, lease_ttl_ms=30_000)

    assert claimed.lease is None
    assert claimed.transition.reason == "guard_identity_mismatch"
    assert await redis.zscore(queue._keys.ready, task.task_id) == 0
    assert await redis.zcard(queue._keys.inflight) == 0


@pytest.mark.parametrize("cohort", ["c1", "c2", "c3"])
async def test_cold_rollback_atomically_restores_ready_and_drops_terminal(
    redis: Any, cohort: str
) -> None:
    ready = _task(ready_at_ms=123_000)
    terminal = _task(
        ready_at_ms=456_000,
        task_id="00000000-0000-4000-8000-000000000002",
    )
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, ready.route, cohort=cohort)
    for task in (ready, terminal):
        await _seed_legacy_ready(redis, task)
        assert (
            await _activate_legacy(queue, task, legacy_config=_legacy_config(task), cohort=cohort)
        ).accepted
    await redis.set(f"delay:{ready.domain}", "0")
    claimed = await queue.claim_next(ready.route, lease_ttl_ms=30_000)
    assert claimed.lease is not None and claimed.lease.task.task_id == ready.task_id
    assert (await queue.complete(claimed.lease)).accepted
    await redis.set(f"ratelimit:{ready.domain}", "0")

    rolled_back = await queue.rollback_legacy(
        ready.route,
        cohort=cohort,
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        plan={
            ready.task_id: {"action": "drop"},
            terminal.task_id: await _rollback_schedule_for_bound_guard(redis, terminal),
        },
    )

    assert rolled_back.accepted and rolled_back.reason == "rolled_back"
    assert (rolled_back.value, rolled_back.secondary_value) == (1, 1)
    assert await redis.exists(*queue._keys.ordered()) == 0
    assert await redis.hgetall("lightpanda-b0:producer-owner") == {
        "schema": "jobseek.lightpanda.producer-rollback/v1",
        "namespace": "production-b0",
        "shard_id": "lightpanda-b0",
        "routing_epoch": "7",
        "engine_owner": "go",
        "cohort": cohort,
        "rollback_plan_digest": ROLLBACK_DIGEST,
        "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
    }
    assert await redis.hget("lightpanda-b0:legacy-guard", ready.task_id) is None
    assert await redis.hget("lightpanda-b0:legacy-guard", terminal.task_id) is None
    assert await redis.zscore(f"scrapes_browser:{ready.domain}", terminal.task_id) == 456
    assert await redis.zscore(f"ft_scrapes_browser:{ready.domain}", ready.task_id) is None
    assert await redis.zscore("ready:browser:2", ready.domain) == 456
    assert await redis.hget(f"scrape:{terminal.task_id}", "description_r2_hash") == "0"
    assert not await redis.exists(f"scrape:{ready.task_id}")


async def test_cold_rollback_preserves_legacy_domain_rotation_floor(redis: Any) -> None:
    """Rollback scheduling cannot lower an existing tier-2 rotation marker."""
    task = _task(ready_at_ms=123_000)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    assert (await _activate_legacy(queue, task, legacy_config=_legacy_config(task))).accepted
    await redis.zadd("ready:rotation:browser", {task.domain: 5_000})
    await redis.zadd("ready:browser:2", {task.domain: 6_000})

    rolled_back = await queue.rollback_legacy(
        task.route,
        cohort="c1",
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        plan={task.task_id: await _rollback_schedule_for_bound_guard(redis, task)},
    )

    assert rolled_back.accepted and rolled_back.reason == "rolled_back"
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) == 123
    assert await redis.zscore("ready:rotation:browser", task.domain) == 5_000
    assert await redis.zscore("ready:browser:2", task.domain) == 5_000


async def test_cold_rollback_refuses_inflight_without_partial_mutation(redis: Any) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await _activate_legacy(queue, task, legacy_config=_legacy_config(task))
    claimed = await queue.claim_next(task.route, lease_ttl_ms=30_000)
    assert claimed.lease is not None

    rejected = await queue.rollback_legacy(
        task.route,
        cohort="c1",
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        plan={task.task_id: await _rollback_schedule_for_bound_guard(redis, task)},
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
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await _activate_legacy(queue, task, legacy_config=_legacy_config(task))
    claimed = await queue.claim_next(task.route, lease_ttl_ms=1)
    assert claimed.lease is not None
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    await asyncio.sleep(0.01)

    monkeypatch.setattr(activation, "_producer_marker_phase", lambda _cohort: "active")
    settled = await activation.settle_rollback_namespace(
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )

    assert settled == {"inflight_settled": 1, "expired_requeued": 1, "dead_preserved": 0}
    assert await redis.zcard(queue._keys.inflight) == 0
    assert await redis.zscore(queue._keys.ready, task.task_id) is not None
    rolled_back = await queue.rollback_legacy(
        task.route,
        cohort="c1",
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        plan={task.task_id: await _rollback_schedule_for_bound_guard(redis, task)},
    )
    assert rolled_back.accepted
    assert not await redis.exists(*queue._keys.ordered())


async def test_cold_recovery_never_reaps_a_live_lease(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await _activate_legacy(queue, task, legacy_config=_legacy_config(task))
    claimed = await queue.claim_next(task.route, lease_ttl_ms=30_000)
    assert claimed.lease is not None
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setattr(activation, "_ROLLBACK_SETTLE_MAX_SECONDS", 0.01)

    with pytest.raises(activation.ActivationError, match="bounded cold rollback wait"):
        monkeypatch.setattr(activation, "_producer_marker_phase", lambda _cohort: "active")
        await activation.settle_rollback_namespace(
            redis,
            cohort="c1",
            receipt_state="active",
            source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        )

    stored = await queue.inspect(task.task_id, task.route)
    assert stored is not None and stored.state == "inflight" and stored.failures == 0
    assert await redis.zscore(queue._keys.inflight, task.task_id) is not None


async def test_cold_recovery_deterministically_rolls_back_a_dead_letter(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    await redis.set(f"delay:{task.domain}", "0")
    await _activate_legacy(queue, task, legacy_config=_legacy_config(task))
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

    monkeypatch.setattr(activation, "_producer_marker_phase", lambda _cohort: "active")
    settled = await activation.settle_rollback_namespace(
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )

    assert settled == {"inflight_settled": 1, "expired_requeued": 0, "dead_preserved": 1}
    assert await redis.sismember(queue._keys.dead, task.task_id)
    rolled_back = await queue.rollback_legacy(
        task.route,
        cohort="c1",
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        plan={task.task_id: _rollback_schedule(task)},
    )
    assert rolled_back.accepted
    assert not await redis.exists(*queue._keys.ordered())


async def test_cold_recovery_hard_bounds_a_hung_redis_audit(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setattr(activation, "_ROLLBACK_SETTLE_HARD_TIMEOUT_SECONDS", 0.01)

    async def hung_audit(_self: LightpandaB0Queue, _route: RouteIdentity) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(LightpandaB0Queue, "audit_conservation", hung_audit)

    with pytest.raises(activation.ActivationError, match="hard timeout"):
        monkeypatch.setattr(activation, "_producer_marker_phase", lambda _cohort: "active")
        await activation.settle_rollback_namespace(
            redis,
            cohort="c1",
            receipt_state="active",
            source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        )


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

    result = await activation._run(
        argparse.Namespace(
            command="settle-rollback",
            cohort="c1",
            receipt_state="pending",
            source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        )
    )

    assert result == {
        "operation": "settle-rollback",
        "cohort": "c1",
        "inflight_settled": 0,
        "expired_requeued": 0,
        "dead_preserved": 0,
    }


async def test_routing_epoch_reservations_are_db_only_and_monotonic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Pool:
        def __init__(self) -> None:
            self.values = iter((2, 3))
            self.queries: list[str] = []

        def acquire(self) -> Pool:
            return self

        async def __aenter__(self) -> Pool:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def transaction(self) -> Pool:
            return self

        async def execute(self, query: str, *args: object) -> None:
            self.queries.append(f"{query}:{args!r}")

        async def fetchval(self, query: str) -> int:
            self.queries.append(query)
            return next(self.values)

    pool = Pool()
    closes = 0

    async def create_pool() -> Pool:
        return pool

    async def close_pool() -> None:
        nonlocal closes
        closes += 1

    def forbidden_redis() -> Any:
        raise AssertionError("routing epoch allocation must not open Redis")

    monkeypatch.setattr(activation, "create_local_pool", create_pool)
    monkeypatch.setattr(activation, "close_local_pool", close_pool)
    monkeypatch.setattr(activation, "get_redis", forbidden_redis)

    first = await activation._run(argparse.Namespace(command="reserve-epoch"))
    second = await activation._run(argparse.Namespace(command="reserve-epoch"))

    assert first == {"routing_epoch": 2}
    assert second == {"routing_epoch": 3}
    lock_call = (
        f"{activation._LOCK_ROUTING_EPOCH_ALLOCATOR_SQL}:"
        f"{(activation._ROUTING_EPOCH_ADVISORY_LOCK_ID,)!r}"
    )
    assert pool.queries == [
        lock_call,
        activation._RESERVE_ROUTING_EPOCH_SQL,
        lock_call,
        activation._RESERVE_ROUTING_EPOCH_SQL,
    ]
    assert closes == 2


async def test_routing_epoch_attestation_requires_called_exact_postgres_high_water(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Pool:
        async def fetchrow(self, query: str) -> dict[str, object]:
            assert query == activation._CURRENT_ROUTING_EPOCH_SQL
            return {"last_value": 7, "is_called": True}

    async def create_pool() -> Pool:
        return Pool()

    async def closed() -> None:
        return None

    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setattr(activation, "create_local_pool", create_pool)
    monkeypatch.setattr(activation, "close_local_pool", closed)
    monkeypatch.setattr(
        activation,
        "get_redis",
        lambda: (_ for _ in ()).throw(AssertionError("epoch attestation reached Redis")),
    )

    result = await activation._run(argparse.Namespace(command="attest-epoch"))

    assert result == {"routing_epoch": 7, "current": True}


@pytest.mark.parametrize(
    ("last_value", "is_called"),
    [(8, True), (7, False), (0, True), (10_000_000_000_000, True)],
)
async def test_routing_epoch_attestation_rejects_stale_or_invalid_high_water(
    monkeypatch: pytest.MonkeyPatch, last_value: int, is_called: bool
) -> None:
    class Pool:
        async def fetchrow(self, _query: str) -> dict[str, object]:
            return {"last_value": last_value, "is_called": is_called}

    async def create_pool() -> Pool:
        return Pool()

    async def closed() -> None:
        return None

    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setattr(activation, "create_local_pool", create_pool)
    monkeypatch.setattr(activation, "close_local_pool", closed)

    with pytest.raises(activation.ActivationError, match="not the current PostgreSQL high-water"):
        await activation._run(argparse.Namespace(command="attest-epoch"))


@pytest.mark.parametrize("failure", ["missing", "permission", "exhausted"])
async def test_routing_epoch_allocator_failure_is_fail_closed_before_redis(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    closed = False

    class Pool:
        async def fetchval(self, query: str) -> int:
            assert query == activation._RESERVE_ROUTING_EPOCH_SQL
            raise RuntimeError(failure)

    async def create_pool() -> Pool:
        return Pool()

    async def close_pool() -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr(activation, "create_local_pool", create_pool)
    monkeypatch.setattr(activation, "close_local_pool", close_pool)
    monkeypatch.setattr(
        activation,
        "get_redis",
        lambda: (_ for _ in ()).throw(AssertionError("allocator failure reached Redis")),
    )

    with pytest.raises(activation.ActivationError, match="routing epoch reservation failed"):
        await activation._run(argparse.Namespace(command="reserve-epoch"))
    assert closed


async def test_cold_rollback_refuses_orphan_global_guard_without_mutation(redis: Any) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task)
    assert (await _activate_legacy(queue, task, legacy_config=_legacy_config(task))).accepted
    await redis.hset("lightpanda-b0:legacy-guard", "orphan-posting", "foreign")

    rejected = await queue.rollback_legacy(
        task.route,
        cohort="c1",
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        plan={task.task_id: _rollback_schedule(task)},
    )

    assert not rejected.accepted and rejected.reason == "guard_identity_mismatch"
    assert await redis.exists(*queue._keys.ordered()) > 0
    assert await redis.hget("lightpanda-b0:legacy-guard", task.task_id) is not None
    assert await redis.hget("lightpanda-b0:legacy-guard", "orphan-posting") == "foreign"
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) is None


async def test_rollback_rebuilds_first_time_ready_as_exclusive_tier_zero(redis: Any) -> None:
    task = _task(ready_at_ms=0)
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    await _seed_legacy_ready(redis, task, first_time=True)
    assert (
        await _activate_legacy(queue, task, legacy_config=_legacy_config(task), first_time=True)
    ).accepted
    await redis.zadd(f"monitors_browser:{task.domain}", {"other-board": 5})
    await redis.zadd(f"scrapes_browser:{task.domain}", {"other-posting": 6})
    await redis.zadd("ready:browser:1", {task.domain: 5})
    await redis.zadd("ready:browser:2", {task.domain: 6})

    rolled_back = await queue.rollback_legacy(
        task.route,
        cohort="c1",
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        plan={task.task_id: _rollback_schedule(task, description_hash="")},
    )

    assert rolled_back.accepted
    assert await redis.zscore("ready:browser:0", task.domain) == 0
    assert await redis.zscore("ready:browser:1", task.domain) is None
    assert await redis.zscore("ready:browser:2", task.domain) is None


async def test_operator_feeder_activates_authoritative_existing_schedule(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(ready_at_ms=0)
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
    await _seed_legacy_ready(redis, task, first_time=True)
    await redis.hset(
        f"scrape:{task.task_id}",
        mapping={
            "description_r2_hash": "-123",
            "scrape_step": "0",
        },
    )
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "enabled")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setenv("LIGHTPANDA_B0_SUPERVISOR_MODE", "dark")

    async def request_manifest(cohort: str) -> SimpleNamespace:
        assert cohort == "c1"
        queue = LightpandaB0Queue(redis, namespace="production-b0")
        occupancy = await redis.hlen(queue._keys.records)
        return SimpleNamespace(
            outcome="manifest",
            cohort="c1",
            board_slugs=("browser-use-careers",),
            lifetime_occupancy=occupancy,
            lifetime_capacity=2_048,
            lifetime_headroom=2_048 - occupancy,
        )

    producer_requests: list[dict[str, Any]] = []

    async def request_task(**request: Any) -> ProducerResult:
        producer_requests.append(request)
        queue = LightpandaB0Queue(redis, namespace="production-b0")
        stored = (
            await queue.inspect(task.task_id, task.route)
            if await redis.exists(queue._keys.route)
            else None
        )
        if request["operation"] == "prepare":
            return ProducerResult(
                "prepared",
                "a" * 64,
                task.payload_sha256,
                stored.state if stored else "",
                stored.task.payload_sha256 if stored else "",
            )
        assert request["expected_digest"] == "a" * 64
        await _initialize_producer(queue, task.route)
        result = await _activate_legacy(
            queue,
            task,
            legacy_config=request["config"],
            operator_transfer=True,
            first_time=request["first_time"],
        )
        assert result.accepted
        return ProducerResult(
            "activated", "a" * 64, task.payload_sha256, activated=result.reason == "activated"
        )

    monkeypatch.setattr(activation, "request_task", request_task)
    monkeypatch.setattr(activation, "request_manifest", request_manifest)

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
    assert plan.tasks[0]["first_time"] is True
    assert plan.tasks[0]["next_scrape_at"] == "0"
    summary = await activation.apply_activation_plan(
        Pool(), redis, cohort="c1", expect_digest=plan.digest
    )

    assert (summary["selected"], summary["activated"], summary["already_activated"]) == (
        1,
        1,
        0,
    )
    assert [request["next_scrape_at"] for request in producer_requests] == [0.0, 0.0, 0.0]
    assert all(request["first_time"] is True for request in producer_requests)
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
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "enabled")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setenv("LIGHTPANDA_B0_SUPERVISOR_MODE", "dark")

    async def request_manifest(cohort: str) -> SimpleNamespace:
        assert cohort == "c1"
        return SimpleNamespace(
            outcome="manifest",
            cohort="c1",
            board_slugs=("browser-use-careers",),
            lifetime_occupancy=0,
            lifetime_capacity=2_048,
            lifetime_headroom=2_048,
        )

    monkeypatch.setattr(activation, "request_manifest", request_manifest)

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


async def test_operator_rollback_preserves_ready_guard_due_and_cleans_go_fence(
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
    await redis.zrem(f"ft_scrapes_browser:{task.domain}", task.task_id)
    await redis.zrem("ready:browser:0", task.domain)
    await redis.zadd(f"scrapes_browser:{task.domain}", {task.task_id: 42})
    await redis.zadd("ready:browser:2", {task.domain: 42})
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    assert (await _activate_legacy(queue, task, legacy_config=_legacy_config(task))).accepted
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")

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
            assert args == ("lightpanda-b0", 7)
            self.executed = True
            return "DELETE 1"

        async def fetchval(self, query: str, *args: object) -> int:
            assert "count(*)" in query
            assert args == ("lightpanda-b0", 7)
            return 0

    pool = Pool()
    monkeypatch.setattr(activation, "_producer_marker_phase", lambda _cohort: "active")
    plan = await activation.build_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )
    entry = plan.document["redis_plan"][task.task_id]  # type: ignore[index]
    assert entry["first_time"] is False
    assert entry["worker_type"] == "browser"
    assert entry["score"] == "42"
    assert entry["config"]["description_r2_hash"] == "0"

    result = await activation.apply_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        expect_digest=plan.digest,
    )

    assert result["write_fences_remaining"] == 0
    assert pool.executed
    assert await redis.hget(f"scrape:{task.task_id}", "description_r2_hash") == "0"
    assert await redis.zscore(f"scrapes_browser:{task.domain}", task.task_id) == 42


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
    await _initialize_producer(queue, task.route)
    assert (await _activate_legacy(queue, task, legacy_config=_legacy_config(task))).accepted
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

    monkeypatch.setattr(activation, "_producer_marker_phase", lambda _cohort: "active")
    plan = await activation.build_rollback_plan(
        Pool(),
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )

    assert plan.document["redis_plan"][task.task_id] == {"action": "drop"}  # type: ignore[index]


async def test_operator_rollback_recovers_empty_initialized_namespace(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
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

    monkeypatch.setattr(activation, "_producer_marker_phase", lambda _cohort: "active")
    plan = await activation.build_rollback_plan(
        Pool(),
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )
    assert plan.document["namespace_present"] is True

    await activation.apply_rollback_plan(
        Pool(),
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        expect_digest=plan.digest,
    )

    assert not await redis.exists(*queue._keys.ordered())
    assert (
        await redis.hget("lightpanda-b0:producer-owner", "schema")
        == "jobseek.lightpanda.producer-rollback/v1"
    )


async def test_operator_rollback_noops_when_sentinel_precedes_redis_initialization(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host wrapper can complete recovery after the sentinel fsync crash window."""

    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")

    class Pool:
        executed = False

        async def fetch(self, _query: str, *args: object) -> list[dict[str, Any]]:
            del args
            return []

        async def execute(self, _query: str, *args: object) -> str:
            assert args == ("lightpanda-b0", 7)
            self.executed = True
            return "DELETE 0"

        async def fetchval(self, _query: str, *args: object) -> int:
            assert args == ("lightpanda-b0", 7) and self.executed
            return 0

    pool = Pool()
    plan = await activation.build_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="pending",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )
    assert plan.document["namespace_present"] is False
    assert plan.document["redis_plan"] == {}

    summary = await activation.apply_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="pending",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        expect_digest=plan.digest,
    )

    assert summary["ready_restored"] == 0
    assert summary["terminal_dropped"] == 0
    assert summary["write_fences_remaining"] == 0
    assert (
        await redis.hget("lightpanda-b0:producer-owner", "schema")
        == "jobseek.lightpanda.producer-rollback/v1"
    )


@pytest.mark.parametrize("redis_present", [False, True], ids=("redis-absent", "redis-present"))
@pytest.mark.parametrize("receipt_state", ["active", "pending"])
@pytest.mark.parametrize("marker_phase", ["absent", "preparing", "partial", "active", "unsafe"])
async def test_rollback_receipt_marker_redis_decision_matrix(
    redis: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    redis_present: bool,
    receipt_state: str,
    marker_phase: str,
) -> None:
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    authority = tmp_path / "producer-authority"
    authority.mkdir(mode=0o700)
    monkeypatch.setattr(activation, "_PRODUCER_AUTHORITY_DIRECTORY", authority)
    monkeypatch.setattr(activation, "_PRODUCER_AUTHORITY_UID", os.geteuid())
    marker = authority / activation._PRODUCER_ACTIVATION_MARKER
    identity = {
        "board_slugs": ["browser-use-careers"],
        "cohort": "c1",
        "engine_owner": "go",
        "namespace": "production-b0",
        "routing_epoch": 7,
        "schema": activation._PRODUCER_SENTINEL_SCHEMA,
        "shard_id": "lightpanda-b0",
    }
    encoded, _digest = activation._canonical(identity)
    payloads = {
        "preparing": b"P\n" + encoded.encode("ascii") + b"\n",
        "partial": b'P\n{"board_slugs"',
        "active": b"A\n" + encoded.encode("ascii") + b"\n",
        "unsafe": b'A\n{"board_slugs"',
    }
    if marker_phase != "absent":
        marker.write_bytes(payloads[marker_phase])
        marker.chmod(0o600)

    if redis_present:
        task = _task()
        queue = LightpandaB0Queue(redis, namespace="production-b0")
        await _initialize_producer(queue, task.route)

    class Pool:
        async def fetch(self, _query: str, *args: object) -> list[dict[str, Any]]:
            del args
            return []

    allowed = (
        redis_present
        and (
            (receipt_state == "active" and marker_phase == "active")
            or (receipt_state == "pending" and marker_phase in {"preparing", "active"})
        )
    ) or (
        not redis_present
        and receipt_state == "pending"
        and marker_phase in {"absent", "preparing", "partial"}
    )
    if not allowed:
        with pytest.raises(activation.ActivationError):
            await activation.build_rollback_plan(
                Pool(),
                redis,
                cohort="c1",
                receipt_state=receipt_state,
                source_receipt_sha256=SOURCE_RECEIPT_SHA256,
            )
        return

    plan = await activation.build_rollback_plan(
        Pool(),
        redis,
        cohort="c1",
        receipt_state=receipt_state,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )
    assert plan.document["receipt_state"] == receipt_state
    assert plan.document["producer_marker_phase"] == marker_phase
    assert plan.document["namespace_present"] is redis_present


async def test_rollback_tombstone_recovers_postgres_failure_and_sentinel_clear_crash(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    marker_phase = "active"
    monkeypatch.setattr(activation, "_producer_marker_phase", lambda _cohort: marker_phase)

    class Pool:
        fail_delete = True

        async def fetch(self, _query: str, *args: object) -> list[dict[str, Any]]:
            del args
            return []

        async def execute(self, _query: str, *args: object) -> str:
            assert args == ("lightpanda-b0", 7)
            if self.fail_delete:
                self.fail_delete = False
                raise RuntimeError("injected PostgreSQL failure")
            return "DELETE 0"

        async def fetchval(self, _query: str, *args: object) -> int:
            assert args == ("lightpanda-b0", 7)
            return 0

    pool = Pool()
    plan = await activation.build_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )
    with pytest.raises(RuntimeError, match="injected PostgreSQL failure"):
        await activation.apply_rollback_plan(
            pool,
            redis,
            cohort="c1",
            receipt_state="active",
            source_receipt_sha256=SOURCE_RECEIPT_SHA256,
            expect_digest=plan.digest,
        )

    assert not await redis.exists(*queue._keys.ordered())
    assert await redis.hget("lightpanda-b0:producer-owner", "rollback_plan_digest") == plan.digest

    marker_phase = "absent"  # crash after the Go sentinel clear but before receipt publication
    recovery = await activation.build_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )
    assert activation._canonical(recovery.document)[1] == recovery.digest
    assert recovery.rollback_commit_digest == plan.digest
    summary = await activation.apply_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        expect_digest=recovery.digest,
    )
    stale_clear = await queue.clear_rollback_tombstone(
        task.route,
        cohort="c1",
        rollback_plan_digest="d" * 64,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        allow_absent=False,
    )
    assert not stale_clear.accepted and stale_clear.reason == "namespace_corrupt"
    cleared = await queue.clear_rollback_tombstone(
        task.route,
        cohort="c1",
        rollback_plan_digest=plan.digest,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        allow_absent=False,
    )
    assert cleared.accepted and cleared.reason == "rollback_tombstone_cleared"
    assert summary["rollback_plan_digest"] == plan.digest


async def test_rollback_tombstone_recovers_lost_redis_reply_and_rejects_stale_aba(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, task.route)
    monkeypatch.setattr(activation.settings, "lightpanda_b0_producer_mode", "off")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_queue_namespace", "production-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_shard_id", "lightpanda-b0")
    monkeypatch.setattr(activation.settings, "lightpanda_b0_routing_epoch", "7")
    monkeypatch.setattr(activation, "_producer_marker_phase", lambda _cohort: "active")

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

    pool = Pool()
    plan = await activation.build_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )
    original = LightpandaB0Queue.rollback_legacy

    async def commit_then_lose_reply(self: LightpandaB0Queue, *args: Any, **kwargs: Any) -> Any:
        await original(self, *args, **kwargs)
        raise TimeoutError("injected lost Redis reply")

    monkeypatch.setattr(LightpandaB0Queue, "rollback_legacy", commit_then_lose_reply)
    with pytest.raises(TimeoutError, match="lost Redis reply"):
        await activation.apply_rollback_plan(
            pool,
            redis,
            cohort="c1",
            receipt_state="active",
            source_receipt_sha256=SOURCE_RECEIPT_SHA256,
            expect_digest=plan.digest,
        )
    monkeypatch.setattr(LightpandaB0Queue, "rollback_legacy", original)

    with pytest.raises(activation.ActivationError, match="stale, or unbound"):
        await activation.build_rollback_plan(
            pool,
            redis,
            cohort="c1",
            receipt_state="active",
            source_receipt_sha256="c" * 64,
        )
    recovery = await activation.build_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
    )
    await activation.apply_rollback_plan(
        pool,
        redis,
        cohort="c1",
        receipt_state="active",
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        expect_digest=recovery.digest,
    )


async def test_new_epoch_rejects_restored_old_rdb_and_stale_tombstone(redis: Any) -> None:
    route_one = RouteIdentity(shard_id="lightpanda-b0", routing_epoch=7, engine_owner="go")
    route_two = RouteIdentity(shard_id="lightpanda-b0", routing_epoch=8, engine_owner="go")
    task = _task()
    queue = LightpandaB0Queue(redis, namespace="production-b0")
    await _initialize_producer(queue, route_one)
    await _seed_legacy_ready(redis, task)
    assert (await _activate_legacy(queue, task, legacy_config=_legacy_config(task))).accepted

    old_rdb: dict[str, tuple[int, bytes]] = {}
    for key in await redis.keys("*"):
        payload = await redis.dump(key)
        assert payload is not None
        old_rdb[str(key)] = (await redis.pttl(key), payload)

    rolled_back = await queue.rollback_legacy(
        route_one,
        cohort="c1",
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        plan={task.task_id: {"action": "drop"}},
    )
    assert rolled_back.accepted
    tombstone = await redis.dump("lightpanda-b0:producer-owner")
    assert tombstone is not None
    cleared = await queue.clear_rollback_tombstone(
        route_one,
        cohort="c1",
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        allow_absent=False,
    )
    assert cleared.accepted

    await _initialize_producer(queue, route_two)
    current_owner = await redis.dump("lightpanda-b0:producer-owner")
    assert current_owner is not None

    # Receipt/route E2 cannot clear a replayed E1 rollback tombstone.
    await redis.delete("lightpanda-b0:producer-owner")
    await redis.restore("lightpanda-b0:producer-owner", 0, tombstone)
    stale_tombstone = await queue.clear_rollback_tombstone(
        route_two,
        cohort="c1",
        rollback_plan_digest=ROLLBACK_DIGEST,
        source_receipt_sha256=SOURCE_RECEIPT_SHA256,
        allow_absent=False,
    )
    assert not stale_tombstone.accepted
    assert stale_tombstone.reason == "namespace_corrupt"
    assert await redis.hget("lightpanda-b0:producer-owner", "routing_epoch") == "7"

    # Model a full Redis/RDB restore after E2 became current. Both the route
    # and the exact producer owner revert to E1, while the caller stays E2.
    await redis.flushall()
    for key, (ttl, payload) in old_rdb.items():
        await redis.restore(key, max(ttl, 0), payload)
    before = {
        "route": await redis.hgetall(queue._keys.route),
        "owner": await redis.hgetall("lightpanda-b0:producer-owner"),
        "ready": await redis.zcard(queue._keys.ready),
        "inflight": await redis.zcard(queue._keys.inflight),
    }

    claim = await queue.claim_next(route_two, lease_ttl_ms=30_000)
    audit = await queue.audit_conservation(route_two)

    assert claim.lease is None
    assert claim.transition.reason == "routing_epoch_mismatch"
    assert audit.reason == "routing_epoch_mismatch"
    assert before == {
        "route": await redis.hgetall(queue._keys.route),
        "owner": await redis.hgetall("lightpanda-b0:producer-owner"),
        "ready": await redis.zcard(queue._keys.ready),
        "inflight": await redis.zcard(queue._keys.inflight),
    }
