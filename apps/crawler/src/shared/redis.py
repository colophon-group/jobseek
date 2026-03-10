"""Shared async Redis client singleton for the crawler."""

from __future__ import annotations

import os

from upstash_redis.asyncio import Redis

from src.config import settings

_client: Redis | None = None
_checked = False


def get_redis() -> Redis | None:
    """Return the shared async Redis client, creating it on first call.

    Resolves credentials from environment first, then ``src.config.settings``.
    Returns None when Redis is not configured (e.g. workspace CLI).
    """
    global _client, _checked
    if not _checked:
        _checked = True
        url = os.environ.get("UPSTASH_REDIS_REST_URL") or settings.upstash_redis_rest_url
        token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or settings.upstash_redis_rest_token
        if url and token:
            _client = Redis(url=url, token=token)
    return _client
