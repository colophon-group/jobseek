"""Bounded Redis capacity inventory, orphan cleanup, and scheduler rebuild.

Redis is a derived scheduler/cache layer, but ``noeviction`` makes its growth
an availability concern. This module keeps inspection read-only, makes cleanup
dry-run-first and cursor-bounded, and rebuilds scrape schedules from local
Postgres after an RDB loss.
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asyncpg
import redis.asyncio as aioredis

from src.queries.scrape import _SKIP_NO_SCRAPE_PREDICATE
from src.redis_queue import ScrapeSchedule, enqueue_scrapes, get_redis

MIB = 1024 * 1024
SAMPLE_SIZE = 128
SCAN_COUNT = 2000
MAX_PRUNE_SCAN = 250_000
MAX_PRUNE_DELETE = 100_000
MAX_REBUILD_ROWS = 50_000
_PRUNE_LUA = Path(__file__).with_name("lua").joinpath("prune_orphan_scrape.lua")


@dataclass(frozen=True, slots=True)
class FamilyPolicy:
    name: str
    owner: str
    lifecycle: str
    ttl: str
    budget_keys: int
    budget_items: int
    budget_bytes: int


FAMILY_POLICIES = (
    FamilyPolicy(
        "scrape_config",
        "scrape scheduler",
        "created atomically on enqueue; removed when the final queue/lease/deadletter drains",
        "persistent while reachable",
        600_000,
        600_000,
        384 * MIB,
    ),
    FamilyPolicy(
        "board_config",
        "crawler sync",
        "one hash per configured board; removed by sync when disabled/retired",
        "persistent",
        10_000,
        10_000,
        16 * MIB,
    ),
    FamilyPolicy(
        "scrape_queue_first",
        "scrape scheduler",
        "first scrape; claim moves the item to inflight",
        "persistent until claimed",
        5_000,
        200_000,
        64 * MIB,
    ),
    FamilyPolicy(
        "scrape_queue_recurring",
        "scrape scheduler",
        "recurring scrape; claim moves the item to inflight",
        "persistent until claimed/rescheduled",
        5_000,
        600_000,
        96 * MIB,
    ),
    FamilyPolicy(
        "monitor_queue_first",
        "monitor scheduler",
        "initial board monitor; claim moves the item to inflight",
        "persistent until claimed",
        10_000,
        10_000,
        16 * MIB,
    ),
    FamilyPolicy(
        "monitor_queue_recurring",
        "monitor scheduler",
        "recurring board monitor; claim moves the item to inflight",
        "persistent until claimed/rescheduled",
        10_000,
        10_000,
        16 * MIB,
    ),
    FamilyPolicy(
        "ready_queue",
        "queue Lua",
        "six fixed tier indexes rebuilt by enqueue/reschedule/claim",
        "persistent",
        12,
        20_000,
        16 * MIB,
    ),
    FamilyPolicy(
        "inflight",
        "lease reaper",
        "two fixed lease sets; heartbeat, completion, reschedule, or reaper removes entries",
        "logical lease TTL in score",
        4,
        5_000,
        8 * MIB,
    ),
    FamilyPolicy(
        "inflight_strikes",
        "lease reaper",
        "retry counter cleared on completion; poison tasks move to deadletter",
        "persistent until completion",
        4,
        5_000,
        4 * MIB,
    ),
    FamilyPolicy(
        "deadletter",
        "operator recovery",
        "poison leases retained until explicit retry/prune",
        "persistent",
        4,
        1_000,
        4 * MIB,
    ),
    FamilyPolicy(
        "delay",
        "crawler sync/enqueue",
        "one domain throttle value; overwritten whenever domain work is enqueued",
        "persistent",
        20_000,
        20_000,
        4 * MIB,
    ),
    FamilyPolicy(
        "rate_limit",
        "claim Lua",
        "short per-domain claim throttle",
        "seconds",
        20_000,
        20_000,
        4 * MIB,
    ),
    FamilyPolicy(
        "host_circuit",
        "upstream circuit breaker",
        "failure/open/probe state expires after the configured recovery window",
        "minutes",
        30_000,
        30_000,
        8 * MIB,
    ),
    FamilyPolicy(
        "provider_circuit",
        "provider circuit breaker",
        "incident host/open/probe state expires after the configured recovery window",
        "minutes",
        1_000,
        20_000,
        4 * MIB,
    ),
    FamilyPolicy(
        "other",
        "operator review",
        "unclassified keys must be assigned before becoming material",
        "unknown",
        1_000,
        10_000,
        8 * MIB,
    ),
)
POLICY_BY_NAME = {policy.name: policy for policy in FAMILY_POLICIES}
QUEUE_FAMILIES = {
    "scrape_queue_first",
    "scrape_queue_recurring",
    "monitor_queue_first",
    "monitor_queue_recurring",
    "ready_queue",
    "inflight",
    "deadletter",
}


def classify_key(key: str) -> str:
    if key.startswith("scrape:"):
        return "scrape_config"
    if key.startswith("board:"):
        return "board_config"
    if key.startswith(("ft_scrapes_simple:", "ft_scrapes_browser:")):
        return "scrape_queue_first"
    if key.startswith(("scrapes_simple:", "scrapes_browser:")):
        return "scrape_queue_recurring"
    if key.startswith(("ft_monitors_simple:", "ft_monitors_browser:")):
        return "monitor_queue_first"
    if key.startswith(("monitors_simple:", "monitors_browser:")):
        return "monitor_queue_recurring"
    if key.startswith("ready:"):
        return "ready_queue"
    if key.startswith("inflight_strikes:"):
        return "inflight_strikes"
    if key.startswith("inflight:"):
        return "inflight"
    if key.startswith("deadletter:"):
        return "deadletter"
    if key.startswith("delay:"):
        return "delay"
    if key.startswith("ratelimit:"):
        return "rate_limit"
    if key.startswith(("host_fail:", "host_open:", "host_probe:")):
        return "host_circuit"
    if key.startswith(("provider_fail_hosts:", "provider_open:", "provider_probe:")):
        return "provider_circuit"
    return "other"


async def _scan_keys(redis: aioredis.Redis):
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, count=SCAN_COUNT)
        for key in keys:
            yield str(key)
        if cursor == 0:
            break


async def _queue_keys_and_reachable(redis: aioredis.Redis) -> tuple[list[str], set[str]]:
    queue_keys: list[str] = []
    async for key in _scan_keys(redis):
        if classify_key(key) in QUEUE_FAMILIES:
            queue_keys.append(key)

    reachable: set[str] = set()
    for start in range(0, len(queue_keys), 500):
        batch = queue_keys[start : start + 500]
        pipe = redis.pipeline(transaction=False)
        for key in batch:
            pipe.zrange(key, 0, -1)
        for key, members in zip(batch, await pipe.execute(), strict=True):
            family = classify_key(key)
            if family.startswith("scrape_queue"):
                reachable.update(str(member) for member in members)
            elif family in {"inflight", "deadletter"}:
                for member in members:
                    parts = str(member).split("|", 2)
                    if len(parts) == 3 and parts[0] == "scrape":
                        reachable.add(parts[2])
    return queue_keys, reachable


async def inventory(redis: aioredis.Redis | None = None) -> dict[str, Any]:
    """Return a bounded-label, non-blocking key-family capacity snapshot."""
    started = time.time()
    r = redis or get_redis()
    queue_keys, reachable = await _queue_keys_and_reachable(r)

    key_counts: Counter[str] = Counter()
    scrape_state: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    queue_family_keys: dict[str, list[str]] = defaultdict(list)
    logical_length_keys: dict[str, list[str]] = defaultdict(list)

    async for key in _scan_keys(r):
        family = classify_key(key)
        key_counts[family] += 1
        if len(samples[family]) < SAMPLE_SIZE:
            samples[family].append(key)
        if family in QUEUE_FAMILIES:
            queue_family_keys[family].append(key)
        elif family in {"inflight_strikes", "provider_circuit"}:
            logical_length_keys[family].append(key)
        if family == "scrape_config":
            scrape_state["reachable" if key[7:] in reachable else "orphan"] += 1

    item_counts: Counter[str] = Counter(key_counts)
    for family, keys in queue_family_keys.items():
        total = 0
        for start in range(0, len(keys), 500):
            pipe = r.pipeline(transaction=False)
            for key in keys[start : start + 500]:
                pipe.zcard(key)
            total += sum(int(value or 0) for value in await pipe.execute())
        item_counts[family] = total

    for family, keys in logical_length_keys.items():
        total = 0
        for start in range(0, len(keys), 500):
            pipe = r.pipeline(transaction=False)
            for key in keys[start : start + 500]:
                if family == "inflight_strikes":
                    pipe.hlen(key)
                elif key.startswith("provider_fail_hosts:"):
                    pipe.scard(key)
                else:
                    pipe.exists(key)
            total += sum(int(value or 0) for value in await pipe.execute())
        item_counts[family] = total

    family_rows: list[dict[str, Any]] = []
    for policy in FAMILY_POLICIES:
        keys = samples.get(policy.name, [])
        usages: list[int] = []
        persistent = 0
        if keys:
            pipe = r.pipeline(transaction=False)
            for key in keys:
                pipe.memory_usage(key)
                pipe.pttl(key)
            values = await pipe.execute()
            for index in range(0, len(values), 2):
                usage = values[index]
                ttl = values[index + 1]
                if usage is not None:
                    usages.append(int(usage))
                if int(ttl) == -1:
                    persistent += 1
        average = (sum(usages) / len(usages)) if usages else 0.0
        count = key_counts[policy.name]
        family_rows.append(
            {
                **asdict(policy),
                "keys": count,
                "items": item_counts[policy.name],
                "sample_size": len(usages),
                "sample_average_bytes": round(average, 2),
                "sample_persistent": persistent,
                "estimated_bytes": math.ceil(average * count),
            }
        )

    memory = await r.info("memory")
    persistence = await r.info("persistence")
    stats = await r.info("stats")
    return {
        "snapshot_unixtime": int(time.time()),
        "duration_seconds": round(time.time() - started, 3),
        "db_keys": sum(key_counts.values()),
        "redis": {
            "used_memory_bytes": int(memory.get("used_memory", 0)),
            "used_memory_rss_bytes": int(memory.get("used_memory_rss", 0)),
            "maxmemory_bytes": int(memory.get("maxmemory", 0)),
            "maxmemory_policy": str(memory.get("maxmemory_policy", "unknown")),
            "rdb_last_bgsave_status": str(persistence.get("rdb_last_bgsave_status", "unknown")),
            "aof_enabled": int(persistence.get("aof_enabled", 0)),
            "evicted_keys_total": int(stats.get("evicted_keys", 0)),
            "error_replies_total": int(stats.get("total_error_replies", 0)),
        },
        "scrape_config_state": {
            "reachable": scrape_state["reachable"],
            "orphan": scrape_state["orphan"],
        },
        "families": family_rows,
        "queue_keys_scanned": len(queue_keys),
    }


def format_prometheus(snapshot: dict[str, Any]) -> str:
    lines = [
        f"jobseek_redis_capacity_snapshot_unixtime {snapshot['snapshot_unixtime']}",
        f"jobseek_redis_capacity_scan_duration_seconds {snapshot['duration_seconds']}",
        f"jobseek_redis_capacity_db_keys {snapshot['db_keys']}",
    ]
    redis = snapshot["redis"]
    lines.extend(
        (
            f"jobseek_redis_capacity_used_memory_bytes {redis['used_memory_bytes']}",
            f"jobseek_redis_capacity_maxmemory_bytes {redis['maxmemory_bytes']}",
        )
    )
    for state in ("reachable", "orphan"):
        lines.append(
            "jobseek_redis_scrape_config_state_keys"
            f'{{state="{state}"}} {snapshot["scrape_config_state"][state]}'
        )
    for family in snapshot["families"]:
        label = f'family="{family["name"]}"'
        for metric, field in (
            ("keys", "keys"),
            ("items", "items"),
            ("estimated_bytes", "estimated_bytes"),
            ("budget_keys", "budget_keys"),
            ("budget_items", "budget_items"),
            ("budget_bytes", "budget_bytes"),
        ):
            lines.append(f"jobseek_redis_key_family_{metric}{{{label}}} {family[field]}")
    return "\n".join(lines) + "\n"


async def prune_orphan_scrape_configs(
    *,
    cursor: int = 0,
    max_scanned: int = 50_000,
    max_delete: int = 50_000,
    apply: bool = False,
    redis: aioredis.Redis | None = None,
) -> dict[str, int | bool]:
    """Classify/delete one bounded SCAN slice of unreachable scrape hashes."""
    if not 1 <= max_scanned <= MAX_PRUNE_SCAN:
        raise ValueError(f"max_scanned must be between 1 and {MAX_PRUNE_SCAN}")
    if not 1 <= max_delete <= MAX_PRUNE_DELETE:
        raise ValueError(f"max_delete must be between 1 and {MAX_PRUNE_DELETE}")
    if cursor < 0:
        raise ValueError("cursor must be non-negative")

    r = redis or get_redis()
    sha = await r.script_load(_PRUNE_LUA.read_text())
    scanned = deleted = reachable = missing = missing_domain = would_delete = 0
    current = cursor

    while scanned < max_scanned:
        current, keys = await r.scan(
            cursor=current,
            match="scrape:*",
            count=min(SCAN_COUNT, max_scanned - scanned),
        )
        if not keys:
            if current == 0:
                break
            continue
        remaining = max_scanned - scanned
        keys = list(keys[:remaining])
        scanned += len(keys)

        pipe = r.pipeline(transaction=False)
        for key in keys:
            pipe.evalsha(sha, 0, str(key)[7:], "0")
        classified = [int(value) for value in await pipe.execute()]
        orphan_ids = [
            str(key)[7:] for key, value in zip(keys, classified, strict=True) if value == 1
        ]
        reachable += sum(value == 0 for value in classified)
        missing += sum(value == -1 for value in classified)
        missing_domain += sum(value == -2 for value in classified)
        would_delete += len(orphan_ids)

        if apply and deleted < max_delete and orphan_ids:
            selected = orphan_ids[: max_delete - deleted]
            apply_pipe = r.pipeline(transaction=False)
            for posting_id in selected:
                apply_pipe.evalsha(sha, 0, posting_id, "1")
            deleted += sum(int(value) == 1 for value in await apply_pipe.execute())

        if current == 0:
            break

    return {
        "dry_run": not apply,
        "start_cursor": cursor,
        "next_cursor": int(current),
        "scanned": scanned,
        "reachable": reachable,
        "would_delete": would_delete,
        "deleted": deleted,
        "missing": missing,
        "missing_domain": missing_domain,
        "delete_budget_exhausted": bool(apply and would_delete > deleted),
    }


_REBUILD_QUERY = f"""
SELECT jp.id,
       jp.source_url,
       jp.board_id,
       jp.description_r2_hash,
       jp.next_scrape_at,
       jb.scraper_needs_browser
