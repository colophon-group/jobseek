"""PostgreSQL proof for the Umantis provider-ID migration."""

from __future__ import annotations

import importlib
import json
import os
import uuid

import asyncpg
import pytest

REQUIRE_POSTGRES_E2E = os.getenv("REQUIRE_POSTGRES_E2E") == "true"
pytestmark = pytest.mark.skipif(
    not REQUIRE_POSTGRES_E2E,
    reason="set REQUIRE_POSTGRES_E2E=true against an isolated migrated PostgreSQL",
)


def _decode_json(value: object) -> dict:
    if isinstance(value, str):
        return json.loads(value)
    assert isinstance(value, dict)
    return value


async def _insert_contract_boards(
    connection: asyncpg.Connection,
    migration,
) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    board_ids = {}
    for board_slug, company_slug, board_url, _source_base in migration._UMANTIS_BOARD_CONTRACTS:
        company_id = uuid.uuid4()
        board_id = uuid.uuid4()
        await connection.execute(
            "INSERT INTO company (id, slug, name) VALUES ($1, $2, $3)",
            company_id,
            company_slug,
            f"{company_slug} Umantis migration E2E",
        )
        await connection.execute(
            "INSERT INTO job_board "
            "(id, company_id, board_slug, board_url, crawler_type) "
            "VALUES ($1, $2, $3, $4, 'umantis')",
            board_id,
            company_id,
            board_slug,
            board_url,
        )
        board_ids[board_slug] = (board_id, company_id)
    return board_ids


async def test_umantis_migration_preserves_rows_and_writes_exact_receipts() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        boards = await _insert_contract_boards(connection, migration)
        board_id, company_id = boards["bobst-global"]
        posting_id = uuid.uuid4()
        legacy_url = "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description/2"
        await connection.execute(
            "INSERT INTO job_posting "
            "(id, company_id, board_id, source_url, next_scrape_at, titles) "
            "VALUES ($1, $2, $3, $4, now(), ARRAY['Account Manager'])",
            posting_id,
            company_id,
            board_id,
            legacy_url,
        )
        before = await connection.fetchrow(
            "SELECT id, is_active, next_scrape_at, titles, updated_at "
            "FROM job_posting WHERE id = $1",
            posting_id,
        )

        await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)

        after = await connection.fetchrow(
            "SELECT id, source_url, is_active, next_scrape_at, titles, updated_at "
            "FROM job_posting WHERE id = $1",
            posting_id,
        )
        assert after is not None and before is not None
        assert after["id"] == before["id"]
        assert after["source_url"] == (
            "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description"
        )
        assert after["is_active"] == before["is_active"] is True
        assert after["next_scrape_at"] == before["next_scrape_at"]
        assert after["titles"] == before["titles"]
        assert after["updated_at"] > before["updated_at"]

        receipts = await connection.fetch(
            "SELECT board_slug, metadata -> $1 AS receipt FROM job_board ORDER BY board_slug",
            migration._RECEIPT_KEY,
        )
        assert len(receipts) == len(migration._UMANTIS_BOARD_CONTRACTS)
        for row in receipts:
            receipt = _decode_json(row["receipt"])
            assert set(receipt) == {
                "id",
                "version",
                "completed_at",
                "migrated_count",
                "total_count",
            }
            assert receipt["id"] == migration._MIGRATION_ID
            assert receipt["version"] == 1
            expected = 1 if row["board_slug"] == "bobst-global" else 0
            assert receipt["migrated_count"] == expected
            assert receipt["total_count"] == expected

        first = await connection.fetch(
            "SELECT id, source_url, updated_at FROM job_posting ORDER BY id"
        )
        await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        second = await connection.fetch(
            "SELECT id, source_url, updated_at FROM job_posting ORDER BY id"
        )
        assert second == first
    finally:
        await transaction.rollback()
        await connection.close()


