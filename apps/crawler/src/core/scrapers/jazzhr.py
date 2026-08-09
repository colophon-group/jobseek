"""JazzHR detail scraper composed from Jobseek's JSON-LD and DOM parsers."""

from __future__ import annotations

import httpx
import structlog

from src.core.monitors.jazzhr import _tenant_from_url
from src.core.scrapers import JobContent, register
from src.core.scrapers.dom import parse_html as parse_dom_html
from src.core.scrapers.jsonld import parse_html as parse_jsonld_html
from src.shared.http_retry import fetch_response_with_status_retries

log = structlog.get_logger()

_DOM_FALLBACK_CONFIG = {
    "steps": [
        {"tag": "h1", "attr": "class=job_title", "field": "title"},
        {
            "tag": "h3",
            "attr": "class=job_meta",
            "field": "metadata.job_meta",
            "optional": True,
        },
        {
            "field": "description",
            "html": True,
            "stop": "Apply Now",
        },
    ]
}


def _parse_job_meta(value: object) -> tuple[str | None, str | None]:
    """Split JazzHR's visible ``location - employment`` summary."""
    if not isinstance(value, str):
        return None, None
    parts = [part.strip() for part in value.split(" - ") if part.strip()]
    if len(parts) < 2:
        return None, None
    # Some themes prefix a department (``Customer Success - Remote - Full
    # Time``). The location is therefore the component immediately before
    # the employment label, not necessarily the first component.
    return parts[-2], parts[-1]


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Prefer standard JobPosting JSON-LD, filling old themes from DOM."""
    _ = config
    content = parse_jsonld_html(html)
    if content.title and content.description and content.locations and content.employment_type:
        return content

    fallback = parse_dom_html(html, _DOM_FALLBACK_CONFIG)
    if not content.title:
        content.title = fallback.title
    if not content.description:
        content.description = fallback.description
    metadata = fallback.metadata or {}
    location, employment_type = _parse_job_meta(metadata.get("job_meta"))
    if not content.locations and location:
        content.locations = [location]
    if not content.employment_type and employment_type:
        content.employment_type = employment_type
    if (
        not content.job_location_type
        and location
        and location.casefold()
        in {
            "hybrid",
            "remote",
        }
    ):
        content.job_location_type = location
    return content


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    **kwargs,
) -> JobContent:
    """Fetch one JazzHR detail page and parse it without a browser."""
    _ = config, kwargs
    if _tenant_from_url(url) is None or "/apply/jobs/details/" not in url:
        raise ValueError(f"invalid JazzHR job URL: {url!r}")
    response = await fetch_response_with_status_retries(
        http,
        url,
        retry_limits={403: 1},
        log_event="jazzhr.detail_retry",
    )
    response.raise_for_status()
    content = parse_html(response.text)
    if not content.title:
        log.warning("jazzhr_scraper.not_found", url=url)
    return content


register("jazzhr", scrape, parse_html=parse_html)
