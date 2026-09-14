"""Safety contracts for the Starbucks regional-company merge."""

from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

_DATA = Path(__file__).parents[1] / "data"


def test_starbucks_uses_one_company_with_both_regional_boards() -> None:
    with (_DATA / "companies.csv").open(newline="", encoding="utf-8") as handle:
        companies = [row for row in csv.DictReader(handle) if row["slug"].startswith("starbucks")]
    with (_DATA / "boards.csv").open(newline="", encoding="utf-8") as handle:
        boards = [row for row in csv.DictReader(handle) if "starbucks" in row["board_slug"]]

    assert [(row["slug"], row["name"]) for row in companies] == [("starbucks", "Starbucks")]
    assert {row["board_slug"] for row in boards} == {
        "starbucks-eightfold",
        "starbucks-china-careers",
    }
    assert {row["company_slug"] for row in boards} == {"starbucks"}
    china_board = next(row for row in boards if row["board_slug"] == "starbucks-china-careers")
    assert json.loads(china_board["monitor_config"])["portal_id"] == (
        "447d00df-76b1-4da9-a0c5-287e2adb7b0c"
    )


def test_migration_rehomes_every_duplicate_reference_before_deletion() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0028_merge_starbucks_company_identity"
    )
    sql = " ".join(migration._MERGE_STARBUCKS_COMPANY_IDENTITY.split()).lower()

    assert migration.revision == "0028"
    assert migration.down_revision == "0027"
    assert "where slug = 'starbucks'" in sql
    assert "where slug = 'starbucks-china'" in sql
    assert "update job_board set company_id = canonical_id" in sql
    assert "update job_posting set company_id = canonical_id" in sql
    assert "updated_at = now()" in sql
    assert "update murmur_accept_log set company_id = $1 where company_id = $2" in sql
    assert "delete from company_description where company_id = $1" in sql
    assert "crawler references remain" in sql
    assert "delete from company where id = duplicate_id" in sql
    assert " like " not in sql
    assert " similar to " not in sql
    assert " ~ " not in sql

    execute = MagicMock()
    original_op = migration.op
    migration.op = MagicMock(execute=execute)
    try:
        migration.upgrade()
        migration.downgrade()
    finally:
        migration.op = original_op
    execute.assert_called_once_with(migration._MERGE_STARBUCKS_COMPANY_IDENTITY)
