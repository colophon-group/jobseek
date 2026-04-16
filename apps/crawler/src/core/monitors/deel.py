"""Deel ATS Job Board API monitor.

Public API (api-prod.letsdeel.com):
- Settings: GET /guest/ats/organizations/{slug}/career_page_settings
- Postings: GET /guest/ats/organizations/{org_id}/job_boards/{board_id}/job_postings

Board page lives at https://jobs.deel.com/{slug}; individual postings at
https://jobs.deel.com/{slug}/job-details/{posting_id}/overview.

Returns full job data (title, rich-text description, locations, salary, employment type).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from src.core.monitors import DiscoveredJob, register

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

_FRONTEND_BASE = "https://jobs.deel.com"
_API_BASE = "https://api-prod.letsdeel.com"
_SETTINGS = f"{_API_BASE}/guest/ats/organizations/{{slug}}/career_page_settings"
_POSTINGS = f"{_API_BASE}/guest/ats/organizations/{{org_id}}/job_boards/{{board_id}}/job_postings"

# Accepts both the legacy `/job-boards/{slug}` layout and the current `/{slug}` layout.
_DEEL_RE = re.compile(r"jobs\.deel\.com/(?:job-boards/)?([\w-]+)")
_IGNORE_SLUGS = frozenset(
    {"auth", "login", "signup", "guest", "api", "deelapi", "job-boards", "job-details"}
)


def _parse_salary(posting: dict) -> dict | None:
    if not posting.get("isCompensationVisible", False):
        return None
    comp = (posting.get("job") or {}).get("currentCompensation")
    if not comp:
        return None
    sal_min = comp.get("minAmount")
    sal_max = comp.get("maxAmount")
    if sal_min is None and sal_max is None:
        return None
    return {
        "currency": comp.get("currencyIsoCode"),
        "min": sal_min,
        "max": sal_max,
        "unit": "year",
    }


def _parse_job(posting: dict, slug: str) -> DiscoveredJob | None:
    posting_id = posting.get("id")
    if not posting_id:
        return None

    url = f"{_FRONTEND_BASE}/{slug}/job-details/{posting_id}/overview"
    job = posting.get("job") or {}

    # Locations
    locations = [
        loc["name"]
        for jl in job.get("jobLocations") or []
        if (loc := jl.get("location")) and loc.get("name")
    ]

    # Employment type — take first
    emp_type = None
    for jet in job.get("jobEmploymentTypes") or []:
        name = (jet.get("employmentType") or {}).get("name")
        if name:
            emp_type = name
            break

    # Metadata: team + department
    metadata: dict = {}
    teams = [
        t["name"] for jt in job.get("jobTeams") or [] if (t := jt.get("team")) and t.get("name")
    ]
    if teams:
        metadata["team"] = ", ".join(teams)
    departments = [
        d["name"]
        for jd in job.get("jobDepartments") or []
        if (d := jd.get("department")) and d.get("name")
    ]
    if departments:
        metadata["department"] = ", ".join(departments)
    if posting_id:
        metadata["id"] = posting_id

    return DiscoveredJob(
        url=url,
        title=posting.get("title"),
        description=posting.get("richtextDescription"),
        locations=locations or None,
        employment_type=emp_type,
        base_salary=_parse_salary(posting),
        date_posted=posting.get("createdAt"),
        metadata=metadata or None,
    )


async def _fetch_settings(slug: str, client: httpx.AsyncClient) -> dict | None:
    try:
        resp = await client.get(_SETTINGS.format(slug=slug), timeout=15)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _ids_from_settings(settings: dict) -> tuple[str | None, str | None]:
    org_id = settings.get("organizationId")
    board_id = (settings.get("jobBoard") or {}).get("id")
    return org_id, board_id


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob]:
    """Fetch all job postings from the Deel API."""
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug")
    org_id = metadata.get("org_id")
    board_id = metadata.get("board_id")

    if not slug:
        m = _DEEL_RE.search(board["board_url"])
        if m and m.group(1) not in _IGNORE_SLUGS:
            slug = m.group(1)

    if not slug:
        raise ValueError(
            f"Cannot derive Deel slug from {board['board_url']!r} and no slug in metadata"
        )

    if not org_id or not board_id:
        settings = await _fetch_settings(slug, client)
        if not settings:
            raise ValueError(f"Failed to fetch Deel settings for slug {slug!r}")
        org_id, board_id = _ids_from_settings(settings)

    if not org_id or not board_id:
        raise ValueError(f"Missing org_id or board_id for Deel slug {slug!r}")

    url = _POSTINGS.format(org_id=org_id, board_id=board_id)
    resp = await client.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    postings = data if isinstance(data, list) else data.get("jobPostings") or []
    jobs = [j for raw in postings if isinstance(raw, dict) and (j := _parse_job(raw, slug))]
    log.info("deel.discovered", slug=slug, jobs=len(jobs))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect Deel: domain match + settings API probe."""
    m = _DEEL_RE.search(url)
    if not m:
        return None

    slug = m.group(1)
    if slug in _IGNORE_SLUGS:
        return None

    if client is None:
        return {"slug": slug}

    settings = await _fetch_settings(slug, client)
    if not settings:
        return None

    org_id, board_id = _ids_from_settings(settings)
    if not org_id or not board_id:
        return None

    # Verify by fetching postings count
    try:
        postings_url = _POSTINGS.format(org_id=org_id, board_id=board_id)
        resp = await client.get(postings_url, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        postings = data if isinstance(data, list) else data.get("jobPostings") or []
        count = len(postings)
    except Exception:
        return None

    return {
        "slug": slug,
        "org_id": org_id,
        "board_id": board_id,
        "jobs": count,
    }


register("deel", discover, cost=10, can_handle=can_handle, rich=True)
