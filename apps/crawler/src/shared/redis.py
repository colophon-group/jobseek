"""Shared async Redis client singleton for the crawler."""

from __future__ import annotations

from upstash_redis.asyncio import Redis

_client: Redis | None = None


def get_redis() -> Redis:
    """Return the shared async Redis client, creating it on first call.

    Reads UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN from environment.
    """
    global _client
    if _client is None:
        _client = Redis.from_env()
    return _client
