"""BambooHR public careers API monitor.

BambooHR tenant boards expose every open requisition in one public JSON
response::

    GET https://{tenant}.bamboohr.com/careers/list

The listing is rich enough for stable identity and useful summaries, but the
HTML description and posting date live on the per-job detail endpoint. The
generic API scraper performs that enrichment on the normal scrape schedule,
using a BambooHR preset supplied by the workspace compatibility layer, so
hourly monitoring stays one request per board without a dedicated scraper.
Shared group tenants may opt into detail-description filtering; that mode
fetches each detail during discovery so employer-specific boards do not ingest
sibling brands.
"""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.raw import save_json_response
from src.shared.http_retry import PaginationFetchError, fetch_json_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

MAX_JOBS = 50_000
_DETAIL_CONCURRENCY = 10
_DESCRIPTION_FILTER_MAX_JOBS = 500
_DESCRIPTION_FILTER_MAX_PATTERN_LENGTH = 1_000

_TENANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HOST_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)\.bamboohr\.com$")
_PAGE_PATTERNS = [
    re.compile(r"https?://([a-z0-9][a-z0-9-]*)\.bamboohr\.com/careers", re.IGNORECASE)
]
_IGNORE_TENANTS = frozenset({"api", "app", "help", "static", "www"})
_LOCATION_TYPES = {"0": "onsite", "1": "remote", "2": "hybrid"}
_GONE_STATUSES = frozenset({404, 410})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _normalize_tenant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tenant = value.strip().lower()
    if tenant in _IGNORE_TENANTS or not _TENANT_RE.fullmatch(tenant):
        return None
    return tenant


def _tenant_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    match = _HOST_RE.fullmatch(host)
    if not match:
        return None
    path = parsed.path.rstrip("/").lower()
    if path not in {"/careers", "/careers/list", "/jobs/embed2.php"}:
        return None
    return _normalize_tenant(match.group(1))


def _origin(tenant: str) -> str:
    return f"https://{tenant}.bamboohr.com"


def _list_url(tenant: str) -> str:
    return f"{_origin(tenant)}/careers/list"


def _detail_url(tenant: str, job_id: object) -> str:
    return f"{_origin(tenant)}/careers/{job_id}"


def _detail_api_url(tenant: str, job_id: object) -> str:
    return f"{_detail_url(tenant, job_id)}/detail"


