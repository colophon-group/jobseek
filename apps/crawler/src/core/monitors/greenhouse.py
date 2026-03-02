"""Greenhouse JSON API monitor.

Public API: GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Returns full job data — title, HTML description, locations, departments, etc.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000

_PAGE_PATTERNS = [
    re.compile(r"boards-api\.greenhouse\.io/v1/boards/(\w+)"),
    re.compile(r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?(\w+)"),
]

_IGNORE_TOKENS = frozenset({"embed", "v1", "api", "js", "css", "assets"})


def _parse_job(job: dict) -> DiscoveredJob | None:
    url = job.get("absolute_url")
    if not url:
        return None

    locations: list[str] = []
    seen: set[str] = set()
    loc = job.get("location")
    if isinstance(loc, dict) and loc.get("name"):
        name = loc["name"]
        locations.append(name)
        seen.add(name)
    for office in job.get("offices", []):
        name = office.get("name")
        if name and name not in seen:
            locations.append(name)
            seen.add(name)

    metadata: dict = {}
    departments = [d.get("name") for d in job.get("departments", []) if d.get("name")]
    if departments:
        metadata["departments"] = departments
    if job.get("education"):
        metadata["education"] = job["education"]
    if job.get("requisition_id"):
        metadata["requisition_id"] = job["requisition_id"]

    return DiscoveredJob(
        url=url,
        title=job.get("title"),
        description=job.get("content"),
        locations=locations or None,
        date_posted=job.get("first_published"),
        metadata=metadata or None,
    )


def _token_from_url(board_url: str) -> str | None:
    match = re.search(r"boards\.greenhouse\.io/(\w+)", board_url)
    if match and match.group(1) not in _IGNORE_TOKENS:
        return match.group(1)
    return None


def _api_url(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


async def _probe_token(token: str, client: httpx.AsyncClient) -> bool:
    try:
        resp = await client.get(_api_url(token), params={"content": "false"})
        if resp.status_code != 200:
            return False
        data = resp.json()
        return isinstance(data.get("jobs"), list)
    except Exception:
        return False


async def discover(board: dict, client: httpx.AsyncClient) -> list[DiscoveredJob]:
    """Fetch job listings with full content from the Greenhouse public API."""
    metadata = board.get("metadata") or {}
    token = metadata.get("token") or _token_from_url(board["board_url"])

    if not token:
        raise ValueError(
            f"Cannot derive Greenhouse token from board URL {board['board_url']!r} "
            "and no token in metadata"
        )

    url = _api_url(token)
    response = await client.get(url, params={"content": "true"})
    response.raise_for_status()

    data = response.json()
    raw_jobs = data.get("jobs", [])

    jobs: list[DiscoveredJob] = []
    for raw in raw_jobs:
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("greenhouse.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]

    return jobs


async def can_handle(url: str, client=None) -> dict | None:
    """Detect Greenhouse: domain check -> page HTML scan -> slug-based API probe."""
    token = _token_from_url(url)
    if token:
        return {}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    log.info("greenhouse.detected_in_page", url=url, token=found)
                    return {"token": found}

    for slug in slugs_from_url(url):
        if await _probe_token(slug, client):
            log.info("greenhouse.detected_by_probe", url=url, token=slug)
            return {"token": slug}

    return None


register("greenhouse", discover, cost=10, can_handle=can_handle)
