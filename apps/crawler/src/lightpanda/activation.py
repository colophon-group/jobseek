"""Digest-gated cold cutover and rollback for the fixed Go Lightpanda B0 cohort."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from redis.asyncio import Redis

from src.config import settings
from src.db import close_local_pool, create_local_pool
from src.lightpanda.producer import (
    LightpandaB0ProducerError,
    build_allowlisted_task,
    enqueue_if_allowlisted,
)
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda_queue import MAX_RECORDS, LightpandaB0Queue, RouteIdentity, StoredTask
from src.redis_queue import close_redis, get_redis
from src.runtime.config import BoardRuntimeConfig

_COHORTS = {
    "c1": ("browser-use-careers",),
    "c4": (
        "browser-use-careers",
        "eclypsium-careers",
        "kandou-ai-careers",
        "poke-and-wiggle-careers",
    ),
}

_ROLLBACK_SETTLE_MAX_SECONDS = 75.0
_ROLLBACK_SETTLE_HARD_TIMEOUT_SECONDS = 80.0
_ROLLBACK_SETTLE_POLL_SECONDS = 1.0
_ROLLBACK_SETTLE_MAX_ITERATIONS = 96
_ROLLBACK_MAX_FAILURES = 3

_BOARDS_SQL = """
SELECT id::text AS board_id,
       board_slug,
       board_url,
       is_enabled,
       board_status,
       metadata,
       crawler_type,
       scraper_needs_browser,
       scrape_interval_hours
FROM job_board
WHERE board_slug = ANY($1::text[])
ORDER BY board_slug
"""

_POSTINGS_SQL = """
SELECT jp.id::text AS posting_id,
       jp.board_id::text AS board_id,
       jp.source_url,
       jp.description_r2_hash,
       jp.is_active,
       jp.next_scrape_at,
       jp.leased_until,
       (jp.leased_until > now()) AS lease_active,
       jb.board_slug,
       jb.scraper_needs_browser,
       jb.scrape_interval_hours
FROM job_posting AS jp
JOIN job_board AS jb ON jb.id = jp.board_id
WHERE jb.board_slug = ANY($1::text[])
ORDER BY jp.id
"""

_ROLLBACK_RECORDS_SQL = """
SELECT jp.id::text AS posting_id,
       jp.board_id::text AS board_id,
       jp.source_url,
       jp.description_r2_hash,
       jp.is_active,
       jp.next_scrape_at,
       jp.leased_until,
       (jp.leased_until > now()) AS lease_active,
       jb.board_slug,
       jb.is_enabled,
       jb.board_status,
       jb.scraper_needs_browser,
       jb.scrape_interval_hours
FROM job_posting AS jp
JOIN job_board AS jb ON jb.id = jp.board_id
WHERE jp.id = ANY($1::uuid[])
ORDER BY jp.id
"""

_DELETE_GO_FENCES_SQL = """
DELETE FROM lightpanda_b0_write_fence AS fence
USING job_posting AS jp
WHERE fence.job_posting_id = jp.id
  AND fence.engine_owner = 'go'
  AND (
    fence.job_posting_id = ANY($2::uuid[])
    OR EXISTS (
      SELECT 1 FROM job_board AS jb
      WHERE jb.id = jp.board_id AND jb.board_slug = ANY($1::text[])
    )
  )
"""

_COUNT_GO_FENCES_SQL = """
SELECT count(*)
FROM lightpanda_b0_write_fence AS fence
JOIN job_posting AS jp ON jp.id = fence.job_posting_id
WHERE fence.engine_owner = 'go'
  AND (
    fence.job_posting_id = ANY($2::uuid[])
    OR EXISTS (
      SELECT 1 FROM job_board AS jb
      WHERE jb.id = jp.board_id AND jb.board_slug = ANY($1::text[])
    )
  )
"""

_GO_FENCE_IDS_SQL = """
SELECT job_posting_id::text AS posting_id
FROM lightpanda_b0_write_fence
WHERE engine_owner = 'go'
  AND shard_id = $1
  AND routing_epoch = $2
