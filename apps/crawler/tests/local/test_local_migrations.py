from __future__ import annotations

from pathlib import Path


def test_lookup_table_migration_exists() -> None:
    versions_dir = Path(__file__).resolve().parents[2] / "src" / "migrations" / "versions"
    sql = "\n".join(path.read_text() for path in sorted(versions_dir.rglob("*.py")))

    assert "CREATE TABLE IF NOT EXISTS occupation_domain" in sql
    assert "CREATE TABLE IF NOT EXISTS occupation" in sql
    assert "CREATE TABLE IF NOT EXISTS seniority" in sql
    assert "CREATE TABLE IF NOT EXISTS location" in sql
    assert "CREATE TABLE IF NOT EXISTS currency_rate" in sql
    assert "CREATE TABLE IF NOT EXISTS taxonomy_miss" in sql
