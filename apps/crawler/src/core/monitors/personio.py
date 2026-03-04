"""Personio Public XML Feed monitor.

Public API: GET https://{slug}.jobs.personio.de/xml?language=en
Returns full job data in a single request — no pagination needed.
XML format with <workzag-jobs> root and <position> elements.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000

_DOMAIN_RE = re.compile(r"^([\w-]+)\.jobs\.personio\.(?:de|com)$")

_PAGE_PATTERNS = [
    re.compile(r"([\w-]+)\.jobs\.personio\.(?:de|com)"),
    re.compile(r"personio\.de/job/"),
]

_IGNORE_SLUGS = frozenset({"www", "api", "app", "docs", "help", "support", "status"})

_EMPLOYMENT_TYPE_MAP: dict[str, str | None] = {
    "permanent": None,  # combined with schedule
    "intern": "Intern",
    "trainee": "Intern",
    "freelance": "Contract",
}

_SCHEDULE_MAP: dict[str, str] = {
    "full-time": "Full-time",
    "part-time": "Part-time",
}


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Personio company slug from a *.jobs.personio.de URL."""
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_url(slug: str) -> str:
    return f"https://{slug}.jobs.personio.de/xml?language=en"


def _text(el: ET.Element, tag: str) -> str | None:
    """Get text content of a child element, or None."""
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _parse_employment_type(position: ET.Element) -> str | None:
    """Combine employmentType + schedule into a standard employment type."""
    emp_type = (_text(position, "employmentType") or "").lower()
    schedule = (_text(position, "schedule") or "").lower()

    # If employmentType maps to a specific type (intern/freelance), use that
    mapped = _EMPLOYMENT_TYPE_MAP.get(emp_type)
    if mapped is not None:
        return mapped

    # Otherwise, use schedule to determine Full-time/Part-time
    return _SCHEDULE_MAP.get(schedule)


def _parse_description(position: ET.Element) -> str | None:
    """Combine jobDescriptions into a single HTML description."""
    descs_el = position.find("jobDescriptions")
    if descs_el is None:
        return None

    parts: list[str] = []
    for desc in descs_el.findall("jobDescription"):
        name = _text(desc, "name")
        value = _text(desc, "value")
        if value:
            if name:
                parts.append(f"<h3>{name}</h3>")
            parts.append(value)

    return "\n".join(parts) if parts else None


def _parse_job(position: ET.Element, slug: str) -> DiscoveredJob | None:
    """Parse a <position> XML element into a DiscoveredJob."""
    pos_id = _text(position, "id")
    title = _text(position, "name")
    if not pos_id:
        return None

    url = f"https://{slug}.jobs.personio.de/job/{pos_id}"

    # Location
    office = _text(position, "office")
    locations = [office] if office else None

    # Metadata
    metadata: dict = {}
    if pos_id:
        metadata["id"] = pos_id
    for field in (
        "department",
        "subcompany",
        "recruitingCategory",
        "seniority",
        "yearsOfExperience",
        "occupation",
        "occupationCategory",
        "keywords",
    ):
        val = _text(position, field)
        if val:
            metadata[field] = val

    return DiscoveredJob(
        url=url,
        title=title,
        description=_parse_description(position),
        locations=locations,
        employment_type=_parse_employment_type(position),
        date_posted=_text(position, "createdAt"),
        metadata=metadata or None,
    )


async def _probe_api(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Personio XML feed. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(slug), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        root = ET.fromstring(resp.text)
        positions = root.findall(".//position")
        if positions:
            return True, len(positions)
        # Valid XML but no positions — still a valid Personio feed
        if root.tag == "workzag-jobs":
            return True, 0
        return False, None
    except Exception:
        return False, None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Personio public XML feed."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    slug = metadata.get("slug") or _slug_from_url(board_url)
    if not slug:
        raise ValueError(
            f"Cannot derive Personio slug from board URL {board_url!r} "
            "and no slug in metadata"
        )

    url = _api_url(slug)
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()

    root = ET.fromstring(response.text)
    positions = root.findall(".//position")

    jobs: list[DiscoveredJob] = []
    for pos in positions:
        parsed = _parse_job(pos, slug)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("personio.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Personio: domain check -> page HTML scan -> slug-based probe."""
    # 1. Direct *.jobs.personio.de URL
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

    # 2. HTML scan for Personio markers
    html = await fetch_page_text(url, client)
    if html:
        slug_match = re.search(r"([\w-]+)\.jobs\.personio\.(?:de|com)", html)
        if slug_match:
            found_slug = slug_match.group(1)
            if found_slug not in _IGNORE_SLUGS:
                log.info("personio.detected_in_page", url=url, slug=found_slug)
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
            log.info("personio.detected_by_probe", url=url, slug=slug)
            result = {"slug": slug}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("personio", discover, cost=10, can_handle=can_handle)
