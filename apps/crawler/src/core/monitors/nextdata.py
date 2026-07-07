"""Embedded JSON monitor (Next.js, React Router, etc.).

Extracts job listings from embedded JSON blobs in server-rendered pages.
Configurable via the ``source`` key:

- ``"nextdata"`` (default) — ``<script id="__NEXT_DATA__">``
- ``"reactrouter"`` — ``window.__staticRouterHydrationData``

Supports two modes:
- **Rich mode** (``fields`` configured): returns ``list[DiscoveredJob]``
- **URL-only mode** (no ``fields``): returns ``set[str]``

Pagination
----------
When the ``pagination`` config key is present, the monitor fetches multiple
pages and merges the results.  Config shape::

    "pagination": {
        "path": "props.pageProps.data.pagination",  # jmespath to pagination object
        "page_count": "pageCount",                  # field within that object
        "page_param": "page"                        # query-string parameter (default "page")
    }

Alternative pagination using total_records + page_size (computes page_count)::

    "pagination": {
        "path": "loaderData.search",
        "total_records": "totalRecords",
        "page_size": 20,
        "page_param": "page"
    }

Offset mode (Phenom Canvas-style ``?from=25&from=50...``)::

    "pagination": {
        "mode": "offset",
        "path": "eagerLoadRefineSearch",
        "total_records": "totalHits",
        "page_size": 25,
        "offset_param": "from"
    }
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.shared.browser import NAVIGATE_KEYS
from src.shared.nextdata import (
    extract_embedded_json,
    extract_field,
    extract_next_data,
    extract_phenom_canvas_data,
    extract_react_router_data,
    extract_rsc_data,
    resolve_path,
)
from src.shared.slug import slugify

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_URLS = 50_000
_MAX_CONCURRENT_PAGES = 5

# Common paths where Next.js apps store job listings.
_COMMON_PATHS = [
    "props.pageProps.positions",
    "props.pageProps.jobs",
    "props.pageProps.openings",
    "props.pageProps.allJobs",
    "props.pageProps.data.positions",
    "props.pageProps.data.jobs",
    "props.pageProps.initialState.jobs.items",
]

# Common paths where React Router apps store job listings.
_REACT_ROUTER_PATHS = [
    "loaderData.search.searchResults",
    "loaderData.root.jobs",
    "loaderData.routes.jobs",
]

# Common paths where RSC flight payloads store job listings.
# RSC data dicts are extracted flat (no props.pageProps wrapper).
_RSC_PATHS = [
    "positions",
    "jobs",
    "openings",
    "allJobs",
    "data.positions",
    "data.jobs",
]

# Path to jobs array in a Phenom Canvas ``phApp.ddo`` blob.
_PHENOM_CANVAS_PATHS = [
    "eagerLoadRefineSearch.data.jobs",
]


def _build_url(
    item: dict,
    url_template: str,
    slug_fields: list[str] | None,
) -> str | None:
    """Build a job URL from *item* fields and *url_template*.

    Template variables come from the raw item values.  The special
    ``{slug}`` variable is built by slugifying + joining the values of
    *slug_fields*.
    """
    variables: dict[str, object] = {}
    for key, value in item.items():
        if isinstance(value, (str, int, float)):
            variables[key] = value

    if slug_fields:
        parts = []
        for field in slug_fields:
            val = item.get(field)
            if val is not None:
                parts.append(slugify(str(val)))
        if parts:
            variables["slug"] = "-".join(parts)

    try:
        return url_template.format_map(variables)
    except (KeyError, IndexError, ValueError):
        return None


def _add_query_param(url: str, param: str, value: int) -> str:
    """Add or replace a query parameter in a URL."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params[param] = [str(value)]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _pagination_mode(cfg: dict) -> str:
    """Return "page" (default) or "offset"."""
    return cfg.get("mode", "page")


