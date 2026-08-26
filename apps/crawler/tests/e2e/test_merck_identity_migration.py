"""PostgreSQL proof for the receipt-backed Merck identity migration."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.processing.board import (
    _IDENTITY_MIGRATION_MAX_ROWS,
    _MERCK_CANONICAL_URL_PATTERN,
    _MERCK_IDENTITY_MIGRATION,
    _MERCK_IDENTITY_MIGRATION_VERSION,
    _MERCK_LEGACY_URL_PATTERN,
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
        "VALUES ($1, $2, $3, $4, 'sitemap', '{}'::jsonb)",
        board_id,
        company_id,
        f"merck-identity-e2e-{suffix}-{board_id}",
        f"https://merck-identity-e2e.invalid/{suffix}/{board_id}",
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


def _legacy_urls(start: int) -> list[str]:
    return [
        f"https://careers.merckgroup.com/de/de/job/{job_id}/legacy-{job_id}"
        for job_id in range(start, start + 5)
    ]


def _decode_json(value: object) -> dict:
    if isinstance(value, str):
        return json.loads(value)
    assert isinstance(value, dict)
    return value


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
        _MERCK_LEGACY_URL_PATTERN,
        _MERCK_CANONICAL_URL_PATTERN,
        json.dumps(
            {
                "id": _MERCK_IDENTITY_MIGRATION,
                "version": _MERCK_IDENTITY_MIGRATION_VERSION,
                "config_fingerprint": "merck-identity-e2e",
            }
        ),
        "merck",
        False,
    )
    assert row is not None
    return row


async def test_merck_identity_migration_is_atomic_and_receipt_gated() -> None:
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    outer = connection.transaction()
    await outer.start()
    company_id = uuid.uuid4()
    canonical_board_id = uuid.uuid4()
    migration_board_id = uuid.uuid4()
    rollback_board_id = uuid.uuid4()
    monitor_start = datetime.now(UTC) - timedelta(minutes=1)
    canonical_urls = [
        f"https://careers.emdgroup.com/us/en/job/{job_id}" for job_id in range(910_000, 910_003)
    ]

    try:
        await connection.execute(
            "INSERT INTO company (id, slug, name) VALUES ($1, 'merck', 'Merck E2E')",
            company_id,
        )
        await _insert_board(
            connection,
            board_id=canonical_board_id,
            company_id=company_id,
            suffix="canonical",
        )
        await _insert_board(
            connection,
            board_id=migration_board_id,
            company_id=company_id,
            suffix="commit",
        )
        await _insert_board(
            connection,
            board_id=rollback_board_id,
            company_id=company_id,
            suffix="rollback",
        )
        await _insert_postings(
            connection,
            company_id=company_id,
            board_id=canonical_board_id,
            source_urls=canonical_urls,
            last_seen_at=monitor_start + timedelta(seconds=1),
        )

        # Five legacy rows include two IDs with no current canonical twin.
        legacy_urls = _legacy_urls(910_000)
        await _insert_postings(
            connection,
            company_id=company_id,
            board_id=migration_board_id,
            source_urls=legacy_urls,
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
            "active": 5,
            "legacy": 5,
            "canonical": 0,
            "unknown": 0,
            "discovered": 3,
            "validated": 3,
            "retired": 5,
            "receipt_written": True,
            "existing_receipt": None,
        }
        assert (
            await connection.fetchval(
                "SELECT COUNT(*) FROM job_posting WHERE board_id = $1 AND is_active = false",
                migration_board_id,
            )
            == 5
        )
        receipt = _decode_json(
            await connection.fetchval(
                "SELECT metadata -> '_identity_migration_receipt' FROM job_board WHERE id = $1",
                migration_board_id,
            )
        )
        assert receipt["id"] == _MERCK_IDENTITY_MIGRATION
        assert receipt["version"] == _MERCK_IDENTITY_MIGRATION_VERSION
        assert receipt["config_fingerprint"] == "merck-identity-e2e"
        assert receipt["retired_count"] == 5
        assert receipt["completed_at"]

        # A stale Redis replay cannot re-arm the migration. Even a newly
        # inserted strict legacy row remains active once the DB receipt exists.
        replay_url = "https://careers.merckgroup.com/de/de/job/910999/legacy-after-receipt"
        await _insert_postings(
            connection,
            company_id=company_id,
            board_id=migration_board_id,
            source_urls=[replay_url],
            last_seen_at=monitor_start - timedelta(days=30),
        )
        replay = await _run_migration(
            connection,
            board_id=migration_board_id,
            company_id=company_id,
            monitor_start=monitor_start,
            canonical_urls=canonical_urls,
        )
        assert replay["receipt_written"] is False
        assert _decode_json(replay["existing_receipt"])["retired_count"] == 5
        assert (
            await connection.fetchval(
                "SELECT is_active FROM job_posting WHERE source_url = $1",
                replay_url,
            )
            is True
        )

        # The data-changing CTE and receipt participate in one transaction.
        rollback_legacy_urls = _legacy_urls(920_000)
        await _insert_postings(
            connection,
            company_id=company_id,
            board_id=rollback_board_id,
            source_urls=rollback_legacy_urls,
            last_seen_at=monitor_start - timedelta(days=30),
        )
        savepoint = connection.transaction()
        await savepoint.start()
        rolled_back_result = await _run_migration(
            connection,
            board_id=rollback_board_id,
            company_id=company_id,
            monitor_start=monitor_start,
            canonical_urls=canonical_urls,
        )
        assert rolled_back_result["retired"] == 5
        assert rolled_back_result["receipt_written"] is True
        await savepoint.rollback()

        assert (
            await connection.fetchval(
                "SELECT COUNT(*) FROM job_posting WHERE board_id = $1 AND is_active = true",
                rollback_board_id,
            )
            == 5
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
