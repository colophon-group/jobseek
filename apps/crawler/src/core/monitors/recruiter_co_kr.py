"""Recruiter.co.kr ATS monitor.

Recruiter.co.kr is a Korean ATS serving careers pages at
``https://{slug}.recruiter.co.kr/career/home``. Known customers include
McDonald's Korea and Tokyo Electron.

The tenant identity is passed to a shared API via a ``prefix`` request
header, not a path segment. Two public endpoints are used:

- ``POST https://api-recruiter.recruiter.co.kr/position/v1/jobflex``
  Paginated job list. Payload filters are passed in the body
  ``{"pageableRq": {"page", "size", "sort"}, "filter": {...}}``.
  Returns ``{"pagination": {...}, "list": [{positionSn, title, ...}]}``.

- ``GET https://api-recruiter.recruiter.co.kr/position/v2/jobflex/{positionSn}``
  Full job detail including HTML ``jobDescription``.

Neither endpoint requires authentication beyond the ``prefix`` header,
and both respond HTTP-only (no Playwright needed). The monitor fetches
the paginated list, then concurrently fetches details for each job.

Config keys:
    slug   Customer subdomain (e.g. "mcdonalds"). Auto-derived from the
           board URL hostname if omitted.
    include_closed
           When true, include postings with ``openStatus != "OPEN"`` or
           ``submissionStatus == "POST_SUBMISSION"``. Default false.
"""

from __future__ import annotations

import asyncio
import random
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register

log = structlog.get_logger()

_API_BASE = "https://api-recruiter.recruiter.co.kr"
_LIST_PATH = "/position/v1/jobflex"
_DETAIL_PATH_FMT = "/position/v2/jobflex/{sn}"
_PAGE_SIZE = 100
_MAX_JOBS = 50_000
_DETAIL_CONCURRENCY = 8
_HARD_PAGE_CAP = 200  # 200 * 100 = 20,000 jobs

# Retry policy for the list endpoint. The shared api-recruiter.recruiter.co.kr
# host occasionally returns 429/5xx during bursty monitor runs; a short
# jittered exponential backoff absorbs the blip before failing the whole
# monitor (which would double the board-level backoff via _RECORD_FAILURE).
# Mirrors the pattern added to oracle_hcm.py in commit fc9031c1.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 2.0

_IGNORE_SLUGS = frozenset({"www", "api", "api-recruiter", "infra1-static", "cdn"})

# Recruiter.co.kr stores careerType values from the public API.
_CAREER_TYPE_MAP: dict[str, str] = {
    "NEW": "Full-time",  # 신입 (new graduate) — treat as full-time
    "CAREER": "Full-time",  # 경력 (experienced)
    "INTERN": "Intern",
    "INTERNSHIP": "Intern",
    "CONTRACT": "Contract",
    "PARTTIME": "Part-time",
    "PART_TIME": "Part-time",
}