def _compute_page_urls(board_url: str, page_count: int, cfg: dict) -> list[str]:
    """Return URLs for pages 2..page_count under the current pagination config.

    Page mode uses ``?page=N`` with N in [2..page_count]. Offset mode uses
    ``?from=page_size*N`` for N in [1..page_count-1] (page 1 served by
    ``board_url`` itself).
    """
    if _pagination_mode(cfg) == "offset":
        param = cfg.get("offset_param", "from")
        page_size = int(cfg.get("page_size") or 0)
        return [_add_query_param(board_url, param, page_size * n) for n in range(1, page_count)]
    page_param = cfg.get("page_param", "page")
    return [_add_query_param(board_url, page_param, p) for p in range(2, page_count + 1)]


def _resolve_field(item: dict, spec: str | dict) -> str | list[str] | None:
    """Extract a field value, optionally applying a value map.

    *spec* is either a jmespath string or a dict ``{"path": "...", "map": {...}}``.
    Delegates to :func:`extract_field` which handles all spec types
    (string, list, dict with path+map).
    """
    return extract_field(item, spec)


def _extract_salary(item: dict, cfg: dict) -> dict | None:
    """Build a ``base_salary`` dict from per-item fields.

    Config shape::

        {
            "min": "salaryAmountFrom.amount",
            "max": "salaryAmountTo.amount",
            "currency": "salaryAmountFrom.currency",
            "unit": "salaryFrequency",
            "divisor": 100,
            "unit_map": {"PER_YEAR": "year", ...}
        }
    """
    divisor = cfg.get("divisor", 1)
    unit_map = cfg.get("unit_map", {})
    salary: dict = {}

    for key in ("min", "max", "currency", "unit"):
        path = cfg.get(key)
        if not path:
            continue
        raw = resolve_path(item, path)
        if raw is None:
            continue

        if key in ("min", "max"):
            try:
                val = float(raw) / divisor
                salary[key] = int(val) if val == int(val) else val
            except (ValueError, TypeError):
                continue
        elif key == "unit":
            salary[key] = unit_map.get(str(raw), str(raw))
        else:
            salary[key] = str(raw)

    # Require at least one of min/max to be meaningful
    if not salary or ("min" not in salary and "max" not in salary):
        return None
    return salary


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


