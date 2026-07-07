"""Pinpoint HQ career page monitor.

Public API:
  List: GET https://{subdomain}.pinpointhq.com/postings.json

Returns full job data in a single request — no pagination needed.
The response contains a ``data`` array with complete posting objects
including HTML descriptions, locations, compensation, and metadata.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type, normalize_salary_unit
from src.core.monitors import DiscoveredJob, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

MAX_JOBS = 50_000

_DOMAIN_RE = re.compile(r"^([\w-]+)\.pinpointhq\.com$")

_PAGE_PATTERNS = [
    re.compile(r"([\w-]+)\.pinpointhq\.com"),
]

_IGNORE_SLUGS = frozenset({"api", "www", "app", "docs", "help", "support", "status"})

# Pinpoint employment_type codes are passed through unchanged — the
# central :func:`src.core.enum_normalize.normalize_employment_type`
# handles ``full_time``/``permanent_full_time``/``contract_temp``/
# ``volunteer`` etc.  The ``employment_type_text`` fallback below is
# used when the upstream code is missing/empty.  ``workplace_type``
# (``remote``/``hybrid``/``onsite``) is funnelled through
# :func:`src.core.enum_normalize.normalize_job_location_type`.


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Pinpoint subdomain from a *.pinpointhq.com URL."""
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _api_url(slug: str) -> str:
    return f"https://{slug}.pinpointhq.com/postings.json"


def _build_description(posting: dict) -> str | None:
    """Combine description + responsibilities + skills + benefits into HTML."""
    parts: list[str] = []
    for key, header_key in (
        ("description", None),
        ("key_responsibilities", "key_responsibilities_header"),
        ("skills_knowledge_expertise", "skills_knowledge_expertise_header"),
        ("benefits", "benefits_header"),
    ):
        text = posting.get(key)
        if not text or not isinstance(text, str):
            continue
        header = posting.get(header_key) if header_key else None
        if header:
            parts.append(f"<h3>{header}</h3>\n{text}")
        else:
            parts.append(text)
    return "\n".join(parts) if parts else None


def _build_location(posting: dict) -> list[str] | None:
    """Extract location from the nested location object."""
    loc = posting.get("location")
    if not loc or not isinstance(loc, dict):
        return None

    name = loc.get("name")
    if name and isinstance(name, str):
        return [name]

    # Fallback: build from parts
    city = loc.get("city")
    province = loc.get("province")
    parts = [p for p in (city, province) if p]
    if parts:
        return [", ".join(parts)]

    return None


def _parse_salary(posting: dict) -> dict | None:
    """Extract salary from compensation fields."""
    sal_min = posting.get("compensation_minimum")
    sal_max = posting.get("compensation_maximum")
    if sal_min is None and sal_max is None:
        return None
    if not posting.get("compensation_visible", True):
        return None

    currency = posting.get("compensation_currency")
    # Pinpoint defaults to ``year`` when frequency is missing/unknown.
    # ``two_weeks`` is normalised to ``week`` by the central helper.
    unit = normalize_salary_unit(posting.get("compensation_frequency")) or "year"

    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_job(posting: dict) -> DiscoveredJob | None:
    """Map a Pinpoint posting to a DiscoveredJob."""
    url = posting.get("url")
    if not url:
        return None

    # Employment type — pass through raw upstream code; if the API
    # didn't supply a code, fall back to the human-readable text field
    # (the central normalizer handles both shapes).
    employment_type = posting.get("employment_type") or posting.get("employment_type_text") or None

    # Workplace / job location type
    workplace_raw = posting.get("workplace_type") or ""
    job_location_type = normalize_job_location_type(workplace_raw, default=None)

    # Metadata
    metadata: dict = {}
    job_obj = posting.get("job")
    if isinstance(job_obj, dict):
        dept = job_obj.get("department")
        if isinstance(dept, dict) and dept.get("name"):
            metadata["department"] = dept["name"]
        div = job_obj.get("division")
        if isinstance(div, dict) and div.get("name"):
            metadata["division"] = div["name"]
        req_id = job_obj.get("requisition_id")
        if req_id:
            metadata["requisition_id"] = req_id

    return DiscoveredJob(
        url=url,
        title=posting.get("title"),
        description=_build_description(posting),
        locations=_build_location(posting),
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=posting.get("deadline_at"),
        base_salary=_parse_salary(posting),
        metadata=metadata or None,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Pinpoint public postings API."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    slug = metadata.get("slug") or _slug_from_url(board_url)
    if not slug:
        raise ValueError(
            f"Cannot derive Pinpoint slug from board URL {board_url!r} and no slug in metadata"
        )

    url = _api_url(slug)
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()

    data = response.json()
    raw_postings = data.get("data", [])

    jobs: list[DiscoveredJob] = []
    for raw in raw_postings:
        parsed = _parse_job(raw)
        if parsed:
            jobs.append(parsed)

    if len(jobs) > MAX_JOBS:
        log.warning("pinpoint.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
        return truncated_rich_result(jobs)

    return jobs


async def _probe_api(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Pinpoint postings API. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(slug), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        postings = data.get("data")
        if isinstance(postings, list):
            return True, len(postings)
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(
    slug: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_api(slug, client)
    if found:
        return count
    return None


async def _probe_template_slug(
    slug: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_api(slug, client)


def _slug_result(slug: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    result: dict = {"slug": slug}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Pinpoint: domain check -> page HTML scan -> slug-based API probe."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="pinpoint",
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


register("pinpoint", discover, cost=10, can_handle=can_handle, rich=True)
