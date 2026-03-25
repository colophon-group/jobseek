"""Oracle Cloud HCM monitor.

Thin wrapper around api_sniffer that auto-constructs the Oracle HCM REST
API URLs from a ``host`` and ``site`` in the board metadata.  Supports
pagination via the ``finder`` param's ``offset`` suffix.

Board metadata:
    host        Oracle HCM tenant hostname (e.g. "jpmc.fa.oraclecloud.com")
    site        Career site identifier (e.g. "CX_1001", "CampusHiring")
    fields      Optional field mapping override (defaults provided)

The monitor returns rich data (title, location, date, employment_type).
Pair with the oracle_hcm scraper for description enrichment.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.core.monitors.api_sniffer import discover as api_sniffer_discover

log = structlog.get_logger()

_DEFAULT_FIELDS = {
    "title": "Title",
    "locations": "PrimaryLocation",
    "date_posted": "PostedDate",
    "employment_type": "JobSchedule",
}

_DEFAULT_PAGINATION = {
    "param_name": "offset",
    "start": 0,
    "increment": 200,
    "location": "suffix",
}

_ORACLE_HCM_RE = re.compile(
    r"(?:\.fa\.|\.fa\.us\d+\.)(?:ocs\.)?oraclecloud\.com/hcmUI/CandidateExperience",
)


def _build_api_url(host: str, site: str) -> str:
    return (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        f"?onlyData=true"
        f"&expand=requisitionList.workLocation,requisitionList.secondaryLocations"
        f"&finder=findReqs;siteNumber={site}"
        f",facetsList=LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES"
        f"%3BCATEGORIES%3BORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS"
        f",limit=200,sortBy=POSTING_DATES_DESC"
    )


def _build_url_template(host: str, site: str) -> str:
    return f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{{Id}}"


async def can_handle(
    url: str,
    client: httpx.AsyncClient,
    pw=None,
) -> dict | None:
    """Detect Oracle Cloud HCM career sites."""
    if not _ORACLE_HCM_RE.search(url):
        return None

    parsed = urlparse(url)
    host = parsed.hostname
    # Extract site from path: /en/sites/{site}/...
    parts = parsed.path.rstrip("/").split("/")
    try:
        idx = parts.index("sites")
        site = parts[idx + 1]
    except (ValueError, IndexError):
        return None

    # Verify API is accessible
    api_url = _build_api_url(host, site)
    try:
        resp = await client.get(api_url, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("items", [{}])[0].get("TotalJobsCount", 0)
        if total == 0:
            return None
    except Exception:
        return None

    return {"host": host, "site": site, "jobs_count": total}


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | set[str]:
    """Discover jobs via Oracle HCM REST API.

    Delegates to api_sniffer after injecting the constructed API URL,
    field mapping, and pagination config.
    """
    metadata = board.get("metadata") or {}
    host = metadata.get("host")
    site = metadata.get("site")

    if not host or not site:
        # Try to extract from board_url
        parsed = urlparse(board["board_url"])
        host = host or parsed.hostname
        parts = parsed.path.rstrip("/").split("/")
        try:
            idx = parts.index("sites")
            site = site or parts[idx + 1]
        except (ValueError, IndexError):
            log.error("oracle_hcm.missing_host_or_site", board_url=board["board_url"])
            return set()

    # Inject api_sniffer config
    enriched_metadata = {
        **metadata,
        "api_url": _build_api_url(host, site),
        "json_path": "items[0].requisitionList",
        "url_template": _build_url_template(host, site),
        "fields": metadata.get("fields") or _DEFAULT_FIELDS,
        "pagination": metadata.get("pagination") or _DEFAULT_PAGINATION,
    }
    enriched_board = {**board, "metadata": enriched_metadata}

    return await api_sniffer_discover(enriched_board, client, pw=pw)


register("oracle_hcm", discover, cost=15, can_handle=can_handle, rich=True)
