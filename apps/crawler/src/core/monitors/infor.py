"""Infor Global HR / Lawson CandidateSelfService monitor.

Infor Candidate Space renders listings inside an iframe and exposes job rows
through a session-bound Landmark ``ldrest`` endpoint.  A normal DOM monitor
only sees the shell's language links, while a direct API request without the
bootstrap cookies fails with ``No data context available``.

This monitor bootstraps an anonymous CandidateSelfService session, replays the
native listing operation, and emits rich summary rows.  The paired ``infor``
scraper uses the provider's ``Find_PostingDisplay_FormOperation`` endpoint to
enrich descriptions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.http import DEFAULT_ACCEPT, DEFAULT_USER_AGENT
from src.shared.http_retry import fetch_response_with_status_retries

log = structlog.get_logger()

_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
_DATAAREA_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_INFOR_HOST_RE = re.compile(r"(?:[a-z0-9-]{1,63}\.)+cloud\.infor\.com", re.IGNORECASE)
_ALLOWED_PORTS = frozenset({None, 443, 1443, 1444})
_MAX_JOBS = 50_000

_LIST_RESPONSE = "JobPostingListWebServices_ListOperationResponse"
_LIST_WRAPPER = f"{_LIST_RESPONSE}Array"


@dataclass(frozen=True, slots=True)
class InforSite:
    origin: str
    dataarea: str
    job_board: str
    hr_organization: str


def _single_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if values is None or len(values) != 1:
        return None
    value = values[0].strip()
    return value if _TOKEN_RE.fullmatch(value) else None


def parse_candidate_url(
    url: str, *, require_job: bool = False
) -> tuple[InforSite, str | None, str | None] | None:
    """Parse a trusted Infor CandidateSelfService board or detail URL."""
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = parse_qs(parsed.query, keep_blank_values=True)
    except ValueError:
        return None

    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in _ALLOWED_PORTS
        or _INFOR_HOST_RE.fullmatch(host) is None
    ):
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) != 3
        or parts[1] != "CandidateSelfService"
        or parts[2]
        not in {
            "lm",
            "controller.servlet",
        }
    ):
        return None
    dataarea = parts[0]
    if _DATAAREA_RE.fullmatch(dataarea) is None:
        return None

    configured_dataarea = query.get("context.dataarea") or query.get("dataarea")
    if configured_dataarea is not None and (
        len(configured_dataarea) != 1 or configured_dataarea[0] != dataarea
    ):
        return None

    job_board = _single_query_value(query, "context.session.key.JobBoard")
    hr_organization = _single_query_value(query, "context.session.key.HROrganization")
    if job_board is None or hr_organization is None:
        return None

    job_requisition = _single_query_value(query, "JobReq")
    job_posting = _single_query_value(query, "JobPost")
    if (job_requisition is None) != (job_posting is None):
        return None
    if require_job and job_requisition is None:
        return None

    origin = f"https://{host}"
    if port not in {None, 443}:
        origin += f":{port}"
    site = InforSite(origin, dataarea, job_board, hr_organization)
    return site, job_requisition, job_posting


def _listing_url(site: InforSite) -> str:
    return (
        f"{site.origin}/{site.dataarea}/soapExt/ldrest/JobPosting/"
        "JobPostingListWebServices_ListOperation"
    )


def detail_api_url(site: InforSite) -> str:
    return (
        f"{site.origin}/{site.dataarea}/soapExt/ldrest/JobPosting/Find_PostingDisplay_FormOperation"
    )


def build_job_url(site: InforSite, job_requisition: str, job_posting: str) -> str:
    query = urlencode(
        {
            "context.dataarea": site.dataarea,
            "webappname": "CandidateSelfService",
            "context.session.key.JobBoard": site.job_board,
            "context.session.key.HROrganization": site.hr_organization,
            "_saveKeys": "true",
            "JobPost": job_posting,
            "JobReq": job_requisition,
            "context.session.key.noheader": "true",
        }
    )
    return f"{site.origin}/{site.dataarea}/CandidateSelfService/lm?{query}"


def _session_headers(
    response: httpx.Response, initial_cookies: dict[str, str] | None = None
) -> dict[str, str]:
    """Return isolated Cookie/CSRF headers from one bootstrap redirect chain."""
    cookies: dict[str, str] = dict(initial_cookies or {})
    for item in [*response.history, response]:
        cookies.update(dict(item.cookies.items()))
    csrf = cookies.get("SSO.CSRF")
    if not csrf:
        raise ValueError("Infor CandidateSelfService bootstrap did not set SSO.CSRF")
    return {
        "Accept": "application/json",
        "Cookie": "; ".join(f"{name}={value}" for name, value in cookies.items()),
        "SSO.CSRF": csrf,
    }


def _client_cookie_snapshot(client: httpx.AsyncClient, url: str) -> dict[str, str]:
    """Copy cookies applicable to *url* without retaining the mutable jar."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    result: dict[str, str] = {}
    try:
        jar = client.cookies.jar
        for cookie in jar:
            domain = cookie.domain.lstrip(".").lower()
            domain_matches = host == domain or host.endswith(f".{domain}")
            if domain_matches and path.startswith(cookie.path or "/"):
                result[cookie.name] = cookie.value
    except (AttributeError, TypeError):
        # Lightweight mocked clients do not necessarily expose a CookieJar.
        pass
    return result


