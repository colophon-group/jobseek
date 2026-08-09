"""Paycom public career-portal monitor.

Paycom boards bootstrap a short-lived public session token and a regional API
origin in their server-rendered HTML. The monitor validates that bootstrap,
then pages through the credential-free posting-preview endpoint. Detail pages
remain on the normal scraper schedule so monitoring does not fan out per job.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type
from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.shared.http_retry import (
    PaginationFetchError,
    fetch_json_page_with_retry,
    fetch_text_page_with_retry,
)
from src.shared.tdm import TDMReservedError

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
STREAM_BATCH = 200

_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_CONFIG_MARKER_RE = re.compile(r"\bvar\s+configsFromHost\s*=\s*")
_PAGE_PATTERNS = [
    re.compile(
        r"https?://(?:www\.)?paycomonline\.net/v4/ats/web\.php/portal/"
        r"([0-9a-f]{32})(?:/|[?\"'])",
        re.IGNORECASE,
    )
]
_MISSING_BOARD_MARKER = "Job board does not exist or is unavailable"
_PORTAL_HOSTS = frozenset({"paycomonline.net", "www.paycomonline.net"})


@dataclass(frozen=True, slots=True)
class Bootstrap:
    service_url: str
    headers: dict[str, str]


def clean_paycom_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def normalize_paycom_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    return token if _TOKEN_RE.fullmatch(token) else None


def paycom_token_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in _PORTAL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 5 or segments[:4] != ["v4", "ats", "web.php", "portal"]:
        return None
    return normalize_paycom_token(segments[4])


def resolve_paycom_token(
    board_url: str,
    metadata: Mapping[str, object],
) -> str | None:
    """Resolve one portal token and reject contradictory configured identity."""

    direct = paycom_token_from_url(board_url)
    has_configured = "token" in metadata
    configured = normalize_paycom_token(metadata.get("token"))
    if has_configured and configured is None:
        raise ValueError("Configured Paycom token is invalid")
    if direct is not None and configured is not None and direct != configured:
        raise ValueError(
            f"Configured Paycom token {configured!r} does not match the board URL token {direct!r}"
        )
    return direct or configured


def paycom_portal_url(token: str) -> str:
    return f"https://www.paycomonline.net/v4/ats/web.php/portal/{token}/career-page"


def _job_url(token: str, job_id: int) -> str:
    return f"https://www.paycomonline.net/v4/ats/web.php/portal/{token}/jobs/{job_id}"


def _trusted_service_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Paycom bootstrap omitted the ATS service URL")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Paycom bootstrap returned an invalid ATS service URL") from exc
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or not (host == "paycomonline.net" or host.endswith(".paycomonline.net"))
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Paycom bootstrap returned an untrusted ATS service URL")
    return value.rstrip("/")


def extract_paycom_bootstrap(page: str, portal_url: str) -> Bootstrap:
    match = _CONFIG_MARKER_RE.search(page)
    if match is None:
        raise ValueError("Paycom portal omitted configsFromHost")
    try:
        config, _ = json.JSONDecoder().raw_decode(page, match.end())
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Paycom portal returned invalid configsFromHost JSON") from exc
    if not isinstance(config, dict):
        raise ValueError("Paycom configsFromHost is not an object")

    session_jwt = clean_paycom_string(config.get("sessionJWT"))
    if session_jwt is None or len(session_jwt.split(".")) != 3:
        raise ValueError("Paycom portal returned a malformed session token")

    raw_lib_config = config.get("libConfig")
    if not isinstance(raw_lib_config, str):
        raise ValueError("Paycom portal omitted its library config")
    try:
        lib_config = json.loads(raw_lib_config)
    except json.JSONDecodeError as exc:
        raise ValueError("Paycom portal returned invalid library config JSON") from exc
    if not isinstance(lib_config, dict):
        raise ValueError("Paycom library config is not an object")

    service_url = _trusted_service_url(lib_config.get("atsPortalMantleServiceUrl"))
    locale = clean_paycom_string(lib_config.get("locale")) or "en-US"
    highlights = str(bool(lib_config.get("translationHighlights"))).lower()
    return Bootstrap(
        service_url=service_url,
        headers={
            "Accept": "application/json",
            "Authorization": session_jwt,
            "Locale": locale,
            "Translation-Highlights": highlights,
            "Portal-Host-Referrer": portal_url,
        },
    )


async def bootstrap_paycom(token: str, client: httpx.AsyncClient) -> Bootstrap:
    portal_url = paycom_portal_url(token)
    try:
        page = await fetch_text_page_with_retry(
            client,
            portal_url,
            retries=3,
            base_delay=0.5,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            log_event="paycom.bootstrap_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in {404, 410}:
            raise BoardGoneError(
                "Paycom board no longer exists",
                url=portal_url,
                status_code=exc.last_status,
            ) from exc
        raise
    if page is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError(f"Paycom bootstrap returned no page for {token!r}")
    if _MISSING_BOARD_MARKER.casefold() in page.casefold():
        raise BoardGoneError("Paycom board no longer exists", url=portal_url, status_code=200)
    return extract_paycom_bootstrap(page, portal_url)


def _search_url(bootstrap: Bootstrap) -> str:
    return f"{bootstrap.service_url}/api/ats/job-posting-previews/search"


def _search_payload(skip: int, *, take: int = PAGE_SIZE) -> dict:
    return {
        "skip": skip,
        "take": take,
        "filtersForQuery": {
            "distanceFrom": 0,
            "workEnvironments": [],
            "positionTypes": [],
            "educationLevels": [],
            "categories": [],
            "travelTypes": [],
            "shiftTypes": [],
            "otherFilters": [],
            "keywordSearchText": "",
            "location": "",
            "sortOption": "",
        },
    }


async def _fetch_search_page(
    bootstrap: Bootstrap,
    client: httpx.AsyncClient,
    *,
    skip: int,
    take: int = PAGE_SIZE,
) -> dict:
    return await fetch_json_page_with_retry(
        client,
        _search_url(bootstrap),
        expect_shape=dict,
        method="POST",
        json_body=_search_payload(skip, take=take),
        headers=bootstrap.headers,
        retries=3,
        base_delay=0.5,
        log_event="paycom.search_backoff",
    )


def _parse_job(raw: dict, token: str) -> DiscoveredJob | None:
    job_id = raw.get("jobId")
    if isinstance(job_id, bool) or not isinstance(job_id, (str, int)):
        return None
    if isinstance(job_id, str) and not job_id.isdigit():
        return None
    job_id = int(job_id)
    if job_id <= 0:
        return None

    location = clean_paycom_string(raw.get("locations"))
    location_type = normalize_job_location_type(clean_paycom_string(raw.get("remoteType")))
    if location_type is None and location and "remote" in location.casefold():
        location_type = "remote"

    metadata = {
        key: value
        for key, value in {
            "job_id": job_id,
            "is_hot_job": raw.get("isHotJob"),
        }.items()
        if value not in (None, "")
    }
    return DiscoveredJob(
        url=_job_url(token, job_id),
        title=clean_paycom_string(raw.get("jobTitle")),
        description=clean_paycom_string(raw.get("description")),
        locations=[location] if location else None,
        employment_type=clean_paycom_string(raw.get("positionType")),
        job_location_type=location_type,
        date_posted=clean_paycom_string(raw.get("postedOn")),
        metadata=metadata,
    )


def _page_rows(payload: dict, token: str) -> tuple[int, list[dict]]:
    total = payload.get("jobPostingPreviewsCount")
    rows = payload.get("jobPostingPreviews")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ValueError(f"Paycom {token!r} search omitted a valid count")
    if not isinstance(rows, list):
        raise ValueError(f"Paycom {token!r} search omitted its preview list")
    return total, rows


async def stream(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> AsyncIterator[MonitorResult]:
    """Stream API pagination in bounded hybrid batches for lease heartbeats."""
    from src.core.monitor import MonitorResult

    _ = pw
    metadata = board.get("metadata") or {}
    token = resolve_paycom_token(board["board_url"], metadata)
    if token is None:
        raise ValueError(
            f"Cannot derive Paycom token from board URL {board['board_url']!r} "
            "and no valid token is present in metadata"
        )

    bootstrap = await bootstrap_paycom(token, client)
    pending: list[DiscoveredJob] = []
    seen_urls: set[str] = set()
    expected_total: int | None = None
    raw_seen = 0
    raw_since_yield = 0
    invalid = 0
    duplicates = 0
    emitted = False

    for _page_number in range(MAX_PAGES):
        skip = raw_seen
        payload = await _fetch_search_page(bootstrap, client, skip=skip)
        total, rows = _page_rows(payload, token)
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValueError(
                f"Paycom {token!r} count changed during pagination ({expected_total} -> {total})"
            )

        if not rows:
            if skip < total:
                payload = await _fetch_search_page(bootstrap, client, skip=skip)
                retry_total, rows = _page_rows(payload, token)
                if retry_total != expected_total:
                    raise ValueError(
                        f"Paycom {token!r} count changed during pagination "
                        f"({expected_total} -> {retry_total})"
                    )
                if not rows:
                    raise PaginationFetchError(
                        _search_url(bootstrap),
                        attempts=2,
                        last_status=200,
                        last_error="PrematureEmptyPaycomPage",
                    )
            else:
                break

        if len(rows) > PAGE_SIZE or raw_seen + len(rows) > total:
            raise ValueError(f"Paycom {token!r} returned an inconsistent preview page")

        for raw in rows:
            raw_seen += 1
            raw_since_yield += 1
            if not isinstance(raw, dict):
                invalid += 1
                continue
            job = _parse_job(raw, token)
            if job is None:
                invalid += 1
                continue
            if job.url in seen_urls:
                duplicates += 1
                continue
            seen_urls.add(job.url)
            pending.append(job)

        done = raw_seen >= total or raw_seen >= MAX_JOBS
        if not done and raw_since_yield >= STREAM_BATCH:
            batch = {job.url: job for job in pending}
            yield MonitorResult(
                urls=set(batch),
                jobs_by_url=batch,
                hybrid=True,
            )
            emitted = True
            pending.clear()
            raw_since_yield = 0
        if done:
            break
    else:
        log.warning("paycom.page_cap", token=token, cap=MAX_PAGES)

    assert expected_total is not None
    if expected_total and not seen_urls:
        raise ValueError(f"Paycom {token!r} returned no valid job IDs")

    truncated = (
        invalid > 0 or duplicates > 0 or raw_seen != expected_total or expected_total > MAX_JOBS
    )
    if truncated:
        log.warning(
            "paycom.truncated",
            token=token,
            jobs=len(seen_urls),
            raw_seen=raw_seen,
            expected_total=expected_total,
            invalid=invalid,
            duplicates=duplicates,
            cap=MAX_JOBS,
        )
    final_batch = {job.url: job for job in pending}
    if final_batch or not emitted or truncated:
        yield MonitorResult(
            urls=set(final_batch),
            jobs_by_url=final_batch,
            hybrid=True,
            truncated=truncated,
        )
    log.info("paycom.discovered", token=token, jobs=len(seen_urls), truncated=truncated)


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
):
    """Materialize the streaming monitor for single-board debug callers."""

    from src.core.monitor import MonitorResult

    urls: set[str] = set()
    jobs_by_url: dict[str, DiscoveredJob] = {}
    truncated = False
    async for batch in stream(board, client, pw=pw):
        urls.update(batch.urls)
        if batch.jobs_by_url:
            jobs_by_url.update(batch.jobs_by_url)
        truncated = truncated or batch.truncated
    return MonitorResult(
        urls=urls,
        jobs_by_url=jobs_by_url,
        hybrid=True,
        truncated=truncated,
    )


async def fetch_paycom_job_count(token: str, client: httpx.AsyncClient) -> int:
    """Validate the real bootstrap/search path and return its advertised count."""

    bootstrap = await bootstrap_paycom(token, client)
    payload = await _fetch_search_page(bootstrap, client, skip=0, take=1)
    total, rows = _page_rows(payload, token)
    if len(rows) != min(total, 1) or (rows and _parse_job(rows[0], token) is None):
        raise ValueError(f"Paycom {token!r} returned an inconsistent one-row search probe")
    return total


async def probe_paycom_token(token: str, client: httpx.AsyncClient) -> ProbeResult:
    try:
        total = await fetch_paycom_job_count(token, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("paycom.probe_failed", token=token, exc_info=True)
        return False, None
    return True, total


async def _fetch_job_count(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await probe_paycom_token(token, client)
    return count if found else None


async def _probe_candidate(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await probe_paycom_token(token, client)


def _build_result(token: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    result: dict = {"token": token}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked Paycom public portals."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="paycom",
        token_from_url=paycom_token_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=frozenset(),
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_candidate,
        initial_context=None,
        result_builder=_build_result,
        page_token_probe=_probe_candidate,
        require_direct_count=True,
        allow_slug_guess=False,
        log_token_field="token",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    token = resolve_paycom_token(board_url, metadata)
    if token is None:
        return
    bootstrap = await bootstrap_paycom(token, client)
    payload = await _fetch_search_page(bootstrap, client, skip=0)
    (artifact_dir / "paycom-search.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


register(
    "paycom",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    stream=stream,
    save_raw=save_raw,
)
