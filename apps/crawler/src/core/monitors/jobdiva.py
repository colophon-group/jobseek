"""JobDiva candidate portal monitor.

JobDiva's public portal starts a search with a form POST, then drains the
remaining results from a separate position-based endpoint.  Both calls need
the short-lived token returned by the portal's public bootstrap endpoint.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
import structlog

from src.core.monitors import register
from src.core.monitors.api_sniffer import http_fetch_with_retry
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

_BASE_URL = "https://ws.jobdiva.com/candPortal/rest"
_AUTH_URL = f"{_BASE_URL}/auth/a"
_SEARCH_URL = f"{_BASE_URL}/job/searchjobsportal"
_MORE_URL = f"{_BASE_URL}/job/getmore"
_PUBLIC_BASIC_AUTH = "Basic YXhlbG9uOmF4ZWxvbg=="
_PAGE_SIZE = 200
_MAX_JOBS = 50_000
_SNAPSHOT_ATTEMPTS = 4
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


def _append_job_ids(rows: list[dict], ids: list[str], seen: set[str]) -> str | None:
    """Append valid, unique IDs and describe the first inconsistent row."""
    for row in rows:
        job_id = row.get("id")
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
            return "JobDiva returned a row without a valid positive integer ID"
        value = str(job_id)
        if value in seen:
            return f"JobDiva repeated job ID {value} while pagination was in progress"
        seen.add(value)
        ids.append(value)
    return None


async def _collect_snapshot(
    client: httpx.AsyncClient,
    tenant: str,
) -> tuple[tuple[str, ...], int, str | None]:
    """Drain one bounded inventory snapshot without trusting the volatile total."""
    headers, advertised_total, first_items = await _first_page(client, tenant)
    ids: list[str] = []
    seen: set[str] = set()
    expected_first = min(advertised_total, _PAGE_SIZE)
    if len(first_items) != expected_first:
        return (
            tuple(ids),
            advertised_total,
            f"JobDiva returned {len(first_items)} first-page rows, expected {expected_first}",
        )
    inconsistency = _append_job_ids(first_items, ids, seen)
    if inconsistency is not None:
        return tuple(ids), advertised_total, inconsistency

    # Mirror the storefront's getMoreJobs(from, 0, count) contract. Its last
    # request uses count=201 and caps the rendered rows at 200. Supplying ``to``
    # as a range end or ``count`` as the advertised total yields unstable,
    # overlong slices. The repeated full-snapshot proof below handles a total
    # that changes between search requests without trusting mixed snapshots.
    page_count = max(1, (advertised_total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    if page_count > 1:
        for page_number in range(2, page_count + 1):
            start = (page_number - 1) * _PAGE_SIZE + 1
            count = _PAGE_SIZE + 1 if page_number == page_count else _PAGE_SIZE
            query = urlencode({"from": start, "to": 0, "count": count, "portaltype": 1})
            url = f"{_MORE_URL}?{query}"
            data = await http_fetch_with_retry(
                client,
                "GET",
                url,
                headers,
                None,
                raise_non_retryable=True,
            )
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise ValueError("JobDiva pagination returned an invalid response")
            items = data["data"]
            if len(items) > count or any(not isinstance(item, dict) for item in items):
                raise ValueError("JobDiva pagination returned an invalid page")
            visible_items = items[:_PAGE_SIZE]
            expected = min(_PAGE_SIZE, advertised_total - len(ids))
            bounded_items = visible_items[:expected]
            inconsistency = _append_job_ids(bounded_items, ids, seen)
            if inconsistency is not None:
                return tuple(ids), advertised_total, inconsistency
            if len(visible_items) != expected:
                return (
                    tuple(ids),
                    advertised_total,
                    f"JobDiva page {page_number} returned {len(visible_items)} rows, "
                    f"expected {expected}",
                )

    return tuple(ids), advertised_total, None


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return every canonical JobDiva portal detail URL."""
    _ = pw
    tenant = _tenant_from_board(board)
    previous_ids: tuple[str, ...] | None = None
    last_total = 0
    accumulated_ids: set[str] = set()

    # A stable result must be reproduced by two bounded drains. That catches
    # both duplicate-producing backward shifts and silent gaps caused by
    # forward shifts while the range cursor is walking the listing.
    for attempt in range(1, _SNAPSHOT_ATTEMPTS + 1):
        ids, advertised_total, inconsistency = await _collect_snapshot(client, tenant)
        last_total = advertised_total
        accumulated_ids.update(ids)
        if inconsistency is not None:
            log.warning(
                "jobdiva.snapshot_inconsistent",
                attempt=attempt,
                jobs=len(ids),
                advertised=advertised_total,
                reason=inconsistency,
            )
            continue
        if previous_ids == ids:
            urls = {_portal_job_url(tenant, job_id) for job_id in ids}
            log.info(
                "jobdiva.discovered",
                jobs=len(urls),
                advertised=advertised_total,
                attempts=attempt,
                truncated=False,
            )
            return urls
        if previous_ids is not None:
            log.warning(
                "jobdiva.snapshot_changed",
                attempt=attempt,
                previous_jobs=len(previous_ids),
                jobs=len(ids),
                advertised=advertised_total,
            )
        previous_ids = ids

    urls = {_portal_job_url(tenant, job_id) for job_id in accumulated_ids}
    log.warning(
        "jobdiva.discovered",
        jobs=len(urls),
        advertised=last_total,
        attempts=_SNAPSHOT_ATTEMPTS,
        truncated=True,
    )
    return truncated_url_result(urls)


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
