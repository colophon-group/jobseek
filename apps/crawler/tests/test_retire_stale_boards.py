"""Tests for retire_stale_boards formatting + query shape.

The DB query is exercised in the e2e suite (or by running the CLI against
a populated dev DB). Unit tests cover the pure formatting layer plus
guard-rail assertions on the query string itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from src.retire_stale_boards import (
    _COMPANY_QUERY,
    _QUERY,
    find_dead_companies,
    find_stale_boards,
    format_md,
    format_shell_snippets,
    report_stale_boards,
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


# ---------- SQL syntactic guard-rails -----------------------------------
#
# Substring assertions above don't catch a structurally broken query
# (mismatched parens, missing CTE close, etc.). The cheapest backstop
# without a real Postgres in the test env is paren-balance + a single-
# `SELECT` statement check at the structure level (postgres lets you
# nest SELECTs but the WITH ... SELECT shape we use should have one
# outer SELECT after the final CTE close paren, no stray top-level
# fragments).


def _strip_string_literals(sql: str) -> str:
    """Remove '...' literals so paren-balance counting isn't fooled by
    a literal that happens to contain `(` or `)`. Both queries use only
    simple ANSI single-quoted strings without escaped quotes."""
    out: list[str] = []
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            continue
        if not in_str:
            out.append(ch)
    return "".join(out)


def _assert_balanced(sql: str) -> None:
    cleaned = _strip_string_literals(sql)
    depth = 0
    for ch in cleaned:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            assert depth >= 0, f"unbalanced ')' at depth -1 in:\n{sql}"
    assert depth == 0, f"{depth} unclosed '(' in:\n{sql}"


def test_query_has_balanced_parens() -> None:
    """Regression for an Edit that dropped a CTE's closing `)` —
    substring tests still passed, but the SQL would have failed at
    runtime with a syntax error. This guard would have caught it."""
    _assert_balanced(_QUERY)


def test_query_cte_close_is_followed_by_outer_select() -> None:
    """The CTE-then-SELECT shape: ``)\\nSELECT`` (or `) SELECT`) appears
    exactly once after the WITH block. Asserts the structural seam
    rather than counting characters."""
    # Strip whitespace variants for a robust check.
    compact = " ".join(_QUERY.split())
    assert ") SELECT" in compact


# ---------- Section B: company-level "entirely dead" -----------------------


def _company_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "company_id": "00000000-0000-0000-0000-000000000010",
        "company_slug": "ghost-corp",
        "company_name": "Ghost Corp",
        "total_boards": 2,
        "stale_dead_boards": 2,
        "oldest_stale_days": 28.4,
    }
    base.update(overrides)
    return base


# ---------- _COMPANY_QUERY shape ----------------------------------------


def test_company_query_uses_dispatcher_live_definition() -> None:
    """Live = `is_enabled = true AND board_status IN ('active', 'suspect')`,
    matching the dispatcher's claim filter in queries/monitor.py. The
    SECTION_B definition of "dead" is the strict negation, so a board
    with (board_status='active', is_enabled=false) — which Section A's
    outer `board_status IN ('disabled', 'gone')` filter would silently
    miss — is still counted as dead here."""
    assert "is_enabled = true" in _COMPANY_QUERY
    assert "board_status IN ('active', 'suspect')" in _COMPANY_QUERY
    assert "AS is_live" in _COMPANY_QUERY


def test_company_query_filters_to_zero_live_boards() -> None:
    """A company with even one live board is NOT a Section B candidate
    (it lives via that board). Mutual exclusion with Section A follows
    from this filter."""
    assert "ch.live_boards = 0" in _COMPANY_QUERY


def test_company_query_requires_every_dead_board_stale() -> None:
    """A company whose boards just turned dead in the last few hours is
    not yet a candidate — transient outages happen. Every non-live
    board must pass the --days staleness gate."""
    assert "stale_dead_boards = ch.total_boards" in _COMPANY_QUERY
    assert "$1::int || ' days'" in _COMPANY_QUERY


def test_company_query_excludes_companies_with_active_postings() -> None:
    """Active postings = real user-facing pages; do not retire."""
    assert "ch.total_active_postings = 0" in _COMPANY_QUERY


def test_company_query_excludes_companies_with_zero_boards() -> None:
    """A company row without any boards (rare; CSV bug) shouldn't be
    flagged as 'all dead' — it's just incomplete."""
    assert "ch.total_boards >= 1" in _COMPANY_QUERY


