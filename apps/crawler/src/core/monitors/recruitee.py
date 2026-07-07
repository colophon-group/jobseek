"""Recruitee Careers Site API monitor.

Public API: GET https://{slug}.recruitee.com/api/offers
Returns full job data in a single request — no pagination needed.
Also works on custom domains: GET https://{custom-domain}/api/offers
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_salary_unit
from src.core.monitors import (
    BoardGoneError,
    DiscoveredJob,
    register,
    slug_guess_allowed,
)
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.raw import save_json_response
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

MAX_JOBS = 50_000

_DOMAIN_RE = re.compile(r"^([\w-]+)\.recruitee\.com$")

_PAGE_PATTERNS = (
    re.compile(r"([\w-]+)\.recruitee\.com"),
    re.compile(r"\b(?:recruiteecdn\.com|window\.recruitee)\b()"),
)

_IGNORE_SLUGS = frozenset({"api", "www", "app", "docs", "help", "support", "status"})

# Recruitee employment-type codes (``fulltime_permanent``,
# ``parttime_fixed_term``, ``freelance``, ``traineeship``, …) pass
# through unchanged — the central
# :func:`src.core.enum_normalize.normalize_employment_type` handles
# them.


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Recruitee company slug from a *.recruitee.com URL."""
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_base_from_url(board_url: str) -> str | None:
    """Derive the API base URL. Returns https://{host} for any Recruitee URL."""
    parsed = urlparse(board_url)
    host = parsed.hostname
    if host:
        scheme = parsed.scheme or "https"
        return f"{scheme}://{host}"
    return None


def _api_url(api_base: str) -> str:
    return f"{api_base}/api/offers"


def _parse_locations(offer: dict) -> list[str] | None:
    """Extract locations from a Recruitee offer."""
    locations: list[str] = []
    seen: set[str] = set()

    # Structured locations array (preferred)
    for loc in offer.get("locations", []):
        city = loc.get("city", "")
        country = loc.get("country", "")
        parts = [p for p in (city, country) if p]
        name = ", ".join(parts)
        if name and name not in seen:
            locations.append(name)
            seen.add(name)

    # Fallback to flat location string
    if not locations:
        flat_loc = offer.get("location")
        if flat_loc and isinstance(flat_loc, str):
            locations.append(flat_loc)

    return locations or None


def _parse_job_location_type(offer: dict) -> str | None:
    """Derive job_location_type from boolean flags."""
    if offer.get("remote"):
        return "remote"
    if offer.get("hybrid"):
        return "hybrid"
    if offer.get("on_site"):
        return "onsite"
    return None


def _parse_salary(offer: dict) -> dict | None:
    """Extract salary from the salary object."""
    salary = offer.get("salary")
    if not salary or not isinstance(salary, dict):
        return None
    sal_min = salary.get("min")
    sal_max = salary.get("max")
    if sal_min is None and sal_max is None:
        return None
    currency = salary.get("currency")
    # Recruitee defaults to ``year`` when period is missing/unknown.
    unit = normalize_salary_unit(salary.get("period")) or "year"
    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_job(offer: dict) -> DiscoveredJob | None:
    """Map a Recruitee offer to a DiscoveredJob."""
    url = offer.get("careers_url")
    if not url:
        return None

    # Combine description + requirements into a single HTML body
    parts: list[str] = []
    desc = offer.get("description")
    if desc:
        parts.append(desc)
    reqs = offer.get("requirements")
    if reqs:
        parts.append(reqs)
    description = "\n".join(parts) if parts else None

    # Employment type — pass through raw upstream code.
    employment_type = offer.get("employment_type_code") or None

    # Metadata
    metadata: dict = {}
    department = offer.get("department")
    if department:
        metadata["department"] = department
    tags = offer.get("tags")
    if tags and isinstance(tags, list):
        metadata["tags"] = tags
    category = offer.get("category_code")
    if category:
        metadata["category"] = category
    offer_id = offer.get("id")
    if offer_id:
        metadata["id"] = offer_id

    return DiscoveredJob(
        url=url,
        title=offer.get("title"),
        description=description,
        locations=_parse_locations(offer),
        employment_type=employment_type,
        job_location_type=_parse_job_location_type(offer),
        date_posted=offer.get("published_at"),
        base_salary=_parse_salary(offer),
        metadata=metadata or None,
    )


