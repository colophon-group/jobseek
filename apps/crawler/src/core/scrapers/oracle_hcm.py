"""Oracle Cloud HCM scraper.

Thin wrapper around api_sniffer that fetches job details from the Oracle
HCM ``recruitingCEJobRequisitionDetails`` REST API.  Extracts description,
qualifications, and responsibilities — no browser needed.

Board metadata:
    host    Oracle HCM tenant hostname
    site    Career site identifier (e.g. "CX_1001")
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors.oracle_hcm import (
    _normalize_oracle_host,
    _normalize_oracle_site,
    _parse_candidate_url,
)
from src.core.scrapers import JobContent, register
from src.core.scrapers.api_sniffer import scrape as api_sniffer_scrape

log = structlog.get_logger()

_DEFAULT_FIELDS = {
    "title": "Title",
    "locations": "PrimaryLocation",
    "date_posted": "ExternalPostedStartDate",
    # Some Oracle tenants publish the role-specific field as an empty string
    # while still exposing useful hotel/business context in the organization
    # fields. JMESPath's OR expression skips empty strings, preserving the
    # specific description whenever present and falling back without
    # manufacturing content for genuinely blank requisitions.
    "description": (
        "ExternalDescriptionStr || OrganizationDescriptionStr || CorporateDescriptionStr"
    ),
    "qualifications": "ExternalQualificationsStr",
    "responsibilities": "ExternalResponsibilitiesStr",
    "employment_type": "JobSchedule",
}

_CONFIGURED_JOB_PATH_RE = re.compile(r"/(?:job|requisitions/preview)/([A-Za-z0-9._-]{1,128})/?$")


def _configured_job_id(url: str) -> str | None:
    """Extract a safe ID from a vanity-domain URL backed by trusted metadata."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    match = _CONFIGURED_JOB_PATH_RE.search(parsed.path)
    return match.group(1) if match else None


def _build_detail_url(host: str, site: str) -> str:
    return (
        f"https://{host}/hcmRestApi/resources/latest"
        f"/recruitingCEJobRequisitionDetails"
        f"?expand=all&onlyData=true"
        f'&finder=ById;Id="{{req_id}}",siteNumber={site}'
    )


async def can_handle(url: str, client: httpx.AsyncClient) -> dict | None:
    """Detect Oracle HCM job detail URLs."""
    return {} if _parse_candidate_url(url, require_job=True) is not None else None


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    pw=None,
    **kwargs,
) -> JobContent:
    """Scrape a single Oracle HCM job via the detail REST API."""
    raw_host = config.get("host")
    raw_site = config.get("site")
    host = _normalize_oracle_host(raw_host)
    site = _normalize_oracle_site(raw_site)
    if raw_host is not None and host is None:
        raise ValueError("Oracle HCM scraper host metadata is invalid")
    if raw_site is not None and site is None:
        raise ValueError("Oracle HCM scraper site metadata is invalid")

    candidate = _parse_candidate_url(url, require_job=True)
    if candidate is not None:
        host = host or candidate[0]
        site = site or candidate[1]
    elif not (host and site and _configured_job_id(url)):
        log.error("oracle_hcm.scraper.invalid_job_url", url=url)
        return JobContent()

    enriched_config = {
        **config,
        "api_url": _build_detail_url(host, site),
        "url_pattern": r"/(?:job|requisitions/preview)/(?P<req_id>[^/?#]+)",
        "json_path": "items[0]",
        "fields": config.get("fields") or _DEFAULT_FIELDS,
    }

    return await api_sniffer_scrape(url, enriched_config, http, pw=pw, **kwargs)


register("oracle_hcm", scrape, can_handle=can_handle)
