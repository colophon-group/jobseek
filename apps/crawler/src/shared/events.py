"""Job event publishing to Redis for real-time consumers."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

import structlog

from src.shared.redis import get_redis

log = structlog.get_logger()

EVENTS_KEY = "events:jobs"
EVENTS_TTL = 3600  # Keep events for 1 hour
MAX_EVENTS = 1000


@dataclass
class JobEvent:
    """A job lifecycle event (new, relisted, or delisted)."""

    type: str  # "new" | "relisted" | "delisted"
    company_slug: str
    board_slug: str
    job_count: int
    sample_titles: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


def publish_job_events(events: list[JobEvent]) -> None:
    """Publish job events to Redis for consumers (web app polling endpoint)."""
    if not events:
        return

    redis = get_redis()
    pipe = redis.pipeline()
    for event in events:
        pipe.lpush(EVENTS_KEY, json.dumps(asdict(event)))
    # Trim to last MAX_EVENTS entries
    pipe.ltrim(EVENTS_KEY, 0, MAX_EVENTS - 1)
    pipe.expire(EVENTS_KEY, EVENTS_TTL)
    pipe.execute()

    log.info(
        "events.published",
        count=len(events),
        types=[e.type for e in events],
    )
