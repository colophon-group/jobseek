"""Static contracts for the bounded NW provider identity migration."""

from __future__ import annotations

import csv
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cli import parse_args
from src.nw_provider_cutover import _migration_sql, reapply_nw_provider_cutover

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"

_EXPECTED_MAPPINGS = {
    (
        "https://jobs.nw-groupe.com/jobs/7465186-bess-project-manager-italy",
        "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"
        "bess-project-manager-italy_milano",
    ),
    (
        "https://jobs.nw-groupe.com/jobs/8125397-ingenieur-automaticien-bess-h-f-cdi",
        "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"
        "ingenieur-automaticien-bess-h-f-cdi_paris",
    ),
    (
        "https://jobs.nw-groupe.com/jobs/"
        "5985741-charge-du-suivi-des-financements-h-f-apprentissage",
        "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"
        "charge-du-suivi-des-financements-apprentissage_paris_NW_6089Kzx",
    ),
    (
        "https://jobs.nw-groupe.com/jobs/8115338-charge-de-financement-h-f-cdi",
        "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"
        "charge-de-financement-h-f-cdi_paris",
    ),
    (
        "https://jobs.nw-groupe.com/jobs/"
        "8113870-ingenieur-qualite-produit-maintenance-n3-h-f-stage",
        "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"
        "ingenieur-qualite-produit-maintenance-n3-h-f-stage_paris",
    ),
    (
        "https://jobs.nw-groupe.com/jobs/8108098-analyste-foncier-h-f-apprentissage",
        "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"
        "analyste-foncier-h-f-apprentissage_lyon",
    ),
    (
        "https://jobs.nw-groupe.com/jobs/7580949-purchasing-contract-manager-h-f-cdi",
        "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"
        "purchasing-contract-manager-h-f-cdi_paris_NW_qyklLVV",
    ),
    (
        "https://jobs.nw-groupe.com/jobs/8011898-senior-erp-project-manager-h-f-cdi",
        "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"
        "senior-erp-migration-project-manager-h-f-cdi_paris",
    ),
    (
        "https://jobs.nw-groupe.com/jobs/8010487-rnw-stage-chef-de-projet-marche-h-f",
        "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs/"
        "rnw-stage-chef-de-projet-marche-h-f_paris_NW_VdkN6eN",
    ),
}


def test_nw_board_and_identity_migration_form_one_atomic_provider_cutover() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0021_migrate_nw_provider_identities"
    )
    with _BOARDS.open(newline="") as handle:
        board = next(row for row in csv.DictReader(handle) if row["board_slug"] == "nw-careers")

    assert board == {
        "company_slug": "nw",
        "board_slug": "nw-careers",
        "board_url": "https://www.welcometothejungle.com/fr/companies/nw-groupe/jobs",
        "monitor_type": "welcometothejungle",
        "monitor_config": '{"slug": "nw-groupe", "locale": "fr"}',
        "scraper_type": "skip",
        "scraper_config": "",
    }
    assert migration.revision == "0021"
    assert migration.down_revision == "0020"
    assert set(migration._NW_IDENTITY_MAPPINGS) == _EXPECTED_MAPPINGS
    assert len(migration._NW_IDENTITY_MAPPINGS) == 9

    sql = migration._MIGRATE_NW_PROVIDER_IDENTITIES
    assert sql.count("board.board_slug = 'nw-careers'") == 3
    assert sql.count("legacy.is_active = true") == 2
    assert "posting.is_active = true" in sql
    assert "EXISTS (" in sql
    assert "NOT EXISTS (" in sql
    assert "IF nw_board_count > 1" in sql
    assert "foreign canonical URL ownership" in sql
    assert sql.count("canonical.board_id = board.id") == 2
    assert "canonical.board_id IS DISTINCT FROM" in sql
    assert "SET source_url = identity_map.canonical_url" in sql
    assert "SET is_active = false" in sql
    assert sql.count("next_scrape_at = NULL") == 2
    assert sql.count("updated_at = now()") == 3
    assert "posting.source_url LIKE 'https://jobs.nw-groupe.com/jobs/%'" in sql
    assert "6819795" not in migration._IDENTITY_VALUES

    execute = MagicMock()
    original_op = migration.op
    migration.op = MagicMock(execute=execute)
    try:
        migration.upgrade()
        migration.downgrade()
    finally:
        migration.op = original_op
    execute.assert_called_once_with(sql)


def test_nw_cutover_retry_command_has_no_unbounded_arguments(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "repair-nw-provider-cutover"])
    args = parse_args()
    assert vars(args) == {"command": "repair-nw-provider-cutover"}


@pytest.mark.asyncio
async def test_nw_cutover_retry_reuses_the_exact_idempotent_migration_sql() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0021_migrate_nw_provider_identities"
    )
    connection = AsyncMock()
    connection.execute.return_value = "UPDATE 0"

    result = await reapply_nw_provider_cutover(connection)

    assert _migration_sql() is migration._MIGRATE_NW_PROVIDER_IDENTITIES
    connection.execute.assert_awaited_once_with(migration._MIGRATE_NW_PROVIDER_IDENTITIES)
    assert result == "UPDATE 0"