def _slug_from_url(url: str) -> str | None:
    """Extract customer slug from a ``*.recruiter.co.kr`` URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host.endswith(".recruiter.co.kr"):
        return None
    slug = host.removesuffix(".recruiter.co.kr")
    if not slug or slug in _IGNORE_SLUGS or "." in slug:
        return None
    return slug


def _board_url(slug: str) -> str:
    return f"https://{slug}.recruiter.co.kr/career/home"


def _job_url(slug: str, position_sn: int | str) -> str:
    return f"https://{slug}.recruiter.co.kr/career/jobs/{position_sn}"


def _api_headers(slug: str) -> dict[str, str]:
    return {
        "prefix": f"{slug}.recruiter.co.kr",
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "referer": f"https://{slug}.recruiter.co.kr/",
    }


def _dt_date(value: str | None) -> str | None:
    """Return only the date portion of an ISO-8601 timestamp.

    The API returns values like ``2026-04-22T00:00:00``. Schema.org
    ``datePosted`` expects a date-only string.
    """
    if not value or not isinstance(value, str):
        return None
    return value.split("T", 1)[0]


def _parse_list_item(item: dict, slug: str) -> dict | None:
    """Return a shallow summary suitable for merging with detail data.

    Kept minimal — full data is filled by ``_parse_detail``.
    """
    position_sn = item.get("positionSn")
    if position_sn is None:
        return None
    return {
        "positionSn": position_sn,
        "url": _job_url(slug, position_sn),
        "list_title": item.get("title"),
        "startDateTime": item.get("startDateTime"),
        "careerType": item.get("careerType"),
        "classificationCode": item.get("classificationCode"),
        "tagList": item.get("tagList") or [],
        "openStatus": item.get("openStatus"),
        "submissionStatus": item.get("submissionStatus"),
    }


def _extract_locations(detail: dict, summary: dict) -> list[str]:
    """Best-effort location extraction from an API payload.

    The live McDonald's-KR and Tokyo-Electron-KR probes (Apr 2026) returned
    NO location fields in either the list or detail responses — every job
    is tagged only by ``classificationCode`` ("Headquarters", "Restaurant",
    "Part-time"). The shared ``/region/v1`` endpoint requires authentication
    and the list filter ``regionSnList`` suggests region taxonomies are
    per-tenant and only populated for multi-site customers.

    TODO: once a fixture from a tenant with populated regions is captured
    (e.g. a retailer with per-branch hiring), update this helper to read
    the right fields. Likely candidates — inferred from Korean ATS
    conventions — include ``regionList`` / ``regionName`` / ``workPlace`` /
    ``workingAreaList`` / ``siteName``. Until then the helper defensively
    collects anything that looks location-shaped from both list and detail.
    """
    names: list[str] = []
    seen: set[str] = set()

    def _push(value) -> None:
        if isinstance(value, str):
            v = value.strip()
            if v and v not in seen:
                seen.add(v)
                names.append(v)
        elif isinstance(value, dict):
            # Korean-ATS convention: {regionName, regionSn} or {name, sn}.
            for key in ("regionName", "name", "cityName", "siteName", "displayName"):
                if isinstance(value.get(key), str):
                    _push(value[key])
                    return

    # Likely location-bearing keys — kept forgiving since we don't yet have a
    # populated fixture. Merge detail over summary: detail is canonical.
    for source in (summary, detail):
        for key in (
            "regionList",
            "regionNameList",
            "workPlace",
            "workPlaceList",
            "workingArea",
            "workingAreaList",
            "siteList",
            "locationList",
        ):
            value = source.get(key)
            if isinstance(value, list):
                for v in value:
                    _push(v)
            elif value:
                _push(value)
        # Scalar name fields fall through too.
        for key in ("regionName", "workPlaceName", "siteName", "cityName"):
            _push(source.get(key))

    return names


def _parse_detail(detail: dict, summary: dict, slug: str) -> DiscoveredJob | None:
    """Merge list summary + detail response into a ``DiscoveredJob``."""
    title = detail.get("title") or summary.get("list_title")
    if not title:
        return None

    description = detail.get("jobDescription")
    if detail.get("jobDescriptionType") not in (None, "HTML") and description:
        # Non-HTML content: wrap in a <pre> block so the pipeline still
        # recognises it as HTML. Coerce to ``str`` defensively — the API
        # is schemaless and could return numbers/lists for this field.
        description = f"<pre>{str(description)}</pre>"

    career_type = detail.get("careerType") or summary.get("careerType")
    employment_type = _CAREER_TYPE_MAP.get((career_type or "").upper())

    date_posted = _dt_date(detail.get("startDateTime") or summary.get("startDateTime"))

    tags = detail.get("tagList") or summary.get("tagList") or []
    if not isinstance(tags, list):
        # API is schemaless — if ``tagList`` comes back as a dict/string, skip it.
        tags = []
    tag_names = [t.get("tagName") for t in tags if isinstance(t, dict) and t.get("tagName")]

    locations = _extract_locations(detail, summary)

    metadata: dict = {}
    classification = detail.get("classificationCode") or summary.get("classificationCode")
    if classification:
        metadata["classification"] = classification
    if tag_names:
        metadata["tags"] = tag_names
    end_dt = _dt_date(detail.get("endDateTime"))
    if end_dt:
        metadata["valid_through"] = end_dt
    ann_type = detail.get("announcementType")
    if ann_type:
        metadata["announcement_type"] = ann_type
    recruitment_type = detail.get("recruitmentType")
    if recruitment_type:
        metadata["recruitment_type"] = recruitment_type

    return DiscoveredJob(
        url=summary["url"],
        title=title,
        description=description,
        locations=locations or None,
        employment_type=employment_type,
        date_posted=date_posted,
        language="ko",
        metadata=metadata or None,
    )


async def _post_with_retry(
    client: httpx.AsyncClient, url: str, *, headers: dict, json: dict
) -> httpx.Response:
    """POST with exponential-jitter backoff on 429/5xx.

    On a non-transient status or after exhausting retries, returns the final
    response — the caller should still call ``raise_for_status()`` so
    persistent upstream failures propagate to ``_RECORD_FAILURE``.
    """
    resp: httpx.Response | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        resp = await client.post(url, headers=headers, json=json)
        if resp.status_code not in _TRANSIENT_STATUS:
            return resp
        if attempt == _RETRY_ATTEMPTS - 1:
            break
        base_delay = _RETRY_BASE_DELAY_S * (2**attempt)
        jittered = base_delay * random.uniform(0.8, 1.2)
        log.warning(
            "recruiter_co_kr.transient_retry",
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


async def _fetch_list_page(
    slug: str, client: httpx.AsyncClient, page: int, include_closed: bool
) -> dict:
    """Fetch a single page of the jobflex list API."""
    submission = [] if include_closed else ["IN_SUBMISSION"]
    open_status = [] if include_closed else ["OPEN"]
    body = {
        "pageableRq": {"page": page, "size": _PAGE_SIZE, "sort": ["CREATED_DATE_TIME"]},
        "filter": {
            "keyword": "",
            "tagSnList": [],
            "jobGroupSnList": [],
            "careerTypeList": [],
            "regionSnList": [],
            "submissionStatusList": submission,
            "openStatusList": open_status,
            "resumeLanguageTypeList": [],
        },
    }
    resp = await _post_with_retry(
        client, _API_BASE + _LIST_PATH, headers=_api_headers(slug), json=body
    )
    resp.raise_for_status()
    return resp.json()


async def _fetch_detail(slug: str, client: httpx.AsyncClient, sn: int | str) -> dict | None:
    """Fetch the v2 detail record for one positionSn. Returns None on 404."""
    url = _API_BASE + _DETAIL_PATH_FMT.format(sn=sn)
    try:
        resp = await client.get(url, headers=_api_headers(slug))
    except httpx.HTTPError as exc:
        log.warning("recruiter_co_kr.detail_error", slug=slug, sn=sn, error=str(exc))
        return None
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        log.warning("recruiter_co_kr.detail_status", slug=slug, sn=sn, status=resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        return None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch all open jobs from a Recruiter.co.kr career site."""
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board.get("board_url", ""))
    if not slug:
        raise ValueError(
            f"Cannot derive recruiter.co.kr slug from board URL "
            f"{board.get('board_url')!r} and no slug in metadata"
        )

    include_closed = bool(metadata.get("include_closed"))

    # --- 1. paginate the list endpoint ---------------------------------
    summaries: list[dict] = []
    page = 1
    while page <= _HARD_PAGE_CAP:
        payload = await _fetch_list_page(slug, client, page, include_closed)
        items = payload.get("list") or []
        pagination = payload.get("pagination") or {}
        total_pages = pagination.get("totalPages")

        for raw in items:
            parsed = _parse_list_item(raw, slug)
            if parsed is not None:
                summaries.append(parsed)
            if len(summaries) >= _MAX_JOBS:
                break

        if not items or len(summaries) >= _MAX_JOBS:
            break
        if isinstance(total_pages, int) and page >= total_pages:
            break
        page += 1
    else:
        log.warning(
            "recruiter_co_kr.hard_page_cap_reached",
            slug=slug,
            pages=_HARD_PAGE_CAP,
            collected=len(summaries),
        )

    if not summaries:
        return []

    # --- 2. fetch details in parallel ----------------------------------
    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def _one(summary: dict) -> DiscoveredJob | None:
        async with semaphore:
            detail = await _fetch_detail(slug, client, summary["positionSn"])
        if detail is None:
            # Fall back to summary-only record so we still surface the URL.
            detail = {}
        return _parse_detail(detail, summary, slug)

    results = await asyncio.gather(*(_one(s) for s in summaries), return_exceptions=True)

    jobs: list[DiscoveredJob] = []
    for res in results:
        if isinstance(res, BaseException):
            log.warning("recruiter_co_kr.detail_exception", slug=slug, error=str(res))
            continue
        if res is not None:
            jobs.append(res)

    log.info(
        "recruiter_co_kr.complete",
        slug=slug,
        collected=len(summaries),
        jobs=len(jobs),
    )
    return jobs


