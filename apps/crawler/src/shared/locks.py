"""Distributed board locks via Redis."""

from __future__ import annotations

import structlog

from src.shared.redis import get_redis

log = structlog.get_logger()


def acquire_board_lock(board_id: str, ttl: int = 300) -> bool:
    """Try to acquire a lock for monitoring a board.

    Returns True if the lock was acquired, False if another instance holds it.
    The lock auto-expires after `ttl` seconds.
    """
    key = f"lock:board:{board_id}"
    result = get_redis().set(key, "1", nx=True, ex=ttl)
    if not result:
        log.debug("lock.skipped", board_id=board_id)
    return result is not None


def release_board_lock(board_id: str) -> None:
    """Release a board monitoring lock."""
    get_redis().delete(f"lock:board:{board_id}")
