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

import asyncio
import random
import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.core.monitors.api_sniffer import discover as api_sniffer_discover

log = structlog.get_logger()

# Transient upstream failures are common on ``*.fa.em2.oraclecloud.com`` because
# dozens of Oracle HCM tenants share the same backend. A single Oracle-side
# hiccup returns 503 for every tenant we hit during that window (issue #2217:
# 15 distinct boards all 503'd inside a 2m15s window on 2026-04-17 14:31:59Z
# — one Oracle infra burp, not 15 separate board failures).
#
# Retry in-place with jittered exponential backoff before giving up to the
# board-level backoff (``_RECORD_FAILURE``, which doubles the next-check
# interval). If Oracle recovers within a few seconds, the monitor run still
# completes successfully and no ``batch.monitor.error`` fires.
#
# Attempts chosen conservatively — 3 × ~6-18s covers Oracle's typical burp
# window without stretching a single monitor beyond the 10-min lease budget.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 3.0


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, *, timeout: float = 30.0
) -> httpx.Response:
    """GET with exponential-jitter backoff on 429/5xx.

    On a non-transient status or after exhausting retries, returns the final
    response — the caller should still call ``raise_for_status()`` on it so
    persistent upstream failures still propagate to ``_RECORD_FAILURE``.
    """
    resp: httpx.Response | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        resp = await client.get(url, timeout=timeout)
        if resp.status_code not in _TRANSIENT_STATUS:
            return resp
        if attempt == _RETRY_ATTEMPTS - 1:
            break
        base_delay = _RETRY_BASE_DELAY_S * (2**attempt)
        jittered = base_delay * random.uniform(0.8, 1.2)
        log.warning(
            "oracle_hcm.transient_retry",
            url=url,
            status=resp.status_code,
            attempt=attempt + 1,
            backoff_s=round(jittered, 2),
        )
        await asyncio.sleep(jittered)
    # resp is guaranteed non-None: the first request always assigns it or raises,
    # and the loop only breaks on transient status after at least one assignment.
    assert resp is not None
    return resp


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


async def discover_stream(board: dict, client: httpx.AsyncClient, pw=None):
    """Yield batches of DiscoveredJob per API page, pulsing heartbeats."""
    metadata = board.get("metadata") or {}
    host = metadata.get("host")
    site = metadata.get("site")

    if not host or not site:
        parsed = urlparse(board["board_url"])
        host = host or parsed.hostname
        parts = parsed.path.rstrip("/").split("/")
        try:
            idx = parts.index("sites")
            site = site or parts[idx + 1]
        except (ValueError, IndexError):
            log.error("oracle_hcm.missing_host_or_site", board_url=board["board_url"])
            return

    fields = metadata.get("fields") or _DEFAULT_FIELDS
    url_template = _build_url_template(host, site)
    api_url = _build_api_url(host, site)

    offset = 0
    total = None
    while total is None or offset < total:
        page_url = f"{api_url},offset={offset}" if offset else api_url
        resp = await _get_with_retry(client, page_url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        wrapper = (data.get("items") or [{}])[0]
        if total is None:
            total = wrapper.get("TotalJobsCount", 0)
            if total == 0:
                return

        items = wrapper.get("requisitionList", [])
        if not items:
            break

        jobs: list[DiscoveredJob] = []
        for item in items:
            job_id = item.get("Id")
            if not job_id:
                continue
            url = url_template.format(Id=job_id)
            jobs.append(
                DiscoveredJob(
                    url=url,
                    title=item.get(fields.get("title", "Title")),
                    locations=[item[fields["locations"]]]
                    if item.get(fields.get("locations", "PrimaryLocation"))
                    else None,
                    date_posted=item.get(fields.get("date_posted", "PostedDate")),
                    employment_type=item.get(fields.get("employment_type", "JobSchedule")),
                )
            )

        if jobs:
            yield jobs
            log.debug("oracle_hcm.stream_batch", offset=offset, batch=len(jobs), total=total)

        offset += 200
        if len(items) < 200:
            break

    log.info("oracle_hcm.stream_done", host=host, site=site, total=total)


register("oracle_hcm", discover, cost=15, can_handle=can_handle, rich=True, stream=discover_stream)
