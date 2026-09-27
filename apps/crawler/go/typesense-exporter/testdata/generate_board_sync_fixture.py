"""Freeze full Redis state from the existing Python board queue publisher."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import fakeredis.aioredis

import src.redis_queue as queue
from src.config import settings


def key(kind, value):
    return {"type": kind, "value": value}


def schedule(**values):
    return {
        "domain": "example.test",
        "board_id": "board",
        "next_check_at": 1100,
        "config": {"board_url": "https://example.test/jobs", "metadata": '{"token":"ü"}'},
        "browser": False,
        "first_time": False,
        **values,
    }


def fixtures():
    cases = [
        {"name": "new", "input": {"schedules": [schedule()]}},
        {
            "name": "browser_first",
            "input": {"schedules": [schedule(browser=True, first_time=True)]},
        },
        {"name": "ats_delay", "input": {"schedules": [schedule(domain="apply.workable.com")]}},
        {"name": "empty_config", "input": {"schedules": [schedule(config={})]}},
        {"name": "no_work", "input": {}},
    ]
    for name, seed in [
        ("existing_due", {"monitors_simple:example.test": key("zset", {"board": 1200})}),
        ("inflight", {"inflight:simple": key("zset", {"monitor|example.test|board": 1200})}),
        (
            "repair_due",
            {"monitor_repair_due:simple": key("hash", {"monitor|example.test|board": "1150"})},
        ),
        ("rate_floor", {"ratelimit:example.test": key("string", "1400")}),
        ("corrupt_guard", {"lightpanda-b0:legacy-guard": key("string", "corrupt")}),
        ("corrupt_rotation", {"ready:rotation:simple": key("string", "corrupt")}),
    ]:
        cases.append({"name": name, "seed": seed, "input": {"schedules": [schedule()]}})
    for browser in [False, True]:
        wt = "browser" if browser else "simple"
        cases.append(
            {
                "name": f"remove_{wt}",
                "input": {"orphans": [["example.test", "old"]]},
                "seed": {
                    f"monitors_{wt}:example.test": key("zset", {"old": 700, "other": 900}),
                    f"ft_monitors_{wt}:example.test": key("zset", {"old": 0}),
                    f"scrapes_{wt}:example.test": key("zset", {"job": 600}),
                    f"ready:rotation:{wt}": key("zset", {"example.test": 1500}),
                    f"ready:{wt}:0": key("zset", {"example.test": 0}),
                    f"monitor_repair_due:{wt}": key("hash", {"monitor|example.test|old": "600"}),
                    "board:old": key("hash", {"domain": "example.test"}),
                },
            }
        )
    cases.append(
        {
            "name": "upsert_then_retire",
            "input": {"schedules": [schedule()], "orphans": [["example.test", "board"]]},
        }
    )
    return cases


async def snapshot(redis):
    result = {}
    for name in sorted(await redis.keys("*")):
        kind = await redis.type(name)
        if kind == "zset":
            value = dict(await redis.zrange(name, 0, -1, withscores=True))
        elif kind == "hash":
            value = await redis.hgetall(name)
        else:
            assert kind == "string"
            value = await redis.get(name)
        result[name] = key(kind, value)
    return result


async def generate():
    cases = fixtures()
    for case in cases:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)
        for name, item in case.setdefault("seed", {}).items():
            if item["type"] == "zset":
                await redis.zadd(name, item["value"])
            elif item["type"] == "hash":
                await redis.hset(name, mapping=item["value"])
            else:
                await redis.set(name, item["value"])
        case["steps"] = []
        with (
            patch.object(queue, "get_redis", return_value=redis),
            patch.object(queue.time, "time", return_value=1000.0),
            patch.object(settings, "throttle_delay_default", 2.0),
            patch.object(settings, "throttle_delay_ats", 0.5),
            patch.object(queue, "_CLAIM_SHA", None),
        ):
            for _ in range(2):
                failed = False
                try:
                    await queue.enqueue_monitors(
                        [queue.MonitorSchedule(**s) for s in case["input"].get("schedules", [])]
                    )
                    await queue.remove_monitors(case["input"].get("orphans", []))
                except Exception:
                    failed = True
                case["steps"].append({"failed": failed, "state": await snapshot(redis)})
        await redis.aclose()
    return cases


if __name__ == "__main__":
    output = json.dumps(asyncio.run(generate()), indent=2, sort_keys=True) + "\n"
    path = Path(__file__).with_name("board_sync_fixture.json")
    if "--check" in sys.argv:
        assert path.read_text() == output, "board sync oracle changed"
    else:
        path.write_text(output)
