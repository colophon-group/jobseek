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

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser, LexborNode

from src.core.monitors import DiscoveredJob, register
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

PAGE_SIZE = 25
MAX_JOBS = 1_000

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

    def discovered(self, company_id: str) -> DiscoveredJob:
        metadata: dict[str, str] = {
            "job_id": self.job_id,
            "linkedin_company_id": company_id,
        }
        if self.company_slug:
            metadata["linkedin_company_slug"] = self.company_slug
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


def _canonical_job_url(job_id: str, href: str | None) -> str:
    if href:
        parsed = urlparse(href)
        if _is_linkedin_host((parsed.hostname or "").lower()) and parsed.path.startswith(
            "/jobs/view/"
        ):
            return urlunparse(("https", "www.linkedin.com", parsed.path, "", "", ""))
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def _parse_listing_cards(html: str) -> list[_ListingJob]:
    tree = LexborHTMLParser(html)
    jobs: list[_ListingJob] = []
    seen: set[str] = set()

    for card in tree.css(".base-search-card"):
        urn = card.attributes.get("data-entity-urn") or ""
        match = _JOB_URN_RE.search(urn)
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue
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
                url=_canonical_job_url(job_id, href),
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
    params: dict[str, str | int] = {"start": start}
    if company_id is not None:
        params["f_C"] = company_id
    if keywords is not None:
        params["keywords"] = keywords
    query = urlencode(params)
    return f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{query}"


def _detail_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


async def _fetch_listings(
    client: httpx.AsyncClient,
    company_id: str,
    *,
    company_slug: str | None = None,
) -> tuple[list[_ListingJob], bool]:
    jobs: list[_ListingJob] = []
    seen: set[str] = set()
    start = 0

    while True:
        page_url = _listing_url(company_id=company_id, start=start)
        html = await fetch_text_page_with_retry(client, page_url)
        if html is None:
            break
        page = _parse_listing_cards(html)
        if not page:
            break

        for job in page:
            if company_slug and job.company_slug != company_slug:
                continue
            if job.job_id not in seen:
                seen.add(job.job_id)
                jobs.append(job)

        if len(jobs) >= MAX_JOBS:
            return jobs[:MAX_JOBS], True
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    return jobs, False


async def _resolve_company_id(company_slug: str, client: httpx.AsyncClient) -> str | None:
    """Resolve a LinkedIn company slug through exact-slug guest job results."""
    search_url = _listing_url(keywords=company_slug.replace("-", " "), start=0)
    html = await fetch_text_page_with_retry(client, search_url)
    if html is None:
        return None

    candidate = next(
        (job for job in _parse_listing_cards(html) if job.company_slug == company_slug),
        None,
    )
    if candidate is None:
        return None

    detail = await fetch_text_page_with_retry(client, _detail_url(candidate.job_id))
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
    if not company_id:
        raise ValueError(
            "LinkedIn monitor requires company_id (numeric f_C value) or a resolvable "
            f"company jobs URL; got {board_url!r}"
        )

    jobs, truncated = await _fetch_listings(
        client,
        str(company_id),
        company_slug=company_slug,
    )
    discovered = [job.discovered(str(company_id)) for job in jobs]
    log.info(
        "linkedin.discovered",
        company_id=company_id,
        company_slug=company_slug,
        jobs=len(discovered),
        truncated=truncated,
    )
    if truncated:
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
        _listing_url(company_id=str(company_id)),
        filename="listing.html",
        follow_redirects=True,
    )


register("linkedin", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
