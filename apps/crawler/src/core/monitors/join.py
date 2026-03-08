"""JOIN (join.com) monitor — pre-configured nextdata monitor.

JOIN career pages are Next.js apps with job data embedded in
``__NEXT_DATA__`` at ``props.pageProps.initialState.jobs``.  Server-side
pagination is fixed at 5 jobs per page (``?page=N``).

Board config only needs a slug::

    {"slug": "acme"}

The slug is auto-derived from the board URL when omitted.
"""

from __future__ import annotations

import asyncio
import re
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.core.monitors.nextdata import discover as nextdata_discover
from src.shared.nextdata import extract_next_data, resolve_path

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

_SLUG_RE = re.compile(r"^/companies/([\w-]+)")

_EMPLOYMENT_TYPE_MAP = {
    "Employee": "Full-time",
    "Internship": "Intern",
    "Working Student": "Working Student",
    "Freelancer": "Contract",
    "Freelance": "Contract",
    "Worker": "Full-time",
    "Mini Job": "Part-time",
}

_WORKPLACE_TYPE_MAP = {
    "REMOTE": "remote",
    "HYBRID": "hybrid",
    "ONSITE": "onsite",
}

_SALARY_UNIT_MAP = {
    "PER_YEAR": "year",
    "PER_MONTH": "month",
    "PER_HOUR": "hour",
}

_DEFAULT_DESCRIPTION_PATHS = (
    "props.pageProps.initialState.job.schemaDescription",
    "props.pageProps.initialState.job.unifiedDescription",
    "props.pageProps.initialState.job.description",
)
_DETAIL_FETCH_CONCURRENCY = 5


def _slug_from_url(url: str) -> str | None:
    """Extract the company slug from a join.com URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("join.com", "www.join.com"):
        return None
    match = _SLUG_RE.match(parsed.path)
    return match.group(1) if match else None


def _build_metadata(slug: str) -> dict:
    """Build the full nextdata metadata dict for a JOIN company."""
    return {
        "path": "props.pageProps.initialState.jobs.items",
        "url_template": f"https://join.com/companies/{slug}/{{idParam}}",
        "pagination": {
            "path": "props.pageProps.initialState.jobs.pagination",
            "page_count": "pageCount",
            "page_param": "page",
        },
        "fields": {
            "title": "title",
            "date_posted": "createdAt",
            "locations": "city.cityName",
            "employment_type": {
                "path": "employmentType.name",
                "map": _EMPLOYMENT_TYPE_MAP,
            },
            "job_location_type": {
                "path": "workplaceType",
                "map": _WORKPLACE_TYPE_MAP,
            },
            "metadata.category": "category.name",
            "metadata.id": "id",
        },
        "base_salary": {
            "min": "salaryAmountFrom.amount",
            "max": "salaryAmountTo.amount",
            "currency": "salaryAmountFrom.currency",
            "unit": "salaryFrequency",
            "divisor": 100,
            "unit_map": _SALARY_UNIT_MAP,
        },
    }


def _resolve_description_paths(metadata: dict) -> tuple[str, ...]:
    """Resolve description extraction paths from monitor metadata."""
    raw_paths = metadata.get("description_paths")
    if isinstance(raw_paths, str):
        path = raw_paths.strip()
        if path:
            return (path,)
    if isinstance(raw_paths, list):
        paths = tuple(p.strip() for p in raw_paths if isinstance(p, str) and p.strip())
        if paths:
            return paths

    raw_single = metadata.get("description_path")
    if isinstance(raw_single, str):
        path = raw_single.strip()
        if path:
            return (path,)

    return _DEFAULT_DESCRIPTION_PATHS


def _normalize_description(raw: str) -> str | None:
    """Normalize JOIN description into an HTML fragment."""
    text = raw.strip()
    if not text:
        return None

    # JOIN's schemaDescription is already HTML.
    if "<" in text and ">" in text:
        return text

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    return "".join(f"<p>{escape(line)}</p>" for line in lines)


def _description_from_detail_data(data: dict, description_paths: tuple[str, ...]) -> str | None:
    """Extract description HTML/text from JOIN detail-page __NEXT_DATA__."""
    for path in description_paths:
        value = resolve_path(data, path)
        if isinstance(value, str):
            normalized = _normalize_description(value)
            if normalized:
                return normalized
    return None


async def _fetch_job_description(
    url: str,
    client: httpx.AsyncClient,
    description_paths: tuple[str, ...],
    pw=None,
) -> str | None:
    """Fetch one JOIN job detail page and extract description."""
    html = await fetch_page_text(url, client)

    # Optional fallback for interactive runs that pass a shared Playwright instance.
    if not html and pw is not None:
        try:
            from src.shared.browser import render as browser_render

            html = await browser_render(url, pw=pw)
        except Exception:
            log.debug("join.detail_render_failed", url=url, exc_info=True)
            return None

    if not html:
        return None

    data = extract_next_data(html)
    if not data:
        return None
    return _description_from_detail_data(data, description_paths)


async def _enrich_descriptions(
    jobs: list[DiscoveredJob],
    client: httpx.AsyncClient,
    description_paths: tuple[str, ...],
    pw=None,
) -> None:
    """Populate missing descriptions from JOIN job detail pages."""
    targets = [job for job in jobs if not job.description]
    if not targets:
        return

    sem = asyncio.Semaphore(_DETAIL_FETCH_CONCURRENCY)

    async def _enrich_one(job: DiscoveredJob) -> None:
        try:
            async with sem:
                description = await _fetch_job_description(
                    job.url,
                    client,
                    description_paths,
                    pw=pw,
                )
            if description:
                job.description = description
        except Exception:
            log.debug("join.detail_extract_failed", url=job.url, exc_info=True)

    async with asyncio.TaskGroup() as tg:
        for job in targets:
            tg.create_task(_enrich_one(job))


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Discover jobs from a JOIN career page via nextdata."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]
    description_paths = _resolve_description_paths(metadata)

    slug = metadata.get("slug") or _slug_from_url(board_url)
    if not slug:
        log.error("join.missing_slug", board_url=board_url)
        return []

    nextdata_board = {
        "board_url": board_url,
        "metadata": _build_metadata(slug),
    }
    result = await nextdata_discover(nextdata_board, client, pw=pw)
    # nextdata returns list[DiscoveredJob] when fields are configured
    jobs = result if isinstance(result, list) else []
    await _enrich_descriptions(jobs, client, description_paths, pw=pw)
    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect a join.com career page."""
    slug = _slug_from_url(url)
    if not slug:
        return None

    if client is None:
        return {"slug": slug}

    # Verify the page has job data
    html = await fetch_page_text(url, client)
    if not html:
        return None
    data = extract_next_data(html)
    if not data:
        return None

    items = resolve_path(data, "props.pageProps.initialState.jobs.items")
    if not isinstance(items, list):
        return None

    pagination = resolve_path(data, "props.pageProps.initialState.jobs.pagination")
    total = pagination.get("total") if isinstance(pagination, dict) else len(items)

    log.info("join.detected", url=url, slug=slug, jobs=total)
    return {"slug": slug, "jobs": total}


register("join", discover, cost=9, can_handle=can_handle, rich=True)