def _clean_part(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _parse_locations(raw: dict) -> list[str] | None:
    location = raw.get("location")
    if isinstance(location, str):
        value = _clean_part(location)
        return [value] if value else None
    if not isinstance(location, dict):
        location = {}

    ats_location = raw.get("atsLocation")
    if isinstance(ats_location, str):
        ats_value = _clean_part(ats_location)
        if ats_value:
            return [ats_value]
        ats_location = {}
    if not isinstance(ats_location, dict):
        ats_location = {}

    candidates = (
        (location.get("city"), ats_location.get("city")),
        (
            location.get("state") or location.get("province"),
            ats_location.get("state") or ats_location.get("province"),
        ),
        (
            location.get("addressCountry") or location.get("country"),
            ats_location.get("country"),
        ),
    )
    parts: list[str] = []
    for primary, fallback in candidates:
        part = _clean_part(primary) or _clean_part(fallback)
        if part and part.casefold() not in {existing.casefold() for existing in parts}:
            parts.append(part)
    return [", ".join(parts)] if parts else None


def _parse_location_type(raw: dict) -> str | None:
    value = raw.get("locationType")
    if value is not None:
        return _LOCATION_TYPES.get(str(value))
    return "remote" if raw.get("isRemote") is True else None


def _parse_job(raw: dict, tenant: str) -> DiscoveredJob | None:
    job_id = raw.get("id")
    if isinstance(job_id, bool) or not isinstance(job_id, (str, int)):
        return None
    job_id = str(job_id).strip()
    if not job_id.isdigit():
        return None

    metadata: dict = {"job_id": job_id}
    for source, target in (
        ("departmentLabel", "department"),
        ("departmentId", "department_id"),
    ):
        value = raw.get(source)
        if value not in (None, ""):
            metadata[target] = value

    return DiscoveredJob(
        url=_detail_url(tenant, job_id),
        title=_clean_part(raw.get("jobOpeningName")),
        locations=_parse_locations(raw),
        employment_type=_clean_part(raw.get("employmentStatusLabel")),
        job_location_type=_parse_location_type(raw),
        metadata=metadata,
    )


async def _fetch_listing(tenant: str, client: httpx.AsyncClient) -> dict:
    return await fetch_json_page_with_retry(
        client,
        _list_url(tenant),
        expect_shape=dict,
        retries=3,
        base_delay=0.5,
        log_event="bamboohr.list_backoff",
    )


def _description_include_pattern(metadata: dict) -> re.Pattern[str] | None:
    value = metadata.get("description_include_regex")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("BambooHR description_include_regex must be a non-empty string")
    value = value.strip()
    if len(value) > _DESCRIPTION_FILTER_MAX_PATTERN_LENGTH:
        raise ValueError(
            "BambooHR description_include_regex must be at most "
            f"{_DESCRIPTION_FILTER_MAX_PATTERN_LENGTH} characters"
        )
    try:
        return re.compile(value)
    except re.error as exc:
        raise ValueError(f"Invalid BambooHR description_include_regex: {exc}") from exc


def _description_text(description: str) -> str:
    return " ".join(html.unescape(_HTML_TAG_RE.sub(" ", description)).split())


async def _fetch_detail_description(
    tenant: str,
    job_id: object,
    client: httpx.AsyncClient,
) -> str:
    payload = await fetch_json_page_with_retry(
        client,
        _detail_api_url(tenant, job_id),
        expect_shape=dict,
        retries=3,
        base_delay=0.5,
        log_event="bamboohr.detail_backoff",
    )
    result = payload.get("result")
    opening = result.get("jobOpening") if isinstance(result, dict) else None
    description = opening.get("description") if isinstance(opening, dict) else None
    if not isinstance(description, str):
        raise ValueError(
            f"BambooHR detail for tenant {tenant!r}, job {job_id!r} has no string description"
        )
    return description


async def _filter_jobs_by_description(
    jobs: list[DiscoveredJob],
    tenant: str,
    pattern: re.Pattern[str],
    client: httpx.AsyncClient,
) -> list[DiscoveredJob]:
    if len(jobs) > _DESCRIPTION_FILTER_MAX_JOBS:
        raise ValueError(
            "BambooHR description filtering is limited to "
            f"{_DESCRIPTION_FILTER_MAX_JOBS} listed jobs; got {len(jobs)}"
        )

    async def matches(job: DiscoveredJob) -> DiscoveredJob | None:
        metadata = job.metadata or {}
        job_id = metadata.get("job_id")
        description = await _fetch_detail_description(tenant, job_id, client)
        if pattern.search(_description_text(description)) is None:
            return None
        job.description = description
        return job

    filtered: list[DiscoveredJob] = []
    for start in range(0, len(jobs), _DETAIL_CONCURRENCY):
        window = jobs[start : start + _DETAIL_CONCURRENCY]
        results = await asyncio.gather(*(matches(job) for job in window))
        filtered.extend(job for job in results if job is not None)
    return filtered


def _is_retirement_redirect(exc: PaginationFetchError) -> bool:
    if exc.last_status not in _REDIRECT_STATUSES or not exc.last_location:
        return False
    host = (urlparse(urljoin(exc.url, exc.last_location)).hostname or "").lower()
    return host in {"bamboohr.com", "www.bamboohr.com"}


def _parse_listing(
    payload: dict,
    tenant: str,
) -> tuple[list[DiscoveredJob], bool]:
    raw_jobs = payload.get("result")
    if not isinstance(raw_jobs, list):
        raise ValueError(f"BambooHR listing for {tenant!r} has invalid result")

    jobs: list[DiscoveredJob] = []
    seen_urls: set[str] = set()
    invalid_records = 0
    duplicate_records = 0
    for raw in raw_jobs:
        if not isinstance(raw, dict):
            invalid_records += 1
            continue
        job = _parse_job(raw, tenant)
        if job is None:
            invalid_records += 1
            continue
        if job.url in seen_urls:
            duplicate_records += 1
            continue
        seen_urls.add(job.url)
        jobs.append(job)

    if raw_jobs and not jobs:
        raise ValueError(f"BambooHR listing for {tenant!r} contains no valid job IDs")

    meta = payload.get("meta")
    raw_total = meta.get("totalCount") if isinstance(meta, dict) else None
    if not isinstance(raw_total, int) or isinstance(raw_total, bool) or raw_total < 0:
        raise ValueError(f"BambooHR listing for {tenant!r} has invalid meta.totalCount")
    total = raw_total
    if total < len(raw_jobs):
        raise ValueError(
            f"BambooHR listing for {tenant!r} returned {len(raw_jobs)} rows "
            f"but declared totalCount={total}"
        )

    truncated = (
        invalid_records > 0
        or duplicate_records > 0
        or len(jobs) > MAX_JOBS
        or total > len(raw_jobs)
    )
    if invalid_records or duplicate_records:
        log.warning(
            "bamboohr.invalid_records",
            tenant=tenant,
            invalid=invalid_records,
            duplicates=duplicate_records,
            rows=len(raw_jobs),
        )
    return jobs[:MAX_JOBS], truncated


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | MonitorResult:
    """Fetch one authoritative BambooHR tenant listing."""
    _ = pw
    metadata = board.get("metadata") or {}
    tenant = _normalize_tenant(metadata.get("tenant")) or _tenant_from_url(board["board_url"])
    if not tenant:
        raise ValueError(
            f"Cannot derive BambooHR tenant from board URL {board['board_url']!r} "
            "and no valid tenant is present in metadata"
        )
    description_include = _description_include_pattern(metadata)

    try:
        payload = await _fetch_listing(tenant, client)
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES or _is_retirement_redirect(exc):
            raise BoardGoneError(
                f"BambooHR tenant {tenant!r} no longer exists",
                url=_list_url(tenant),
            ) from exc
        raise

    jobs, truncated = _parse_listing(payload, tenant)
    listed_jobs = len(jobs)
    if description_include is not None:
        jobs = await _filter_jobs_by_description(jobs, tenant, description_include, client)
        log.info(
            "bamboohr.description_filter_applied",
            tenant=tenant,
            before=listed_jobs,
            after=len(jobs),
        )
    if truncated:
        meta = payload.get("meta")
        total = meta.get("totalCount") if isinstance(meta, dict) else None
        log.warning(
            "bamboohr.truncated",
            tenant=tenant,
            returned=len(jobs),
            declared_total=total,
            cap=MAX_JOBS,
        )
        return truncated_rich_result(jobs)

    log.info("bamboohr.discovered", tenant=tenant, jobs=len(jobs), listed_jobs=listed_jobs)
    return jobs


async def _probe_tenant(tenant: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    try:
        payload = await _fetch_listing(tenant, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("bamboohr.probe_failed", tenant=tenant, exc_info=True)
        return False, None

    raw_jobs = payload.get("result")
    if not isinstance(raw_jobs, list):
        return False, None
    meta = payload.get("meta")
    total = meta.get("totalCount") if isinstance(meta, dict) else None
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        return False, None
    return True, total


async def _fetch_job_count(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_tenant(token, client)
    return count if found else None


async def _probe_candidate(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_tenant(token, client)


def _build_result(token: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    result: dict = {"tenant": token}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked BambooHR tenant boards."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="bamboohr",
        token_from_url=_tenant_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=_IGNORE_TENANTS,
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_candidate,
        initial_context=None,
        result_builder=_build_result,
        page_token_probe=_probe_candidate,
        require_direct_count=True,
        allow_slug_guess=False,
        log_token_field="tenant",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    tenant = _normalize_tenant(metadata.get("tenant")) or _tenant_from_url(board_url)
    if tenant:
        await save_json_response(artifact_dir, client, _list_url(tenant))


register("bamboohr", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
