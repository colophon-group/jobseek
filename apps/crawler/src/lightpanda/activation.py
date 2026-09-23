"""Digest-gated cold cutover and rollback for the fixed Go Lightpanda B0 cohort."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import stat
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from redis.asyncio import Redis

from src.config import settings
from src.db import close_local_pool, create_local_pool
from src.lightpanda.producer_client import (
    ProducerClientError,
    request_manifest,
    request_task,
)
from src.lightpanda_queue import MAX_RECORDS, LightpandaB0Queue, RouteIdentity, StoredTask
from src.redis_queue import close_redis, get_redis
from src.runtime.config import BoardRuntimeConfig

_COHORT_NAMES = ("c1", "c3")
_PRODUCER_AUTHORITY_DIRECTORY = Path("/run/jobseek-lightpanda-producer")
_PRODUCER_ACTIVATION_MARKER = ".activation-v1"
_PRODUCER_AUTHORITY_UID = 10001
_PRODUCER_SENTINEL_MAX_BYTES = 4096
_PRODUCER_SENTINEL_SCHEMA = "jobseek.lightpanda.producer-activation/v1"
_PRODUCER_OWNER_KEY = "lightpanda-b0:producer-owner"
_PRODUCER_OWNER_SCHEMA = "jobseek.lightpanda.producer-owner/v1"
_ROLLBACK_TOMBSTONE_SCHEMA = "jobseek.lightpanda.producer-rollback/v1"
_LEGACY_GUARD_KEY = "lightpanda-b0:legacy-guard"
_SAFE_PRODUCER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_SECONDS = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_ROUTING_EPOCH_ADVISORY_LOCK_ID = 7_544_422_533_504_811_009

_ROLLBACK_SETTLE_MAX_SECONDS = 75.0
_ROLLBACK_SETTLE_HARD_TIMEOUT_SECONDS = 80.0
_ROLLBACK_SETTLE_POLL_SECONDS = 1.0
_ROLLBACK_SETTLE_MAX_ITERATIONS = 96
_ROLLBACK_MAX_FAILURES = 3
_PILOT_LIFETIME_CAPACITY = 2_048
_PILOT_ROLLBACK_SIGNAL_THRESHOLD = 1_600

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

_DELETE_ROUTE_GO_FENCES_SQL = """
DELETE FROM lightpanda_b0_write_fence
WHERE engine_owner = 'go'
  AND shard_id = $1
  AND routing_epoch = $2
"""

_COUNT_ROUTE_GO_FENCES_SQL = """
SELECT count(*)
FROM lightpanda_b0_write_fence
WHERE engine_owner = 'go'
  AND shard_id = $1
  AND routing_epoch = $2
"""

_GO_FENCE_IDS_SQL = """
SELECT job_posting_id::text AS posting_id
FROM lightpanda_b0_write_fence
WHERE engine_owner = 'go'
  AND shard_id = $1
  AND routing_epoch = $2