ORDER BY job_posting_id
"""


class ActivationError(RuntimeError):
    """The bounded cold activation or rollback preconditions were not met."""


@dataclass(frozen=True, slots=True)
class CutoverPlan:
    operation: str
    cohort: str
    digest: str
    document: dict[str, object]
    tasks: tuple[dict[str, Any], ...]

    @property
    def count(self) -> int:
        return len(self.tasks)


def _route() -> RouteIdentity:
    return RouteIdentity(
        shard_id=settings.lightpanda_b0_shard_id,
        routing_epoch=int(settings.lightpanda_b0_routing_epoch),
        engine_owner="go",
    )


def _text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise ActivationError(f"authoritative PostgreSQL row has invalid {field}")
    return value


def _wire_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    if isinstance(value, str):
        return value
    raise ActivationError("Redis returned a non-text authority value")


def _domain(source_url: str) -> str:
    try:
        parsed = urlsplit(source_url)
        port = parsed.port
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ActivationError("authoritative PostgreSQL source URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise ActivationError("authoritative PostgreSQL source URL is outside HTTPS B0")
    return parsed.hostname.encode("idna").decode("ascii").lower()


def _seconds(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ActivationError("authoritative PostgreSQL schedule is not timezone-aware")
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    seconds = Decimal(delta.days * 86_400 + delta.seconds) + (
        Decimal(delta.microseconds) / Decimal(1_000_000)
    )
    text = format(seconds, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _canonical(document: Mapping[str, object]) -> tuple[str, str]:
    encoded = json.dumps(
        document, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    return encoded, hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _board_document(row: Mapping[str, Any]) -> dict[str, object]:
    config = BoardRuntimeConfig.from_mapping(row)
    metadata = config.metadata
    parser_config = config.scraper_config
    if parser_config is None:
        raise ActivationError("fixed cohort board has no parser configuration")
    assignment = resolve_render_assignment(
        str(metadata.get("scraper_type", "")), parser_config, scraper_step=0
    )
    if (
        row.get("is_enabled") is not True
        or _text(row, "board_status") != "active"
        or not config.board_slug
        or not config.board_url
        or not config.crawler_type
        or not config.scraper_needs_browser
        or not 1 <= config.scrape_interval_hours <= 8_760
        or assignment is None
        or assignment.browser_backend != "lightpanda"
    ):
        raise ActivationError("fixed cohort board is not an enabled active Lightpanda lane")
    return {
        "board_id": _text(row, "board_id"),
        "board_slug": config.board_slug,
        "board_url": config.board_url,
        "crawler_type": config.crawler_type,
        "scraper_needs_browser": True,
        "scrape_interval_hours": config.scrape_interval_hours,
        "metadata": metadata,
        "assignment_digest_sha256": assignment.config_digest_sha256,
    }


async def _validated_boards(
    pool: Any, redis: Redis, cohort: str
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, object]]]:
    expected = _COHORTS.get(cohort)
    if expected is None:
        raise ActivationError("cohort must be exactly c1 or c4")
    rows = await pool.fetch(_BOARDS_SQL, list(expected))
    slugs = [_text(row, "board_slug") for row in rows]
    if (
        len(rows) != len(expected)
        or sorted(slugs) != sorted(expected)
        or len(set(slugs)) != len(slugs)
    ):
        raise ActivationError("PostgreSQL does not contain the exact fixed cohort board set")
    by_id: dict[str, Mapping[str, Any]] = {}
    documents: list[dict[str, object]] = []
    for row in rows:
        document = _board_document(row)
        board_id = str(document["board_id"])
        if board_id in by_id:
            raise ActivationError("fixed cohort board UUID is duplicated")
        raw_snapshot = await redis.hgetall(f"board:{board_id}")
        if not raw_snapshot:
            raise ActivationError("fixed cohort Redis board snapshot is missing")
        snapshot_mapping = {
            _wire_text(key): _wire_text(value) for key, value in raw_snapshot.items()
        }
        snapshot = BoardRuntimeConfig.from_mapping(snapshot_mapping)
        if (
            snapshot.board_slug != document["board_slug"]
            or snapshot.board_url != document["board_url"]
            or snapshot.crawler_type != document["crawler_type"]
            or snapshot.scraper_needs_browser is not True
            or snapshot.scrape_interval_hours != document["scrape_interval_hours"]
            or snapshot.metadata != document["metadata"]
        ):
            raise ActivationError("Redis board snapshot disagrees with authoritative PostgreSQL")
        by_id[board_id] = row
        documents.append(document)
    return by_id, documents


async def _existing_records(
    redis: Redis,
    queue: LightpandaB0Queue,
    route: RouteIdentity,
    *,
    allow_dead: bool = False,
) -> dict[str, StoredTask]:
    route_type = _wire_text(await redis.type(queue._keys.route))
    other_types = [_wire_text(await redis.type(key)) for key in queue._keys.ordered()[1:]]
    if route_type == "none":
        if any(value != "none" for value in other_types):
            raise ActivationError("B0 namespace has state without a route fence")
        return {}
    audit = await queue.audit_conservation(route)
    if not audit.accepted:
        raise ActivationError(f"B0 audit failed: {audit.decision.value}/{audit.reason}")
    if audit.secondary_value:
        raise ActivationError("B0 namespace has inflight authority")
    if not allow_dead and await redis.scard(queue._keys.dead):
        raise ActivationError("B0 namespace has dead-letter authority")
    result: dict[str, StoredTask] = {}
    for raw_id in await redis.hkeys(queue._keys.records):
        task_id = _wire_text(raw_id)
        stored = await queue.inspect(task_id, route)
        if stored is None:
            raise ActivationError("B0 record disappeared during audited plan")
        result[task_id] = stored
    return result


async def _prove_no_suffix_authority(redis: Redis, task_ids: set[str]) -> None:
    if not task_ids:
        return
    for worker_type in ("simple", "browser"):
        for prefix in ("inflight", "deadletter"):
            async for raw_member, _score in redis.zscan_iter(f"{prefix}:{worker_type}"):
                member = _wire_text(raw_member)
                if member.startswith("scrape|") and any(
                    member.endswith(f"|{task_id}") for task_id in task_ids
                ):
                    raise ActivationError("cohort has legacy inflight or dead-letter authority")


async def _legacy_preflight(
    redis: Redis,
    tasks: Sequence[dict[str, Any]],
    existing: Mapping[str, StoredTask],
) -> None:
    await _prove_no_suffix_authority(redis, {str(item["posting_id"]) for item in tasks})
    for item in tasks:
        task_id = str(item["posting_id"])
        domain = str(item["domain"])
        config = item["legacy_config"]
        raw_config = await redis.hgetall(f"scrape:{task_id}")
        current_config = {_wire_text(key): _wire_text(value) for key, value in raw_config.items()}
        if task_id not in existing:
            required_fields = set(config) - {"scrape_interval_hours"}
            accepted_fields = (required_fields, set(config))
            if set(current_config) not in accepted_fields or any(
                current_config.get(key) != value
                for key, value in config.items()
                if key in current_config
            ):
                raise ActivationError("legacy scrape hash disagrees with authoritative PostgreSQL")
        memberships = 0
        for worker_type in ("simple", "browser"):
            for prefix in ("ft_scrapes", "scrapes"):
                if await redis.zscore(f"{prefix}_{worker_type}:{domain}", task_id) is not None:
                    memberships += 1
        expected = 0 if task_id in existing else 1
        if memberships != expected:
            raise ActivationError("legacy schedule membership is not exact for cold transfer")


async def build_activation_plan(pool: Any, redis: Redis, *, cohort: str) -> CutoverPlan:
    if settings.lightpanda_b0_producer_mode != "enabled":
        raise ActivationError("B0 producer must be enabled for activation planning")
    if settings.lightpanda_b0_producer_cohort != cohort:
        raise ActivationError("configured and requested fixed cohorts disagree")
    boards, board_documents = await _validated_boards(pool, redis, cohort)
    rows = await pool.fetch(_POSTINGS_SQL, list(_COHORTS[cohort]))
    schedulable = [
        row
        for row in rows
        if row.get("is_active") is True and row.get("next_scrape_at") is not None
    ]
    if not schedulable:
        raise ActivationError("fixed cohort has no schedulable PostgreSQL postings")
    if len(schedulable) > MAX_RECORDS:
        raise ActivationError("fixed cohort exceeds the bounded B0 namespace")
    if any(row.get("lease_active") is True for row in rows):
        raise ActivationError("cohort has a PostgreSQL lease; workers are not quiescent")
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    route = _route()
    existing = await _existing_records(redis, queue, route)
    scheduled_ids = {_text(row, "posting_id") for row in schedulable}
    cohort_board_ids = set(boards)
    if any(
        stored.state != "terminal" or stored.task.board_id not in cohort_board_ids
        for task_id, stored in existing.items()
        if task_id not in scheduled_ids
    ):
        raise ActivationError("live B0 authority is outside the requested current schedule set")
    await _prove_no_suffix_authority(redis, set(existing) | scheduled_ids)
    tasks: list[dict[str, Any]] = []
    for row in schedulable:
        board_id = _text(row, "board_id")
        if board_id not in boards:
            raise ActivationError("posting references a board outside the fixed cohort")
        source_url = _text(row, "source_url")
        domain = _domain(source_url)
        interval = row.get("scrape_interval_hours")
        if (
            isinstance(interval, bool)
            or not isinstance(interval, int)
            or not 1 <= interval <= 8_760
        ):
            raise ActivationError("authoritative scrape interval is invalid")
        raw_hash = row.get("description_r2_hash")
        if raw_hash is not None and (
            isinstance(raw_hash, bool)
            or not isinstance(raw_hash, int)
            or not -(2**63) <= raw_hash < 2**63
        ):
            raise ActivationError("authoritative description hash is invalid")
        config = {
            "domain": domain,
            "board_id": board_id,
            "source_url": source_url,
            "description_r2_hash": "" if raw_hash is None else str(raw_hash),
            "scrape_step": "0",
            "scrape_interval_hours": str(interval),
        }
        next_scrape_at = row["next_scrape_at"]
        task = await build_allowlisted_task(
            redis,
            domain=domain,
            posting_id=_text(row, "posting_id"),
            next_scrape_at=next_scrape_at.timestamp(),
            config=config,
            browser=True,
        )
        if task is None:
            raise ActivationError("authoritative schedule escaped the fixed B0 cohort")
        stored = existing.get(task.task_id)
        if (
            stored is not None
            and stored.state in {"ready", "inflight"}
            and (
                stored.task.board_id != task.board_id
                or stored.task.source_url != task.source_url
                or stored.task.domain != task.domain
                or stored.task.assignment != task.assignment
            )
        ):
            raise ActivationError("live B0 task identity changed without terminal revision")
        tasks.append(
            {
                "posting_id": task.task_id,
                "board_id": board_id,
                "source_url": source_url,
                "domain": domain,
                "next_scrape_at": _seconds(next_scrape_at),
                "payload_sha256": task.payload_sha256,
                "existing_state": stored.state if stored else None,
                "existing_payload_sha256": stored.task.payload_sha256 if stored else None,
                "legacy_config": config,
            }
        )
    await _legacy_preflight(redis, tasks, existing)
    document: dict[str, object] = {
        "schema": "jobseek.lightpanda-b0-cutover-plan/v1",
        "operation": "activate",
        "cohort": cohort,
        "namespace": settings.lightpanda_b0_queue_namespace,
        "shard_id": route.shard_id,
        "routing_epoch": route.routing_epoch,
        "boards": board_documents,
        "tasks": tasks,
        "retained_terminal_ids": sorted(set(existing) - scheduled_ids),
    }
    _encoded, digest = _canonical(document)
    return CutoverPlan("activate", cohort, digest, document, tuple(tasks))


async def apply_activation_plan(
    pool: Any, redis: Redis, *, cohort: str, expect_digest: str
) -> dict[str, int | str]:
    plan = await build_activation_plan(pool, redis, cohort=cohort)
    if expect_digest != plan.digest:
        raise ActivationError("activation plan digest changed; run a new dry-run")
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    initialized = await queue.initialize(_route())
    if not initialized.accepted:
        raise ActivationError(
            f"B0 initialize failed: {initialized.decision.value}/{initialized.reason}"
        )
    activated = 0
    for item in plan.tasks:
        try:
            added = await enqueue_if_allowlisted(
                redis,
                domain=item["domain"],
                posting_id=item["posting_id"],
                next_scrape_at=float(item["next_scrape_at"]),
                config=item["legacy_config"],
                browser=True,
                operator_transfer=True,
            )
        except (LightpandaB0ProducerError, TypeError, ValueError) as exc:
            raise ActivationError("atomic B0 schedule activation failed") from exc
        if added is None:
            raise ActivationError("authoritative schedule escaped the fixed B0 cohort")
        activated += int(added)
    audit = await queue.audit_conservation(_route())
    if (
        not audit.accepted
        or audit.value is None
        or audit.value < plan.count
        or audit.secondary_value
    ):
        raise ActivationError("post-activation B0 conservation audit failed")
    retained = plan.document["retained_terminal_ids"]
    if not isinstance(retained, list):
        raise ActivationError("activation retained-terminal plan shape is invalid")
    await _prove_no_suffix_authority(
        redis,
        {str(item["posting_id"]) for item in plan.tasks} | {str(value) for value in retained},
    )
    return {
        "selected": plan.count,
        "activated": activated,
        "already_activated": plan.count - activated,
        "digest": plan.digest,
    }


async def build_rollback_plan(pool: Any, redis: Redis, *, cohort: str) -> CutoverPlan:
    if settings.lightpanda_b0_producer_mode != "off":
        raise ActivationError("B0 producer must be off for rollback planning")
    if cohort not in _COHORTS:
        raise ActivationError("cohort must be exactly c1 or c4")
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    route = _route()
    namespace_present = _wire_text(await redis.type(queue._keys.route)) != "none"
    existing = await _existing_records(redis, queue, route, allow_dead=True)
    task_ids = set(existing)
    await _prove_no_suffix_authority(redis, task_ids)
    cohort_rows = await pool.fetch(_POSTINGS_SQL, list(_COHORTS[cohort]))
    fence_rows = await pool.fetch(_GO_FENCE_IDS_SQL, route.shard_id, route.routing_epoch)
    fence_task_ids = {_text(row, "posting_id") for row in fence_rows}
    query_ids = task_ids | fence_task_ids | {_text(row, "posting_id") for row in cohort_rows}
    rows = await pool.fetch(_ROLLBACK_RECORDS_SQL, list(query_ids)) if query_ids else []
    if any(row.get("lease_active") is True for row in rows):
        raise ActivationError("cohort has a PostgreSQL lease; rollback is not cold")
    by_id = {_text(row, "posting_id"): row for row in rows}
    tasks: list[dict[str, Any]] = []
    redis_plan: dict[str, dict[str, object]] = {}
    for task_id, stored in sorted(existing.items()):
        row = by_id.get(task_id)
        if (
            row is None
            or row.get("is_active") is not True
            or row.get("next_scrape_at") is None
            or row.get("is_enabled") is not True
            or row.get("board_status") != "active"
        ):
            redis_plan[task_id] = {"action": "drop"}
            tasks.append({"posting_id": task_id, "action": "drop"})
            continue
        source_url = _text(row, "source_url")
        domain = _domain(source_url)
        board_id = _text(row, "board_id")
        raw_hash = row.get("description_r2_hash")
        interval = row.get("scrape_interval_hours")
        if raw_hash is not None and (
            isinstance(raw_hash, bool)
            or not isinstance(raw_hash, int)
            or not -(2**63) <= raw_hash < 2**63
        ):
            raise ActivationError("authoritative rollback description hash is invalid")
        if (
            isinstance(interval, bool)
            or not isinstance(interval, int)
            or not 1 <= interval <= 8_760
        ):
            raise ActivationError("authoritative rollback interval is invalid")
        first_time = raw_hash is None
        score = "0" if first_time else _seconds(row["next_scrape_at"])
        config = {
            "domain": domain,
            "board_id": board_id,
            "source_url": source_url,
            "description_r2_hash": "" if raw_hash is None else str(raw_hash),
            "scrape_step": "0",
            "scrape_interval_hours": str(interval),
        }
        entry: dict[str, object] = {
            "action": "schedule",
            "domain": domain,
            "worker_type": "browser" if row.get("scraper_needs_browser") is True else "simple",
            "first_time": first_time,
            "score": score,
            "config": config,
        }
        redis_plan[task_id] = entry
        tasks.append({"posting_id": task_id, **entry})
        if stored.task.board_id != board_id or stored.task.source_url != source_url:
            tasks[-1]["identity_changed_since_activation"] = True
    document: dict[str, object] = {
        "schema": "jobseek.lightpanda-b0-cutover-plan/v1",
        "operation": "rollback",
        "cohort": cohort,
        "namespace": settings.lightpanda_b0_queue_namespace,
        "shard_id": route.shard_id,
        "routing_epoch": route.routing_epoch,
        "tasks": tasks,
        "redis_plan": redis_plan,
        "fence_task_ids": sorted(fence_task_ids | task_ids),
        "namespace_present": namespace_present,
    }
    _encoded, digest = _canonical(document)
    return CutoverPlan("rollback", cohort, digest, document, tuple(tasks))


async def _settle_rollback_namespace(redis: Redis) -> dict[str, int]:
    """Cold-reap expired B0 leases before the atomic rollback plan gate.

    Redis TIME from the fenced queue audit decides whether a lease is live. The
    client monotonic clock only bounds how long this operator recovery waits.
    """

    if settings.lightpanda_b0_producer_mode != "off":
        raise ActivationError("B0 producer must be off for rollback settling")
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    route = _route()
    route_type = _wire_text(await redis.type(queue._keys.route))
    other_types = [_wire_text(await redis.type(key)) for key in queue._keys.ordered()[1:]]
    if route_type == "none":
        if any(value != "none" for value in other_types):
            raise ActivationError("B0 namespace has state without a route fence")
        return {"inflight_settled": 0, "expired_requeued": 0, "dead_preserved": 0}

    deadline = time.monotonic() + _ROLLBACK_SETTLE_MAX_SECONDS
    settled = 0
    requeued = 0
    for _iteration in range(_ROLLBACK_SETTLE_MAX_ITERATIONS):
        audit = await queue.audit_conservation(route)
        if not audit.accepted or audit.server_time_ms is None:
            raise ActivationError(f"B0 audit failed: {audit.decision.value}/{audit.reason}")
        dead_count = await redis.scard(queue._keys.dead)
        inflight = audit.secondary_value
        if inflight is None:
            raise ActivationError("B0 audit returned an invalid inflight count")
        if inflight == 0:
            return {
                "inflight_settled": settled,
                "expired_requeued": requeued,
                "dead_preserved": dead_count,
            }

        outcome = await queue.reap_expired(route, max_failures=_ROLLBACK_MAX_FAILURES)
        if not outcome.accepted:
            raise ActivationError(
                f"B0 expired-lease reap failed: {outcome.decision.value}/{outcome.reason}"
            )
        reaped = (outcome.value or 0) + (outcome.secondary_value or 0)
        if reaped:
            settled += reaped
            requeued += outcome.value or 0
            continue

        earliest = await redis.zrange(queue._keys.inflight, 0, 0, withscores=True)
        if len(earliest) != 1:
            raise ActivationError("B0 inflight index changed during cold rollback settling")
        task_id, raw_lease_until_ms = earliest[0]
        _wire_text(task_id)
        lease_until_ms = float(raw_lease_until_ms)
        if (
            not math.isfinite(lease_until_ms)
            or not lease_until_ms.is_integer()
            or lease_until_ms < 1
        ):
            raise ActivationError("B0 inflight index has an invalid lease deadline")
        wait_seconds = (int(lease_until_ms) - audit.server_time_ms + 1) / 1000
        if wait_seconds <= 0:
            raise ActivationError("B0 expired lease could not be reaped under its fences")
        remaining = deadline - time.monotonic()
        if remaining <= 0 or wait_seconds > remaining:
            raise ActivationError("B0 live leases exceed the bounded cold rollback wait")
        await asyncio.sleep(min(wait_seconds, _ROLLBACK_SETTLE_POLL_SECONDS, remaining))

    raise ActivationError("B0 rollback settling exceeded its bounded scan limit")


async def settle_rollback_namespace(redis: Redis) -> dict[str, int]:
    try:
        async with asyncio.timeout(_ROLLBACK_SETTLE_HARD_TIMEOUT_SECONDS):
            return await _settle_rollback_namespace(redis)
    except TimeoutError as exc:
        raise ActivationError("B0 rollback settling exceeded its hard timeout") from exc


async def apply_rollback_plan(
    pool: Any, redis: Redis, *, cohort: str, expect_digest: str
) -> dict[str, int | str]:
    plan = await build_rollback_plan(pool, redis, cohort=cohort)
    if plan.digest != expect_digest:
        raise ActivationError("rollback plan digest changed; run a new dry-run")
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    redis_plan = plan.document["redis_plan"]
    assert isinstance(redis_plan, dict)
    restored = dropped = 0
    if redis_plan or plan.document.get("namespace_present") is True:
        outcome = await queue.rollback_legacy(_route(), plan=redis_plan)
        if not outcome.accepted:
            raise ActivationError(
                f"cold rollback failed: {outcome.decision.value}/{outcome.reason}"
            )
        restored = outcome.value or 0
        dropped = outcome.secondary_value or 0
    raw_fence_task_ids = plan.document["fence_task_ids"]
    if not isinstance(raw_fence_task_ids, list):
        raise ActivationError("rollback fence plan shape is invalid")
    task_ids = [str(value) for value in raw_fence_task_ids]
    await pool.execute(_DELETE_GO_FENCES_SQL, list(_COHORTS[cohort]), task_ids)
    remaining = await pool.fetchval(_COUNT_GO_FENCES_SQL, list(_COHORTS[cohort]), task_ids)
    if remaining != 0:
        raise ActivationError("Go PostgreSQL write fences remain after Redis rollback")
    return {
        "ready_restored": restored,
        "terminal_dropped": dropped,
        "write_fences_remaining": 0,
        "digest": plan.digest,
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    redis = get_redis()
    if args.command == "settle-rollback":
        try:
            return {
                "operation": "settle-rollback",
                "cohort": args.cohort,
                **await settle_rollback_namespace(redis),
            }
        finally:
            await close_redis()
    pool = await create_local_pool()
    try:
        if args.command == "plan":
            plan = (
                await build_activation_plan(pool, redis, cohort=args.cohort)
                if args.operation == "activate"
                else await build_rollback_plan(pool, redis, cohort=args.cohort)
            )
            return {"digest": plan.digest, "count": plan.count, "plan": plan.document}
        if args.command == "activate":
            return {
                "operation": "activate",
                "cohort": args.cohort,
                **await apply_activation_plan(
                    pool, redis, cohort=args.cohort, expect_digest=args.expect_digest
                ),
            }
        return {
            "operation": "rollback",
            "cohort": args.cohort,
            **await apply_rollback_plan(
                pool, redis, cohort=args.cohort, expect_digest=args.expect_digest
            ),
        }
    finally:
        await close_redis()
        await close_local_pool()


def main() -> None:
    parser = argparse.ArgumentParser(prog="lightpanda-b0-activation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--operation", choices=("activate", "rollback"), required=True)
    plan.add_argument("--cohort", choices=tuple(_COHORTS), required=True)
    settle = subparsers.add_parser("settle-rollback")
    settle.add_argument("--cohort", choices=tuple(_COHORTS), required=True)
    for command in ("activate", "rollback"):
        apply_parser = subparsers.add_parser(command)
        apply_parser.add_argument("--cohort", choices=tuple(_COHORTS), required=True)
        apply_parser.add_argument("--apply", action="store_true", required=True)
        apply_parser.add_argument("--expect-digest", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(_run(args)), allow_nan=False, sort_keys=True))
    except ActivationError as exc:
        raise SystemExit(f"activation refused: {exc}") from exc


if __name__ == "__main__":
    main()
