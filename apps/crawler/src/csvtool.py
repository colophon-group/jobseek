"""CSV management library for adding, updating, and removing company/board rows.

Library functions used by workspace CLI commands.
No standalone CLI entry point — use ``ws`` commands instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.shared.constants import DATA_DIR, SLUG_RE
from src.shared.csv_io import read_csv as _read_csv
from src.shared.csv_io import write_csv as _write_csv

_SLUG_RE = SLUG_RE


def _company_slugs(path: Path) -> set[str]:
    """Return the set of slugs in companies.csv."""
    _, rows = _read_csv(path)
    return {r["slug"] for r in rows}


def company_add(
    slug: str,
    *,
    name: str | None = None,
    website: str | None = None,
    logo_url: str | None = None,
    icon_url: str | None = None,
) -> None:
    """Add a new company or update an existing one."""
    if not _SLUG_RE.match(slug):
        print(f"Error: invalid slug format: {slug!r}", file=sys.stderr)
        sys.exit(1)

    companies_path = DATA_DIR / "companies.csv"
    headers, rows = _read_csv(companies_path)

    target = None
    for row in rows:
        if row["slug"] == slug:
            target = row
            break

    if target is None:
        # Create new row
        new_row = {col: "" for col in headers}
        new_row["slug"] = slug
        if name is not None:
            new_row["name"] = name
        if website is not None:
            new_row["website"] = website
        if logo_url is not None:
            new_row["logo_url"] = logo_url
        if icon_url is not None:
            new_row["icon_url"] = icon_url
        rows.append(new_row)
        _write_csv(companies_path, headers, rows)

        fields = {k: v for k, v in new_row.items() if v and k != "slug"}
        extra = f" ({', '.join(f'{k}={v!r}' for k, v in fields.items())})" if fields else ""
        print(f"Added company {slug!r}{extra}")
    else:
        # Update existing row
        updates: dict[str, str] = {}
        if name is not None:
            updates["name"] = name
        if website is not None:
            updates["website"] = website
        if logo_url is not None:
            updates["logo_url"] = logo_url
        if icon_url is not None:
            updates["icon_url"] = icon_url

        if not updates:
            print(f"Company {slug!r} already exists, nothing to update.", file=sys.stderr)
            sys.exit(1)

        target.update(updates)
        _write_csv(companies_path, headers, rows)

        fields = ", ".join(f"{k}={v!r}" for k, v in updates.items())
        print(f"Updated company {slug!r}: {fields}")


def company_del(slug: str) -> None:
    """Remove a company and all its boards."""
    companies_path = DATA_DIR / "companies.csv"
    boards_path = DATA_DIR / "boards.csv"

    headers, rows = _read_csv(companies_path)
    original_len = len(rows)
    rows = [r for r in rows if r["slug"] != slug]

    if len(rows) == original_len:
        print(f"Error: slug {slug!r} not found in companies.csv", file=sys.stderr)
        sys.exit(1)

    _write_csv(companies_path, headers, rows)

    # Remove associated boards
    b_headers, b_rows = _read_csv(boards_path)
    b_original_len = len(b_rows)
    b_rows = [r for r in b_rows if r["company_slug"] != slug]
    _write_csv(boards_path, b_headers, b_rows)

    removed_boards = b_original_len - len(b_rows)
    board_msg = f" and {removed_boards} board(s)" if removed_boards else ""
    print(f"Removed company {slug!r}{board_msg}")


def board_add(
    slug: str,
    *,
    board_slug: str | None = None,
    board_url: str | None = None,
    monitor_type: str | None = None,
    monitor_config: str | None = None,
    scraper_type: str | None = None,
    scraper_config: str | None = None,
) -> None:
    """Add a new board or update an existing one."""
    companies_path = DATA_DIR / "companies.csv"
    boards_path = DATA_DIR / "boards.csv"

    if slug not in _company_slugs(companies_path):
        print(f"Error: slug {slug!r} not found in companies.csv", file=sys.stderr)
        sys.exit(1)

    headers, rows = _read_csv(boards_path)

    # Look for existing board to update (by board_url or board_slug)
    target = None
    if board_url:
        for row in rows:
            if row["company_slug"] == slug and row["board_url"] == board_url:
                target = row
                break
    elif board_slug:
        for row in rows:
            if row.get("board_slug") == board_slug:
                target = row
                break

    if target is not None:
        # Update existing board
        updates: dict[str, str] = {}
        if board_slug is not None:
            updates["board_slug"] = board_slug
        if monitor_type is not None:
            updates["monitor_type"] = monitor_type
        if monitor_config is not None:
            updates["monitor_config"] = monitor_config
        if scraper_type is not None:
            updates["scraper_type"] = scraper_type
        if scraper_config is not None:
            updates["scraper_config"] = scraper_config

        if not updates:
            print(f"Board {board_url!r} already exists, nothing to update.", file=sys.stderr)
            sys.exit(1)

        target.update(updates)
        _write_csv(boards_path, headers, rows)

        fields = ", ".join(f"{k}={v!r}" for k, v in updates.items())
        print(f"Updated board {board_url or board_slug!r}: {fields}")
    else:
        # Create new board
        if not board_url:
            print("Error: --board-url is required when adding a new board", file=sys.stderr)
            sys.exit(1)

        new_row = {col: "" for col in headers}
        new_row["company_slug"] = slug
        if board_slug is not None:
            new_row["board_slug"] = board_slug
        new_row["board_url"] = board_url
        if monitor_type is not None:
            new_row["monitor_type"] = monitor_type
        if monitor_config is not None:
            new_row["monitor_config"] = monitor_config
        if scraper_type is not None:
            new_row["scraper_type"] = scraper_type
        if scraper_config is not None:
            new_row["scraper_config"] = scraper_config
        rows.append(new_row)

        _write_csv(boards_path, headers, rows)
        print(f"Added board for {slug!r}: {board_url} (monitor: {monitor_type or ''})")


def board_del(slug: str, *, board_url: str | None = None) -> None:
    """Remove a board row."""
    boards_path = DATA_DIR / "boards.csv"
    headers, rows = _read_csv(boards_path)

    if board_url:
        original_len = len(rows)
        rows = [r for r in rows if not (r["company_slug"] == slug and r["board_url"] == board_url)]
        if len(rows) == original_len:
            print(
                f"Error: board ({slug!r}, {board_url!r}) not found in boards.csv",
                file=sys.stderr,
            )
            sys.exit(1)
        _write_csv(boards_path, headers, rows)
        print(f"Removed board {board_url!r} for {slug!r}")
    else:
        original_len = len(rows)
        rows = [r for r in rows if r["company_slug"] != slug]
        removed = original_len - len(rows)
        if removed == 0:
            print(f"Error: no boards found for {slug!r}", file=sys.stderr)
            sys.exit(1)
        _write_csv(boards_path, headers, rows)
        print(f"Removed {removed} board(s) for {slug!r}")
