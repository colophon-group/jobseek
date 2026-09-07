"""ByteDance first-party careers API monitor.

ByteDance exposes one global supplier portal and two China portals.  The
experienced-hire API caps an unfiltered query at 10,000 results, so this
monitor discovers the provider's current top-level job categories and
collects each category as an internal partition.  The public board remains a
single unfiltered Jobseek board; provider filters never leak into board URLs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlparse

import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.api_sniff import FetchJsonFn, _fetch_page_with_retry, make_browser_fetcher
from src.shared.browser import navigate, open_page

log = structlog.get_logger()

_API_ORIGIN = "https://jobs.bytedance.com"
_SEARCH_API = f"{_API_ORIGIN}/api/v1/search/job/posts"
_GLOBAL_API = f"{_API_ORIGIN}/api/v1/public/supplier/search/job/posts"
_FILTER_API = f"{_API_ORIGIN}/api/v1/config/job/filters/2"
_PAGE_SIZE = 1_000
_RESULT_CAP = 10_000


@dataclass(frozen=True, slots=True)
class _Portal:
    api_url: str
    portal_type: int | None
    website_path: str
    portal_channel: str | None
    detail_template: str
    partition_categories: bool = False


_GLOBAL = _Portal(
    api_url=_GLOBAL_API,
    portal_type=None,
    website_path="en",
    portal_channel=None,
    detail_template="https://joinbytedance.com/search/{id}",
)
_EXPERIENCED = _Portal(
    api_url=_SEARCH_API,
    portal_type=2,
    website_path="society",
    portal_channel="office",
    detail_template=f"{_API_ORIGIN}/experienced/position/{{id}}/detail",
    partition_categories=True,
)
_CAMPUS = _Portal(
    api_url=_SEARCH_API,
    portal_type=3,
    website_path="campus",
    portal_channel="campus",
    detail_template=f"{_API_ORIGIN}/campus/position/{{id}}/detail",
)


def _portal_for_url(url: str) -> _Portal | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if parsed.query or parsed.fragment:
        return None
    if host in {"joinbytedance.com", "www.joinbytedance.com"} and path in {"", "/search"}:
        return _GLOBAL
    if host != "jobs.bytedance.com":
        return None
    if path == "/experienced/position":
        return _EXPERIENCED
    if path == "/campus/position":
        return _CAMPUS
    return None


async def can_handle(url: str, client, pw=None) -> dict | None:
    """Recognize only the three canonical, unfiltered ByteDance boards."""
    del client, pw
    return {} if _portal_for_url(url) is not None else None


def _headers(portal: _Portal) -> dict[str, str]:
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "website-path": portal.website_path,
        "portal-platform": "pc",
    }
    if portal.portal_channel:
        headers["portal-channel"] = portal.portal_channel
    if portal is _GLOBAL:
        headers["x-tt-env"] = "boe_epam_api"
    return headers


def _body(portal: _Portal, *, offset: int, category_ids: list[str] | None) -> str:
    if portal is _GLOBAL:
        value = {
            "recruitment_id_list": [],
            "job_category_id_list": category_ids or [],
            "subject_id_list": [],
            "location_code_list": [],
            "keyword": "",
            "limit": _PAGE_SIZE,
            "offset": offset,
        }
    else:
        value = {
            "keyword": "",
            "limit": _PAGE_SIZE,
            "offset": offset,
            "job_category_id_list": category_ids or [],
            "tag_id_list": [],
            "location_code_list": [],
            "subject_id_list": [],
            "recruitment_id_list": [],
            "portal_type": portal.portal_type,
            "job_function_id_list": [],
            "storefront_id_list": [],
            "portal_entrance": 1,
        }
    return json.dumps(value, separators=(",", ":"))


async def _fetch_json(
    fetch: FetchJsonFn,
    method: str,
    url: str,
    headers: dict[str, str],
    body: str | None = None,
) -> object:
    """Retry transient browser-fetch failures, then fail the whole cycle."""
    return await _fetch_page_with_retry(fetch, method, url, headers, body)


def _response_page(data: object) -> tuple[list[dict], int]:
    if not isinstance(data, dict) or data.get("code") != 0:
        raise RuntimeError("ByteDance jobs API returned an invalid response")
    payload = data.get("data")
    if not isinstance(payload, dict):
        raise RuntimeError("ByteDance jobs API omitted its data object")
    raw_items = payload.get("job_post_list")
    raw_total = payload.get("count")
    if not isinstance(raw_items, list) or not all(isinstance(item, dict) for item in raw_items):
        raise RuntimeError("ByteDance jobs API returned an invalid job list")
    if not isinstance(raw_total, int) or raw_total < 0:
        raise RuntimeError("ByteDance jobs API returned an invalid total")
    return raw_items, raw_total


async def _collect_partition(
    fetch: FetchJsonFn,
    portal: _Portal,
    category_ids: list[str] | None,
) -> tuple[list[dict], int]:
    headers = _headers(portal)
    items: list[dict] = []
    expected_total: int | None = None
    offset = 0

    while expected_total is None or offset < expected_total:
        data = await _fetch_json(
            fetch,
            "POST",
            portal.api_url,
            headers,
            _body(portal, offset=offset, category_ids=category_ids),
        )
        page, total = _response_page(data)
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise RuntimeError(
                f"ByteDance jobs total changed during pagination ({expected_total} -> {total})"
            )
        if expected_total >= _RESULT_CAP:
            raise RuntimeError(
                "ByteDance query reached the 10,000-result cap and requires a smaller partition"
            )
        if expected_total == 0:
            if page:
                raise RuntimeError("ByteDance jobs API returned rows with a zero total")
            return [], 0
        if not page:
            raise RuntimeError(
                f"ByteDance pagination ended at {len(items)} of {expected_total} jobs"
            )
        items.extend(page)
        offset += _PAGE_SIZE

    assert expected_total is not None
    ids = [str(item.get("id") or "") for item in items]
    if any(not value for value in ids) or len(set(ids)) != expected_total:
        raise RuntimeError(
            f"ByteDance pagination produced {len(set(ids))} unique IDs for {expected_total} jobs"
        )
    return items, expected_total


def _category_partitions(data: object) -> list[list[str]]:
    if not isinstance(data, dict) or data.get("code") != 0:
        raise RuntimeError("ByteDance filters API returned an invalid response")
    payload = data.get("data")
    if not isinstance(payload, dict):
        raise RuntimeError("ByteDance filters API omitted its data object")
    categories = payload.get("job_type_list")
    counts = payload.get("job_type_count_map")
    if not isinstance(categories, list) or not isinstance(counts, dict) or not categories:
        raise RuntimeError("ByteDance filters API omitted category partitions")

    partitions: list[list[str]] = []
    for category in categories:
        if not isinstance(category, dict) or not isinstance(category.get("id"), str):
            raise RuntimeError("ByteDance filters API returned an invalid category")
        category_id = category["id"]
        count = counts.get(category_id)
        if not isinstance(count, int) or count < 0:
            raise RuntimeError(f"ByteDance filters API omitted the count for {category_id}")
        if count == 0:
            continue
        if count < _RESULT_CAP:
            partitions.append([category_id])
            continue

        children = category.get("children")
        if not isinstance(children, list) or not children:
            raise RuntimeError(f"ByteDance category {category_id} exceeds the result cap")
        # Child totals are validated from each search response; using one child
        # per partition keeps every request below the provider ceiling.
        for child in children:
            if not isinstance(child, dict) or not isinstance(child.get("id"), str):
                raise RuntimeError("ByteDance filters API returned an invalid child category")
            partitions.append([child["id"]])

    return partitions


async def _collect(fetch: FetchJsonFn, portal: _Portal) -> list[dict]:
    if not portal.partition_categories:
        items, _total = await _collect_partition(fetch, portal, None)
        return items

    filters = await _fetch_json(fetch, "GET", _FILTER_API, _headers(portal))
    partitions = _category_partitions(filters)
    if not partitions:
        return []
    collected: dict[str, dict] = {}
    partition_total = 0
    for category_ids in partitions:
        items, total = await _collect_partition(fetch, portal, category_ids)
        partition_total += total
        for item in items:
            collected[str(item["id"])] = item

    # Filter counts cover multiple recruitment lanes and therefore exceed the
    # experienced-only search totals. The search response total is the
    # authoritative count for each partition. Job categories are mutually
    # exclusive, so any overlap is a completeness failure.
    if len(collected) != partition_total:
        raise RuntimeError(
            "ByteDance category partitions overlapped "
            f"({len(collected)} unique, {partition_total} fetched)"
        )
    log.info("bytedance.partition_done", partitions=len(partitions), jobs=len(collected))
    return list(collected.values())


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _to_job(item: dict, portal: _Portal) -> DiscoveredJob:
    job_id = str(item["id"])
    responsibility = _text(item.get("description"))
    requirement = _text(item.get("requirement"))
    description_parts: list[str] = []
    if responsibility:
        description_parts.extend(("<h3>Responsibilities</h3>", responsibility))
    if requirement:
        description_parts.extend(("<h3>Requirements</h3>", requirement))

    city = item.get("city_info") if isinstance(item.get("city_info"), dict) else {}
    parent = city.get("parent") if isinstance(city.get("parent"), dict) else {}
    location_parts = [_text(city.get("en_name")), _text(parent.get("en_name"))]
    location = ", ".join(dict.fromkeys(part for part in location_parts if part))

    recruit = item.get("recruit_type") if isinstance(item.get("recruit_type"), dict) else {}
    category = item.get("job_category")
    if not isinstance(category, dict):
        category = item.get("job_type") if isinstance(item.get("job_type"), dict) else {}
    team = _text(category.get("en_name")) or _text(category.get("name"))

    return DiscoveredJob(
        url=portal.detail_template.format(id=job_id),
        title=_text(item.get("title")),
        description="".join(description_parts) or None,
        locations=[location] if location else None,
        employment_type=_text(recruit.get("en_name")) or _text(recruit.get("name")),
        date_posted=str(item["create_time"]) if item.get("create_time") is not None else None,
        metadata={"team": team} if team else None,
        source_identity=f"bytedance:jobs.bytedance.com:{job_id}",
    )


async def discover(board: dict, client, pw=None) -> list[DiscoveredJob]:
    del client
    board_url = board["board_url"]
    portal = _portal_for_url(board_url)
    if portal is None:
        raise ValueError(f"Unsupported ByteDance board URL: {board_url}")
    if pw is None:
        raise RuntimeError("ByteDance monitor requires Playwright")

    async with open_page(pw, target_url=board_url) as page:
        await navigate(page, board_url)
        items = await _collect(make_browser_fetcher(page), portal)

    jobs = [_to_job(item, portal) for item in items]
    if any(not job.title or not job.description or not job.locations for job in jobs):
        raise RuntimeError("ByteDance API omitted a required job field")
    log.info("bytedance.discovered", board=board.get("board_slug"), jobs=len(jobs))
    return jobs


register("bytedance", discover, cost=10, can_handle=can_handle, rich=True)