async def _probe_api(api_base: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Recruitee API. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(api_base), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        offers = data.get("offers")
        if isinstance(offers, list):
            return True, len(offers)
        return False, None
    except Exception:
        log.debug(
            "recruitee.probe_failed",
            probe="api",
            api_base=api_base,
            url=_api_url(api_base),
            exc_info=True,
        )
        return False, None


def _api_base_from_slug(slug: str) -> str | None:
    if not slug:
        return None
    return f"https://{slug}.recruitee.com"


async def _fetch_template_count(
    token: str,
    client: httpx.AsyncClient,
    context: str | None,
) -> ProbeCount | None:
    base = context or _api_base_from_slug(token)
    if base is None:
        return None
    found, count = await _probe_api(base, client)
    return count if found else None


async def _probe_template_slug(
    token: str,
    client: httpx.AsyncClient,
    context: str | None,
) -> ProbeResult:
    _ = context
    base = _api_base_from_slug(token)
    if base is None:
        return False, None
    return await _probe_api(base, client)


async def _probe_page_token(
    token: str,
    client: httpx.AsyncClient,
    context: str | None,
) -> ProbeResult:
    _ = token
    if context is None:
        return False, None
    return await _probe_api(context, client)


def _build_template_result(
    slug: str,
    count: ProbeCount | None,
    api_base: str | None,
) -> dict:
    base = api_base or _api_base_from_slug(slug)
    result: dict = {}
    if slug:
        result["slug"] = slug
    if base is not None:
        result["api_base"] = base
    if count is not None:
        result["jobs"] = count
    return result


async def discover(
    board: dict, client: httpx.AsyncClient, pw=None
) -> list[DiscoveredJob] | MonitorResult:
    """Fetch job listings from the Recruitee public API."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    # Determine API base: explicit api_base in metadata, or from slug, or from board URL
    api_base = metadata.get("api_base")
    if not api_base:
        slug = metadata.get("slug") or _slug_from_url(board_url)
        api_base = (
            f"https://{slug}.recruitee.com"
            if slug
            else _api_base_from_url(board_url)  # Custom domain
        )

    if not api_base:
        raise ValueError(
            f"Cannot derive Recruitee API base from board URL {board_url!r} "
            "and no slug or api_base in metadata"
        )

    url = _api_url(api_base)
    response = await client.get(url, follow_redirects=True)
    if response.status_code == 404:
        # Recruitee 404s when the company subdomain (slug) has been
        # removed. Surface as a "gone" signal for one-shot disable.
        # See issue #2215.
        raise BoardGoneError(
            f"Recruitee API base {api_base!r} returned 404",
            url=str(response.url),
        )
    response.raise_for_status()

    data = response.json()
    raw_offers = data.get("offers", [])

    jobs: list[DiscoveredJob] = []
    for raw in raw_offers:
        if raw.get("status") != "published":
            continue
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("recruitee.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        return truncated_rich_result(jobs)

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Recruitee: domain check -> page HTML scan -> slug-based API probe."""
    _ = pw

    def context_from_match(
        match: re.Match[str],
        context: str | None,
    ) -> str | None:
        _ = (match, context)
        return _api_base_from_url(url)

    return await ats_can_handle(
        url,
        client,
        monitor_name="recruitee",
        token_from_url=_slug_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=_IGNORE_SLUGS,
        fetch_job_count=_fetch_template_count,
        api_probe=_probe_template_slug,
        initial_context=None,
        result_builder=_build_template_result,
        context_from_match=context_from_match,
        page_token_probe=_probe_page_token,
        allow_slug_guess=slug_guess_allowed(),
        log_token_field="slug",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    api_base = metadata.get("api_base")
    if not api_base:
        slug = metadata.get("slug") or _slug_from_url(board_url)
        api_base = f"https://{slug}.recruitee.com" if slug else _api_base_from_url(board_url)
    if not api_base:
        return
    await save_json_response(
        artifact_dir,
        client,
        _api_url(api_base),
        follow_redirects=True,
    )


register("recruitee", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
