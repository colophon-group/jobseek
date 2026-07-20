"""Paylocity Recruiting embedded-data monitor.

Paylocity renders the public job list into ``window.pageData`` in the board
HTML.  The list is available without JavaScript even when the rest of the
page displays Paylocity's unsupported-browser fallback.

The embedded records contain clean summary fields but only a truncated
description.  This monitor therefore returns rich summaries without a
description; the paired Paylocity scraper hydrates full detail-page content
on the normal scrape schedule.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

_PAGE_DATA_RE = re.compile(r"\bwindow\.pageData\s*=\s*")


def _is_paylocity_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    return host.endswith("recruiting.paylocity.com") and "/recruiting/jobs/" in path


def _extract_page_data(html: str) -> dict | None:
    """Decode the JSON object assigned to ``window.pageData``.

    ``JSONDecoder.raw_decode`` is intentionally used instead of a non-greedy
    regular expression: job text can itself contain braces or ``};``.
    """
    match = _PAGE_DATA_RE.search(html)
    if not match:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(html, match.end())
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _detail_url(board_url: str, job_id: object) -> str:
    parsed = urlparse(board_url)
    return f"{parsed.scheme}://{parsed.netloc}/Recruiting/Jobs/Details/{job_id}"


def _location_type(raw: dict) -> str | None:
    location = str(raw.get("LocationName") or "").lower()
    if "hybrid" in location:
        return "hybrid"
    if raw.get("IsRemote") or "remote" in location:
        return "remote"
    return None


def _parse_job(raw: dict, board_url: str) -> DiscoveredJob | None:
    job_id = raw.get("JobId")
    if job_id is None:
        return None

    location = raw.get("LocationName")
    locations = [location] if isinstance(location, str) and location.strip() else None

    metadata: dict = {"job_id": job_id}
    department = raw.get("HiringDepartment")
    if isinstance(department, str) and department:
        metadata["department"] = department

    return DiscoveredJob(
        url=_detail_url(board_url, job_id),
        title=raw.get("JobTitle"),
        locations=locations,
        job_location_type=_location_type(raw),
        date_posted=raw.get("PublishedDate"),
        metadata=metadata,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch the board HTML and return the embedded Paylocity job summaries."""
    _ = pw
    board_url = board["board_url"]
    page = await fetch_text_page_with_retry(client, board_url)
    if page is None:
        raise BoardGoneError("Paylocity board no longer exists", url=board_url)

    page_data = _extract_page_data(page)
    if page_data is None:
        raise ValueError(f"Paylocity pageData not found at {board_url!r}")

    raw_jobs = page_data.get("Jobs")
    if not isinstance(raw_jobs, list):
        raise ValueError(f"Paylocity pageData.Jobs is not a list at {board_url!r}")

    jobs: list[DiscoveredJob] = []
    for raw in raw_jobs:
        if not isinstance(raw, dict):
            continue
        job = _parse_job(raw, board_url)
        if job is not None:
            jobs.append(job)
    log.info("paylocity.discovered", board_url=board_url, jobs=len(jobs))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect Paylocity public job-list URLs, including empty boards."""
    _ = pw
    if not _is_paylocity_url(url):
        return None

    if client is None:
        return {}

    try:
        page = await fetch_text_page_with_retry(client, url)
        if page is None:
            return None
        page_data = _extract_page_data(page)
        if page_data is not None and isinstance(page_data.get("Jobs"), list):
            return {"jobs": len(page_data["Jobs"])}
    except TDMReservedError:
        raise
    except Exception:
        log.debug("paylocity.probe_failed", url=url, exc_info=True)
    return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    """Save the server-rendered listing HTML for debugging."""
    _ = metadata
    await save_text_response(
        artifact_dir,
        client,
        board_url,
        filename="page.html",
        follow_redirects=True,
    )


register(
    "paylocity",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    save_raw=save_raw,
)
