"""SEEK AU/NZ public GraphQL detail scraper."""

from __future__ import annotations

import httpx
import structlog

from src.core.scrapers import JobContent, register
from src.core.scrapers.api_sniffer import _probe_seek_http, _scrape_http, _seek_http_config

log = structlog.get_logger()


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Hydrate one canonical SEEK job through its anonymous GraphQL API."""
    _ = config, kwargs
    api_config = _seek_http_config([url])
    if api_config is None:
        log.warning("seek_scraper.invalid_url", url=url)
        return JobContent()
    return await _scrape_http(url, api_config, http)


async def probe_pw(urls: list[str], pw) -> tuple[dict | None, str]:
    """Verify canonical SEEK samples through direct HTTP, never navigation."""
    _ = pw
    api_config = _seek_http_config(urls)
    if api_config is None:
        return None, "SEEK GraphQL not detected"
    return await _probe_seek_http(urls, api_config)


register("seek", scrape, probe_pw=probe_pw)
