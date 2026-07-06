"""Tests for the EU salary reprocess operator command (#3359)."""

from __future__ import annotations

import sys
from pathlib import Path

from src.cli import parse_args
from src.salary_reprocess import _country_rows_sql, _resolve_country_ids


def _compact(sql: str) -> str:
    return " ".join(sql.split())


def test_include_inactive_drops_only_active_predicate() -> None:
    active_sql = _compact(_country_rows_sql(limit=None, include_inactive=False))
    inactive_sql = _compact(_country_rows_sql(limit=None, include_inactive=True))

    assert "WHERE jp.location_ids && $1::int[] AND jp.is_active" in active_sql
    assert "WHERE jp.location_ids && $1::int[]" in inactive_sql
    assert "AND jp.is_active" not in inactive_sql
    assert "jp.is_active" in inactive_sql
    assert "JOIN LATERAL" in inactive_sql


def test_country_set_all_includes_both_salary_scopes() -> None:
    scope_a = _resolve_country_ids("scope-a")
    scope_b = _resolve_country_ids("scope-b")
    all_scopes = _resolve_country_ids("all")

    assert all_scopes == {**scope_a, **scope_b}
    assert len(all_scopes) == 19


def test_crawler_cli_exposes_salary_reprocess_command(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crawler",
            "reprocess-salary-eu",
            "--dry-run",
            "--countries-set",
            "all",
            "--include-inactive",
            "--progress-every",
            "500",
        ],
    )

    args = parse_args()

    assert args.command == "reprocess-salary-eu"
    assert args.dry_run is True
    assert args.countries_set == "all"
    assert args.include_inactive is True
    assert args.progress_every == 500


def test_legacy_script_remains_wrapper() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "reprocess_salary_eu.py"

    text = script.read_text()

    assert "from src.salary_reprocess import main" in text
    assert "asyncio.run(main())" in text
