"""One-shot repair of canonical location identity fields in local PostgreSQL.

The historical Supabase-to-local bootstrap copied the location hierarchy but
omitted ``slug``, ``lat``, and ``lng``.  This operation treats the retained web
database as an immutable canonical snapshot, rejects every form of drift, and
fills only missing local values before proving exact equality.

Production invocation is intentionally exposed only through the owner-bound
workflow in ``.github/workflows/repair-location-taxonomy-source.yml``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import asyncpg
import structlog

log = structlog.get_logger()

EXPECTED_LOCATION_ROWS = 37_526
LOCATION_SLUG_CONSTRAINT = "chk_location_slug_nonblank"

_FETCH_LOCATIONS = "SELECT id, slug, lat, lng FROM location ORDER BY id"

_UPDATE_MISSING_FIELDS = """
WITH updated AS (
    UPDATE location AS local
    SET slug = CASE
            WHEN local.slug IS NULL OR btrim(local.slug) = '' THEN source.slug
            ELSE local.slug
        END,
        lat = COALESCE(local.lat, source.lat),
        lng = COALESCE(local.lng, source.lng)
    FROM _location_taxonomy_repair AS source
    WHERE local.id = source.id
      AND (
          ((local.slug IS NULL OR btrim(local.slug) = '') AND source.slug IS NOT NULL)
          OR (local.lat IS NULL AND source.lat IS NOT NULL)
          OR (local.lng IS NULL AND source.lng IS NOT NULL)
      )
    RETURNING local.id
)
SELECT count(*) FROM updated
"""


class LocationTaxonomyRepairError(RuntimeError):
    """The canonical source or local target failed a repair invariant."""


@dataclass(frozen=True, slots=True)
class LocationRow:
    id: int
    slug: str | None
    lat: float | None
    lng: float | None


@dataclass(frozen=True, slots=True)
class LocationTaxonomyRepairSummary:
    expected_rows: int
    source_rows: int
    local_rows: int
    source_coordinate_pairs: int
    missing_slugs_before: int
    missing_coordinate_values_before: int
    updated_rows: int
    source_local_equal: bool
    constraint_validated: bool

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def _rows(records: list[Any]) -> tuple[LocationRow, ...]:
    return tuple(
        LocationRow(
            id=int(record["id"]),
            slug=record["slug"],
            lat=float(record["lat"]) if record["lat"] is not None else None,
            lng=float(record["lng"]) if record["lng"] is not None else None,
        )
        for record in records
    )


def _validate_source(rows: tuple[LocationRow, ...], *, expected_rows: int) -> None:
    if len(rows) != expected_rows:
        raise LocationTaxonomyRepairError(
            f"canonical location row count is {len(rows)}, expected {expected_rows}"
        )

    ids = [row.id for row in rows]
    if len(set(ids)) != len(ids):
        raise LocationTaxonomyRepairError("canonical location IDs are not unique")

    slugs = [row.slug for row in rows]
    blank_slugs = sum(not slug or not slug.strip() for slug in slugs)
    if blank_slugs:
        raise LocationTaxonomyRepairError(
            f"canonical location source has {blank_slugs} blank slugs"
        )
    canonical_slugs = [slug for slug in slugs if slug is not None]
    duplicate_slugs = len(canonical_slugs) - len(set(canonical_slugs))
    if duplicate_slugs:
        raise LocationTaxonomyRepairError(
            f"canonical location source has {duplicate_slugs} duplicate slugs"
        )

    partial_coordinates = sum((row.lat is None) != (row.lng is None) for row in rows)
    if partial_coordinates:
        raise LocationTaxonomyRepairError(
            f"canonical location source has {partial_coordinates} partial coordinate pairs"
        )


def _validate_local_before(
    source_rows: tuple[LocationRow, ...],
    local_rows: tuple[LocationRow, ...],
) -> None:
    source_by_id = {row.id: row for row in source_rows}
    local_by_id = {row.id: row for row in local_rows}
    source_ids = set(source_by_id)
    local_ids = set(local_by_id)
    if source_ids != local_ids:
        raise LocationTaxonomyRepairError(
            "canonical/local location ID sets differ "
            f"(missing_local={len(source_ids - local_ids)}, "
            f"extra_local={len(local_ids - source_ids)})"
        )

    slug_conflicts = 0
    lat_conflicts = 0
    lng_conflicts = 0
    for location_id, source in source_by_id.items():
        local = local_by_id[location_id]
        if local.slug is not None and local.slug.strip() and local.slug != source.slug:
            slug_conflicts += 1
        if local.lat is not None and local.lat != source.lat:
            lat_conflicts += 1
        if local.lng is not None and local.lng != source.lng:
            lng_conflicts += 1

    if slug_conflicts or lat_conflicts or lng_conflicts:
        raise LocationTaxonomyRepairError(
            "populated local location values conflict with the canonical source "
            f"(slug={slug_conflicts}, lat={lat_conflicts}, lng={lng_conflicts})"
        )


def _validate_exact_equality(
    source_rows: tuple[LocationRow, ...],
    local_rows: tuple[LocationRow, ...],
) -> None:
    if source_rows != local_rows:
        source_by_id = {row.id: row for row in source_rows}
        local_by_id = {row.id: row for row in local_rows}
        mismatches = sum(
            source_by_id.get(location_id) != local_by_id.get(location_id)
            for location_id in source_by_id.keys() | local_by_id.keys()
        )
        raise LocationTaxonomyRepairError(
            f"canonical/local location equality proof failed for {mismatches} rows"
        )


async def repair_location_taxonomy_source(
    source_pool: asyncpg.Pool,
    local_pool: asyncpg.Pool,
) -> LocationTaxonomyRepairSummary:
    """Fill missing local canonical fields and prove the target converged.

    The source snapshot stays repeatable-read for the complete operation.  The
    target is locked and updated in one serializable transaction; any failed
    invariant, postflight, or constraint validation rolls back every target
    field change.
    """

    async with (
        source_pool.acquire() as source_conn,
        local_pool.acquire() as local_conn,
        source_conn.transaction(isolation="repeatable_read", readonly=True),
        local_conn.transaction(isolation="serializable"),
    ):
        source_rows = _rows(await source_conn.fetch(_FETCH_LOCATIONS))
        _validate_source(source_rows, expected_rows=EXPECTED_LOCATION_ROWS)

        await local_conn.execute("SET LOCAL statement_timeout = '5min'")
        await local_conn.execute("SET LOCAL lock_timeout = '30s'")
        await local_conn.execute("LOCK TABLE location IN SHARE ROW EXCLUSIVE MODE")

        constraint_exists = await local_conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'location'::regclass
                  AND conname = $1
                  AND contype = 'c'
            )
            """,
            LOCATION_SLUG_CONSTRAINT,
        )
        if not constraint_exists:
            raise LocationTaxonomyRepairError(
                "the durable nonblank location slug constraint is unavailable"
            )

        local_rows_before = _rows(await local_conn.fetch(_FETCH_LOCATIONS))
        _validate_local_before(source_rows, local_rows_before)

        missing_slugs_before = sum(
            row.slug is None or not row.slug.strip() for row in local_rows_before
        )
        missing_coordinate_values_before = sum(row.lat is None for row in local_rows_before) + sum(
            row.lng is None for row in local_rows_before
        )

        await local_conn.execute(
            """
            CREATE TEMP TABLE _location_taxonomy_repair (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                lat REAL,
                lng REAL,
                CHECK ((lat IS NULL) = (lng IS NULL))
            ) ON COMMIT DROP
            """
        )
        await local_conn.copy_records_to_table(
            "_location_taxonomy_repair",
            records=[(row.id, row.slug, row.lat, row.lng) for row in source_rows],
            columns=["id", "slug", "lat", "lng"],
        )
        updated_rows = int(await local_conn.fetchval(_UPDATE_MISSING_FIELDS))

        local_rows_after = _rows(await local_conn.fetch(_FETCH_LOCATIONS))
        _validate_exact_equality(source_rows, local_rows_after)

        await local_conn.execute(
            f"ALTER TABLE location VALIDATE CONSTRAINT {LOCATION_SLUG_CONSTRAINT}"
        )
        constraint_validated = bool(
            await local_conn.fetchval(
                """
                SELECT convalidated
                FROM pg_constraint
                WHERE conrelid = 'location'::regclass AND conname = $1
                """,
                LOCATION_SLUG_CONSTRAINT,
            )
        )
        if not constraint_validated:
            raise LocationTaxonomyRepairError(
                "the durable nonblank location slug constraint was not validated"
            )

    summary = LocationTaxonomyRepairSummary(
        expected_rows=EXPECTED_LOCATION_ROWS,
        source_rows=len(source_rows),
        local_rows=len(local_rows_after),
        source_coordinate_pairs=sum(row.lat is not None for row in source_rows),
        missing_slugs_before=missing_slugs_before,
        missing_coordinate_values_before=missing_coordinate_values_before,
        updated_rows=updated_rows,
        source_local_equal=True,
        constraint_validated=constraint_validated,
    )
    log.info("location_taxonomy_repair.completed", **summary.to_dict())
    return summary