async def test_umantis_migration_rejects_locale_alias_duplicates_atomically() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        boards = await _insert_contract_boards(connection, migration)
        board_id, company_id = boards["bobst-global"]
        urls = [
            "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description/2",
            "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description/3",
        ]
        await connection.executemany(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            [(uuid.uuid4(), company_id, board_id, url) for url in urls],
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="duplicate provider identities"):
            await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        await attempt.rollback()

        assert await connection.fetchval(
            "SELECT count(*) FROM job_posting WHERE board_id = $1 AND source_url = ANY($2::text[])",
            board_id,
            urls,
        ) == len(urls)
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM job_board WHERE metadata ? $1",
                migration._RECEIPT_KEY,
            )
            == 0
        )
    finally:
        await transaction.rollback()
        await connection.close()


async def test_umantis_migration_rejects_legacy_replay_after_receipt() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        boards = await _insert_contract_boards(connection, migration)
        await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        board_id, company_id = boards["bobst-global"]
        legacy_url = "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description/2"
        await connection.execute(
            "ALTER TABLE job_posting DISABLE TRIGGER jobseek_canonicalize_umantis_source_url_v1"
        )
        await connection.execute(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            uuid.uuid4(),
            company_id,
            board_id,
            legacy_url,
        )
        await connection.execute(
            "ALTER TABLE job_posting ENABLE TRIGGER jobseek_canonicalize_umantis_source_url_v1"
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="legacy URLs after its receipt"):
            await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        await attempt.rollback()

        assert (
            await connection.fetchval(
                "SELECT source_url FROM job_posting WHERE board_id = $1",
                board_id,
            )
            == legacy_url
        )
    finally:
        await transaction.rollback()
        await connection.close()


async def test_umantis_migration_rejects_canonical_prestate_without_exact_receipt() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        boards = await _insert_contract_boards(connection, migration)
        board_id, company_id = boards["bobst-global"]
        canonical_url = "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description"
        await connection.execute(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            uuid.uuid4(),
            company_id,
            board_id,
            canonical_url,
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="canonical URLs without an exact receipt"):
            await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        await attempt.rollback()

        assert (
            await connection.fetchval(
                "SELECT count(*) FROM job_board WHERE metadata ? $1",
                migration._RECEIPT_KEY,
            )
            == 0
        )
        assert (
            await connection.fetchval(
                f"SELECT count(*) FROM {migration._LEDGER_TABLE} WHERE migration_id = $1",
                migration._MIGRATION_ID,
            )
            == 0
        )
    finally:
        await transaction.rollback()
        await connection.close()


async def test_umantis_migration_rejects_deleted_zero_receipt() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        boards = await _insert_contract_boards(connection, migration)
        await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        board_id, _company_id = boards["bobst-global"]
        await connection.execute(
            "UPDATE job_board SET metadata = metadata - $1 WHERE id = $2",
            migration._RECEIPT_KEY,
            board_id,
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="receipt mismatch"):
            await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        await attempt.rollback()

        assert (
            await connection.fetchval(
                f"SELECT total_count FROM {migration._LEDGER_TABLE} "
                "WHERE migration_id = $1 AND board_id = $2",
                migration._MIGRATION_ID,
                board_id,
            )
            == 0
        )
    finally:
        await transaction.rollback()
        await connection.close()


async def test_umantis_migration_rejects_zero_count_tampering() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        boards = await _insert_contract_boards(connection, migration)
        board_id, company_id = boards["bobst-global"]
        await connection.execute(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            uuid.uuid4(),
            company_id,
            board_id,
            "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description/2",
        )
        await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        await connection.execute(
            "UPDATE job_board SET metadata = "
            "jsonb_set(jsonb_set(metadata, $1::text[], '0'::jsonb), $2::text[], '0'::jsonb) "
            "WHERE id = $3",
            [migration._RECEIPT_KEY, "migrated_count"],
            [migration._RECEIPT_KEY, "total_count"],
            board_id,
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="receipt mismatch"):
            await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        await attempt.rollback()

        ledger = await connection.fetchrow(
            f"SELECT migrated_count, total_count FROM {migration._LEDGER_TABLE} "
            "WHERE migration_id = $1 AND board_id = $2",
            migration._MIGRATION_ID,
            board_id,
        )
        assert ledger is not None
        assert dict(ledger) == {"migrated_count": 1, "total_count": 1}
    finally:
        await transaction.rollback()
        await connection.close()


