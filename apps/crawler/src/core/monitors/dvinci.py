"""d.vinci ATS monitor.

Public API (no auth): GET https://{customer}.dvinci-hr.com/jobPublication/list.json
Returns full job data — title, HTML description, locations, salary, working times.

Detection: URL domain match (*.dvinci-hr.com) or page HTML markers
(ng-app="dvinci.apps.Dvinci", meta[name=dvinciVersion], DvinciData).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 10_000

# Page HTML patterns for detecting d.vinci career portals
_PAGE_PATTERNS = [
    re.compile(r'ng-app="dvinci\.apps\.Dvinci"'),
    re.compile(r'name="dvinciVersion"'),
    re.compile(r"var\s+DvinciData\s*="),
    re.compile(r"([\w-]+)\.dvinci-hr\.com"),
]

_IGNORE_SLUGS = frozenset({"www", "static", "api", "cdn"})


def _slug_from_url(url: str) -> str | None:
    """Extract customer slug from a *.dvinci-hr.com URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith(".dvinci-hr.com"):
        slug = host.removesuffix(".dvinci-hr.com")
        if slug and slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_url(slug: str) -> str:
    return f"https://{slug}.dvinci-hr.com/jobPublication/list.json"


def _board_url(slug: str) -> str:
    return f"https://{slug}.dvinci-hr.com"


def _parse_job(job: dict) -> DiscoveredJob | None:
    """Parse a d.vinci job publication into a DiscoveredJob."""
    url = job.get("jobPublicationURL")
    if not url:
        return None

    title = job.get("position")

    # Build HTML description from sections
    sections = []
    for field in ("introduction", "tasks", "profile", "weOffer", "closingText"):
        content = job.get(field)
        if content and isinstance(content, str):
            sections.append(content)
    description = "\n".join(sections) if sections else None

    # Locations
    opening = job.get("jobOpening") or {}
    locations: list[str] = []
    for loc in opening.get("locations", []):
        name = loc.get("name")
        if name and name not in locations:
            locations.append(name)

    # Employment type
    working_times = opening.get("workingTimes", [])
    employment_type = None
    if working_times:
        internal = working_times[0].get("internalName", "")
        _map = {"FULL_TIME": "full-time", "PART_TIME": "part-time"}
        employment_type = _map.get(internal)

    # Salary
    salary_obj = opening.get("salary")
    base_salary = None
    if isinstance(salary_obj, dict):
        value = salary_obj.get("value") or {}
        currency = salary_obj.get("currency")
        min_val = value.get("minValue")
        max_val = value.get("maxValue")
        unit_text = value.get("unitText", "").lower()
        unit_map = {"year": "year", "month": "month", "day": "day", "hour": "hour"}
        if currency and (min_val is not None or max_val is not None):
            base_salary = {
                "currency": currency,
                "min": min_val,
                "max": max_val,
                "unit": unit_map.get(unit_text, "year"),
            }

    # Job location type (remote/onsite) — d.vinci doesn't have a dedicated field
    # but we can preserve contract period in metadata
    metadata: dict = {}
    contract = opening.get("contractPeriod")
    if isinstance(contract, dict) and contract.get("internalName"):
        metadata["contract_period"] = contract["internalName"].lower()
    ref = opening.get("reference")
    if ref:
        metadata["reference"] = ref
    categories = [c.get("name") for c in opening.get("categories", []) if c.get("name")]
    if categories:
        metadata["categories"] = categories
    department = opening.get("department")
    if department:
        metadata["department"] = department

    return DiscoveredJob(
        url=url,
        title=title,
        description=description,
        locations=locations or None,
        employment_type=employment_type,
        date_posted=opening.get("createdDate"),
        base_salary=base_salary,
        metadata=metadata or None,
    )


async def _probe_api(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the d.vinci API. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(slug), params={"fields": "small"})
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        if isinstance(data, list):
            return True, len(data)
        return False, None
    except Exception:
        return False, None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings with full content from the d.vinci public API."""
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive d.vinci slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    url = _api_url(slug)
    params: dict[str, str] = {"lang": "en"}
    response = await client.get(url, params=params)
    response.raise_for_status()

    raw_jobs = response.json()
    if not isinstance(raw_jobs, list):
        return []

    jobs: list[DiscoveredJob] = []
    for raw in raw_jobs:
        # Skip unsolicited/initiative applications
        opening = raw.get("jobOpening") or {}
        if opening.get("type") == "UNSOLICITED":
            continue
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("dvinci.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect d.vinci: URL domain match -> page HTML scan.

    No slug-based blind probe — d.vinci subdomains are custom names,
    not derivable from company domain.
    """
    # 1. Direct *.dvinci-hr.com URL
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_api(slug, client)
            if found:
                result: dict = {"slug": slug}
                if count is not None:
                    result["jobs"] = count
                return result
            # DNS resolved but API failed — still a d.vinci portal
            return {"slug": slug}
        return {"slug": slug}

    if client is None:
        return None

    # 2. HTML scan for d.vinci markers
    html = await fetch_page_text(url, client)
    if html:
        # Look for d.vinci Angular app or version meta tag
        for pattern in _PAGE_PATTERNS[:3]:
            if pattern.search(html):
                # Found marker — try to extract the dvinci-hr.com slug from HTML
                slug_match = re.search(r"([\w-]+)\.dvinci-hr\.com", html)
                if slug_match:
                    found_slug = slug_match.group(1)
                    if found_slug not in _IGNORE_SLUGS:
                        log.info("dvinci.detected_in_page", url=url, slug=found_slug)
                        found, count = await _probe_api(found_slug, client)
                        if found:
                            result = {"slug": found_slug}
                            if count is not None:
                                result["jobs"] = count
                            return result
                break  # Marker found but no slug — can't probe

    return None


register("dvinci", discover, cost=10, can_handle=can_handle, rich=True)
