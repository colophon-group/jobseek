"""API sniffer monitor.

Discovers job listings by capturing XHR/fetch requests that career pages make
to internal APIs.  Works for React SPAs, custom platforms, and any site that
loads job data via JSON APIs.

Supports two modes:

- **Rich mode** (``fields`` configured): returns ``list[DiscoveredJob]``
- **URL-only mode** (no ``fields``): returns ``set[str]``

When replaying from stored config (``api_url`` present), opens the page to
establish cookies/auth context, then replays the API via in-browser fetch.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.api_sniff import (
    auto_map_fields,
    capture_exchanges,
    clean_headers,
    detect_job_list,
    extract_items,
    extract_urls,
    extract_urls_via_dom_crossref,
    fetch_json,
    infer_pagination,
    paginate_all,
    trigger_interactions,
)
from src.shared.nextdata import extract_field

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_ITEMS = 10_000
MAX_PAGES = 50


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Detect whether *url* loads job data via XHR/fetch APIs.

    Returns a metadata dict suitable for use as monitor_config, or None
    if no job-list API is detected.  Requires Playwright (*pw*).
    """
    if pw is None:
        return None

    from src.shared.browser import dismiss_overlays, navigate, open_page

    try:
        async with open_page(pw, {}) as page:
            page_host = urlparse(url).netloc
            exchanges = await capture_exchanges(page, page_host)

            await navigate(page, url, {"wait": "networkidle", "timeout": 45_000})
            await asyncio.sleep(3)

            await dismiss_overlays(page)
            await trigger_interactions(page, exchanges)

            result = detect_job_list(exchanges, url)
            if result is None:
                return None

            ex = result.candidate.exchange
            page_size = len(result.candidate.items)

            # Infer pagination if two matching exchanges exist
            result.pagination = infer_pagination(exchanges, ex.url, page_size)

            # Auto-map fields
            fields = auto_map_fields(result.candidate.items)

            # Build metadata
            meta: dict = {
                "api_url": ex.url,
                "method": ex.method,
                "json_path": result.candidate.json_path,
                "items": page_size,
                "score": result.candidate.score,
            }
            if result.url_field:
                meta["url_field"] = result.url_field
            else:
                # No URL field — try DOM cross-reference to derive url_template
                try:
                    from src.shared.api_sniff import ID_FIELDS as _ID_FIELDS

                    dom_urls = await extract_urls_via_dom_crossref(
                        page, result.candidate.items, url,
                    )
                    if dom_urls:
                        # Derive template from the first URL + first item
                        first_item = result.candidate.items[0]
                        id_field = None
                        for key in first_item:
                            if _ID_FIELDS.match(key):
                                id_field = key
                                break
                        if id_field:
                            first_id = str(first_item[id_field])
                            first_url = dom_urls[0]
                            # Replace the ID with a {id_field} placeholder
                            template = first_url.replace(first_id, "{" + id_field + "}")
                            meta["url_template"] = template
                except Exception:
                    log.debug("api_sniffer.dom_crossref_failed", exc_info=True)

            if result.total_count:
                meta["total"] = result.total_count
            if ex.post_data:
                meta["post_data"] = ex.post_data
            if result.pagination:
                pag = result.pagination
                meta["pagination"] = {
                    "param_name": pag.param_name,
                    "style": pag.style,
                    "start_value": pag.start_value,
                    "increment": pag.increment,
                    "location": pag.location,
                }

            # Include request headers (cleaned)
            headers = clean_headers(ex.request_headers)
            if headers:
                meta["request_headers"] = headers

            if fields:
                meta["fields"] = fields

            return meta

    except Exception:
        log.debug("api_sniffer.can_handle_failed", url=url, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | set[str]:
    """Discover jobs via API sniffing.

    - **Replay mode** (config has ``api_url``): navigate to board_url to
      establish cookies, then replay the stored API call via in-browser fetch.
    - **Auto-discover mode** (no ``api_url``): full capture + detect pipeline.
    """
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    if pw is None:
        log.error("api_sniffer.no_playwright", board_url=board_url)
        return set()

    api_url = metadata.get("api_url")

    if api_url:
        return await _discover_replay(board_url, metadata, pw)
    return await _discover_auto(board_url, metadata, pw)


async def _discover_replay(
    board_url: str,
    config: dict,
    pw,
) -> list[DiscoveredJob] | set[str]:
    """Replay a stored API call, optionally paginating."""
    from src.shared.api_sniff import JobListResult, ArrayCandidate, Exchange, PaginationInfo
    from src.shared.browser import navigate, open_page

    api_url = config["api_url"]
    method = config.get("method", "GET")
    json_path = config.get("json_path", "$")
    url_field = config.get("url_field")
    url_template = config.get("url_template")
    post_data = config.get("post_data")
    request_headers = config.get("request_headers", {})
    fields_map: dict[str, str] = config.get("fields") or {}
    pagination_config = config.get("pagination")

    async with open_page(pw, {}) as page:
        # Navigate to board_url to establish cookies/auth context
        try:
            await navigate(page, board_url, {"wait": "networkidle", "timeout": 45_000})
        except Exception:
            log.warning("api_sniffer.navigation_failed", board_url=board_url, exc_info=True)

        await asyncio.sleep(1)

        # Replay the API call
        headers = clean_headers(request_headers)
        try:
            data = await fetch_json(page, method, api_url, headers, post_data)
        except Exception:
            log.error("api_sniffer.replay_failed", api_url=api_url, exc_info=True)
            return list() if fields_map else set()

        items = extract_items(data, json_path)
        if not items:
            log.warning("api_sniffer.no_items", api_url=api_url, json_path=json_path)
            return list() if fields_map else set()

        # Paginate if configured
        if pagination_config and len(items) > 0:
            pag = PaginationInfo(
                param_name=pagination_config["param_name"],
                style=pagination_config["style"],
                start_value=pagination_config["start_value"],
                increment=pagination_config["increment"],
                location=pagination_config["location"],
            )
            ex = Exchange(
                method=method, url=api_url,
                request_headers=request_headers,
                post_data=post_data, status=200,
                body=data, content_type="application/json",
                phase="load",
            )
            from src.shared.api_sniff import find_total_count
            total_count = find_total_count(data, json_path)
            cand = ArrayCandidate(exchange=ex, json_path=json_path, items=items)
            job_result = JobListResult(
                candidate=cand, url_field=url_field,
                total_count=total_count, pagination=pag,
            )
            items = await paginate_all(page, job_result, MAX_PAGES)

        # Cap
        if len(items) > MAX_ITEMS:
            log.warning("api_sniffer.truncated", total=len(items), cap=MAX_ITEMS)
            items = items[:MAX_ITEMS]

        # Build URL map via DOM cross-ref if no url_field and no url_template
        url_map: dict[str, str] | None = None
        if not url_field and not url_template:
            from src.shared.api_sniff import ID_FIELDS as _ID_FIELDS

            dom_urls = await extract_urls_via_dom_crossref(page, items, board_url)
            if dom_urls:
                # Build id → url map
                id_f = None
                for key in items[0]:
                    if _ID_FIELDS.match(key):
                        id_f = key
                        break
                if id_f:
                    url_map = {}
                    for item, u in zip(items, dom_urls):
                        url_map[str(item.get(id_f, ""))] = u

        if fields_map:
            return _extract_rich(items, fields_map, url_field, url_template, board_url, url_map=url_map)

        # URL-only mode
        if url_template:
            return _extract_urls_from_template(items, url_template, board_url)
        urls = extract_urls(items, url_field, board_url)
        if not urls and url_map:
            return set(url_map.values())
        if not urls:
            urls = await extract_urls_via_dom_crossref(page, items, board_url)
        return set(urls)


async def _discover_auto(
    board_url: str,
    config: dict,
    pw,
) -> list[DiscoveredJob] | set[str]:
    """Full auto-discover: capture exchanges, detect, paginate."""
    from src.shared.browser import dismiss_overlays, navigate, open_page

    fields_map: dict[str, str] = config.get("fields") or {}

    async with open_page(pw, {}) as page:
        page_host = urlparse(board_url).netloc
        exchanges = await capture_exchanges(page, page_host)

        try:
            await navigate(page, board_url, {"wait": "networkidle", "timeout": 45_000})
        except Exception:
            log.warning("api_sniffer.navigation_failed", board_url=board_url, exc_info=True)

        await asyncio.sleep(3)
        await dismiss_overlays(page)
        await trigger_interactions(page, exchanges)

        result = detect_job_list(exchanges, board_url)
        if result is None:
            log.warning("api_sniffer.no_api_detected", board_url=board_url)
            return list() if fields_map else set()

        page_size = len(result.candidate.items)
        result.pagination = infer_pagination(
            exchanges, result.candidate.exchange.url, page_size,
        )

        items = await paginate_all(page, result, MAX_PAGES)

        if len(items) > MAX_ITEMS:
            items = items[:MAX_ITEMS]

        # Auto-map fields if not configured
        if not fields_map:
            fields_map = auto_map_fields(items)

        url_field = result.url_field

        # Build URL map via DOM cross-ref if no url_field
        url_map: dict[str, str] | None = None
        if not url_field and items:
            from src.shared.api_sniff import ID_FIELDS as _ID_FIELDS

            dom_urls = await extract_urls_via_dom_crossref(page, items, board_url)
            if dom_urls:
                id_f = None
                for key in items[0]:
                    if _ID_FIELDS.match(key):
                        id_f = key
                        break
                if id_f:
                    url_map = {}
                    for item, u in zip(items, dom_urls):
                        url_map[str(item.get(id_f, ""))] = u

        if fields_map:
            return _extract_rich(items, fields_map, url_field, None, board_url, url_map=url_map)

        urls = extract_urls(items, url_field, board_url)
        if not urls and url_map:
            return set(url_map.values())
        if not urls:
            urls = await extract_urls_via_dom_crossref(page, items, board_url)
        return set(urls)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_rich(
    items: list[dict],
    fields_map: dict[str, str],
    url_field: str | None,
    url_template: str | None,
    board_url: str,
    url_map: dict[str, str] | None = None,
) -> list[DiscoveredJob]:
    """Extract DiscoveredJob objects from items using field mapping.

    *url_map* is an optional pre-built mapping from item ID to URL
    (e.g. from DOM cross-reference).
    """
    from urllib.parse import urljoin

    # Build id_field lookup for url_map
    id_field = None
    if url_map and items:
        from src.shared.api_sniff import ID_FIELDS as _ID_FIELDS
        for key in items[0]:
            if _ID_FIELDS.match(key):
                id_field = key
                break

    jobs: list[DiscoveredJob] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        # Build URL
        url = None
        if url_template:
            try:
                # Use a safe dict that returns empty string for missing keys
                safe = {k: v for k, v in item.items() if isinstance(v, (str, int, float))}
                url = url_template.format_map(safe)
            except (KeyError, IndexError, ValueError):
                pass
        if not url and url_map and id_field:
            item_id = str(item.get(id_field, ""))
            url = url_map.get(item_id)
        if not url and url_field:
            raw = item.get(url_field)
            if isinstance(raw, str) and raw:
                url = urljoin(board_url, raw)
        if not url:
            # Try to find any URL in the item
            for val in item.values():
                if isinstance(val, str) and val.startswith(("http://", "https://")):
                    url = val
                    break
        if not url:
            continue

        kwargs: dict[str, object] = {"url": url}
        metadata_fields: dict[str, object] = {}

        for target, spec in fields_map.items():
            value = extract_field(item, spec)
            if value is None:
                continue
            if target.startswith("metadata."):
                metadata_fields[target.removeprefix("metadata.")] = value
            elif target in (
                "title", "description", "employment_type",
                "job_location_type", "date_posted",
            ):
                kwargs[target] = value
            elif target == "locations":
                kwargs["locations"] = value if isinstance(value, list) else [value]
            elif target in ("skills", "responsibilities", "qualifications"):
                kwargs[target] = value if isinstance(value, list) else [value]
            elif target == "base_salary":
                # Attempt to parse as dict if it's a string
                if isinstance(value, str):
                    try:
                        kwargs["base_salary"] = json.loads(value)
                    except (json.JSONDecodeError, ValueError):
                        metadata_fields[target] = value
                elif isinstance(value, dict):
                    kwargs["base_salary"] = value
                else:
                    metadata_fields[target] = value
            else:
                metadata_fields[target] = value

        if metadata_fields:
            kwargs["metadata"] = metadata_fields

        jobs.append(DiscoveredJob(**kwargs))

    return jobs


def _extract_urls_from_template(
    items: list[dict],
    url_template: str,
    board_url: str,
) -> set[str]:
    """Build URL-only set from items using a URL template."""
    from urllib.parse import urljoin

    urls: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            url = url_template.format_map(item)
            urls.add(urljoin(board_url, url))
        except (KeyError, IndexError, ValueError):
            continue
    return urls


register("api_sniffer", discover, cost=80, can_handle=can_handle)
