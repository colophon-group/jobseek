"""Exact legacy owner binding for the supported all-writer ordinary cutover.

This module cannot stage, activate, adopt the allocator or repair Redis state.
Every installed legacy claim reattests its startup identity under the same DB
lease/epoch barriers used by native claims and ownership transitions.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import asyncpg

from src.config import settings
from src.joint_ownership import attest_joint_ownership, read_b0_audit

LEASE_BARRIER = 7544422533504811010
EPOCH_BARRIER = 7544422533504811009


class OrdinaryOwnershipError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("ordinary ownership rejected")


@dataclass(frozen=True)
class LegacyOwnership:
    plan_sha256: str
    projection_sha1: str
    source_revision: str
    routing_epoch: str
    audit_lua: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"[0-9a-f]{64}", self.plan_sha256) is None
            or re.fullmatch(r"[0-9a-f]{40}", self.projection_sha1) is None
            or re.fullmatch(r"[0-9a-f]{40}", self.source_revision) is None
            or re.fullmatch(r"[1-9][0-9]{0,12}", self.routing_epoch) is None
        ):
            raise OrdinaryOwnershipError()

    def claim_arguments(self) -> tuple[str, ...]:
        return (
            "",  # Tokenless legacy attempt; native claims carry a private token.
            "legacy",
            self.plan_sha256,
            self.projection_sha1,
            self.routing_epoch,
            self.source_revision,
            "",
            "",
        )


def configured_legacy_ownership() -> LegacyOwnership | None:
    values = (
        settings.ordinary_ownership_plan_sha256,
        settings.ordinary_ownership_projection_sha1,
        settings.ordinary_ownership_source_revision,
        settings.ordinary_ownership_routing_epoch,
    )
    if not any(values):
        return None
    return LegacyOwnership(*values, audit_lua=read_b0_audit(settings.ordinary_go_b0_audit_lua_file))


async def _attest(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy, expected: LegacyOwnership | None
) -> None:
    row = await conn.fetchrow(
        "SELECT plan_sha256,routing_epoch,source_revision,payload "
        "FROM public.ordinary_worker_ownership_plan WHERE state='active'"
    )
    if expected is None:
        if row is not None:
            raise OrdinaryOwnershipError()
        await attest_joint_ownership(conn, None, "", "", "", "")
        return
    if row is None:
        raise OrdinaryOwnershipError()
    allocator = await conn.fetchrow(
        "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq"
    )
    payload = row["payload"]
    if (
        row["plan_sha256"] != expected.plan_sha256
        or str(row["routing_epoch"]) != expected.routing_epoch
        or row["source_revision"] != expected.source_revision
        or allocator is None
        or allocator["is_called"] is not True
        or str(allocator["last_value"]) != expected.routing_epoch
        or not isinstance(payload, str)
        or not 1 <= len(payload.encode("utf-8")) <= 16777216
        or hashlib.sha256(payload.encode("utf-8")).hexdigest() != expected.plan_sha256
        or hashlib.sha1(payload.encode("utf-8")).hexdigest() != expected.projection_sha1
    ):
        raise OrdinaryOwnershipError()
    await attest_joint_ownership(
        conn,
        payload,
        expected.plan_sha256,
        expected.source_revision,
        expected.routing_epoch,
        expected.audit_lua,
    )


@asynccontextmanager
async def legacy_ownership_barrier(
    pool: asyncpg.Pool, expected: LegacyOwnership | None
) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(15), pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock_shared($1)", LEASE_BARRIER)
            await conn.execute("SELECT pg_advisory_xact_lock_shared($1)", EPOCH_BARRIER)
            await _attest(conn, expected)
            yield
    except asyncio.CancelledError:
        raise
    except Exception:
        # Neither DB errors nor installed config values are diagnostic authority.
        raise OrdinaryOwnershipError() from None


async def prepare_legacy_ownership(pool: asyncpg.Pool) -> LegacyOwnership | None:
    try:
        expected = configured_legacy_ownership()
    except Exception:
        raise OrdinaryOwnershipError() from None
    async with legacy_ownership_barrier(pool, expected):
        pass
    return expected
