"""PostgreSQL proof for the reversible ECOM Teamtailor identity cutover."""

from __future__ import annotations

import importlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import asyncpg
import pytest

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)

_CURRENT_HOST_COUNTS = {
    "careerslatam.ecomtrading.com": 10,
    "careerswestafrica.ecomtrading.com": 17,
    "careersasiapacific.ecomtrading.com": 3,
    "careersbrazil.ecomtrading.com": 4,
    "careersmexico.ecomtrading.com": 1,
    "careerseurope.ecomtrading.com": 9,
}


def _as_json(value):
    return json.loads(value) if isinstance(value, str) else value


def _canonical_url(source_url: str) -> str:
    host = urlsplit(source_url).hostname
    assert host is not None
    if host == "careerseurope.ecomtrading.com":
        host = "ecomeurope.teamtailor.com"
    provider_id = re.search(r"/jobs/([0-9]+)", source_url)
    assert provider_id is not None
    return f"https://{host}/jobs/{provider_id.group(1)}"


async def _insert_board(connection, migration) -> tuple[uuid.UUID, uuid.UUID]:
    company_id = uuid.uuid4()
    board_id = uuid.uuid4()
    await connection.execute(
        "INSERT INTO company (id, slug, name) "
        "VALUES ($1, 'ecom-agroindustrial', 'ECOM Agroindustrial')",
        company_id,
    )
    await connection.execute(
        "INSERT INTO job_board "
        "(id, company_id, board_slug, board_url, crawler_type, metadata) "
        "VALUES ($1, $2, 'ecom-agroindustrial-global', $3, 'rss', $4::jsonb)",
        board_id,
        company_id,
        migration._BOARD_URL,
        json.dumps({"_monitor_config_fingerprint": migration._CONFIG_FINGERPRINT}),
    )
    return company_id, board_id


async def test_ecom_cutover_handles_production_shape_idempotently_and_rolls_back() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()

    try:
        company_id, board_id = await _insert_board(connection, migration)
        posting_rows = []
        job_id = 8_000_000
        for host, count in _CURRENT_HOST_COUNTS.items():
            for index in range(count):
                locale = "/de" if host == "careerseurope.ecomtrading.com" and index % 2 else ""
                source_url = f"https://{host}{locale}/jobs/{job_id}-regional-title-{index}"
                is_active = job_id != 8_000_000
                posting_rows.append(
                    (
                        uuid.uuid4(),
                        company_id,
                        board_id,
                        source_url,
                        is_active,
                        datetime(2026, 8, 26, tzinfo=UTC),
                        0 if is_active else 2,
                    )
                )
                job_id += 1

        duplicate_source = (
            "https://careerswestafrica.ecomtrading.com/jobs/8000010-retired-old-title"
        )
        posting_rows.append(
            (
                uuid.uuid4(),
                company_id,
                board_id,
                duplicate_source,
                False,
                datetime(2026, 7, 1, tzinfo=UTC),
                4,
            )
        )
        assert len(posting_rows) == 45

        await connection.executemany(
            "INSERT INTO job_posting "
            "(id, company_id, board_id, source_url, is_active, last_seen_at, "
            " next_scrape_at, missing_count) "
            "VALUES ($1, $2, $3, $4, $5, $6, now(), $7)",
            posting_rows,
        )
        original = {
            str(row["id"]): dict(row)
            for row in await connection.fetch(
                "SELECT id, source_url, is_active, missing_count, next_scrape_at "
                "FROM job_posting WHERE board_id = $1 ORDER BY id",
                board_id,
            )
        }

        await connection.execute(migration._MIGRATE_ECOM_TEAMTAILOR_IDENTITIES)

        migrated = await connection.fetch(
            "SELECT id, source_url, is_active FROM job_posting WHERE board_id = $1 ORDER BY id",
            board_id,
        )
        canonical_rows = [
            row for row in migrated if re.fullmatch(migration._CANONICAL_PATTERN, row["source_url"])
        ]
        assert len(migrated) == 45
        assert len(canonical_rows) == 44
        assert sum(row["is_active"] for row in migrated) == 43
        assert all("ecomtradinggroup.teamtailor.com" not in row["source_url"] for row in migrated)
        assert (
            sum(
                row["source_url"].startswith("https://ecomeurope.teamtailor.com/jobs/")
                for row in canonical_rows
            )
            == 9
        )
        assert {_canonical_url(row[3]) for row in posting_rows} == {
            row["source_url"] for row in canonical_rows
        }

        receipt = _as_json(
            await connection.fetchval(
                "SELECT metadata -> '_identity_migration_receipt' FROM job_board WHERE id = $1",
                board_id,
            )
        )
        assert receipt["id"] == migration._MIGRATION_ID
        assert receipt["retired_count"] == 1
        assert len(receipt["rollback_rows"]) == 45

        await connection.execute(migration._MIGRATE_ECOM_TEAMTAILOR_IDENTITIES)
        assert (
            _as_json(
                await connection.fetchval(
                    "SELECT metadata -> '_identity_migration_receipt' FROM job_board WHERE id = $1",
                    board_id,
                )
            )
            == receipt
        )

        await connection.execute(migration._ROLLBACK_ECOM_TEAMTAILOR_IDENTITIES)
        restored = {
            str(row["id"]): dict(row)
            for row in await connection.fetch(
                "SELECT id, source_url, is_active, missing_count, next_scrape_at "
                "FROM job_posting WHERE board_id = $1 ORDER BY id",
                board_id,
            )
        }
        assert restored == original
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


async def test_ecom_cutover_rejects_unknown_active_source_atomically() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()

    try:
        company_id, board_id = await _insert_board(connection, migration)
        known_url = "https://careerslatam.ecomtrading.com/jobs/8000000-known-title"
        await connection.executemany(
            "INSERT INTO job_posting (id, company_id, board_id, source_url, is_active) "
            "VALUES ($1, $2, $3, $4, true)",
            [
                (uuid.uuid4(), company_id, board_id, known_url),
                (uuid.uuid4(), company_id, board_id, "https://evil.example/jobs/8000001"),
            ],
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="unknown active source identities"):
            await connection.execute(migration._MIGRATE_ECOM_TEAMTAILOR_IDENTITIES)
        await attempt.rollback()

        assert (
            await connection.fetchval(
                "SELECT count(*) FROM job_posting WHERE board_id = $1 AND source_url = $2",
                board_id,
                known_url,
            )
            == 1
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


async def test_ecom_cutover_rejects_preexisting_canonical_collision_atomically() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()

    try:
        company_id, board_id = await _insert_board(connection, migration)
        legacy_id = uuid.uuid4()
        canonical_id = uuid.uuid4()
        legacy_url = "https://careerseurope.ecomtrading.com/jobs/7769137-current-title"
        canonical_url = "https://ecomeurope.teamtailor.com/jobs/7769137"
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
