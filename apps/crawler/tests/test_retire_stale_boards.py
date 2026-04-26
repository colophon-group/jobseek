"""Tests for retire_stale_boards formatting + query shape.

The DB query is exercised in the e2e suite (or by running the CLI against
a populated dev DB). Unit tests cover the pure formatting layer plus
guard-rail assertions on the query string itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.retire_stale_boards import (
    _QUERY,
    format_md,
    format_shell_snippets,
)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "00000000-0000-0000-0000-000000000001",
        "company_id": "00000000-0000-0000-0000-000000000010",
        "company_slug": "acme",
        "company_name": "Acme Corp",
        "board_slug": "acme-careers",
        "crawler_type": "greenhouse",
        "board_url": "https://boards.greenhouse.io/acme",
        "board_status": "disabled",
        "last_success_at": datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
        "consecutive_failures": 8,
        "stale_days": 15.4,
        "active_postings": 0,
        "healthy_siblings": 1,
    }
    base.update(overrides)
    return base


# ---------- format_md ----------------------------------------------------


def test_format_md_empty_returns_friendly_message() -> None:
    assert format_md([]) == "No retirement candidates found."


def test_format_md_renders_header_and_one_row() -> None:
    out = format_md([_row()])
    assert "| Company | Board slug |" in out
    assert "|---|" in out
    assert "acme (Acme Corp)" in out
    assert "`acme-careers`" in out
    assert "`greenhouse`" in out
    assert "`disabled`" in out
    assert "2026-04-10 12:00 UTC" in out
    assert "15.4" in out


def test_format_md_handles_missing_board_slug_and_crawler() -> None:
    out = format_md([_row(board_slug=None, crawler_type=None)])
    assert "(no slug)" in out
    assert "(none)" in out


def test_format_md_handles_no_last_success() -> None:
    out = format_md([_row(last_success_at=None)])
    assert "(never)" in out


def test_format_md_renders_multiple_rows_in_order() -> None:
    rows = [
        _row(company_slug="alpha", board_slug="alpha-careers"),
        _row(company_slug="beta", board_slug="beta-careers"),
    ]
    out = format_md(rows)
    alpha_pos = out.index("alpha-careers")
    beta_pos = out.index("beta-careers")
    assert alpha_pos < beta_pos


# ---------- format_shell_snippets -----------------------------------------


def test_format_csv_empty_returns_friendly_comment() -> None:
    assert format_shell_snippets([]) == "# No retirement candidates found."


def test_format_csv_renders_grep_per_row() -> None:
    out = format_shell_snippets([_row()])
    assert "grep -vF" in out
    assert "data/boards.csv" in out
    assert "https://boards.greenhouse.io/acme" in out
    assert "# acme acme-careers (disabled)" in out


def test_format_csv_uses_fixed_string_match_not_regex() -> None:
    """`grep -F` is critical: URLs contain `.` which would otherwise be a regex
    metachar and over-match unrelated rows (e.g. `jobs.x.com` matching
    `jobsXxXcom`)."""
    out = format_shell_snippets([_row(board_url="https://jobs.example.com/path")])
    assert "grep -vF" in out
    assert "https://jobs.example.com/path" in out


def test_format_csv_skips_rows_with_unsafe_quote_in_url() -> None:
    """Defensive: shell-unsafe URLs (rare; no-op today) emit a SKIP comment
    rather than a snippet that the operator might paste blindly."""
    out = format_shell_snippets([_row(board_url="https://x.com/o'brien")])
    assert "SKIP" in out
    assert "single quote" in out
    assert "grep -vF" not in out


def test_format_csv_anchors_on_url_with_csv_separators() -> None:
    """The pattern `,<url>,` ensures we don't accidentally match a substring
    of a different column (e.g. a URL embedded in scraper_config JSON)."""
    out = format_shell_snippets([_row()])
    assert ",https://boards.greenhouse.io/acme," in out


def test_format_csv_includes_operator_instructions_header() -> None:
    out = format_shell_snippets([_row()])
    assert "Run from apps/crawler/" in out
    assert "git checkout -b" in out


# ---------- query shape ---------------------------------------------------


def test_query_filters_to_disabled_or_gone() -> None:
    """Only boards in the dead status set should be candidates."""
    assert "board_status IN ('disabled', 'gone')" in _QUERY


def test_query_excludes_boards_with_active_postings() -> None:
    """A board with active postings must not be a retirement candidate
    (orphan postings are a separate cleanup concern)."""
    assert "bs.active_postings = 0" in _QUERY


def test_query_excludes_companies_without_healthy_siblings() -> None:
    """Retirement must not orphan a company — at least one live sibling
    board must remain. Live = active OR suspect (matches the dispatcher's
    definition in queries/monitor.py)."""
    assert "bs.healthy_siblings >= 1" in _QUERY
    assert "sib.board_status IN ('active', 'suspect')" in _QUERY
    assert "sib.is_enabled = true" in _QUERY


def test_query_treats_never_succeeded_as_strongest_candidate() -> None:
    """A disabled board with `last_success_at IS NULL` (never succeeded)
    must be a candidate, not silently excluded. The strip-NOT-NULL bug in
    an earlier draft would have hidden these from the operator."""
    assert "jb.last_success_at IS NULL" in _QUERY
    assert "OR jb.last_success_at <" in _QUERY


def test_query_filters_on_last_success_age_with_days_param() -> None:
    """The --days threshold must apply to last_success_at."""
    assert "jb.last_success_at <" in _QUERY
    assert "$1::int || ' days'" in _QUERY


def test_query_orders_results_for_stable_diffs() -> None:
    """Stable ordering keeps PR diffs and operator scans deterministic."""
    assert "ORDER BY c.slug, bs.board_slug" in _QUERY


def test_query_only_targets_dead_statuses_in_outer_filter() -> None:
    """Defensive: the outer `board_status` filter must exclude live statuses.
    `'active'`/`'suspect'` legitimately appear in the sibling sub-query —
    use a full-string check on the outer filter clause."""
    assert "jb.board_status IN ('disabled', 'gone')" in _QUERY
