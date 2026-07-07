"""Greenhouse JSON API monitor.

Public API: GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Returns full job data — title, HTML description, locations, departments, etc.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from src.core.monitors import (
    BoardGoneError,
    DiscoveredJob,
    fetch_page_text,
    register,
    slugs_from_url,
)
from src.core.monitors.raw import save_json_response
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

MAX_JOBS = 50_000

_PAGE_PATTERNS = [
    re.compile(r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    re.compile(r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    re.compile(r"job-boards(?:\.[\w-]+)?\.greenhouse\.io/([\w-]+)"),
    re.compile(r'"urlToken":"([\w-]+)"'),
    re.compile(r"boardToken[\"']?\s*[:=]\s*[\"']([\w-]+)[\"']", re.IGNORECASE),
]

_IGNORE_TOKENS = frozenset({"embed", "v1", "api", "js", "css", "assets"})

# Href-less anchors whose text is a collapsed-section toggle. Some Greenhouse
# boards (Varda Space, etc.) paste HTML from WYSIWYG editors that leave
# stranded ``<a>Read more</a>`` / ``<a>Learn more</a>`` artifacts — no href,
# purely decorative. Strip them so they don't trail the description.
_STRAY_TOGGLE_RE = re.compile(
    r"<a(?![^>]*\shref=)[^>]*>\s*(?:read|learn|show|see|view)\s+more\s*</a>",
    re.IGNORECASE,
)


def _clean_description(html: str | None) -> str | None:
    if not html:
        return html
    cleaned = _STRAY_TOGGLE_RE.sub("", html)
    return cleaned


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
        description=_clean_description(job.get("content")),
        locations=locations or None,
        date_posted=job.get("first_published"),
        language=job.get("language"),
        metadata=metadata or None,
    )


def _token_from_url(board_url: str) -> str | None:
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)

    def _clean(token: str | None) -> str | None:
        if not token:
            return None
        token = token.strip()
        if not token:
            return None
        return token if token not in _IGNORE_TOKENS else None

    if host == "boards-api.greenhouse.io":
        if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "boards":
            return _clean(parts[2])
        return None

    if host == "boards.greenhouse.io":
        if parts and parts[0] == "embed":
            return _clean(query.get("for", [None])[0])
        if parts:
            return _clean(parts[0])
        return None

    if re.fullmatch(r"job-boards(?:\.[\w-]+)?\.greenhouse\.io", host):
        if parts:
            return _clean(parts[0])
        return _clean(query.get("url_token", [None])[0] or query.get("for", [None])[0])

    return None


def _api_url(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


async def _probe_token(token: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Greenhouse API for a token. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(token), params={"content": "false"})
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        jobs = data.get("jobs")
        if isinstance(jobs, list):
            return True, len(jobs)
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(token: str, client: httpx.AsyncClient) -> int | None:
    """Lightweight API call to get the job count for a token."""
    try:
        resp = await client.get(_api_url(token), params={"content": "false"})
        if resp.status_code != 200:
            return None
        data = resp.json()
        jobs = data.get("jobs")
        return len(jobs) if isinstance(jobs, list) else None
    except Exception:
        return None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
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
    if response.status_code == 404:
        # Greenhouse returns 404 when the board token has been deleted
        # upstream — i.e. the company removed the board. Surface this
        # as a structural "gone" signal so the board processor can
        # disable in one shot instead of grinding through 5 retries.
        # See issue #2215.
        raise BoardGoneError(
            f"Greenhouse board token {token!r} returned 404",
            url=str(response.url),
        )
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
        return truncated_rich_result(jobs)

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Greenhouse: domain check -> page HTML scan -> slug-based API probe."""
    token = _token_from_url(url)
    if token:
        if client is not None:
            count = await _fetch_job_count(token, client)
            if count is not None:
                return {"token": token, "jobs": count}
        return {"token": token}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    log.info("greenhouse.detected_in_page", url=url, board_token=found)
                    count = await _fetch_job_count(found, client)
                    result: dict = {"token": found}
                    if count is not None:
                        result["jobs"] = count
                    return result

    for slug in slugs_from_url(url):
        found, count = await _probe_token(slug, client)
        if found:
            log.info("greenhouse.detected_by_probe", url=url, board_token=slug)
            result = {"token": slug}
            if count is not None:
                result["jobs"] = count
            return result

    return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    token = metadata.get("token") or _token_from_url(board_url)
    if not token:
        return
    await save_json_response(
        artifact_dir,
        client,
        _api_url(token),
        params={"content": "true"},
    )


register("greenhouse", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
