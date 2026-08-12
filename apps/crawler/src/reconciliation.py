"""Deterministic, resumable reconciliation of downstream posting mirrors.

Local PostgreSQL is authoritative. Supabase is a web-facing relational
mirror, while Typesense is a derived search index. This module compares both
directions in bounded UUID partitions and repairs from authoritative local
rereads under the exporter fence. Progress lives in local PostgreSQL, so
container recreation cannot reset or hide the schedule. Supabase support is
retained for direct rollback tests only; the production CLI and scheduler can
select Typesense only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import quote

import asyncpg
import httpx
import structlog

from src.config import settings
from src.export_cursor_fence import export_cursor_fence
from src.exporter import (
    PostingSchema,
    TaxonomyMaps,
    _build_typesense_docs,
    _get_taxonomy_maps,
    _upsert_to_supabase,
    _upsert_to_typesense,
)

log = structlog.get_logger()

ReconciliationTarget = Literal["supabase", "typesense"]
TargetScope = Literal["all", "supabase", "typesense"]

PARTITION_COUNT = 256
DEFAULT_MAX_PARTITIONS = 16
REPAIR_BATCH_SIZE = 500
TYPESENSE_EXPORT_BATCH_SIZE = 1_000
TYPESENSE_DELETE_CONCURRENCY = 20
RECONCILIATION_LOCK_ID = 0x5245434F4E434C  # positive bigint, ASCII-ish ``RECONCL``
_TYPESENSE_EXPORT_MAX_ATTEMPTS = 3
_TYPESENSE_EXPORT_BACKOFF_BASE_SECONDS = 1.0
_TYPESENSE_EXPORT_BACKOFF_CAP_SECONDS = 2.0
_TYPESENSE_EXPORT_RETRYABLE_STATUSES = frozenset((408, 425, 429, 500, 502, 503, 504))
_TYPESENSE_UNBUCKETED_CANDIDATE_LIMIT = 50_000
_TYPESENSE_UNBUCKETED_ID_BYTES_LIMIT = 16 * 1024 * 1024

# A 200 response can still terminate while HTTPX is reading or decoding the
# body, or leave a syntactically incomplete final NDJSON line. Those failures
# retry the complete export with fresh attempt-local state. HTTP status errors
# and decoded document invariant failures are deterministic for this request
# and fail immediately instead of entering the retry loop.
_TYPESENSE_EXPORT_RETRYABLE_ERRORS = (
    httpx.RequestError,
    json.JSONDecodeError,
    UnicodeDecodeError,
)

_PARTITION_STATE_SQL = """
SELECT id, is_active
FROM job_posting
WHERE id >= $1
  AND ($2::uuid IS NULL OR id < $2::uuid)
