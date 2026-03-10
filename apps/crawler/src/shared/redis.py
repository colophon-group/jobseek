"""Shared async Redis client singleton for the crawler."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from upstash_redis.asyncio import Redis

_client: Any = None
_checked = False


def get_redis() -> "Redis | None":
    """Return the shared async Redis client, creating it on first call.

    Resolves credentials from environment first, then ``src.config.settings``.
    Returns None when Redis is not configured (e.g. workspace CLI).
    """
    global _client, _checked
    if not _checked:
        _checked = True
        url = os.environ.get("UPSTASH_REDIS_REST_URL") or ""
        token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or ""
        if not url or not token:
            try:
                from src.config import settings

                url = url or settings.upstash_redis_rest_url
                token = token or settings.upstash_redis_rest_token
            except (ImportError, ModuleNotFoundError):
                pass
        if url and token:
            from upstash_redis.asyncio import Redis

            _client = Redis(url=url, token=token)
    return _client
