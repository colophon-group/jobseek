"""Lever Postings API monitor.

Public API: GET https://api.lever.co/v0/postings/{SITE}
Returns full job data. Supports pagination via skip/limit. Rate limit: 2 req/sec.
"""

from __future__ import annotations

import asyncio
import random
import re
from pathlib import Path

import httpx
import structlog

from src.core.enum_normalize import normalize_salary_unit
from src.core.monitors import (
    BoardGoneError,
    DiscoveredJob,
    fetch_page_text,
    register,
    slugs_from_url,
)
from src.core.monitors.raw import save_json_response
from src.shared.http_retry import PaginationFetchError, is_retryable_status
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
        return None


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
    skip: int = 0,
) -> list[dict]:
    """GET a Lever list-API page with bounded retries (#2749).

    Mirrors the contract used by ``fetch_with_retry`` (#2722) and the
    sibling monitor helpers (workday #2748, accenture #2735, PCSX
    #2734, api_sniffer #2733): retryable failures back off exponentially
    and, on budget exhaustion, raise :class:`PaginationFetchError` so
    the run is recorded as a failure rather than silently truncating to
    whatever pages happened to succeed.

    Lever's end-of-pagination signal is ``len(batch) < BATCH_SIZE``.
    A CDN/anti-bot incident dropping the body and returning ``200 []``
    looks identical to a legitimate empty page — pre-fix, the loop
    simply broke and the caller treated the partial discovery as
    success, which fed ``_MARK_GONE_BY_TIMESTAMP`` for the missing
    URLs (same shape as #2722 / #2737 / #2748).

    Retried:
      - HTTP 5xx (Cloudflare 520-526/530 included), 408, 425, 429.
      - Arbitrary network exceptions (timeout, connection reset, JSON
        parse error on a captcha/HTML body served as 200).
      - HTTP 200 with a body that decodes to a non-list shape — e.g.,
        a CDN error envelope ``{}`` served as 200 (same shape as the
        workday ``null``-body guard).

    Fail-fast (non-retryable 4xx — auth-expired 401, misconfigured 400,
    forbidden 403): raises :class:`PaginationFetchError` on the first
    attempt. These won't recover within the retry budget and we'd
    rather surface the misconfiguration than burn the budget. The
    *first-page-only* 404 -> ``BoardGoneError`` mapping is preserved
    by the caller (``discover``) because it's a structural signal,
    not a transient one — see #2215.

    Backoff: ``base_delay × 2^attempt × (0.5 + random())`` — exponential
    with full jitter, identical cadence to workday (#2748).
    """
    # Retry observability (#3210). Same counter as ``http_retry.py`` so
    # the cross-monitor "retry storm" PromQL query aggregates lever in.
    from src.metrics import http_retry_attempts_total, http_retry_host
    from src.shared.tdm import TDMReservedError
    from src.shared.tdm import check_response as _tdm_check

    host = http_retry_host(url)

    last_exc: BaseException | None = None
    last_status: int | None = None
    retried = False

    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params, headers=_API_HEADERS)
            last_status = resp.status_code
            if resp.status_code == 200:
                # TDM-Reservation respect (#2842) — header-only check on
                # API endpoints (JSON body, no HTML meta to scan).
                _tdm_check(resp)
                # ``resp.json()`` may raise ``json.JSONDecodeError`` on a
                # captcha/HTML body served as 200 — falls into the
                # ``except Exception`` branch below, retried, then
                # surfaced as ``PaginationFetchError``. No silent break.
                data = resp.json()
                # Lever's list endpoint returns a JSON array of postings.
                # A body that decodes to ``null`` or to a non-list shape
                # (e.g., an unexpected error envelope) is treated as a
                # transient failure: retry, then raise. Without this
                # guard, ``len(batch) < BATCH_SIZE`` on a non-list ``batch``
                # raises ``TypeError`` mid-loop, OR (worse) a coincidental
                # ``[]`` would silently break the loop — same shape of
                # silent-break bug the issue (#2749) is fixing.
                if not isinstance(data, list):
                    raise ValueError(
                        f"lever list endpoint returned non-list body: {type(data).__name__}"
                    )
                if retried:
                    http_retry_attempts_total.labels(host=host, outcome="recovered").inc()
                return data
            if resp.status_code == 404 and skip == 0:
                # First-page 404 is Lever's "board removed" signal.
                # Re-raise as ``BoardGoneError`` via the caller; here we
                # surface a sentinel by raising the canonical structural
                # error and letting ``discover`` translate. We do this
                # by attaching the response so the caller can detect.
                raise PaginationFetchError(
                    url,
                    attempts=attempt + 1,
                    last_status=404,
                )
            if is_retryable_status(resp.status_code):
                last_exc = None  # status-only, no exception
                http_retry_attempts_total.labels(host=host, outcome="retry").inc()
                retried = True
            else:
                # Non-retryable 4xx — fail fast. ``resp.raise_for_status``
                # would raise ``HTTPStatusError`` which the caller doesn't
                # uniformly handle; raise ``PaginationFetchError`` directly
                # for cross-monitor symmetry (callers ``except
                # PaginationFetchError`` will route to ``_RECORD_FAILURE``).
                raise PaginationFetchError(
                    url,
                    attempts=attempt + 1,
                    last_status=resp.status_code,
                )
        except (PaginationFetchError, TDMReservedError):
            raise
        except Exception as exc:  # noqa: BLE001 — timeout, network, JSON parse
            last_exc = exc
            last_status = None
            http_retry_attempts_total.labels(host=host, outcome="retry").inc()
            retried = True

        if attempt < retries - 1:
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "lever.list_backoff",
                url=url,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                last_status=last_status,
                last_error=type(last_exc).__name__ if last_exc else None,
            )
            await asyncio.sleep(delay)

    http_retry_attempts_total.labels(host=host, outcome="exhausted").inc()
    raise PaginationFetchError(
        url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_exc).__name__ if last_exc else None,
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
