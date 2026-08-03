"""ADP Workforce Now public career-center monitor.

The public listing API omits descriptions, so this monitor returns the rich
fields available in each 20-row search page and lets Jobseek's existing ADP
detail scraper enrich new and refreshed postings. Transport, retry, pagination,
and field normalization remain shared with the generic API monitor machinery.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import httpx
import structlog

from src.core.adp import normalize_adp_employment_type
from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.shared.adp import (
    AdpBoard,
    adp_board_from_metadata,
    adp_board_from_url,
    adp_start_from_search_url,
    normalize_adp_job_id,
)
from src.shared.api_sniff import (
    ArrayCandidate,
    Exchange,
    JobListResult,
    PaginationInfo,
    _fetch_page_with_retry,
    make_http_fetcher,
    paginate_all,
)
from src.shared.http_retry import PaginationFetchError
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

PAGE_SIZE = 20
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
_GONE_STATUSES = frozenset({404, 410})
_PAGE_PATTERNS = [
    re.compile(
        r"(https?://workforcenow\.adp\.com/mascsr/default/mdf/recruitment/"
        r"recruitment\.html\?[^\"'<\s]+)",
        re.IGNORECASE,
    )
]
_REQUEST_HEADERS = {
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "X-Forwarded-Host": "workforcenow.adp.com",
}


def _board_key(board: dict) -> AdpBoard:
    metadata = board.get("metadata") or {}
    key = adp_board_from_metadata(metadata) or adp_board_from_url(board["board_url"])
    if key is None:
        raise ValueError(
            f"Cannot derive ADP cid/cc_id/locale from board URL {board['board_url']!r} or metadata"
        )
    return key


def _page_rows(payload: object, board: AdpBoard, requested_start: int) -> tuple[int, list[dict]]:
    if not isinstance(payload, dict):
        raise ValueError(f"ADP {board.cid!r} search returned a non-object payload")
    rows = payload.get("jobRequisitions")
    meta = payload.get("meta")
    if not isinstance(rows, list) or not isinstance(meta, dict):
        raise ValueError(f"ADP {board.cid!r} search omitted jobs or metadata")
    total = meta.get("totalNumber")
    start = meta.get("startSequence")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError(f"ADP {board.cid!r} search omitted a valid total")
    if start != requested_start:
        raise ValueError(f"ADP {board.cid!r} search returned an unexpected start sequence")
    if len(rows) > PAGE_SIZE:
        raise ValueError(f"ADP {board.cid!r} search exceeded the page-size contract")
    expected_rows = min(PAGE_SIZE, max(total - requested_start + 1, 0))
    if len(rows) != expected_rows:
        raise ValueError(f"ADP {board.cid!r} search returned an inconsistent page")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"ADP {board.cid!r} search returned a malformed job row")
    return total, rows


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = html.unescape(value).strip()
    return value or None


def _locations(raw: object) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    locations: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name_code = item.get("nameCode") if isinstance(item, dict) else None
        location = (
            _clean_string(name_code.get("shortName")) if isinstance(name_code, dict) else None
        )
        if location:
            location = re.sub(r"\s+,", ",", " ".join(location.split()))
            key = location.casefold()
            if key not in seen:
                seen.add(key)
                locations.append(location)
    return locations or None


def _parse_job(raw: object, board: AdpBoard) -> DiscoveredJob | None:
    if not isinstance(raw, dict):
        return None
    job_id = normalize_adp_job_id(raw.get("itemID"))
    title = _clean_string(raw.get("requisitionTitle"))
    if job_id is None or title is None:
        return None
    work_level = raw.get("workLevelCode")
    employment_type = normalize_adp_employment_type(
        work_level.get("shortName") if isinstance(work_level, dict) else None
    )
    date_posted = _clean_string(raw.get("postDate"))
    metadata: dict[str, object] = {"item_id": job_id}
    requisition_id = _clean_string(raw.get("clientRequisitionID"))
    if requisition_id is not None:
        metadata["requisition_id"] = requisition_id
    return DiscoveredJob(
        url=board.job_url(job_id),
        title=title,
        locations=_locations(raw.get("requisitionLocations")),
        employment_type=employment_type,
        date_posted=date_posted,
        language=board.locale[:2],
        metadata=metadata,
    )


async def _fetch_all(
    board: AdpBoard,
    client: httpx.AsyncClient,
) -> tuple[list[DiscoveredJob], bool]:
    raw_fetch = make_http_fetcher(client)
    observed_totals: list[int] = []

    async def validated_fetch(method: str, url: str, headers: dict, body: str | None):
        if method != "GET" or body is not None:
            raise ValueError("ADP search transport received an unexpected request")
        start = adp_start_from_search_url(url, board)
        if start is None:
            raise ValueError("ADP search transport received an untrusted URL")
        payload = await raw_fetch(method, url, headers, body)
        total, _rows = _page_rows(payload, board, start)
        observed_totals.append(total)
        return payload

    first_url = board.search_url(start=1)
    try:
        first_payload = await _fetch_page_with_retry(
            validated_fetch,
            "GET",
            first_url,
            _REQUEST_HEADERS,
            None,
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError("ADP board no longer exists", url=first_url) from exc
        raise

    first_total, first_rows = _page_rows(first_payload, board, 1)
    if not first_rows:
        return [], first_total != 0 or len(set(observed_totals)) != 1

    if first_total <= len(first_rows):
        jobs = [job for raw in first_rows if (job := _parse_job(raw, board)) is not None]
        unique_jobs = {job.url: job for job in jobs}
        truncated = len(jobs) != len(first_rows) or len(unique_jobs) != len(jobs)
        return list(unique_jobs.values()), truncated

    result = JobListResult(
        candidate=ArrayCandidate(
            exchange=Exchange(
                method="GET",
                url=first_url,
                request_headers=_REQUEST_HEADERS,
                post_data=None,
                status=200,
                body=first_payload,
                content_type="application/json",
                phase="load",
            ),
            json_path="jobRequisitions",
            items=first_rows,
        ),
        url_field=None,
        total_count=first_total,
        pagination=PaginationInfo(
            param_name="$skip",
            style="offset",
            start_value=1,
            increment=PAGE_SIZE,
            location="query",
        ),
    )
    rows = await paginate_all(validated_fetch, result, MAX_PAGES)
    jobs = [job for raw in rows if (job := _parse_job(raw, board)) is not None]
    unique_jobs = {job.url: job for job in jobs}
    expected = min(first_total, MAX_JOBS)
    truncated = (
        len(set(observed_totals)) != 1
        or len(rows) != expected
        or len(jobs) != len(rows)
        or len(unique_jobs) != len(jobs)
        or first_total > MAX_JOBS
    )
    return list(unique_jobs.values()), truncated


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover and map all public ADP requisition listing pages."""
    _ = pw
    key = _board_key(board)
    jobs, truncated = await _fetch_all(key, client)
    log.info("adp.discovered", cid=key.cid, cc_id=key.cc_id, jobs=len(jobs), truncated=truncated)
    return truncated_rich_result(jobs) if truncated else jobs


