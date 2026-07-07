"""Tests for the occupation reprocess operator command (#3360)."""

from __future__ import annotations

import sys

from src.cli import parse_args
from src.occupation_reprocess import (
    SPLIT_PARENT_SLUGS,
    _candidate_rows_sql,
    _diff_row,
)


def _compact(sql: str) -> str:
    return " ".join(sql.split())


def test_candidate_sql_defaults_to_active_and_includes_nulls() -> None:
    sql = _compact(
        _candidate_rows_sql(
            limit=None,
            include_inactive=False,
            include_nulls=True,
        )
    )

    assert "AND jp.is_active" in sql
    assert "o.slug = ANY($1::text[]) OR jp.occupation_id IS NULL" in sql
    assert "LIMIT" not in sql


def test_candidate_sql_include_inactive_drops_only_active_predicate() -> None:
    sql = _compact(
        _candidate_rows_sql(
            limit=50,
            include_inactive=True,
            include_nulls=True,
        )
    )

    assert "AND jp.is_active" not in sql
    assert "LIMIT 50" in sql


def test_candidate_sql_can_skip_null_backfill_scope() -> None:
    sql = _compact(
        _candidate_rows_sql(
            limit=None,
            include_inactive=False,
            include_nulls=False,
        )
    )

    assert "o.slug = ANY($1::text[])" in sql
    assert "OR jp.occupation_id IS NULL" not in sql


def test_diff_row_resolves_new_split_alias_from_null() -> None:
    change = _diff_row(
        {
            "id": "posting-1",
            "title": "Senior MLOps Engineer",
            "old_id": None,
            "old_slug": None,
            "is_active": True,
        },
        {"mlops-engineer": 72},
    )

    assert change is not None
    assert change.old_slug is None
    assert change.new_slug == "mlops-engineer"
    assert change.new_id == 72
    assert change.pair == ("NULL", "mlops-engineer")


def test_diff_row_can_clear_pruned_precision_alias() -> None:
    change = _diff_row(
        {
            "id": "posting-2",
            "title": "Financial Analyst",
            "old_id": 42,
            "old_slug": "data-analyst",
            "is_active": True,
        },
        {},
    )

    assert change is not None
    assert change.old_slug == "data-analyst"
    assert change.new_slug is None
    assert change.new_id is None
    assert change.pair == ("data-analyst", "NULL")


def test_diff_row_ignores_already_current_assignment() -> None:
    assert (
        _diff_row(
            {
                "id": "posting-3",
                "title": "Cloud Engineer",
                "old_id": 70,
                "old_slug": "cloud-engineer",
                "is_active": True,
            },
            {"cloud-engineer": 70},
        )
        is None
    )


def test_split_parent_scope_contains_issue_3360_parents() -> None:
    assert "devops-engineer" in SPLIT_PARENT_SLUGS
    assert "embedded-engineer" in SPLIT_PARENT_SLUGS
    assert "solutions-architect" in SPLIT_PARENT_SLUGS
    assert "data-annotator" in SPLIT_PARENT_SLUGS


def test_crawler_cli_exposes_occupation_reprocess_command(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crawler",
            "reprocess-occupations",
            "--dry-run",
            "--include-inactive",
            "--skip-nulls",
            "--limit",
            "100",
            "--progress-every",
            "10",
        ],
    )

    args = parse_args()

    assert args.command == "reprocess-occupations"
    assert args.dry_run is True
    assert args.include_inactive is True
    assert args.skip_nulls is True
    assert args.limit == 100
    assert args.progress_every == 10


def test_crawler_cli_exposes_occupation_reprocess_stats_mode(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "reprocess-occupations", "--stats"])

    args = parse_args()

    assert args.command == "reprocess-occupations"
    assert args.stats is True
    assert args.dry_run is False
    assert args.live is False


def test_crawler_cli_exposes_occupation_reprocess_live_mode(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "reprocess-occupations", "--live"])

    args = parse_args()

    assert args.command == "reprocess-occupations"
    assert args.live is True
    assert args.dry_run is False
    assert args.stats is False
