"""SEEK AU/NZ advertiser-board monitor.

SEEK's public listing pages are protected by a browser challenge on crawler
egress, but the anonymous candidate UI reads employer-scoped results from the
public ``/api/jobsearch/v5/search`` endpoint. This monitor accepts only an
exact advertiser ID board, drains every reported page, and returns canonical
job URLs for the existing SEEK GraphQL detail scraper preset.
"""

from __future__ import annotations

import re
from math import ceil
from urllib.parse import parse_qsl, urlparse

import httpx
import structlog

from src.core.monitors import register
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_JSON_BYTES = 2_000_000

_MARKETS = {
    "au.seek.com": {
        "api_host": "www.seek.com.au",
        "job_host": "au.seek.com",
        "site_key": "AU-Main",
    },
    "www.seek.com.au": {
        "api_host": "www.seek.com.au",
        "job_host": "au.seek.com",
        "site_key": "AU-Main",
    },
    "nz.seek.com": {
        "api_host": "nz.seek.com",
        "job_host": "nz.seek.com",
        "site_key": "NZ-Main",
    },
    "www.seek.co.nz": {
        "api_host": "nz.seek.com",
        "job_host": "nz.seek.com",
        "site_key": "NZ-Main",
    },
}
_NUMERIC_ID_RE = re.compile(r"^\d{1,18}$")