def _find_jobs_path(data: dict, paths: list[str] | None = None) -> tuple[str, int] | None:
    """Search common paths for a plausible jobs array. Returns (path, count) or None."""
    for path in paths or _COMMON_PATHS:
        arr = resolve_path(data, path)
        if (
            isinstance(arr, list)
            and len(arr) >= 5
            and all(isinstance(item, dict) for item in arr[:5])
        ):
            return path, len(arr)
    return None


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Detect whether *url* has embedded JSON with a plausible jobs array.

    Checks for Next.js ``__NEXT_DATA__`` and React Router
    ``__staticRouterHydrationData``.  Tries static HTTP first, then falls
    back to Playwright if neither is found.

    When *pw* is provided, the Playwright fallback reuses that instance.
    """
    html = await fetch_page_text(url, client)
    if html:
        # Try __NEXT_DATA__ first
        data = extract_next_data(html)
        if data:
            result = _find_jobs_path(data)
            if result:
                path, count = result
                log.info("nextdata.detected", url=url, path=path, count=count)
                return {"path": path, "count": count}

        # Try React Router hydration data
        data = extract_react_router_data(html)
        if data:
            result = _find_jobs_path(data, _REACT_ROUTER_PATHS)
            if result:
                path, count = result
                log.info("nextdata.detected", url=url, source="reactrouter", path=path, count=count)
                return {"source": "reactrouter", "path": path, "count": count}

        # Try RSC flight payload (Next.js App Router)
        data = extract_rsc_data(html)
        if data:
            result = _find_jobs_path(data, _RSC_PATHS)
            if result:
                path, count = result
                log.info("nextdata.detected", url=url, source="rsc", path=path, count=count)
                return {"source": "rsc", "path": path, "count": count}

        # Try Phenom Canvas (phApp.ddo = {...})
        data = extract_phenom_canvas_data(html)
        if data:
            result = _find_jobs_path(data, _PHENOM_CANVAS_PATHS)
            if result:
                path, count = result
                meta = _phenom_canvas_meta(data, path, count)
                log.info(
                    "nextdata.detected", url=url, path=path, count=count, source="phenom_canvas"
                )
                return meta

    # Fall back to Playwright (client-rendered)
    try:
        from src.shared.browser import render as browser_render

        rendered_html = await browser_render(url, pw=pw)
        for source, extractor, paths in [
            ("nextdata", extract_next_data, _COMMON_PATHS),
            ("reactrouter", extract_react_router_data, _REACT_ROUTER_PATHS),
            ("rsc", extract_rsc_data, _RSC_PATHS),
            ("phenom_canvas", extract_phenom_canvas_data, _PHENOM_CANVAS_PATHS),
        ]:
            data = extractor(rendered_html)
            if data:
                result = _find_jobs_path(data, paths)
                if result:
                    path, count = result
                    log.info(
                        "nextdata.detected",
                        url=url,
                        source=source,
                        path=path,
                        count=count,
                        render=True,
                    )
                    if source == "phenom_canvas":
                        meta = _phenom_canvas_meta(data, path, count)
                        meta["render"] = True
                        return meta
                    meta = {"path": path, "count": count, "render": True}
                    if source != "nextdata":
                        meta["source"] = source
                    return meta
    except Exception:
        log.debug("nextdata.render_fallback_failed", url=url, exc_info=True)

    return None


def _phenom_canvas_meta(data: dict, path: str, count: int) -> dict:
    """Build auto-detection metadata for a Phenom Canvas page.

    Includes the pagination config so ``ws probe`` surfaces a ready-to-run
    monitor_config (Canvas uses ``?from=N`` offset pagination, where N is
    computed from ``eagerLoadRefineSearch.totalHits`` and the server-
    configured page size).
    """
    eager = resolve_path(data, "eagerLoadRefineSearch") or {}
    total = eager.get("totalHits")
    page_size = eager.get("hits") or count
    meta: dict = {
        "source": "phenom_canvas",
        "path": path,
        "count": count,
    }
    if isinstance(total, int) and isinstance(page_size, int) and page_size > 0:
        meta["pagination"] = {
            "mode": "offset",
            "path": "eagerLoadRefineSearch",
            "total_records": "totalHits",
            "page_size": page_size,
            "offset_param": "from",
        }
        meta["total"] = total
    return meta


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | set[str]:
    """Discover jobs from embedded JSON on a career page."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    path = metadata.get("path")
    if not path:
        log.error("nextdata.missing_path", board_url=board_url)
        return set()

    url_template = metadata.get("url_template")
    if not url_template:
        log.error("nextdata.missing_url_template", board_url=board_url)
        return set()

    source: str = metadata.get("source", "nextdata")
    fields_map: dict[str, str | dict] = metadata.get("fields") or {}
    slug_fields: list[str] | None = metadata.get("slug_fields")
    render = metadata.get("render", False)
    actions = metadata.get("actions")
    pagination_cfg: dict | None = metadata.get("pagination")
    base_salary_cfg: dict | None = metadata.get("base_salary")

    if not render and actions:
        log.warning(
            "nextdata.misconfiguration",
            board_url=board_url,
            detail="actions require render=true; overriding render to true",
        )
        render = True

    browser_config = {k: v for k, v in metadata.items() if k in NAVIGATE_KEYS}

    # Fetch the page
    html = await _fetch_html(
        board_url,
        render,
        client,
        pw=pw,
        browser_config=browser_config,
    )
    if not html:
        log.warning("nextdata.fetch_failed", board_url=board_url)
        return list() if fields_map else set()

    # Extract embedded JSON (source-aware)
    data = extract_embedded_json(html, source)
    if not data:
        log.warning("nextdata.no_data", board_url=board_url, source=source)
        return list() if fields_map else set()

    # Walk path to jobs array
    items = resolve_path(data, path)
    if not isinstance(items, list):
        log.warning("nextdata.path_not_list", board_url=board_url, path=path)
        return list() if fields_map else set()

    # Pagination: fetch remaining pages and merge
    if pagination_cfg:
        items = await _fetch_remaining_pages(
            items,
            data,
            board_url,
            render,
            client,
            path,
            pagination_cfg,
            source=source,
            pw=pw,
            browser_config=browser_config,
        )

    # Cap items
    if len(items) > MAX_URLS:
        log.warning("nextdata.truncated", total=len(items), cap=MAX_URLS)
        items = items[:MAX_URLS]

    if fields_map:
        return _extract_rich(items, url_template, slug_fields, fields_map, base_salary_cfg)
    return _extract_urls(items, url_template, slug_fields)


