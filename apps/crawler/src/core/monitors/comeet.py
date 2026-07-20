"""Comeet careers monitor.

Comeet exposes the same public positions dataset in two forms: hosted boards
embed ``COMPANY_POSITIONS_DATA`` in the page, while custom career sites embed
a tokenized ``careers-api`` URL. Supporting both here keeps tenant-specific
board configuration out of the scraper layer and also handles valid empty
feeds.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type
from src.core.monitors import BoardGoneError, DiscoveredJob, fetch_page_text, register
from src.core.monitors.raw import save_json_response, save_text_response
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

MAX_JOBS = 50_000

_POSITIONS_MARKER = "COMPANY_POSITIONS_DATA = "
_COMPANY_MARKER = "COMPANY_DATA = "
_HOSTED_HOSTS = frozenset({"comeet.com", "www.comeet.com"})
_API_HOSTS = frozenset({"comeet.co", "www.comeet.co"})
_API_PATH_RE = re.compile(r"^/careers-api/2\.0/company/(?P<company_id>[A-Za-z0-9.]+)/positions/?$")
_API_REF_RE = re.compile(
    r"https?://(?:www\.)?comeet\.co/careers-api/2\.0/company/"
    r"[A-Za-z0-9.]+/positions/?(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)


def _board_parts(url: str) -> tuple[str, str] | None:
    """Return ``(company, board_id)`` for a Comeet hosted-board URL."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in _HOSTED_HOSTS:
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


def _decode_assignment(page: str, marker: str, expected_type: type) -> object | None:
    marker_index = page.find(marker)
    if marker_index < 0:
        return None

    payload = page[marker_index + len(marker) :].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError:
        log.warning("comeet.assignment_decode_failed", marker=marker.strip())
        return None
    return value if isinstance(value, expected_type) else None


def _positions_assignment(page: str) -> list[dict] | None:
    value = _decode_assignment(page, _POSITIONS_MARKER, list)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


def _extract_positions(page: str) -> list[dict]:
    """Decode Comeet's embedded positions, preserving the legacy helper API."""
    return _positions_assignment(page) or []


def _company_assignment(page: str) -> dict | None:
    value = _decode_assignment(page, _COMPANY_MARKER, dict)
    return value if isinstance(value, dict) else None


def _api_url(company_id: str) -> str:
    return f"https://www.comeet.co/careers-api/2.0/company/{company_id}/positions"


def _clean_embedded_url(value: str) -> str:
    """Decode HTML and JavaScript ampersand escapes in an embedded API URL."""
    return html.unescape(value).replace(r"\u0026", "&").replace(r"\x26", "&")


def _credentials_from_api_url(url: str) -> tuple[str, str] | None:
    """Return public ``(company_id, token)`` credentials from an API URL."""
    parsed = urlparse(_clean_embedded_url(url))
    if (parsed.hostname or "").lower() not in _API_HOSTS:
        return None
    match = _API_PATH_RE.match(parsed.path)
    if not match:
        return None
    token = (parse_qs(parsed.query).get("token") or [None])[0]
    if not token:
        return None
    return match.group("company_id"), token


def _credentials_from_html(page: str) -> tuple[str, str] | None:
    """Find the public Comeet endpoint embedded in a custom careers page."""
    decoded = _clean_embedded_url(page)
    for match in _API_REF_RE.finditer(decoded):
        credentials = _credentials_from_api_url(match.group(0))
        if credentials:
            return credentials
    return None


def _credentials_from_board(board: dict) -> tuple[str, str] | None:
    metadata = board.get("metadata") or {}
    company_id = metadata.get("company_id")
    token = metadata.get("token")
    if company_id and token:
        return str(company_id), str(token)
    return _credentials_from_api_url(board.get("board_url") or "")


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


def _details(raw: dict) -> list[dict]:
    details = raw.get("details")
    if not isinstance(details, list):
        custom_fields = raw.get("custom_fields")
        details = custom_fields.get("details") if isinstance(custom_fields, dict) else None
    if not isinstance(details, list):
        return []
    valid = [item for item in details if isinstance(item, dict)]
    valid.sort(key=lambda item: item.get("order") if isinstance(item.get("order"), int) else 0)
    return valid


def _content(raw: dict) -> tuple[str | None, dict | None]:
    sections: list[str] = []
    qualifications: list[str] = []
    responsibilities: list[str] = []

    for detail in _details(raw):
        name = detail.get("name")
        value = detail.get("value")
        if not isinstance(value, str) or not value.strip():
            continue

        label = str(name).strip() if name else "Details"
        value = value.strip()
        sections.append(f"<h3>{html.escape(label)}</h3>\n{value}")

        normalized = label.casefold().replace("’", "'")
        if any(
            marker in normalized
            for marker in (
                "requirement",
                "qualification",
                "skill set",
                "who you are",
                "what you bring",
            )
        ):
            qualifications.append(value)
        if any(
            marker in normalized for marker in ("responsibilit", "what you'll", "what you will")
        ):
            responsibilities.append(value)

    extras: dict[str, str] = {}
    if qualifications:
        extras["qualifications"] = "\n".join(qualifications)
    if responsibilities:
        extras["responsibilities"] = "\n".join(responsibilities)
    return "\n".join(sections) or None, extras or None


