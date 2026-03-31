"""Lever Postings API monitor.

Public API: GET https://api.lever.co/v0/postings/{SITE}
Returns full job data. Supports pagination via skip/limit. Rate limit: 2 req/sec.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 50_000
BATCH_SIZE = 100

_INTERVAL_TO_UNIT: dict[str, str] = {
    "per-year-salary": "year",
    "per-month-salary": "month",
    "per-hour-wage": "hour",
}

_PAGE_PATTERNS = [
    re.compile(r"api\.(?:eu\.)?lever\.co/v0/postings/([\w-]+)"),
    re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)"),
]

_EU_DOMAIN = re.compile(r"(?:api|jobs)\.eu\.lever\.co/")

_IGNORE_TOKENS = frozenset({"v0", "api", "js", "css", "assets"})


def _build_description(posting: dict) -> str | None:
    parts: list[str] = []
    description = posting.get("description")
    if description:
        parts.append(description)
    for item in posting.get("lists", []):
        text = item.get("text", "")
        content = item.get("content", "")
        if text or content:
            parts.append(f"<h3>{text}</h3><ul>{content}</ul>")
    additional = posting.get("additional")
    if additional:
        parts.append(additional)
    return "\n".join(parts) if parts else None


def _parse_salary(salary_range: dict | None) -> dict | None:
    if not salary_range:
        return None
    currency = salary_range.get("currency")
    sal_min = salary_range.get("min")
    sal_max = salary_range.get("max")
    interval = salary_range.get("interval", "")
    if sal_min is None and sal_max is None:
        return None
    return {
        "currency": currency,
        "min": sal_min,
        "max": sal_max,
        "unit": _INTERVAL_TO_UNIT.get(interval, interval),
    }


def _parse_job(posting: dict) -> DiscoveredJob | None:
    url = posting.get("hostedUrl")
    if not url:
        return None

    categories = posting.get("categories", {})
    all_locations = categories.get("allLocations", [])
    if not all_locations:
        single = categories.get("location")
        all_locations = [single] if single else []

    metadata: dict = {}
    team = categories.get("team")
    if team:
        metadata["team"] = team
    department = categories.get("department")
    if department:
        metadata["department"] = department
    posting_id = posting.get("id")
    if posting_id:
        metadata["id"] = posting_id

    return DiscoveredJob(
        url=url,
        title=posting.get("text"),
        description=_build_description(posting),
        locations=all_locations or None,
        employment_type=categories.get("commitment"),
        job_location_type=posting.get("workplaceType"),
        base_salary=_parse_salary(posting.get("salaryRange")),
        metadata=metadata or None,
    )


def _token_from_url(board_url: str) -> str | None:
    match = re.search(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)", board_url)
    if match and match.group(1) not in _IGNORE_TOKENS:
        return match.group(1)
    return None


def _region_from_url(url: str) -> str | None:
    """Return 'eu' if the URL is on a Lever EU domain, else None."""
    return "eu" if _EU_DOMAIN.search(url) else None


def _api_url(token: str, region: str | None = None) -> str:
    if region == "eu":
        return f"https://api.eu.lever.co/v0/postings/{token}"
    return f"https://api.lever.co/v0/postings/{token}"


async def _probe_token(
    token: str, client: httpx.AsyncClient, region: str | None = None
) -> tuple[bool, int | None]:
    """Probe the Lever API for a token. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(token, region), params={"limit": 100})
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        if isinstance(data, list):
            count = len(data)
            if count >= 100:
                return True, "100+"  # type: ignore[return-value]
            return True, count
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(
    token: str, client: httpx.AsyncClient, region: str | None = None
) -> int | str | None:
    """Lightweight API call to get the job count for a token."""
    try:
        resp = await client.get(_api_url(token, region), params={"limit": 100})
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, list):
            return "100+" if len(data) >= 100 else len(data)
        return None
    except Exception:
        return None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Lever public API with pagination."""
    metadata = board.get("metadata") or {}
    token = metadata.get("token") or _token_from_url(board["board_url"])

    if not token:
        raise ValueError(
            f"Cannot derive Lever token from board URL {board['board_url']!r} "
            "and no token in metadata"
        )

    region = metadata.get("region") or _region_from_url(board["board_url"])
    url = _api_url(token, region)
    jobs: list[DiscoveredJob] = []
    skip = 0

    while True:
        response = await client.get(url, params={"limit": BATCH_SIZE, "skip": skip})
        response.raise_for_status()

        batch: list[dict] = response.json()
        for raw in batch:
            parsed = _parse_job(raw)
            if parsed:
                jobs.append(parsed)

        if len(batch) < BATCH_SIZE:
            break

        skip += BATCH_SIZE

        if len(jobs) >= MAX_JOBS:
            log.warning("lever.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
            jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]
            break

        await asyncio.sleep(0.5)

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Lever: domain check -> page HTML scan -> slug-based API probe."""
    region = _region_from_url(url)
    token = _token_from_url(url)
    if token:
        if client is not None:
            count = await _fetch_job_count(token, client, region)
            if count is not None:
                result: dict = {"token": token, "jobs": count}
                if region:
                    result["region"] = region
                return result
        result = {"token": token}
        if region:
            result["region"] = region
        return result

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found = match.group(1)
                if found not in _IGNORE_TOKENS:
                    log.info("lever.detected_in_page", url=url, board_token=found)
                    # Detect region from the matched URL in the HTML
                    html_region = region
                    if not html_region and match.group(0) and ".eu.lever.co" in match.group(0):
                        html_region = "eu"
                    count = await _fetch_job_count(found, client, html_region)
                    result = {"token": found}
                    if html_region:
                        result["region"] = html_region
                    if count is not None:
                        result["jobs"] = count
                    return result

    for slug in slugs_from_url(url):
        found, count = await _probe_token(slug, client, region)
        if found:
            log.info("lever.detected_by_probe", url=url, board_token=slug)
            result = {"token": slug}
            if region:
                result["region"] = region
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("lever", discover, cost=10, can_handle=can_handle, rich=True)
