"""CSV validation and diagnostic library.

Library functions for CSV validation and monitor/scraper diagnostics.
Used by workspace CLI commands. No standalone CLI entry point — use ``ws`` commands instead.
"""

from __future__ import annotations

import json
import time
from dataclasses import fields as dc_fields

from src.core.scrapers import _REGISTRY as SCRAPER_REGISTRY
from src.core.scrapers import JobContent
from src.shared.constants import LOGO_TYPES, SLUG_RE, URL_RE, get_data_dir
from src.shared.csv_io import read_csv
from src.workspace._compat import (
    all_monitor_types,
    api_monitor_types,
    auto_scraper_type,
)

_JOBCONTENT_FIELD_NAMES = frozenset(f.name for f in dc_fields(JobContent))

try:
    import structlog

    log = structlog.get_logger()
except ImportError:
    import logging

    log = logging.getLogger(__name__)

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

    companies_path = get_data_dir() / "companies.csv"
    boards_path = get_data_dir() / "boards.csv"

    if not companies_path.exists():
        errors.append(ValidationError("companies.csv", None, "File not found"))
        return errors

    if not boards_path.exists():
        errors.append(ValidationError("boards.csv", None, "File not found"))
        return errors

    # Load CSVs
    company_headers, company_rows = read_csv(companies_path)
    board_headers, board_rows = read_csv(boards_path)

    # Validate companies
    required_company_cols = {"slug", "name", "website"}
    actual_cols = set(company_headers)
    missing = required_company_cols - actual_cols
    if missing:
        errors.append(ValidationError("companies.csv", None, f"Missing columns: {missing}"))
        return errors

    slugs: set[str] = set()
    for i, row in enumerate(company_rows, start=2):
        slug = row.get("slug", "")
        name = row.get("name", "")
        website = row.get("website", "")
        logo_type = (row.get("logo_type") or "").strip()

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
        if logo_type and logo_type not in LOGO_TYPES:
            errors.append(
                ValidationError(
                    "companies.csv",
                    i,
                    f"Invalid logo_type: {logo_type!r} (expected one of {', '.join(LOGO_TYPES)})",
                )
            )

    # Validate boards
    required_board_cols = {"company_slug", "board_slug", "board_url", "monitor_type"}
    actual_cols = set(board_headers)
    missing = required_board_cols - actual_cols
    if missing:
        errors.append(ValidationError("boards.csv", None, f"Missing columns: {missing}"))
        return errors

    valid_monitor_types = all_monitor_types()
    valid_scraper_types = set(SCRAPER_REGISTRY) | {
        "",  # API monitors don't need a scraper
    }
    url_only_monitors = {"sitemap", "dom"}
    board_urls: set[str] = set()
    board_slugs: set[str] = set()

    for i, row in enumerate(board_rows, start=2):
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
            errors.append(ValidationError("boards.csv", i, f"Duplicate board_slug: {board_slug!r}"))
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
                        "api_sniffer without 'fields' in config requires a scraper_type",
                    )
                )

        # Monitors that don't return rich data and don't auto-configure a
        # scraper (personio, umantis, notion, nextdata without 'fields') need
        # an explicit scraper_type. Use 'skip' when the monitor returns full
        # job data, or name a scraper. Without this the runtime falls back to
        # json-ld, which silently produces empty descriptions.
        if (
            monitor_type
            and monitor_type in valid_monitor_types
            and not scraper_type
            and monitor_type not in url_only_monitors
            and monitor_type != "api_sniffer"
        ):
            mc_obj: dict | None = None
            if monitor_config:
                try:
                    parsed = json.loads(monitor_config)
                    if isinstance(parsed, dict):
                        mc_obj = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if auto_scraper_type(monitor_type, mc_obj) is None:
                errors.append(
                    ValidationError(
                        "boards.csv",
                        i,
                        (
                            f"monitor_type {monitor_type!r} requires explicit "
                            "scraper_type (use 'skip' when the monitor returns "
                            "rich data, or name a scraper)"
                        ),
                    )
                )

        # scraper_type=skip is only valid when the monitor returns full job
        # data inline (rich monitors, api_sniffer/nextdata with 'fields',
        # or personio whose XML feed includes descriptions). Pairing skip
        # with a URL-only monitor leaves descriptions empty silently — see
        # issue #2637 ("Broken descriptions from lazy scraper configurers").
        if scraper_type == "skip":
            mc_obj_skip: dict | None = None
            if monitor_config:
                try:
                    parsed = json.loads(monitor_config)
                    if isinstance(parsed, dict):
                        mc_obj_skip = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            skip_allowed = api_monitor_types() | {"personio"}
            is_skip_ok = monitor_type in skip_allowed or (
                monitor_type in ("api_sniffer", "nextdata")
                and bool((mc_obj_skip or {}).get("fields"))
            )
            if not is_skip_ok:
                errors.append(
                    ValidationError(
                        "boards.csv",
                        i,
                        (
                            f"scraper_type='skip' is invalid for monitor_type "
                            f"{monitor_type!r}: this monitor does not return "
                            "rich job data inline. Pick a scraper_type or "
                            "switch to a rich monitor."
                        ),
                    )
                )

        # Validate JSON configs
        if monitor_config:
            try:
                mc_obj = json.loads(monitor_config)
            except json.JSONDecodeError:
                errors.append(ValidationError("boards.csv", i, "Invalid monitor_config JSON"))
            else:
                # rescrape_policy controls whether workers re-scrape postings
                # after a successful scrape (see _RECORD_SCRAPE_SUCCESS).
                # Only "never" is supported today; absent means default cadence.
                if isinstance(mc_obj, dict) and "rescrape_policy" in mc_obj:
                    rp = mc_obj["rescrape_policy"]
                    if rp not in ("never",):
                        errors.append(
                            ValidationError(
                                "boards.csv",
                                i,
                                (
                                    f"Invalid rescrape_policy={rp!r} in monitor_config "
                                    "(supported: 'never')"
                                ),
                            )
                        )

                if (
                    isinstance(mc_obj, dict)
                    and "proxy" in mc_obj
                    and not isinstance(mc_obj["proxy"], bool)
                ):
                    errors.append(
                        ValidationError(
                            "boards.csv",
                            i,
                            f"'proxy' in monitor_config must be bool, got {mc_obj['proxy']!r}",
                        )
                    )

        if scraper_config:
            try:
                json.loads(scraper_config)
            except json.JSONDecodeError:
                errors.append(ValidationError("boards.csv", i, "Invalid scraper_config JSON"))

        # Validate fallback chain and enrich inside scraper_config
        if scraper_config:
            try:
                sc_obj = json.loads(scraper_config)
                if isinstance(sc_obj, dict):
                    if "proxy" in sc_obj and not isinstance(sc_obj["proxy"], bool):
                        errors.append(
                            ValidationError(
                                "boards.csv",
                                i,
                                f"'proxy' in scraper_config must be bool, got {sc_obj['proxy']!r}",
                            )
                        )

                    # Validate enrich key
                    enrich = sc_obj.get("enrich")
                    if enrich is not None:
                        if not isinstance(enrich, list):
                            errors.append(
                                ValidationError(
                                    "boards.csv",
                                    i,
                                    "'enrich' must be a list",
                                )
                            )
                        else:
                            for fname in enrich:
                                if fname not in _JOBCONTENT_FIELD_NAMES:
                                    errors.append(
                                        ValidationError(
                                            "boards.csv",
                                            i,
                                            f"Invalid enrich field: {fname!r}",
                                        )
                                    )
                            # Warn if enrich is used with URL-only monitor
                            if enrich and monitor_type in url_only_monitors:
                                errors.append(
                                    ValidationError(
                                        "boards.csv",
                                        i,
                                        f"'enrich' is unnecessary with URL-only monitor"
                                        f" {monitor_type!r}"
                                        " (scraper already runs for all fields)",
                                    )
                                )

                fb = sc_obj.get("fallback") if isinstance(sc_obj, dict) else None
                depth = 0
                while isinstance(fb, dict) and depth < 10:
                    fb_type = fb.get("type", "")
                    if fb_type and fb_type not in valid_scraper_types:
                        errors.append(
                            ValidationError(
                                "boards.csv",
                                i,
                                f"Invalid fallback scraper type: {fb_type!r}",
                            )
                        )
                    fb_fields = fb.get("fields")
                    if fb_fields is not None:
                        if not isinstance(fb_fields, list):
                            errors.append(
                                ValidationError(
                                    "boards.csv",
                                    i,
                                    "Fallback 'fields' must be a list",
                                )
                            )
                        else:
                            for fname in fb_fields:
                                if fname not in _JOBCONTENT_FIELD_NAMES:
                                    errors.append(
                                        ValidationError(
                                            "boards.csv",
                                            i,
                                            f"Invalid fallback field: {fname!r}",
                                        )
                                    )
                    fb_cfg = fb.get("config")
                    if (
                        isinstance(fb_cfg, dict)
                        and "proxy" in fb_cfg
                        and not isinstance(fb_cfg["proxy"], bool)
                    ):
                        errors.append(
                            ValidationError(
                                "boards.csv",
                                i,
                                f"'proxy' in fallback config must be bool, got {fb_cfg['proxy']!r}",
                            )
                        )
                    fb = fb_cfg.get("fallback") if isinstance(fb_cfg, dict) else None
                    depth += 1
            except json.JSONDecodeError:
                pass  # Already reported above

    # Validate occupation_domains.csv
    domains_path = get_data_dir() / "occupation_domains.csv"
    domain_slugs: set[str] = set()
    if domains_path.exists():
        dom_headers, dom_rows = read_csv(domains_path)
        required_dom_cols = {"slug", "en"}
        if not required_dom_cols.issubset(set(dom_headers)):
            errors.append(
                ValidationError(
                    "occupation_domains.csv",
                    None,
                    f"Missing columns: {required_dom_cols - set(dom_headers)}",
                )
            )
        else:
            for i, row in enumerate(dom_rows, start=2):
                dom_slug = row.get("slug", "")
                if not dom_slug:
                    errors.append(ValidationError("occupation_domains.csv", i, "Empty slug"))
                elif not _SLUG_RE.match(dom_slug):
                    errors.append(
                        ValidationError("occupation_domains.csv", i, f"Invalid slug: {dom_slug!r}")
                    )
                elif dom_slug in domain_slugs:
                    errors.append(
                        ValidationError(
                            "occupation_domains.csv", i, f"Duplicate slug: {dom_slug!r}"
                        )
                    )
                domain_slugs.add(dom_slug)

    # Validate occupations.csv domain references
    occ_path = get_data_dir() / "occupations.csv"
    if occ_path.exists() and domain_slugs:
        occ_headers, occ_rows = read_csv(occ_path)
        if "domain" in occ_headers:
            for i, row in enumerate(occ_rows, start=2):
                domain_ref = (row.get("domain") or "").strip()
                if domain_ref and domain_ref not in domain_slugs:
                    errors.append(
                        ValidationError(
                            "occupations.csv",
                            i,
                            f"domain {domain_ref!r} not in occupation_domains.csv",
                        )
                    )

    # Validate company_descriptions.csv
    descs_path = get_data_dir() / "company_descriptions.csv"
    if descs_path.exists():
        desc_headers, desc_rows = read_csv(descs_path)
        for i, row in enumerate(desc_rows, start=2):
            desc_slug = row.get("slug", "")
            if desc_slug and desc_slug not in slugs:
                errors.append(
                    ValidationError(
                        "company_descriptions.csv",
                        i,
                        f"slug {desc_slug!r} not in companies.csv",
                    )
                )

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
