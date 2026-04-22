"""Tiered domain-based Redis queue with Lua scripts.

Ready queues (6 global ZSETs, 3 tiers × 2 worker types):
    ready:{wtype}:0  — domains with first-time work
    ready:{wtype}:1  — domains with due monitors
    ready:{wtype}:2  — domains with due scrapes

Per-domain task queues:
    ft_monitors_{wtype}:{domain}  — first-time monitors
    ft_scrapes_{wtype}:{domain}   — first-time scrapes
    monitors_{wtype}:{domain}     — recurring monitors
    scrapes_{wtype}:{domain}      — recurring scrapes

Rate limiting (shared across worker types):
    ratelimit:{domain}  — STRING with TTL
    delay:{domain}      — per-domain delay (0.5 for ATS, 2.0 default)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import redis.asyncio as aioredis
import structlog

from src.config import settings

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_pool: aioredis.ConnectionPool | None = None


def get_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
        )
    return _pool


def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=get_pool())


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BoardWork:
    board_id: str
    config: dict
    domain: str = ""


@dataclass
class ScrapeWork:
    posting_id: str
    source_url: str
    board_id: str
    description_r2_hash: int | None
    scraper_needs_browser: bool
    scrape_interval_hours: int
    scrape_step: int = 0
    domain: str = ""


@dataclass
class WorkItem:
    """A claimed work item — either a board monitor or a scrape."""

    kind: str  # "monitor" or "scrape"
    board_work: BoardWork | None = None
    scrape_work: ScrapeWork | None = None


# ---------------------------------------------------------------------------
# Lua script loading
# ---------------------------------------------------------------------------

_LUA_DIR = Path(__file__).parent / "lua"
_CLAIM_SHA: str | None = None
_ENQUEUE_SHA: str | None = None
_RESCHEDULE_SHA: str | None = None


async def _load_scripts() -> None:
    """Load Lua scripts into Redis and cache their SHAs."""
    global _CLAIM_SHA, _ENQUEUE_SHA, _RESCHEDULE_SHA
    if _CLAIM_SHA is not None:
        return
    r = get_redis()
    _CLAIM_SHA = await r.script_load((_LUA_DIR / "claim_work.lua").read_text())
    _ENQUEUE_SHA = await r.script_load((_LUA_DIR / "enqueue_task.lua").read_text())
    _RESCHEDULE_SHA = await r.script_load((_LUA_DIR / "reschedule_task.lua").read_text())


# ---------------------------------------------------------------------------
# Domain delay
# ---------------------------------------------------------------------------

_KNOWN_ATS_DOMAINS = frozenset(
    {
        "greenhouse",
        "lever",
        "ashby",
        "workable",
        "smartrecruiters",
        "hireology",
        "rippling",
        "recruitee",
        "personio",
        "workday",
        "pinpoint",
        "gem",
        "rss",
        "bite",
        "breezy",
        "join",
        "softgarden",
        "traffit",
        "mokahr",
        "dvinci",
        "recruiter_co_kr",
        "oracle_hcm",
        "eightfold",
        "accenture",
        "deel",
    }
)


def delay_for_domain(domain: str) -> float:
    """Return the throttle delay for a domain."""
    if domain in _KNOWN_ATS_DOMAINS:
        return settings.throttle_delay_ats
    return settings.throttle_delay_default


# ---------------------------------------------------------------------------
# Enqueue operations
# ---------------------------------------------------------------------------


async def enqueue_monitor(
    domain: str,
    board_id: str,
    next_check_at: float,
    config: dict,
    *,
    browser: bool = False,
    first_time: bool = False,
) -> bool:
    """Enqueue a board monitor task. Returns True if newly added."""
    await _load_scripts()
    r = get_redis()
    wtype = "browser" if browser else "simple"

    added = await r.evalsha(
        _ENQUEUE_SHA,
        0,
        wtype,
        domain,
        board_id,
        str(next_check_at),
        "monitor",
        "1" if first_time else "0",
        str(time.time()),
    )

    # Set config hash + domain delay
    if config:
        config["domain"] = domain
        await r.hset(f"board:{board_id}", mapping=config)
    await r.set(f"delay:{domain}", str(delay_for_domain(domain)))

    return bool(added)


async def remove_monitor(domain: str, board_id: str) -> None:
    """Remove a board monitor task from Redis and delete its config hash.

    Idempotent: clears all four possible monitor queue keys (first-time and
    recurring, simple and browser) plus the ``board:{board_id}`` config.
    Used by ``sync`` after a board is removed from ``boards.csv`` — without
    this, the per-domain queue keeps the stale board_id and workers keep
    probing the dead URL every cycle.

    The ready-queue entry for the domain self-heals on the next ``claim_work``:
    when the per-domain queue is empty, the Lua script removes the domain
    from ``ready:{wtype}:{tier}``.
    """
    r = get_redis()
    pipe = r.pipeline()
    for wtype in ("simple", "browser"):
        pipe.zrem(f"ft_monitors_{wtype}:{domain}", board_id)
        pipe.zrem(f"monitors_{wtype}:{domain}", board_id)
    pipe.delete(f"board:{board_id}")
    await pipe.execute()


async def enqueue_scrape(
    domain: str,
    posting_id: str,
    next_scrape_at: float,
    config: dict,
    *,
    browser: bool = False,
    first_time: bool = False,
) -> bool:
    """Enqueue a scrape task. Returns True if newly added."""
    await _load_scripts()
    r = get_redis()
    wtype = "browser" if browser else "simple"

    added = await r.evalsha(
        _ENQUEUE_SHA,
        0,
        wtype,
        domain,
        posting_id,
        str(next_scrape_at),
        "scrape",
        "1" if first_time else "0",
        str(time.time()),
    )

    if config:
        config["domain"] = domain
        await r.hset(f"scrape:{posting_id}", mapping=config)
    await r.set(f"delay:{domain}", str(delay_for_domain(domain)))

    return bool(added)


# ---------------------------------------------------------------------------
# Claim work
# ---------------------------------------------------------------------------


async def claim_work(*, browser: bool = False) -> WorkItem | None:
    """Claim the next available work item from the tiered ready queues.

    Uses a Lua script for atomic claim + rate limit + reschedule.
    """
    await _load_scripts()
    r = get_redis()
    wtype = "browser" if browser else "simple"

    result = await r.evalsha(
        _CLAIM_SHA,
        0,
        wtype,
        str(time.time()),
        str(settings.throttle_delay_default),
        "10",  # max domains to check per tier
    )

    if not result:
        return None

    task_id, source_type, domain = result[0], result[1], result[2]

    if source_type == "monitor":
        config = await r.hgetall(f"board:{task_id}")
        if not config:
            log.warning("redis_queue.missing_board_config", board_id=task_id)
            return None
        return WorkItem(
            kind="monitor",
            board_work=BoardWork(board_id=task_id, config=config, domain=domain),
        )
    else:
        config = await r.hgetall(f"scrape:{task_id}")
        if not config:
            log.warning("redis_queue.missing_scrape_config", posting_id=task_id)
            return None
        return WorkItem(
            kind="scrape",
            scrape_work=ScrapeWork(
                posting_id=task_id,
                source_url=config.get("source_url", ""),
                board_id=config.get("board_id", ""),
                description_r2_hash=int(config["description_r2_hash"])
                if config.get("description_r2_hash")
                else None,
                scraper_needs_browser=config.get("scraper_needs_browser", "false").lower()
                == "true",
                scrape_interval_hours=int(config.get("scrape_interval_hours", "24")),
                scrape_step=int(config.get("scrape_step", "0")),
                domain=domain,
            ),
        )


# ---------------------------------------------------------------------------
# Reschedule
# ---------------------------------------------------------------------------


async def reschedule_task(
    domain: str,
    task_id: str,
    task_type: str,
    next_due: float,
    *,
    browser: bool = False,
) -> None:
    """Reschedule a task after processing."""
    await _load_scripts()
    r = get_redis()
    wtype = "browser" if browser else "simple"
    await r.evalsha(
        _RESCHEDULE_SHA,
        0,
        wtype,
        domain,
        task_id,
        task_type,
        str(next_due),
    )


# ---------------------------------------------------------------------------
# Metrics / observability
# ---------------------------------------------------------------------------


async def get_queue_depths() -> dict[str, int]:
    """Return domains ready now (score <= now) and total per ready queue."""
    r = get_redis()
    now = str(time.time())
    pipe = r.pipeline()
    keys = []
    for wtype in ("simple", "browser"):
        for tier in range(3):
            key = f"ready:{wtype}:{tier}"
            keys.append(key)
            pipe.zcount(key, "-inf", now)  # ready now
            pipe.zcard(key)  # total (including future)
    results = await pipe.execute()
    depths = {}
    for i, key in enumerate(keys):
        depths[f"{key}:ready"] = results[i * 2]
        depths[f"{key}:total"] = results[i * 2 + 1]
    return depths
