"""Surface boards (and companies) that are ripe for retirement.

Operator workflow today (#2216, #2598, #2632, ...): SSH into the Hetzner
Postgres box, run an ad-hoc SQL query against ``job_board`` to find boards
with ``board_status='disabled'`` + a stale ``last_success_at``, manually
cross-check the company has at least one healthy sibling board, then
hand-file a CSV-only retirement PR. Each retirement is N minutes of
boilerplate; some boards have been disabled for 7-17 days before anyone
notices.

This module surfaces the same query as a CLI subcommand so retirement
candidates show up automatically. It does **NOT** mutate the CSV — review
still happens in PR — but it produces both a markdown table for the PR
description and shell snippets the operator can paste into a terminal
to remove the rows from ``boards.csv`` (and, for entirely-dead companies,
``companies.csv``).

The report has two sections:

**Section A — board-level retirement.** A board is a candidate when ALL of:

- ``board_status IN ('disabled', 'gone')``
- ``last_success_at`` is NULL (never succeeded) OR older than ``--days``
  (default 14). A never-succeeded disabled board is the strongest
  candidate, not the weakest.
- Zero active postings remain (``job_posting.is_active = true``)
- The company has at least one other live board (``board_status IN
  ('active', 'suspect')``, ``is_enabled = true``) so retirement won't
  orphan it. Matches the live-board definition used by the dispatcher
  (``queries/monitor.py``).

**Section B — entirely-dead companies (#2714).** A company is a candidate
when ALL of:

- The company has at least one board.
- ZERO boards are live by the dispatcher's definition (i.e.
  ``is_enabled = true AND board_status IN ('active', 'suspect')``).
- EVERY non-live board passes the same ``--days`` staleness gate as
  Section A (``last_success_at IS NULL`` always passes). A company whose
  boards just turned dead in the last few hours is not yet a removal
  candidate — transient outages happen.
- Zero active postings remain across ALL boards.

Section B candidates are arguably ripe for ``companies.csv`` removal in
addition to per-board retirement, so the shell snippets target both
``data/companies.csv`` and ``data/boards.csv``. Section A snippets only
touch ``data/boards.csv`` because the company stays live via its sibling
boards. The two sections are mutually exclusive: section A's
``healthy_siblings >= 1`` guarantees the company has at least one live
board, which excludes it from section B's ``live_boards = 0``.
"""

from __future__ import annotations

from typing import Any

import asyncpg

_QUERY = """
WITH board_stats AS (
    SELECT
        jb.id,
        jb.company_id,
        jb.board_slug,
        jb.crawler_type,
        jb.board_url,
        jb.board_status,
        jb.last_success_at,
        jb.consecutive_failures,
        EXTRACT(EPOCH FROM (now() - jb.last_success_at)) / 86400.0 AS stale_days,
        (
            SELECT COUNT(*)
            FROM job_posting jp
            WHERE jp.board_id = jb.id AND jp.is_active = true
        ) AS active_postings,
        (
            SELECT COUNT(*)
            FROM job_board sib
            WHERE sib.company_id = jb.company_id
              AND sib.id <> jb.id
              AND sib.board_status IN ('active', 'suspect')
              AND sib.is_enabled = true
        ) AS healthy_siblings
    FROM job_board jb
    WHERE jb.board_status IN ('disabled', 'gone')
      AND (
        jb.last_success_at IS NULL
        OR jb.last_success_at < now() - ($1::int || ' days')::interval
      )
)
SELECT
    bs.*,
    c.slug AS company_slug,
    c.name AS company_name
FROM board_stats bs
JOIN company c ON c.id = bs.company_id
WHERE bs.active_postings = 0
  AND bs.healthy_siblings >= 1
ORDER BY c.slug, bs.board_slug
"""


