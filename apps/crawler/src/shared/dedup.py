"""URL deduplication via Redis set."""

from __future__ import annotations

import structlog

from src.shared.redis import get_redis

log = structlog.get_logger()

SEEN_KEY = "dedup:urls"
SEEN_TTL = 86400 * 7  # 7 days


async def filter_unseen(urls: list[str]) -> list[str]:
    """Return only URLs not already in the dedup set."""
    if not urls:
        return []
    redis = get_redis()
    unseen = [url for url in urls if not await redis.sismember(SEEN_KEY, url)]
    return unseen


async def mark_seen(urls: list[str]) -> None:
    """Mark URLs as seen in the dedup set."""
    if not urls:
        return
    redis = get_redis()
    pipe = redis.pipeline()
    for url in urls:
        pipe.sadd(SEEN_KEY, url)
    pipe.expire(SEEN_KEY, SEEN_TTL)
    await pipe.exec()
