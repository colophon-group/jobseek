"""104 Job Bank company-listing monitor.

104's company HTML is a client-rendered shell and may carry a Cloudflare
JavaScript challenge even when the public company jobs API remains available.
The API used by that page is therefore the authoritative inventory surface.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors.raw import save_json_response
from src.shared.http_retry import PaginationFetchError, fetch_json_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

MAX_JOBS = 50_000
PAGE_SIZE = 100

_TOKEN_RE = re.compile(r"^[a-z0-9]{5,16}$", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"^[a-z0-9]{5,16}$", re.IGNORECASE)
_GONE_STATUSES = frozenset({404, 410})


@dataclass(frozen=True, slots=True)
class _ApiPage:
    jobs: list[DiscoveredJob]
    total_count: int
    total_pages: int
    page: int
    page_size: int


def _normalize_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    return token if _TOKEN_RE.fullmatch(token) else None


def _token_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "www.104.com.tw"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 2 or segments[0].lower() != "company":
        return None
    return _normalize_token(segments[1])


def _resolve_token(board_url: str, metadata: dict) -> str | None:
    return _normalize_token(metadata.get("token")) or _token_from_url(board_url)


def _listing_url(token: str) -> str:
    return f"https://www.104.com.tw/company/{token}"


def _api_url(token: str) -> str:
    return f"https://www.104.com.tw/api/companies/{token}/jobs"


def _api_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Referer": _listing_url(token),
    }


def _canonical_job_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "www.104.com.tw"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) != 2
        or segments[0].lower() != "job"
        or _JOB_ID_RE.fullmatch(segments[1]) is None
    ):
        return None
    return f"https://www.104.com.tw/job/{segments[1].lower()}"


def _required_nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"104 Job Bank API returned invalid {field}")
    return value


def _required_text(value: object, field: str, *, page: int) -> str:
    if not isinstance(value, str) or not (cleaned := value.strip()):
        raise ValueError(f"104 Job Bank API page {page} returned an invalid {field}")
    return cleaned


def _plain_text_to_html(value: str) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]
    return "\n".join(f"<p>{escape(part).replace(chr(10), '<br>')}</p>" for part in paragraphs)


def _parse_api_page(payload: dict, *, requested_page: int) -> _ApiPage:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("104 Job Bank API omitted its data object")

    total_count = _required_nonnegative_int(data.get("totalCount"), "totalCount")
    total_pages = _required_nonnegative_int(data.get("totalPages"), "totalPages")
    page = _required_nonnegative_int(data.get("page"), "page")
    page_size = _required_nonnegative_int(data.get("pageSize"), "pageSize")
    if page != requested_page:
        raise ValueError(f"104 Job Bank API returned page {page}, requested {requested_page}")
    if page_size < 1 or page_size > PAGE_SIZE:
        raise ValueError(f"104 Job Bank API returned invalid pageSize {page_size}")

    listing = data.get("list")
    rows = listing.get("normalJobs") if isinstance(listing, dict) else None
    if not isinstance(rows, list):
        raise ValueError("104 Job Bank API omitted list.normalJobs")

    expected_pages = math.ceil(total_count / page_size) if total_count else 0
    if total_pages != expected_pages:
        raise ValueError(f"104 Job Bank API reported {total_pages} pages for {total_count} jobs")

    expected_rows = min(page_size, max(0, total_count - (page - 1) * page_size))
    if len(rows) != expected_rows:
        raise ValueError(
            f"104 Job Bank API page {page} returned {len(rows)} rows, expected {expected_rows}"
        )

    jobs: list[DiscoveredJob] = []
    urls: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"104 Job Bank API page {page} returned a non-object job")
        job_id = _normalize_token(row.get("jobNo"))
        canonical = _canonical_job_url(row.get("jobUrl", ""))
        if job_id is None or canonical != f"https://www.104.com.tw/job/{job_id}":
            raise ValueError(f"104 Job Bank API page {page} returned an invalid job identity")
        if canonical in urls:
            raise ValueError(f"104 Job Bank API page {page} repeated job {job_id}")
        urls.add(canonical)
        title = _required_text(row.get("jobName"), "jobName", page=page)
        location = _required_text(row.get("jobAddrNoDesc"), "jobAddrNoDesc", page=page)
        description = _required_text(row.get("jobDescription"), "jobDescription", page=page)
        jobs.append(
            DiscoveredJob(
                url=canonical,
                title=title,
                description=_plain_text_to_html(description),
                locations=[location],
            )
        )

    return _ApiPage(
        jobs=jobs,
        total_count=total_count,
        total_pages=total_pages,
        page=page,
        page_size=page_size,
    )


async def _fetch_api_page(
    token: str,
    page: int,
    client: httpx.AsyncClient,
) -> _ApiPage:
    url = _api_url(token)
    try:
        payload = await fetch_json_page_with_retry(
            client,
            url,
            expect_shape=dict,
            params={"page": page, "pageSize": PAGE_SIZE},
            headers=_api_headers(token),
            retryable_statuses={202, 401, 403, 999},
            log_event="jobbank104.list_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "104 Job Bank company API no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    return _parse_api_page(payload, requested_page=page)


async def _discover_jobs(token: str, client: httpx.AsyncClient) -> tuple[list[DiscoveredJob], bool]:
    first = await _fetch_api_page(token, 1, client)
    target = min(first.total_count, MAX_JOBS)
    pages = math.ceil(target / first.page_size) if target else 0
    jobs = list(first.jobs)
    urls = {job.url for job in first.jobs}
    for page_number in range(2, pages + 1):
        page = await _fetch_api_page(token, page_number, client)
        if (
            page.total_count != first.total_count
            or page.total_pages != first.total_pages
            or page.page_size != first.page_size
        ):
            raise ValueError("104 Job Bank inventory changed during pagination")
        page_urls = {job.url for job in page.jobs}
        overlap = urls & page_urls
        if overlap:
            raise ValueError(f"104 Job Bank API page {page_number} repeated {len(overlap)} jobs")
        jobs.extend(page.jobs)
        urls.update(page_urls)

    if len(jobs) != target:
        raise ValueError(f"104 Job Bank discovered {len(jobs)} jobs, expected {target}")
    return jobs, first.total_count > MAX_JOBS


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover rich jobs from the public company jobs API."""
    _ = pw
    metadata = board.get("metadata") or {}
    token = _resolve_token(board["board_url"], metadata)
    if token is None:
        raise ValueError(
            f"Cannot derive 104 Job Bank company token from {board['board_url']!r} "
            "and no valid token is present in metadata"
        )

    jobs, truncated = await _discover_jobs(token, client)
    log.info("jobbank104.discovered", token=token, jobs=len(jobs), truncated=truncated)
    return truncated_rich_result(jobs) if truncated else jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize an exact public 104 company URL and verify when reachable."""
    _ = pw
    token = _token_from_url(url)
    if token is None:
        return None
    result: dict = {"token": token}
    if client is None:
        return result
    try:
        jobs, _truncated = await _discover_jobs(token, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("jobbank104.probe_failed", token=token, exc_info=True)
        return result
    result["jobs"] = len(jobs)
    return result


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    token = _resolve_token(board_url, metadata)
    if token is None:
        return
    from src.shared.http import client_for

    async with client_for(client, metadata) as routed_client:
        await save_json_response(
            artifact_dir,
            routed_client,
            _api_url(token),
            filename="jobbank104-listing.json",
            params={"page": 1, "pageSize": PAGE_SIZE},
            headers=_api_headers(token),
        )


register("jobbank104", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
