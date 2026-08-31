"""SEEK AU/NZ public GraphQL detail scraper."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.scrapers import JobContent, register
from src.core.scrapers.api_sniffer import (
    _probe_seek_http,
    _scrape_http,
    _seek_http_config,
)

log = structlog.get_logger()

_JOB_PATH_RE = re.compile(r"^/job/(\d{1,18})/?$")
_NUMERIC_ID_RE = re.compile(r"^\d{1,18}$")


def _configured_advertiser_id(config: dict) -> str | None:
    if "advertiser_id" not in config:
        return None
    advertiser_id = config.get("advertiser_id")
    if not isinstance(advertiser_id, str) or _NUMERIC_ID_RE.fullmatch(advertiser_id) is None:
        raise ValueError("Invalid SEEK scraper advertiser_id configuration")
    return advertiser_id


def _validate_content(
    content: JobContent,
    *,
    job_id: str,
    advertiser_id: str | None,
) -> JobContent:
    """Bind extracted GraphQL content to its requested job and advertiser."""
    if content == JobContent():
        return content
    metadata = content.metadata if isinstance(content.metadata, dict) else {}
    if str(metadata.get("seek_id") or "") != job_id:
        raise ValueError(f"SEEK detail response ID does not match {job_id}")

    is_expired = metadata.get("is_expired")
    status = metadata.get("status")
    if isinstance(is_expired, bool):
        expired = is_expired
    elif is_expired in ("True", "False"):
        expired = is_expired == "True"
    else:
        raise ValueError(f"SEEK job {job_id} returned invalid status fields")
    if not isinstance(status, str):
        raise ValueError(f"SEEK job {job_id} returned invalid status fields")
    if expired or status.casefold() != "active":
        return JobContent()

    actual_advertiser_id = str(metadata.get("advertiser_id") or "")
    if _NUMERIC_ID_RE.fullmatch(actual_advertiser_id) is None:
        raise ValueError(f"SEEK job {job_id} omitted a valid advertiser identity")
    if advertiser_id is not None and actual_advertiser_id != advertiser_id:
        raise ValueError(f"SEEK detail response advertiser does not match {advertiser_id}")

    if (
        not isinstance(content.title, str)
        or not content.title.strip()
        or not isinstance(content.description, str)
        or not content.description.strip()
    ):
        raise ValueError(f"SEEK job {job_id} omitted title or description")
    return content


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Hydrate one canonical SEEK job through its anonymous GraphQL API."""
    _ = kwargs
    api_config = _seek_http_config([url])
    job_match = _JOB_PATH_RE.fullmatch(urlparse(url).path)
    if api_config is None or job_match is None:
        log.warning("seek_scraper.invalid_url", url=url)
        return JobContent()
    job_id = job_match.group(1)
    advertiser_id = _configured_advertiser_id(config)
    content = await _scrape_http(url, api_config, http)
    return _validate_content(
        content,
        job_id=job_id,
        advertiser_id=advertiser_id,
    )


async def probe_pw(urls: list[str], pw) -> tuple[dict | None, str]:
    """Verify canonical SEEK samples through direct HTTP, never navigation."""
    _ = pw
    api_config = _seek_http_config(urls)
    if api_config is None:
        return None, "SEEK GraphQL not detected"
    return await _probe_seek_http(urls, api_config)


register("seek", scrape, probe_pw=probe_pw)