async def bootstrap_session(url: str, client: httpx.AsyncClient) -> dict[str, str]:
    """Create an anonymous Infor data context for the following API request."""
    initial_cookies = _client_cookie_snapshot(client, url)
    response = await fetch_response_with_status_retries(
        client,
        url,
        retry_limits={},
        same_origin_redirects=True,
        headers={
            "Accept": DEFAULT_ACCEPT,
            "User-Agent": DEFAULT_USER_AGENT,
        },
    )
    response.raise_for_status()
    current_cookies = _client_cookie_snapshot(client, url)
    return _session_headers(response, {**initial_cookies, **current_cookies})


def _listing_params(site: InforSite) -> dict[str, str]:
    return {
        "_clientType": "INTERNAL",
        "JobBoard": site.job_board,
        "LocationOfJob": " ",
        "Category": " ",
        "SubCategory": " ",
        "WorkType": " ",
        "JobRequisition": " ",
        "__Description_translation___": " ",
        "JobPosting": " ",
        "PostingStatus": "2",
        "PostingDateRange.Begin": " ",
        "PostingDateRange.End": " ",
        "JobRequisitionPriority": " ",
        "csk.IsoLocale": "en",
        "HROrganization": site.hr_organization,
        "_limit": "-1",
        "AtApplicationLimit": " ",
    }


def _rows(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError("Infor listing response is not an object")
    wrapped = payload.get(_LIST_WRAPPER)
    if not isinstance(wrapped, list):
        raise ValueError("Infor listing response is missing its result array")
    if len(wrapped) > _MAX_JOBS:
        raise ValueError(f"Infor listing exceeds the {_MAX_JOBS}-job safety cap")

    rows: list[dict] = []
    for item in wrapped:
        if not isinstance(item, dict) or not isinstance(item.get(_LIST_RESPONSE), dict):
            raise ValueError("Infor listing response contains a malformed row")
        rows.append(item[_LIST_RESPONSE])
    return rows


async def _fetch_rows(board_url: str, site: InforSite, client: httpx.AsyncClient) -> list[dict]:
    headers = await bootstrap_session(board_url, client)
    response = await client.get(
        _listing_url(site),
        params=_listing_params(site),
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    return _rows(response.json())


def _date(value: object) -> str | None:
    if value in (None, "", "00000000"):
        return None
    if not isinstance(value, str):
        raise ValueError("Infor posting date is not a string")
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"Infor posting date is invalid: {value!r}") from exc


def normalize_location(value: object) -> list[str] | None:
    if not isinstance(value, str):
        return None
    parts = [part.strip() for part in value.split(":") if part.strip()]
    return [", ".join(reversed(parts))] if parts else None


def _site_for_board(board: dict) -> InforSite:
    candidate = parse_candidate_url(board["board_url"])
    if candidate is None:
        raise ValueError("Infor board URL is invalid")
    site = candidate[0]
    metadata = board.get("metadata") or {}
    expected = {
        "origin": site.origin,
        "dataarea": site.dataarea,
        "job_board": site.job_board,
        "hr_organization": site.hr_organization,
    }
    for key, value in expected.items():
        configured = metadata.get(key)
        if configured is not None and configured != value:
            raise ValueError(f"Infor {key} metadata conflicts with the board URL")
    return site


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    candidate = parse_candidate_url(url)
    if candidate is None:
        return None
    site = candidate[0]
    try:
        rows = await _fetch_rows(url, site, client)
    except Exception:
        return None
    return {
        "origin": site.origin,
        "dataarea": site.dataarea,
        "job_board": site.job_board,
        "hr_organization": site.hr_organization,
        "jobs_count": len(rows),
    }


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    site = _site_for_board(board)
    rows = await _fetch_rows(board["board_url"], site, client)
    seen: set[tuple[str, str]] = set()
    jobs: list[DiscoveredJob] = []

    for row in rows:
        job_requisition = str(row.get("JobRequisition") or "").strip()
        job_posting = str(row.get("JobPosting") or "").strip()
        title = str(row.get("__Description_translation___") or "").strip()
        if (
            _TOKEN_RE.fullmatch(job_requisition) is None
            or _TOKEN_RE.fullmatch(job_posting) is None
            or not title
        ):
            raise ValueError("Infor listing response contains an invalid job identity")
        identity = (job_requisition, job_posting)
        if identity in seen:
            raise ValueError(f"Infor listing contains duplicate job identity {identity!r}")
        seen.add(identity)

        metadata = {
            "job_requisition": job_requisition,
            "job_posting": job_posting,
        }
        for source, target in (
            ("WorkType", "work_type"),
            ("Category", "category"),
            ("SubCategory", "subcategory"),
        ):
            value = row.get(source)
            if isinstance(value, str) and value.strip():
                metadata[target] = value.strip()

        jobs.append(
            DiscoveredJob(
                url=build_job_url(site, job_requisition, job_posting),
                title=title,
                locations=normalize_location(row.get("LocationOfJob")),
                date_posted=_date(row.get("PostingDateRange.Begin")),
                language="en",
                metadata=metadata,
            )
        )

    log.info(
        "infor.discover_complete",
        origin=site.origin,
        hr_organization=site.hr_organization,
        job_board=site.job_board,
        jobs=len(jobs),
    )
    return jobs


register("infor", discover, cost=10, can_handle=can_handle, rich=True)
