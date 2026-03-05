"""Per-domain request throttling via Redis."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

import structlog

from src.shared.redis import get_redis

log = structlog.get_logger()

# Minimum seconds between requests to the same domain
_DEFAULT_DELAY = 2.0
_API_DELAY = 0.5  # Known ATS APIs with higher rate limits

_KNOWN_ATS_HOSTS = frozenset(
    {
        "boards-api.greenhouse.io",
        "api.lever.co",
        "api.ashbyhq.com",
        "api.smartrecruiters.com",
        "api.hireology.com",
        "api.rippling.com",
    }
)


def _delay_for_host(hostname: str) -> float:
    """Return the throttle delay for a given hostname."""
    if hostname in _KNOWN_ATS_HOSTS:
        return _API_DELAY
    return _DEFAULT_DELAY


async def throttle_domain(url: str) -> None:
    """Wait if needed to enforce per-domain politeness.

    Uses Redis to track the last request time per domain so throttling
    works across concurrent tasks and multiple crawler instances.
    """
    hostname = urlparse(url).hostname
    if not hostname:
        return

    delay = _delay_for_host(hostname)
    key = f"throttle:{hostname}"
    try:
        redis = get_redis()
    except Exception:
        await asyncio.sleep(delay)
        return

    if redis is None:
        return

    last_raw = await redis.get(key)
    if last_raw is not None:
        elapsed = time.time() - float(last_raw)
        if elapsed < delay:
            wait = delay - elapsed
            log.debug("throttle.wait", hostname=hostname, wait_s=round(wait, 2))
            await asyncio.sleep(wait)

    await redis.set(key, str(time.time()), ex=int(delay * 3))
