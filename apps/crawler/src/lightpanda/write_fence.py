"""PostgreSQL write fence for the Python-owned Lightpanda B0 lane."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import asyncpg
from asyncpg.pool import PoolConnectionProxy

if TYPE_CHECKING:
    from src.lightpanda_queue import Lease

_MAX_INTEGER = 9_999_999_999_999
_TOKEN_RE = re.compile(r"^([1-9][0-9]{0,12}):([1-9][0-9]{0,12})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REJECTION_MESSAGE = "lightpanda_b0_write_fence_rejected"
_REJECTION_REASONS = frozenset(
    {
        "config_revision_mismatch",
        "config_revision_not_monotonic",
        "engine_owner_mismatch",
        "generation_in_use",
        "generation_not_advanced",
        "invalid_claim_token",
        "invalid_identity",
        "job_posting_id_mismatch",
        "missing",
        "payload_change_requires_config_revision",
        "payload_digest_mismatch",
        "revoked",
        "revoked_replay",
        "route_change_requires_epoch",
        "routing_epoch_mismatch",
        "shard_id_mismatch",
        "claim_token_mismatch",
    }
)

_ACTIVATE = "SELECT public.jobseek_lightpanda_b0_activate_write_fence($1, $2, $3, $4, $5, $6, $7)"
_REQUIRE = "SELECT public.jobseek_lightpanda_b0_require_write_fence($1, $2, $3, $4, $5, $6, $7)"
_REVOKE = "SELECT public.jobseek_lightpanda_b0_revoke_write_fence($1, $2, $3, $4, $5, $6, $7)"


class LightpandaWriteFenceRejected(asyncio.CancelledError):
    """A stale claim cancellation, deliberately outside ``Exception``."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Lightpanda B0 write fence rejected: {reason}")


@dataclass(frozen=True, slots=True)
class LightpandaWriteFence:
    """Exact PostgreSQL identity derived from one validated Redis lease."""

    job_posting_id: uuid.UUID
    shard_id: str
    routing_epoch: int
    engine_owner: Literal["python"]
    config_revision: int
    payload_sha256: str
    claim_token: str
    claim_sequence: int = field(init=False)

    def __post_init__(self) -> None:
        match = _TOKEN_RE.fullmatch(self.claim_token) if isinstance(self.claim_token, str) else None
        if (
            not isinstance(self.job_posting_id, uuid.UUID)
            or not isinstance(self.shard_id, str)
            or _SAFE_ID_RE.fullmatch(self.shard_id) is None
            or type(self.routing_epoch) is not int
            or not 1 <= self.routing_epoch <= _MAX_INTEGER
            or self.engine_owner != "python"
            or type(self.config_revision) is not int
            or not 1 <= self.config_revision <= _MAX_INTEGER
            or not isinstance(self.payload_sha256, str)
            or _SHA256_RE.fullmatch(self.payload_sha256) is None
            or match is None
            or int(match.group(1)) != self.routing_epoch
        ):
            raise ValueError("invalid Lightpanda B0 write fence")
        object.__setattr__(self, "claim_sequence", int(match.group(2)))

    @classmethod
    def from_lease(cls, lease: Lease) -> LightpandaWriteFence:
        """Derive the only accepted database identity from a queue lease."""

        from src.lightpanda_queue import Lease

        if not isinstance(lease, Lease):
            raise TypeError("lease must be a validated Lightpanda B0 Lease")
        try:
            posting_id = uuid.UUID(lease.task.task_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Lightpanda B0 task_id must be a job_posting UUID") from exc
        if str(posting_id) != lease.task.task_id:
            raise ValueError("Lightpanda B0 task_id must be a canonical job_posting UUID")
        if lease.task.route.engine_owner != "python":
            raise ValueError("Lightpanda B0 lease must be Python-owned")
        return cls(
            job_posting_id=posting_id,
            shard_id=lease.task.route.shard_id,
            routing_epoch=lease.task.route.routing_epoch,
            engine_owner="python",
            config_revision=lease.task.config_revision,
            payload_sha256=lease.task.payload_sha256,
            claim_token=lease.claim_token,
        )

    def sql_args(self) -> tuple[uuid.UUID, str, int, str, int, str, str]:
        return (
            self.job_posting_id,
            self.shard_id,
            self.routing_epoch,
            self.engine_owner,
            self.config_revision,
            self.payload_sha256,
            self.claim_token,
        )


async def _execute(
    connection: asyncpg.Connection | PoolConnectionProxy,
    query: str,
    fence: LightpandaWriteFence,
) -> None:
    try:
        await connection.execute(query, *fence.sql_args())
    except asyncpg.RaiseError as exc:
        if exc.sqlstate == "P0001" and getattr(exc, "message", None) == _REJECTION_MESSAGE:
            detail = getattr(exc, "detail", None)
            reason = detail if detail in _REJECTION_REASONS else "rejected"
            raise LightpandaWriteFenceRejected(reason) from None
        raise


def validate_write_fence_target(
    fence: LightpandaWriteFence | None,
    job_posting_id: str,
) -> None:
    """Cancel before I/O when an item does not match its claimed posting."""

    if fence is None:
        return
    try:
        parsed = uuid.UUID(job_posting_id)
    except (AttributeError, TypeError, ValueError):
        raise LightpandaWriteFenceRejected("job_posting_id_mismatch") from None
    if str(parsed) != job_posting_id or parsed != fence.job_posting_id:
        raise LightpandaWriteFenceRejected("job_posting_id_mismatch")


async def activate_write_fence(pool: asyncpg.Pool, fence: LightpandaWriteFence) -> None:
    """Activate or supersede a generation in its own short transaction."""

    async with pool.acquire() as connection, connection.transaction():
        await _execute(connection, _ACTIVATE, fence)


@asynccontextmanager
async def authoritative_write(
    pool: asyncpg.Pool,
    fence: LightpandaWriteFence | None,
    *,
    job_posting_id: str,
) -> AsyncIterator[asyncpg.Connection | PoolConnectionProxy]:
    """Yield the authoritative writer and atomically revoke fenced claims."""

    validate_write_fence_target(fence, job_posting_id)
    async with pool.acquire() as connection:
        if fence is None:
            yield connection
            return
        async with connection.transaction():
            await _execute(connection, _REQUIRE, fence)
            yield connection
            await _execute(connection, _REVOKE, fence)