async def _probe_listing_url(
    listing_url: str,
    client: httpx.AsyncClient,
) -> tuple[bool, int | None]:
    board = adp_board_from_url(html.unescape(listing_url))
    if board is None:
        return False, None
    raw_fetch = make_http_fetcher(client)
    url = board.search_url(start=1)
    try:
        payload = await _fetch_page_with_retry(raw_fetch, "GET", url, _REQUEST_HEADERS, None)
        total, _rows = _page_rows(payload, board, 1)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("adp.probe_failed", listing_url=listing_url, exc_info=True)
        return False, None
    return True, total


async def _fetch_job_count(
    listing_url: str,
    client: httpx.AsyncClient,
    context: int | None,
) -> ProbeCount | None:
    _ = listing_url, client
    return context


async def _probe_candidate(
    listing_url: str,
    client: httpx.AsyncClient,
    context: int | None,
) -> ProbeResult:
    _ = context
    return await _probe_listing_url(listing_url, client)


async def _resolve_direct(
    url: str,
    listing_url: str,
    client: httpx.AsyncClient,
    context: int | None,
) -> tuple[str, int | None] | None:
    _ = url, context
    found, total = await _probe_listing_url(listing_url, client)
    return (listing_url, total) if found else None


def _listing_url_from_url(url: str) -> str | None:
    board = adp_board_from_url(html.unescape(url))
    return board.listing_url() if board is not None else None


def _build_result(
    listing_url: str,
    count: ProbeCount | None,
    context: int | None,
) -> dict:
    _ = context
    board = adp_board_from_url(html.unescape(listing_url))
    if board is None:
        raise ValueError("ADP result builder received an invalid listing URL")
    result: dict[str, object] = {
        "cid": board.cid,
        "cc_id": board.cc_id,
        "locale": board.locale,
    }
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked public ADP boards."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="adp",
        token_from_url=_listing_url_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=frozenset(),
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_candidate,
        initial_context=None,
        result_builder=_build_result,
        direct_token_resolver=_resolve_direct,
        page_token_probe=_probe_candidate,
        require_direct_count=False,
        allow_slug_guess=False,
        log_token_field="listing_url",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    board = adp_board_from_metadata(metadata) or adp_board_from_url(board_url)
    if board is None:
        return
    raw_fetch = make_http_fetcher(client)
    payload = await _fetch_page_with_retry(
        raw_fetch,
        "GET",
        board.search_url(start=1),
        _REQUEST_HEADERS,
        None,
    )
    _page_rows(payload, board, 1)
    (artifact_dir / "adp-listing.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


register("adp", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
