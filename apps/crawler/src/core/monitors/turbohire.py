"""TurboHire public career-page monitor.

TurboHire portals expose a public, short-lived bearer token and use it for a
filtered jobs request plus per-job detail requests.  Listing records contain
the public identifiers and summary fields; the detail endpoint supplies the
complete HTML description.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import UUID

import httpx

from src.core.monitors import DiscoveredJob, register
from src.shared.html_normalize import normalize_description_html
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.tdm import TDMReservedError

API_BASE = "https://thapi.azurewebsites.net"
MAX_JOBS = 50_000
DETAIL_CONCURRENCY = 10

_CAREER_PAGE_RE = re.compile(
    r"^/careerpage/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)


def _valid_org_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _org_id_from_url(board_url: str) -> str | None:
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    if not host.endswith(".turbohire.co"):
        return None

    match = _CAREER_PAGE_RE.fullmatch(parsed.path)
    if match:
        return _valid_org_id(match.group(1))

    values = parse_qs(parsed.query).get("orgId") or parse_qs(parsed.query).get("orgid")
    return _valid_org_id(values[0]) if values else None


def _board_key(board: dict) -> tuple[str, str]:
    board_url = board["board_url"]
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    if not host.endswith(".turbohire.co"):
        raise ValueError(f"TurboHire board URL has unsupported host: {board_url!r}")

    configured = _valid_org_id((board.get("metadata") or {}).get("org_id"))
    direct = _org_id_from_url(board_url)
    if configured is not None and direct is not None and configured != direct:
        raise ValueError("Configured TurboHire organization does not match the board URL")
    org_id = configured or direct
    if org_id is None:
        raise ValueError(f"Cannot derive TurboHire organization ID from {board_url!r} or metadata")
    return org_id, f"{parsed.scheme or 'https'}://{host}"


def _request_headers(board_url: str, *, token: str | None = None) -> dict[str, str]:
    parsed = urlparse(board_url)
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    headers = {
        "accept": "application/json, text/plain, */*",
        "origin": origin,
        "referer": board_url,
    }
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return headers


def _jobs_request_body() -> dict:
    empty_filter = {"Value": None, "FilterType": 0}
    return {
        "SortByV2": {"Key": "PostedDate", "Order": 2},
        "BunitIds": dict(empty_filter),
        "Experience": dict(empty_filter),
        "JobTypes": dict(empty_filter),
        "JobTypeV2": dict(empty_filter),
        "Locations": dict(empty_filter),
        "CreatedDate": dict(empty_filter),
        "Compensation": dict(empty_filter),
        "Skills": dict(empty_filter),
        "Keyword": "",
        "ClientIds": dict(empty_filter),
        "Department": "",
        "CustomFields": {},
    }


async def _public_token(client: httpx.AsyncClient, board_url: str) -> str:
    payload = await fetch_json_page_with_retry(
        client,
        f"{API_BASE}/api/token/noauth",
        expect_shape=dict,
        headers=_request_headers(board_url),
        retryable_statuses={401, 403},
        log_event="turbohire.token_backoff",
    )
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("TurboHire public token response omitted access_token")
    return token


async def _listing(
    client: httpx.AsyncClient,
    board_url: str,
    org_id: str,
    token: str,
) -> tuple[int, list[dict]]:
    payload = await fetch_json_page_with_retry(
        client,
        f"{API_BASE}/api/careerpagev2/filteredjobs",
        expect_shape=dict,
        method="POST",
        params={"orgId": org_id, "pageType": 0},
        json_body=_jobs_request_body(),
        headers=_request_headers(board_url, token=token),
        retryable_statuses={401, 403},
        log_event="turbohire.list_backoff",
    )
    total = payload.get("Total")
    rows = payload.get("Result")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("TurboHire jobs response omitted a valid total")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("TurboHire jobs response omitted valid job records")
    if total > MAX_JOBS or len(rows) > MAX_JOBS:
        raise ValueError(f"TurboHire organization exceeded the {MAX_JOBS:,}-job safety cap")
    if len(rows) != total:
        raise ValueError(
            f"TurboHire jobs response was incomplete: returned {len(rows)} of {total} jobs"
        )
    return total, rows


async def _detail(
    client: httpx.AsyncClient,
    board_url: str,
    token: str,
    job_id: str,
) -> dict:
    return await fetch_json_page_with_retry(
        client,
        f"{API_BASE}/api/publicjobs",
        expect_shape=dict,
        params={"jobId": job_id, "fieldVisibility": 0},
        headers=_request_headers(board_url, token=token),
        retryable_statuses={401, 403},
        log_event="turbohire.detail_backoff",
    )


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _locations(value: object) -> list[str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        raw_locations = json.loads(value)
    except json.JSONDecodeError:
        return [_clean_string(value)] if _clean_string(value) else None
    if not isinstance(raw_locations, list):
        return None

    locations: list[str] = []
    seen: set[str] = set()
    for raw in raw_locations:
        if not isinstance(raw, dict):
            continue
        location = _clean_string(raw.get("Address"))
        identity = location.casefold() if location else None
        if location is not None and identity is not None and identity not in seen:
            locations.append(location)
            seen.add(identity)
    return locations or None


def _description(raw: dict) -> str | None:
    for field in ("JobDescriptionV2", "JobDescription"):
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            return normalize_description_html(value)
    return None


def _public_job_url(portal_origin: str, public_id: str) -> str:
    """Build a single path-segment URL from a possibly pre-encoded ID."""
    encoded_id = quote(unquote(public_id), safe="")
    return f"{portal_origin}/job/publicjobs/{encoded_id}"


def _parse_job(raw: dict, *, portal_origin: str) -> DiscoveredJob:
    job_id = _clean_string(raw.get("JobId"))
    public_id = _clean_string(raw.get("JobIdObfuscated"))
    title = _clean_string(raw.get("JobTitle"))
    if job_id is None or public_id is None or title is None:
        raise ValueError("TurboHire returned a job without a valid public identity or title")

    metadata: dict[str, object] = {"id": job_id}
    for source, target in (
        ("JobCode", "job_code"),
        ("Department", "department"),
        ("ClientName", "client_name"),
    ):
        value = _clean_string(raw.get(source))
        if value is not None:
            metadata[target] = value
    experience = raw.get("Experience")
    if isinstance(experience, dict):
        minimum = experience.get("MinExp")
        maximum = experience.get("MaxExp")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            metadata["experience_min"] = minimum
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            metadata["experience_max"] = maximum

    extras: dict[str, object] = {}
    skills = raw.get("Skills")
    if isinstance(skills, list):
        cleaned_skills = [
            value for raw_skill in skills if (value := _clean_string(raw_skill)) is not None
        ]
        if cleaned_skills:
            extras["skills"] = list(dict.fromkeys(cleaned_skills))
    for source, target in (
        ("RolesAndResponsibilitiesV2", "responsibilities"),
        ("EligibilityV2", "qualifications"),
    ):
        value = raw.get(source)
        if isinstance(value, str) and value.strip():
            normalized = normalize_description_html(value)
            if normalized:
                extras[target] = normalized

    employment_type = _clean_string(raw.get("JobTypeV2")) or _clean_string(raw.get("Type"))
    if employment_type and employment_type.casefold() == "unspecified":
        employment_type = None

    published_dates = raw.get("PublishedDates")
    date_posted = (
        published_dates.get("CAREERPAGE") if isinstance(published_dates, dict) else None
    ) or raw.get("PublishedDate")

    return DiscoveredJob(
        url=_public_job_url(portal_origin, public_id),
        title=title,
        description=_description(raw),
        locations=_locations(raw.get("Location")),
        employment_type=employment_type,
        date_posted=date_posted if isinstance(date_posted, str) else None,
        language="en",
        extras=extras or None,
        metadata=metadata,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch every public job and its complete detail record."""
    _ = pw
    org_id, portal_origin = _board_key(board)
    board_url = board["board_url"]
    token = await _public_token(client, board_url)
    _total, rows = await _listing(client, board_url, org_id, token)

    async def fetch_and_parse(row: dict) -> DiscoveredJob:
        public_id = _clean_string(row.get("JobIdObfuscated"))
        if public_id is None:
            raise ValueError("TurboHire listing returned a job without JobIdObfuscated")
        detail = await _detail(client, board_url, token, public_id)
        if detail.get("JobId") != row.get("JobId"):
            raise ValueError("TurboHire detail response did not match its listing record")
        return _parse_job(detail, portal_origin=portal_origin)

    jobs: list[DiscoveredJob] = []
    for start in range(0, len(rows), DETAIL_CONCURRENCY):
        jobs.extend(
            await asyncio.gather(
                *(fetch_and_parse(row) for row in rows[start : start + DETAIL_CONCURRENCY])
            )
        )
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect a TurboHire portal and verify its public jobs endpoint."""
    _ = pw
    org_id = _org_id_from_url(url)
    if org_id is None:
        return None
    result: dict = {"org_id": org_id}
    if client is None:
        return result
    try:
        token = await _public_token(client, url)
        total, _rows = await _listing(client, url, org_id, token)
    except TDMReservedError:
        raise
    except Exception:
        return None
    result["jobs"] = total
    return result


register("turbohire", discover, cost=10, can_handle=can_handle, rich=True)
