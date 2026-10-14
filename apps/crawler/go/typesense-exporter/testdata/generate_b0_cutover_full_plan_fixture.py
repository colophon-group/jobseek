"""Full retained plans from isolated PG rows, actual Redis Lua and captured producer replies."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src import lightpanda_queue as qm
from src.lightpanda import activation
from src.lightpanda.producer_client import ProducerResult
from tests.test_lightpanda_activation import _activate_legacy, _initialize_producer, _task

BOARD = {
    "board_id": "11111111-1111-4111-8111-111111111111",
    "board_slug": "browser-use-careers",
    "board_url": "https://jobs.example.com",
    "is_enabled": True,
    "board_status": "active",
    "metadata": {"number": 1.0, "unicode": "ü"},
    "crawler_type": "json-ld",
    "scraper_needs_browser": True,
    "scrape_interval_hours": 24,
}
POSTING = {
    "posting_id": "00000000-0000-4000-8000-000000000001",
    "board_id": BOARD["board_id"],
    "source_url": "https://jobs.example.com/posting",
    "description_r2_hash": 0,
    "is_active": True,
    "next_scrape_at": datetime.fromtimestamp(120.000001, UTC),
    "lease_active": False,
    "leased_until": None,
    "board_slug": BOARD["board_slug"],
    "scraper_needs_browser": True,
    "scrape_interval_hours": 24,
    "is_enabled": True,
    "board_status": "active",
}


async def snapshot(redis: object) -> dict:
    result = {}
    for key in sorted(await redis.keys("*")):
        kind = await redis.type(key)
        if kind == "hash":
            value = await redis.hgetall(key)
        elif kind == "zset":
            value = [
                [member, score] for member, score in await redis.zrange(key, 0, -1, withscores=True)
            ]
        elif kind == "set":
            value = sorted(await redis.smembers(key))
        elif kind == "string":
            value = await redis.get(key)
        else:
            raise AssertionError(kind)
        result[key] = {"type": kind, "value": value}
    return result


async def case(
    name: str, operation: str, state: str, first: bool, change: str, redis: Redis
) -> dict:
    await redis.flushdb()  # Exclusive temporary Unix-socket fixture created below.
    board, posting = copy.deepcopy(BOARD), copy.deepcopy(POSTING)
    namespace = "production-b0"
    queue = qm.LightpandaB0Queue(redis, namespace=namespace)
    task = _task(ready_at_ms=0 if first else 123_000)
    config = {
        "board_id": task.board_id,
        "source_url": task.source_url,
        "domain": task.domain,
        "description_r2_hash": "17",
        "scrape_step": "0",
        "scrape_interval_hours": "24",
    }
    cached = {
        "board_slug": board["board_slug"],
        "board_url": board["board_url"],
        "crawler_type": board["crawler_type"],
        "scraper_needs_browser": "1",
        "scrape_interval_hours": "24",
        "metadata": json.dumps(board["metadata"]),
    }
    await redis.hset("board:" + board["board_id"], mapping=cached)
    if first:
        posting["description_r2_hash"] = None
        config["description_r2_hash"] = ""
    await redis.hset("scrape:" + task.task_id, mapping=config)
    prefix = "ft_scrapes" if first else "scrapes"
    await redis.zadd(f"{prefix}_browser:{task.domain}", {task.task_id: 0 if first else 42.000001})
    await redis.zadd(
        "ready:browser:" + ("0" if first else "2"), {task.domain: 0 if first else 42.000001}
    )
    if state:
        await _initialize_producer(queue, task.route)
        result = await _activate_legacy(
            queue, task, legacy_config=config, operator_transfer=True, first_time=first
        )
        assert result.accepted, (name, result)
        if state in {"terminal", "dead"}:
            claimed = await queue.claim_next(task.route, lease_ttl_ms=1000)
            assert claimed.lease
            assert (await queue.complete(claimed.lease)).accepted
            if state == "dead":
                raw = json.loads(await redis.hget(queue._keys.records, task.task_id))
                raw.update(state="dead", failures=1)
                await redis.hset(queue._keys.records, task.task_id, json.dumps(raw))
                await redis.srem(queue._keys.terminal, task.task_id)
                await redis.sadd(queue._keys.dead, task.task_id)
    if change == "lease":
        posting["lease_active"] = True
    elif change == "disabled":
        posting["is_enabled"] = False
        board["is_enabled"] = False
    elif change == "inactive":
        posting["is_active"] = False
    elif change == "invalid-interval":
        posting["scrape_interval_hours"] = 0
        board["scrape_interval_hours"] = 0
    elif change == "pruned":
        await redis.delete("scrape:" + task.task_id)
        await redis.zrem(f"{prefix}_browser:{task.domain}", task.task_id)
    elif change == "wrong-cache":
        await redis.hset("board:" + board["board_id"], "board_url", "https://foreign.example")
    elif change == "numeric-cache":
        await redis.hset(
            "board:" + board["board_id"], "metadata", json.dumps({"number": True, "unicode": "ü"})
        )
    elif change == "foreign-suffix":
        await redis.zadd("inflight:simple", {f"scrape|foreign.example|{task.task_id}": 1})
    elif change == "invalid-hint":
        await redis.hset("scrape:" + task.task_id, "description_r2_hash", "-0")
    elif change == "identity-changed":
        posting["source_url"] = "https://jobs.example.com/changed"
    # A completed setup claim leaves a wall-clock rate-limit value. Make that
    # fixture input stable; neither plan contains or trusts a caller clock.
    rate_key = "ratelimit:" + task.domain
    if await redis.exists(rate_key):
        await redis.set(rate_key, "1790000000")
    seed = await snapshot(redis)
    requests, replies, payloads = [], [], []

    class Pool:
        deleted = False

        async def execute(self, query: str, *args: object) -> str:
            assert query == activation._DELETE_ROUTE_GO_FENCES_SQL
            assert args == ("lightpanda-b0", 7)
            self.deleted = True
            return "DELETE 1"

        async def fetchval(self, query: str, *args: object) -> int:
            assert query == activation._COUNT_ROUTE_GO_FENCES_SQL and args
            return 0 if self.deleted else 1

        async def fetch(self, query: str, *args: object) -> list:
            assert args
            if query == activation._BOARDS_SQL:
                return [board]
            if query == activation._GO_FENCE_IDS_SQL:
                return [] if self.deleted else [{"posting_id": task.task_id}]
            return [posting]

    async def manifest(cohort: str) -> ProducerResult:
        occupancy = await redis.hlen(queue._keys.records)
        return ProducerResult(
            "manifest",
            cohort=cohort,
            board_slugs=(board["board_slug"],),
            lifetime_occupancy=occupancy,
            lifetime_headroom=2048 - occupancy,
        )

    async def request(**kwargs: object) -> ProducerResult:
        request = {**kwargs, "expected_digest": kwargs.get("expected_digest", "")}
        requests.append(request)
        record = (
            await queue.inspect(task.task_id, task.route)
            if await redis.exists(queue._keys.route)
            else None
        )
        desired = (
            task
            if record and record.state == "ready"
            else _task(
                ready_at_ms=math.ceil(float(kwargs["next_scrape_at"]) * 1000),
                revision=2 if record else 1,
            )
        )
        doc = {
            "browser": True,
            "config": kwargs["config"],
            "domain": kwargs["domain"],
            "first_time": kwargs["first_time"],
            "next_scrape_at_ms": math.ceil(float(kwargs["next_scrape_at"]) * 1000),
            "operator_transfer": True,
            "posting_id": kwargs["posting_id"],
            "task_payload_sha256": desired.payload_sha256,
            "version": "jobseek.lightpanda.producer/v1",
        }
        if kwargs["legacy_schedule_score"]:
            doc["legacy_schedule_score"] = kwargs["legacy_schedule_score"]
        prep = hashlib.sha256(
            json.dumps(doc, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        result = ProducerResult(
            "prepared",
            preparation_digest=prep,
            payload_sha256=desired.payload_sha256,
            existing_state=record.state if record else "",
            existing_payload_sha256=record.task.payload_sha256 if record else "",
            lifetime_occupancy=1 if state else 0,
            lifetime_headroom=2047 if state else 2048,
        )
        if kwargs["operation"] == "activate":
            assert kwargs["expected_digest"] == prep
            if not await redis.exists(queue._keys.route):
                await _initialize_producer(queue, task.route)
            activated = await _activate_legacy(
                queue,
                desired,
                legacy_config=kwargs["config"],
                operator_transfer=True,
                first_time=kwargs["first_time"],
                legacy_schedule_score=kwargs["legacy_schedule_score"],
                previous_payload_sha256=record.task.payload_sha256 if record else "",
            )
            assert activated.accepted, (name, activated)
            occupancy = await redis.hlen(queue._keys.records)
            result = dataclasses.replace(
                result,
                outcome="activated",
                activated=activated.reason in {"activated", "reactivated"},
                lifetime_occupancy=occupancy,
                lifetime_headroom=2048 - occupancy,
            )
        replies.append(dataclasses.asdict(result))
        payloads.append(desired.payload)
        return result

    settings = SimpleNamespace(
        lightpanda_b0_producer_mode="enabled" if operation == "activate" else "off",
        lightpanda_b0_queue_namespace=namespace,
        lightpanda_b0_shard_id="lightpanda-b0",
        lightpanda_b0_routing_epoch=7,
    )
    vector = {
        "name": name,
        "operation": operation,
        "board": board,
        "posting": {**posting, "next_scrape_at": posting["next_scrape_at"].isoformat()},
        "seed": seed,
        "requests": requests,
        "replies": replies,
        "payloads": payloads,
        "occupancy": 1 if state else 0,
        "source_receipt": "a" * 64,
        "marker_phase": "active" if state else "absent",
    }
    with (
        patch.object(activation, "settings", settings),
        patch.object(activation, "request_manifest", manifest),
        patch.object(activation, "request_task", request),
        patch.object(activation, "_producer_marker_phase", lambda _: vector["marker_phase"]),
    ):
        try:
            plan = (
                await activation.build_activation_plan(Pool(), redis, cohort="c1")
                if operation == "activate"
                else await activation.build_rollback_plan(
                    Pool(),
                    redis,
                    cohort="c1",
                    receipt_state="active" if state else "pending",
                    source_receipt_sha256="a" * 64,
                )
            )
        except (activation.ActivationError, RuntimeError):
            vector["accepted"] = False
        else:
            vector.update(accepted=True, document=plan.document, digest=plan.digest)
            pool = Pool()
            applied = (
                await activation.apply_activation_plan(
                    pool, redis, cohort="c1", expect_digest=plan.digest
                )
                if operation == "activate"
                else await activation.apply_rollback_plan(
                    pool,
                    redis,
                    cohort="c1",
                    expect_digest=plan.digest,
                    receipt_state="active" if state else "pending",
                    source_receipt_sha256="a" * 64,
                )
            )
            vector["applied"] = applied
            vector["after"] = await snapshot(redis)
    return vector


async def main() -> None:
    vectors = []
    server = shutil.which("redis-server")
    assert server, "private Redis executable required"
    with tempfile.TemporaryDirectory(prefix="jobseek-original-b0-plan-") as directory:
        os.chmod(directory, 0o700)
        socket = str(Path(directory) / "control.sock")
        log_path = Path(directory) / "redis.log"
        with log_path.open("xb") as log:
            os.chmod(log_path, 0o600)
            process = subprocess.Popen(
                [
                    server,
                    "--port",
                    "0",
                    "--bind",
                    "127.0.0.1",
                    "--unixsocket",
                    socket,
                    "--unixsocketperm",
                    "700",
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                    "--dir",
                    directory,
                ],
                stdout=log,
                stderr=log,
                env={"PATH": os.environ["PATH"]},
            )
            redis = Redis(unix_socket_path=socket, decode_responses=True, protocol=2)
            try:
                for _ in range(100):
                    try:
                        if await redis.ping():
                            break
                    except (OSError, RedisConnectionError):
                        await asyncio.sleep(0.01)
                else:
                    raise AssertionError("private Redis fixture did not start")
                await generate_vectors(vectors, redis)
            finally:
                await redis.aclose()
                process.terminate()
                process.wait(timeout=10)
    Path(__file__).with_name("python_b0_cutover_full_plan.json").write_text(
        json.dumps(
            {"schema": "jobseek.b0-full-cutover-plans-original/v1", "vectors": vectors},
            default=str,
            ensure_ascii=True,
            indent=2,
        )
        + "\n"
    )


async def generate_vectors(vectors: list, redis: Redis) -> None:

    for state in ("", "ready", "terminal", "dead"):
        for first in (False, True):
            vectors.append(
                await case(
                    f"activate-{state or 'new'}-{first}", "activate", state, first, "", redis=redis
                )
            )
    for change in (
        "lease",
        "disabled",
        "inactive",
        "invalid-interval",
        "pruned",
        "wrong-cache",
        "numeric-cache",
        "foreign-suffix",
        "invalid-hint",
    ):
        vectors.append(await case(f"activate-{change}", "activate", "", False, change, redis=redis))
    for state in ("ready", "terminal", "dead"):
        for change in (
            "",
            "disabled",
            "inactive",
            "lease",
            "identity-changed",
            "invalid-interval",
        ):
            vectors.append(
                await case(
                    f"rollback-{state}-{change}", "rollback", state, False, change, redis=redis
                )
            )
    vectors.append(await case("rollback-empty", "rollback", "", False, "pruned", redis=redis))


asyncio.run(main())
