"""Comeet hosted careers monitor.

Comeet renders each public board with a ``COMPANY_POSITIONS_DATA`` JSON
assignment containing every active posting and its full structured content.
Reading that payload is both cheaper and more complete than rendering the
Angular page and scraping individual job pages.
"""

from __future__ import annotations

import html
import json
from urllib.parse import urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type
from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

_POSITIONS_MARKER = "COMPANY_POSITIONS_DATA = "
_COMEET_HOSTS = frozenset({"comeet.com", "www.comeet.com"})


def _board_parts(url: str) -> tuple[str, str] | None:
    """Return ``(company, board_id)`` for a Comeet hosted-board URL."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in _COMEET_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[0] != "jobs":
        return None
    return parts[1], parts[2]


def _board_url(url: str) -> str | None:
    parts = _board_parts(url)
    if not parts:
        return None
    company, board_id = parts
    return f"https://www.comeet.com/jobs/{company}/{board_id}"


def _extract_positions(page: str) -> list[dict]:
    """Decode Comeet's JSON assignment without regex-balancing hazards."""
    marker_index = page.find(_POSITIONS_MARKER)
    if marker_index < 0:
        return []

    payload = page[marker_index + len(_POSITIONS_MARKER) :].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError:
        log.warning("comeet.positions_decode_failed")
        return []

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _location(raw: dict) -> list[str] | None:
    location = raw.get("location")
    if isinstance(location, str):
        name = location.strip()
        return [name] if name else None
    if not isinstance(location, dict):
        return None

    name = location.get("name")
    if isinstance(name, str) and name.strip():
        return [name.strip()]

    parts = [location.get(key) for key in ("city", "state", "country")]
    fallback = ", ".join(str(part).strip() for part in parts if part)
    return [fallback] if fallback else None


def _content(raw: dict) -> tuple[str | None, dict | None]:
    custom_fields = raw.get("custom_fields")
    if not isinstance(custom_fields, dict):
        return None, None
    details = custom_fields.get("details")
    if not isinstance(details, list):
        return None, None

    sections: list[str] = []
    extras: dict[str, str] = {}
    for detail in details:
        if not isinstance(detail, dict):
            continue
        name = detail.get("name")
        value = detail.get("value")
        if not isinstance(value, str) or not value.strip():
            continue

        label = str(name).strip() if name else "Details"
        sections.append(f"<h3>{html.escape(label)}</h3>\n{value.strip()}")

        normalized = label.lower()
        if "responsib" in normalized:
            extras["responsibilities"] = value.strip()
        elif "requirement" in normalized or "skill set" in normalized:
            extras["qualifications"] = value.strip()

    description = "\n".join(sections) or None
    return description, extras or None


def _parse_job(raw: dict) -> DiscoveredJob | None:
    url = (
        raw.get("url_comeet_hosted_page")
        or raw.get("url_recruit_hosted_page")
        or raw.get("url_active_page")
        or raw.get("url_detected_page")
    )
    if not isinstance(url, str) or not url:
        return None

    description, extras = _content(raw)
    workplace_type = raw.get("workplace_type")

    metadata = {
        key: raw[key]
        for key in ("uid", "department", "experience_level", "company_name", "time_updated")
        if raw.get(key) not in (None, "")
    }

    return DiscoveredJob(
        url=url,
        title=raw.get("name") or None,
        description=description,
        locations=_location(raw),
        employment_type=raw.get("employment_type") or None,
        job_location_type=(
            normalize_job_location_type(workplace_type, default=None) if workplace_type else None
        ),
        extras=extras,
        metadata=metadata or None,
    )


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob]:
    """Fetch and parse all active jobs from a Comeet hosted board."""
    url = _board_url(board["board_url"])
    if not url:
        raise ValueError(f"Cannot derive Comeet board from {board['board_url']!r}")

    page = await fetch_page_text(url, client, max_chars=20_000_000)
    if page is None:
        raise ValueError(f"Failed to fetch Comeet board {url!r}")
    jobs = [job for raw in _extract_positions(page) if (job := _parse_job(raw))]
    log.info("comeet.discovered", board_url=url, jobs=len(jobs))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect Comeet URLs and verify their embedded positions payload."""
    parts = _board_parts(url)
    if not parts:
        return None
    company, board_id = parts
    metadata = {"company": company, "board_id": board_id}
    if client is None:
        return metadata

    board_url = _board_url(url)
    page = await fetch_page_text(board_url, client, max_chars=20_000_000)
    if page is None or _POSITIONS_MARKER not in page:
        return None
    positions = _extract_positions(page)

    return {**metadata, "jobs": len(positions)}


register("comeet", discover, cost=10, can_handle=can_handle, rich=True)
