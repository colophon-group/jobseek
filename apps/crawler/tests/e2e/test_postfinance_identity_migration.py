"""PostgreSQL proof for the PostFinance multi-board identity cutover."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.processing.board import (
    _IDENTITY_MIGRATION_MAX_ROWS,
    _POSTFINANCE_CANONICAL_URL_PATTERN,
    _POSTFINANCE_IDENTITY_MIGRATION,
    _POSTFINANCE_IDENTITY_MIGRATION_VERSION,
    _POSTFINANCE_LEGACY_URL_PATTERN,
)
from src.queries.monitor import _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


async def _insert_board(
    connection: asyncpg.Connection,
    *,
    board_id: uuid.UUID,
    company_id: uuid.UUID,
    suffix: str,
) -> None:
    await connection.execute(
        "INSERT INTO job_board "
        "(id, company_id, board_slug, board_url, crawler_type, metadata) "
        "VALUES ($1, $2, $3, $4, 'rss', '{}'::jsonb)",
        board_id,
        company_id,
        f"postfinance-identity-e2e-{suffix}-{board_id}",
        f"https://postfinance-identity-e2e.invalid/{suffix}/{board_id}",
    )


async def _insert_postings(
    connection: asyncpg.Connection,
    *,
    company_id: uuid.UUID,
    board_id: uuid.UUID,
    source_urls: list[str],
    last_seen_at: datetime,
) -> None:
    await connection.executemany(
        "INSERT INTO job_posting "
        "(id, company_id, board_id, source_url, last_seen_at) "
        "VALUES ($1, $2, $3, $4, $5)",
        [
            (uuid.uuid4(), company_id, board_id, source_url, last_seen_at)
            for source_url in source_urls
        ],
    )


async def _run_migration(
    connection: asyncpg.Connection,
    *,
    board_id: uuid.UUID,
    company_id: uuid.UUID,
    monitor_start: datetime,
    canonical_urls: list[str],
) -> asyncpg.Record:
    row = await connection.fetchrow(
        _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES,
        board_id,
        company_id,
        monitor_start,
        _IDENTITY_MIGRATION_MAX_ROWS,
        sorted(canonical_urls),
        _POSTFINANCE_LEGACY_URL_PATTERN,
        _POSTFINANCE_CANONICAL_URL_PATTERN,
        json.dumps(
            {
                "id": _POSTFINANCE_IDENTITY_MIGRATION,
                "version": _POSTFINANCE_IDENTITY_MIGRATION_VERSION,
                "config_fingerprint": "postfinance-identity-e2e",
            }
        ),
        "postfinance",
        True,
    )
    assert row is not None
    return row


async def test_postfinance_cleanup_spans_removed_boards_and_rolls_back_atomically() -> None:
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    outer = connection.transaction()
    await outer.start()
    company_id = uuid.uuid4()
    migration_board_id = uuid.uuid4()
    removed_de_board_id = uuid.uuid4()
    removed_fr_board_id = uuid.uuid4()
    rollback_board_id = uuid.uuid4()
    monitor_start = datetime.now(UTC) - timedelta(minutes=1)
    canonical_urls = [
        "https://jobs.postfinance.ch/job/_/1426471633/",
        "https://jobs.postfinance.ch/job/_/1427053133/",
    ]
    legacy_by_board = {
        migration_board_id: [
            "https://job.post.ch/PostFinance/job/Analyst/74398-de_DE",
        ],
        removed_de_board_id: [
            "https://job.post.ch/default/job/Analyst/74398-de_DE",
            "https://job.post.ch/search?locale=de_DE&shortcut=redirect-jobs",
        ],
        removed_fr_board_id: [
            "https://job.post.ch/default/job/Analyste/74398-fr_FR",
            "https://career.post.ch/de",
        ],
    }

    try:
        await connection.execute(
            "INSERT INTO company (id, slug, name) VALUES ($1, 'postfinance', 'PostFinance E2E')",
            company_id,
        )
        for board_id, suffix in (
            (migration_board_id, "migration"),
            (removed_de_board_id, "removed-de"),
            (removed_fr_board_id, "removed-fr"),
            (rollback_board_id, "rollback"),
        ):
            await _insert_board(
                connection,
                board_id=board_id,
                company_id=company_id,
                suffix=suffix,
            )
        await _insert_postings(
            connection,
            company_id=company_id,
            board_id=migration_board_id,
            source_urls=canonical_urls,
            last_seen_at=monitor_start + timedelta(seconds=1),
        )
        for board_id, urls in legacy_by_board.items():
            await _insert_postings(
                connection,
                company_id=company_id,
                board_id=board_id,
                source_urls=urls,
                last_seen_at=monitor_start - timedelta(days=30),
            )

        result = await _run_migration(
            connection,
            board_id=migration_board_id,
            company_id=company_id,
            monitor_start=monitor_start,
            canonical_urls=canonical_urls,
        )

        assert dict(result) == {
            "active": 7,
            "legacy": 5,
            "canonical": 2,
            "unknown": 0,
            "discovered": 2,
            "validated": 2,
            "retired": 5,
            "receipt_written": True,
            "existing_receipt": None,
        }
        for board_id in legacy_by_board:
            assert await connection.fetchval(
                "SELECT COUNT(*) FROM job_posting WHERE board_id = $1 AND is_active = true",
                board_id,
            ) == (2 if board_id == migration_board_id else 0)

        # A failed cutover transaction restores both the distributed legacy
        # rows and the receipt instead of leaving a half-completed migration.
        rollback_urls = [
            "https://job.post.ch/PostFinance/job/Rollback/74999-de_DE",
            "https://job.post.ch/default/job/Rollback/74999-fr_FR",
        ]
        await _insert_postings(
            connection,
            company_id=company_id,
            board_id=rollback_board_id,
            source_urls=rollback_urls,
            last_seen_at=monitor_start - timedelta(days=30),
        )
        savepoint = connection.transaction()
        await savepoint.start()
        rolled_back = await _run_migration(
            connection,
            board_id=rollback_board_id,
            company_id=company_id,
            monitor_start=monitor_start,
            canonical_urls=canonical_urls,
        )
        assert rolled_back["retired"] == 2
        assert rolled_back["receipt_written"] is True
        await savepoint.rollback()

        assert (
            await connection.fetchval(
                "SELECT COUNT(*) FROM job_posting WHERE board_id = $1 AND is_active = true",
                rollback_board_id,
            )
            == 2
        )
        assert (
            await connection.fetchval(
                "SELECT metadata -> '_identity_migration_receipt' FROM job_board WHERE id = $1",
                rollback_board_id,
            )
            is None
        )
    finally:
        await outer.rollback()
        await connection.close()