# How many pages to fetch per streaming batch before yielding.
_STREAM_BATCH_PAGES = 10


async def discover_stream(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
):
    """Yield job batches so the caller can pulse heartbeats on large boards.

    Non-paginated boards yield a single batch.  Paginated boards yield the
    first page immediately, then groups of ``_STREAM_BATCH_PAGES`` pages.
    """
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    path = metadata.get("path")
    url_template = metadata.get("url_template")
    if not path or not url_template:
        return

    source: str = metadata.get("source", "nextdata")
    fields_map: dict[str, str | dict] = metadata.get("fields") or {}
    slug_fields: list[str] | None = metadata.get("slug_fields")
    render = metadata.get("render", False)
    actions = metadata.get("actions")
    pagination_cfg: dict | None = metadata.get("pagination")
    base_salary_cfg: dict | None = metadata.get("base_salary")

    if not render and actions:
        render = True

    browser_config = {k: v for k, v in metadata.items() if k in NAVIGATE_KEYS}

    html = await _fetch_html(
        board_url,
        render,
        client,
        pw=pw,
        browser_config=browser_config,
    )
    if not html:
        return

    data = extract_embedded_json(html, source)
    if not data:
        return

    items = resolve_path(data, path)
    if not isinstance(items, list):
        return

    # No pagination — single yield
    if not pagination_cfg:
        if fields_map:
            yield _extract_rich(items, url_template, slug_fields, fields_map, base_salary_cfg)
        else:
            yield _extract_urls(items, url_template, slug_fields)
        return

    # Determine page count
    page_count = _resolve_page_count(data, pagination_cfg)
    if page_count is None or page_count <= 1:
        if fields_map:
            yield _extract_rich(items, url_template, slug_fields, fields_map, base_salary_cfg)
        else:
            yield _extract_urls(items, url_template, slug_fields)
        return

    # Yield first page immediately
    if fields_map:
        yield _extract_rich(items, url_template, slug_fields, fields_map, base_salary_cfg)
    else:
        yield _extract_urls(items, url_template, slug_fields)

    page_urls = _compute_page_urls(board_url, page_count, pagination_cfg)
    sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

    async def _fetch_page(page_url: str) -> list:
        async with sem:
            page_html = await _fetch_html(
                page_url,
                render,
                client,
                pw=pw,
                browser_config=browser_config,
            )
            if not page_html:
                return []
            page_data = extract_embedded_json(page_html, source)
            if not page_data:
                return []
            page_items = resolve_path(page_data, path)
            return page_items if isinstance(page_items, list) else []

    # Fetch remaining pages in batches of _STREAM_BATCH_PAGES
    for i in range(0, len(page_urls), _STREAM_BATCH_PAGES):
        chunk = page_urls[i : i + _STREAM_BATCH_PAGES]
        results = await asyncio.gather(*[_fetch_page(u) for u in chunk])
        batch_items: list = []
        for page_items in results:
            batch_items.extend(page_items)
        if batch_items:
            if fields_map:
                yield _extract_rich(
                    batch_items, url_template, slug_fields, fields_map, base_salary_cfg
                )
            else:
                yield _extract_urls(batch_items, url_template, slug_fields)


def _resolve_page_count(data: dict, pagination_cfg: dict) -> int | None:
    """Extract page count from first-page data."""
    pagination_path = pagination_cfg.get("path")
    page_count_field = pagination_cfg.get("page_count")
    total_records_field = pagination_cfg.get("total_records")
    page_size = pagination_cfg.get("page_size")

    if not pagination_path:
        return None
    if not page_count_field and not (total_records_field and page_size):
        return None

    pagination_data = resolve_path(data, pagination_path)
    if not isinstance(pagination_data, dict):
        return None

    if page_count_field:
        raw_count = resolve_path(pagination_data, page_count_field)
        if raw_count is None:
            return None
        try:
            return int(raw_count)
        except (ValueError, TypeError):
            return None
    else:
        raw_total = resolve_path(pagination_data, total_records_field)
        if raw_total is None:
            return None
        try:
            import math

            return math.ceil(int(raw_total) / int(page_size))
        except (ValueError, TypeError):
            return None


