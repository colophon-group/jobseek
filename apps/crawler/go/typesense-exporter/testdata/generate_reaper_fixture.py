"""Frozen Redis effects from the retained Python lease-reaper adapter."""

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


def fixtures():
    def monitor(name, **extra):
        seed = {
            "inflight:simple": key("zset", {"monitor|example.test|board": 900}),
            "board:board": key("hash", {"crawler_type": "dom"}),
        }
        seed.update(extra)
        return {"name": name, "wtype": "simple", "seed": seed}

    cases = [
        monitor("expired_monitor", **{"ratelimit:example.test": key("string", "1050")}),
        monitor(
            "fresh_lease", **{"inflight:simple": key("zset", {"monitor|example.test|board": 1100})}
        ),
        monitor(
            "poison",
            **{"inflight_strikes:simple": key("hash", {"monitor|example.test|board": "4"})},
        ),
        monitor(
            "existing_schedule", **{"monitors_simple:example.test": key("zset", {"board": 800})}
        ),
        monitor(
            "repair_precedes_poison",
            **{
                "monitor_repair_due:simple": key("hash", {"monitor|example.test|board": "950"}),
                "inflight_strikes:simple": key("hash", {"monitor|example.test|board": "4"}),
                "monitors_simple:example.test": key("zset", {"board": 980}),
            },
        ),
        monitor(
            "first_time_priority", **{"ft_scrapes_simple:example.test": key("zset", {"other": 700})}
        ),
        monitor(
            "cross_lane_isolation",
            **{"inflight:browser": key("zset", {"monitor|other.test|other": 800})},
        ),
    ]
    missing = monitor("missing_config")
    del missing["seed"]["board:board"]
    cases.append(missing)
    for name, guard, rotation in [
        ("browser_scrape", False, False),
        ("go_guard", True, False),
        ("rotation_floor", False, True),
    ]:
        seed = {
            "inflight:browser": key("zset", {"scrape|example.test|posting": 900}),
            "scrape:posting": key("hash", {"url": "https://example.test/job"}),
        }
        if guard:
            seed["lightpanda-b0:legacy-guard"] = key("hash", {"posting": "epoch"})
            seed["inflight_strikes:browser"] = key("hash", {"scrape|example.test|posting": "4"})
        if rotation:
            seed["ready:rotation:browser"] = key("zset", {"example.test": 2000})
        cases.append({"name": name, "wtype": "browser", "seed": seed})
    cases.append(
        {
            "name": "malformed",
            "wtype": "simple",
            "seed": {
                "inflight:simple": key("zset", {"missing": 900, "one|separator": 900}),
                "inflight_strikes:simple": key("hash", {"missing": "3", "one|separator": "4"}),
            },
        }
    )
    seed = {
        "inflight:simple": key(
            "zset", {f"monitor|example.test|board{i}": 900 + i for i in range(5)}
        )
    }
    seed.update({f"board:board{i}": key("hash", {"crawler_type": "dom"}) for i in range(5)})
    cases.append({"name": "bounded_batches", "wtype": "simple", "batch": 2, "seed": seed})
    for corrupt in (
        "lightpanda-b0:legacy-guard",
        "ready:rotation:simple",
        "monitor_repair_due:simple",
    ):
        cases.append(monitor("corrupt_" + corrupt, **{corrupt: key("string", "wrong-type")}))
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
            assert kind == "string", kind
            value = await redis.get(name)
        result[name] = key(kind, value)
    return result


async def generate():
    result = fixtures()
    for case in result:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True, protocol=2)
        for name, item in case["seed"].items():
            if item["type"] == "zset":
                await redis.zadd(name, item["value"])
            elif item["type"] == "hash":
                await redis.hset(name, mapping=item["value"])
            else:
                await redis.set(name, item["value"])
        case.update(now=1000, max_strikes=5, batch=case.get("batch", 200), steps=[])
        with (
            patch.object(queue, "get_redis", return_value=redis),
            patch.object(queue, "_CLAIM_SHA", None),
            patch.object(queue.time, "time", return_value=1000),
            patch.object(settings, "reaper_batch_size", case["batch"]),
            patch.object(settings, "reaper_max_strikes", case["max_strikes"]),
        ):
            for _ in range(3):
                try:
                    counts = await queue.reap_expired(browser=case["wtype"] == "browser")
                    step = {"failed": False, "result": counts}
                except Exception:
                    if not case["name"].startswith("corrupt_"):
                        raise
                    step = {"failed": True}
                step["state"] = await snapshot(redis)
                case["steps"].append(step)
        await redis.aclose()
    return result


if __name__ == "__main__":
    path = Path(__file__).with_name("lease_reaper_fixture.json")
    content = json.dumps(asyncio.run(generate()), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        assert path.read_text() == content, "Regenerate Python lease-reaper fixture"
    else:
        path.write_text(content)
