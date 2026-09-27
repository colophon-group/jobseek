"""Recheck retained corpus rows against current crawler mining restrictions."""

from __future__ import annotations

import asyncio
from uuid import UUID


class MiningGuardError(RuntimeError):
    pass


async def _assert_current(posting_ids: list[str]) -> None:
    from src.db import close_all_pools, create_local_pool

    try:
        ids = list({UUID(value) for value in posting_ids})
        pool = await create_local_pool()
        for offset in range(0, len(ids), 500):
            batch = ids[offset : offset + 500]
            count = await pool.fetchval(
                "SELECT count(*) FROM job_posting WHERE id = ANY($1::uuid[]) AND NOT tdm_reserved",
                batch,
            )
            if count != len(batch):
                raise MiningGuardError("Reserved or missing source posting; mining/export refused")
    except MiningGuardError:
        raise
    except Exception as exc:
        raise MiningGuardError(
            "Current mining eligibility unavailable; mining/export refused"
        ) from exc
    finally:
        await close_all_pools()


def assert_mining_allowed(posting_ids: list[str]) -> None:
    if posting_ids:
        asyncio.run(_assert_current(posting_ids))