async def _fetch_html(
    url: str,
    render: bool,
    client: httpx.AsyncClient,
    pw=None,
    browser_config: dict | None = None,
) -> str | None:
    """Fetch page HTML via httpx or Playwright.

    ``browser_config`` is a full projection of browser-recognised keys (use
    ``BROWSER_KEYS`` at the call site) so ``wait`` / ``wait_fallback`` /
    ``timeout`` / ``actions`` etc. all reach ``navigate()``.
    """
    if render:
        try:
            from src.shared.browser import render as browser_render

            return await browser_render(url, config=browser_config or {}, pw=pw)
        except Exception:
            log.warning("nextdata.render_failed", url=url, exc_info=True)
            return None
    return await fetch_page_text(url, client)


async def _fetch_remaining_pages(
    first_page_items: list,
    data: dict,
    board_url: str,
    render: bool,
    client: httpx.AsyncClient,
    path: str,
    pagination_cfg: dict,
    source: str = "nextdata",
    pw=None,
    browser_config: dict | None = None,
) -> list:
    """Fetch pages 2..N and merge items with the first page."""
    page_count = _resolve_page_count(data, pagination_cfg)
    if page_count is None or page_count <= 1:
        return first_page_items

    page_urls = _compute_page_urls(board_url, page_count, pagination_cfg)
    if not page_urls:
        return first_page_items

    log.info(
        "nextdata.paginating",
        board_url=board_url,
        page_count=page_count,
        first_page_items=len(first_page_items),
        mode=_pagination_mode(pagination_cfg),
    )

    sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

    async def _fetch_page(page_url: str) -> list:
        async with sem:
            html = await _fetch_html(
                page_url,
                render,
                client,
                pw=pw,
                browser_config=browser_config,
            )
            if not html:
                log.warning("nextdata.page_fetch_failed", url=page_url)
                return []
            page_data = extract_embedded_json(html, source)
            if not page_data:
                return []
            items = resolve_path(page_data, path)
            return items if isinstance(items, list) else []

    tasks = [_fetch_page(u) for u in page_urls]
    results = await asyncio.gather(*tasks)

    all_items = list(first_page_items)
    for page_items in results:
        all_items.extend(page_items)

    return all_items


def _extract_rich(
    items: list[dict],
    url_template: str,
    slug_fields: list[str] | None,
    fields_map: dict[str, str | dict],
    base_salary_cfg: dict | None = None,
) -> list[DiscoveredJob]:
    """Extract ``DiscoveredJob`` objects using the field mapping."""
    jobs: list[DiscoveredJob] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = _build_url(item, url_template, slug_fields)
        if not url:
            continue

        kwargs: dict[str, object] = {"url": url}
        metadata_fields: dict[str, object] = {}

        for target, spec in fields_map.items():
            value = _resolve_field(item, spec)
            if value is None:
                continue
            if target.startswith("metadata."):
                metadata_fields[target.removeprefix("metadata.")] = value
            elif target in (
                "title",
                "description",
                "employment_type",
                "job_location_type",
                "date_posted",
            ):
                kwargs[target] = value
            elif target == "locations":
                kwargs["locations"] = value if isinstance(value, list) else [value]
            else:
                metadata_fields[target] = value

        if base_salary_cfg:
            salary = _extract_salary(item, base_salary_cfg)
            if salary:
                kwargs["base_salary"] = salary

        if metadata_fields:
            kwargs["metadata"] = metadata_fields

        jobs.append(DiscoveredJob(**kwargs))

    return jobs


def _extract_urls(
    items: list[dict],
    url_template: str,
    slug_fields: list[str] | None,
) -> set[str]:
    """Build URL-only set from items."""
    urls: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = _build_url(item, url_template, slug_fields)
        if url:
            urls.add(url)
    return urls


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get(board_url, follow_redirects=True)
    if resp.status_code != 200:
        return
    data = extract_next_data(resp.text)
    if data:
        (artifact_dir / "nextdata.json").write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )


register(
    "nextdata",
    discover,
    cost=20,
    can_handle=can_handle,
    stream=discover_stream,
    save_raw=save_raw,
)
