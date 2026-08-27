"""PostgreSQL E2E for durable identity, URL changes, lifecycle, and rollback."""

from __future__ import annotations

import importlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
import structlog

from src.processing.board import SourceIdentityConflictError, _fetch_durable_diff_batch
from src.queries.monitor import (
    _INSERT_URL_ONLY_JOBS_DURABLE,
    _MARK_GONE_BY_TIMESTAMP,
)

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


async def _insert_owner(
    pool: asyncpg.Pool,
    *,
    company_id: uuid.UUID,
    board_id: uuid.UUID,
    suffix: str,
) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO company (id, name, slug) VALUES ($1, $2, $3)",
            company_id,
            f"Durable identity {suffix}",
            f"durable-identity-{suffix}",
        )
        await connection.execute(
            "INSERT INTO job_board (id, company_id, board_slug, board_url) VALUES ($1, $2, $3, $4)",
            board_id,
            company_id,
            f"durable-identity-{suffix}",
            f"https://durable-identity.invalid/{suffix}",
        )


async def test_identity_preserves_uuid_through_locale_url_lifecycle_and_cdc() -> None:
    pool = await asyncpg.create_pool(os.environ["LOCAL_DATABASE_URL"], min_size=1, max_size=3)
    owner_company = uuid.uuid4()
    owner_board = uuid.uuid4()
    foreign_company = uuid.uuid4()
    foreign_board = uuid.uuid4()
    suffix = uuid.uuid4().hex
    foreign_suffix = uuid.uuid4().hex
    identity = f"smartrecruiters:nagarro:{uuid.uuid4().hex}"
    en_url = f"https://jobs.invalid/en/{suffix}/old-title"
    de_url = f"https://jobs.invalid/de/{suffix}/neuer-titel"
    fr_url = f"https://jobs.invalid/fr/{suffix}/nouveau-titre"
    it_url = f"https://jobs.invalid/it/{suffix}/nuovo-titolo"
    legacy_id = uuid.uuid4()
    legacy_url = f"https://jobs.invalid/legacy/{suffix}"
    posting_id: uuid.UUID | None = None

    try:
        await _insert_owner(
            pool,
            company_id=owner_company,
            board_id=owner_board,
            suffix=suffix,
        )
        await _insert_owner(
            pool,
            company_id=foreign_company,
            board_id=foreign_board,
            suffix=foreign_suffix,
        )

        # Compatibility proof: direct/legacy writers omit source_identity and
        # the migration trigger preserves the historical URL-as-identity rule.
        await pool.execute(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            legacy_id,
            owner_company,
            owner_board,
            legacy_url,
        )
        assert (
            await pool.fetchval("SELECT source_identity FROM job_posting WHERE id = $1", legacy_id)
            == legacy_url
        )

        rows = await _fetch_durable_diff_batch(
            pool,
            [(identity, en_url, True)],
            str(owner_company),
            str(owner_board),
            False,
            structlog.get_logger(),
        )
        assert [row["action"] for row in rows] == ["new"]
        inserted = await pool.fetch(
            _INSERT_URL_ONLY_JOBS_DURABLE,
            owner_company,
            owner_board,
            [identity],
            [en_url],
            False,
        )
        assert len(inserted) == 1
        posting_id = inserted[0]["id"]
        before_url_change = await pool.fetchval(
            "SELECT updated_at FROM job_posting WHERE id = $1", posting_id
        )

        # A title-bearing DE publication replaces EN in place and archives the
        # old outbound URL. The CDC trigger advances without an explicit stamp.
        touched = await _fetch_durable_diff_batch(
            pool,
            [(identity, de_url, True)],
            str(owner_company),
            str(owner_board),
            False,
            structlog.get_logger(),
        )
        assert [(row["action"], row["id"]) for row in touched] == [("touched", str(posting_id))]
        changed = await pool.fetchrow(
            "SELECT id, source_identity, source_url, updated_at FROM job_posting WHERE id = $1",
            posting_id,
        )
        assert changed is not None
        assert changed["id"] == posting_id
        assert changed["source_identity"] == identity
        assert changed["source_url"] == de_url
        assert changed["updated_at"] > before_url_change
        assert (
            await pool.fetchval(
                "SELECT posting_id FROM job_posting_source_alias WHERE source_url = $1",
                en_url,
            )
            == posting_id
        )

        # Gone detection remains timestamp-based, then the exact same identity
        # relists under a third locale without replacing its UUID/history.
        await pool.execute(
            "UPDATE job_posting SET last_seen_at = $2 WHERE id = $1",
            posting_id,
            datetime.now(UTC) - timedelta(hours=1),
        )
        gone = await pool.fetch(
            _MARK_GONE_BY_TIMESTAMP,
            owner_board,
            datetime.now(UTC),
            1,
        )
        assert posting_id in {row["id"] for row in gone}
        relisted = await _fetch_durable_diff_batch(
            pool,
            [(identity, fr_url, True)],
            str(owner_company),
            str(owner_board),
            False,
            structlog.get_logger(),
        )
        assert [(row["action"], row["id"]) for row in relisted] == [("relisted", str(posting_id))]
        assert await pool.fetchval("SELECT is_active FROM job_posting WHERE id = $1", posting_id)

        # Removing that preferred publication only changes the link again.
        await _fetch_durable_diff_batch(
            pool,
            [(identity, it_url, True)],
            str(owner_company),
            str(owner_board),
            False,
            structlog.get_logger(),
        )
        final = await pool.fetchrow(
            "SELECT id, source_url FROM job_posting WHERE source_identity = $1",
            identity,
        )
        assert final is not None
        assert final["id"] == posting_id
        assert final["source_url"] == it_url

        # The same explicit identity cannot transfer to a different company.
        with pytest.raises(SourceIdentityConflictError, match="cross_owner_identity"):
            await _fetch_durable_diff_batch(
                pool,
                [(identity, f"https://jobs.invalid/en/{foreign_suffix}", True)],
                str(foreign_company),
                str(foreign_board),
                False,
                structlog.get_logger(),
            )

        # Receipt-backed downgrade refuses to discard exercised identity/alias
        # evidence. The savepoint rolls the expected exception back cleanly.
        migration = importlib.import_module(
            "src.migrations.versions.0023_add_durable_source_identity"
        )
        async with pool.acquire() as connection:
            transaction = connection.transaction()
            await transaction.start()
            try:
                with pytest.raises(asyncpg.RaiseError, match="rollback refused"):
                    await connection.execute(migration._DOWNGRADE_GUARD)
            finally:
                await transaction.rollback()
    finally:
        if posting_id is not None:
            await pool.execute("DELETE FROM job_posting WHERE id = $1", posting_id)
        await pool.execute("DELETE FROM job_posting WHERE id = $1", legacy_id)
        await pool.execute(
            "DELETE FROM job_board WHERE id = ANY($1::uuid[])",
            [owner_board, foreign_board],
        )
        await pool.execute(
            "DELETE FROM company WHERE id = ANY($1::uuid[])",
            [owner_company, foreign_company],
        )
        await pool.close()