def test_company_query_orders_results_for_stable_diffs() -> None:
    assert "ORDER BY c.slug" in _COMPANY_QUERY


def test_company_query_joins_company_metadata() -> None:
    """Need slug + name for the report rows."""
    assert "JOIN company c" in _COMPANY_QUERY
    assert "c.slug AS company_slug" in _COMPANY_QUERY
    assert "c.name AS company_name" in _COMPANY_QUERY


def test_company_query_has_balanced_parens() -> None:
    """Same guard as `test_query_has_balanced_parens` — the company-
    level query has nested CTEs (per_board, company_health) and is
    even more vulnerable to a stray `)` drop."""
    _assert_balanced(_COMPANY_QUERY)


# ---------- format_md with Section B ------------------------------------


def test_format_md_with_only_section_b() -> None:
    out = format_md([], [_company_row()])
    assert "## Section A" not in out
    assert "## Section B" in out
    assert "ghost-corp (Ghost Corp)" in out
    assert "| 2 | 2 | 28.4 |" in out


def test_format_md_with_both_sections() -> None:
    """Section A renders before Section B."""
    out = format_md([_row()], [_company_row()])
    a_pos = out.index("## Section A")
    b_pos = out.index("## Section B")
    assert a_pos < b_pos
    assert "acme (Acme Corp)" in out
    assert "ghost-corp (Ghost Corp)" in out


def test_format_md_with_neither_section_returns_friendly_message() -> None:
    assert format_md([], []) == "No retirement candidates found."
    assert format_md([], None) == "No retirement candidates found."


def test_format_md_section_b_handles_null_oldest_stale_days() -> None:
    """All boards `last_success_at IS NULL` → Postgres returns NULL for
    the MAX. Render as 0.0 rather than crashing."""
    out = format_md([], [_company_row(oldest_stale_days=None)])
    assert "| 0.0 |" in out


# ---------- format_shell_snippets with Section B ------------------------


def test_format_shell_section_b_drops_company_row_anchored_at_line_start() -> None:
    """companies.csv schema: slug is the first column. Anchor with ^slug,
    so a slug that is a substring of another slug doesn't false-positive."""
    out = format_shell_snippets([], [_company_row(company_slug="ghost-corp")])
    assert "data/companies.csv" in out
    assert "'^ghost-corp,'" in out


def test_format_shell_section_b_drops_all_company_boards() -> None:
    """boards.csv schema: company_slug is the first column. Use the same
    ^slug, anchor; one grep call removes every board row for the company."""
    out = format_shell_snippets([], [_company_row(company_slug="ghost-corp")])
    assert "data/boards.csv" in out
    # Both companies.csv and boards.csv use the ^slug, prefix; check the
    # boards.csv line specifically by looking for the comment tail.
    assert "remove all boards for ghost-corp" in out


def test_format_shell_section_b_skips_unsafe_slugs() -> None:
    """Defensive: a quote/comma in a slug would shell-escape badly. SLUG_RE
    excludes both today, but bail loudly if the data ever drifts."""
    out = format_shell_snippets([], [_company_row(company_slug="bad'slug")])
    assert "SKIP bad'slug" in out
    assert "'^bad'slug,'" not in out


def test_format_shell_with_both_sections_orders_a_then_b() -> None:
    out = format_shell_snippets([_row()], [_company_row()])
    a_pos = out.index("--- Section A")
    b_pos = out.index("--- Section B")
    assert a_pos < b_pos
    # Section A still emits its boards.csv-only grep
    assert "https://boards.greenhouse.io/acme" in out
    # Section B emits the company-wide block
    assert "remove ghost-corp" in out


def test_format_shell_with_neither_section_returns_friendly_comment() -> None:
    assert format_shell_snippets([], []) == "# No retirement candidates found."
    assert format_shell_snippets([], None) == "# No retirement candidates found."


