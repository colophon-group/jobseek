"""Deploy-safe PostgreSQL/Redis completion for Umantis stable identities.

Revision 0022 changes the durable PostgreSQL identity and installs a trigger
that canonicalizes writes from a rolled-back runtime. Workers are quiesced
while this command atomically validates and updates every pre-existing Redis
``scrape:<posting_id>`` hash in bounded batches. During rollback it also parks
the incompatible old monitor schedules without deleting board hashes needed
by canonical scrape work. A failure leaves workers stopped; rerunning is
idempotent and completes any earlier batches.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from typing import Protocol, cast

import asyncpg
import redis.asyncio as aioredis

_BATCH_SIZE = 500


class _CutoverRow(Protocol):
    def __getitem__(self, field: str, /) -> object: ...


_REPAIR_SCRAPE_HASHES_LUA = """
for index, key in ipairs(KEYS) do
    if redis.call("EXISTS", key) == 1 then
        local offset = (index - 1) * 2
        local expected_board = ARGV[offset + 1]
        local canonical_url = ARGV[offset + 2]
        local actual_board = redis.call("HGET", key, "board_id")
        local actual_url = redis.call("HGET", key, "source_url")
        local prefix = canonical_url .. "/"
        local locale_suffix = actual_url and string.sub(actual_url, #prefix + 1) or ""
        local is_locale_alias = actual_url
            and string.sub(actual_url, 1, #prefix) == prefix
            and string.sub(locale_suffix, 1, 1) ~= "0"
            and string.match(locale_suffix, "^[0-9]+$") ~= nil

        if actual_board ~= expected_board then
            return redis.error_reply("Umantis scrape hash board mismatch at batch index " .. index)
        end
        if actual_url ~= canonical_url and not is_locale_alias then
            return redis.error_reply("Umantis scrape hash URL mismatch at batch index " .. index)
        end
    end
end

local changed = 0
for index, key in ipairs(KEYS) do
    if redis.call("EXISTS", key) == 1 then
        local canonical_url = ARGV[(index - 1) * 2 + 2]
        if redis.call("HGET", key, "source_url") ~= canonical_url then
            redis.call("HSET", key, "source_url", canonical_url)
            changed = changed + 1
        end
    end
end
return changed
"""

_PARK_MONITORS_LUA = """
local function refresh_ready(wtype, domain)
    for tier = 0, 2 do
        redis.call("ZREM", "ready:" .. wtype .. ":" .. tier, domain)
    end

    local rate_limit = tonumber(redis.call("GET", "ratelimit:" .. domain) or "0")
    local first_time_score = nil
    for _, prefix in ipairs({"ft_monitors_", "ft_scrapes_"}) do
        local head = redis.call("ZRANGE", prefix .. wtype .. ":" .. domain, 0, 0, "WITHSCORES")
        if #head >= 2 then
            local score = tonumber(head[2])
            if first_time_score == nil or score < first_time_score then
                first_time_score = score
            end
        end
    end
    if first_time_score ~= nil then
        redis.call(
            "ZADD", "ready:" .. wtype .. ":0",
            math.max(rate_limit, first_time_score), domain
        )
        return
    end

    for _, queue in ipairs({{"monitors_", 1}, {"scrapes_", 2}}) do
        local head = redis.call(
            "ZRANGE", queue[1] .. wtype .. ":" .. domain, 0, 0, "WITHSCORES"
        )
        if #head >= 2 then
            redis.call(
                "ZADD", "ready:" .. wtype .. ":" .. queue[2],
                math.max(rate_limit, tonumber(head[2])), domain
            )
        end
    end
end

local parked = 0
for index = 1, #ARGV, 2 do
    local board_id = ARGV[index]
    local domain = ARGV[index + 1]
    for _, wtype in ipairs({"simple", "browser"}) do
        parked = parked + redis.call("ZREM", "ft_monitors_" .. wtype .. ":" .. domain, board_id)
        parked = parked + redis.call("ZREM", "monitors_" .. wtype .. ":" .. domain, board_id)
        local member = "monitor|" .. domain .. "|" .. board_id
        parked = parked + redis.call("ZREM", "inflight:" .. wtype, member)
        redis.call("HDEL", "inflight_strikes:" .. wtype, member)
        redis.call("ZREM", "deadletter:" .. wtype, member)
        refresh_ready(wtype, domain)
    end
end
return parked
"""


def _migration_module():
    return importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )


def _migration_sql() -> str:
    return cast(str, _migration_module()._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)


def _posting_query() -> str:
    migration = _migration_module()
    board_values = ",\n".join(
        f"({_literal(board_slug)})"
        for board_slug, _company_slug, _board_url, _source_base in (
            migration._UMANTIS_BOARD_CONTRACTS
        )
    )
    return f"""
WITH contract (board_slug) AS (
    VALUES
{board_values}
)
SELECT posting.id::text AS posting_id,
       board.id::text AS board_id,
       posting.source_url
FROM contract
JOIN job_board AS board
  ON board.board_slug = contract.board_slug
JOIN job_posting AS posting
  ON posting.board_id = board.id
ORDER BY posting.id
"""


def _board_query() -> str:
    migration = _migration_module()
    board_values = ",\n".join(
        f"({_literal(board_slug)})"
        for board_slug, _company_slug, _board_url, _source_base in (
            migration._UMANTIS_BOARD_CONTRACTS
        )
    )
    return f"""
WITH contract (board_slug) AS (
    VALUES
{board_values}
)
SELECT board.id::text AS board_id,
       board.throttle_key
FROM contract
JOIN job_board AS board
  ON board.board_slug = contract.board_slug
ORDER BY board.id
"""


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _repair_batch(
    redis: aioredis.Redis,
    rows: Sequence[_CutoverRow],
) -> int:
    keys = [f"scrape:{row['posting_id']}" for row in rows]
    arguments = [value for row in rows for value in (str(row["board_id"]), str(row["source_url"]))]
    return int(await redis.eval(_REPAIR_SCRAPE_HASHES_LUA, len(keys), *keys, *arguments))


async def _park_monitor_batch(
    redis: aioredis.Redis,
    rows: Sequence[_CutoverRow],
) -> int:
    arguments = [
        value for row in rows for value in (str(row["board_id"]), str(row["throttle_key"]))
    ]
    return int(await redis.eval(_PARK_MONITORS_LUA, 0, *arguments))


async def repair_umantis_identity_cutover(
    connection: asyncpg.Connection,
    redis: aioredis.Redis,
    *,
    park_monitors: bool = False,
) -> dict[str, int]:
    """Validate revision 0022 and canonicalize every extant scrape hash.

    The caller must keep workers quiesced for the whole command. Each batch is
    all-or-nothing in Redis; failure stops deployment, and a later invocation
    safely resumes because canonical hashes are accepted unchanged.
    """

    # Re-run the exact fail-closed receipt validator. Its writes are a no-op
    # after Alembic, but it detects deleted/tampered receipts before Redis is
    # allowed to move.
    await connection.execute(_migration_sql())
    rows = await connection.fetch(_posting_query())

    changed = 0
    for start in range(0, len(rows), _BATCH_SIZE):
        changed += await _repair_batch(redis, rows[start : start + _BATCH_SIZE])

    # The first pass may be partially committed only if a later batch fails.
    # On success, this second all-batch pass is the consistency proof: every
    # extant hash still has exact board ownership and the canonical DB URL.
    for start in range(0, len(rows), _BATCH_SIZE):
        if await _repair_batch(redis, rows[start : start + _BATCH_SIZE]) != 0:
            raise RuntimeError("Umantis Redis identity verification changed unexpected state")

    parked = 0
    if park_monitors:
        boards = await connection.fetch(_board_query())
        if any(
            not isinstance(row["throttle_key"], str) or not row["throttle_key"] for row in boards
        ):
            raise RuntimeError("Umantis rollback board had no exact throttle key")
        for start in range(0, len(boards), _BATCH_SIZE):
            parked += await _park_monitor_batch(redis, boards[start : start + _BATCH_SIZE])

    return {
        "postings": len(rows),
        "redis_hashes_changed": changed,
        "monitor_entries_parked": parked,
    }
