"""Pinpoint HQ career page monitor.

Public API:
  List: GET https://{subdomain}.pinpointhq.com/postings.json

Returns full job data in a single request — no pagination needed.
The response contains a ``data`` array with complete posting objects
including HTML descriptions, locations, compensation, and metadata.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000

_DOMAIN_RE = re.compile(r"^([\w-]+)\.pinpointhq\.com$")

_PAGE_PATTERNS = [
    re.compile(r"([\w-]+)\.pinpointhq\.com"),
]

_IGNORE_SLUGS = frozenset({"api", "www", "app", "docs", "help", "support", "status"})

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "full_time": "Full-time",
    "permanent_full_time": "Full-time",
    "part_time": "Part-time",
    "permanent_part_time": "Part-time",
    "contract_temp": "Contract",
    "contract_to_hire": "Contract",
    "fixed_term_contract": "Contract",
    "freelance": "Contract",
    "temporary": "Temporary",
    "internship": "Intern",
    "permanent": "Full-time",
    "volunteer": "Volunteer",
}

_WORKPLACE_MAP: dict[str, str] = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "onsite",
}


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Pinpoint subdomain from a *.pinpointhq.com URL."""
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_url(slug: str) -> str:
    return f"https://{slug}.pinpointhq.com/postings.json"


def _build_description(posting: dict) -> str | None:
    """Combine description + responsibilities + skills + benefits into HTML."""
    parts: list[str] = []
    for key, header_key in (
        ("description", None),
        ("key_responsibilities", "key_responsibilities_header"),
        ("skills_knowledge_expertise", "skills_knowledge_expertise_header"),
        ("benefits", "benefits_header"),
    ):
        text = posting.get(key)
        if not text or not isinstance(text, str):
            continue
        header = posting.get(header_key) if header_key else None
        if header:
            parts.append(f"<h3>{header}</h3>\n{text}")
        else:
            parts.append(text)
    return "\n".join(parts) if parts else None


def _build_location(posting: dict) -> list[str] | None:
    """Extract location from the nested location object."""
    loc = posting.get("location")
    if not loc or not isinstance(loc, dict):
        return None

    name = loc.get("name")
    if name and isinstance(name, str):
        return [name]

    # Fallback: build from parts
    city = loc.get("city")
    province = loc.get("province")
    parts = [p for p in (city, province) if p]
    if parts:
        return [", ".join(parts)]

    return None


def _parse_salary(posting: dict) -> dict | None:
    """Extract salary from compensation fields."""
    sal_min = posting.get("compensation_minimum")
    sal_max = posting.get("compensation_maximum")
    if sal_min is None and sal_max is None:
        return None
    if not posting.get("compensation_visible", True):
        return None

    currency = posting.get("compensation_currency")
    freq = (posting.get("compensation_frequency") or "").lower()
    unit = "year"
    if "hour" in freq:
        unit = "hour"
    elif "month" in freq:
        unit = "month"
    elif "week" in freq or "two_weeks" in freq:
        unit = "week"
    elif "day" in freq:
        unit = "day"

    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_job(posting: dict) -> DiscoveredJob | None:
    """Map a Pinpoint posting to a DiscoveredJob."""
    url = posting.get("url")
    if not url:
        return None

    # Employment type
    emp_raw = posting.get("employment_type") or ""
    employment_type = _EMPLOYMENT_TYPE_MAP.get(emp_raw)
    if not employment_type:
        # Fallback to human-readable text
        employment_type = posting.get("employment_type_text") or None

    # Workplace / job location type
    workplace_raw = posting.get("workplace_type") or ""
    job_location_type = _WORKPLACE_MAP.get(workplace_raw)

    # Metadata
    metadata: dict = {}
    job_obj = posting.get("job")
    if isinstance(job_obj, dict):
        dept = job_obj.get("department")
        if isinstance(dept, dict) and dept.get("name"):
            metadata["department"] = dept["name"]
        div = job_obj.get("division")
        if isinstance(div, dict) and div.get("name"):
            metadata["division"] = div["name"]
        req_id = job_obj.get("requisition_id")
        if req_id:
            metadata["requisition_id"] = req_id

    return DiscoveredJob(
        url=url,
        title=posting.get("title"),
        description=_build_description(posting),
        locations=_build_location(posting),
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=posting.get("deadline_at"),
        base_salary=_parse_salary(posting),
        metadata=metadata or None,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Pinpoint public postings API."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    slug = metadata.get("slug") or _slug_from_url(board_url)
    if not slug:
        raise ValueError(
            f"Cannot derive Pinpoint slug from board URL {board_url!r} "
            "and no slug in metadata"
        )

    url = _api_url(slug)
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()

    data = response.json()
    raw_postings = data.get("data", [])

    jobs: list[DiscoveredJob] = []
    for raw in raw_postings:
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("pinpoint.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


async def _probe_api(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Pinpoint postings API. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(slug), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        postings = data.get("data")
        if isinstance(postings, list):
            return True, len(postings)
        return False, None
    except Exception:
        return False, None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Pinpoint: domain check -> page HTML scan -> slug-based API probe."""
    # 1. Direct *.pinpointhq.com URL
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_api(slug, client)
            if found:
                result: dict = {"slug": slug}
                if count is not None:
                    result["jobs"] = count
                return result
        return {"slug": slug}

    if client is None:
        return None

    # 2. HTML scan for Pinpoint markers
    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found_slug = match.group(1)
                if found_slug not in _IGNORE_SLUGS:
                    log.info("pinpoint.detected_in_page", url=url, slug=found_slug)
                    found, count = await _probe_api(found_slug, client)
                    if found:
                        result = {"slug": found_slug}
                        if count is not None:
                            result["jobs"] = count
                        return result

    # 3. Slug-based probe as fallback
    for slug in slugs_from_url(url):
        found, count = await _probe_api(slug, client)
        if found:
            log.info("pinpoint.detected_by_probe", url=url, slug=slug)
            result = {"slug": slug}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("pinpoint", discover, cost=10, can_handle=can_handle)
