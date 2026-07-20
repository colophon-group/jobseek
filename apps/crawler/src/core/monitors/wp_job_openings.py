"""WordPress WP Job Openings monitor.

The WP Job Openings plugin registers published vacancies as the public
``awsm_job_openings`` WordPress post type.  Its REST collection remains
available when a board has no live jobs, which makes it a reliable source for
both empty-board detection and future job URLs.

Detail pages include schema.org ``JobPosting`` JSON-LD, so this URL-only
monitor is paired with the ``json-ld`` scraper.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import register
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
_PAGE_SIZE = 100
_POST_TYPE = "awsm_job_openings"
_MARKERS = ("wp-job-openings", "awsm-job-")


def _origin(url: str) -> str | None:
    """Return the normalized origin for an HTTP(S) URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname.lower()}{port}"


def _rest_url(origin: str, rest_base: str = _POST_TYPE) -> str:
    return f"{origin.rstrip('/')}/wp-json/wp/v2/{rest_base.strip('/')}"


def _has_plugin_marker(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _MARKERS)


def _links_from_payload(payload: object) -> set[str]:
    if not isinstance(payload, list):
        raise ValueError("WP Job Openings REST endpoint did not return a JSON list")
    urls: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        link = item.get("link")
        if isinstance(link, str) and link.strip():
            urls.add(link.strip())
    return urls


async def _fetch_collection_page(
    rest_url: str,
    client: httpx.AsyncClient,
    page: int,
) -> httpx.Response:
    response = await client.get(
        rest_url,
        params={"per_page": _PAGE_SIZE, "page": page, "_fields": "link"},
        follow_redirects=True,
    )
    response.raise_for_status()
    return response


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Return every published WP Job Openings detail URL."""
    metadata = board.get("metadata") or {}
    rest_url = metadata.get("rest_url")
    if not isinstance(rest_url, str) or not rest_url.strip():
        origin = _origin(board["board_url"])
        if not origin:
            raise ValueError(f"Cannot derive WordPress origin from {board['board_url']!r}")
        rest_url = _rest_url(origin)
    rest_url = rest_url.strip()

    first = await _fetch_collection_page(rest_url, client, 1)
    urls = _links_from_payload(first.json())

    total_header = first.headers.get("X-WP-Total")
    pages_header = first.headers.get("X-WP-TotalPages")
    try:
        total = int(total_header) if total_header is not None else len(urls)
    except ValueError:
        total = len(urls)
    try:
        total_pages = int(pages_header) if pages_header is not None else 1
    except ValueError:
        total_pages = 1

    max_pages = min(total_pages, (MAX_JOBS + _PAGE_SIZE - 1) // _PAGE_SIZE)
    for page in range(2, max_pages + 1):
        response = await _fetch_collection_page(rest_url, client, page)
        urls.update(_links_from_payload(response.json()))

    truncated = total > MAX_JOBS or total_pages > max_pages
    if truncated:
        log.warning("wp_job_openings.truncated", rest_url=rest_url, total=total, cap=MAX_JOBS)
        return truncated_url_result(urls)

    log.info("wp_job_openings.listed", rest_url=rest_url, jobs=len(urls))
    return urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect WP Job Openings from first-party page markers and REST metadata."""
    _ = pw
    if client is None:
        return None

    try:
        page = await client.get(url, follow_redirects=True)
        if page.status_code != 200 or not _has_plugin_marker(page.text):
            return None

        origin = _origin(str(page.url))
        if not origin:
            return None

        type_response = await client.get(
            f"{origin}/wp-json/wp/v2/types/{_POST_TYPE}",
            params={"_fields": "rest_base,slug"},
            follow_redirects=True,
        )
        if type_response.status_code != 200:
            return None
        type_data = type_response.json()
        if not isinstance(type_data, dict) or type_data.get("slug") != _POST_TYPE:
            return None
        rest_base = type_data.get("rest_base")
        if not isinstance(rest_base, str) or not rest_base:
            return None

        rest_url = _rest_url(origin, rest_base)
        collection = await _fetch_collection_page(rest_url, client, 1)
        _links_from_payload(collection.json())
        try:
            jobs = int(collection.headers.get("X-WP-Total", "0"))
        except ValueError:
            jobs = len(collection.json())
        return {"rest_url": rest_url, "jobs": jobs}
    except Exception:
        return None


register("wp_job_openings", discover, cost=10, can_handle=can_handle)
