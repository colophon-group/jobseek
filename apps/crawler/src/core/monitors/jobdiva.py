"""JobDiva candidate portal monitor.

JobDiva's public portal starts a search with a form POST, then drains the
remaining results from a separate inclusive-range endpoint.  Both calls need
the short-lived token returned by the portal's public bootstrap endpoint.
"""

from __future__ import annotations

import math
import re
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
import structlog

from src.core.monitors import register
from src.core.monitors.api_sniffer import http_fetch_with_retry
from src.shared.api_sniff import (
    ArrayCandidate,
    Exchange,
    JobListResult,
    PaginationInfo,
    make_http_fetcher,
    paginate_all,
)
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

_BASE_URL = "https://ws.jobdiva.com/candPortal/rest"
_AUTH_URL = f"{_BASE_URL}/auth/a"
_SEARCH_URL = f"{_BASE_URL}/job/searchjobsportal"
_MORE_URL = f"{_BASE_URL}/job/getmore"
_PUBLIC_BASIC_AUTH = "Basic YXhlbG9uOmF4ZWxvbg=="
_PAGE_SIZE = 200
_MAX_JOBS = 50_000
_TENANT_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")


def _tenant_from_board(board: dict) -> str:
    metadata = board.get("metadata") or {}
    configured = metadata.get("token") if isinstance(metadata, dict) else None
    if isinstance(configured, str) and _TENANT_RE.fullmatch(configured):
        return configured

    query = parse_qs(urlparse(board["board_url"]).query)
    values = query.get("a", [])
    if values and _TENANT_RE.fullmatch(values[0]):
        return values[0]
    raise ValueError("Cannot derive a valid JobDiva tenant key from board URL or metadata")


def _portal_job_url(tenant: str, job_id: object) -> str:
    return f"https://www2.jobdiva.com/portal/?a={quote(tenant, safe='')}&compid=0#/jobs/{job_id}"


async def _authorized_headers(
    client: httpx.AsyncClient,
    tenant: str,
) -> dict[str, str]:
    bootstrap_headers = {
        "Authorization": _PUBLIC_BASIC_AUTH,
        "portalID": "1",
        "a": tenant,
        "compid": "-1",
    }
    data = await http_fetch_with_retry(
        client,
        "GET",
        _AUTH_URL,
        bootstrap_headers,
        None,
        raise_non_retryable=True,
    )
    if not isinstance(data, dict):
        raise ValueError("JobDiva auth bootstrap returned an invalid response")

    required = {"token": data.get("token"), "portalid": data.get("portalID")}
    if any(value is None or isinstance(value, (dict, list)) for value in required.values()):
        raise ValueError("JobDiva auth bootstrap omitted its token or portal ID")
    return {
        "token": str(required["token"]),
        "portalid": str(required["portalid"]),
        "a": str(data.get("a") or tenant),
        "compid": str(data.get("compid", -1)),
        "content-type": "application/x-www-form-urlencoded",
        "referer": "https://www2.jobdiva.com/",
    }


def _search_body() -> str:
    return urlencode(
        {
            "city": "",
            "country": "",
            "from": 1,
            "jobCategories": "",
            "jobDivisions": "",
            "jobTypes": "",
            "keywords": "",
            "miles": "",
            "onsiteFlex": "",
            "portalID": 1,
            "qualifications": "",
            "states": "",
            "to": _PAGE_SIZE,
            "unit": "mi",
            "zipcode": "",
        }
    )


async def _first_page(
    client: httpx.AsyncClient,
    tenant: str,
) -> tuple[dict[str, str], int, list[dict]]:
    headers = await _authorized_headers(client, tenant)
    data = await http_fetch_with_retry(
        client,
        "POST",
        _SEARCH_URL,
        headers,
        _search_body(),
        raise_non_retryable=True,
    )
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("JobDiva search returned an invalid response")
    total = data.get("total")
    if not isinstance(total, int) or isinstance(total, bool) or not 0 <= total <= _MAX_JOBS:
        raise ValueError("JobDiva search returned an invalid total")
    items = data["data"]
    if len(items) > _PAGE_SIZE or any(not isinstance(item, dict) for item in items):
        raise ValueError("JobDiva search returned an invalid first page")
    if (total == 0) != (len(items) == 0):
        raise ValueError("JobDiva search total and first page disagree")
    return headers, total, items


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return every canonical JobDiva portal detail URL."""
    _ = pw
    tenant = _tenant_from_board(board)
    headers, total, first_items = await _first_page(client, tenant)
    if total == 0:
        return set()

    more_url = (
        f"{_MORE_URL}?{urlencode({'from': 1, 'to': _PAGE_SIZE, 'count': total, 'portaltype': 1})}"
    )
    result = JobListResult(
        candidate=ArrayCandidate(
            exchange=Exchange(
                method="GET",
                url=more_url,
                request_headers=headers,
                post_data=None,
                status=200,
                body={"total": total, "data": first_items},
                content_type="application/json",
                phase="load",
            ),
            json_path="data",
            items=first_items,
        ),
        url_field=None,
        total_count=total,
        pagination=PaginationInfo(
            param_name="from",
            style="offset",
            start_value=1,
            increment=_PAGE_SIZE,
            location="query",
            end_param_name="to",
        ),
    )
    rows = await paginate_all(
        make_http_fetcher(client),
        result,
        max_pages=max(1, math.ceil(total / _PAGE_SIZE)),
        require_object_items=True,
    )

    ids: list[str] = []
    invalid = False
    for row in rows:
        job_id = row.get("id")
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
            invalid = True
            continue
        ids.append(str(job_id))
    unique_ids = set(ids)
    truncated = invalid or len(ids) != total or len(unique_ids) != total
    urls = {_portal_job_url(tenant, job_id) for job_id in unique_ids}
    log_method = log.warning if truncated else log.info
    log_method("jobdiva.discovered", jobs=len(urls), expected=total, truncated=truncated)
    return truncated_url_result(urls) if truncated else urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect a JobDiva portal URL and verify its tenant against the API."""
    _ = pw
    if client is None:
        return None
    try:
        tenant = _tenant_from_board({"board_url": url})
    except ValueError:
        return None
    if (urlparse(url).hostname or "").lower() not in {"jobdiva.com", "www2.jobdiva.com"}:
        return None
    try:
        _headers, total, _items = await _first_page(client, tenant)
    except Exception:
        log.debug("jobdiva.probe_failed", exc_info=True)
        return None
    return {"token": tenant, "jobs": total}


register("jobdiva", discover, cost=10, can_handle=can_handle)
