"""LinkedIn public guest-jobs listing monitor.

LinkedIn company pages may be the only public hiring surface for small
companies.  The normal company page returns an anti-bot response to static
clients, but LinkedIn's logged-out jobs experience exposes a server-rendered
listing endpoint used by its own public search page.

The monitor returns rich summaries (title, location, posting date) and leaves
description hydration to the paired ``linkedin`` scraper on the daily scrape
schedule.  This keeps monitor cycles cheap and avoids fetching every detail
page whenever existence is checked.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser, LexborNode

from src.core.location_resolve import _ISO3_TO_COUNTRY
from src.core.monitors import DiscoveredJob, register
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

# LinkedIn's public guest endpoint currently returns ten cards per request.
# Pagination must advance by the provider page size: treating it as 25 both
# stops on the first ten-card page and skips offsets 10-24.
PAGE_SIZE = 10
MAX_JOBS = 1_000
_PAGE_DELAY_S = 1.0
_RETRY_ATTEMPTS = 4
_RETRY_BASE_DELAY_S = 1.5
_WORLDWIDE_LOCATION = "Worldwide"
_REQUEST_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}

_COMPANY_PATH_RE = re.compile(r"^/company/([^/?#]+)/jobs/?$", re.IGNORECASE)
_JOB_URN_RE = re.compile(r"urn:li:jobPosting:(\d+)")
_COMPANY_ID_RE = re.compile(r"facetCurrentCompany(?:%3D|=)(\d+)", re.IGNORECASE)


@dataclass(slots=True)
class _ListingJob:
    job_id: str
    url: str
    title: str | None
    locations: list[str] | None
    date_posted: str | None
    company_slug: str | None
    location_country_code: str | None = None

    def discovered(self, company_id: str) -> DiscoveredJob:
        metadata: dict[str, str] = {
            "job_id": self.job_id,
            "linkedin_company_id": company_id,
        }
        if self.company_slug:
            metadata["linkedin_company_slug"] = self.company_slug
        if self.location_country_code:
            metadata["location_country_code"] = self.location_country_code
        return DiscoveredJob(
            url=self.url,
            title=self.title,
            locations=self.locations,
            date_posted=self.date_posted,
            metadata=metadata,
        )


def _is_linkedin_host(host: str) -> bool:
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def _company_slug_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not _is_linkedin_host((parsed.hostname or "").lower()):
        return None
    match = _COMPANY_PATH_RE.match(parsed.path)
    return match.group(1) if match else None


def _company_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not _is_linkedin_host((parsed.hostname or "").lower()):
        return None
    values = parse_qs(parsed.query).get("f_C", [])
    for value in values:
        if value.isdigit():
            return value
    return None


def _company_slug_from_link(node: LexborNode | None) -> str | None:
    if node is None:
        return None
    href = node.attributes.get("href") or ""
    parsed = urlparse(href)
    if not _is_linkedin_host((parsed.hostname or "").lower()):
        return None
    match = re.match(r"^/company/([^/?#]+)", parsed.path, re.IGNORECASE)
    return match.group(1) if match else None


def _clean_text(node: LexborNode | None) -> str | None:
    if node is None:
        return None
    value = node.text(strip=True)
    return value or None


def _canonical_job_url(
    job_id: str,
    href: str | None,
    *,
    numeric_identity: bool = False,
) -> str:
    # Numeric identity is deliberately opt-in until existing title-bearing
    # source URLs have an ID-preserving migration. Default behavior retains
    # the validated LinkedIn path exactly as before this option was added.
    if not numeric_identity and href:
        parsed = urlparse(href)
        path_id = re.search(r"(?:-|/)(\d+)/?$", parsed.path)
        if (
            _is_linkedin_host((parsed.hostname or "").lower())
            and parsed.scheme.lower() == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port is None
            and parsed.path.startswith("/jobs/view/")
            and path_id
            and path_id.group(1) == job_id
        ):
            return urlunparse(("https", "www.linkedin.com", parsed.path, "", "", ""))
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def _validated_source_ownership_country_codes(value: object) -> frozenset[str]:
    """Validate ISO-3166 alpha-3 countries delegated to another provider."""
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not value or len(value) > 32:
        raise ValueError(
            "LinkedIn source_ownership_excluded_country_codes must be a non-empty "
            "list of ISO-3166 alpha-3 codes"
        )
    codes: set[str] = set()
    for raw in value:
        if not isinstance(raw, str) or raw != raw.strip() or raw not in _ISO3_TO_COUNTRY:
            raise ValueError(
                "LinkedIn source_ownership_excluded_country_codes contains an invalid "
                "ISO-3166 alpha-3 code"
            )
        codes.add(raw)
    return frozenset(codes)


def _exact_location_country_code(locations: list[str] | None) -> str:
    """Resolve LinkedIn's exact, English country field or fail closed.

    The guest endpoint is requested in ``en-US``. Its location field is a
    comma-delimited hierarchy whose final field is the country. Source
    ownership may only be enabled when every location has one unambiguous,
    exact country value; substring or fuzzy matching is deliberately absent.
    """
    if not locations:
        raise ValueError("LinkedIn source ownership requires a location with an exact country")

    by_name: dict[str, set[str]] = {}
    for code, country in _ISO3_TO_COUNTRY.items():
        by_name.setdefault(country.casefold(), set()).add(code)

    resolved: set[str] = set()
    for location in locations:
        if not isinstance(location, str) or "," not in location:
            raise ValueError(
                "LinkedIn source ownership requires a comma-delimited exact country field"
            )
        country = location.rsplit(",", 1)[1].strip().casefold()
        matches = by_name.get(country, set())
        if len(matches) != 1:
            raise ValueError(
                f"LinkedIn source ownership found an unknown or ambiguous country: {location!r}"
            )
        resolved.update(matches)

    if len(resolved) != 1:
        raise ValueError("LinkedIn source ownership requires one unambiguous country per job")
    return next(iter(resolved))


def _apply_source_ownership(
    jobs: list[_ListingJob],
    excluded_country_codes: frozenset[str],
) -> list[_ListingJob]:
    if not excluded_country_codes:
        return jobs

    owned: list[_ListingJob] = []
    for job in jobs:
        country_code = _exact_location_country_code(job.locations)
        job.location_country_code = country_code
        if country_code not in excluded_country_codes:
            owned.append(job)
    return owned


def _parse_listing_cards(
    html: str,
    *,
    canonical_numeric_job_urls: bool = False,
) -> list[_ListingJob]:
    tree = LexborHTMLParser(html)
    jobs: list[_ListingJob] = []
    seen: set[str] = set()

    for card in tree.css(".base-search-card"):
        urn = card.attributes.get("data-entity-urn") or ""
        match = _JOB_URN_RE.search(urn)
        if not match:
            raise ValueError("LinkedIn listing card has no numeric job URN")
        job_id = match.group(1)
        if job_id in seen:
            raise ValueError(f"LinkedIn listing page repeats job {job_id}")
        seen.add(job_id)

        link = card.css_first(".base-card__full-link")
        href = link.attributes.get("href") if link is not None else None
        location = _clean_text(card.css_first(".job-search-card__location"))
        date = card.css_first("time")
        date_posted = date.attributes.get("datetime") if date is not None else None
        company = card.css_first('.base-search-card__subtitle a[href*="/company/"]')

        jobs.append(
            _ListingJob(
                job_id=job_id,
                url=_canonical_job_url(
                    job_id,
                    href,
                    numeric_identity=canonical_numeric_job_urls,
                ),
                title=_clean_text(card.css_first(".base-search-card__title")),
                locations=[location] if location else None,
                date_posted=date_posted or None,
                company_slug=_company_slug_from_link(company),
            )
        )
    return jobs


def _listing_url(
    *,
    company_id: str | None = None,
    keywords: str | None = None,
    start: int = 0,
) -> str:
    # The guest endpoint can localize or relevance-rank an otherwise exact
    # company filter. Worldwide/date-descending scope makes pagination stable
    # and independent of crawler egress location.
    params: dict[str, str | int] = {
        "location": _WORLDWIDE_LOCATION,
        "sortBy": "DD",
        "start": start,
    }
    if company_id is not None:
        params["f_C"] = company_id
    if keywords is not None:
        params["keywords"] = keywords
    query = urlencode(params)
    return f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{query}"


def _detail_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


def _is_empty_listing_fragment(html: str) -> bool:
    """Recognize LinkedIn's boilerplate-only end-of-pagination fragment.

    The guest endpoint sometimes prefixes its normal ``<!---->`` terminator
    with ``<!DOCTYPE html>``.  A doctype contains no listing information and
    must not turn an authoritative empty page into a parser failure.
    """
    stripped = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    stripped = re.sub(r"<!DOCTYPE\s+html\s*>", "", stripped, flags=re.IGNORECASE)
    return not stripped.strip()


async def _fetch_listing_query(
    client: httpx.AsyncClient,
    company_id: str,
    *,
    company_slug: str | None = None,
    keywords: str | None = None,
    canonical_numeric_job_urls: bool = False,
) -> tuple[list[_ListingJob], bool]:
    jobs: list[_ListingJob] = []
    seen: set[str] = set()
    start = 0

    while True:
        page_url = _listing_url(company_id=company_id, keywords=keywords, start=start)
        html = await fetch_text_page_with_retry(
            client,
            page_url,
            retries=_RETRY_ATTEMPTS,
            base_delay=_RETRY_BASE_DELAY_S,
            log_event="linkedin.list_backoff",
            retryable_statuses={401, 403, 999},
            headers=_REQUEST_HEADERS,
        )
        if html is None:
            break
        page = _parse_listing_cards(
            html,
            canonical_numeric_job_urls=canonical_numeric_job_urls,
        )
        if not page:
            if _is_empty_listing_fragment(html):
                break
            raise ValueError("LinkedIn listing returned non-empty HTML without job cards")

        for job in page:
            if company_slug and job.company_slug != company_slug:
                raise ValueError(
                    f"LinkedIn company filter returned {job.company_slug!r}, "
                    f"expected {company_slug!r}"
                )
            if job.job_id in seen:
                raise ValueError(f"LinkedIn pagination repeated job {job.job_id}")
            seen.add(job.job_id)
            jobs.append(job)

        if len(jobs) >= MAX_JOBS:
            return jobs[:MAX_JOBS], True
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
        await asyncio.sleep(_PAGE_DELAY_S)

    return jobs, False


async def _fetch_listings(
    client: httpx.AsyncClient,
    company_id: str,
    *,
    company_slug: str | None = None,
    keywords: str | None = None,
    canonical_numeric_job_urls: bool = False,
) -> tuple[list[_ListingJob], bool]:
    """Fetch the exact company query plus any keyword recovery query as a union."""
    jobs, truncated = await _fetch_listing_query(
        client,
        company_id,
        company_slug=company_slug,
        canonical_numeric_job_urls=canonical_numeric_job_urls,
    )
    if truncated or not keywords:
        return jobs, truncated

    # Keywords work around LinkedIn tenants whose f_C-only search is empty or
    # relevance-ranked. They are supplemental: replacing the exact query could
    # silently exclude valid titles that do not contain the company name.
    await asyncio.sleep(_PAGE_DELAY_S)
    recovered, recovered_truncated = await _fetch_listing_query(
        client,
        company_id,
        company_slug=company_slug,
        keywords=keywords,
        canonical_numeric_job_urls=canonical_numeric_job_urls,
    )
    by_id = {job.job_id: job for job in jobs}
    for job in recovered:
        by_id.setdefault(job.job_id, job)
    combined = list(by_id.values())
    truncated = recovered_truncated or len(combined) >= MAX_JOBS
    return combined[:MAX_JOBS], truncated


async def _resolve_company_id(company_slug: str, client: httpx.AsyncClient) -> str | None:
    """Resolve a LinkedIn company slug through exact-slug guest job results."""
    search_url = _listing_url(keywords=company_slug.replace("-", " "), start=0)
    html = await fetch_text_page_with_retry(client, search_url, headers=_REQUEST_HEADERS)
    if html is None:
        return None

    candidate = next(
        (job for job in _parse_listing_cards(html) if job.company_slug == company_slug),
        None,
    )
    if candidate is None:
        return None

    detail = await fetch_text_page_with_retry(
        client,
        _detail_url(candidate.job_id),
        headers=_REQUEST_HEADERS,
    )
    if detail is None:
        return None
    match = _COMPANY_ID_RE.search(detail)
    return match.group(1) if match else None


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return LinkedIn job summaries for one numeric company ID."""
    _ = pw
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]
    company_slug = metadata.get("company_slug") or _company_slug_from_url(board_url)
    company_id = metadata.get("company_id") or _company_id_from_url(board_url)
    if not company_id and company_slug:
        company_id = await _resolve_company_id(company_slug, client)
    if not company_id or not str(company_id).isdigit():
        raise ValueError(
            "LinkedIn monitor requires company_id (numeric f_C value) or a resolvable "
            f"company jobs URL; got {board_url!r}"
        )

    keywords = metadata.get("keywords")
    if keywords is not None and (not isinstance(keywords, str) or not keywords.strip()):
        raise ValueError("LinkedIn keywords must be a non-empty string when configured")

    canonical_numeric_job_urls = metadata.get("canonical_numeric_job_urls", False)
    if not isinstance(canonical_numeric_job_urls, bool):
        raise ValueError("LinkedIn canonical_numeric_job_urls must be a boolean")

    excluded_country_codes = _validated_source_ownership_country_codes(
        metadata.get("source_ownership_excluded_country_codes")
    )

    jobs, truncated = await _fetch_listings(
        client,
        str(company_id),
        company_slug=company_slug,
        keywords=keywords.strip() if isinstance(keywords, str) else None,
        canonical_numeric_job_urls=canonical_numeric_job_urls,
    )
    jobs = _apply_source_ownership(jobs, excluded_country_codes)
    discovered = [job.discovered(str(company_id)) for job in jobs]
    # A configured keyword is an explicit recovery path for tenants where
    # LinkedIn serves non-authoritative, varying subsets for the same f_C
    # filter. Preserve every discovered URL, but suppress monitor-level gone
    # detection; the daily detail scraper remains authoritative for closure.
    partial = truncated or keywords is not None
    log.info(
        "linkedin.discovered",
        company_id=company_id,
        company_slug=company_slug,
        jobs=len(discovered),
        truncated=partial,
    )
    if partial:
        return truncated_rich_result(discovered)
    return discovered


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect LinkedIn company jobs pages and company-filtered search URLs."""
    _ = pw
    company_slug = _company_slug_from_url(url)
    company_id = _company_id_from_url(url)
    if not company_slug and not company_id:
        return None

    result: dict[str, str | int] = {}
    if company_slug:
        result["company_slug"] = company_slug
    if company_id:
        result["company_id"] = company_id
    if client is None:
        return result

    try:
        if not company_id and company_slug:
            company_id = await _resolve_company_id(company_slug, client)
            if not company_id:
                return None
            result["company_id"] = company_id
        jobs, _truncated = await _fetch_listings(
            client,
            str(company_id),
            company_slug=company_slug,
        )
        result["jobs"] = len(jobs)
        return result
    except TDMReservedError:
        raise
    except Exception:
        log.debug("linkedin.probe_failed", url=url, exc_info=True)
        return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    company_id = metadata.get("company_id") or _company_id_from_url(board_url)
    if not company_id:
        return
    await save_text_response(
        artifact_dir,
        client,
        _listing_url(
            company_id=str(company_id),
            keywords=str(metadata["keywords"]).strip() if metadata.get("keywords") else None,
        ),
        filename="listing.html",
        follow_redirects=True,
    )


register("linkedin", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
