"""PostgreSQL proof for the reversible ECOM Teamtailor identity cutover."""

from __future__ import annotations

import importlib
import os
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


async def test_ecom_cutover_canonicalizes_aliases_and_restores_them_on_rollback() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    old_alias_id = uuid.uuid4()
    current_alias_id = uuid.uuid4()
    other_id = uuid.uuid4()
    old_alias = "https://careers.ecomtrading.com/jobs/7769137-accounts-and-finance-executive"
    current_alias = "https://ecomtradinggroup.teamtailor.com/jobs/7769137-assistant-finance-manager"
    other_alias = "https://ecomtradinggroup.teamtailor.com/jobs/8000000-trader"

    try:
        await connection.execute(
            "INSERT INTO company (id, slug, name) "
            "VALUES ($1, 'ecom-agroindustrial', 'ECOM Agroindustrial')",
            company_id,
        )
        await connection.execute(
            "INSERT INTO job_board "
            "(id, company_id, board_slug, board_url, crawler_type) "
            "VALUES ($1, $2, 'ecom-agroindustrial-global', $3, 'rss')",
            board_id,
            company_id,
            migration._OLD_BOARD_URL,
        )
        await connection.executemany(
            "INSERT INTO job_posting "
            "(id, company_id, board_id, source_url, is_active, last_seen_at, "
            " next_scrape_at, missing_count) "
            "VALUES ($1, $2, $3, $4, $5, $6, now(), $7)",
            [
                (
                    old_alias_id,
                    company_id,
                    board_id,
                    old_alias,
                    False,
                    datetime(2026, 7, 1, tzinfo=UTC),
                    4,
                ),
                (
                    current_alias_id,
                    company_id,
                    board_id,
                    current_alias,
                    True,
                    datetime(2026, 8, 26, tzinfo=UTC),
                    0,
                ),
                (
                    other_id,
                    company_id,
                    board_id,
                    other_alias,
                    True,
                    datetime(2026, 8, 26, tzinfo=UTC),
                    0,
                ),
            ],
        )

        await connection.execute(migration._MIGRATE_ECOM_TEAMTAILOR_IDENTITIES)

        rows = {
            row["id"]: row
            for row in await connection.fetch(
                "SELECT id, source_url, is_active, missing_count, next_scrape_at "
                "FROM job_posting WHERE board_id = $1",
                board_id,
            )
        }
        assert rows[current_alias_id]["source_url"].endswith("/jobs/7769137")
        assert rows[current_alias_id]["is_active"] is True
        assert rows[old_alias_id]["source_url"] == old_alias
        assert rows[old_alias_id]["is_active"] is False
        assert rows[old_alias_id]["next_scrape_at"] is None
        assert rows[other_id]["source_url"].endswith("/jobs/8000000")
        receipt = await connection.fetchval(
            "SELECT metadata -> '_identity_migration_receipt' FROM job_board WHERE id = $1",
            board_id,
        )
        assert receipt["id"] == migration._MIGRATION_ID
        assert receipt["retired_count"] == 1
        assert len(receipt["rollback_rows"]) == 3

        await connection.execute(migration._ROLLBACK_ECOM_TEAMTAILOR_IDENTITIES)

        restored = {
            row["id"]: row
            for row in await connection.fetch(
                "SELECT id, source_url, is_active, missing_count, next_scrape_at "
                "FROM job_posting WHERE board_id = $1",
                board_id,
            )
        }
        assert restored[old_alias_id]["source_url"] == old_alias
        assert restored[old_alias_id]["is_active"] is False
        assert restored[old_alias_id]["missing_count"] == 4
        assert restored[current_alias_id]["source_url"] == current_alias
        assert restored[current_alias_id]["is_active"] is True
        assert restored[other_id]["source_url"] == other_alias
        assert (
            await connection.fetchval(
                "SELECT metadata -> '_identity_migration_receipt' FROM job_board WHERE id = $1",
                board_id,
            )
            is None
        )
    finally:
        await transaction.rollback()
        await connection.close()


async def test_ecom_cutover_rejects_preexisting_canonical_collision_atomically() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    legacy_id = uuid.uuid4()
    canonical_id = uuid.uuid4()
    legacy_url = "https://ecomtradinggroup.teamtailor.com/jobs/7769137-current-title"
    canonical_url = "https://ecomtradinggroup.teamtailor.com/jobs/7769137"

    try:
        await connection.execute(
            "INSERT INTO company (id, slug, name) "
            "VALUES ($1, 'ecom-agroindustrial', 'ECOM Agroindustrial')",
            company_id,
        )
        await connection.execute(
            "INSERT INTO job_board "
            "(id, company_id, board_slug, board_url, crawler_type) "
            "VALUES ($1, $2, 'ecom-agroindustrial-global', $3, 'rss')",
            board_id,
            company_id,
            migration._BOARD_URL,
        )
        await connection.executemany(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            [
                (legacy_id, company_id, board_id, legacy_url),
                (canonical_id, company_id, board_id, canonical_url),
            ],
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="canonical/legacy row collisions"):
            await connection.execute(migration._MIGRATE_ECOM_TEAMTAILOR_IDENTITIES)
        await attempt.rollback()

        assert (
            await connection.fetchval("SELECT source_url FROM job_posting WHERE id = $1", legacy_id)
            == legacy_url
        )
        assert (
            await connection.fetchval(
                "SELECT metadata -> '_identity_migration_receipt' FROM job_board WHERE id = $1",
                board_id,
            )
            is None
        )
    finally:
        await transaction.rollback()
        await connection.close()
