"""NowHiring/Snagajob career-site monitor."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from urllib.parse import quote, urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

_HOST = "nowhiring.com"
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}", re.IGNORECASE)
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_DETAIL_CONCURRENCY = 10
MAX_JOBS = 50_000


def _slug_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1 or not _SLUG_RE.fullmatch(parts[0]):
        return None
    return parts[0].lower()


def _site_url(slug: str) -> str:
    key = quote(f"{_HOST}/{slug}", safe="")
    return f"https://{_HOST}/api/career-live-sites/{key}"


def _values_by_field(criteria: object) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not isinstance(criteria, list):
        return result
    for item in criteria:
        if not isinstance(item, dict):
            continue
        name = item.get("fieldName")
        value = item.get("fieldValue")
        if isinstance(name, str) and isinstance(value, (str, int)) and str(value).strip():
            result.setdefault(name, []).append(str(value).strip())
    return result


async def _fetch_site(slug: str, client: httpx.AsyncClient) -> dict:
    response = await client.get(
        _site_url(slug),
        headers={"Accept": "application/json", "Referer": f"https://{_HOST}/{slug}/"},
        follow_redirects=True,
        timeout=30,
    )
    if response.status_code in {404, 410}:
        raise BoardGoneError(
            f"NowHiring board {slug!r} no longer exists",
            url=str(response.url),
            status_code=response.status_code,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("NowHiring career-site response is not an object")
    values = _values_by_field(payload.get("jobSearchCriteria"))
    customer_ids = values.get("billingAccountId", [])
    if not customer_ids:
        raise ValueError("NowHiring career site omitted its billing account scope")
    return {
        "slug": slug,
        "customer_ids": customer_ids,
        "brand_template_ids": values.get("brandTemplateId", []),
        "brand_ids": values.get("brandId", []),
    }


def _search_payload(metadata: Mapping[str, object], start: int) -> dict:
    return {
        "customerId": list(metadata["customer_ids"]),
        "brandTemplateId": list(metadata["brand_template_ids"]),
        "brandId": list(metadata["brand_ids"]),
        "start": str(start),
        "sort": "jobtitle",
        "includefacetlist": False,
        "manyselectfacets": [],
        "specificfacets": [],
        "disablesuppression": True,
        "specificlocationsonly": True,
    }


async def _fetch_search_page(
    metadata: Mapping[str, object],
    start: int,
    client: httpx.AsyncClient,
) -> tuple[list[dict], int]:
    response = await client.post(
        f"https://{_HOST}/api/jobs/search",
        json=_search_payload(metadata, start),
        headers={"Accept": "application/json", "Referer": f"https://{_HOST}/"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    raw_jobs = payload.get("list") if isinstance(payload, dict) else None
    total = payload.get("total") if isinstance(payload, dict) else None
    if not isinstance(raw_jobs, list) or not isinstance(total, int) or total < 0:
        raise ValueError("NowHiring job search returned an invalid list or total")
    return [item for item in raw_jobs if isinstance(item, dict)], total


async def _fetch_detail(job_id: str, client: httpx.AsyncClient) -> dict:
    response = await client.get(
        f"https://{_HOST}/api/jobs/{job_id}",
        headers={"Accept": "application/json", "Referer": f"https://{_HOST}/"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"NowHiring job {job_id!r} detail is not an object")
    return payload


def _location(raw: Mapping[str, object]) -> list[str] | None:
    parts: list[str] = []
    for key in ("addressLine1", "addressLine2", "city", "stateProvCode", "postalCode"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return [", ".join(parts)] if parts else None


def _parse_job(
    raw: Mapping[str, object],
    *,
    slug: str,
    customer_id: str,
) -> DiscoveredJob | None:
    job_id = str(raw.get("id", "")).strip()
    detail_customer_id = str(raw.get("billingAccountId") or customer_id).strip()
    title = raw.get("jobTitle")
    if (
        not _ID_RE.fullmatch(job_id)
        or not detail_customer_id.isdigit()
        or not isinstance(title, str)
        or not title.strip()
    ):
        return None
    categories = raw.get("categories")
    employment_type = None
    if isinstance(categories, list):
        employment_type = next(
            (item.strip() for item in categories if isinstance(item, str) and item.strip()),
            None,
        )
    return DiscoveredJob(
        url=f"https://{_HOST}/{slug}/job-details/{job_id}",
        title=title.strip(),
        description=raw.get("jobDescription")
        if isinstance(raw.get("jobDescription"), str)
        else None,
        locations=_location(raw),
        employment_type=employment_type,
        date_posted=raw.get("postedDate") if isinstance(raw.get("postedDate"), str) else None,
        language="en",
        metadata={
            key: value
            for key, value in {
                "job_id": raw.get("id"),
                "company": raw.get("company"),
                "application_url": raw.get("thirdPartyApplyUrl") or raw.get("applicationURL"),
                "brand_id": raw.get("brandId"),
            }.items()
            if value not in {None, ""}
        }
        or None,
        source_identity=f"nowhiring:{detail_customer_id}:{job_id}",
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch a complete NowHiring search and hydrate its public job details."""
    _ = pw
    configured = board.get("metadata") or {}
    slug = configured.get("slug") or _slug_from_url(board["board_url"])
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ValueError("Cannot derive a valid NowHiring career-site slug")
    slug = slug.lower()
    metadata = await _fetch_site(slug, client)

    summaries: list[dict] = []
    total = 0
    while len(summaries) < MAX_JOBS:
        page, total = await _fetch_search_page(metadata, len(summaries), client)
        summaries.extend(page)
        if len(summaries) >= total:
            break
        if not page:
            raise ValueError("NowHiring search stopped before its advertised total")

    ids: list[str] = []
    seen_ids: set[str] = set()
    duplicates = 0
    for summary in summaries:
        job_id = str(summary.get("id", "")).strip()
        if not _ID_RE.fullmatch(job_id):
            raise ValueError("NowHiring search returned a job without a valid id")
        if job_id in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(job_id)
        ids.append(job_id)
    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def hydrate(job_id: str) -> dict:
        async with semaphore:
            return await _fetch_detail(job_id, client)

    details = await asyncio.gather(*(hydrate(job_id) for job_id in ids))
    customer_id = str(metadata["customer_ids"][0])
    jobs = [
        job
        for raw in details
        if (job := _parse_job(raw, slug=slug, customer_id=customer_id)) is not None
    ]
    invalid = len(details) - len(jobs)
    if details and not jobs:
        raise ValueError("NowHiring details returned no valid jobs")
    truncated = invalid > 0 or duplicates > 0 or total > len(summaries)
    if truncated:
        log.warning(
            "nowhiring.truncated",
            slug=slug,
            total=total,
            returned=len(jobs),
            invalid=invalid,
            duplicates=duplicates,
        )
        return truncated_rich_result(jobs)
    log.info("nowhiring.discovered", slug=slug, jobs=len(jobs))
    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect and verify a NowHiring career site, including empty boards."""
    _ = pw
    slug = _slug_from_url(url)
    if slug is None:
        return None
    if client is None:
        return {"slug": slug}
    try:
        metadata = await _fetch_site(slug, client)
        _, total = await _fetch_search_page(metadata, 0, client)
    except Exception:
        return None
    return {**metadata, "jobs": total}


register("nowhiring", discover, cost=10, can_handle=can_handle, rich=True)
