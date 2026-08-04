"""PostgreSQL integration proof for the canonical location source repair."""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

import src.location_taxonomy_repair as repair

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


async def _pools() -> tuple[asyncpg.Connection, asyncpg.Pool, asyncpg.Pool, str, str]:
    dsn = os.environ["LOCAL_DATABASE_URL"]
    control = await asyncpg.connect(dsn)
    source_schema = f"location_repair_source_{uuid.uuid4().hex}"
    local_schema = f"location_repair_local_{uuid.uuid4().hex}"
    await control.execute(f'CREATE SCHEMA "{source_schema}"')
    await control.execute(f'CREATE SCHEMA "{local_schema}"')
    for schema in (source_schema, local_schema):
        await control.execute(
            f'CREATE TABLE "{schema}".location ('
            "id INTEGER PRIMARY KEY, slug TEXT, lat REAL, lng REAL)"
        )
    source_pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=2,
        server_settings={"search_path": f'"{source_schema}",public'},
    )
    local_pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=2,
        server_settings={"search_path": f'"{local_schema}",public'},
    )
    return control, source_pool, local_pool, source_schema, local_schema


async def _add_not_valid_guard(control: asyncpg.Connection, local_schema: str) -> None:
    await control.execute(
        f'ALTER TABLE "{local_schema}".location '
        "ADD CONSTRAINT chk_location_slug_nonblank "
        "CHECK (slug IS NOT NULL AND btrim(slug) <> '') NOT VALID"
    )


async def _close(
    control: asyncpg.Connection,
    source_pool: asyncpg.Pool,
    local_pool: asyncpg.Pool,
    source_schema: str,
    local_schema: str,
) -> None:
    await source_pool.close()
    await local_pool.close()
    await control.execute(f'DROP SCHEMA IF EXISTS "{source_schema}" CASCADE')
    await control.execute(f'DROP SCHEMA IF EXISTS "{local_schema}" CASCADE')
    await control.close()


async def test_real_transaction_fills_only_missing_values_proves_equality_and_validates_guard(
    monkeypatch,
) -> None:
    control, source_pool, local_pool, source_schema, local_schema = await _pools()
    monkeypatch.setattr(repair, "EXPECTED_LOCATION_ROWS", 3)
    try:
        await control.executemany(
            f'INSERT INTO "{source_schema}".location (id, slug, lat, lng) VALUES ($1, $2, $3, $4)',
            [
                (1, "alpha", 1.25, 2.5),
                (2, "beta", None, None),
                (3, "gamma", -1.0, 4.0),
            ],
        )
        await control.executemany(
            f'INSERT INTO "{local_schema}".location (id, slug, lat, lng) VALUES ($1, $2, $3, $4)',
            [
                (1, " ", None, None),
                (2, "beta", None, None),
                (3, "gamma", -1.0, None),
            ],
        )
        await _add_not_valid_guard(control, local_schema)

        result = await repair.repair_location_taxonomy_source(source_pool, local_pool)
        assert result.source_rows == 3
        assert result.local_rows == 3
        assert result.updated_rows == 2
        assert result.source_local_equal is True
        assert result.constraint_validated is True

        source = await source_pool.fetch("SELECT id, slug, lat, lng FROM location ORDER BY id")
        local = await local_pool.fetch("SELECT id, slug, lat, lng FROM location ORDER BY id")
        assert [tuple(row.values()) for row in local] == [tuple(row.values()) for row in source]
        assert await local_pool.fetchval(
            "SELECT convalidated FROM pg_constraint "
            "WHERE conrelid = 'location'::regclass AND conname = 'chk_location_slug_nonblank'"
        )

        with pytest.raises(asyncpg.CheckViolationError):
            await local_pool.execute(
                "INSERT INTO location (id, slug, lat, lng) VALUES (4, ' ', NULL, NULL)"
            )

        rerun = await repair.repair_location_taxonomy_source(source_pool, local_pool)
        assert rerun.updated_rows == 0
    finally:
        await _close(control, source_pool, local_pool, source_schema, local_schema)


async def test_populated_conflict_rolls_back_every_missing_field(monkeypatch) -> None:
    control, source_pool, local_pool, source_schema, local_schema = await _pools()
    monkeypatch.setattr(repair, "EXPECTED_LOCATION_ROWS", 2)
    try:
        await control.executemany(
            f'INSERT INTO "{source_schema}".location (id, slug, lat, lng) VALUES ($1, $2, $3, $4)',
            [(1, "alpha", 1.0, 2.0), (2, "beta", None, None)],
        )
        await control.executemany(
            f'INSERT INTO "{local_schema}".location (id, slug, lat, lng) VALUES ($1, $2, $3, $4)',
            [(1, "wrong", None, None), (2, None, None, None)],
        )
        await _add_not_valid_guard(control, local_schema)
        before = await local_pool.fetch("SELECT id, slug, lat, lng FROM location ORDER BY id")

        with pytest.raises(repair.LocationTaxonomyRepairError, match="populated local"):
            await repair.repair_location_taxonomy_source(source_pool, local_pool)

        after = await local_pool.fetch("SELECT id, slug, lat, lng FROM location ORDER BY id")
        assert [tuple(row.values()) for row in after] == [tuple(row.values()) for row in before]
        assert not await local_pool.fetchval(
            "SELECT convalidated FROM pg_constraint "
            "WHERE conrelid = 'location'::regclass AND conname = 'chk_location_slug_nonblank'"
        )
    finally:
        await _close(control, source_pool, local_pool, source_schema, local_schema)
