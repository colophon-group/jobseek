"""Wecruit (Dayee/Hotjob) public recruiting API monitor.

Branded Wecruit entry pages are small iframe launchers.  The actual SPA uses
the public ``/wecruit/positionInfo`` form APIs for every recruitment lane.
Joining the list and detail endpoints here avoids browser interaction and
keeps the four provider lanes in one complete board result.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.http_retry import (
    PaginationFetchError,
    fetch_json_page_with_retry,
    fetch_text_page_with_retry,
)
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

PAGE_SIZE = 50
MAX_JOBS = 50_000
DETAIL_CONCURRENCY = 20
RECRUIT_TYPES = (1, 2, 12, 13)
_SNAPSHOT_ATTEMPTS = 2
_SNAPSHOT_RETRY_DELAY = 1.0


class _SnapshotChanged(ValueError):
    """A recruitment lane changed while pagination was in progress."""


_SUITE_RE = re.compile(r"^/SU(?P<suite>[0-9a-f]{24})/(?:pb|mc)(?:/|$)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:$|[ T])")
_FORM_HEADERS = {"content-type": "application/x-www-form-urlencoded"}


@dataclass(frozen=True, slots=True)
class _Tenant:
    origin: str
    suite_key: str


def _https_origin(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None
    return f"https://{parsed.netloc.casefold()}"


def _suite_from_url(url: str) -> str | None:
    try:
        path = urlparse(url).path
    except ValueError:
        return None
    match = _SUITE_RE.match(path)
    return match.group("suite").lower() if match else None


def _provider_link_allowed(board_origin: str, link: str) -> bool:
    link_origin = _https_origin(link)
    if link_origin is None:
        return False
    host = (urlparse(link).hostname or "").casefold()
    return link_origin == board_origin or host == "wecruit.hotjob.cn"


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    data: dict[str, object],
    *,
    event: str,
) -> dict:
    text = await fetch_text_page_with_retry(
        client,
        url,
        method="POST",
        content=urlencode(data),
        headers=_FORM_HEADERS,
        timeout=30,
        end_of_pagination_statuses=(),
        require_nonempty=True,
        max_bytes=10_000_000,
        log_event=event,
    )
    if text is None:
        raise PaginationFetchError(url, attempts=1, last_status=404)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Wecruit endpoint returned invalid JSON: {url}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Wecruit endpoint returned a non-object response: {url}")
    return payload


def _envelope_data(payload: dict, *, context: str) -> dict:
    data = payload.get("data")
    if str(payload.get("state")) != "200" or not isinstance(data, dict):
        raise ValueError(f"Wecruit {context} response has an invalid envelope")
    return data


async def _validate_tenant(tenant: _Tenant, client: httpx.AsyncClient) -> None:
    url = f"{tenant.origin}/wecruit/suite/config/SU{tenant.suite_key}"
    payload = await fetch_json_page_with_retry(
        client,
        url,
        expect_shape=dict,
        timeout=30,
        follow_redirects=False,
        log_event="wecruit.config_backoff",
    )
    data = _envelope_data(payload, context="configuration")
    if (
        str(data.get("suiteKey", "")).casefold() != tenant.suite_key
        or not isinstance(data.get("companyId"), str)
        or not isinstance(data.get("config"), dict)
    ):
        raise ValueError("Wecruit configuration does not identify the requested suite")


async def _resolve_tenant(url: str, client: httpx.AsyncClient) -> _Tenant | None:
    board_origin = _https_origin(url)
    if board_origin is None:
        return None

    direct_suite = _suite_from_url(url)
    if direct_suite:
        tenant = _Tenant(board_origin, direct_suite)
    else:
        payload = await _post_json(
            client,
            f"{board_origin}/wecruit/common/getSLD",
            {"sld": urlparse(url).netloc.casefold()},
            event="wecruit.sld_backoff",
        )
        data = _envelope_data(payload, context="SLD")
        link_data = data.get("linkData")
        link = link_data.get("link") if isinstance(link_data, dict) else None
        if not isinstance(link, str) or not _provider_link_allowed(board_origin, link):
            return None
        suite_key = _suite_from_url(link)
        origin = _https_origin(link)
        if suite_key is None or origin is None:
            return None
        tenant = _Tenant(origin, suite_key)

    await _validate_tenant(tenant, client)
    return tenant


def _validated_recruit_types(value: object) -> tuple[int, ...]:
    if value is None:
        return RECRUIT_TYPES
    if (
        not isinstance(value, list)
        or not value
        or any(type(item) is not int or item not in RECRUIT_TYPES for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"Wecruit recruit_types must be unique values from {RECRUIT_TYPES}")
    return tuple(value)


async def _fetch_listing_page(
    tenant: _Tenant,
    recruit_type: int,
    page: int,
    client: httpx.AsyncClient,
) -> tuple[list[dict], int, int, int]:
    url = f"{tenant.origin}/wecruit/positionInfo/listPosition/SU{tenant.suite_key}"
    payload = await _post_json(
        client,
        url,
        {
            "isFrompb": "true",
            "recruitType": recruit_type,
            "pageSize": PAGE_SIZE,
            "currentPage": page,
        },
        event="wecruit.list_backoff",
    )
    data = _envelope_data(payload, context=f"listing lane {recruit_type} page {page}")
    page_form = data.get("pageForm")
    if not isinstance(page_form, dict):
        raise ValueError("Wecruit listing response is missing pageForm")

    rows = page_form.get("pageData")
    total_pages = page_form.get("totalPage")
    page_size = page_form.get("pageSize")
    current_page = page_form.get("currentPage")
    total = page_form.get("dataCount")
    provider_total = data.get("positonNum")
    explicit_zero = (
        rows == []
        and total_pages == 0
        and total == 0
        and provider_total == 0
        and (page_size, current_page) in {(0, 0), (PAGE_SIZE, page)}
    )
    if explicit_zero:
        return [], 0, 0, PAGE_SIZE
    if (
        not isinstance(rows, list)
        or any(not isinstance(row, dict) for row in rows)
        or type(total_pages) is not int
        or type(page_size) is not int
        or type(current_page) is not int
        or type(total) is not int
        or type(provider_total) is not int
        or total_pages < 1
        or page_size < 1
        or current_page != page
        or total < 0
        or provider_total != total
        or len(rows) > page_size
    ):
        raise ValueError("Wecruit listing response has invalid pagination metadata")
    expected_pages = (total + page_size - 1) // page_size
    if total_pages != expected_pages:
        raise ValueError(
            f"Wecruit listing page count mismatch: expected {expected_pages}, got {total_pages}"
        )
    return rows, total, total_pages, page_size


async def _fetch_listings_once(
    tenant: _Tenant,
    recruit_types: tuple[int, ...],
    client: httpx.AsyncClient,
) -> tuple[list[dict], bool]:
    listings: list[dict] = []
    seen: set[str] = set()
    truncated = False

    for recruit_type in recruit_types:
        first_rows, expected_total, total_pages, page_size = await _fetch_listing_page(
            tenant, recruit_type, 1, client
        )
        lane_rows = first_rows
        for page in range(2, total_pages + 1):
            rows, total, pages, runtime_page_size = await _fetch_listing_page(
                tenant, recruit_type, page, client
            )
            if total != expected_total or pages != total_pages or runtime_page_size != page_size:
                raise _SnapshotChanged(f"Wecruit lane {recruit_type} changed during pagination")
            lane_rows.extend(rows)

        if len(lane_rows) != expected_total:
            raise ValueError(
                f"Wecruit lane {recruit_type} returned {len(lane_rows)} of {expected_total} jobs"
            )
        for row in lane_rows:
            post_id = row.get("postId")
            if not isinstance(post_id, str) or _TOKEN_RE.fullmatch(post_id) is None:
                raise ValueError("Wecruit listing row has an invalid postId")
            if row.get("recruitType") != recruit_type:
                raise ValueError(f"Wecruit listing {post_id!r} has the wrong recruitment lane")
            if str(row.get("currentSuiteKey", "")).casefold() != tenant.suite_key:
                raise ValueError(f"Wecruit listing {post_id!r} belongs to another suite")
            if post_id in seen:
                raise ValueError(f"Wecruit listing response duplicated {post_id!r}")
            seen.add(post_id)
            if len(listings) >= MAX_JOBS:
                truncated = True
                continue
            listings.append(row)

    return listings, truncated


async def _fetch_listings(
    tenant: _Tenant,
    recruit_types: tuple[int, ...],
    client: httpx.AsyncClient,
) -> tuple[list[dict], bool]:
    """Retry one inconsistent lane snapshot from the first configured lane."""
    for attempt in range(1, _SNAPSHOT_ATTEMPTS + 1):
        try:
            return await _fetch_listings_once(tenant, recruit_types, client)
        except _SnapshotChanged as exc:
            if attempt == _SNAPSHOT_ATTEMPTS:
                raise
            log.warning(
                "wecruit.snapshot_changed",
                origin=tenant.origin,
                attempt=attempt,
                error=str(exc),
            )
            await asyncio.sleep(_SNAPSHOT_RETRY_DELAY)
    raise AssertionError("unreachable")


async def _fetch_details(
    tenant: _Tenant,
    listings: list[dict],
    client: httpx.AsyncClient,
) -> list[dict]:
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)
    url = f"{tenant.origin}/wecruit/positionInfo/listPositionDetail/SU{tenant.suite_key}"

    async def fetch_one(listing: dict) -> dict:
        post_id = listing["postId"]
        async with semaphore:
            payload = await _post_json(
                client,
                url,
                {"postId": post_id},
                event="wecruit.detail_backoff",
            )
        detail = _envelope_data(payload, context=f"detail {post_id}")
        if detail.get("postId") != post_id:
            raise ValueError(f"Wecruit detail identity mismatch for {post_id!r}")
        if detail.get("recruitType") != listing["recruitType"]:
            raise ValueError(f"Wecruit detail lane mismatch for {post_id!r}")
        return detail

    return list(await asyncio.gather(*(fetch_one(listing) for listing in listings)))


def _date(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    match = _DATE_RE.match(value.strip())
    if match is None:
        raise ValueError(f"Wecruit response has an invalid date: {value!r}")
    return date.fromisoformat(match.group(1)).isoformat()


def _text_html(value: object) -> str:
    if not isinstance(value, str):
        return ""
    lines = [html.escape(line.strip(), quote=False) for line in value.splitlines() if line.strip()]
    return f"<p>{'<br>'.join(lines)}</p>" if lines else ""


def _description(detail: dict, post_id: str) -> str:
    responsibilities = _text_html(detail.get("workContent"))
    qualifications = _text_html(detail.get("serviceCondition"))
    sections: list[str] = []
    if responsibilities:
        sections.extend(("<h3>工作职责</h3>", responsibilities))
    if qualifications:
        sections.extend(("<h3>任职要求</h3>", qualifications))
    if not sections:
        raise ValueError(f"Wecruit detail {post_id!r} has no job description")
    return "\n".join(sections)


def _locations(detail: dict, post_id: str) -> list[str]:
    result: list[str] = []
    raw_locations = detail.get("workPlaceList")
    if isinstance(raw_locations, list):
        for item in raw_locations:
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str) and name.strip() and name.strip() not in result:
                result.append(name.strip())
    if not result:
        fallback = detail.get("workPlaceStr")
        if isinstance(fallback, str) and fallback.strip():
            result.append(fallback.strip())
    if not result:
        raise ValueError(f"Wecruit detail {post_id!r} has no work location")
    return result


def _parse_job(tenant: _Tenant, listing: dict, detail: dict) -> DiscoveredJob:
    post_id = listing["postId"]
    recruit_type = listing["recruitType"]
    title = detail.get("postName") or listing.get("postName")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Wecruit detail {post_id!r} has no title")

    extras: dict = {}
    qualifications = detail.get("serviceCondition")
    responsibilities = detail.get("workContent")
    if isinstance(qualifications, str) and qualifications.strip():
        extras["qualifications"] = qualifications.strip()
    if isinstance(responsibilities, str) and responsibilities.strip():
        extras["responsibilities"] = responsibilities.strip()
    valid_through = _date(detail.get("endDate"))
    if valid_through:
        extras["valid_through"] = valid_through

    metadata = {
        "post_code": detail.get("postCode"),
        "external_post_id": detail.get("externalPostId"),
        "company": detail.get("company"),
        "department": detail.get("department"),
        "post_type": detail.get("postTypeName"),
        "job_level": detail.get("jobLevel"),
        "education": detail.get("education"),
        "recruits": detail.get("recruitNumStr"),
        "project": detail.get("projectName"),
        "recruit_type": recruit_type,
    }
    metadata = {key: value for key, value in metadata.items() if value not in (None, "")}

    return DiscoveredJob(
        url=(
            f"{tenant.origin}/SU{tenant.suite_key}/pb/posDetail.html?"
            f"postId={post_id}&postType={recruit_type}"
        ),
        title=title.strip(),
        description=_description(detail, post_id),
        locations=_locations(detail, post_id),
        date_posted=_date(detail.get("publishDate") or listing.get("publishDate")),
        language="zh",
        extras=extras or None,
        metadata=metadata or None,
        source_identity=f"wecruit:{tenant.suite_key}:{post_id}",
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch every configured recruitment lane and join public job details."""
    _ = pw
    metadata = board.get("metadata") or {}
    suite_key = metadata.get("suite_key")
    origin = metadata.get("api_origin")
    if suite_key is not None and (
        not isinstance(suite_key, str) or _TOKEN_RE.fullmatch(suite_key) is None
    ):
        raise ValueError("Wecruit suite_key must be a 24-character hexadecimal token")
    if origin is not None and (not isinstance(origin, str) or _https_origin(origin) != origin):
        raise ValueError("Wecruit api_origin must be a canonical HTTPS origin")

    if suite_key is None or origin is None:
        tenant = await _resolve_tenant(board["board_url"], client)
        if tenant is None:
            raise ValueError(f"Cannot resolve Wecruit tenant from {board['board_url']!r}")
    else:
        tenant = _Tenant(origin, suite_key.casefold())

    recruit_types = _validated_recruit_types(metadata.get("recruit_types"))
    listings, truncated = await _fetch_listings(tenant, recruit_types, client)
    details = await _fetch_details(tenant, listings, client)
    jobs = [
        _parse_job(tenant, listing, detail)
        for listing, detail in zip(listings, details, strict=True)
    ]
    log.info(
        "wecruit.discovered",
        suite_key=tenant.suite_key,
        recruit_types=recruit_types,
        jobs=len(jobs),
        truncated=truncated,
    )
    return truncated_rich_result(jobs) if truncated else jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Resolve an iframe launcher or direct Wecruit SPA URL and verify its API."""
    _ = pw
    if client is None:
        return None
    try:
        tenant = await _resolve_tenant(url, client)
        if tenant is None:
            return None
        total = 0
        for recruit_type in RECRUIT_TYPES:
            _rows, count, _pages, _page_size = await _fetch_listing_page(
                tenant, recruit_type, 1, client
            )
            total += count
        return {
            "api_origin": tenant.origin,
            "suite_key": tenant.suite_key,
            "recruit_types": list(RECRUIT_TYPES),
            "jobs": total,
        }
    except (PaginationFetchError, ValueError):
        log.debug("wecruit.probe_failed", url=url, exc_info=True)
        return None


register("wecruit", discover, cost=10, can_handle=can_handle, rich=True)
