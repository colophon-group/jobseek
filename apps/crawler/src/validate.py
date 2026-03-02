"""CSV validation and diagnostic tools.

Usage:
    uv run python -m src.validate                           # validate CSVs
    uv run python -m src.validate --detect <url>            # auto-detect monitor type
    uv run python -m src.validate --probe-jsonld <url>      # check for JSON-LD
    uv run python -m src.validate --test-monitor <slug> <board-url>  # test crawl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import polars as pl
import structlog

from src.shared.logging import setup_logging

log = structlog.get_logger()

DATA_DIR = Path(__file__).parent.parent / "data"

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_URL_RE = re.compile(r"^https?://")


class ValidationError:
    def __init__(self, file: str, row: int | None, message: str):
        self.file = file
        self.row = row
        self.message = message

    def __str__(self):
        if self.row is not None:
            return f"{self.file}:{self.row}: {self.message}"
        return f"{self.file}: {self.message}"


def validate_csvs() -> list[ValidationError]:
    """Validate companies.csv and boards.csv. Returns list of errors."""
    errors: list[ValidationError] = []

    companies_path = DATA_DIR / "companies.csv"
    boards_path = DATA_DIR / "boards.csv"

    if not companies_path.exists():
        errors.append(ValidationError("companies.csv", None, "File not found"))
        return errors

    if not boards_path.exists():
        errors.append(ValidationError("boards.csv", None, "File not found"))
        return errors

    # Load CSVs
    companies = pl.read_csv(companies_path, infer_schema_length=0)
    boards = pl.read_csv(boards_path, infer_schema_length=0)

    # Validate companies
    required_company_cols = {"slug", "name", "website"}
    actual_cols = set(companies.columns)
    missing = required_company_cols - actual_cols
    if missing:
        errors.append(ValidationError("companies.csv", None, f"Missing columns: {missing}"))
        return errors

    slugs: set[str] = set()
    for i, row in enumerate(companies.iter_rows(named=True), start=2):
        slug = row.get("slug", "")
        name = row.get("name", "")
        website = row.get("website", "")

        if not slug:
            errors.append(ValidationError("companies.csv", i, "Empty slug"))
        elif not _SLUG_RE.match(slug):
            errors.append(ValidationError("companies.csv", i, f"Invalid slug format: {slug!r}"))
        elif slug in slugs:
            errors.append(ValidationError("companies.csv", i, f"Duplicate slug: {slug!r}"))
        slugs.add(slug)

        if not name:
            errors.append(ValidationError("companies.csv", i, "Empty name"))

        if website and not _URL_RE.match(website):
            errors.append(ValidationError("companies.csv", i, f"Invalid URL: {website!r}"))

    # Validate boards
    required_board_cols = {"company_slug", "board_url", "monitor_type"}
    actual_cols = set(boards.columns)
    missing = required_board_cols - actual_cols
    if missing:
        errors.append(ValidationError("boards.csv", None, f"Missing columns: {missing}"))
        return errors

    valid_monitor_types = {"greenhouse", "lever", "sitemap", "discover"}
    valid_scraper_types = {"greenhouse_api", "lever_api", "json-ld", "html", "browser", ""}
    url_only_monitors = {"sitemap", "discover"}
    board_urls: set[str] = set()

    for i, row in enumerate(boards.iter_rows(named=True), start=2):
        company_slug = row.get("company_slug", "")
        board_url = row.get("board_url", "")
        monitor_type = row.get("monitor_type", "")
        monitor_config = row.get("monitor_config") or ""
        scraper_type = row.get("scraper_type") or ""
        scraper_config = row.get("scraper_config") or ""

        if not company_slug:
            errors.append(ValidationError("boards.csv", i, "Empty company_slug"))
        elif company_slug not in slugs:
            errors.append(ValidationError("boards.csv", i, f"company_slug {company_slug!r} not in companies.csv"))

        if not board_url:
            errors.append(ValidationError("boards.csv", i, "Empty board_url"))
        elif not _URL_RE.match(board_url):
            errors.append(ValidationError("boards.csv", i, f"Invalid board_url: {board_url!r}"))
        elif board_url in board_urls:
            errors.append(ValidationError("boards.csv", i, f"Duplicate board_url: {board_url!r}"))
        board_urls.add(board_url)

        if not monitor_type:
            errors.append(ValidationError("boards.csv", i, "Empty monitor_type"))
        elif monitor_type not in valid_monitor_types:
            errors.append(ValidationError("boards.csv", i, f"Invalid monitor_type: {monitor_type!r}"))

        if scraper_type and scraper_type not in valid_scraper_types:
            errors.append(ValidationError("boards.csv", i, f"Invalid scraper_type: {scraper_type!r}"))

        if monitor_type in url_only_monitors and not scraper_type:
            errors.append(ValidationError("boards.csv", i, f"monitor_type {monitor_type!r} requires a scraper_type"))

        # Validate JSON configs
        if monitor_config:
            try:
                json.loads(monitor_config)
            except json.JSONDecodeError:
                errors.append(ValidationError("boards.csv", i, f"Invalid monitor_config JSON"))

        if scraper_config:
            try:
                json.loads(scraper_config)
            except json.JSONDecodeError:
                errors.append(ValidationError("boards.csv", i, f"Invalid scraper_config JSON"))

    return errors


async def detect_monitor_type(url: str) -> None:
    """Auto-detect the best monitor type for a URL."""
    from src.core.monitors import detect_monitor_type as detect
    from src.shared.http import create_http_client

    http = create_http_client()
    try:
        print(f"Detecting monitor type for: {url}")
        result = await detect(url, http)
        if result:
            name, metadata = result
            print(f"  Monitor type: {name}")
            if metadata:
                print(f"  Metadata: {json.dumps(metadata)}")
        else:
            print("  No monitor type detected automatically.")
            print("  Consider using 'discover' monitor type (requires Playwright).")
    finally:
        await http.aclose()


async def probe_jsonld(url: str) -> None:
    """Check if a URL has JSON-LD JobPosting data."""
    from src.core.scrapers.jsonld import probe
    from src.shared.http import create_http_client

    http = create_http_client()
    try:
        print(f"Probing for JSON-LD at: {url}")
        found = await probe(url, http)
        if found:
            print("  JSON-LD JobPosting found! Use scraper_type: json-ld")
        else:
            print("  No JSON-LD JobPosting found.")
            print("  Consider using scraper_type: html (with CSS selectors)")
    finally:
        await http.aclose()


async def test_monitor(slug: str, board_url: str) -> None:
    """Test crawl a board and report results."""
    from src.core.monitor import monitor_one
    from src.core.monitors import detect_monitor_type as detect
    from src.shared.http import create_http_client

    http = create_http_client()
    try:
        # Auto-detect monitor type
        print(f"Detecting monitor type for: {board_url}")
        detection = await detect(board_url, http)
        if not detection:
            print("  Could not auto-detect monitor type.")
            return

        monitor_type, metadata = detection
        print(f"  Monitor type: {monitor_type}")

        # Run monitor
        print(f"Crawling {board_url}...")
        start = time.monotonic()
        result = await monitor_one(board_url, monitor_type, metadata, http)
        elapsed = time.monotonic() - start

        print(f"  Found {len(result.urls)} jobs in {elapsed:.1f}s")
        if result.jobs_by_url:
            print(f"  Rich data: yes (titles, descriptions, etc.)")
            sample = next(iter(result.jobs_by_url.values()))
            print(f"  Sample: {sample.title}")
        else:
            print(f"  Rich data: no (URLs only, needs scraper)")
            if result.urls:
                sample_url = next(iter(result.urls))
                print(f"  Sample URL: {sample_url}")

        # Auto-merge guidance
        count = len(result.urls)
        if count < 500:
            print(f"\n  Label: auto-merge (< 500 jobs)")
        elif count < 5000:
            print(f"\n  Label: review-size (500-5000 jobs)")
        else:
            print(f"\n  Label: review-load (> 5000 jobs)")

    finally:
        await http.aclose()


def main():
    setup_logging("INFO")

    parser = argparse.ArgumentParser(description="CSV validation and diagnostics")
    parser.add_argument("--detect", metavar="URL", help="Auto-detect monitor type for a URL")
    parser.add_argument("--probe-jsonld", metavar="URL", help="Check if URL has JSON-LD JobPosting")
    parser.add_argument("--test-monitor", nargs=2, metavar=("SLUG", "BOARD_URL"), help="Test crawl a board")
    args = parser.parse_args()

    if args.detect:
        asyncio.run(detect_monitor_type(args.detect))
        return

    if args.probe_jsonld:
        asyncio.run(probe_jsonld(args.probe_jsonld))
        return

    if args.test_monitor:
        slug, board_url = args.test_monitor
        asyncio.run(test_monitor(slug, board_url))
        return

    # Default: validate CSVs
    errors = validate_csvs()
    if errors:
        print(f"Validation failed with {len(errors)} error(s):\n")
        for error in errors:
            print(f"  {error}")
        sys.exit(1)
    else:
        print("Validation passed.")


if __name__ == "__main__":
    main()
