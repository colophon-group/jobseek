"""Postgres-backed :class:`ClaimKV` for the Murmur HTTP shim.

Mirrors the TS `apps/murmur-shim/src/lib/murmur/claim-kv.ts` module against the
same `murmur_claim_kv` table. Used only by the HTTP shim entry point
(`cli_shim`); the regular CLI keeps using the in-memory implementation
or the YAML-backed adapter.

Schema (ddl owned by `apps/web/src/db/schema.ts`, re-exported by
`apps/murmur-shim/src/db/schema.ts`)::

    CREATE TABLE murmur_claim_kv (
        claim_token  text          NOT NULL,
        name         text          NOT NULL,
        value        jsonb         NOT NULL,
        created_at   timestamptz   NOT NULL DEFAULT now(),
        updated_at   timestamptz   NOT NULL DEFAULT now(),
        PRIMARY KEY (claim_token, name)
    );

The class binds a single ``claim_token`` at construction time so callers
do not have to thread it through every call. ``ACTIVE_KEY`` is treated
as a regular slot for read/write purposes; ``list_all`` filters it out.

@see colophon-group/jobseek#2759
@see colophon-group/jobseek#2757 (TS counterpart)
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from src.workspace.lib.claim_kv import ACTIVE_KEY


class PostgresClaimKV:
    """:class:`~src.workspace.lib.claim_kv.ClaimKV` backed by Postgres.

    Each call opens a short-lived connection. The HTTP shim is invoked
    once per agent request, so connection pooling at the asyncpg level
    is not needed for the demo; a single ``connect / fetch / close``
    per call is fine.
    """

    def __init__(self, claim_token: str, dsn: str) -> None:
        if not claim_token:
            raise ValueError("PostgresClaimKV: claim_token must be non-empty")
        if not dsn:
            raise ValueError("PostgresClaimKV: dsn must be non-empty")
        self._claim_token = claim_token
        self._dsn = dsn

    async def _connect(self) -> asyncpg.Connection[Any]:
        return await asyncpg.connect(self._dsn)

    async def get(self, name: str) -> Any | None:  # noqa: ANN401
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                "SELECT value FROM murmur_claim_kv WHERE claim_token = $1 AND name = $2",
                self._claim_token,
                name,
            )
        finally:
            await conn.close()
        if row is None:
            return None
        raw = row["value"]
        # asyncpg returns jsonb as the raw text by default; if the user
        # has installed a json codec it may already be parsed. Handle
        # both cases idempotently.
        if isinstance(raw, str):
            return json.loads(raw)
        return raw

    async def set(self, name: str, value: Any) -> None:  # noqa: ANN401
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO murmur_claim_kv (claim_token, name, value) "
                "VALUES ($1, $2, $3::jsonb) "
                "ON CONFLICT (claim_token, name) "
                "DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                self._claim_token,
                name,
                json.dumps(value),
            )
        finally:
            await conn.close()

    async def list_all(self) -> dict[str, Any]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                "SELECT name, value FROM murmur_claim_kv WHERE claim_token = $1",
                self._claim_token,
            )
        finally:
            await conn.close()
        out: dict[str, Any] = {}
        for r in rows:
            n = r["name"]
            if n == ACTIVE_KEY:
                continue
            raw = r["value"]
            out[n] = json.loads(raw) if isinstance(raw, str) else raw
        return out

    async def clear(self) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                "DELETE FROM murmur_claim_kv WHERE claim_token = $1",
                self._claim_token,
            )
        finally:
            await conn.close()

    async def get_active(self) -> str | None:
        v = await self.get(ACTIVE_KEY)
        return v if isinstance(v, str) else None

    async def set_active(self, name: str) -> None:
        await self.set(ACTIVE_KEY, name)


__all__ = ["PostgresClaimKV"]