async def _probe_api(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the tenant against the shared API.

    The ``design/v2`` endpoint returns 200 for known tenants and 400
    ``NotFoundCompanyException`` for unknown ones.
    """
    try:
        resp = await client.get(
            _API_BASE + "/design/v2",
            params={"languageType": "KOR"},
            headers=_api_headers(slug),
        )
    except httpx.HTTPError:
        return False, None
    if resp.status_code != 200:
        return False, None

    # Best-effort job count via the list endpoint.
    try:
        body = {
            "pageableRq": {"page": 1, "size": 1, "sort": ["CREATED_DATE_TIME"]},
            "filter": {
                "keyword": "",
                "tagSnList": [],
                "jobGroupSnList": [],
                "careerTypeList": [],
                "regionSnList": [],
                "submissionStatusList": ["IN_SUBMISSION"],
                "openStatusList": ["OPEN"],
                "resumeLanguageTypeList": [],
            },
        }
        list_resp = await client.post(_API_BASE + _LIST_PATH, headers=_api_headers(slug), json=body)
        if list_resp.status_code == 200:
            total = (list_resp.json().get("pagination") or {}).get("totalCount")
            if isinstance(total, int):
                return True, total
    except httpx.HTTPError:
        pass
    return True, None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Recruiter.co.kr via URL hostname pattern."""
    slug = _slug_from_url(url)
    if not slug:
        return None
    if client is None:
        return {"slug": slug}
    found, count = await _probe_api(slug, client)
    if not found:
        return None
    result: dict = {"slug": slug}
    if count is not None:
        result["jobs"] = count
    return result


register("recruiter_co_kr", discover, cost=15, can_handle=can_handle, rich=True)
