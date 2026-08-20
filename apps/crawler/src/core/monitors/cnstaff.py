"""CNStaff public career-board monitor.

CNStaff tenants expose server-rendered boards at
``https://{tenant}.cnstaff.com/recruit``. Pagination requests to the same
route return complete JSON records when ``n=1`` is present. Those records
contain the stable job ID, title, location, publication date, responsibilities,
and qualifications, so no per-job scraper or browser session is needed.
"""

from __future__ import annotations

import math
import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.shared.html_normalize import normalize_description_html
from src.shared.http_retry import PaginationFetchError, fetch_json_page_with_retry
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

PAGE_SIZE = 15
MAX_JOBS = 50_000
_GONE_STATUSES = frozenset({404, 410})
_JOB_ID_RE = re.compile(r"^[0-9]+$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _origin_from_url(board_url: str) -> str | None:
    """Return a safe CNStaff tenant origin for an exact public board URL."""
    try:
        parsed = urlparse(board_url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host.endswith(".cnstaff.com")
        or host == "cnstaff.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path.rstrip("/") != "/recruit"
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"https://{host}"


def _board_url(origin: str) -> str:
    return f"{origin}/recruit"


def _job_url(origin: str, job_id: str) -> str:
    return f"{origin}/recruitment/job/detail/id/{job_id}/"


def _headers(origin: str) -> dict[str, str]:
    return {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "referer": _board_url(origin),
        "x-requested-with": "XMLHttpRequest",
    }


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"CNStaff returned invalid {field}")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isdigit():
        parsed = int(value)
    else:
        raise ValueError(f"CNStaff returned invalid {field}")
    if parsed < 0:
        raise ValueError(f"CNStaff returned invalid {field}")
    return parsed


def _date(value: object) -> str | None:
    raw = _clean_string(value)
    if raw is None:
        return None
    candidate = raw.split(" ", 1)[0]
    if candidate == "0000-00-00" or _DATE_RE.fullmatch(candidate) is None:
        return None
    return candidate


def _parse_job(raw: dict, origin: str) -> DiscoveredJob:
    job_id = _clean_string(raw.get("job_id"))
    title = _clean_string(raw.get("job_name_show")) or _clean_string(raw.get("job_name"))
    if job_id is None or _JOB_ID_RE.fullmatch(job_id) is None or title is None:
        raise ValueError("CNStaff returned a job without a valid ID or title")

    responsibilities = normalize_description_html(_clean_string(raw.get("job_detail")))
    qualifications = normalize_description_html(_clean_string(raw.get("job_desc2")))
    description = normalize_description_html(
        "\n".join(part for part in (responsibilities, qualifications) if part)
    )
    if description is None:
        raise ValueError("CNStaff returned a job without a description")

    location = _clean_string(raw.get("job_address_name"))
    extras = {
        key: value
        for key, value in {
            "responsibilities": responsibilities,
            "qualifications": qualifications,
            "valid_through": _date(raw.get("job_end_at")),
        }.items()
        if value
    }
    metadata = {
        key: value
        for key, value in {
            "id": job_id,
            "employer": _clean_string(raw.get("company_orgnize_name_show")),
            "department": _clean_string(raw.get("ws_system_job_type_ids_name")),
            "job_function": _clean_string(raw.get("g_job_type")),
        }.items()
        if value
    }

    return DiscoveredJob(
        url=_job_url(origin, job_id),
        title=title,
        description=description,
        locations=[location] if location else None,
        date_posted=_date(raw.get("job_published_at")),
        language="zh",
        extras=extras or None,
        metadata=metadata,
    )


async def _fetch_page(origin: str, page: int, client: httpx.AsyncClient) -> dict:
    url = _board_url(origin)
    try:
        return await fetch_json_page_with_retry(
            client,
            url,
            expect_shape=dict,
            params={"n": 1, "p": page},
            headers=_headers(origin),
            log_event="cnstaff.list_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "CNStaff public board no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise


def _parse_page(payload: dict, *, requested_page: int) -> tuple[int, int, list[dict]]:
    total = _nonnegative_int(payload.get("total"), "total")
    page_meta = payload.get("page")
    rows = payload.get("list")
    if not isinstance(page_meta, dict) or not isinstance(rows, list):
        raise ValueError("CNStaff response omitted page or list")

    current = _nonnegative_int(page_meta.get("now"), "page.now")
    total_pages = _nonnegative_int(page_meta.get("total"), "page.total")
    expected_pages = math.ceil(total / PAGE_SIZE) if total else 0
    if current != requested_page:
        raise ValueError(f"CNStaff returned page {current}, requested {requested_page}")
    if total_pages != expected_pages:
        raise ValueError(f"CNStaff reported {total_pages} pages for {total} jobs")

    expected_rows = min(PAGE_SIZE, max(0, total - (requested_page - 1) * PAGE_SIZE))
    if len(rows) != expected_rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(
            f"CNStaff page {requested_page} returned {len(rows)} rows, expected {expected_rows}"
        )
    return total, total_pages, rows


async def _discover(origin: str, client: httpx.AsyncClient) -> tuple[list[DiscoveredJob], bool]:
    first_payload = await _fetch_page(origin, 1, client)
    total, total_pages, first_rows = _parse_page(first_payload, requested_page=1)
    target = min(total, MAX_JOBS)
    pages = math.ceil(target / PAGE_SIZE) if target else 0

    rows = list(first_rows[:target])
    for page_number in range(2, pages + 1):
        payload = await _fetch_page(origin, page_number, client)
        page_total, page_count, page_rows = _parse_page(payload, requested_page=page_number)
        if page_total != total or page_count != total_pages:
            raise ValueError("CNStaff inventory changed during pagination")
        rows.extend(page_rows[: max(0, target - len(rows))])

    jobs: list[DiscoveredJob] = []
    seen_ids: set[str] = set()
    for row in rows:
        job = _parse_job(row, origin)
        job_id = str((job.metadata or {})["id"])
        if job_id in seen_ids:
            raise ValueError(f"CNStaff repeated job {job_id}")
        seen_ids.add(job_id)
        jobs.append(job)

    if len(jobs) != target:
        raise ValueError(f"CNStaff discovered {len(jobs)} jobs, expected {target}")
    return jobs, total > MAX_JOBS


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch all public jobs from a CNStaff tenant's paginated JSON listing."""
    _ = pw
    origin = _origin_from_url(board.get("board_url", ""))
    if origin is None:
        raise ValueError(f"Unsupported CNStaff board URL: {board.get('board_url')!r}")
    jobs, truncated = await _discover(origin, client)
    log.info("cnstaff.complete", origin=origin, jobs=len(jobs), truncated=truncated)
    return truncated_rich_result(jobs) if truncated else jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize and, when possible, verify an exact CNStaff board URL."""
    _ = pw
    origin = _origin_from_url(url)
    if origin is None:
        return None
    result: dict = {"origin": origin}
    if client is None:
        return result
    try:
        payload = await _fetch_page(origin, 1, client)
        total, _pages, _rows = _parse_page(payload, requested_page=1)
    except Exception:
        log.debug("cnstaff.probe_failed", origin=origin, exc_info=True)
        return None
    result["jobs"] = total
    return result


register("cnstaff", discover, cost=10, can_handle=can_handle, rich=True)
