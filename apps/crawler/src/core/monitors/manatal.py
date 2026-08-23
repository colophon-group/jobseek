"""Manatal public career-page API monitor.

Manatal-hosted boards live at ``https://www.careers-page.com/{slug}`` and
expose published postings through an unauthenticated paginated API.  The list
response already contains the full description and location, so this monitor
returns rich jobs and does not need a detail scraper.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

_FRONTEND_BASE = "https://www.careers-page.com"
_API_TEMPLATE = f"{_FRONTEND_BASE}/api/v1.0/c/{{slug}}/jobs/"
_URL_RE = re.compile(r"(?:www\.)?careers-page\.com/([\w-]+)(?:[/#?]|$)", re.IGNORECASE)
_IGNORE_SLUGS = frozenset({"api", "assets", "job", "jobs", "login", "static", "www"})
_PAGE_SIZE = 50
MAX_JOBS = 50_000
MAX_PAGES = 10_000


def _slug_from_url(url: str) -> str | None:
    match = _URL_RE.search(url)
    if not match:
        return None
    slug = match.group(1).lower()
    return None if slug in _IGNORE_SLUGS else slug


def _api_url(slug: str) -> str:
    return _API_TEMPLATE.format(slug=slug)


def _location(post: dict) -> list[str] | None:
    display = post.get("location_display")
    if isinstance(display, str) and display.strip():
        return [display.strip()]

    parts = [post.get("city"), post.get("state"), post.get("country")]
    text = ", ".join(str(part).strip() for part in parts if str(part or "").strip())
    return [text] if text else None


def _parse_job(post: dict, slug: str) -> DiscoveredJob | None:
    job_hash = post.get("hash")
    if not job_hash:
        return None

    metadata: dict = {}
    if post.get("id") is not None:
        metadata["id"] = post["id"]

    return DiscoveredJob(
        url=f"{_FRONTEND_BASE}/{slug}/job/{job_hash}",
        title=post.get("position_name"),
        description=post.get("description"),
        locations=_location(post),
        metadata=metadata or None,
    )


async def _fetch_page(slug: str, page: int, client: httpx.AsyncClient) -> dict:
    response = await client.get(
        _api_url(slug),
        params={
            "page_size": _PAGE_SIZE,
            "page": page,
            "ordering": "-is_pinned_in_career_page,-last_published_at",
        },
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    count = payload.get("count") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("results"), list)
        or type(count) is not int
        or count < 0
    ):
        raise ValueError(f"Unexpected Manatal jobs response for slug {slug!r}")
    return payload


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob]:
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])
    if not slug:
        raise ValueError(
            f"Cannot derive Manatal slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    jobs: list[DiscoveredJob] = []
    seen_hashes: set[str] = set()
    advertised: int | None = None
    page = 1

    while len(jobs) < MAX_JOBS:
        if page > MAX_PAGES:
            raise ValueError(f"Manatal pagination exceeded {MAX_PAGES} pages for slug {slug!r}")
        payload = await _fetch_page(slug, page, client)
        page_advertised = payload["count"]
        if advertised is None:
            advertised = page_advertised
        elif page_advertised != advertised:
            raise ValueError(
                f"Manatal job count changed during pagination for slug {slug!r}: "
                f"{advertised} -> {page_advertised}"
            )

        jobs_before_page = len(jobs)
        for post in payload["results"]:
            if not isinstance(post, dict):
                continue
            job_hash = post.get("hash")
            if not job_hash or job_hash in seen_hashes:
                continue
            parsed = _parse_job(post, slug)
            if parsed:
                seen_hashes.add(job_hash)
                jobs.append(parsed)
                if len(jobs) >= MAX_JOBS:
                    break

        next_page = payload.get("next")
        if not next_page:
            break
        if len(jobs) == jobs_before_page:
            raise ValueError(f"Manatal pagination made no progress for slug {slug!r}")
        page += 1

    assert advertised is not None
    if advertised > len(jobs):
        log.warning("manatal.truncated", slug=slug, fetched=len(jobs), advertised=advertised)
        return truncated_rich_result(jobs)
    if advertised < len(jobs):
        raise ValueError(
            f"Manatal returned {len(jobs)} unique jobs for advertised count {advertised} "
            f"for slug {slug!r}"
        )
    if len(jobs) >= MAX_JOBS:
        log.warning("manatal.truncated", slug=slug, total=len(jobs), cap=MAX_JOBS)
        return truncated_rich_result(jobs)

    log.info("manatal.discovered", slug=slug, jobs=len(jobs))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    slug = _slug_from_url(url)
    if not slug:
        return None
    if client is None:
        return {"slug": slug}

    try:
        payload = await _fetch_page(slug, 1, client)
    except (httpx.HTTPError, ValueError, TypeError):
        return None

    return {"slug": slug, "jobs": payload["count"]}


register("manatal", discover, cost=10, can_handle=can_handle, rich=True)
