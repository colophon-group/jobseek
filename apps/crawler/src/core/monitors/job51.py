"""51job branded career-board monitor.

51job hosts employer microsites at ``https://{tenant}.51job.com/*job_list.html``.
The visible table is populated by the provider's public CoAPI. Its listing and
detail responses contain stable job IDs plus complete descriptions, so a
dedicated HTTP monitor is cheaper and more reliable than browser extraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from urllib.parse import urlencode, urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.shared.html_normalize import normalize_description_html
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

PAGE_SIZE = 20
MAX_JOBS = 50_000
_DETAIL_CONCURRENCY = 5
_API_ORIGIN = "https://coapi.51job.com"
# Public signing material shipped by 51job in coapi.min.js. It authenticates
# the browser protocol, not a Jobseek account or private tenant credential.
_PUBLIC_SIGNING_KEY = "tuD&#mheJQBlgy&Sm300l8xK^X4NzFYBcrN8@YLCret$fv1AZbtujg*KN^$YnUkh"
_SIGNING_KEY_INDEX = 1
_BOARD_PATH_RE = re.compile(r"/[A-Za-z0-9_-]{1,64}job_list\.html")
_CTMID_RE = re.compile(r"\bctmid\s*:\s*['\"]?(\d{1,12})")
_JOB_ID_RE = re.compile(r"^[0-9]{1,20}$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_QUALIFICATIONS_RE = re.compile(r"(?:岗位要求|任职要求|职位要求)\s*[:：]?", re.IGNORECASE)
_EDGE_BREAKS_RE = re.compile(r"^(?:<br\s*/?>)+|(?:<br\s*/?>)+$", re.IGNORECASE)
_GONE_STATUSES = frozenset({404, 410})


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _board_origin(url: str) -> str | None:
    """Return the exact HTTPS origin for an unfiltered 51job board URL."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host.endswith(".51job.com")
        or host in {"51job.com", "www.51job.com", "jobs.51job.com", "coapi.51job.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or _BOARD_PATH_RE.fullmatch(parsed.path) is None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"https://{host}"


def _configured_ctmid(board: dict) -> int | None:
    raw = (board.get("metadata") or {}).get("ctmid")
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError("51job ctmid must be a positive integer")
    if isinstance(raw, int):
        ctmid = raw
    elif isinstance(raw, str) and raw.isdigit():
        ctmid = int(raw)
    else:
        raise ValueError("51job ctmid must be a positive integer")
    if ctmid <= 0 or ctmid > 999_999_999_999:
        raise ValueError("51job ctmid must be a positive integer")
    return ctmid


def _ctmid_from_html(html: str) -> int:
    match = _CTMID_RE.search(html)
    if match is None:
        raise ValueError("51job board omitted its public ctmid")
    return int(match.group(1))


def _signed_url(endpoint: str, params: dict) -> str:
    serialized = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
    key_slice = _PUBLIC_SIGNING_KEY[_SIGNING_KEY_INDEX : _SIGNING_KEY_INDEX + 15]
    signature = hashlib.md5(  # noqa: S324 - provider protocol, not security
        f"coapi{serialized}{key_slice}".encode()
    ).hexdigest()
    query = urlencode({"key": _SIGNING_KEY_INDEX, "sign": signature, "params": serialized})
    return f"{_API_ORIGIN}/{endpoint}?{query}"


def _parse_jsonp(text: str) -> dict:
    prefix = "jsoncallback("
    if not text.startswith(prefix) or not text.endswith(")"):
        raise ValueError("51job CoAPI returned malformed JSONP")
    try:
        payload = json.loads(text[len(prefix) : -1])
    except json.JSONDecodeError as exc:
        raise ValueError("51job CoAPI returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "1":
        raise ValueError("51job CoAPI reported an unsuccessful response")
    body = payload.get("resultbody")
    if not isinstance(body, dict):
        raise ValueError("51job CoAPI omitted resultbody")
    return body


async def _fetch_jsonp(endpoint: str, params: dict, client: httpx.AsyncClient) -> dict:
    url = _signed_url(endpoint, params)
    try:
        text = await fetch_text_page_with_retry(
            client,
            url,
            require_nonempty=True,
            max_chars=5_000_000,
            end_of_pagination_statuses=(),
            log_event="job51.api_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "51job public CoAPI endpoint no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    assert text is not None
    return _parse_jsonp(text)


async def _resolve_ctmid(board: dict, client: httpx.AsyncClient) -> int:
    configured = _configured_ctmid(board)
    if configured is not None:
        return configured
    board_url = board.get("board_url", "")
    try:
        html = await fetch_text_page_with_retry(
            client,
            board_url,
            require_nonempty=True,
            max_chars=1_000_000,
            end_of_pagination_statuses=(),
            log_event="job51.board_backoff",
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "51job public board no longer exists",
                url=board_url,
                status_code=exc.last_status,
            ) from exc
        raise
    assert html is not None
    return _ctmid_from_html(html)


def _list_params(ctmid: int, page: int) -> dict:
    return {
        "ctmid": ctmid,
        "pagesize": PAGE_SIZE,
        "pagenum": page,
        "jobarea": "",
        "issuedate": "",
        "keyword": "",
        "divid": "",
        "poscode": "",
    }


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"51job returned invalid {field}")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isdigit():
        parsed = int(value)
    else:
        raise ValueError(f"51job returned invalid {field}")
    if parsed < 0:
        raise ValueError(f"51job returned invalid {field}")
    return parsed


def _parse_list_page(body: dict, *, requested_page: int) -> tuple[int, list[dict]]:
    total = _nonnegative_int(body.get("totalnum"), "totalnum")
    rows = body.get("joblist")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("51job listing omitted joblist")
    expected = min(PAGE_SIZE, max(0, total - (requested_page - 1) * PAGE_SIZE))
    if len(rows) != expected:
        raise ValueError(
            f"51job page {requested_page} returned {len(rows)} rows, expected {expected}"
        )
    return total, rows


def _job_url(job_id: str) -> str:
    return f"https://jobs.51job.com/all/{job_id}.html"


def _date(value: object) -> str | None:
    raw = _clean_string(value)
    if raw is None:
        return None
    match = _DATE_RE.match(raw)
    return match.group(1) if match else None


def _description_parts(raw_html: str) -> tuple[str | None, str | None]:
    def normalized_part(value: str) -> str | None:
        normalized = normalize_description_html(value)
        if normalized is None:
            return None
        cleaned = _EDGE_BREAKS_RE.sub("", normalized).strip()
        return cleaned or None

    match = _QUALIFICATIONS_RE.search(raw_html)
    if match is None:
        return normalized_part(raw_html), None
    responsibilities = normalized_part(raw_html[: match.start()])
    qualifications = normalized_part(raw_html[match.end() :])
    return responsibilities, qualifications


def _parse_job(raw: dict, *, ctmid: int, expected_job_id: str) -> DiscoveredJob:
    job_id = _clean_string(raw.get("jobid"))
    title = _clean_string(raw.get("jobname"))
    returned_ctmid = _nonnegative_int(raw.get("ctmid"), "ctmid")
    if (
        job_id is None
        or _JOB_ID_RE.fullmatch(job_id) is None
        or job_id != expected_job_id
        or returned_ctmid != ctmid
        or title is None
    ):
        raise ValueError("51job returned a detail with an invalid identity or title")

    raw_description = _clean_string(raw.get("jobinfo"))
    description = normalize_description_html(raw_description)
    if raw_description is None or description is None:
        raise ValueError("51job returned a job without a description")
    responsibilities, qualifications = _description_parts(raw_description)

    location = _clean_string(raw.get("jobareaname")) or _clean_string(raw.get("workareaname"))
    skills_text = _clean_string(raw.get("jkeyword"))
    skills = list(dict.fromkeys(skills_text.split())) if skills_text else None
    extras = {
        key: value
        for key, value in {
            "skills": skills,
            "responsibilities": responsibilities,
            "qualifications": qualifications,
        }.items()
        if value
    }
    metadata = {
        key: value
        for key, value in {
            "id": job_id,
            "employer": _clean_string(raw.get("coname")),
            "department": _clean_string(raw.get("divname")),
            "address": _clean_string(raw.get("address")),
            "job_function": _clean_string(raw.get("funtype")),
            "experience": _clean_string(raw.get("workyearname")),
            "education": _clean_string(raw.get("degreefrom")),
            "salary_label": _clean_string(raw.get("providesalarname")),
            "benefits": _clean_string(raw.get("jobwelf")),
        }.items()
        if value
    }
    return DiscoveredJob(
        url=_job_url(job_id),
        title=title,
        description=description,
        locations=[location] if location else None,
        employment_type=_clean_string(raw.get("term")),
        date_posted=_date(raw.get("issuedate")),
        language="zh",
        extras=extras or None,
        metadata=metadata,
        source_identity=f"job51:{ctmid}:{job_id}",
    )


async def _discover(ctmid: int, client: httpx.AsyncClient) -> tuple[list[DiscoveredJob], bool]:
    first_body = await _fetch_jsonp("job_list.php", _list_params(ctmid, 1), client)
    total, first_rows = _parse_list_page(first_body, requested_page=1)
    target = min(total, MAX_JOBS)
    pages = math.ceil(target / PAGE_SIZE) if target else 0
    rows = list(first_rows[:target])

    for page in range(2, pages + 1):
        body = await _fetch_jsonp("job_list.php", _list_params(ctmid, page), client)
        page_total, page_rows = _parse_list_page(body, requested_page=page)
        if page_total != total:
            raise ValueError("51job inventory changed during pagination")
        rows.extend(page_rows[: max(0, target - len(rows))])

    job_ids: list[str] = []
    seen_ids: set[str] = set()
    for row in rows:
        job_id = _clean_string(row.get("jobid"))
        if job_id is None or _JOB_ID_RE.fullmatch(job_id) is None:
            raise ValueError("51job listing returned an invalid job ID")
        if job_id in seen_ids:
            raise ValueError(f"51job listing repeated job {job_id}")
        seen_ids.add(job_id)
        job_ids.append(job_id)
    if len(job_ids) != target:
        raise ValueError(f"51job discovered {len(job_ids)} jobs, expected {target}")

    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def fetch_detail(job_id: str) -> DiscoveredJob:
        async with semaphore:
            body = await _fetch_jsonp("job_detail.php", {"jobid": job_id}, client)
        return _parse_job(body, ctmid=ctmid, expected_job_id=job_id)

    jobs = list(await asyncio.gather(*(fetch_detail(job_id) for job_id in job_ids)))
    return jobs, total > MAX_JOBS


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return all complete jobs from a 51job employer microsite."""
    _ = pw
    board_url = board.get("board_url", "")
    origin = _board_origin(board_url)
    if origin is None:
        raise ValueError(f"Unsupported 51job board URL: {board_url!r}")
    ctmid = await _resolve_ctmid(board, client)
    jobs, truncated = await _discover(ctmid, client)
    log.info("job51.complete", origin=origin, ctmid=ctmid, jobs=len(jobs), truncated=truncated)
    return truncated_rich_result(jobs) if truncated else jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize and verify an exact 51job employer listing URL."""
    _ = pw
    origin = _board_origin(url)
    if origin is None:
        return None
    if client is None:
        return {"origin": origin}
    try:
        ctmid = await _resolve_ctmid({"board_url": url}, client)
        body = await _fetch_jsonp("job_list.php", _list_params(ctmid, 1), client)
        total, _rows = _parse_list_page(body, requested_page=1)
    except Exception:
        log.debug("job51.probe_failed", origin=origin, exc_info=True)
        return None
    return {"origin": origin, "ctmid": ctmid, "jobs": total}


register("job51", discover, cost=10, can_handle=can_handle, rich=True)
