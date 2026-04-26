"""Surface boards that are ripe for retirement (CSV-only PR candidates).

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
description and ``sed`` snippets the operator can paste into a terminal
to remove the rows from ``boards.csv``.

A board is a retirement candidate when ALL of:

- ``board_status IN ('disabled', 'gone')``
- ``last_success_at`` is NULL (never succeeded) OR older than ``--days``
  (default 14). A never-succeeded disabled board is the strongest
  candidate, not the weakest.
- Zero active postings remain (``job_posting.is_active = true``)
- The company has at least one other live board (``board_status IN
  ('active', 'suspect')``, ``is_enabled = true``) so retirement won't
  orphan it. Matches the live-board definition used by the dispatcher
  (``queries/monitor.py``).
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


async def find_stale_boards(conn: asyncpg.Connection, *, days: int) -> list[dict[str, Any]]:
    """Run the candidate-finding query and return row dicts.

    Caller is responsible for the connection lifetime.
    """
    rows = await conn.fetch(_QUERY, days)
    return [dict(r) for r in rows]


def format_md(rows: list[dict[str, Any]]) -> str:
    """Render rows as a markdown table for a PR description.

    Returns a friendly "no candidates" line when the result set is empty.
    """
    if not rows:
        return "No retirement candidates found."

    header = (
        "| Company | Board slug | Crawler | Status | Last success "
        "| Stale (days) | Healthy siblings |"
    )
    sep = "|---|---|---|---|---|---:|---:|"
    lines = [header, sep]
    for r in rows:
        last_success = (
            r["last_success_at"].strftime("%Y-%m-%d %H:%M UTC")
            if r["last_success_at"]
            else "(never)"
        )
        lines.append(
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
    return "\n".join(lines)


def format_shell_snippets(rows: list[dict[str, Any]]) -> str:
    """Render `grep -vF` shell snippets that delete the matching rows from boards.csv.

    Targets the ``board_url`` (CSV-unique within the file, enforced by the
    ``UNIQUE`` constraint on ``job_board.board_url``) using a fixed-string
    match, NOT regex — URLs contain `.` which would otherwise be a regex
    metachar and over-match. The pattern ``,<url>,`` anchors on the
    surrounding CSV separators so a URL that happens to be a substring of a
    longer URL on another row does not collide.

    The anchoring is correct for today's `boards.csv`, where the third
    column (``board_url``) is always a bare URL between literal commas and
    no row has a quoted cell. If a future row has a comma inside the URL
    column the snippet will silently no-op; the operator should notice via
    an empty `git diff` and fall back to a manual edit.

    Each snippet rewrites the file in place via a temp-file rename so the
    operator can review with `git diff`.
    """
    if not rows:
        return "# No retirement candidates found."

    lines = [
        "# Run from apps/crawler/ to drop the rows from boards.csv.",
        "# Inspect the diff afterwards (`git diff data/boards.csv`) and",
        "# open a retirement PR with `git checkout -b fix-crawler/retire-...`.",
        "",
    ]
    for r in rows:
        url = r["board_url"] or ""
        # Single-quote the URL for shell safety; embedded single-quotes
        # would need escaping, but board URLs in this codebase don't
        # contain quotes.
        if "'" in url:
            # Defensive: skip rows we can't safely shell-quote.
            lines.append(
                f"# SKIP {r['company_slug']} {r['board_slug']}: board_url "
                "contains a single quote — drop the row manually."
            )
            continue
        lines.append(
            f"grep -vF -- ',{url},' data/boards.csv > data/boards.csv.new "
            f"&& mv data/boards.csv.new data/boards.csv  "
            f"# {r['company_slug']} {r['board_slug']} ({r['board_status']})"
        )
    return "\n".join(lines)


async def report_stale_boards(conn: asyncpg.Connection, *, days: int, fmt: str) -> str:
    """Convenience wrapper: query + format in one call."""
    rows = await find_stale_boards(conn, days=days)
    if fmt == "shell":
        return format_shell_snippets(rows)
    return format_md(rows)
