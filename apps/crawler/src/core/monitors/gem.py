"""Gem ATS Job Board API monitor.

Public API:
  GET https://api.gem.com/job_board/v0/{slug}/job_posts/

Returns all published jobs in a single request — no pagination, no auth.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import (
    DiscoveredJob,
    fetch_page_text,
    register,
    slug_guess_allowed,
    slugs_from_url,
)

log = structlog.get_logger()

MAX_JOBS = 10_000

_URL_PATTERN = re.compile(r"jobs\.gem\.com/([\w-]+)")

_IGNORE_SLUGS = frozenset({"api", "www", "app", "docs", "help", "support"})

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    "full_time": "Full-time",
    "part_time": "Part-time",
    "contract": "Contract",
    "temporary": "Temporary",
    "internship": "Intern",
    "volunteer": "Volunteer",
}

_LOCATION_TYPE_MAP: dict[str, str] = {
    "remote": "remote",
    "hybrid": "hybrid",
    "in_office": "onsite",
    "on_site": "onsite",
    "onsite": "onsite",
}


def _slug_from_url(url: str) -> str | None:
    """Extract the Gem board slug from a jobs.gem.com URL."""
    match = _URL_PATTERN.search(url)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_url(slug: str) -> str:
    return f"https://api.gem.com/job_board/v0/{slug}/job_posts/"


def _parse_locations(post: dict) -> list[str] | None:
    """Extract locations from offices array, falling back to location.name."""
    locations: list[str] = []
    seen: set[str] = set()

    offices = post.get("offices")
    if offices and isinstance(offices, list):
        for office in offices:
            loc = office.get("location")
            name = loc.get("name") if isinstance(loc, dict) else None
            if not name:
                name = office.get("name")
            if name and name not in seen:
                locations.append(name)
                seen.add(name)

    if not locations:
        loc = post.get("location")
        if isinstance(loc, dict):
            name = loc.get("name")
            if name:
                locations.append(name)
        elif isinstance(loc, str) and loc:
            locations.append(loc)

    return locations or None


def _parse_job(post: dict) -> DiscoveredJob | None:
    """Map a Gem API job post to a DiscoveredJob."""
    url = post.get("absolute_url")
    if not url:
        return None

    # Employment type
    raw_type = post.get("employment_type") or ""
    employment_type = _EMPLOYMENT_TYPE_MAP.get(raw_type, raw_type or None)

    # Location type
    raw_loc_type = post.get("location_type") or ""
    job_location_type = _LOCATION_TYPE_MAP.get(raw_loc_type)

    # Metadata
    metadata: dict = {}
    departments = post.get("departments")
    if departments and isinstance(departments, list):
        dept_names = [d["name"] for d in departments if isinstance(d, dict) and d.get("name")]
        if dept_names:
            metadata["department"] = ", ".join(dept_names)

    return DiscoveredJob(
        url=url,
        title=post.get("title"),
        description=post.get("content"),
        locations=_parse_locations(post),
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=post.get("first_published_at"),
        metadata=metadata or None,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Gem public API."""
    metadata = board.get("metadata") or {}
    slug = metadata.get("token") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Gem slug from board URL {board['board_url']!r} and no token in metadata"
        )

    url = _api_url(slug)
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()

    posts = response.json()
    if not isinstance(posts, list):
        log.warning("gem.unexpected_response", slug=slug, type=type(posts).__name__)
        return []

    jobs: list[DiscoveredJob] = []
    for post in posts:
        parsed = _parse_job(post)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("gem.truncated", slug=slug, total=len(jobs), cap=MAX_JOBS)
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


async def _probe_api(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Gem API for a slug. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(slug), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        if isinstance(data, list):
            return True, len(data)
        return False, None
    except Exception:
        return False, None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Gem: URL pattern -> page HTML scan -> slug-based API probe."""
    # 1. Direct jobs.gem.com URL
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_api(slug, client)
            if found:
                result: dict = {"token": slug}
                if count is not None:
                    result["jobs"] = count
                return result
        return {"token": slug}

    if client is None:
        return None

    # 2. HTML scan for Gem markers (embedded boards)
    html = await fetch_page_text(url, client)
    if html:
        # Look for jobs.gem.com/{slug} references
        match = _URL_PATTERN.search(html)
        if match:
            found_slug = match.group(1)
            if found_slug not in _IGNORE_SLUGS:
                log.info("gem.detected_in_page", url=url, slug=found_slug)
                found, count = await _probe_api(found_slug, client)
                if found:
                    result = {"token": found_slug}
                    if count is not None:
                        result["jobs"] = count
                    return result

        # Check for __GEM_TRACKING_CONTEXT__ marker
        if "__GEM_TRACKING_CONTEXT__" in html:
            # Try to extract slug from the page URL's domain
            parsed = urlparse(url)
            path_parts = (parsed.path or "").strip("/").split("/")
            if path_parts and path_parts[0]:
                candidate = path_parts[0]
                if candidate not in _IGNORE_SLUGS:
                    found, count = await _probe_api(candidate, client)
                    if found:
                        log.info("gem.detected_tracking_context", url=url, slug=candidate)
                        result = {"token": candidate}
                        if count is not None:
                            result["jobs"] = count
                        return result

    # 3. Slug-based probe as fallback (explicit blind-probe mode only)
    if slug_guess_allowed():
        for candidate in slugs_from_url(url):
            found, count = await _probe_api(candidate, client)
            if found:
                log.info("gem.detected_by_probe", url=url, slug=candidate)
                result = {"token": candidate}
                if count is not None:
                    result["jobs"] = count
                return result

    return None


register("gem", discover, cost=10, can_handle=can_handle, rich=True)
