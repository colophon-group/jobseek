"""LinkedIn public guest-job detail scraper."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.scrapers import JobContent, register
from src.shared.http_retry import fetch_text_page_with_retry

log = structlog.get_logger()

_JOB_ID_RE = re.compile(r"(?:-|/)(\d+)$")


def _job_id_from_url(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    if "/jobs/view/" not in path:
        return None
    match = _JOB_ID_RE.search(path)
    return match.group(1) if match else None


def _company_slug_from_href(href: str) -> str | None:
    match = re.match(r"https?://(?:[\w-]+\.)?linkedin\.com/company/([^/?#]+)", href)
    return match.group(1) if match else None


def _location_type(location: str | None) -> str | None:
    lowered = (location or "").casefold()
    if "hybrid" in lowered:
        return "hybrid"
    if "remote" in lowered:
        return "remote"
    return None


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Parse one server-rendered guest job fragment."""
    _ = config
    tree = LexborHTMLParser(html)

    title_node = tree.css_first(".top-card-layout__title")
    title = title_node.text(strip=True) if title_node is not None else None

    description_node = tree.css_first(".show-more-less-html__markup")
    description_html = description_node.inner_html if description_node is not None else None
    description = description_html.strip() if description_html else None

    location_node = tree.css_first(".topcard__flavor--bullet")
    location = location_node.text(strip=True) if location_node is not None else None

    criteria: dict[str, str] = {}
    for item in tree.css(".description__job-criteria-item"):
        header = item.css_first(".description__job-criteria-subheader")
        value = item.css_first(".description__job-criteria-text")
        if header is None or value is None:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", header.text(strip=True).casefold()).strip("_")
        text = value.text(strip=True)
        if key and text:
            criteria[key] = text

    employment_type = criteria.pop("employment_type", None)
    company = tree.css_first(".topcard__org-name-link")
    metadata: dict[str, str] = criteria
    if company is not None:
        company_slug = _company_slug_from_href(company.attributes.get("href") or "")
        if company_slug:
            metadata["linkedin_company_slug"] = company_slug

    return JobContent(
        title=title or None,
        description=description or None,
        locations=[location] if location else None,
        employment_type=employment_type or None,
        job_location_type=_location_type(location),
        metadata=metadata or None,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch a LinkedIn detail fragment by the numeric ID in the public URL."""
    _ = kwargs
    job_id = _job_id_from_url(url)
    if not job_id:
        log.warning("linkedin_scraper.invalid_url", url=url)
        return JobContent()

    detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    html = await fetch_text_page_with_retry(http, detail_url)
    if html is None:
        return JobContent()
    return parse_html(html, config)


register("linkedin", scrape, parse_html=parse_html)