"""
_TYPESENSE_LOCAL_SELECT_LIST = ", ".join(
    (
        *(f"jp.{name}" for name in PostingSchema.column_names()),
        "jp.last_seen_at",
        "c.name AS company_name",
        "c.slug AS company_slug",
        "c.icon AS company_icon",
    )
)
_PARTITION_TYPESENSE_SQL = (
    f"SELECT {_TYPESENSE_LOCAL_SELECT_LIST} "
    "FROM job_posting jp JOIN company c ON c.id = jp.company_id "
    "WHERE jp.id >= $1 AND ($2::uuid IS NULL OR jp.id < $2::uuid) ORDER BY jp.id"
)
_TYPESENSE_POSTINGS_BY_ID_SQL = (
    f"SELECT {_TYPESENSE_LOCAL_SELECT_LIST} "
    "FROM job_posting jp JOIN company c ON c.id = jp.company_id "
    "WHERE jp.id = ANY($1::uuid[]) ORDER BY jp.id"
)
_AUTHORITATIVE_POSTINGS_SQL = (
    "SELECT "
    + PostingSchema.select_list("last_seen_at")
    + " FROM job_posting WHERE id = ANY($1::uuid[]) ORDER BY id"
)

# These fields form the bounded, user-visible reconciliation contract. The
# reconciler compares their canonical content directly rather than trusting a
# checksum stored beside the document it is meant to verify. Array order is
# significant for location and technology fields because web readers zip IDs,
# names, and types by position. Only intrinsically unordered locale and
# occupation-ancestor arrays are sorted for comparison.
TYPESENSE_RECONCILIATION_PAYLOAD_FIELDS: tuple[str, ...] = (
    "company_id",
    "company_name",
    "company_slug",
    "company_icon",
    "title",
    "has_content",
    "location_ids",
    "location_direct_ids",
    "location_names",
    "location_types",
    "location_geo_types",
    "occupation_id",
    "occupation_ids",
    "occupation_name",
    "seniority_id",
    "seniority_name",
    "technology_ids",
    "technology_names",
    "employment_type",
    "salary_eur",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "experience_min",
    "experience_max",
    "experience_min_years",
    "experience_max_years",
    "locales",
    "first_seen_at",
    "source_url",
)
_ORDER_INSENSITIVE_TYPESENSE_ARRAY_FIELDS = frozenset(("locales", "occupation_ids"))


class ReconciliationError(RuntimeError):
    """A reconciliation invariant or downstream operation failed."""


class ReconciliationRunFailed(ReconciliationError):
    """At least one requested target failed; durable progress was retained."""


@dataclass(frozen=True, slots=True)
class StoreSnapshot:
    states: Mapping[uuid.UUID, bool]
    payload_fingerprints: Mapping[uuid.UUID, str] | None = None

    def __post_init__(self) -> None:
        if self.payload_fingerprints is not None and set(self.payload_fingerprints) != set(
            self.states
        ):
            raise ReconciliationError("Snapshot payload and state IDs differ")

    @property
    def rows(self) -> int:
        return len(self.states)

    @property
    def active(self) -> int:
        return sum(self.states.values())


@dataclass(frozen=True, slots=True)
class PartitionDiff:
    missing_remote: frozenset[uuid.UUID]
    state_mismatch: frozenset[uuid.UUID]
    payload_mismatch: frozenset[uuid.UUID]
    remote_only_active: frozenset[uuid.UUID]
    remote_only_inactive: frozenset[uuid.UUID]

    def actionable_ids(self, target: ReconciliationTarget) -> frozenset[uuid.UUID]:
        remote_only = self.remote_only_active
        if target == "typesense":
            # Typesense has no foreign-key or user-history consumers. Exact
            # document-set parity is safe and avoids orphan search documents.
            remote_only = remote_only | self.remote_only_inactive
        return self.missing_remote | self.state_mismatch | self.payload_mismatch | remote_only

    def detected(self, target: ReconciliationTarget) -> int:
        return len(self.actionable_ids(target))


@dataclass(frozen=True, slots=True)
class PartitionResult:
    target: ReconciliationTarget
    partition: int
    local_rows: int
    local_active: int
    remote_rows: int
    remote_active: int
    missing_remote: int
    state_mismatch: int
    payload_mismatch: int
    remote_only_active: int
    remote_only_inactive: int
    detected: int
    repaired: int
    unresolved: int
    duration_seconds: float


class _PartitionRepairFailed(ReconciliationError):
    """Carry aggregate partition evidence across a sanitized repair failure."""

    def __init__(self, result: PartitionResult, error_class: str) -> None:
        super().__init__(
            f"{result.target} partition repair failed with {result.unresolved} unresolved rows"
        )
        self.result = result
        self.error_class = error_class


@dataclass(slots=True)
class RunSummary:
    run_id: uuid.UUID
    mode: str
    target_scope: TargetScope
    partitions_completed: int = 0
    checked_local: int = 0
    checked_remote: int = 0
    detected: int = 0
    payload_mismatch: int = 0
    repaired: int = 0
    unresolved: int = 0

    def add(self, result: PartitionResult) -> None:
        self.partitions_completed += 1
        self._add_counts(result)

    def add_failed(self, result: PartitionResult) -> None:
        """Record observed work without claiming the partition completed."""

        self._add_counts(result)

    def _add_counts(self, result: PartitionResult) -> None:
        self.checked_local += result.local_rows
        self.checked_remote += result.remote_rows
        self.detected += result.detected
        self.payload_mismatch += result.payload_mismatch
        self.repaired += result.repaired
        self.unresolved += result.unresolved


def partition_bucket(partition: int) -> str:
    if not 0 <= partition < PARTITION_COUNT:
        raise ValueError(f"partition must be in [0, {PARTITION_COUNT})")
    return f"{partition:02x}"


def partition_bounds(partition: int) -> tuple[uuid.UUID, uuid.UUID | None]:
    prefix = partition_bucket(partition)
    lower = uuid.UUID(hex=prefix + "0" * 30)
    upper = (
        None
        if partition == PARTITION_COUNT - 1
        else uuid.UUID(hex=f"{partition + 1:02x}" + "0" * 30)
    )
    return lower, upper


def reconciliation_bucket(posting_id: uuid.UUID | str) -> str:
    parsed = posting_id if isinstance(posting_id, uuid.UUID) else uuid.UUID(str(posting_id))
    return parsed.hex[:2]


def _canonical_payload_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_payload_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_canonical_payload_value(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        # Typesense may serialize an integral float as either ``1`` or ``1.0``.
        # The schema value is equivalent, so do not create false payload drift.
        return int(value)
    return value


def _typesense_payload_fingerprint(document: Mapping[str, object]) -> str:
    payload: dict[str, object] = {}
    for field in TYPESENSE_RECONCILIATION_PAYLOAD_FIELDS:
        value = _canonical_payload_value(document.get(field))
        if field in _ORDER_INSENSITIVE_TYPESENSE_ARRAY_FIELDS and isinstance(value, list):
            value = sorted(
                value,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        payload[field] = value
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _typesense_documents_snapshot(
    documents: Iterable[Mapping[str, object]],
) -> StoreSnapshot:
    states: dict[uuid.UUID, bool] = {}
    payload_fingerprints: dict[uuid.UUID, str] = {}
    for document in documents:
        try:
            posting_id = uuid.UUID(str(document.get("id", "")))
        except ValueError as exc:
            raise ReconciliationError("Typesense document has an invalid ID") from exc
        active = document.get("is_active")
        if not isinstance(active, bool):
            raise ReconciliationError("Typesense document has invalid active state")
        if posting_id in states:
            raise ReconciliationError("Typesense partition returned a duplicate document")
        states[posting_id] = active
        payload_fingerprints[posting_id] = _typesense_payload_fingerprint(document)
    return StoreSnapshot(states, payload_fingerprints)


def _snapshot_subset(
    snapshot: StoreSnapshot,
    posting_ids: frozenset[uuid.UUID],
) -> StoreSnapshot:
    """Project a store snapshot onto one frozen repair candidate set."""

    states = {
        posting_id: active
        for posting_id, active in snapshot.states.items()
        if posting_id in posting_ids
    }
    payload_fingerprints = snapshot.payload_fingerprints
    if payload_fingerprints is None:
        return StoreSnapshot(states)
    return StoreSnapshot(
        states,
        {
            posting_id: fingerprint
            for posting_id, fingerprint in payload_fingerprints.items()
            if posting_id in posting_ids
        },
    )


def compare_snapshots(local: StoreSnapshot, remote: StoreSnapshot) -> PartitionDiff:
    local_ids = set(local.states)
    remote_ids = set(remote.states)
    shared = local_ids & remote_ids
    remote_only = remote_ids - local_ids
    if (local.payload_fingerprints is None) != (remote.payload_fingerprints is None):
        raise ReconciliationError("Snapshots disagree on payload comparison support")
    payload_mismatch: frozenset[uuid.UUID] = frozenset()
    if local.payload_fingerprints is not None and remote.payload_fingerprints is not None:
        payload_mismatch = frozenset(
            posting_id
            for posting_id in shared
            if local.payload_fingerprints[posting_id] != remote.payload_fingerprints[posting_id]
        )
    return PartitionDiff(
        missing_remote=frozenset(local_ids - remote_ids),
        state_mismatch=frozenset(
            posting_id
            for posting_id in shared
            if local.states[posting_id] != remote.states[posting_id]
        ),
        payload_mismatch=payload_mismatch,
        remote_only_active=frozenset(
            posting_id for posting_id in remote_only if remote.states[posting_id]
        ),
        remote_only_inactive=frozenset(
            posting_id for posting_id in remote_only if not remote.states[posting_id]
        ),
    )


def _chunks[T](values: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


async def _postgres_partition_snapshot(
    pool: asyncpg.Pool,
    partition: int,
) -> StoreSnapshot:
    lower, upper = partition_bounds(partition)
    rows = await pool.fetch(_PARTITION_STATE_SQL, lower, upper)
    states = {row["id"]: bool(row["is_active"]) for row in rows}
    if len(states) != len(rows):
        raise ReconciliationError("PostgreSQL partition returned duplicate posting IDs")
    return StoreSnapshot(states)


async def _postgres_typesense_partition_snapshot(
    pool: asyncpg.Pool,
    partition: int,
    maps: TaxonomyMaps,
) -> StoreSnapshot:
    lower, upper = partition_bounds(partition)
    rows = await pool.fetch(_PARTITION_TYPESENSE_SQL, lower, upper)
    return _typesense_documents_snapshot(_build_typesense_docs(list(rows), maps))


class TypesenseReconciliationClient:
    """Bounded streaming access to the Typesense reconciliation surface."""

    def __init__(self) -> None:
        if not settings.typesense_host or not settings.typesense_operations_key:
            raise ReconciliationError("Typesense reconciliation is not configured")
        self._base_url = (
            f"{settings.typesense_protocol}://{settings.typesense_host}:"
            f"{settings.typesense_port}/collections/job_posting/documents"
        )
        self._client = httpx.AsyncClient(
            headers={"X-TYPESENSE-API-KEY": settings.typesense_operations_key},
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=60.0, pool=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _retry_complete_export[T](
        self,
        consumer: Literal["partition", "unbucketed"],
        attempt_export: Callable[[], Awaitable[T]],
    ) -> T:
        """Retry only transient whole-export acquisition failures.

        ``attempt_export`` must allocate every result container inside the
        callable. Nothing from a failed stream may be returned to comparison,
        repair, deletion, or durable progress code.
        """

        for attempt in range(1, _TYPESENSE_EXPORT_MAX_ATTEMPTS + 1):
            try:
                return await attempt_export()
            except httpx.HTTPStatusError as exc:
                error_class = type(exc).__name__
                if exc.response.status_code not in _TYPESENSE_EXPORT_RETRYABLE_STATUSES:
                    # Deterministic status failures must be translated before
                    # the outer logger can expose the configured address, URL,
                    # or response body carried by HTTPStatusError.
                    log.error(
                        "reconciliation.typesense_export_failed",
                        consumer=consumer,
                        attempts=attempt,
                        error_class=error_class,
                    )
                    raise ReconciliationError(
                        f"Typesense export acquisition failed ({error_class})"
                    ) from None
                await self._wait_for_export_retry(consumer, attempt, error_class)
            except httpx.InvalidURL as exc:
                error_class = type(exc).__name__
                log.error(
                    "reconciliation.typesense_export_failed",
                    consumer=consumer,
                    attempts=attempt,
                    error_class=error_class,
                )
                raise ReconciliationError(
                    f"Typesense export acquisition failed ({error_class})"
                ) from None
            except _TYPESENSE_EXPORT_RETRYABLE_ERRORS as exc:
                error_class = type(exc).__name__
                await self._wait_for_export_retry(consumer, attempt, error_class)

        raise AssertionError("Typesense export retry loop exhausted without a result")

    async def _wait_for_export_retry(
        self,
        consumer: Literal["partition", "unbucketed"],
        attempt: int,
        error_class: str,
    ) -> None:
        if attempt >= _TYPESENSE_EXPORT_MAX_ATTEMPTS:
            log.error(
                "reconciliation.typesense_export_retry_exhausted",
                consumer=consumer,
                attempts=attempt,
                error_class=error_class,
            )
            raise ReconciliationError(
                f"Typesense export acquisition failed after {attempt} attempts ({error_class})"
            ) from None
        retry_in = min(
            _TYPESENSE_EXPORT_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
            _TYPESENSE_EXPORT_BACKOFF_CAP_SECONDS,
        )
        log.warning(
            "reconciliation.typesense_export_retry",
            consumer=consumer,
            attempt=attempt,
            max_attempts=_TYPESENSE_EXPORT_MAX_ATTEMPTS,
            retry_in_seconds=retry_in,
            error_class=error_class,
        )
        await asyncio.sleep(retry_in)

    async def partition_snapshot(self, partition: int) -> StoreSnapshot:
        bucket = partition_bucket(partition)

        async def attempt_export() -> StoreSnapshot:
            states: dict[uuid.UUID, bool] = {}
            payload_fingerprints: dict[uuid.UUID, str] = {}
            async with self._client.stream(
                "GET",
                f"{self._base_url}/export",
                params={
                    "filter_by": f"reconciliation_bucket:={bucket}",
                    "include_fields": ",".join(
                        (
                            "id",
                            "is_active",
                            "reconciliation_bucket",
                            *TYPESENSE_RECONCILIATION_PAYLOAD_FIELDS,
                        )
                    ),
                    "batch_size": str(TYPESENSE_EXPORT_BATCH_SIZE),
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    document = json.loads(line)
                    if not isinstance(document, dict):
                        raise ReconciliationError("Typesense export returned a non-object document")
                    if document.get("reconciliation_bucket") != bucket:
                        raise ReconciliationError("Typesense document is in the wrong partition")
                    try:
                        posting_id = uuid.UUID(str(document.get("id", "")))
                    except ValueError:
                        raise ReconciliationError("Typesense document has an invalid ID") from None
                    active = document.get("is_active")
                    if not isinstance(active, bool):
                        raise ReconciliationError("Typesense document has invalid active state")
                    if posting_id in states:
                        raise ReconciliationError(
                            "Typesense partition returned a duplicate document"
                        )
                    states[posting_id] = active
                    payload_fingerprints[posting_id] = _typesense_payload_fingerprint(document)
            return StoreSnapshot(states, payload_fingerprints)

        return await self._retry_complete_export("partition", attempt_export)

    async def unbucketed_batches(
        self,
        *,
        batch_size: int = REPAIR_BATCH_SIZE,
    ) -> AsyncIterator[list[tuple[str, bool]]]:
        async def attempt_export() -> list[tuple[str, bool]]:
            # Only unbucketed candidates are retained, but none are exposed to
            # the deletion consumer until the complete stream reaches EOF.
            candidates: list[tuple[str, bool]] = []
            candidate_id_bytes = 0
            async with self._client.stream(
                "GET",
                f"{self._base_url}/export",
                params={
                    "include_fields": "id,is_active,reconciliation_bucket",
                    "batch_size": str(TYPESENSE_EXPORT_BATCH_SIZE),
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    document = json.loads(line)
                    if not isinstance(document, dict):
                        raise ReconciliationError("Typesense export returned a non-object document")
                    raw_id = str(document.get("id", ""))
                    active = document.get("is_active")
                    if not isinstance(active, bool):
                        raise ReconciliationError("Typesense document has invalid active state")
                    try:
                        expected = reconciliation_bucket(raw_id)
                    except ValueError:
                        expected = ""
                    if document.get("reconciliation_bucket") == expected and expected:
                        continue
                    candidate_id_bytes += len(raw_id.encode("utf-8"))
                    if (
                        len(candidates) >= _TYPESENSE_UNBUCKETED_CANDIDATE_LIMIT
                        or candidate_id_bytes > _TYPESENSE_UNBUCKETED_ID_BYTES_LIMIT
                    ):
                        raise ReconciliationError(
                            "Typesense unbucketed export exceeded its candidate safety limit"
                        )
                    candidates.append((raw_id, active))
            return candidates

        candidates = await self._retry_complete_export("unbucketed", attempt_export)
        for batch in _chunks(candidates, batch_size):
            yield list(batch)

    async def delete_ids(self, posting_ids: Sequence[str]) -> None:
        semaphore = asyncio.Semaphore(TYPESENSE_DELETE_CONCURRENCY)

        async def delete_one(posting_id: str) -> None:
            async with semaphore:
                encoded_id = quote(posting_id, safe="")
                response = await self._client.delete(f"{self._base_url}/{encoded_id}")
                if response.status_code == 404:
                    return
                response.raise_for_status()

        tasks = [asyncio.create_task(delete_one(posting_id)) for posting_id in posting_ids]
        if not tasks:
            return
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            # Cancellation must not let a sibling request outlive the caller
            # (and, for repair, the exporter fence that contains it).
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        failed = sum(isinstance(result, BaseException) for result in results)
        if failed:
            # HTTPX exceptions include request URLs and can include response
            # details. Do not chain any individual exception into the outer
            # reconciliation logger, where a document UUID or payload could
            # become telemetry.
            raise ReconciliationError(
                f"Typesense delete failed for {failed} reconciliation documents"
            ) from None


async def _authoritative_local_rows(
    pool: asyncpg.Pool,
    posting_ids: Sequence[uuid.UUID],
) -> list[asyncpg.Record]:
    if not posting_ids:
        return []
    return list(await pool.fetch(_AUTHORITATIVE_POSTINGS_SQL, list(posting_ids)))


async def _deactivate_supabase_ids(
    supa_pool: asyncpg.Pool,
    posting_ids: Sequence[uuid.UUID],
) -> None:
    if not posting_ids:
        return
    await supa_pool.execute(
        "UPDATE job_posting SET is_active = false WHERE id = ANY($1::uuid[]) AND is_active",
        list(posting_ids),
    )


async def _repair_supabase_partition(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    candidate_ids: frozenset[uuid.UUID],
    partition: int,
) -> tuple[int, int]:
    if not candidate_ids:
        return 0, 0
    ordered_ids = sorted(candidate_ids)

    # Keep the exporter cursor pinned across the direct repair, but never keep
    # a PostgreSQL transaction open across downstream network I/O. Workers may
    # still update a candidate concurrently; the authoritative post-write read
    # below detects that race and fails the partition closed. The worker's
    # updated_at remains above the pinned cursor and is exported after release.
    async with export_cursor_fence(local_pool):
        rows = await _authoritative_local_rows(local_pool, ordered_ids)
        row_ids = {row["id"] for row in rows}
        absent = set(ordered_ids) - row_ids
        for batch in _chunks(rows, REPAIR_BATCH_SIZE):
            failed = await _upsert_to_supabase(supa_pool, list(batch))
            if failed:
                raise ReconciliationError(f"Supabase rejected {len(failed)} reconciliation rows")
        for batch in _chunks(sorted(absent), REPAIR_BATCH_SIZE):
            await _deactivate_supabase_ids(supa_pool, batch)

        current_rows = await _authoritative_local_rows(local_pool, ordered_ids)
        current_ids = {row["id"] for row in current_rows}
        current = {row["id"]: bool(row["is_active"]) for row in current_rows}
        currently_absent = set(ordered_ids) - current_ids
        verified = await _postgres_partition_snapshot(supa_pool, partition)
        unresolved = sum(
            1 for posting_id, active in current.items() if verified.states.get(posting_id) != active
        )
        unresolved += sum(
            1 for posting_id in currently_absent if verified.states.get(posting_id) is True
        )
        if unresolved:
            raise ReconciliationError(
                f"Supabase reconciliation verification left {unresolved} unresolved rows"
            )
    return len(candidate_ids), 0


async def _repair_typesense_partition(
    local_pool: asyncpg.Pool,
    typesense: TypesenseReconciliationClient,
    maps: TaxonomyMaps,
    candidate_ids: frozenset[uuid.UUID],
    partition: int,
) -> tuple[int, int]:
    if not candidate_ids:
        return 0, 0
    ordered_ids = sorted(candidate_ids)
    # The exporter fence serializes this direct write with CDC cursor
    # advancement. Do not hold a PostgreSQL transaction across Typesense I/O:
    # freeze the current candidate rows, write that exact snapshot, then verify
    # only those candidates (including expected absences) and prove their local
    # payload stayed stable before durable progress can advance. Unrelated
    # source changes in the same partition cannot masquerade as a failed repair.
    async with export_cursor_fence(local_pool):
        rows = list(await local_pool.fetch(_TYPESENSE_POSTINGS_BY_ID_SQL, ordered_ids))
        row_ids = {row["id"] for row in rows}
        absent = set(ordered_ids) - row_ids
        documents = _build_typesense_docs(list(rows), maps)
        expected = _typesense_documents_snapshot(documents)
        rejected_ids: set[str] = set()
        for batch in _chunks(documents, REPAIR_BATCH_SIZE):
            rejected_ids.update(
                await _upsert_to_typesense(
                    list(batch),
                    log_rejected_documents=False,
                )
            )
        for batch in _chunks(sorted(absent), REPAIR_BATCH_SIZE):
            await typesense.delete_ids([str(posting_id) for posting_id in batch])

        verified_partition = await typesense.partition_snapshot(partition)
        verified = _snapshot_subset(verified_partition, candidate_ids)
        verification_unresolved = compare_snapshots(expected, verified).actionable_ids("typesense")
        current_rows = list(await local_pool.fetch(_TYPESENSE_POSTINGS_BY_ID_SQL, ordered_ids))
        current = _typesense_documents_snapshot(_build_typesense_docs(current_rows, maps))
        source_changed = compare_snapshots(expected, current).actionable_ids("typesense")
        unresolved_ids = {str(posting_id) for posting_id in verification_unresolved}
        unresolved_ids.update(str(posting_id) for posting_id in source_changed)
        unresolved_ids.update(rejected_ids)
        unresolved = len(unresolved_ids)
        if rejected_ids:
            log.error(
                "reconciliation.typesense_candidates_rejected",
                rejected=len(rejected_ids),
                unresolved=unresolved,
            )
        if source_changed:
            log.error(
                "reconciliation.typesense_candidates_changed_during_repair",
                changed=len(source_changed),
                unresolved=unresolved,
            )
        if verification_unresolved:
            log.error(
                "reconciliation.typesense_candidate_verification_failed",
                verification_unresolved=len(verification_unresolved),
                unresolved=unresolved,
            )
    return len(candidate_ids) - unresolved, unresolved


async def _bootstrap_typesense_buckets(
    local_pool: asyncpg.Pool,
    typesense: TypesenseReconciliationClient,
) -> tuple[int, int, int]:
    """Remove legacy documents that still lack a valid UUID bucket.

    Every authoritative local document was strictly upserted by the preceding
    256 partition cycle. A still-unbucketed valid local ID therefore means the
    cycle invariant was violated or a concurrent legacy writer exists; fail
    closed. IDs absent from local truth are safe to remove from the derived
    search index. A second complete stream verifies convergence before the
    bootstrap flag can advance.
    """

    remote_only_active = 0
    remote_only_inactive = 0
    deleted = 0
    async for batch in typesense.unbucketed_batches():
        valid_ids: list[uuid.UUID] = []
        raw_by_uuid: dict[uuid.UUID, str] = {}
        invalid_ids: list[str] = []
        state_by_raw: dict[str, bool] = {}
        for raw_id, active in batch:
            state_by_raw[raw_id] = active
            try:
                posting_id = uuid.UUID(raw_id)
            except ValueError:
                invalid_ids.append(raw_id)
                continue
            valid_ids.append(posting_id)
            raw_by_uuid[posting_id] = raw_id

        local_ids: set[uuid.UUID] = set()
        if valid_ids:
            rows = await local_pool.fetch(
                "SELECT id FROM job_posting WHERE id = ANY($1::uuid[])",
                valid_ids,
            )
            local_ids = {row["id"] for row in rows}
        if local_ids:
            raise ReconciliationError(
                f"Typesense bootstrap found {len(local_ids)} local documents without buckets"
            )

        delete_ids = invalid_ids + [raw_by_uuid[posting_id] for posting_id in valid_ids]
        remote_only_active += sum(1 for raw_id in delete_ids if state_by_raw[raw_id])
        remote_only_inactive += sum(1 for raw_id in delete_ids if not state_by_raw[raw_id])
        await typesense.delete_ids(delete_ids)
        deleted += len(delete_ids)

    remaining = 0
    async for batch in typesense.unbucketed_batches():
        remaining += len(batch)
        if remaining:
            break
    if remaining:
        raise ReconciliationError("Typesense bootstrap verification found unbucketed documents")
    return remote_only_active, remote_only_inactive, deleted


async def reconcile_partition(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool | None,
    *,
    target: ReconciliationTarget,
    partition: int,
    repair: bool,
    typesense: TypesenseReconciliationClient | None = None,
    maps: TaxonomyMaps | None = None,
) -> PartitionResult:
    started = time.monotonic()
    if target == "supabase":
        if supa_pool is None:
            raise ReconciliationError("Supabase reconciliation requires DATABASE_URL")
        local = await _postgres_partition_snapshot(local_pool, partition)
        remote = await _postgres_partition_snapshot(supa_pool, partition)
    else:
        if typesense is None or maps is None:
            raise ReconciliationError("Typesense reconciliation dependencies are required")
        local = await _postgres_typesense_partition_snapshot(local_pool, partition, maps)
        remote = await typesense.partition_snapshot(partition)
    diff = compare_snapshots(local, remote)
    detected = diff.detected(target)
    repaired = 0
    unresolved = detected

    if repair and detected:
        try:
            if target == "supabase":
                if supa_pool is None:
                    raise ReconciliationError("Supabase reconciliation requires DATABASE_URL")
                repaired, unresolved = await _repair_supabase_partition(
                    local_pool,
                    supa_pool,
                    diff.actionable_ids(target),
                    partition,
                )
            else:
                if typesense is None or maps is None:
                    raise ReconciliationError("Typesense repair dependencies are required")
                repaired, unresolved = await _repair_typesense_partition(
                    local_pool,
                    typesense,
                    maps,
                    diff.actionable_ids(target),
                    partition,
                )
        except Exception as exc:
            # The initial comparison established the exact candidate count.
            # A transport/protocol exception leaves the whole candidate set
            # conservatively unresolved because convergence was not proven.
            result = PartitionResult(
                target=target,
                partition=partition,
                local_rows=local.rows,
                local_active=local.active,
                remote_rows=remote.rows,
                remote_active=remote.active,
                missing_remote=len(diff.missing_remote),
                state_mismatch=len(diff.state_mismatch),
                payload_mismatch=len(diff.payload_mismatch),
                remote_only_active=len(diff.remote_only_active),
                remote_only_inactive=len(diff.remote_only_inactive),
                detected=detected,
                repaired=0,
                unresolved=detected,
                duration_seconds=time.monotonic() - started,
            )
            raise _PartitionRepairFailed(result, type(exc).__name__) from None

    result = PartitionResult(
        target=target,
        partition=partition,
        local_rows=local.rows,
        local_active=local.active,
        remote_rows=remote.rows,
        remote_active=remote.active,
        missing_remote=len(diff.missing_remote),
        state_mismatch=len(diff.state_mismatch),
        payload_mismatch=len(diff.payload_mismatch),
        remote_only_active=len(diff.remote_only_active),
        remote_only_inactive=len(diff.remote_only_inactive),
        detected=detected,
        repaired=repaired,
        unresolved=unresolved,
        duration_seconds=time.monotonic() - started,
    )
    log.info(
        "reconciliation.partition",
        target=target,
        partition=partition_bucket(partition),
        repair=repair,
        local_rows=result.local_rows,
        local_active=result.local_active,
        remote_rows=result.remote_rows,
        remote_active=result.remote_active,
        missing_remote=result.missing_remote,
        state_mismatch=result.state_mismatch,
        payload_mismatch=result.payload_mismatch,
        remote_only_active=result.remote_only_active,
        remote_only_inactive=result.remote_only_inactive,
        repaired=result.repaired,
        unresolved=result.unresolved,
        duration_s=round(result.duration_seconds, 3),
    )
    return result


async def _start_run(
    local_pool: asyncpg.Pool,
    summary: RunSummary,
) -> None:
    # The caller holds the global reconciliation advisory lock. No prior
    # reconciliation process can still be live, so every older running ledger
    # row is an orphan left by a process/container interruption.
    await local_pool.execute(
        "UPDATE cross_store_reconciliation_run "
        "SET status = 'interrupted', completed_at = clock_timestamp(), "
        "error_class = 'InterruptedRun' "
        "WHERE status = 'running'"
    )
    await local_pool.execute(
        "DELETE FROM cross_store_reconciliation_run "
        "WHERE completed_at < clock_timestamp() - interval '180 days'"
    )
    await local_pool.execute(
        "INSERT INTO cross_store_reconciliation_run "
        "(run_id, mode, target_scope) VALUES ($1, $2, $3)",
        summary.run_id,
        summary.mode,
        summary.target_scope,
    )


async def _persist_run_progress(local_pool: asyncpg.Pool, summary: RunSummary) -> None:
    await local_pool.execute(
        "UPDATE cross_store_reconciliation_run SET "
        "partitions_completed = $2, checked_local = $3, checked_remote = $4, "
        "detected = $5, payload_mismatch = $6, repaired = $7, unresolved = $8 "
        "WHERE run_id = $1",
        summary.run_id,
        summary.partitions_completed,
        summary.checked_local,
        summary.checked_remote,
        summary.detected,
        summary.payload_mismatch,
        summary.repaired,
        summary.unresolved,
    )


async def _finish_run(
    local_pool: asyncpg.Pool,
    summary: RunSummary,
    *,
    status: Literal["success", "failed", "interrupted"],
    error_class: str | None = None,
) -> None:
    await _persist_run_progress(local_pool, summary)
    await local_pool.execute(
        "UPDATE cross_store_reconciliation_run SET completed_at = clock_timestamp(), "
        "status = $2, error_class = $3 WHERE run_id = $1",
        summary.run_id,
        status,
        error_class,
    )


async def _ensure_cycle(
    local_pool: asyncpg.Pool,
    target: ReconciliationTarget,
    *,
    fresh: bool = False,
) -> int:
    async with local_pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT * FROM cross_store_reconciliation_state WHERE target = $1 FOR UPDATE",
            target,
        )
        if row is None:
            raise ReconciliationError(f"Missing reconciliation state for {target}")
        if fresh or row["cycle_id"] is None:
            await conn.execute(
                "UPDATE cross_store_reconciliation_state SET "
                "next_partition = 0, cycle_id = $2, cycle_started_at = clock_timestamp(), "
                "cycle_runtime_seconds = 0, cycle_local_rows = 0, "
                "cycle_local_active = 0, cycle_remote_rows = 0, "
                "cycle_remote_active = 0, cycle_detected = 0, cycle_missing_remote = 0, "
                "cycle_state_mismatch = 0, cycle_payload_mismatch = 0, "
                "cycle_remote_only_active = 0, "
                "cycle_remote_only_inactive = 0, cycle_repaired = 0, "
                "last_attempt_at = clock_timestamp(), last_outcome = 'progress', "
                "last_error_class = NULL, last_unresolved = 0, "
                "updated_at = clock_timestamp() WHERE target = $1",
                target,
                uuid.uuid4(),
            )
            if fresh:
                log.info(
                    "reconciliation.cycle_restarted",
                    target=target,
                    previous_partition=int(row["next_partition"]),
                    previous_cycle_id=str(row["cycle_id"]) if row["cycle_id"] else None,
                )
        else:
            await conn.execute(
                "UPDATE cross_store_reconciliation_state SET "
                "last_attempt_at = clock_timestamp(), last_outcome = 'progress', "
                "last_error_class = NULL, last_unresolved = 0, "
                "updated_at = clock_timestamp() WHERE target = $1",
                target,
            )
        return 0 if fresh else cast(int, row["next_partition"])


async def _advance_state(
    local_pool: asyncpg.Pool,
    result: PartitionResult,
    *,
    bootstrap_complete: bool | None = None,
) -> bool:
    async with local_pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT * FROM cross_store_reconciliation_state WHERE target = $1 FOR UPDATE",
            result.target,
        )
        if row is None or row["cycle_id"] is None:
            raise ReconciliationError("Reconciliation cycle state disappeared")
        if row["next_partition"] != result.partition:
            raise ReconciliationError("Reconciliation partition cursor changed concurrently")

        totals = {
            "runtime": float(row["cycle_runtime_seconds"]) + result.duration_seconds,
            "local_rows": int(row["cycle_local_rows"]) + result.local_rows,
            "local_active": int(row["cycle_local_active"]) + result.local_active,
            "remote_rows": int(row["cycle_remote_rows"]) + result.remote_rows,
            "remote_active": int(row["cycle_remote_active"]) + result.remote_active,
            "detected": int(row["cycle_detected"]) + result.detected,
            "missing_remote": int(row["cycle_missing_remote"]) + result.missing_remote,
            "state_mismatch": int(row["cycle_state_mismatch"]) + result.state_mismatch,
            "payload_mismatch": int(row["cycle_payload_mismatch"]) + result.payload_mismatch,
            "remote_only_active": int(row["cycle_remote_only_active"]) + result.remote_only_active,
            "remote_only_inactive": int(row["cycle_remote_only_inactive"])
            + result.remote_only_inactive,
            "repaired": int(row["cycle_repaired"]) + result.repaired,
        }
        bootstrap = (
            bool(row["bootstrap_complete"]) if bootstrap_complete is None else bootstrap_complete
        )
        completed = result.partition == PARTITION_COUNT - 1
        if completed:
            outcome = "repaired" if totals["repaired"] else "clean"
            await conn.execute(
                "UPDATE cross_store_reconciliation_state SET "
                "next_partition = 0, bootstrap_complete = $2, cycle_id = NULL, "
                "cycle_started_at = NULL, cycle_runtime_seconds = 0, "
                "cycle_local_rows = 0, cycle_local_active = 0, "
                "cycle_remote_rows = 0, cycle_remote_active = 0, "
                "cycle_detected = 0, cycle_missing_remote = 0, cycle_state_mismatch = 0, "
                "cycle_payload_mismatch = 0, cycle_remote_only_active = 0, "
                "cycle_remote_only_inactive = 0, "
                "cycle_repaired = 0, last_started_at = $3, "
                "last_attempt_at = clock_timestamp(), last_success_at = clock_timestamp(), "
                "last_duration_seconds = $4, last_local_rows = $5, "
                "last_local_active = $6, last_remote_rows = $7, "
                "last_remote_active = $8, last_detected = $9, last_missing_remote = $10, "
                "last_state_mismatch = $11, last_payload_mismatch = $12, "
                "last_remote_only_active = $13, last_remote_only_inactive = $14, "
                "last_repaired = $15, last_unresolved = 0, last_outcome = $16, "
                "last_error_class = NULL, "
                "updated_at = clock_timestamp() WHERE target = $1",
                result.target,
                bootstrap,
                row["cycle_started_at"],
                totals["runtime"],
                totals["local_rows"],
                totals["local_active"],
                totals["remote_rows"],
                totals["remote_active"],
                totals["detected"],
                totals["missing_remote"],
                totals["state_mismatch"],
                totals["payload_mismatch"],
                totals["remote_only_active"],
                totals["remote_only_inactive"],
                totals["repaired"],
                outcome,
            )
        else:
            await conn.execute(
                "UPDATE cross_store_reconciliation_state SET "
                "next_partition = $2, bootstrap_complete = $3, "
                "cycle_runtime_seconds = $4, cycle_local_rows = $5, "
                "cycle_local_active = $6, cycle_remote_rows = $7, "
                "cycle_remote_active = $8, cycle_detected = $9, cycle_missing_remote = $10, "
                "cycle_state_mismatch = $11, cycle_payload_mismatch = $12, "
                "cycle_remote_only_active = $13, cycle_remote_only_inactive = $14, "
                "cycle_repaired = $15, "
                "last_attempt_at = clock_timestamp(), last_outcome = 'progress', "
                "last_unresolved = 0, last_error_class = NULL, "
                "updated_at = clock_timestamp() WHERE target = $1",
                result.target,
                result.partition + 1,
                bootstrap,
                totals["runtime"],
                totals["local_rows"],
                totals["local_active"],
                totals["remote_rows"],
                totals["remote_active"],
                totals["detected"],
                totals["missing_remote"],
                totals["state_mismatch"],
                totals["payload_mismatch"],
                totals["remote_only_active"],
                totals["remote_only_inactive"],
                totals["repaired"],
            )
        return completed


async def _record_target_failure(
    local_pool: asyncpg.Pool,
    target: ReconciliationTarget,
    error_class: str,
    *,
    unresolved: int = 0,
) -> None:
    await local_pool.execute(
        "UPDATE cross_store_reconciliation_state SET "
        "last_attempt_at = clock_timestamp(), last_outcome = 'failed', "
        "last_unresolved = $2, last_error_class = $3, "
        "updated_at = clock_timestamp() WHERE target = $1",
        target,
        unresolved,
        error_class,
    )


async def _state_bootstrap_complete(
    local_pool: asyncpg.Pool,
    target: ReconciliationTarget,
) -> bool:
    value = await local_pool.fetchval(
        "SELECT bootstrap_complete FROM cross_store_reconciliation_state WHERE target = $1",
        target,
    )
    return bool(value)


def _targets(
    scope: TargetScope,
    *,
    relational_mirror_available: bool,
) -> tuple[ReconciliationTarget, ...]:
    if scope == "all":
        return ("supabase", "typesense") if relational_mirror_available else ("typesense",)
    if scope == "supabase" and not relational_mirror_available:
        raise ReconciliationError("Supabase reconciliation requires DATABASE_URL")
    return (cast(ReconciliationTarget, scope),)


async def run_reconciliation(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool | None,
    *,
    repair: bool = False,
    full: bool = False,
    fresh_cycle: bool = False,
    max_partitions: int = DEFAULT_MAX_PARTITIONS,
    start_partition: int = 0,
    target_scope: TargetScope = "all",
) -> RunSummary:
    """Run or resume a bounded bidirectional reconciliation command.

    Repair runs persist a cursor independently for each target. Dry runs never
    move that cursor and start at ``start_partition``. ``fresh_cycle`` replaces
    an in-progress repair cycle at partition zero and is only valid with a full
    repair. A partition advances only after every downstream mutation and its
    verification succeeds.
    """

    if max_partitions < 1 or max_partitions > PARTITION_COUNT:
        raise ValueError(f"max_partitions must be in [1, {PARTITION_COUNT}]")
    if fresh_cycle and not (repair and full):
        raise ValueError("fresh_cycle requires repair=True and full=True")
    partition_bucket(start_partition)
    run_id = uuid.uuid4()
    summary = RunSummary(
        run_id=run_id,
        mode="repair" if repair else "dry-run",
        target_scope=target_scope,
    )

    async with local_pool.acquire() as lock_conn:
        acquired = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock($1::bigint)",
            RECONCILIATION_LOCK_ID,
        )
        if acquired is not True:
            log.info("reconciliation.already_running")
            if fresh_cycle:
                raise ReconciliationError(
                    "Fresh reconciliation proof could not acquire the advisory lock"
                )
            return summary
        try:
            await _start_run(local_pool, summary)
            run_finished = False
            typesense: TypesenseReconciliationClient | None = None
            maps: TaxonomyMaps | None = None
            try:
                failures: list[str] = []
                selected_targets = _targets(
                    target_scope,
                    relational_mirror_available=supa_pool is not None,
                )
                for target in selected_targets:
                    last_result: PartitionResult | None = None
                    last_result_recorded = False
                    try:
                        if target == "typesense" and typesense is None:
                            typesense = TypesenseReconciliationClient()
                        partition = (
                            await _ensure_cycle(local_pool, target, fresh=fresh_cycle)
                            if repair
                            else start_partition
                        )
                        budget = PARTITION_COUNT - partition if full else max_partitions
                        for _ in range(budget):
                            # Failure accounting belongs to this exact attempt;
                            # never reuse a prior partition's successful result.
                            last_result = None
                            last_result_recorded = False
                            if target == "typesense":
                                # Refresh long-running reconciliation proofs on
                                # the same cadence as ordinary CDC so a stale
                                # taxonomy cache cannot rewrite current names.
                                maps = await _get_taxonomy_maps(local_pool)
                            result = await reconcile_partition(
                                local_pool,
                                supa_pool,
                                target=target,
                                partition=partition,
                                repair=repair,
                                typesense=typesense,
                                maps=maps,
                            )
                            last_result = result
                            if repair and result.unresolved:
                                raise ReconciliationError(
                                    f"{target} partition left {result.unresolved} unresolved rows"
                                )

                            bootstrap_complete: bool | None = None
                            if (
                                repair
                                and target == "typesense"
                                and partition == PARTITION_COUNT - 1
                                and not await _state_bootstrap_complete(local_pool, target)
                            ):
                                if typesense is None:
                                    raise ReconciliationError("Typesense client is required")
                                bootstrap_started = time.monotonic()
                                active, inactive, deleted = await _bootstrap_typesense_buckets(
                                    local_pool,
                                    typesense,
                                )
                                result = PartitionResult(
                                    target=result.target,
                                    partition=result.partition,
                                    local_rows=result.local_rows,
                                    local_active=result.local_active,
                                    remote_rows=result.remote_rows + active + inactive,
                                    remote_active=result.remote_active + active,
                                    missing_remote=result.missing_remote,
                                    state_mismatch=result.state_mismatch,
                                    payload_mismatch=result.payload_mismatch,
                                    remote_only_active=result.remote_only_active + active,
                                    remote_only_inactive=result.remote_only_inactive + inactive,
                                    detected=result.detected + deleted,
                                    repaired=result.repaired + deleted,
                                    unresolved=0,
                                    duration_seconds=(
                                        result.duration_seconds
                                        + time.monotonic()
                                        - bootstrap_started
                                    ),
                                )
                                bootstrap_complete = True

                            summary.add(result)
                            last_result_recorded = True
                            if repair:
                                completed = await _advance_state(
                                    local_pool,
                                    result,
                                    bootstrap_complete=bootstrap_complete,
                                )
                            else:
                                completed = partition == PARTITION_COUNT - 1
                            await _persist_run_progress(local_pool, summary)
                            if completed:
                                break
                            partition += 1
                    except Exception as exc:
                        if isinstance(exc, _PartitionRepairFailed):
                            last_result = exc.result
                            error_class = exc.error_class
                        else:
                            error_class = type(exc).__name__
                        if (
                            last_result is not None
                            and last_result.unresolved
                            and not last_result_recorded
                        ):
                            summary.add_failed(last_result)
                            last_result_recorded = True
                        failures.append(error_class)
                        if repair:
                            await _record_target_failure(
                                local_pool,
                                target,
                                error_class,
                                unresolved=last_result.unresolved if last_result else 0,
                            )
                        log.exception(
                            "reconciliation.target_failed",
                            target=target,
                            error_class=error_class,
                        )
                if failures:
                    await _finish_run(
                        local_pool,
                        summary,
                        status="failed",
                        error_class="PartialFailure" if len(failures) > 1 else failures[0],
                    )
                    run_finished = True
                    raise ReconciliationRunFailed(
                        f"Reconciliation failed for {len(failures)} target(s)"
                    )
                if fresh_cycle:
                    expected_partitions = PARTITION_COUNT * len(selected_targets)
                    if summary.partitions_completed != expected_partitions:
                        raise ReconciliationError(
                            "Fresh reconciliation proof did not inspect every partition: "
                            f"completed={summary.partitions_completed}, "
                            f"expected={expected_partitions}"
                        )
                await _finish_run(local_pool, summary, status="success")
                run_finished = True
            except BaseException as exc:
                if not run_finished:
                    try:
                        interrupted = isinstance(exc, asyncio.CancelledError)
                        await asyncio.shield(
                            _finish_run(
                                local_pool,
                                summary,
                                status="interrupted" if interrupted else "failed",
                                error_class="InterruptedRun" if interrupted else type(exc).__name__,
                            )
                        )
                    except BaseException:
                        log.exception("reconciliation.run_failure_persist_failed")
                raise
            finally:
                if typesense is not None:
                    await typesense.aclose()
        finally:
            unlocked = await lock_conn.fetchval(
                "SELECT pg_advisory_unlock($1::bigint)",
                RECONCILIATION_LOCK_ID,
            )
            if unlocked is not True:
                lock_conn.terminate()
                raise ReconciliationError("Reconciliation advisory lock was not held")

    log.info(
        "reconciliation.completed",
        run_id=str(summary.run_id),
        mode=summary.mode,
        target_scope=summary.target_scope,
        partitions=summary.partitions_completed,
        checked_local=summary.checked_local,
        checked_remote=summary.checked_remote,
        detected=summary.detected,
        repaired=summary.repaired,
        unresolved=summary.unresolved,
    )
    return summary