def _parse_job(raw: dict) -> DiscoveredJob | None:
    url = next(
        (
            raw.get(field)
            for field in (
                "url_active_page",
                "url_comeet_hosted_page",
                "url_recruit_hosted_page",
                "url_detected_page",
            )
            if isinstance(raw.get(field), str) and raw[field].strip()
        ),
        None,
    )
    if not isinstance(url, str):
        return None

    description, extras = _content(raw)
    workplace_type = raw.get("workplace_type")
    location = raw.get("location")
    if not workplace_type and isinstance(location, dict) and location.get("is_remote"):
        workplace_type = "remote"

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
            normalize_job_location_type(str(workplace_type), default=None)
            if workplace_type
            else None
        ),
        date_posted=raw.get("time_updated") or None,
        extras=extras,
        metadata=metadata or None,
    )


def _positions_from_response(data: object) -> list[dict] | None:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("positions"), list):
        return [item for item in data["positions"] if isinstance(item, dict)]
    return None


async def _fetch_api_positions(
    company_id: str,
    token: str,
    client: httpx.AsyncClient,
    *,
    details: bool,
) -> list[dict] | None:
    params = {"token": token}
    if details:
        params["details"] = "true"
    response = await client.get(_api_url(company_id), params=params, follow_redirects=True)
    if response.status_code == 404:
        raise BoardGoneError(
            f"Comeet company {company_id!r} no longer exists",
            url=_api_url(company_id),
        )
    response.raise_for_status()
    return _positions_from_response(response.json())


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch all active jobs from either Comeet's API or embedded board data."""
    _ = pw
    credentials = _credentials_from_board(board)
    if credentials:
        company_id, token = credentials
        positions = await _fetch_api_positions(company_id, token, client, details=True)
        source = _api_url(company_id)
    else:
        source = _board_url(board["board_url"]) or board["board_url"]
        page = await fetch_page_text(source, client, max_chars=20_000_000)
        if page is None:
            raise ValueError(f"Failed to fetch Comeet board {source!r}")
        positions = _positions_assignment(page)

    if positions is None:
        raise ValueError(f"Comeet positions payload not found at {source!r}")

    jobs = [job for raw in positions if (job := _parse_job(raw))]
    log.info("comeet.discovered", board_url=source, jobs=len(jobs))
    if len(jobs) > MAX_JOBS:
        log.warning("comeet.truncated", board_url=source, total=len(jobs), cap=MAX_JOBS)
        return truncated_rich_result(jobs)
    return jobs


async def _probe_api(
    company_id: str,
    token: str,
    client: httpx.AsyncClient,
) -> int | None:
    try:
        positions = await _fetch_api_positions(company_id, token, client, details=False)
    except Exception:
        log.debug("comeet.probe_failed", company_id=company_id, exc_info=True)
        return None
    return len(positions) if positions is not None else None


def _hosted_metadata(url: str, page: str | None = None) -> dict | None:
    parts = _board_parts(url)
    company_data = _company_assignment(page) if page else None
    positions = _positions_assignment(page) if page else None
    if not parts and positions is None:
        return None

    result: dict = {}
    if parts:
        result.update({"company": parts[0], "board_id": parts[1]})
    if company_data:
        result.setdefault("company", company_data.get("slug"))
        result.setdefault("board_id", company_data.get("company_uid"))
        result = {key: value for key, value in result.items() if value}
    if positions is not None:
        result["jobs"] = len(positions)
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect hosted boards, custom embedded boards, and Careers API URLs."""
    _ = pw
    credentials = _credentials_from_api_url(url)
    hosted = _hosted_metadata(url)
    if client is None:
        if credentials:
            return {"company_id": credentials[0], "token": credentials[1]}
        return hosted

    page_url = _board_url(url) or url
    page = await fetch_page_text(page_url, client, max_chars=20_000_000)
    embedded = _hosted_metadata(url, page) if page is not None else hosted
    if embedded and page is not None and "jobs" in embedded:
        return embedded

    if credentials is None and page is not None:
        credentials = _credentials_from_html(page)
    if credentials is None:
        return None

    company_id, token = credentials
    count = await _probe_api(company_id, token, client)
    if count is None:
        return None
    return {"company_id": company_id, "token": token, "jobs": count}


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    credentials = _credentials_from_board({"board_url": board_url, "metadata": metadata})
    if credentials:
        company_id, token = credentials
        await save_json_response(
            artifact_dir,
            client,
            _api_url(company_id),
            params={"token": token, "details": "true"},
            follow_redirects=True,
        )
        return

    await save_text_response(
        artifact_dir,
        client,
        _board_url(board_url) or board_url,
        filename="comeet.html",
        follow_redirects=True,
    )


register("comeet", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
