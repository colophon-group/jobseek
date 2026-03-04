"""SmartRecruiters Posting API monitor.

Public API:
  List:   GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=0
  Detail: GET https://api.smartrecruiters.com/v1/companies/{id}/postings/{postingId}

The list endpoint returns metadata (title, location, department) but not the
job description.  The detail endpoint adds ``jobAd`` (description, qualifications,
etc.) and ``compensation``.  So the monitor fetches each posting individually
for full data (N+1 calls, with concurrency).
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000
PAGE_SIZE = 100
CONCURRENCY = 10

_PAGE_PATTERNS = [
    re.compile(r"api\.smartrecruiters\.com/v1/companies/([\w-]+)"),
    re.compile(r"jobs\.smartrecruiters\.com/([\w-]+)"),
    re.compile(r"careers\.smartrecruiters\.com/([\w-]+)"),
]

_IGNORE_TOKENS = frozenset({"api", "v1", "js", "css", "assets", "postings", "companies"})


def _token_from_url(board_url: str) -> str | None:
    """Extract company identifier from a SmartRecruiters URL."""
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(board_url)
        if match:
            token = match.group(1)
            if token not in _IGNORE_TOKENS:
                return token
    return None


def _api_list_url(token: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{token}/postings"


def _api_detail_url(token: str, posting_id: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}"


def _build_description(job_ad: dict) -> str | None:
    """Combine jobAd sections into a single HTML description."""
    if not job_ad:
        return None
    sections = job_ad.get("sections", {})
    parts: list[str] = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key)
        if isinstance(section, dict):
            title = section.get("title", "")
            text = section.get("text", "")
            if text:
                if title:
                    parts.append(f"<h3>{title}</h3>\n{text}")
                else:
                    parts.append(text)
    return "\n".join(parts) if parts else None


def _build_location(loc: dict) -> str | None:
    """Build a human-readable location string."""
    if not loc:
        return None
    # Prefer fullLocation if available
    full = loc.get("fullLocation")
    if full:
        return full
    # Build from parts
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    filtered = [p for p in parts if p]
    return ", ".join(filtered) if filtered else None


def _parse_salary(posting: dict) -> dict | None:
    """Extract salary from the compensation field if available."""
    comp = posting.get("compensation")
    if not comp:
        return None
    salary = comp.get("salary")
    if not salary:
        return None
    sal_min = salary.get("min")
    sal_max = salary.get("max")
    if sal_min is None and sal_max is None:
        return None
    currency = salary.get("currency")
    period = salary.get("period", "")
    unit = "year"
    if "hour" in period.lower():
        unit = "hour"
    elif "month" in period.lower():
        unit = "month"
    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_job(posting: dict) -> DiscoveredJob | None:
    """Map a detail API response to a DiscoveredJob."""
    url = posting.get("postingUrl")
    if not url:
        # Fallback: build from ref or id
        ref = posting.get("ref")
        if ref:
            url = ref
        else:
            return None

    title = posting.get("name")
    description = _build_description(posting.get("jobAd", {}))

    # Location
    loc = posting.get("location", {})
    location_str = _build_location(loc)
    locations = [location_str] if location_str else None

    # Remote detection
    job_location_type = None
    if loc.get("remote"):
        job_location_type = "remote"
    elif loc.get("hybrid"):
        job_location_type = "hybrid"

    # Employment type
    employment = posting.get("typeOfEmployment")
    employment_type = employment.get("label") if isinstance(employment, dict) else None

    # Metadata
    metadata: dict = {}
    dept = posting.get("department")
    if isinstance(dept, dict) and dept.get("label"):
        metadata["department"] = dept["label"]
    func = posting.get("function")
    if isinstance(func, dict) and func.get("label"):
        metadata["function"] = func["label"]
    exp = posting.get("experienceLevel")
    if isinstance(exp, dict) and exp.get("label"):
        metadata["experienceLevel"] = exp["label"]

    return DiscoveredJob(
        url=url,
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=posting.get("releasedDate"),
        base_salary=_parse_salary(posting),
        metadata=metadata or None,
    )


async def _fetch_detail(
    token: str,
    posting_id: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Fetch a single posting's detail, respecting the concurrency semaphore."""
    async with semaphore:
        try:
            resp = await client.get(_api_detail_url(token, posting_id))
            if resp.status_code != 200:
                log.warning(
                    "smartrecruiters.detail_failed",
                    posting_id=posting_id,
                    status=resp.status_code,
                )
                return None
            return resp.json()
        except Exception as exc:
            log.warning("smartrecruiters.detail_error", posting_id=posting_id, error=str(exc))
            return None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the SmartRecruiters public API.

    Paginates the list endpoint, then fetches each posting's detail
    concurrently to get full descriptions.
    """
    metadata = board.get("metadata") or {}
    token = metadata.get("token") or _token_from_url(board["board_url"])

    if not token:
        raise ValueError(
            f"Cannot derive SmartRecruiters token from board URL {board['board_url']!r} "
            "and no token in metadata"
        )

    # Step 1: Paginate list endpoint to collect all posting IDs
    posting_ids: list[str] = []
    offset = 0

    while True:
        resp = await client.get(
            _api_list_url(token),
            params={"limit": PAGE_SIZE, "offset": offset},
        )
        resp.raise_for_status()
        data = resp.json()

        content = data.get("content", [])
        for item in content:
            pid = item.get("id")
            if pid:
                posting_ids.append(str(pid))

        total_found = data.get("totalFound", 0)
        offset += PAGE_SIZE

        if offset >= total_found or len(content) < PAGE_SIZE:
            break

        if len(posting_ids) >= MAX_JOBS:
            log.warning(
                "smartrecruiters.truncated",
                token=token, total=len(posting_ids), cap=MAX_JOBS,
            )
            posting_ids = posting_ids[:MAX_JOBS]
            break

    log.info("smartrecruiters.listed", token=token, postings=len(posting_ids))

    # Step 2: Fetch details concurrently
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [_fetch_detail(token, pid, client, semaphore) for pid in posting_ids]
    detail_results = await asyncio.gather(*tasks)

    # Step 3: Parse into DiscoveredJobs
    jobs: list[DiscoveredJob] = []
    for detail in detail_results:
        if detail is None:
            continue
        parsed = _parse_job(detail)
        if parsed:
            jobs.append(parsed)

    return jobs


async def _probe_token(token: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the SmartRecruiters API for a token. Returns (found, job_count)."""
    try:
        resp = await client.get(
            _api_list_url(token),
            params={"limit": 1, "offset": 0},
        )
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        total = data.get("totalFound")
        if isinstance(total, int):
            return True, total
        # Check if content exists at all
        content = data.get("content")
        if isinstance(content, list):
            return True, len(content)
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(token: str, client: httpx.AsyncClient) -> int | None:
    """Lightweight API call to get the job count for a token."""
    try:
        resp = await client.get(
            _api_list_url(token),
            params={"limit": 1, "offset": 0},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("totalFound")
        return total if isinstance(total, int) else None
    except Exception:
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect SmartRecruiters: URL pattern -> page HTML scan -> slug-based API probe."""
    token = _token_from_url(url)
    if token:
        if client is not None:
            count = await _fetch_job_count(token, client)
            if count is not None:
                return {"token": token, "jobs": count}
        return {"token": token}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    log.info("smartrecruiters.detected_in_page", url=url, board_token=found)
                    count = await _fetch_job_count(found, client)
                    result: dict = {"token": found}
                    if count is not None:
                        result["jobs"] = count
                    return result

    for slug in slugs_from_url(url):
        found, count = await _probe_token(slug, client)
        if found:
            log.info("smartrecruiters.detected_by_probe", url=url, board_token=slug)
            result = {"token": slug}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("smartrecruiters", discover, cost=10, can_handle=can_handle, rich=True)
