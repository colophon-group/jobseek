"""CSV management library for adding, updating, and removing company/board rows.

Library functions used by workspace CLI commands.
No standalone CLI entry point — use ``ws`` commands instead.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from src.shared.constants import SLUG_RE, get_data_dir
from src.shared.csv_io import read_csv as _read_csv
from src.shared.csv_io import write_csv as _write_csv
from src.shared.output import tty_message
from src.workspace.errors import (
    BoardNotFoundError,
    CsvToolError,
    InvalidSlugError,
    MissingRequiredFieldError,
    NothingToUpdateError,
    SlugNotFoundError,
)

_SLUG_RE = SLUG_RE
log = structlog.get_logger()


def _sort_company_rows(rows: list[dict[str, str]]) -> None:
    rows.sort(key=lambda row: row.get("slug", ""))


def _sort_board_rows(rows: list[dict[str, str]]) -> None:
    rows.sort(key=lambda row: (row.get("company_slug", ""), row.get("board_slug", "")))


def _sort_description_rows(rows: list[dict[str, str]]) -> None:
    rows.sort(key=lambda row: row.get("slug", ""))


def sort_csvs() -> None:
    """Restore canonical ordering for every company registry CSV."""
    companies_path = get_data_dir() / "companies.csv"
    boards_path = get_data_dir() / "boards.csv"

    headers, rows = _read_csv(companies_path)
    _sort_company_rows(rows)
    _write_csv(companies_path, headers, rows)

    b_headers, b_rows = _read_csv(boards_path)
    _sort_board_rows(b_rows)
    _write_csv(boards_path, b_headers, b_rows)

    descs_path = get_data_dir() / "company_descriptions.csv"
    if descs_path.exists():
        d_headers, d_rows = _read_csv(descs_path)
        _sort_description_rows(d_rows)
        _write_csv(descs_path, d_headers, d_rows)


def _company_slugs(path: Path) -> set[str]:
    """Return the set of slugs in companies.csv."""
    _, rows = _read_csv(path)
    return {r["slug"] for r in rows}


def company_description_set(slug: str, locale: str, description: str) -> None:
    """Set a company description in company_descriptions.csv."""
    if not _SLUG_RE.match(slug):
        raise InvalidSlugError(f"Invalid slug format: {slug!r}")

    descs_path = get_data_dir() / "company_descriptions.csv"

    if descs_path.exists():
        headers, rows = _read_csv(descs_path)
    else:
        headers = ["slug", locale]
        rows = []

    # Ensure locale column exists
    if locale not in headers:
        headers.append(locale)
        for row in rows:
            row[locale] = ""

    # Find or create row
    target = None
    for row in rows:
        if row["slug"] == slug:
            target = row
            break

    if target is None:
        new_row = {col: "" for col in headers}
        new_row["slug"] = slug
        new_row[locale] = description
        rows.append(new_row)
    else:
        target[locale] = description

    _sort_description_rows(rows)
    _write_csv(descs_path, headers, rows)
    log.info("csvtool.company_description.set", slug=slug, locale=locale)
    tty_message(f"Set {locale} description for {slug!r}")


def company_add(
    slug: str,
    *,
    name: str | None = None,
    website: str | None = None,
    logo_url: str | None = None,
    icon_url: str | None = None,
    logo_type: str | None = None,
    industry: int | None = None,
    employee_count_range: int | None = None,
    founded_year: int | None = None,
    extras: str | None = None,
) -> None:
    """Add a new company or update an existing one."""
    if not _SLUG_RE.match(slug):
        raise InvalidSlugError(f"Invalid slug format: {slug!r}")

    companies_path = get_data_dir() / "companies.csv"
    headers, rows = _read_csv(companies_path)

    # Build updates dict from all non-None arguments
    field_map: dict[str, str] = {}
    if name is not None:
        field_map["name"] = name
    if website is not None:
        field_map["website"] = website
    if logo_url is not None:
        field_map["logo_url"] = logo_url
    if icon_url is not None:
        field_map["icon_url"] = icon_url
    if logo_type is not None:
        field_map["logo_type"] = logo_type
    if industry is not None:
        field_map["industry"] = str(industry)
    if employee_count_range is not None:
        field_map["employee_count_range"] = str(employee_count_range)
    if founded_year is not None:
        field_map["founded_year"] = str(founded_year)
    if extras is not None:
        field_map["extras"] = extras

    target = None
    for row in rows:
        if row["slug"] == slug:
            target = row
            break

    if target is None:
        # Create new row
        new_row = {col: "" for col in headers}
        new_row["slug"] = slug
        new_row.update(field_map)
        rows.append(new_row)
        _sort_company_rows(rows)
        _write_csv(companies_path, headers, rows)

        fields = [k for k, v in new_row.items() if v and k != "slug"]
        log.info("csvtool.company.added", slug=slug, fields=fields)
        extra = f" ({', '.join(fields)})" if fields else ""
        tty_message(f"Added company {slug!r}{extra}")
    else:
        # Update existing row
        if not field_map:
            raise NothingToUpdateError(f"Company {slug!r} already exists, nothing to update")

        target.update(field_map)
        _sort_company_rows(rows)
        _write_csv(companies_path, headers, rows)

        log.info("csvtool.company.updated", slug=slug, fields=list(field_map))
        fields = ", ".join(field_map)
        tty_message(f"Updated company {slug!r}: {fields}")


def company_del(slug: str) -> None:
    """Remove a company and all its boards."""
    companies_path = get_data_dir() / "companies.csv"
    boards_path = get_data_dir() / "boards.csv"

    headers, rows = _read_csv(companies_path)
    original_len = len(rows)
    rows = [r for r in rows if r["slug"] != slug]

    if len(rows) == original_len:
        raise SlugNotFoundError(f"Slug {slug!r} not found in companies.csv")

    _sort_company_rows(rows)
    _write_csv(companies_path, headers, rows)

    # Remove associated boards
    b_headers, b_rows = _read_csv(boards_path)
    b_original_len = len(b_rows)
    b_rows = [r for r in b_rows if r["company_slug"] != slug]
    _sort_board_rows(b_rows)
    _write_csv(boards_path, b_headers, b_rows)

    removed_boards = b_original_len - len(b_rows)
    log.info("csvtool.company.removed", slug=slug, removed_boards=removed_boards)
    board_msg = f" and {removed_boards} board(s)" if removed_boards else ""
    tty_message(f"Removed company {slug!r}{board_msg}")


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
    companies_path = get_data_dir() / "companies.csv"
    boards_path = get_data_dir() / "boards.csv"

    if slug not in _company_slugs(companies_path):
        raise SlugNotFoundError(f"Slug {slug!r} not found in companies.csv")

    headers, rows = _read_csv(boards_path)

    # Resolve both stable identifiers. A resumed workspace can intentionally
    # change a board URL while preserving its slug, so preferring board_url
    # alone would append a duplicate row on submit.
    url_matches = [
        row
        for row in rows
        if board_url and row["company_slug"] == slug and row["board_url"] == board_url
    ]
    slug_matches = [row for row in rows if board_slug and row.get("board_slug") == board_slug]
    if len(url_matches) > 1:
        raise CsvToolError(f"Board URL matches multiple rows for {slug!r}: {board_url!r}")
    if len(slug_matches) > 1:
        raise CsvToolError(f"Board slug matches multiple rows: {board_slug!r}")

    url_target = url_matches[0] if url_matches else None
    slug_target = slug_matches[0] if slug_matches else None
    if slug_target is not None and slug_target["company_slug"] != slug:
        raise CsvToolError(
            f"Board slug {board_slug!r} belongs to company "
            f"{slug_target['company_slug']!r}, not {slug!r}"
        )
    if url_target is not None and slug_target is not None and url_target is not slug_target:
        raise CsvToolError(
            f"Board identifiers refer to different rows: {board_slug!r}, {board_url!r}"
        )
    target = slug_target or url_target

    if target is not None:
        # Update existing board
        updates: dict[str, str] = {}
        if board_slug is not None:
            updates["board_slug"] = board_slug
        if board_url is not None and board_url != target.get("board_url"):
            updates["board_url"] = board_url
        if monitor_type is not None:
            updates["monitor_type"] = monitor_type
        if monitor_config is not None:
            updates["monitor_config"] = monitor_config
        if scraper_type is not None:
            updates["scraper_type"] = scraper_type
        if scraper_config is not None:
            updates["scraper_config"] = scraper_config

        if not updates:
            raise NothingToUpdateError(f"Board {board_url!r} already exists, nothing to update")

        target.update(updates)
        _sort_board_rows(rows)
        _write_csv(boards_path, headers, rows)

        log.info(
            "csvtool.board.updated",
            slug=slug,
            board=board_url or board_slug,
            fields=list(updates),
        )
        fields = ", ".join(f"{k}={v!r}" for k, v in updates.items())
        tty_message(f"Updated board {board_url or board_slug!r}: {fields}")
    else:
        # Create new board
        if not board_url:
            raise MissingRequiredFieldError("board_url is required when adding a new board")

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

        _sort_board_rows(rows)
        _write_csv(boards_path, headers, rows)
        log.info(
            "csvtool.board.added",
            slug=slug,
            board_url=board_url,
            monitor_type=monitor_type or "",
        )
        tty_message(f"Added board for {slug!r}: {board_url} (monitor: {monitor_type or ''})")


def board_del(slug: str, *, board_url: str | None = None) -> None:
    """Remove a board row."""
    boards_path = get_data_dir() / "boards.csv"
    headers, rows = _read_csv(boards_path)

    if board_url:
        original_len = len(rows)
        rows = [r for r in rows if not (r["company_slug"] == slug and r["board_url"] == board_url)]
        if len(rows) == original_len:
            raise BoardNotFoundError(f"Board ({slug!r}, {board_url!r}) not found in boards.csv")
        _sort_board_rows(rows)
        _write_csv(boards_path, headers, rows)
        log.info("csvtool.board.removed", slug=slug, board_url=board_url, removed=1)
        tty_message(f"Removed board {board_url!r} for {slug!r}")
    else:
        original_len = len(rows)
        rows = [r for r in rows if r["company_slug"] != slug]
        removed = original_len - len(rows)
        if removed == 0:
            raise BoardNotFoundError(f"No boards found for {slug!r}")
        _sort_board_rows(rows)
        _write_csv(boards_path, headers, rows)
        log.info("csvtool.board.removed", slug=slug, board_url=None, removed=removed)
        tty_message(f"Removed {removed} board(s) for {slug!r}")
