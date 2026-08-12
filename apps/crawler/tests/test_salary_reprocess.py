"""Tests for the EU salary reprocess operator command (#3359)."""

from __future__ import annotations

import sys
from pathlib import Path

import src.salary_reprocess as salary_reprocess
from src.cli import parse_args
from src.salary_reprocess import _country_rows_sql, _iter_country_rows, _resolve_country_ids


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


async def test_country_iteration_closes_each_query_before_salary_extraction(monkeypatch) -> None:
    class BatchConnection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.batches = [
                [{"id": "posting-1"}, {"id": "posting-2"}],
                [{"id": "posting-3"}],
            ]

        def transaction(self, *args, **kwargs):
            raise AssertionError("batch iteration must not open a long transaction")

        def cursor(self, *args, **kwargs):
            raise AssertionError("batch iteration must not use a server cursor")

        async def fetch(self, sql: str, *args: object):
            self.calls.append((sql, args))
            return self.batches.pop(0)

    monkeypatch.setattr(salary_reprocess, "FETCH_BATCH", 2)
    connection = BatchConnection()

    rows = [
        row
        async for row in _iter_country_rows(
            connection,
            [1, 2],
            limit=3,
            include_inactive=False,
        )
    ]

    assert [row["id"] for row in rows] == ["posting-1", "posting-2", "posting-3"]
    assert len(connection.calls) == 2
    assert connection.calls[0][1][1] is None
    assert connection.calls[1][1][1] == "posting-2"
    assert "LIMIT 2" in connection.calls[0][0]
    assert "LIMIT 1" in connection.calls[1][0]


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