FROM job_posting jp
JOIN job_board jb ON jb.id = jp.board_id
WHERE jp.is_active
  AND jp.next_scrape_at IS NOT NULL
  AND ($1::uuid IS NULL OR jp.id > $1::uuid)
  AND NOT {_SKIP_NO_SCRAPE_PREDICATE}
ORDER BY jp.id
LIMIT $2
"""


async def rebuild_scrape_schedules(
    pool: asyncpg.Pool,
    *,
    after_id: str | None = None,
    limit: int = 10_000,
    apply: bool = False,
) -> dict[str, Any]:
    """Rehydrate one resumable slice of durable scrape schedules."""
    if not 1 <= limit <= MAX_REBUILD_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_REBUILD_ROWS}")
    rows = await pool.fetch(_REBUILD_QUERY, after_id, limit)
    schedules: list[ScrapeSchedule] = []
    for row in rows:
        domain = urlparse(row["source_url"]).hostname or ""
        has_content = row["description_r2_hash"] is not None
        schedules.append(
            ScrapeSchedule(
                domain=domain,
                posting_id=str(row["id"]),
                next_scrape_at=(row["next_scrape_at"].timestamp() if has_content else 0),
                config={
                    "source_url": row["source_url"],
                    "board_id": str(row["board_id"]),
                    "description_r2_hash": str(row["description_r2_hash"] or ""),
                    "scrape_step": "0",
                },
                browser=bool(row["scraper_needs_browser"]),
                first_time=not has_content,
            )
        )
    results = await enqueue_scrapes(schedules) if apply else []
    return {
        "dry_run": not apply,
        "after_id": after_id,
        "next_after_id": str(rows[-1]["id"]) if rows else after_id,
        "selected": len(rows),
        "newly_enqueued": sum(results),
        "already_present": len(results) - sum(results),
        "complete": len(rows) < limit,
    }
