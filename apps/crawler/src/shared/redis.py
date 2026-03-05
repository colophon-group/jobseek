"""Shared async Redis client singleton for the crawler."""

from __future__ import annotations

import os

from upstash_redis.asyncio import Redis

_client: Redis | None = None
_checked = False


def get_redis() -> Redis | None:
    """Return the shared async Redis client, creating it on first call.

    Reads UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN from environment.
    Returns None when the env vars are not configured (e.g. workspace CLI).
    """
    global _client, _checked
    if not _checked:
        _checked = True
        if os.environ.get("UPSTASH_REDIS_REST_URL"):
            _client = Redis.from_env()
    return _client
