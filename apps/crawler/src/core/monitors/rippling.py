"""Rippling ATS Job Board API monitor.

Public API:
  List:   GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs

The list endpoint returns all jobs (no pagination) with basic metadata.
The monitor extracts UUIDs and constructs posting URLs.  Detail fetching
is handled by the scraper (``src/core/scrapers/rippling``).
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import structlog

from src.core.monitors import register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.raw import save_json_response
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000

_API_BASE = "https://api.rippling.com/platform/api/ats/v1/board"

# Matches ats.rippling.com or ats.us1.rippling.com, with optional locale prefix
_URL_RE = re.compile(r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs")

_PAGE_PATTERNS = [
    re.compile(r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/jobs"),
    re.compile(r"api\.rippling\.com/platform/api/ats/\w+/board/([\w-]+)"),
]

_IGNORE_SLUGS = frozenset({"api", "platform", "static", "assets", "js", "css"})


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Rippling board slug from an ats.rippling.com URL."""
    match = _URL_RE.search(board_url)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_list_url(slug: str) -> str:
    return f"{_API_BASE}/{slug}/jobs"


def _posting_url(slug: str, uuid: str) -> str:
    """Build the public posting URL for a Rippling job."""
    return f"https://ats.rippling.com/{slug}/jobs/{uuid}"


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Fetch job listing URLs from the Rippling public API.

    Lists all jobs via the V1 endpoint (single request, no pagination)
    and constructs posting URLs from slug + uuid.
    """
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Rippling board slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    resp = await client.get(_api_list_url(slug))
    resp.raise_for_status()

    job_list: list[dict] = resp.json()
    if not isinstance(job_list, list):
        return set()

    uuids = [j["uuid"] for j in job_list if j.get("uuid")]
    log.info("rippling.listed", slug=slug, jobs=len(uuids))

    if len(uuids) > MAX_JOBS:
        log.warning("rippling.truncated", slug=slug, total=len(uuids), cap=MAX_JOBS)
        return truncated_url_result({_posting_url(slug, uuid) for uuid in uuids})

    return {_posting_url(slug, uuid) for uuid in uuids}


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Rippling API for a slug. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_list_url(slug))
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        if isinstance(data, list):
            return True, len(data)
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(
    slug: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_slug(slug, client)
    if found:
        return count
    return None


async def _probe_template_slug(
    slug: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_slug(slug, client)


def _slug_result(slug: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    result: dict = {"slug": slug}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Rippling: URL pattern -> page HTML scan -> slug-based API probe."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="rippling",
        token_from_url=_slug_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=_IGNORE_SLUGS,
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_template_slug,
        initial_context=None,
        result_builder=_slug_result,
        page_token_probe=_probe_template_slug,
        log_token_field="slug",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    slug = metadata.get("slug") or _slug_from_url(board_url)
    if not slug:
        return
    await save_json_response(artifact_dir, client, _api_list_url(slug))


register("rippling", discover, cost=10, can_handle=can_handle, rich=False, save_raw=save_raw)
