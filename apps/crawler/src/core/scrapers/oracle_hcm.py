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

from src.core.scrapers import JobContent, register
from src.core.scrapers.api_sniffer import scrape as api_sniffer_scrape

log = structlog.get_logger()

_DEFAULT_FIELDS = {
    "title": "Title",
    "locations": "PrimaryLocation",
    "date_posted": "ExternalPostedStartDate",
    "description": "ExternalDescriptionStr",
    "qualifications": "ExternalQualificationsStr",
    "responsibilities": "ExternalResponsibilitiesStr",
    "employment_type": "JobSchedule",
}

# Match Oracle HCM job URLs: /sites/{site}/job/{id} or /requisitions/preview/{id}
_JOB_ID_RE = re.compile(r"/(?:job|requisitions/preview)/([^/?#]+)")

_ORACLE_HCM_URL_RE = re.compile(
    r"(?:\.fa\.|\.fa\.us\d+\.)(?:ocs\.)?oraclecloud\.com/hcmUI/CandidateExperience",
)


def _build_detail_url(host: str, site: str) -> str:
    return (
        f"https://{host}/hcmRestApi/resources/latest"
        f"/recruitingCEJobRequisitionDetails"
        f"?expand=all&onlyData=true"
        f'&finder=ById;Id="{{req_id}}",siteNumber={site}'
    )


async def can_handle(url: str, client: httpx.AsyncClient) -> dict | None:
    """Detect Oracle HCM job detail URLs."""
    if not _ORACLE_HCM_URL_RE.search(url):
        return None
    if not _JOB_ID_RE.search(url):
        return None
    return {}


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    pw=None,
    **kwargs,
) -> JobContent:
    """Scrape a single Oracle HCM job via the detail REST API."""
    host = config.get("host")
    site = config.get("site")

    if not host or not site:
        parsed = urlparse(url)
        host = host or parsed.hostname
        parts = parsed.path.rstrip("/").split("/")
        try:
            idx = parts.index("sites")
            site = site or parts[idx + 1]
        except (ValueError, IndexError):
            log.error("oracle_hcm.scraper.missing_site", url=url)
            return JobContent()

    m = _JOB_ID_RE.search(url)
    if not m:
        log.error("oracle_hcm.scraper.no_job_id", url=url)
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
