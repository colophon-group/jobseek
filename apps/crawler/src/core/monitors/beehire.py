"""Beehire public career-page monitor.

Beehire career pages load all active campaigns from the unauthenticated
``users/getPublicCampaigns/{slug}`` endpoint.  The payload already contains
the fields displayed by the board, including HTML descriptions and structured
locations, so this monitor returns rich jobs without a detail scraper.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors.raw import save_json_response
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

MAX_JOBS = 50_000
_ORIGIN = "https://app.beehire.com"
_HOST = "app.beehire.com"
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}", re.IGNORECASE)
_LANGUAGES = {
    "0": "en",
    "1": "fr",
    "2": "nl",
    "3": "de",
    "4": "pt",
    "5": "es",
    "6": "it",
}


def _slug_from_url(url: str) -> str | None:
    """Extract a Beehire employer slug from a public career/feed URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() != _HOST:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() not in {"career", "careerrss"}:
        return None
    slug = parts[1]
    return slug.lower() if _SLUG_RE.fullmatch(slug) else None


def _api_url(slug: str) -> str:
    return f"{_ORIGIN}/users/getPublicCampaigns/{slug}"


def _localized_value(value: object, language: object = None) -> str | None:
    """Select the campaign language, English, or first non-empty text."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if not isinstance(value, dict):
        return None

    preferred = str(language) if language is not None else None
    for key in (preferred, "0"):
        candidate = value.get(key) if key is not None else None
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    for candidate in value.values():
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _location(raw: dict) -> list[str] | None:
    location = raw.get("location")
    if not isinstance(location, dict):
        return None

    name = location.get("name")
    if isinstance(name, str) and name.strip():
        return [name.strip()]
    if location.get("isWorldwide"):
        return ["Worldwide"]

    parts: list[str] = []
    for key in ("city", "state", "country"):
        value = location.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return [", ".join(parts)] if parts else None


def _job_url(raw: dict) -> str | None:
    invite_link = raw.get("inviteLink")
    if isinstance(invite_link, str) and invite_link.strip():
        return urljoin(f"{_ORIGIN}/", invite_link.strip())
    invite_key = raw.get("inviteKey")
    if isinstance(invite_key, str) and invite_key.strip():
        return f"{_ORIGIN}/invite/{invite_key.strip()}"
    return None


def _localizations(raw: dict, locations: list[str] | None) -> dict | None:
    raw_titles = raw.get("title")
    titles = raw_titles if isinstance(raw_titles, dict) else {}
    raw_descriptions = raw.get("fullDescription")
    if not isinstance(raw_descriptions, dict):
        raw_descriptions = raw.get("description")
    descriptions = raw_descriptions if isinstance(raw_descriptions, dict) else {}

    result: dict[str, dict] = {}
    for key in titles.keys() | descriptions.keys():
        locale = _LANGUAGES.get(str(key))
        if not locale:
            continue
        title = _localized_value({str(key): titles.get(key)}, key)
        description = _localized_value({str(key): descriptions.get(key)}, key)
        if title or description:
            result[locale] = {
                "title": title,
                "description": description,
                "locations": locations,
            }
    return result or None


def _parse_job(raw: dict) -> DiscoveredJob | None:
    url = _job_url(raw)
    language_key = raw.get("language")
    title = _localized_value(raw.get("title"), language_key)
    if not url or not title:
        return None

    description = _localized_value(raw.get("fullDescription"), language_key)
    if not description:
        description = _localized_value(raw.get("description"), language_key)
    locations = _location(raw)

    raw_details = raw.get("details")
    details = raw_details if isinstance(raw_details, dict) else {}
    raw_contract = details.get("contract")
    contract = raw_contract if isinstance(raw_contract, dict) else {}
    raw_categories = raw.get("jobCategories")
    categories = raw_categories if isinstance(raw_categories, list) else []

    metadata = {
        key: value
        for key, value in {
            "id": raw.get("id") or raw.get("_id"),
            "invite_key": raw.get("inviteKey"),
            "contract": contract or None,
            "deadline": raw.get("deadline"),
            "categories": [
                item.get("label")
                for item in categories
                if isinstance(item, dict) and item.get("label")
            ]
            or None,
        }.items()
        if value is not None and value != ""
    }

    language = _LANGUAGES.get(str(language_key))
    return DiscoveredJob(
        url=url,
        title=title,
        description=description,
        locations=locations,
        employment_type=contract.get("type") or contract.get("duration"),
        job_location_type=contract.get("remote"),
        date_posted=raw.get("created") or raw.get("createdAt"),
        language=language,
        localizations=_localizations(raw, locations),
        metadata=metadata or None,
    )


async def _fetch_payload(slug: str, client: httpx.AsyncClient) -> dict:
    response = await client.get(
        _api_url(slug),
        headers={"Accept": "application/json", "Referer": f"{_ORIGIN}/career/{slug}"},
        follow_redirects=True,
        timeout=30,
    )
    if response.status_code == 404:
        raise BoardGoneError(
            f"Beehire board {slug!r} returned 404",
            url=str(response.url),
            status_code=404,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("campaigns"), list):
        raise ValueError("Beehire public campaign response has no campaigns list")
    return payload


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | MonitorResult:
    """Fetch all currently public campaigns from a Beehire career page."""
    _ = pw
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"Cannot derive Beehire slug from {board['board_url']!r} and no valid slug in metadata"
        )
    slug = slug.lower()

    payload = await _fetch_payload(slug, client)
    raw_campaigns = payload["campaigns"]
    jobs: list[DiscoveredJob] = []
    seen_urls: set[str] = set()
    invalid_records = 0
    duplicate_records = 0
    for raw in raw_campaigns:
        if not isinstance(raw, dict):
            invalid_records += 1
            continue
        job = _parse_job(raw)
        if job is None:
            invalid_records += 1
            continue
        if job.url in seen_urls:
            duplicate_records += 1
            continue
        seen_urls.add(job.url)
        jobs.append(job)

    if raw_campaigns and not jobs:
        raise ValueError(f"Beehire public campaign response for {slug!r} has no valid jobs")

    truncated = bool(invalid_records or duplicate_records or len(raw_campaigns) > MAX_JOBS)
    if invalid_records or duplicate_records:
        log.warning(
            "beehire.invalid_records",
            slug=slug,
            invalid=invalid_records,
            duplicates=duplicate_records,
            rows=len(raw_campaigns),
        )
    if truncated:
        log.warning(
            "beehire.truncated",
            slug=slug,
            total=len(raw_campaigns),
            returned=len(jobs),
            cap=MAX_JOBS,
        )
        return truncated_rich_result(jobs)

    log.info("beehire.discovered", slug=slug, jobs=len(jobs))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect and verify a Beehire public career page, including empty boards."""
    _ = pw
    slug = _slug_from_url(url)
    if not slug:
        return None
    if client is None:
        return {"slug": slug}
    try:
        payload = await _fetch_payload(slug, client)
    except Exception:
        return None
    return {"slug": slug, "jobs": len(payload["campaigns"])}


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    slug = metadata.get("slug") or _slug_from_url(board_url)
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        return
    await save_json_response(
        artifact_dir,
        client,
        _api_url(slug.lower()),
        headers={"Accept": "application/json", "Referer": f"{_ORIGIN}/career/{slug.lower()}"},
    )


register("beehire", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
