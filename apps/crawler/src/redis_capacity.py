"""Frozen capacity policy/SQL for native parity; all operations run in Go."""

from __future__ import annotations

from dataclasses import dataclass

from src.queries.scrape import _SKIP_NO_SCRAPE_PREDICATE

MIB = 1024 * 1024


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
        3_000_000,
        3_000_000,
        1536 * MIB,
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
        3_000_000,
        512 * MIB,
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
        "six fixed tier indexes plus two recurring-scrape rotation indexes",
        "persistent",
        12,
        40_000,
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