# Section B: companies whose ENTIRE board set is dead (#2714).
#
# Live board = ``is_enabled = true AND board_status IN ('active',
# 'suspect')`` (matches the dispatcher's claim filter in
# ``queries/monitor.py``). "Dead" is the strict negation — including
# the (board_status='active', is_enabled=false) case the dispatcher
# also won't pick up, which Section A's outer ``board_status IN
# ('disabled', 'gone')`` filter would silently miss.
#
# All non-live boards must additionally pass the ``--days`` staleness
# gate (``last_success_at IS NULL`` always passes, mirroring Section
# A's "never-succeeded = strongest candidate" semantics). A company
# whose boards just turned dead in the last few hours is not yet a
# candidate — transient outages happen.
#
# Final filter selects companies where (a) at least one board exists,
# (b) ZERO boards are live, (c) every dead board passes the staleness
# gate (``stale_dead_boards = total_boards``), and (d) zero active
# postings remain across all boards. Mutual exclusion with Section A
# follows from (b) — a company in Section A has ``healthy_siblings >=
# 1``, which means at least one live board, which excludes it here.
_COMPANY_QUERY = """
WITH per_board AS (
    SELECT
        jb.company_id,
        jb.id,
        jb.board_status,
        jb.is_enabled,
        jb.last_success_at,
        (
            jb.is_enabled = true
            AND jb.board_status IN ('active', 'suspect')
        ) AS is_live,
        (
            jb.last_success_at IS NULL
            OR jb.last_success_at < now() - ($1::int || ' days')::interval
        ) AS is_stale,
        (
            SELECT COUNT(*)
            FROM job_posting jp
            WHERE jp.board_id = jb.id AND jp.is_active = true
        ) AS active_postings
    FROM job_board jb
),
company_health AS (
    SELECT
        company_id,
        COUNT(*) AS total_boards,
        COUNT(*) FILTER (WHERE is_live) AS live_boards,
        COUNT(*) FILTER (WHERE NOT is_live AND is_stale) AS stale_dead_boards,
        SUM(active_postings) AS total_active_postings,
        MAX(GREATEST(
            EXTRACT(EPOCH FROM (now() - last_success_at)) / 86400.0,
            0
        )) AS oldest_stale_days
    FROM per_board
    GROUP BY company_id
)
SELECT
    c.id AS company_id,
    c.slug AS company_slug,
    c.name AS company_name,
    ch.total_boards,
    ch.stale_dead_boards,
    ch.oldest_stale_days
FROM company_health ch
JOIN company c ON c.id = ch.company_id
WHERE ch.total_boards >= 1
  AND ch.live_boards = 0
  AND ch.stale_dead_boards = ch.total_boards
  AND ch.total_active_postings = 0
ORDER BY c.slug
"""


async def find_stale_boards(conn: asyncpg.Connection, *, days: int) -> list[dict[str, Any]]:
    """Run the section-A board-candidate query and return row dicts.

    Caller is responsible for the connection lifetime.
    """
    rows = await conn.fetch(_QUERY, days)
    return [dict(r) for r in rows]


async def find_dead_companies(conn: asyncpg.Connection, *, days: int) -> list[dict[str, Any]]:
    """Run the section-B entirely-dead-company query and return row dicts.

    Caller is responsible for the connection lifetime. See ``_COMPANY_QUERY``
    for the candidate definition.
    """
    rows = await conn.fetch(_COMPANY_QUERY, days)
    return [dict(r) for r in rows]


_SECTION_A_HEADER = "## Section A — boards to retire (company has healthy siblings)"
_SECTION_B_HEADER = "## Section B — companies entirely dead (consider companies.csv removal)"


def format_md(
    rows: list[dict[str, Any]],
    company_rows: list[dict[str, Any]] | None = None,
) -> str:
    """Render the report as markdown for a PR description.

    *rows* are board-level retirement candidates (Section A). *company_rows*
    are companies whose entire board set is dead (Section B); pass ``None``
    or an empty list to omit Section B (back-compat for callers that only
    want Section A).

    A friendly "no candidates" line is emitted when both sections are
    empty.
    """
    parts: list[str] = []

    if rows:
        parts.append(_SECTION_A_HEADER)
        parts.append("")
        parts.append(
            "| Company | Board slug | Crawler | Status | Last success "
            "| Stale (days) | Healthy siblings |"
        )
        parts.append("|---|---|---|---|---|---:|---:|")
        for r in rows:
            last_success = (
                r["last_success_at"].strftime("%Y-%m-%d %H:%M UTC")
                if r["last_success_at"]
                else "(never)"
            )
            parts.append(
                "| {company_slug} ({company_name}) | `{board_slug}` | `{crawler}` "
                "| `{status}` | {last} | {stale:.1f} | {siblings} |".format(
                    company_slug=r["company_slug"],
                    company_name=r["company_name"],
                    board_slug=r["board_slug"] or "(no slug)",
                    crawler=r["crawler_type"] or "(none)",
                    status=r["board_status"],
                    last=last_success,
                    stale=float(r["stale_days"]),
                    siblings=r["healthy_siblings"],
                )
            )

    if company_rows:
        if parts:
            parts.append("")
        parts.append(_SECTION_B_HEADER)
        parts.append("")
        parts.append("| Company | Total boards | All-dead, stale | Oldest stale (days) |")
        parts.append("|---|---:|---:|---:|")
        for r in company_rows:
            parts.append(
                "| {slug} ({name}) | {total} | {dead} | {oldest:.1f} |".format(
                    slug=r["company_slug"],
                    name=r["company_name"],
                    total=r["total_boards"],
                    dead=r["stale_dead_boards"],
                    oldest=float(r["oldest_stale_days"] or 0.0),
                )
            )

    if not parts:
        return "No retirement candidates found."
    return "\n".join(parts)