async def test_umantis_trigger_canonicalizes_writes_from_a_rolled_back_runtime() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        boards = await _insert_contract_boards(connection, migration)
        await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        board_id, company_id = boards["bobst-global"]
        source_url = await connection.fetchval(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4) RETURNING source_url",
            uuid.uuid4(),
            company_id,
            board_id,
            "https://recruitingapp-2882.umantis.com/Vacancies/9040/Description/3",
        )

        assert source_url == ("https://recruitingapp-2882.umantis.com/Vacancies/9040/Description")
    finally:
        await transaction.rollback()
        await connection.close()


async def test_umantis_migration_rejects_namespaced_receipt_tampering() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        boards = await _insert_contract_boards(connection, migration)
        await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        board_id, _company_id = boards["bobst-global"]
        await connection.execute(
            "UPDATE job_board SET metadata = jsonb_set("
            "metadata, $1::text[], to_jsonb($2::text)) WHERE id = $3",
            [migration._RECEIPT_KEY, "id"],
            "different-provider-migration-v1",
            board_id,
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="receipt mismatch"):
            await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        await attempt.rollback()

        receipt = _decode_json(
            await connection.fetchval(
                "SELECT metadata -> $1 FROM job_board WHERE id = $2",
                migration._RECEIPT_KEY,
                board_id,
            )
        )
        assert receipt["id"] == "different-provider-migration-v1"
    finally:
        await transaction.rollback()
        await connection.close()


async def test_umantis_migration_rejects_foreign_canonical_owner_atomically() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    transaction = connection.transaction()
    await transaction.start()
    try:
        boards = await _insert_contract_boards(connection, migration)
        board_id, company_id = boards["bobst-global"]
        foreign_company_id = uuid.uuid4()
        foreign_board_id = uuid.uuid4()
        await connection.execute(
            "INSERT INTO company (id, slug, name) VALUES ($1, $2, $3)",
            foreign_company_id,
            "foreign-umantis-owner",
            "Foreign Umantis owner",
        )
        await connection.execute(
            "INSERT INTO job_board "
            "(id, company_id, board_slug, board_url, crawler_type) "
            "VALUES ($1, $2, $3, $4, 'dom')",
            foreign_board_id,
            foreign_company_id,
            "foreign-umantis-owner-board",
            "https://foreign.example/jobs",
        )
        legacy_url = "https://recruitingapp-2882.umantis.com/Vacancies/9039/Description/2"
        canonical_url = legacy_url.removesuffix("/2")
        await connection.executemany(
            "INSERT INTO job_posting (id, company_id, board_id, source_url) "
            "VALUES ($1, $2, $3, $4)",
            [
                (uuid.uuid4(), company_id, board_id, legacy_url),
                (
                    uuid.uuid4(),
                    foreign_company_id,
                    foreign_board_id,
                    canonical_url,
                ),
            ],
        )

        attempt = connection.transaction()
        await attempt.start()
        with pytest.raises(asyncpg.RaiseError, match="foreign canonical URL ownership"):
            await connection.execute(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
        await attempt.rollback()

        assert (
            await connection.fetchval(
                "SELECT source_url FROM job_posting WHERE board_id = $1",
                board_id,
            )
            == legacy_url
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM job_board WHERE metadata ? $1",
                migration._RECEIPT_KEY,
            )
            == 0
        )
    finally:
        await transaction.rollback()
        await connection.close()
