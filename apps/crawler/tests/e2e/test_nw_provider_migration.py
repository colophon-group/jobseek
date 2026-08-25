"""PostgreSQL proof for the NW provider identity migration."""

from __future__ import annotations

import importlib
import os
import uuid

import asyncpg
import pytest

from src.nw_provider_cutover import reapply_nw_provider_cutover

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


async def test_nw_migration_preserves_or_deduplicates_identity_without_unique_conflicts() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0021_migrate_nw_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    mappings = migration._NW_IDENTITY_MAPPINGS
    preserved_id = uuid.uuid4()
    duplicate_legacy_id = uuid.uuid4()
    canonical_id = uuid.uuid4()
    stale_id = uuid.uuid4()

    try:
        await connection.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) "
            "VALUES ($1, $2, 'nw-careers', $3)",
            board_id,
            company_id,
            f"https://nw-provider-e2e.invalid/{board_id}",
        )
        await connection.executemany(
            "INSERT INTO job_posting "
            "(id, company_id, board_id, source_url, next_scrape_at) "
            "VALUES ($1, $2, $3, $4, now())",
            [
                (preserved_id, company_id, board_id, mappings[0][0]),
                (duplicate_legacy_id, company_id, board_id, mappings[1][0]),
                (canonical_id, company_id, board_id, mappings[1][1]),
                (
                    stale_id,
                    company_id,
                    board_id,
                    "https://jobs.nw-groupe.com/jobs/6819795-rnw-stage-chef-de-projet-marche-h-f",
                ),
            ],
        )
        before = {
            row["id"]: row["updated_at"]
            for row in await connection.fetch(
                "SELECT id, updated_at FROM job_posting WHERE board_id = $1", board_id
            )
        }

        # If the migration updated before checking the global unique index,
        # this representative post-monitor state would raise UniqueViolation.
        await connection.execute(migration._MIGRATE_NW_PROVIDER_IDENTITIES)

        rows = {
            row["id"]: row
            for row in await connection.fetch(
                "SELECT id, source_url, is_active, next_scrape_at, updated_at "
                "FROM job_posting WHERE board_id = $1",
                board_id,
            )
        }
        assert rows[preserved_id]["source_url"] == mappings[0][1]
        assert rows[preserved_id]["is_active"] is True
        assert rows[preserved_id]["next_scrape_at"] is not None
        assert rows[preserved_id]["updated_at"] > before[preserved_id]

        assert rows[canonical_id]["source_url"] == mappings[1][1]
        assert rows[canonical_id]["is_active"] is True
        assert rows[canonical_id]["updated_at"] == before[canonical_id]
        assert rows[duplicate_legacy_id]["source_url"] == mappings[1][0]
        assert rows[duplicate_legacy_id]["is_active"] is False
        assert rows[duplicate_legacy_id]["next_scrape_at"] is None
        assert rows[duplicate_legacy_id]["updated_at"] > before[duplicate_legacy_id]

        assert rows[stale_id]["is_active"] is False
        assert rows[stale_id]["next_scrape_at"] is None
        assert rows[stale_id]["updated_at"] > before[stale_id]

        first_run = {
            row["id"]: tuple(row.values())
            for row in await connection.fetch(
                "SELECT id, source_url, is_active, next_scrape_at, updated_at "
                "FROM job_posting WHERE board_id = $1",
                board_id,
            )
        }
        await connection.execute(migration._MIGRATE_NW_PROVIDER_IDENTITIES)
        second_run = {
            row["id"]: tuple(row.values())
            for row in await connection.fetch(
                "SELECT id, source_url, is_active, next_scrape_at, updated_at "
                "FROM job_posting WHERE board_id = $1",
                board_id,
            )
        }
        assert second_run == first_run

        # Simulate a failed forward deploy restoring the old Teamtailor
        # runtime: an already-mapped legacy identity is inserted again and
        # existing legacy rows are reactivated. The next current-runtime
        # post-sync hook must clean all of them even though Alembic already
        # recorded revision 0021.
        replayed_legacy_id = uuid.uuid4()
        await connection.execute(
            "INSERT INTO job_posting "
            "(id, company_id, board_id, source_url, next_scrape_at) "
            "VALUES ($1, $2, $3, $4, now())",
            replayed_legacy_id,
            company_id,
            board_id,
            mappings[0][0],
        )
        await connection.execute(
            "UPDATE job_posting "
            "SET is_active = true, next_scrape_at = now(), updated_at = now() "
            "WHERE id = ANY($1::uuid[])",
            [duplicate_legacy_id, stale_id],
        )

        await reapply_nw_provider_cutover(connection)

        replayed = await connection.fetch(
            "SELECT id, is_active, next_scrape_at FROM job_posting "
            "WHERE id = ANY($1::uuid[]) ORDER BY id",
            [replayed_legacy_id, duplicate_legacy_id, stale_id],
        )
        assert len(replayed) == 3
        assert all(row["is_active"] is False for row in replayed)
        assert all(row["next_scrape_at"] is None for row in replayed)
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM job_posting "
                "WHERE board_id = $1 AND is_active "
                "AND source_url LIKE 'https://jobs.nw-groupe.com/jobs/%'",
                board_id,
            )
            == 0
        )
    finally:
        await transaction.rollback()
        await connection.close()


async def test_nw_migration_rejects_foreign_canonical_url_ownership_atomically() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0021_migrate_nw_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    company_id = uuid.uuid4()
    nw_board_id = uuid.uuid4()
    foreign_board_id = uuid.uuid4()
    legacy_id = uuid.uuid4()
    foreign_canonical_id = uuid.uuid4()
    legacy_url, canonical_url = migration._NW_IDENTITY_MAPPINGS[2]

    try:
        await connection.executemany(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            [
                (
                    nw_board_id,
                    company_id,
                    "nw-careers",
                    f"https://nw-provider-e2e.invalid/nw/{nw_board_id}",
                ),
                (
                    foreign_board_id,
                    company_id,
                    f"nw-foreign-{foreign_board_id}",
                    f"https://nw-provider-e2e.invalid/foreign/{foreign_board_id}",
                ),
            ],
        )
        await connection.executemany(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            [
                (legacy_id, company_id, nw_board_id, legacy_url),
                (
                    foreign_canonical_id,
                    company_id,
                    foreign_board_id,
                    canonical_url,
                ),
            ],
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="foreign canonical URL ownership"):
            await connection.execute(migration._MIGRATE_NW_PROVIDER_IDENTITIES)
        await attempt.rollback()

        legacy = await connection.fetchrow(
            "SELECT source_url, is_active FROM job_posting WHERE id = $1", legacy_id
        )
        assert legacy is not None
        assert legacy["source_url"] == legacy_url
        assert legacy["is_active"] is True
    finally:
        await transaction.rollback()
        await connection.close()