ORDER BY job_posting_id
"""

_RESERVE_ROUTING_EPOCH_SQL = "SELECT nextval('public.lightpanda_b0_routing_epoch_seq'::regclass)"
_LOCK_ROUTING_EPOCH_ALLOCATOR_SQL = "SELECT pg_advisory_xact_lock($1)"
_CURRENT_ROUTING_EPOCH_SQL = (
    "SELECT last_value, is_called FROM public.lightpanda_b0_routing_epoch_seq"
)


class ActivationError(RuntimeError):
    """The bounded cold activation or rollback preconditions were not met."""


@dataclass(frozen=True, slots=True)
class CutoverPlan:
    operation: str
    cohort: str
    digest: str
    document: dict[str, object]
    tasks: tuple[dict[str, Any], ...]
    rollback_plan_digest: str | None = None

    @property
    def count(self) -> int:
        return len(self.tasks)

    @property
    def rollback_commit_digest(self) -> str:
        return self.rollback_plan_digest or self.digest


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


async def _bound_legacy_guard(redis: Redis, task_id: str, stored: StoredTask) -> tuple[str, str]:
    raw = await redis.hget(_LEGACY_GUARD_KEY, task_id)
    if raw is None:
        raise ActivationError("existing B0 record lost its legacy transfer guard")
    parts = _wire_text(raw).split("|")
    kinds = {"ft_browser", "ft_simple", "recurring_browser", "recurring_simple"}
    if (
        len(parts) != 7
        or parts[0] != settings.lightpanda_b0_queue_namespace
        or parts[1] != stored.task.route.shard_id
        or parts[2] != str(stored.task.route.routing_epoch)
        or parts[3] != stored.task.board_id
        or parts[4] != stored.task.domain
        or parts[5] not in kinds
        or _DECIMAL_SECONDS.fullmatch(parts[6]) is None
    ):
        raise ActivationError("existing B0 record has an invalid legacy transfer guard")
    try:
        score = Decimal(parts[6])
    except ArithmeticError as exc:
        raise ActivationError("existing B0 record has an invalid legacy transfer guard") from exc
    if not score.is_finite() or not Decimal(0) <= score <= (
        Decimal(9_999_999_999_999) / Decimal(1_000)
    ):
        raise ActivationError("existing B0 record has an invalid legacy transfer guard")
    return parts[5], parts[6]


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


def _go_ready_at(value: datetime, *, first_time: bool) -> str:
    scheduled = _seconds(value)
    return "0" if first_time else scheduled


def _canonical(document: Mapping[str, object]) -> tuple[str, str]:
    encoded = json.dumps(
        document, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    return encoded, hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _producer_marker_phase(cohort: str) -> str:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(_PRODUCER_AUTHORITY_DIRECTORY, directory_flags)
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        raise ActivationError("producer authority volume cannot be attested") from exc
    try:
        directory = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory.st_mode) or (
            directory.st_uid,
            stat.S_IMODE(directory.st_mode),
        ) not in {(0, 0o755), (_PRODUCER_AUTHORITY_UID, 0o700)}:
            return "unsafe"
        marker_flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            marker_flags |= os.O_NOFOLLOW
        try:
            marker_fd = os.open(
                _PRODUCER_ACTIVATION_MARKER,
                marker_flags,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return "absent"
        except OSError:
            return "unsafe"
        try:
            marker = os.fstat(marker_fd)
            if (
                not stat.S_ISREG(marker.st_mode)
                or stat.S_IMODE(marker.st_mode) != 0o600
                or marker.st_uid != _PRODUCER_AUTHORITY_UID
                or marker.st_nlink != 1
                or not 0 <= marker.st_size <= _PRODUCER_SENTINEL_MAX_BYTES
            ):
                return "unsafe"
            payload = os.read(marker_fd, _PRODUCER_SENTINEL_MAX_BYTES + 1)
            after = os.fstat(marker_fd)
            if len(payload) != marker.st_size or (after.st_dev, after.st_ino, after.st_size) != (
                marker.st_dev,
                marker.st_ino,
                marker.st_size,
            ):
                return "unsafe"
        finally:
            os.close(marker_fd)
    finally:
        os.close(directory_fd)
    if payload == b"" or payload == b"P":
        return "partial"
    if payload.startswith(b"P\n") and not payload.endswith(b"\n"):
        return "partial"
    if len(payload) < 3 or payload[:2] not in {b"P\n", b"A\n"} or not payload.endswith(b"\n"):
        return "unsafe"
    try:
        document = json.loads(payload[2:-1])
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unsafe"
    if not isinstance(document, dict) or set(document) != {
        "board_slugs",
        "cohort",
        "engine_owner",
        "namespace",
        "routing_epoch",
        "schema",
        "shard_id",
    }:
        return "unsafe"
    board_slugs = document["board_slugs"]
    if (
        document["schema"] != _PRODUCER_SENTINEL_SCHEMA
        or document["cohort"] != cohort
        or document["engine_owner"] != "go"
        or document["namespace"] != settings.lightpanda_b0_queue_namespace
        or document["shard_id"] != settings.lightpanda_b0_shard_id
        or document["routing_epoch"] != int(settings.lightpanda_b0_routing_epoch)
        or not isinstance(board_slugs, list)
        or not 1 <= len(board_slugs) <= 16
        or any(
            not isinstance(slug, str) or _SAFE_PRODUCER_ID.fullmatch(slug) is None
            for slug in board_slugs
        )
        or board_slugs != sorted(set(board_slugs))
    ):
        return "unsafe"
    encoded, _digest = _canonical(document)
    if payload[2:-1] != encoded.encode("ascii"):
        return "unsafe"
    return "preparing" if payload[0:1] == b"P" else "active"


def _validate_absent_namespace_rollback(*, receipt_state: str, marker_phase: str) -> None:
    if receipt_state == "active":
        raise ActivationError("active receipt lost its Redis namespace")
    if receipt_state != "pending":
        raise ActivationError("rollback receipt state must be active or pending")
    if marker_phase not in {"absent", "preparing", "partial"}:
        raise ActivationError("pending receipt cannot prove pre-commit Redis absence")


def _validate_present_namespace_rollback(*, receipt_state: str, marker_phase: str) -> None:
    if receipt_state == "active" and marker_phase != "active":
        raise ActivationError("active receipt and Redis owner require the exact active sentinel")
    if receipt_state == "pending" and marker_phase not in {"active", "preparing"}:
        raise ActivationError("pending Redis owner requires a complete producer sentinel")


def _board_document(row: Mapping[str, Any]) -> dict[str, object]:
    config = BoardRuntimeConfig.from_mapping(row)
    metadata = config.metadata
    if (
        row.get("is_enabled") is not True
        or _text(row, "board_status") != "active"
        or not config.board_slug
        or not config.board_url
        or not config.crawler_type
        or not config.scraper_needs_browser
        or not 1 <= config.scrape_interval_hours <= 8_760
    ):
        raise ActivationError("fixed cohort board is not enabled, active, and browser-backed")
    return {
        "board_id": _text(row, "board_id"),
        "board_slug": config.board_slug,
        "board_url": config.board_url,
        "crawler_type": config.crawler_type,
        "scraper_needs_browser": True,
        "scrape_interval_hours": config.scrape_interval_hours,
        "metadata": metadata,
    }


async def _validated_boards(
    pool: Any, redis: Redis, board_slugs: Sequence[str]
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, object]]]:
    if not board_slugs or len(set(board_slugs)) != len(board_slugs):
        raise ActivationError("Go producer returned an invalid cohort manifest")
    rows = await pool.fetch(_BOARDS_SQL, list(board_slugs))
    slugs = [_text(row, "board_slug") for row in rows]
    if (
        len(rows) != len(board_slugs)
        or sorted(slugs) != sorted(board_slugs)
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
    try:
        result = await queue.inspect_many(route)
    except RuntimeError as exc:
        raise ActivationError("B0 batch record inspection failed closed") from exc
    if any(stored.state == "inflight" for stored in result.values()):
        raise ActivationError("B0 namespace has inflight authority")
    if not allow_dead and any(stored.state == "dead" for stored in result.values()):
        raise ActivationError("B0 namespace has dead-letter authority")
    return result


async def _producer_owner_schema(redis: Redis) -> str | None:
    owner_type = _wire_text(await redis.type(_PRODUCER_OWNER_KEY))
    if owner_type == "none":
        return None
    if owner_type != "hash":
        raise ActivationError("producer owner authority has an invalid Redis type")
    schema = await redis.hget(_PRODUCER_OWNER_KEY, "schema")
    if schema is None:
        raise ActivationError("producer owner authority has no schema")
    return _wire_text(schema)


async def _bound_rollback_tombstone(
    redis: Redis,
    queue: LightpandaB0Queue,
    route: RouteIdentity,
    *,
    cohort: str,
    source_receipt_sha256: str,
    expected_plan_digest: str | None = None,
) -> dict[str, str] | None:
    schema = await _producer_owner_schema(redis)
    if schema is None:
        return None
    if schema != _ROLLBACK_TOMBSTONE_SCHEMA:
        raise ActivationError("producer owner is not a rollback tombstone")
    raw = await redis.hgetall(_PRODUCER_OWNER_KEY)
    values = {_wire_text(key): _wire_text(value) for key, value in raw.items()}
    expected_keys = {
        "schema",
        "namespace",
        "shard_id",
        "routing_epoch",
        "engine_owner",
        "cohort",
        "rollback_plan_digest",
        "source_receipt_sha256",
    }
    if set(values) != expected_keys or any(
        (
            values.get("schema") != _ROLLBACK_TOMBSTONE_SCHEMA,
            values.get("namespace") != settings.lightpanda_b0_queue_namespace,
            values.get("shard_id") != route.shard_id,
            values.get("routing_epoch") != str(route.routing_epoch),
            values.get("engine_owner") != "go",
            values.get("cohort") != cohort,
            values.get("source_receipt_sha256") != source_receipt_sha256,
            _SHA256.fullmatch(values.get("rollback_plan_digest", "")) is None,
        )
    ):
        raise ActivationError("rollback tombstone is malformed, stale, or unbound")
    if expected_plan_digest is not None and values["rollback_plan_digest"] != expected_plan_digest:
        raise ActivationError("rollback tombstone plan digest is stale")
    namespace_types = [_wire_text(await redis.type(key)) for key in queue._keys.ordered()]
    guard_type = _wire_text(await redis.type(_LEGACY_GUARD_KEY))
    if any(value != "none" for value in namespace_types) or guard_type != "none":
        raise ActivationError("rollback tombstone coexists with Redis authority")
    return values


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
) -> dict[str, bool]:
    await _prove_no_suffix_authority(redis, {str(item["posting_id"]) for item in tasks})
    first_time_by_id: dict[str, bool] = {}
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
        membership_kinds: list[str] = []
        for worker_type in ("simple", "browser"):
            for prefix in ("ft_scrapes", "scrapes"):
                if await redis.zscore(f"{prefix}_{worker_type}:{domain}", task_id) is not None:
                    membership_kinds.append(prefix)
        expected = 0 if task_id in existing else 1
        if len(membership_kinds) != expected:
            raise ActivationError("legacy schedule membership is not exact for cold transfer")
        if task_id in existing:
            stored = existing[task_id]
            guard_kind, _guard_score = await _bound_legacy_guard(redis, task_id, stored)
            first_time_by_id[task_id] = (
                config.get("description_r2_hash") == ""
                if stored.state in {"terminal", "dead"}
                else guard_kind.startswith("ft_")
            )
        else:
            first_time_by_id[task_id] = membership_kinds == ["ft_scrapes"]
    return first_time_by_id


def _pilot_capacity(result: object) -> tuple[int, int, int]:
    occupancy = getattr(result, "lifetime_occupancy", None)
    capacity = getattr(result, "lifetime_capacity", None)
    headroom = getattr(result, "lifetime_headroom", None)
    if (
        type(occupancy) is not int
        or type(capacity) is not int
        or type(headroom) is not int
        or capacity != _PILOT_LIFETIME_CAPACITY
        or not 0 <= occupancy <= capacity
        or headroom != capacity - occupancy
    ):
        raise ActivationError("Go producer returned invalid lifetime capacity telemetry")
    return occupancy, capacity, headroom


async def build_activation_plan(pool: Any, redis: Redis, *, cohort: str) -> CutoverPlan:
    if settings.lightpanda_b0_producer_mode != "enabled":
        raise ActivationError("B0 producer must be enabled for activation planning")
    try:
        manifest = await request_manifest(cohort)
    except ProducerClientError as exc:
        raise ActivationError("Go producer cohort manifest failed closed") from exc
    if manifest.outcome != "manifest" or manifest.cohort != cohort:
        raise ActivationError("Go producer returned the wrong cohort manifest")
    baseline_occupancy, lifetime_capacity, lifetime_headroom = _pilot_capacity(manifest)
    board_slugs = manifest.board_slugs
    boards, board_documents = await _validated_boards(pool, redis, board_slugs)
    rows = await pool.fetch(_POSTINGS_SQL, list(board_slugs))
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
    if baseline_occupancy != len(existing):
        raise ActivationError("producer lifetime occupancy disagrees with audited records")
    scheduled_ids = {_text(row, "posting_id") for row in schedulable}
    new_record_count = len(scheduled_ids - set(existing))
    projected_occupancy = baseline_occupancy + new_record_count
    if new_record_count > lifetime_headroom:
        raise ActivationError("fixed cohort exceeds the producer lifetime headroom")
    if projected_occupancy > _PILOT_ROLLBACK_SIGNAL_THRESHOLD:
        raise ActivationError("fixed cohort exceeds the fail-closed pilot occupancy limit")
    cohort_board_ids = set(boards)
    if any(
        stored.state != "terminal" or stored.task.board_id not in cohort_board_ids
        for task_id, stored in existing.items()
        if task_id not in scheduled_ids
    ):
        raise ActivationError("live B0 authority is outside the requested current schedule set")
    await _prove_no_suffix_authority(redis, set(existing) | scheduled_ids)
    preliminary_tasks: list[dict[str, Any]] = []
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
        posting_id = _text(row, "posting_id")
        preliminary_tasks.append(
            {
                "posting_id": posting_id,
                "board_id": board_id,
                "source_url": source_url,
                "domain": domain,
                "next_scrape_at": next_scrape_at,
                "legacy_config": config,
            }
        )
    first_time_by_id = await _legacy_preflight(redis, preliminary_tasks, existing)
    tasks: list[dict[str, Any]] = []
    for item in preliminary_tasks:
        posting_id = str(item["posting_id"])
        first_time = first_time_by_id[posting_id]
        go_ready_at = _go_ready_at(item["next_scrape_at"], first_time=first_time)
        try:
            prepared = await request_task(
                operation="prepare",
                domain=item["domain"],
                posting_id=posting_id,
                next_scrape_at=float(go_ready_at),
                config=item["legacy_config"],
                browser=True,
                operator_transfer=True,
                first_time=first_time,
            )
        except ProducerClientError as exc:
            raise ActivationError("Go producer preparation failed closed") from exc
        if prepared.is_legacy or prepared.outcome != "prepared":
            raise ActivationError("authoritative schedule escaped the fixed B0 cohort")
        stored = existing.get(posting_id)
        if prepared.existing_state != (
            stored.state if stored else ""
        ) or prepared.existing_payload_sha256 != (stored.task.payload_sha256 if stored else ""):
            raise ActivationError("Go producer and audited queue state disagree")
        tasks.append(
            {
                "posting_id": posting_id,
                "board_id": item["board_id"],
                "source_url": item["source_url"],
                "domain": item["domain"],
                "next_scrape_at": go_ready_at,
                "first_time": first_time,
                "payload_sha256": prepared.payload_sha256,
                "preparation_digest": prepared.preparation_digest,
                "existing_state": stored.state if stored else None,
                "existing_payload_sha256": stored.task.payload_sha256 if stored else None,
                "legacy_config": item["legacy_config"],
            }
        )
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
        "lifetime_occupancy": baseline_occupancy,
        "lifetime_capacity": lifetime_capacity,
        "lifetime_headroom": lifetime_headroom,
        "new_record_count": new_record_count,
        "projected_lifetime_occupancy": projected_occupancy,
        "pilot_rollback_signal_threshold": _PILOT_ROLLBACK_SIGNAL_THRESHOLD,
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
    activated = 0
    for item in plan.tasks:
        try:
            result = await request_task(
                operation="activate",
                domain=item["domain"],
                posting_id=item["posting_id"],
                next_scrape_at=float(item["next_scrape_at"]),
                config=item["legacy_config"],
                browser=True,
                operator_transfer=True,
                first_time=item["first_time"],
                expected_digest=item["preparation_digest"],
            )
        except (ProducerClientError, TypeError, ValueError) as exc:
            raise ActivationError("atomic B0 schedule activation failed") from exc
        if result.outcome != "activated":
            raise ActivationError("authoritative schedule escaped the fixed B0 cohort")
        activated += int(result.activated)
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
    try:
        post_manifest = await request_manifest(cohort)
    except ProducerClientError as exc:
        raise ActivationError("post-activation producer capacity attestation failed") from exc
    occupancy, capacity, headroom = _pilot_capacity(post_manifest)
    projected = plan.document["projected_lifetime_occupancy"]
    if (
        occupancy != projected
        or occupancy > _PILOT_ROLLBACK_SIGNAL_THRESHOLD
        or capacity != plan.document["lifetime_capacity"]
        or headroom != capacity - occupancy
    ):
        raise ActivationError("post-activation producer capacity attestation changed")
    return {
        "selected": plan.count,
        "activated": activated,
        "already_activated": plan.count - activated,
        "digest": plan.digest,
        "lifetime_occupancy": occupancy,
        "lifetime_headroom": headroom,
        "pilot_rollback_signal_threshold": _PILOT_ROLLBACK_SIGNAL_THRESHOLD,
    }


async def build_rollback_plan(
    pool: Any,
    redis: Redis,
    *,
    cohort: str,
    receipt_state: str,
    source_receipt_sha256: str,
) -> CutoverPlan:
    if settings.lightpanda_b0_producer_mode != "off":
        raise ActivationError("B0 producer must be off for rollback planning")
    if cohort not in _COHORT_NAMES:
        raise ActivationError("cohort must be exactly c1 or c3")
    if receipt_state not in {"active", "pending"}:
        raise ActivationError("rollback receipt state must be active or pending")
    if _SHA256.fullmatch(source_receipt_sha256) is None:
        raise ActivationError("source receipt SHA-256 is invalid")
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    route = _route()
    namespace_present = _wire_text(await redis.type(queue._keys.route)) != "none"
    marker_phase = _producer_marker_phase(cohort)
    owner_schema = await _producer_owner_schema(redis)
    if owner_schema == _ROLLBACK_TOMBSTONE_SCHEMA:
        if marker_phase == "unsafe":
            raise ActivationError("rollback tombstone has an unsafe producer sentinel")
        tombstone = await _bound_rollback_tombstone(
            redis,
            queue,
            route,
            cohort=cohort,
            source_receipt_sha256=source_receipt_sha256,
        )
        assert tombstone is not None
        fence_rows = await pool.fetch(_GO_FENCE_IDS_SQL, route.shard_id, route.routing_epoch)
        fence_task_ids = {_text(row, "posting_id") for row in fence_rows}
        document: dict[str, object] = {
            "schema": "jobseek.lightpanda-b0-cutover-plan/v1",
            "operation": "rollback",
            "cohort": cohort,
            "receipt_state": receipt_state,
            "source_receipt_sha256": source_receipt_sha256,
            "producer_marker_phase": marker_phase,
            "namespace": settings.lightpanda_b0_queue_namespace,
            "shard_id": route.shard_id,
            "routing_epoch": route.routing_epoch,
            "tasks": [],
            "redis_plan": {},
            "fence_task_ids": sorted(fence_task_ids),
            "namespace_present": False,
            "rollback_committed": True,
            "rollback_plan_digest": tombstone["rollback_plan_digest"],
        }
        _encoded, recovery_digest = _canonical(document)
        return CutoverPlan(
            "rollback",
            cohort,
            recovery_digest,
            document,
            (),
            tombstone["rollback_plan_digest"],
        )
    if owner_schema not in {None, _PRODUCER_OWNER_SCHEMA}:
        raise ActivationError("producer owner authority has an unknown schema")
    if namespace_present != (owner_schema == _PRODUCER_OWNER_SCHEMA):
        raise ActivationError("B0 namespace and producer owner authority disagree")
    existing = await _existing_records(redis, queue, route, allow_dead=True)
    if not namespace_present:
        _validate_absent_namespace_rollback(receipt_state=receipt_state, marker_phase=marker_phase)
        if _wire_text(await redis.type(_LEGACY_GUARD_KEY)) != "none":
            raise ActivationError("empty rollback has legacy guard authority")
    else:
        _validate_present_namespace_rollback(receipt_state=receipt_state, marker_phase=marker_phase)
    task_ids = set(existing)
    await _prove_no_suffix_authority(redis, task_ids)
    fence_rows = await pool.fetch(_GO_FENCE_IDS_SQL, route.shard_id, route.routing_epoch)
    fence_task_ids = {_text(row, "posting_id") for row in fence_rows}
    query_ids = task_ids | fence_task_ids
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
        if stored.state == "ready":
            guard_kind, score = await _bound_legacy_guard(redis, task_id, stored)
            first_time = guard_kind.startswith("ft_")
            worker_type = guard_kind.removeprefix("ft_").removeprefix("recurring_")
        else:
            first_time = raw_hash is None
            score = "0" if first_time else _seconds(row["next_scrape_at"])
            worker_type = "browser" if row.get("scraper_needs_browser") is True else "simple"
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
            "worker_type": worker_type,
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
        "receipt_state": receipt_state,
        "source_receipt_sha256": source_receipt_sha256,
        "producer_marker_phase": marker_phase,
        "namespace": settings.lightpanda_b0_queue_namespace,
        "shard_id": route.shard_id,
        "routing_epoch": route.routing_epoch,
        "tasks": tasks,
        "redis_plan": redis_plan,
        "fence_task_ids": sorted(fence_task_ids | task_ids),
        "namespace_present": namespace_present,
        "rollback_committed": False,
    }
    _encoded, digest = _canonical(document)
    return CutoverPlan("rollback", cohort, digest, document, tuple(tasks))


async def _settle_rollback_namespace(
    redis: Redis, *, cohort: str, receipt_state: str, source_receipt_sha256: str
) -> dict[str, int]:
    """Cold-reap expired B0 leases before the atomic rollback plan gate.

    Redis TIME from the fenced queue audit decides whether a lease is live. The
    client monotonic clock only bounds how long this operator recovery waits.
    """

    if settings.lightpanda_b0_producer_mode != "off":
        raise ActivationError("B0 producer must be off for rollback settling")
    if cohort not in _COHORT_NAMES or receipt_state not in {"active", "pending"}:
        raise ActivationError("rollback identity is invalid")
    if _SHA256.fullmatch(source_receipt_sha256) is None:
        raise ActivationError("source receipt SHA-256 is invalid")
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    route = _route()
    route_type = _wire_text(await redis.type(queue._keys.route))
    other_types = [_wire_text(await redis.type(key)) for key in queue._keys.ordered()[1:]]
    owner_schema = await _producer_owner_schema(redis)
    if owner_schema == _ROLLBACK_TOMBSTONE_SCHEMA:
        if _producer_marker_phase(cohort) == "unsafe":
            raise ActivationError("rollback tombstone has an unsafe producer sentinel")
        await _bound_rollback_tombstone(
            redis,
            queue,
            route,
            cohort=cohort,
            source_receipt_sha256=source_receipt_sha256,
        )
        return {"inflight_settled": 0, "expired_requeued": 0, "dead_preserved": 0}
    if owner_schema not in {None, _PRODUCER_OWNER_SCHEMA}:
        raise ActivationError("producer owner authority has an unknown schema")
    if route_type == "none":
        if any(value != "none" for value in other_types):
            raise ActivationError("B0 namespace has state without a route fence")
        _validate_absent_namespace_rollback(
            receipt_state=receipt_state,
            marker_phase=_producer_marker_phase(cohort),
        )
        if owner_schema is not None or _wire_text(await redis.type(_LEGACY_GUARD_KEY)) != "none":
            raise ActivationError("empty rollback retains Redis authority")
        return {"inflight_settled": 0, "expired_requeued": 0, "dead_preserved": 0}
    if owner_schema != _PRODUCER_OWNER_SCHEMA:
        raise ActivationError("B0 namespace lost its producer owner authority")
    _validate_present_namespace_rollback(
        receipt_state=receipt_state, marker_phase=_producer_marker_phase(cohort)
    )

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


async def settle_rollback_namespace(
    redis: Redis, *, cohort: str, receipt_state: str, source_receipt_sha256: str
) -> dict[str, int]:
    try:
        async with asyncio.timeout(_ROLLBACK_SETTLE_HARD_TIMEOUT_SECONDS):
            return await _settle_rollback_namespace(
                redis,
                cohort=cohort,
                receipt_state=receipt_state,
                source_receipt_sha256=source_receipt_sha256,
            )
    except TimeoutError as exc:
        raise ActivationError("B0 rollback settling exceeded its hard timeout") from exc


async def apply_rollback_plan(
    pool: Any,
    redis: Redis,
    *,
    cohort: str,
    receipt_state: str,
    source_receipt_sha256: str,
    expect_digest: str,
) -> dict[str, int | str]:
    plan = await build_rollback_plan(
        pool,
        redis,
        cohort=cohort,
        receipt_state=receipt_state,
        source_receipt_sha256=source_receipt_sha256,
    )
    if plan.digest != expect_digest:
        raise ActivationError("rollback plan digest changed; run a new dry-run")
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    redis_plan = plan.document["redis_plan"]
    assert isinstance(redis_plan, dict)
    restored = dropped = 0
    if plan.document.get("rollback_committed") is not True:
        outcome = await queue.rollback_legacy(
            _route(),
            cohort=cohort,
            rollback_plan_digest=plan.rollback_commit_digest,
            source_receipt_sha256=source_receipt_sha256,
            plan=redis_plan,
        )
        if not outcome.accepted:
            raise ActivationError(
                f"cold rollback failed: {outcome.decision.value}/{outcome.reason}"
            )
        restored = outcome.value or 0
        dropped = outcome.secondary_value or 0
    await _bound_rollback_tombstone(
        redis,
        queue,
        _route(),
        cohort=cohort,
        source_receipt_sha256=source_receipt_sha256,
        expected_plan_digest=plan.rollback_commit_digest,
    )
    await pool.execute(_DELETE_ROUTE_GO_FENCES_SQL, _route().shard_id, _route().routing_epoch)
    remaining = await pool.fetchval(
        _COUNT_ROUTE_GO_FENCES_SQL, _route().shard_id, _route().routing_epoch
    )
    if remaining != 0:
        raise ActivationError("Go PostgreSQL write fences remain after Redis rollback")
    return {
        "ready_restored": restored,
        "terminal_dropped": dropped,
        "write_fences_remaining": 0,
        "digest": plan.digest,
        "rollback_plan_digest": plan.rollback_commit_digest,
    }


async def clear_rollback_tombstone(
    pool: Any,
    redis: Redis,
    *,
    cohort: str,
    rollback_plan_digest: str,
    source_receipt_sha256: str,
    allow_absent: bool,
) -> dict[str, int | str]:
    if settings.lightpanda_b0_producer_mode != "off":
        raise ActivationError("B0 producer must be off for rollback cleanup")
    if _producer_marker_phase(cohort) != "absent":
        raise ActivationError("rollback tombstone cannot clear before the sentinel is absent")
    queue = LightpandaB0Queue(redis, namespace=settings.lightpanda_b0_queue_namespace)
    tombstone = await _bound_rollback_tombstone(
        redis,
        queue,
        _route(),
        cohort=cohort,
        source_receipt_sha256=source_receipt_sha256,
        expected_plan_digest=rollback_plan_digest,
    )
    if tombstone is None and not allow_absent:
        raise ActivationError("rollback tombstone is absent")
    remaining = await pool.fetchval(
        _COUNT_ROUTE_GO_FENCES_SQL, _route().shard_id, _route().routing_epoch
    )
    if remaining != 0:
        raise ActivationError("Go PostgreSQL write fences remain before tombstone cleanup")
    outcome = await queue.clear_rollback_tombstone(
        _route(),
        cohort=cohort,
        rollback_plan_digest=rollback_plan_digest,
        source_receipt_sha256=source_receipt_sha256,
        allow_absent=allow_absent,
    )
    if not outcome.accepted:
        raise ActivationError(
            f"rollback tombstone cleanup failed: {outcome.decision.value}/{outcome.reason}"
        )
    if await _producer_owner_schema(redis) is not None:
        raise ActivationError("rollback tombstone remains after exact cleanup")
    return {"write_fences_remaining": 0, "digest": rollback_plan_digest}


async def _reserve_routing_epoch(pool: Any) -> int:
    try:
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                _LOCK_ROUTING_EPOCH_ALLOCATOR_SQL,
                _ROUTING_EPOCH_ADVISORY_LOCK_ID,
            )
            routing_epoch = await connection.fetchval(_RESERVE_ROUTING_EPOCH_SQL)
    except Exception as exc:
        raise ActivationError("routing epoch reservation failed") from exc
    if (
        isinstance(routing_epoch, bool)
        or not isinstance(routing_epoch, int)
        or not 1 <= routing_epoch <= 9_999_999_999_999
    ):
        raise ActivationError("routing epoch allocator returned an invalid value")
    return routing_epoch


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.command in {"reserve-epoch", "attest-epoch"}:
        pool = await create_local_pool()
        try:
            if args.command == "reserve-epoch":
                return {"routing_epoch": await _reserve_routing_epoch(pool)}
            try:
                expected_epoch = _route().routing_epoch
                current = await pool.fetchrow(_CURRENT_ROUTING_EPOCH_SQL)
            except Exception as exc:
                raise ActivationError("routing epoch high-water attestation failed") from exc
            if current is None:
                raise ActivationError("routing epoch high-water attestation failed")
            last_value = current["last_value"]
            is_called = current["is_called"]
            if (
                isinstance(last_value, bool)
                or not isinstance(last_value, int)
                or not 1 <= last_value <= 9_999_999_999_999
                or type(is_called) is not bool
                or not is_called
                or last_value != expected_epoch
            ):
                raise ActivationError("routing epoch is not the current PostgreSQL high-water")
            return {"routing_epoch": expected_epoch, "current": True}
        finally:
            await close_local_pool()
    redis = get_redis()
    if args.command == "settle-rollback":
        try:
            return {
                "operation": "settle-rollback",
                "cohort": args.cohort,
                **await settle_rollback_namespace(
                    redis,
                    cohort=args.cohort,
                    receipt_state=args.receipt_state,
                    source_receipt_sha256=args.source_receipt_sha256,
                ),
            }
        finally:
            await close_redis()
    pool = await create_local_pool()
    try:
        if args.command == "plan":
            plan = (
                await build_activation_plan(pool, redis, cohort=args.cohort)
                if args.operation == "activate"
                else await build_rollback_plan(
                    pool,
                    redis,
                    cohort=args.cohort,
                    receipt_state=args.receipt_state,
                    source_receipt_sha256=args.source_receipt_sha256,
                )
            )
            result: dict[str, object] = {
                "digest": plan.digest,
                "count": plan.count,
                "plan": plan.document,
            }
            if plan.operation == "rollback":
                result["rollback_plan_digest"] = plan.rollback_commit_digest
            return result
        if args.command == "activate":
            return {
                "operation": "activate",
                "cohort": args.cohort,
                **await apply_activation_plan(
                    pool, redis, cohort=args.cohort, expect_digest=args.expect_digest
                ),
            }
        if args.command == "rollback":
            return {
                "operation": "rollback",
                "cohort": args.cohort,
                **await apply_rollback_plan(
                    pool,
                    redis,
                    cohort=args.cohort,
                    receipt_state=args.receipt_state,
                    source_receipt_sha256=args.source_receipt_sha256,
                    expect_digest=args.expect_digest,
                ),
            }
        return {
            "operation": "clear-rollback-tombstone",
            "cohort": args.cohort,
            **await clear_rollback_tombstone(
                pool,
                redis,
                cohort=args.cohort,
                rollback_plan_digest=args.rollback_plan_digest,
                source_receipt_sha256=args.source_receipt_sha256,
                allow_absent=args.allow_absent,
            ),
        }
    finally:
        await close_redis()
        await close_local_pool()


def main() -> None:
    parser = argparse.ArgumentParser(prog="lightpanda-b0-activation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("reserve-epoch")
    subparsers.add_parser("attest-epoch")
    plan = subparsers.add_parser("plan")
    plan.add_argument("--operation", choices=("activate", "rollback"), required=True)
    plan.add_argument("--cohort", choices=_COHORT_NAMES, required=True)
    plan.add_argument("--receipt-state", choices=("active", "pending"))
    plan.add_argument("--source-receipt-sha256")
    settle = subparsers.add_parser("settle-rollback")
    settle.add_argument("--cohort", choices=_COHORT_NAMES, required=True)
    settle.add_argument("--receipt-state", choices=("active", "pending"), required=True)
    settle.add_argument("--source-receipt-sha256", required=True)
    for command in ("activate", "rollback"):
        apply_parser = subparsers.add_parser(command)
        apply_parser.add_argument("--cohort", choices=_COHORT_NAMES, required=True)
        apply_parser.add_argument("--apply", action="store_true", required=True)
        apply_parser.add_argument("--expect-digest", required=True)
        if command == "rollback":
            apply_parser.add_argument(
                "--receipt-state", choices=("active", "pending"), required=True
            )
            apply_parser.add_argument("--source-receipt-sha256", required=True)
    clear = subparsers.add_parser("clear-rollback-tombstone")
    clear.add_argument("--cohort", choices=_COHORT_NAMES, required=True)
    clear.add_argument("--rollback-plan-digest", required=True)
    clear.add_argument("--source-receipt-sha256", required=True)
    clear.add_argument("--allow-absent", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "plan" and args.operation == "rollback" and args.receipt_state is None:
            parser.error("plan --operation rollback requires --receipt-state")
        if (
            args.command == "plan"
            and args.operation == "rollback"
            and args.source_receipt_sha256 is None
        ):
            parser.error("plan --operation rollback requires --source-receipt-sha256")
        print(json.dumps(asyncio.run(_run(args)), allow_nan=False, sort_keys=True))
    except ActivationError as exc:
        raise SystemExit(f"activation refused: {exc}") from exc


if __name__ == "__main__":
    main()
