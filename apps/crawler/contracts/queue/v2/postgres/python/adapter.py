"""Caller-owned asyncpg adapter for the inactive queue-v2 write fence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

MAX_INTEGER = 9_999_999_999_999
_TOKEN_RE = re.compile(r"([1-9][0-9]{0,12}):([1-9][0-9]{0,12})\Z")
_REJECTION_MESSAGE = "queue_v2_write_fence_rejected"
_SCHEMA_SQL = Path(__file__).parents[1] / "write_fence.sql"


class WriteFenceTransactionRequired(RuntimeError):
    """Raised before SQL when the caller has not opened a transaction."""


class WriteFenceRejected(RuntimeError):
    """Stable, sanitized expected-rejection surface from the SQL contract."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"queue-v2 write fence rejected: {reason}")


@dataclass(frozen=True, slots=True)
class WriteFence:
    """Complete PostgreSQL identity for one queue-v2 claim."""

    task_kind: str
    task_id: str
    shard_id: str
    routing_epoch: int
    engine_owner: str
    config_revision: int
    claim_token: str
    claim_sequence: int = field(init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_kind, str)
            or not self.task_kind
            or not isinstance(self.task_id, str)
            or not self.task_id
            or not isinstance(self.shard_id, str)
            or not self.shard_id
            or not isinstance(self.engine_owner, str)
            or self.engine_owner not in {"python", "go"}
            or not isinstance(self.claim_token, str)
            or type(self.routing_epoch) is not int
            or type(self.config_revision) is not int
            or not 1 <= self.routing_epoch <= MAX_INTEGER
            or not 1 <= self.config_revision <= MAX_INTEGER
        ):
            raise ValueError("invalid queue-v2 write fence")
        match = _TOKEN_RE.fullmatch(self.claim_token)
        if match is None:
            raise ValueError("invalid queue-v2 write fence")
        token_epoch = int(match.group(1))
        sequence = int(match.group(2))
        if token_epoch != self.routing_epoch or sequence > MAX_INTEGER:
            raise ValueError("invalid queue-v2 write fence")
        object.__setattr__(self, "claim_sequence", sequence)

    def sql_args(self) -> tuple[str, str, str, int, str, int, str]:
        return (
            self.task_kind,
            self.task_id,
            self.shard_id,
            self.routing_epoch,
            self.engine_owner,
            self.config_revision,
            self.claim_token,
        )


async def install_contract(connection: asyncpg.Connection) -> None:
    """Install the exact inactive SQL source into a dedicated empty database."""

    await connection.execute(_SCHEMA_SQL.read_text(encoding="utf-8"))


def _require_transaction(connection: asyncpg.Connection) -> None:
    if not connection.is_in_transaction():
        raise WriteFenceTransactionRequired(
            "queue-v2 write-fence operations require a caller-owned transaction"
        )


async def _call(
    connection: asyncpg.Connection,
    query: str,
    *arguments: object,
) -> None:
    _require_transaction(connection)
    try:
        await connection.execute(query, *arguments)
    except asyncpg.RaiseError as exc:
        if exc.sqlstate == "P0001" and exc.message == _REJECTION_MESSAGE:
            raise WriteFenceRejected(exc.detail or "rejected") from None
        raise


async def activate_write_fence(
    connection: asyncpg.Connection,
    fence: WriteFence,
) -> None:
    await _call(
        connection,
        "SELECT queue_v2_contract.jobseek_queue_v2_activate_write_fence("
        "$1, $2, $3, $4, $5, $6, $7)",
        *fence.sql_args(),
    )


async def require_write_fence(
    connection: asyncpg.Connection,
    fence: WriteFence,
) -> None:
    await _call(
        connection,
        "SELECT queue_v2_contract.jobseek_queue_v2_require_write_fence($1, $2, $3, $4, $5, $6, $7)",
        *fence.sql_args(),
    )


async def rotate_write_fence(
    connection: asyncpg.Connection,
    current: WriteFence,
    replacement: WriteFence,
) -> None:
    if (current.task_kind, current.task_id) != (
        replacement.task_kind,
        replacement.task_id,
    ):
        raise ValueError("queue-v2 rotation cannot change task identity")
    await _call(
        connection,
        "SELECT queue_v2_contract.jobseek_queue_v2_rotate_write_fence("
        "$1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
        *current.sql_args(),
        replacement.shard_id,
        replacement.routing_epoch,
        replacement.engine_owner,
        replacement.config_revision,
        replacement.claim_token,
    )


async def revoke_write_fence(
    connection: asyncpg.Connection,
    fence: WriteFence,
) -> None:
    await _call(
        connection,
        "SELECT queue_v2_contract.jobseek_queue_v2_revoke_write_fence($1, $2, $3, $4, $5, $6, $7)",
        *fence.sql_args(),
    )
