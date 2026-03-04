"""Redis-backed scrape URL queue.

Replaces the Postgres job_url_queue for transient work distribution.
Job content is still persisted to Postgres — only the queue state lives in Redis.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

import structlog

from src.shared.redis import get_redis

log = structlog.get_logger()

QUEUE_KEY = "queue:scrape"
RETRY_KEY = "queue:scrape:retry"
DEAD_KEY = "queue:scrape:dead"
ACTIVE_KEY = "queue:scrape:active"

MAX_RETRIES = 3
VISIBILITY_TIMEOUT = 300  # 5 minutes


@dataclass
class QueueItem:
    """A URL queued for scraping."""

    job_posting_id: str
    url: str
    retries: int = 0
    board_id: str = ""
    enqueued_at: float = field(default_factory=time.time)

    def serialize(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def deserialize(cls, data: str) -> QueueItem:
        return cls(**json.loads(data))


async def enqueue(items: list[QueueItem]) -> int:
    """Push items to the scrape queue. Returns count enqueued."""
    if not items:
        return 0
    redis = get_redis()
    pipe = redis.pipeline()
    for item in items:
        pipe.lpush(QUEUE_KEY, item.serialize())
    await pipe.exec()
    log.debug("queue.enqueue", count=len(items))
    return len(items)


async def dequeue(limit: int = 10) -> list[QueueItem]:
    """Pop up to `limit` items from the queue."""
    redis = get_redis()
    items: list[QueueItem] = []
    for _ in range(limit):
        raw = await redis.rpop(QUEUE_KEY)
        if raw is None:
            break
        item = QueueItem.deserialize(raw)
        # Track in-flight for stale recovery
        await redis.hset(ACTIVE_KEY, {item.url: str(time.time())})
        items.append(item)
    return items


async def complete(item: QueueItem) -> None:
    """Mark item as successfully processed."""
    await get_redis().hdel(ACTIVE_KEY, item.url)


async def fail(item: QueueItem, error: str) -> None:
    """Handle failed item — retry or move to dead queue."""
    redis = get_redis()
    await redis.hdel(ACTIVE_KEY, item.url)
    item.retries += 1

    if item.retries >= MAX_RETRIES:
        await redis.lpush(DEAD_KEY, item.serialize())
        log.warning("queue.dead", url=item.url, retries=item.retries, error=error)
    else:
        # Exponential backoff: 30s, 60s, 120s, ...
        retry_at = time.time() + (30 * (2 ** item.retries))
        await redis.zadd(RETRY_KEY, {item.serialize(): retry_at})
        log.debug("queue.retry_scheduled", url=item.url, retry=item.retries)


async def requeue_retries() -> int:
    """Move due retry items back to the main queue. Returns count moved."""
    redis = get_redis()
    now = time.time()
    due = await redis.zrangebyscore(RETRY_KEY, 0, now)
    if not due:
        return 0

    pipe = redis.pipeline()
    for raw in due:
        pipe.lpush(QUEUE_KEY, raw)
        pipe.zrem(RETRY_KEY, raw)
    await pipe.exec()
    log.debug("queue.requeue_retries", count=len(due))
    return len(due)


async def recover_stale(timeout: int = VISIBILITY_TIMEOUT) -> int:
    """Recover items stuck in active state beyond timeout.

    Returns count of items removed from active tracking.
    These items will be re-discovered by the next monitor run.
    """
    redis = get_redis()
    active = await redis.hgetall(ACTIVE_KEY)
    if not active:
        return 0

    now = time.time()
    stale_keys = [
        url for url, claimed_at in active.items()
        if now - float(claimed_at) > timeout
    ]

    if stale_keys:
        await redis.hdel(ACTIVE_KEY, *stale_keys)
        log.warning("queue.recover_stale", count=len(stale_keys))

    return len(stale_keys)


async def queue_length() -> int:
    """Return the current length of the main queue."""
    return await get_redis().llen(QUEUE_KEY)
