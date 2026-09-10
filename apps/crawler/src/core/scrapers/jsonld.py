"""Registered HTTP/browser adapter for the browser-neutral JSON-LD parser."""

from __future__ import annotations

import httpx
import structlog

from src.core.job_content import JobContent
from src.core.jsonld import _extract_locations as _extract_locations
from src.core.jsonld import _extract_salary as _extract_salary
from src.core.jsonld import _find_job_posting as _find_job_posting
from src.core.jsonld import _JsonLdExtractor as _JsonLdExtractor
from src.core.jsonld import _normalize_meta_locations as _normalize_meta_locations
from src.core.jsonld import _parse_posting as _parse_posting
from src.core.jsonld import _strip_html as _strip_html
from src.core.jsonld import _text_or_list as _text_or_list
from src.core.jsonld import can_handle as can_handle
from src.core.jsonld import contains_job_posting as contains_job_posting
from src.core.jsonld import parse_html as parse_html
from src.core.jsonld import parse_rendered_html as parse_rendered_html
from src.core.scrapers import register
from src.shared.api_sniff import clean_headers
from src.shared.http import is_avature_job_detail_url
from src.shared.http_retry import fetch_response_with_status_retries

log = structlog.get_logger()


async def _fetch_html(
    url: str,
    http: httpx.AsyncClient,
    *,
    headers: dict[str, str] | None = None,
) -> str:
    """GET the page with bounded provider/status-aware retries."""

    retry_limits = {403: 1}
    if is_avature_job_detail_url(url):
        retry_limits[406] = 2
    response = await fetch_response_with_status_retries(
        http,
        url,
        retry_limits=retry_limits,
        headers=headers,
        log_event="jsonld.fetch.retry_status",
    )
    response.raise_for_status()
    return response.text


async def scrape(url: str, config: dict, http: httpx.AsyncClient, pw=None, **kwargs) -> JobContent:
    """Extract job data from JSON-LD on a page."""

    if config.get("render"):
        from src.shared.browser import BROWSER_KEYS
        from src.shared.browser import render as browser_render

        browser_config = {key: value for key, value in config.items() if key in BROWSER_KEYS}
        html = await browser_render(url, browser_config, pw=pw)
    else:
        request_headers = config.get("request_headers") or {}
        headers = clean_headers(request_headers)
        html = await _fetch_html(url, http, headers=headers or None)

    content = parse_rendered_html(url, config, html)
    if content.title:
        log.debug("jsonld.extracted", url=url, title=content.title)
    else:
        log.warning("jsonld.not_found", url=url)
    return content


async def probe(url: str, http: httpx.AsyncClient) -> bool:
    """Check whether a URL has JSON-LD JobPosting data."""

    try:
        response = await http.get(url, follow_redirects=True)
        if response.status_code != 200:
            return False
        return can_handle([response.text]) is not None
    except Exception:
        return False


register("json-ld", scrape, can_handle=can_handle, parse_html=parse_html)


__all__ = [
    "can_handle",
    "contains_job_posting",
    "parse_html",
    "parse_rendered_html",
    "probe",
    "scrape",
]
