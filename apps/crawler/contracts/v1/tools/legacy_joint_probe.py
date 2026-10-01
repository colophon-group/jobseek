"""Owned-fixture bridge from the actual native executable to legacy admission.

The Go executable test owns PostgreSQL, Redis and all fault injection. This
bounded probe runs the actual legacy startup and claim barrier without repairs.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from urllib.parse import urlsplit

import asyncpg

from src.config import settings
from src.ordinary_ownership import (
    OrdinaryOwnershipError,
    configured_legacy_ownership,
    legacy_ownership_barrier,
    prepare_legacy_ownership,
)
from src.redis_queue import claim_work, get_redis


async def probe() -> None:
    expected_result = sys.argv[1]
    dsn = settings.local_database_url
    address = urlsplit(dsn)
    redis_address = urlsplit(settings.redis_url)
    if (
        os.environ.get("JOBSEEK_ORDINARY_LEGACY_JOINT_PROBE") != "1"
        or expected_result not in {"accepted", "rejected"}
        or address.hostname not in {"127.0.0.1", "localhost"}
        or not address.path.endswith("_ordinary_worker_test")
        or redis_address.scheme != "unix"
        or redis_address.netloc
        or not stat.S_ISSOCK(os.stat(redis_address.path).st_mode)
    ):
        raise ValueError("owned fixture required")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, command_timeout=10)
    try:
        async with asyncio.timeout(20):
            for startup in (True, False):
                rejected = False
                try:
                    if startup:
                        await prepare_legacy_ownership(pool)
                    else:
                        expected = configured_legacy_ownership()
                        async with legacy_ownership_barrier(pool, expected):
                            # The executable fixture schedules ordinary work in
                            # the future. An accepted read may not pop any task.
                            work = await claim_work(ownership=expected)
                            if work is not None:
                                raise ValueError("fixture unexpectedly claimed work")
                except OrdinaryOwnershipError:
                    rejected = True
                if rejected != (expected_result == "rejected"):
                    raise ValueError("legacy admission diverged")
    finally:
        await pool.close()
        await get_redis().aclose()


if __name__ == "__main__":
    try:
        asyncio.run(probe())
    except Exception:
        raise SystemExit("legacy joint probe failed") from None
    print("legacy joint probe verified")
