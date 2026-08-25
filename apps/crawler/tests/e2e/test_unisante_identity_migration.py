"""PostgreSQL proof for Unisanté's in-place identity migration."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.processing.board import (
    _UNISANTE_IDENTITY_MIGRATION,
    _UNISANTE_IDENTITY_MIGRATION_MAX_ROWS,
    _UNISANTE_IDENTITY_MIGRATION_VERSION,
)
from src.queries.monitor import _MIGRATE_UNISANTE_PROVIDER_IDENTITIES

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


def _canonical(reference: str) -> str:
    return f"https://emploi.unisante.ch/index.php/offres?reference={reference}"


def _detail(slug: str) -> str:
    return f"https://emploi.unisante.ch/index.php/offre/{slug}"


async def _run(
    connection: asyncpg.Connection,
    *,
    board_id: uuid.UUID,
    company_id: uuid.UUID,
    references: list[str],
    details: list[str],
) -> asyncpg.Record:
    result = await connection.fetchrow(
        _MIGRATE_UNISANTE_PROVIDER_IDENTITIES,
        board_id,
        company_id,
        [_canonical(reference) for reference in references],
        details,
        _UNISANTE_IDENTITY_MIGRATION_MAX_ROWS,
        json.dumps(
            {
                "id": _UNISANTE_IDENTITY_MIGRATION,
                "version": _UNISANTE_IDENTITY_MIGRATION_VERSION,
            }
        ),
    )
    assert result is not None
    return result


async def test_unisante_migration_preserves_ids_and_handles_existing_canonical() -> None:
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    outer = connection.transaction()
    await outer.start()
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    now = datetime.now(UTC)
    primary_1405_id = uuid.uuid4()
    duplicate_1405_id = uuid.uuid4()
    evergreen_8_id = uuid.uuid4()
    legacy_1393_id = uuid.uuid4()
    canonical_1393_id = uuid.uuid4()
    expired_1215_id = uuid.uuid4()
    details = [
        _detail("1405-assistante-de-direction"),
        _detail("medecin-assistant-cmg"),
        _detail("1393-collaborateur-trice-scientifique-specialise-e-en-recherche-de-financement"),
    ]

    try:
        await connection.execute(
            "INSERT INTO company (id, slug, name) VALUES ($1, 'unisante', 'Unisanté E2E')",
            company_id,
        )
        await connection.execute(
            "INSERT INTO job_board "
            "(id, company_id, board_slug, board_url, crawler_type, metadata) "
            "VALUES ($1, $2, $3, $4, 'unisante', '{}'::jsonb)",
            board_id,
            company_id,
            f"unisante-identity-e2e-{board_id}",
            f"https://unisante-identity-e2e.invalid/{board_id}",
        )
        rows = [
            (
                primary_1405_id,
                details[0],
                now - timedelta(days=3),
                ["Legacy 1405 content"],
            ),
            (
                duplicate_1405_id,
                details[0].replace("/index.php/offre/", "/offre/"),
                now - timedelta(days=2),
                ["Duplicate alias"],
            ),
            (
                evergreen_8_id,
                details[1].replace("/index.php/offre/", "/offre/"),
                now - timedelta(days=3),
                ["Legacy evergreen content"],
            ),
            (
                legacy_1393_id,
                details[2],
                now - timedelta(days=3),
                ["Legacy 1393 content"],
            ),
            (
                canonical_1393_id,
                _canonical("1393"),
                now - timedelta(days=1),
                ["Canonical 1393 content"],
            ),
            (
                expired_1215_id,
                _detail("1215-medecin-pediatre"),
                now - timedelta(days=3),
                ["Expired legacy content"],
            ),
        ]
        await connection.executemany(
            "INSERT INTO job_posting "
            "(id, company_id, board_id, source_url, first_seen_at, last_seen_at, titles) "
            "VALUES ($1, $2, $3, $4, $5, $5, $6)",
            [
                (posting_id, company_id, board_id, source_url, seen_at, titles)
                for posting_id, source_url, seen_at, titles in rows
            ],
        )

        result = await _run(
            connection,
            board_id=board_id,
            company_id=company_id,
            references=["1405", "8", "1393"],
            details=details,
        )
        assert dict(result) == {
            "existing_receipt": None,
            "active": 6,
            "legacy": 5,
            "canonical": 1,
            "unknown": 0,
            "discovered": 3,
            "valid": 3,
            "conflicts": 0,
            "candidates": 4,
            "existing_canonicals": 1,
            "updated": 2,
            "retired": 3,
            "may_migrate": True,
            "receipt_written": True,
        }

        migrated_1405 = await connection.fetchrow(
            "SELECT id, titles, is_active FROM job_posting WHERE source_url = $1",
            _canonical("1405"),
        )
        assert migrated_1405 is not None
        assert migrated_1405["id"] == primary_1405_id
        assert migrated_1405["titles"] == ["Legacy 1405 content"]
        assert migrated_1405["is_active"] is True
        assert (
            await connection.fetchval(
                "SELECT id FROM job_posting WHERE source_url = $1",
                _canonical("8"),
            )
            == evergreen_8_id
        )
        assert (
            await connection.fetchval(
                "SELECT id FROM job_posting WHERE source_url = $1",
                _canonical("1393"),
            )
            == canonical_1393_id
        )
        assert (
            await connection.fetchval(
                "SELECT COUNT(*) FROM job_posting WHERE id = ANY($1::uuid[]) AND is_active = false",
                [duplicate_1405_id, legacy_1393_id, expired_1215_id],
            )
            == 3
        )

        receipt = await connection.fetchval(
            "SELECT metadata -> '_identity_migration_receipt' FROM job_board WHERE id = $1",
            board_id,
        )
        if isinstance(receipt, str):
            receipt = json.loads(receipt)
        assert receipt["id"] == _UNISANTE_IDENTITY_MIGRATION
        assert receipt["updated_count"] == 2
        assert receipt["retired_count"] == 3
        assert receipt["completed_at"]

        replay = await _run(
            connection,
            board_id=board_id,
            company_id=company_id,
            references=["1405", "8", "1393"],
            details=details,
        )
        assert replay["receipt_written"] is False
        assert replay["updated"] == 0
        assert replay["retired"] == 0
    finally:
        await outer.rollback()
        await connection.close()
