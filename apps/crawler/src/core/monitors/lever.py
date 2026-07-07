"""Lever Postings API monitor.

Public API: GET https://api.lever.co/v0/postings/{SITE}
Returns full job data. Supports pagination via skip/limit. Rate limit: 2 req/sec.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import cast

import httpx
import structlog

from src.core.enum_normalize import normalize_salary_unit
from src.core.monitors import (
    BoardGoneError,
    DiscoveredJob,
    register,
)
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.raw import save_json_response
from src.shared.http_retry import PaginationFetchError, fetch_json_page_with_retry
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

MAX_JOBS = 50_000
BATCH_SIZE = 100

# Pagination retry budget. Symmetric with workday (#2748), api_sniffer
# (#2733), accenture (#2735) and PCSX (#2734): 3 total attempts,
# exponential backoff with full jitter starting at 0.5s. Lever's public
# API is documented at 2 req/sec so the existing 0.5s inter-page sleep
# already throttles the success path; the retry budget piggy-backs on
# the same cadence.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5

_PAGE_PATTERNS = [
    re.compile(r"api\.(?:eu\.)?lever\.co/v0/postings/([\w-]+)"),
    re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)"),
]

_EU_DOMAIN = re.compile(r"(?:api|jobs)\.eu\.lever\.co/")

_IGNORE_TOKENS = frozenset({"v0", "api", "js", "css", "assets"})

# Lever's API honors the Accept header: with ``Accept: text/html`` it
# returns a minimal HTML widget ("Derby App") instead of JSON. The
# default httpx client sends a browser Accept header (see
# ``shared/http.py``) which was flipping responses to HTML and failing
# ``response.json()`` with a bare ``JSONDecodeError``. Force JSON.
_API_HEADERS = {"Accept": "application/json"}


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
    # Lever historically passed an unknown interval through unchanged
    # (rather than dropping to ``None``); preserve that to avoid
    # silently changing the R2 ``unit`` value for any future Lever
    # interval token not yet in the central map.
    return {
        "currency": currency,
        "min": sal_min,
        "max": sal_max,
        "unit": normalize_salary_unit(interval) or interval,
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
        resp = await client.get(
            _api_url(token, region), params={"limit": 100}, headers=_API_HEADERS
        )
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
        log.debug(
            "lever.probe_failed",
            probe="token",
            token=token,
            region=region,
            url=_api_url(token, region),
            exc_info=True,
        )
        return False, None


async def _fetch_job_count(
    token: str, client: httpx.AsyncClient, region: str | None = None
) -> int | str | None:
    """Lightweight API call to get the job count for a token."""
    try:
        resp = await client.get(
            _api_url(token, region), params={"limit": 100}, headers=_API_HEADERS
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, list):
            return "100+" if len(data) >= 100 else len(data)
        return None
    except Exception:
        log.debug(
            "lever.probe_failed",
            probe="job_count",
            token=token,
            region=region,
            url=_api_url(token, region),
            exc_info=True,
        )
        return None


async def _fetch_template_count(
    token: str,
    client: httpx.AsyncClient,
    region: str | None,
) -> ProbeCount | None:
    return await _fetch_job_count(token, client, region)


async def _probe_template_token(
    token: str,
    client: httpx.AsyncClient,
    region: str | None,
) -> ProbeResult:
    return await _probe_token(token, client, region)


def _build_template_result(
    token: str,
    count: ProbeCount | None,
    region: str | None,
) -> dict:
    result: dict = {"token": token}
    if region:
        result["region"] = region
    if count is not None:
        result["jobs"] = count
    return result


def _region_from_template_match(
    match: re.Match[str],
    region: str | None,
) -> str | None:
    if region:
        return region
    return "eu" if ".eu.lever.co" in match.group(0) else None


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
    skip: int = 0,
) -> list[dict]:
    """GET a Lever list-API page with bounded retries (#2749)."""
    # Kept for the existing first-page 404 mapping in ``discover``.
    _ = skip
    return cast(
        list[dict],
        await fetch_json_page_with_retry(
            client,
            url,
            params=params,
            headers=_API_HEADERS,
            expect_shape=list,
            retries=retries,
            base_delay=base_delay,
            log_event="lever.list_backoff",
            sleep=asyncio.sleep,
        ),
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Lever public API with pagination.

    Failure semantics (#2749). Each page GET is wrapped by
    :func:`_get_page_with_retry`, which raises
    :class:`PaginationFetchError` on persistent transient failures or
    non-retryable 4xx. The exception propagates out of this function
    (no intervening try/except) and lands in
    ``_process_one_board_streaming``'s generic ``except Exception``,
    which records the run as a failure rather than a partial success
    — preventing ``_MARK_GONE_BY_TIMESTAMP`` from tombstoning the URLs
    that live on the unfetched pages (same shape of bug as #2722,
    #2737, #2748).
    """
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
        try:
            batch = await _get_page_with_retry(
                client, url, {"limit": BATCH_SIZE, "skip": skip}, skip=skip
            )
        except PaginationFetchError as exc:
            # First-page 404 is the "board removed" signal — re-raise as
            # the canonical structural error so the board processor
            # disables in one shot instead of accumulating five
            # consecutive failures. See issue #2215.
            if exc.last_status == 404 and skip == 0:
                raise BoardGoneError(
                    f"Lever board token {token!r} returned 404",
                    url=url,
                ) from exc
            raise

        for raw in batch:
            parsed = _parse_job(raw)
            if parsed:
                jobs.append(parsed)

        if len(batch) < BATCH_SIZE:
            break

        skip += BATCH_SIZE

        if len(jobs) >= MAX_JOBS:
            log.warning("lever.truncated", url=url, total=len(jobs), cap=MAX_JOBS)
            return truncated_rich_result(jobs)

        await asyncio.sleep(0.5)

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Lever: domain check -> page HTML scan -> slug-based API probe."""
    _ = pw
    region = _region_from_url(url)
    return await ats_can_handle(
        url,
        client,
        monitor_name="lever",
        token_from_url=_token_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=_IGNORE_TOKENS,
        fetch_job_count=_fetch_template_count,
        api_probe=_probe_template_token,
        initial_context=region,
        result_builder=_build_template_result,
        context_from_match=_region_from_template_match,
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    token = metadata.get("token") or _token_from_url(board_url)
    if not token:
        return
    region = metadata.get("region") or _region_from_url(board_url)
    await save_json_response(
        artifact_dir,
        client,
        _api_url(token, region),
        params={"limit": 100},
        headers=_API_HEADERS,
    )


register("lever", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
