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

Inflight (lease) tracking — see issues #3159 / #3173:
    inflight:{wtype}              — ZSET keyed by "task_type|domain|task_id",
                                    score = leased_until (unix ts). Atomically
                                    written by ``claim_work.lua``, cleared by
                                    ``reschedule_task.lua``/``complete_task.lua``,
                                    extended by ``heartbeat_task.lua``, and
                                    swept by ``reap_expired.lua`` once score
                                    drops below ``now``.
    inflight_strikes:{wtype}      — HASH of member -> int retry count. A
                                    task that exceeds ``reaper_max_strikes``
                                    is moved to ``deadletter:{wtype}`` for
                                    operator review instead of being
                                    re-enqueued.
    deadletter:{wtype}            — ZSET of poison-pill task descriptors
                                    (score = unix ts of last reap). Inspect
                                    with ``ZRANGE deadletter:simple 0 -1
                                    WITHSCORES`` from ``redis-cli``.

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
    """A claimed work item — either a board monitor or a scrape.

    ``domain`` and ``task_id`` mirror the inner work objects but are
    surfaced here so the worker doesn't have to dispatch on ``kind`` to
    extend or release a lease (see issues #3159 / #3173).
    """

    kind: str  # "monitor" or "scrape"
    board_work: BoardWork | None = None
    scrape_work: ScrapeWork | None = None

    @property
    def domain(self) -> str:
        if self.board_work is not None:
            return self.board_work.domain
        if self.scrape_work is not None:
            return self.scrape_work.domain
        return ""

    @property
    def task_id(self) -> str:
        if self.board_work is not None:
            return self.board_work.board_id
        if self.scrape_work is not None:
            return self.scrape_work.posting_id
        return ""


# ---------------------------------------------------------------------------
# Lua script loading
# ---------------------------------------------------------------------------

_LUA_DIR = Path(__file__).parent / "lua"
_CLAIM_SHA: str | None = None
_ENQUEUE_SHA: str | None = None
_RESCHEDULE_SHA: str | None = None
_COMPLETE_SHA: str | None = None
_HEARTBEAT_SHA: str | None = None
_REAP_SHA: str | None = None


