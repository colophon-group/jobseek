"""Static contracts for the bounded Umantis provider-ID migration."""

from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_mock_engine, text
from sqlalchemy.dialects import postgresql

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_all_umantis_scrapers_follow_only_same_origin_locale_redirects() -> None:
    with _BOARDS.open(newline="") as handle:
        configs = [
            json.loads(row["scraper_config"])
            for row in csv.DictReader(handle)
            if row["monitor_type"] == "umantis"
        ]

    assert len(configs) == 8
    assert all(config.get("same_origin_redirects") is True for config in configs)


def test_umantis_identity_migration_matches_the_exact_existing_registry() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    with _BOARDS.open(newline="") as handle:
        configured = {
            (row["board_slug"], row["company_slug"], row["board_url"])
            for row in csv.DictReader(handle)
            if row["monitor_type"] == "umantis"
            and row["board_slug"] != "university-of-neuchatel-umantis"
        }
    contracted = {
        (board_slug, company_slug, board_url)
        for board_slug, company_slug, board_url, _source_base in (
            migration._UMANTIS_BOARD_CONTRACTS
        )
    }

    assert migration.revision == "0022"
    assert migration.down_revision == "0021"
    assert contracted == configured
    assert len(contracted) == 7
    assert all(
        source_base.startswith("https://") for *_, source_base in migration._UMANTIS_BOARD_CONTRACTS
    )


def test_umantis_identity_migration_is_in_place_fail_closed_and_receipted(monkeypatch) -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    sql = migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES

    assert "board contract mismatch" in sql
    assert ") NOT IN (0, 7)" in sql
    assert "unexpected board URL" in sql
    assert "duplicate provider identities" in sql
    assert "foreign canonical URL ownership" in sql
    assert "legacy URLs after its receipt" in sql
    assert "receipt mismatch" in sql
    assert "to_jsonb(current_state.total_count)" in sql
    assert "posting.board_id = owned_board.id" in sql
    assert "SET source_url = regexp_replace" in sql
    assert "SET is_active = false" not in sql
    assert "next_scrape_at = NULL" not in sql
    assert "updated_at = clock_timestamp()" in sql
    assert migration._RECEIPT_KEY in sql
    assert migration._RECEIPT_KEY == "_identity_migration_receipt"
    assert migration._MIGRATION_ID in sql
    assert "migrated_count" in sql
    assert "total_count" in sql

    execute = MagicMock()
    monkeypatch.setattr(migration, "op", MagicMock(execute=execute))
    migration.upgrade()
    with pytest.raises(RuntimeError, match="cannot be downgraded safely"):
        migration.downgrade()
    execute.assert_called_once_with(sql)


def test_umantis_migration_sql_has_no_sqlalchemy_bind_tokens() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_umantis_provider_identities"
    )
    statement = text(migration._MIGRATE_UMANTIS_PROVIDER_IDENTITIES)
    executed = []
    engine = create_mock_engine(
        "postgresql+psycopg2://",
        lambda sql, *_args, **_kwargs: executed.append(sql),
    )

    engine.connect().execute(statement)

    assert statement.compile(dialect=postgresql.dialect()).params == {}
    assert executed == [statement]