def _shell_snippet_for_board(r: dict[str, Any]) -> str | None:
    """Render one ``grep -vF`` line that drops a board row from boards.csv.

    Returns ``None`` for shell-unsafe URLs (caller emits a SKIP comment).
    """
    url = r["board_url"] or ""
    if "'" in url:
        return None
    return (
        f"grep -vF -- ',{url},' data/boards.csv > data/boards.csv.new "
        f"&& mv data/boards.csv.new data/boards.csv  "
        f"# {r['company_slug']} {r['board_slug']} ({r['board_status']})"
    )


def format_shell_snippets(
    rows: list[dict[str, Any]],
    company_rows: list[dict[str, Any]] | None = None,
) -> str:
    """Render shell snippets to drop the matching rows from CSV.

    Section A snippets target only ``data/boards.csv`` (the company stays
    live via its sibling boards). Section B snippets target both
    ``data/companies.csv`` (one row per dead company) and
    ``data/boards.csv`` (every board belonging to that company), so the
    operator can paste a single block to retire the whole company.

    Each snippet rewrites the file in place via a temp-file rename so the
    operator can review the result with ``git diff``.

    Targets the ``board_url`` (CSV-unique within the file, enforced by the
    ``UNIQUE`` constraint on ``job_board.board_url``) and ``company.slug``
    (anchored as ``^<slug>,`` since slug is the first column of
    ``companies.csv``) using a fixed-string match — URLs contain ``.``
    which would otherwise be a regex metachar and over-match. The pattern
    ``,<url>,`` anchors on the surrounding CSV separators so a URL that
    happens to be a substring of a longer URL on another row does not
    collide.

    The anchoring is correct for today's CSVs, where the third column of
    ``boards.csv`` (``board_url``) and the first column of
    ``companies.csv`` (``slug``) are always bare values between literal
    commas and no row has a quoted cell. If a future row has a comma
    inside the URL column the snippet will silently no-op; the operator
    should notice via an empty ``git diff`` and fall back to a manual
    edit.
    """
    parts: list[str] = []

    if rows:
        parts.append("# --- Section A: boards to retire ---")
        parts.append("# Run from apps/crawler/ to drop rows from boards.csv.")
        parts.append("# Inspect the diff afterwards (`git diff data/boards.csv`) and")
        parts.append("# open a retirement PR with `git checkout -b fix-crawler/retire-...`.")
        parts.append("")
        for r in rows:
            snippet = _shell_snippet_for_board(r)
            if snippet is None:
                parts.append(
                    f"# SKIP {r['company_slug']} {r['board_slug']}: board_url "
                    "contains a single quote — drop the row manually."
                )
                continue
            parts.append(snippet)

    if company_rows:
        if parts:
            parts.append("")
        parts.append("# --- Section B: entirely-dead companies (companies.csv + boards.csv) ---")
        parts.append("# Each block drops the company row + all its boards.")
        parts.append("")
        for r in company_rows:
            slug = r["company_slug"] or ""
            if "'" in slug or "," in slug:
                # Defensive: shell-unsafe slug. companies.csv slugs match
                # SLUG_RE today (lowercase alnum + hyphen), so this branch
                # is unreachable in practice — but a future malformed row
                # would otherwise produce a snippet that drops the wrong
                # rows. Bail loudly.
                parts.append(
                    f"# SKIP {slug}: company slug contains a quote or comma — drop manually."
                )
                continue
            parts.append(f"# {slug} ({r['company_name']}) — {r['total_boards']} dead boards")
            # Both CSVs use ``company_slug`` as the first column (anchored
            # at line start). companies.csv: ``slug,name,...``. boards.csv:
            # ``company_slug,board_slug,...``. ``grep -vE '^<slug>,'`` is a
            # fixed prefix match — slug shape (lowercase alnum + hyphen
            # only, per SLUG_RE) contains no regex metachars so the grep
            # is unambiguous.
            parts.append(
                f"grep -v -- '^{slug},' data/companies.csv > data/companies.csv.new "
                f"&& mv data/companies.csv.new data/companies.csv  "
                f"# remove {slug}"
            )
            parts.append(
                f"grep -v -- '^{slug},' data/boards.csv > data/boards.csv.new "
                f"&& mv data/boards.csv.new data/boards.csv  "
                f"# remove all boards for {slug}"
            )
            parts.append("")

    if not parts:
        return "# No retirement candidates found."
    return "\n".join(parts).rstrip() + "\n"


async def report_stale_boards(conn: asyncpg.Connection, *, days: int, fmt: str) -> str:
    """Convenience wrapper: query both sections + format in one call."""
    rows = await find_stale_boards(conn, days=days)
    company_rows = await find_dead_companies(conn, days=days)
    if fmt == "shell":
        return format_shell_snippets(rows, company_rows)
    return format_md(rows, company_rows)