async def _load_scripts() -> None:
    """Load Lua scripts into Redis and cache their SHAs."""
    global _CLAIM_SHA, _ENQUEUE_SHA, _RESCHEDULE_SHA
    global _COMPLETE_SHA, _HEARTBEAT_SHA, _REAP_SHA
    if _CLAIM_SHA is not None:
        return
    r = get_redis()
    _CLAIM_SHA = await r.script_load((_LUA_DIR / "claim_work.lua").read_text())
    _ENQUEUE_SHA = await r.script_load((_LUA_DIR / "enqueue_task.lua").read_text())
    _RESCHEDULE_SHA = await r.script_load((_LUA_DIR / "reschedule_task.lua").read_text())
    _COMPLETE_SHA = await r.script_load((_LUA_DIR / "complete_task.lua").read_text())
    _HEARTBEAT_SHA = await r.script_load((_LUA_DIR / "heartbeat_task.lua").read_text())
    _REAP_SHA = await r.script_load((_LUA_DIR / "reap_expired.lua").read_text())


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

    Uses a Lua script for atomic claim + rate limit + reschedule +
    inflight lease entry (see issues #3159 / #3173). The lease entry
    is the worker's IOU back to the queue — if the worker dies before
    calling ``reschedule_task`` or ``complete_task``, the reaper sweeps
    the expired lease back onto the per-domain queue.
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
        str(settings.inflight_lease_ttl_seconds),
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
    """Reschedule a task after processing.

    Also clears the inflight lease entry — see ``reschedule_task.lua``.
    """
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
# Lease lifecycle: complete / heartbeat / reap (#3159 / #3173)
# ---------------------------------------------------------------------------


async def complete_task(
    domain: str,
    task_id: str,
    task_type: str,
    *,
    browser: bool = False,
) -> int:
    """Mark a task complete without rescheduling — drop the lease entry.

    Used by self-heal / drop-rich paths that already removed the task
    from the per-domain ZSET (so reschedule semantics don't apply) but
    must still close the inflight lease so the reaper doesn't
    re-enqueue. Idempotent: returns 1 if the lease was present, 0 if
    it had already been swept.
    """
    await _load_scripts()
    r = get_redis()
    wtype = "browser" if browser else "simple"
    result = await r.evalsha(
        _COMPLETE_SHA,
        0,
        wtype,
        task_type,
        domain,
        task_id,
    )
    return int(result or 0)


async def heartbeat_task(
    domain: str,
    task_id: str,
    task_type: str,
    *,
    browser: bool = False,
    extension_seconds: float | None = None,
) -> int:
    """Extend the lease on an in-flight task.

    Workers call this while processing long-running work to push out
    ``leased_until`` so the reaper doesn't reclaim a task that's still
    progressing. Returns 1 if extended, 0 if the inflight entry was
    already gone (reaper raced ahead — caller should stop processing
    to avoid double-execution).
    """
    await _load_scripts()
    r = get_redis()
    wtype = "browser" if browser else "simple"
    ttl = (
        extension_seconds
        if extension_seconds is not None
        else float(settings.inflight_lease_ttl_seconds)
    )
    new_until = time.time() + ttl
    result = await r.evalsha(
        _HEARTBEAT_SHA,
        0,
        wtype,
        task_type,
        domain,
        task_id,
        str(new_until),
    )
    return int(result or 0)


async def reap_expired(*, browser: bool = False) -> dict[str, int]:
    """Sweep expired inflight leases and re-enqueue or dead-letter.

    Returns ``{"reenqueued": N, "dead_lettered": M, "missing_config": K}``
    for the single batch swept. The reaper coroutine calls this on a
    loop; ``max_entries`` caps work per call to bound Lua runtime.
    """
    await _load_scripts()
    r = get_redis()
    wtype = "browser" if browser else "simple"
    now = time.time()
    result = await r.evalsha(
        _REAP_SHA,
        0,
        wtype,
        str(now),
        str(settings.reaper_batch_size),
        str(settings.reaper_max_strikes),
        str(now),  # retry_score = now → "retry ASAP"
    )
    return {
        "reenqueued": int(result[0]),
        "dead_lettered": int(result[1]),
        "missing_config": int(result[2]),
    }


async def get_inflight_depth(*, browser: bool = False) -> int:
    """Return number of currently in-flight (leased) tasks."""
    r = get_redis()
    wtype = "browser" if browser else "simple"
    return int(await r.zcard(f"inflight:{wtype}"))


async def get_deadletter_depth(*, browser: bool = False) -> int:
    """Return number of tasks parked in the dead-letter queue."""
    r = get_redis()
    wtype = "browser" if browser else "simple"
    return int(await r.zcard(f"deadletter:{wtype}"))


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


async def prune_stale_scrape_queues(
    *,
    older_than_days: float = 7.0,
    dry_run: bool = False,
) -> dict[str, int]:
    """Drop entries from ``scrapes_<wtype>:<domain>`` zsets whose score
    (``next_scrape_at``) is older than ``older_than_days`` plus the
    ``scrape:<task_id>`` hashes they reference.

    Score semantics — a scheduled rescrape sets the score to the future
    time at which it's due, so any entry with ``score < now - cutoff``
    is a task that was enqueued that long ago and never claimed (most
    commonly because the domain's shared rate limit can't drain faster
    than the monitor re-enqueues — the head-of-line block behind the
    Pictet / SuccessFactors cardinality bug; see board.py canonicalize).

    Returns ``{"zset_entries": N, "hashes": M, "keys_scanned": K}``.
    """
    r = get_redis()
    cutoff = time.time() - (older_than_days * 86400)

    zset_removed = 0
    hashes_removed = 0
    keys_scanned = 0

    # Both ordinary and first-time scrape queues — both use the same
    # ZSET layout (score = next_scrape_at).
    for pattern in (
        "scrapes_simple:*",
        "scrapes_browser:*",
        "ft_scrapes_simple:*",
        "ft_scrapes_browser:*",
    ):
        async for key in r.scan_iter(match=pattern, count=500):
            keys_scanned += 1
            stale_ids = await r.zrangebyscore(key, "-inf", cutoff)
            if not stale_ids:
                continue
            if dry_run:
                zset_removed += len(stale_ids)
                # Count how many scrape:<id> hashes exist (not all stale
                # ids necessarily have a hash — a race between ZREM and
                # DEL elsewhere leaves a zset-only ghost).
                exists_pipe = r.pipeline()
                for task_id in stale_ids:
                    exists_pipe.exists(f"scrape:{task_id}")
                exists_results = await exists_pipe.execute()
                hashes_removed += sum(1 for x in exists_results if x)
                continue
            # Atomic ZREM + DEL batch per key. Each key's stale set can
            # be thousands of ids (pictet had 27k), so flush by chunks
            # to avoid huge multi-arg commands.
            _CHUNK = 500
            for start in range(0, len(stale_ids), _CHUNK):
                chunk = stale_ids[start : start + _CHUNK]
                pipe = r.pipeline()
                pipe.zrem(key, *chunk)
                for task_id in chunk:
                    pipe.delete(f"scrape:{task_id}")
                results = await pipe.execute()
                zset_removed += int(results[0] or 0)
                hashes_removed += sum(1 for x in results[1:] if x)

    return {
        "zset_entries": zset_removed,
        "hashes": hashes_removed,
        "keys_scanned": keys_scanned,
    }
