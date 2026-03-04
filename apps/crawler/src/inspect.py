"""CSV validation and diagnostic library.

Library functions for CSV validation and monitor/scraper diagnostics.
Used by workspace CLI commands. No standalone CLI entry point — use ``ws`` commands instead.
"""

from __future__ import annotations

import json
import time

import polars as pl
import structlog

from src.shared.constants import DATA_DIR, SLUG_RE, URL_RE

log = structlog.get_logger()

# Re-export for backward compatibility
_SLUG_RE = SLUG_RE
_URL_RE = URL_RE


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
    required_board_cols = {"company_slug", "board_slug", "board_url", "monitor_type"}
    actual_cols = set(boards.columns)
    missing = required_board_cols - actual_cols
    if missing:
        errors.append(ValidationError("boards.csv", None, f"Missing columns: {missing}"))
        return errors

    valid_monitor_types = {
        "ashby", "greenhouse", "lever", "sitemap",
        "nextdata", "dom", "api_sniffer",
    }
    valid_scraper_types = {
        "ashby_api", "greenhouse_api", "lever_api", "json-ld",
        "dom", "nextdata", "embedded", "api_sniffer", "",
    }
    url_only_monitors = {"sitemap", "dom"}
    board_urls: set[str] = set()
    board_slugs: set[str] = set()

    for i, row in enumerate(boards.iter_rows(named=True), start=2):
        company_slug = row.get("company_slug", "")
        board_slug = row.get("board_slug") or ""
        board_url = row.get("board_url", "")
        monitor_type = row.get("monitor_type", "")
        monitor_config = row.get("monitor_config") or ""
        scraper_type = row.get("scraper_type") or ""
        scraper_config = row.get("scraper_config") or ""

        if not company_slug:
            errors.append(ValidationError("boards.csv", i, "Empty company_slug"))
        elif company_slug not in slugs:
            errors.append(
                ValidationError(
                    "boards.csv",
                    i,
                    f"company_slug {company_slug!r} not in companies.csv",
                )
            )

        if not board_slug:
            errors.append(ValidationError("boards.csv", i, "Empty board_slug"))
        elif not _SLUG_RE.match(board_slug):
            errors.append(
                ValidationError("boards.csv", i, f"Invalid board_slug format: {board_slug!r}")
            )
        elif board_slug in board_slugs:
            errors.append(
                ValidationError("boards.csv", i, f"Duplicate board_slug: {board_slug!r}")
            )
        board_slugs.add(board_slug)

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
            errors.append(
                ValidationError(
                    "boards.csv",
                    i,
                    f"Invalid monitor_type: {monitor_type!r}",
                )
            )

        if scraper_type and scraper_type not in valid_scraper_types:
            errors.append(
                ValidationError(
                    "boards.csv",
                    i,
                    f"Invalid scraper_type: {scraper_type!r}",
                )
            )

        if monitor_type in url_only_monitors and not scraper_type:
            errors.append(
                ValidationError(
                    "boards.csv",
                    i,
                    f"monitor_type {monitor_type!r} requires a scraper_type",
                )
            )

        # api_sniffer without fields in config requires a scraper
        if monitor_type == "api_sniffer" and not scraper_type:
            has_fields = False
            if monitor_config:
                try:
                    cfg = json.loads(monitor_config)
                    has_fields = bool(cfg.get("fields"))
                except (json.JSONDecodeError, AttributeError):
                    pass
            if not has_fields:
                errors.append(
                    ValidationError(
                        "boards.csv",
                        i,
                        "monitor_type 'api_sniffer' without 'fields' in config requires a scraper_type",
                    )
                )

        # Validate JSON configs
        if monitor_config:
            try:
                json.loads(monitor_config)
            except json.JSONDecodeError:
                errors.append(ValidationError("boards.csv", i, "Invalid monitor_config JSON"))

        if scraper_config:
            try:
                json.loads(scraper_config)
            except json.JSONDecodeError:
                errors.append(ValidationError("boards.csv", i, "Invalid scraper_config JSON"))

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
            print("  Consider using 'nextdata' (Next.js) or 'dom' (Playwright) monitor type.")
    finally:
        await http.aclose()


async def probe_url(url: str) -> None:
    """Probe all monitor types against a URL and print a summary table."""
    from src.core.monitors import probe_all_monitors
    from src.shared.http import create_http_client

    http = create_http_client()
    try:
        print(f"Probing {url} ...\n")
        results = await probe_all_monitors(url, http)

        # Print table
        header = f"  {'Monitor':<14}{'Result':<8}Details"
        separator = "  " + "\u2500" * 60
        print(header)
        print(separator)
        for name, metadata, comment in results:
            symbol = "\u2713" if metadata is not None else "\u2717"
            print(f"  {name:<14}{symbol:<8}{comment}")
        print()
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
            print("  Consider using scraper_type: dom (with render: false for static pages)")
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
            print("  Rich data: yes (titles, descriptions, etc.)")
            sample = next(iter(result.jobs_by_url.values()))
            print(f"  Sample: {sample.title}")
        else:
            print("  Rich data: no (URLs only, needs scraper)")
            if result.urls:
                sample_url = next(iter(result.urls))
                print(f"  Sample URL: {sample_url}")

    finally:
        await http.aclose()


async def test_scraper(url: str, scraper_type: str, scraper_config_json: str | None) -> None:
    """Test scraping a single job URL and report results + timing."""
    from src.core.scrape import scrape_one
    from src.shared.http import create_http_client

    config = json.loads(scraper_config_json) if scraper_config_json else {}
    http = create_http_client()
    try:
        print(f"Scraping {url} (type: {scraper_type})...")
        start = time.monotonic()
        result = await scrape_one(url, scraper_type, config, http)
        elapsed = time.monotonic() - start

        print(f"  Scraped in {elapsed:.1f}s")
        if result.title:
            print(f"  Title: {result.title}")
        if result.locations:
            print(f"  Location: {', '.join(result.locations)}")
        if result.description:
            desc_preview = result.description[:120].replace("\n", " ")
            print(f"  Description: {desc_preview}...")
        if not result.title and not result.description:
            print("  Warning: no title or description extracted")
    finally:
        await http.aclose()