# ---------- mutual exclusion guard --------------------------------------


def test_section_a_filter_implies_company_is_excluded_from_section_b() -> None:
    """Logical guard, not a code path: `_QUERY` filters per-board with
    `healthy_siblings >= 1`, which means at least one live sibling.
    `_COMPANY_QUERY` filters per-company with `live_boards = 0`. The two
    sets are therefore disjoint by construction — assert both clauses
    coexist so neither is silently dropped in a future refactor."""
    assert "bs.healthy_siblings >= 1" in _QUERY
    assert "ch.live_boards = 0" in _COMPANY_QUERY


# ---------- end-to-end: real call path through report_stale_boards ------


class _StubConn:
    """Minimal asyncpg.Connection stand-in that records fetch() calls.

    The two queries dispatched by ``report_stale_boards`` are
    distinguishable by the SQL — ``_QUERY`` mentions
    ``healthy_siblings`` while ``_COMPANY_QUERY`` mentions
    ``live_boards``. This lets the stub return the right canned rowset
    per call without re-implementing the fetch protocol.
    """

    def __init__(
        self,
        board_rows: list[dict[str, Any]],
        company_rows: list[dict[str, Any]],
    ) -> None:
        self.board_rows = board_rows
        self.company_rows = company_rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((query, args))
        if "healthy_siblings" in query:
            return self.board_rows  # type: ignore[return-value]
        if "live_boards" in query:
            return self.company_rows  # type: ignore[return-value]
        raise AssertionError(f"unexpected query: {query[:80]!r}")


@pytest.mark.asyncio
async def test_report_stale_boards_md_includes_both_sections() -> None:
    """Real call path: report_stale_boards → find_stale_boards +
    find_dead_companies → format_md. Verifies both queries are dispatched
    with the --days param and the rendered output contains both
    sections' content."""
    conn = _StubConn([_row()], [_company_row()])
    out = await report_stale_boards(conn, days=14, fmt="md")  # type: ignore[arg-type]

    assert "## Section A" in out
    assert "## Section B" in out
    assert "acme (Acme Corp)" in out
    assert "ghost-corp (Ghost Corp)" in out
    # Both queries received the days param
    assert len(conn.calls) == 2
    assert conn.calls[0][1] == (14,)
    assert conn.calls[1][1] == (14,)


@pytest.mark.asyncio
async def test_report_stale_boards_shell_includes_both_sections() -> None:
    conn = _StubConn([_row()], [_company_row()])
    out = await report_stale_boards(conn, days=14, fmt="shell")  # type: ignore[arg-type]

    assert "--- Section A" in out
    assert "--- Section B" in out
    assert "data/boards.csv" in out
    assert "data/companies.csv" in out


@pytest.mark.asyncio
async def test_report_stale_boards_with_no_candidates_anywhere() -> None:
    conn = _StubConn([], [])
    md = await report_stale_boards(conn, days=14, fmt="md")  # type: ignore[arg-type]
    shell = await report_stale_boards(conn, days=14, fmt="shell")  # type: ignore[arg-type]
    assert md == "No retirement candidates found."
    assert shell == "# No retirement candidates found."


@pytest.mark.asyncio
async def test_find_dead_companies_dispatches_company_query_with_days() -> None:
    conn = _StubConn([], [_company_row()])
    rows = await find_dead_companies(conn, days=21)  # type: ignore[arg-type]
    assert len(rows) == 1
    assert rows[0]["company_slug"] == "ghost-corp"
    # Only one query (the company-level one), with the days param
    assert len(conn.calls) == 1
    assert "live_boards" in conn.calls[0][0]
    assert conn.calls[0][1] == (21,)


@pytest.mark.asyncio
async def test_find_stale_boards_does_not_dispatch_company_query() -> None:
    """Defense against accidentally calling the wrong query inside
    find_stale_boards (would be a copy-paste regression)."""
    conn = _StubConn([_row()], [])
    rows = await find_stale_boards(conn, days=14)  # type: ignore[arg-type]
    assert len(rows) == 1
    assert len(conn.calls) == 1
    assert "healthy_siblings" in conn.calls[0][0]