def _identity_from_url(url: str) -> tuple[str, str] | None:
    """Return ``(market host, advertiser ID)`` for an exact board URL."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in _MARKETS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path.rstrip("/") != "/jobs"
        or parsed.fragment
    ):
        return None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(query) != 1 or query[0][0] != "advertiserid":
        return None
    advertiser_id = query[0][1]
    return (host, advertiser_id) if _NUMERIC_ID_RE.fullmatch(advertiser_id) else None


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"SEEK API returned invalid {field}")
    return value


def _search_url(host: str) -> str:
    return f"https://{_MARKETS[host]['api_host']}/api/jobsearch/v5/search"


def _job_url(host: str, job_id: str) -> str:
    return f"https://{_MARKETS[host]['job_host']}/job/{job_id}"


def _board_identity(board: dict) -> tuple[str, str]:
    """Validate that URL and optional config describe one exact advertiser board."""
    identity = _identity_from_url(board["board_url"])
    if identity is None:
        raise ValueError(f"Cannot derive SEEK advertiser identity from {board['board_url']!r}")

    metadata = board.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("SEEK monitor configuration must be a mapping")
    has_configured_identity = bool({"host", "advertiser_id"} & metadata.keys())
    if has_configured_identity:
        configured_host = metadata.get("host")
        configured_advertiser_id = metadata.get("advertiser_id")
        if (
            configured_host not in _MARKETS
            or not isinstance(configured_advertiser_id, str)
            or _NUMERIC_ID_RE.fullmatch(configured_advertiser_id) is None
        ):
            raise ValueError("Invalid or incomplete SEEK monitor configuration")
        host, advertiser_id = identity
        if (
            _MARKETS[configured_host]["job_host"] != _MARKETS[host]["job_host"]
            or configured_advertiser_id != advertiser_id
        ):
            raise ValueError("Configured SEEK identity does not match the board URL")
    return identity


async def _fetch_page(
    client: httpx.AsyncClient,
    *,
    host: str,
    advertiser_id: str,
    page: int,
) -> dict:
    return await fetch_json_page_with_retry(
        client,
        _search_url(host),
        expect_shape=dict,
        params={
            "advertiserid": advertiser_id,
            "page": page,
            "pagesize": PAGE_SIZE,
            "siteKey": _MARKETS[host]["site_key"],
        },
        headers={"Accept": "application/json"},
        max_bytes=MAX_JSON_BYTES,
        retryable_statuses={202, 403, 429},
        log_event="seek.list_backoff",
    )


def _parse_page(
    payload: dict,
    *,
    advertiser_id: str,
    requested_page: int,
) -> tuple[list[str], int]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("SEEK search response omitted data")
    total = _required_int(payload.get("totalCount"), "totalCount")
    if total > MAX_JOBS:
        raise ValueError(f"SEEK advertiser board exceeds the {MAX_JOBS}-job safety cap")

    metadata = payload.get("solMetadata")
    if not isinstance(metadata, dict):
        raise ValueError("SEEK search response omitted solMetadata")
    page_number = _required_int(metadata.get("pageNumber"), "pageNumber")
    page_size = _required_int(metadata.get("pageSize"), "pageSize")
    reported_total = _required_int(metadata.get("totalJobCount"), "totalJobCount")
    reported_advertiser_id = metadata.get("advertiser")
    if reported_advertiser_id != advertiser_id:
        raise ValueError("SEEK search response advertiser does not match the request")
    if page_number != requested_page or page_size != PAGE_SIZE or reported_total != total:
        raise ValueError("SEEK search response pagination does not match the request")

    expected_rows = min(PAGE_SIZE, max(0, total - (requested_page - 1) * PAGE_SIZE))
    if len(rows) != expected_rows:
        raise ValueError(
            f"SEEK search page {requested_page} returned {len(rows)} rows, expected {expected_rows}"
        )

    job_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("SEEK search response contains a non-object job")
        job_id = str(row.get("id") or "")
        advertiser = row.get("advertiser")
        actual_advertiser_id = (
            str(advertiser.get("id") or "") if isinstance(advertiser, dict) else ""
        )
        if not _NUMERIC_ID_RE.fullmatch(job_id) or actual_advertiser_id != advertiser_id:
            raise ValueError("SEEK search response contains an invalid job identity")
        if job_id in seen:
            raise ValueError(f"SEEK search page {requested_page} repeated job {job_id}")
        seen.add(job_id)
        job_ids.append(job_id)
    return job_ids, total


async def _fetch_job_ids(
    client: httpx.AsyncClient,
    *,
    host: str,
    advertiser_id: str,
) -> list[str]:
    first_payload = await _fetch_page(
        client,
        host=host,
        advertiser_id=advertiser_id,
        page=1,
    )
    first, total = _parse_page(
        first_payload,
        advertiser_id=advertiser_id,
        requested_page=1,
    )
    page_count = ceil(total / PAGE_SIZE) if total else 0
    job_ids = first
    seen = set(first)

    for page_number in range(2, page_count + 1):
        payload = await _fetch_page(
            client,
            host=host,
            advertiser_id=advertiser_id,
            page=page_number,
        )
        page_ids, page_total = _parse_page(
            payload,
            advertiser_id=advertiser_id,
            requested_page=page_number,
        )
        if page_total != total:
            raise ValueError("SEEK search total changed during pagination")
        overlap = seen & set(page_ids)
        if overlap:
            raise ValueError("SEEK search pagination repeated jobs")
        job_ids.extend(page_ids)
        seen.update(page_ids)

    if len(job_ids) != total:
        raise ValueError(f"SEEK discovered {len(job_ids)} jobs, expected {total}")
    return job_ids


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Return all canonical jobs from one SEEK AU/NZ advertiser board."""
    _ = pw
    host, advertiser_id = _board_identity(board)

    job_ids = await _fetch_job_ids(client, host=host, advertiser_id=advertiser_id)
    log.info("seek.discovered", advertiser_id=advertiser_id, jobs=len(job_ids))
    return {_job_url(host, job_id) for job_id in job_ids}


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize and, when possible, verify an exact advertiser board."""
    _ = pw
    identity = _identity_from_url(url)
    if identity is None:
        return None
    host, advertiser_id = identity
    result: dict[str, object] = {"host": host, "advertiser_id": advertiser_id}
    if client is None:
        return result
    try:
        job_ids = await _fetch_job_ids(client, host=host, advertiser_id=advertiser_id)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("seek.probe_failed", url=url, exc_info=True)
        return result
    result["jobs"] = len(job_ids)
    return result


register("seek", discover, cost=10, can_handle=can_handle)
