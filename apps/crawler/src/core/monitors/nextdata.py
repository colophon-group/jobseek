"""Next.js __NEXT_DATA__ monitor.

Extracts job listings from the ``<script id="__NEXT_DATA__">`` JSON blob
that Next.js embeds in every server-rendered page.  Config maps the
app-specific JSON structure to ``DiscoveredJob`` fields.

Supports two modes:
- **Rich mode** (``fields`` configured): returns ``list[DiscoveredJob]``
- **URL-only mode** (no ``fields``): returns ``set[str]``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.shared.nextdata import extract_field, extract_next_data, resolve_path
from src.shared.slug import slugify

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_URLS = 10_000

# Common paths where Next.js apps store job listings.
_COMMON_PATHS = [
    "props.pageProps.positions",
    "props.pageProps.jobs",
    "props.pageProps.openings",
    "props.pageProps.allJobs",
    "props.pageProps.data.positions",
    "props.pageProps.data.jobs",
]

# Backward-compatible aliases for test imports
_resolve_path = resolve_path
_extract_field = extract_field
_extract_next_data = extract_next_data


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


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


def _find_jobs_path(data: dict) -> tuple[str, int] | None:
    """Search common paths for a plausible jobs array. Returns (path, count) or None."""
    for path in _COMMON_PATHS:
        arr = resolve_path(data, path)
        if isinstance(arr, list) and len(arr) >= 5:
            if all(isinstance(item, dict) for item in arr[:5]):
                return path, len(arr)
    return None


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Detect whether *url* is a Next.js page with a plausible jobs array.

    Tries static HTTP first, then falls back to Playwright if ``__NEXT_DATA__``
    is not found (some Next.js sites render it client-side).  When the Playwright
    fallback succeeds, ``render: true`` is included in the returned metadata so
    the suggested config uses browser rendering.

    When *pw* is provided, the Playwright fallback reuses that instance.
    """
    # Try static HTTP first
    html = await fetch_page_text(url, client)
    if html:
        data = extract_next_data(html)
        if data:
            result = _find_jobs_path(data)
            if result:
                path, count = result
                log.info("nextdata.detected", url=url, path=path, count=count)
                return {"path": path, "count": count}

    # Fall back to Playwright (client-rendered __NEXT_DATA__)
    try:
        from src.shared.browser import render as browser_render

        rendered_html = await browser_render(url, pw=pw)
        data = extract_next_data(rendered_html)
        if data:
            result = _find_jobs_path(data)
            if result:
                path, count = result
                log.info("nextdata.detected", url=url, path=path, count=count, render=True)
                return {"path": path, "count": count, "render": True}
    except Exception:
        log.debug("nextdata.render_fallback_failed", url=url, exc_info=True)

    return None


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | set[str]:
    """Discover jobs from ``__NEXT_DATA__`` on a Next.js career page."""
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

    fields_map: dict[str, str] = metadata.get("fields") or {}
    slug_fields: list[str] | None = metadata.get("slug_fields")
    render = metadata.get("render", False)
    actions = metadata.get("actions")

    if not render and actions:
        log.warning(
            "nextdata.misconfiguration",
            board_url=board_url,
            detail="actions require render=true; overriding render to true",
        )
        render = True

    # Fetch the page
    html = await _fetch_html(board_url, render, client, pw=pw, actions=actions)
    if not html:
        log.warning("nextdata.fetch_failed", board_url=board_url)
        return list() if fields_map else set()

    # Extract __NEXT_DATA__
    data = extract_next_data(html)
    if not data:
        log.warning("nextdata.no_next_data", board_url=board_url)
        return list() if fields_map else set()

    # Walk path to jobs array
    items = resolve_path(data, path)
    if not isinstance(items, list):
        log.warning("nextdata.path_not_list", board_url=board_url, path=path)
        return list() if fields_map else set()

    # Cap items
    if len(items) > MAX_URLS:
        log.warning("nextdata.truncated", total=len(items), cap=MAX_URLS)
        items = items[:MAX_URLS]

    if fields_map:
        return _extract_rich(items, url_template, slug_fields, fields_map)
    return _extract_urls(items, url_template, slug_fields)


async def _fetch_html(
    url: str,
    render: bool,
    client: httpx.AsyncClient,
    pw=None,
    actions: list[dict] | None = None,
) -> str | None:
    """Fetch page HTML via httpx or Playwright."""
    if render:
        try:
            from src.shared.browser import render as browser_render

            browser_config: dict = {}
            if actions:
                browser_config["actions"] = actions
            return await browser_render(url, config=browser_config, pw=pw)
        except Exception:
            log.warning("nextdata.render_failed", url=url, exc_info=True)
            return None
    return await fetch_page_text(url, client)


def _extract_rich(
    items: list[dict],
    url_template: str,
    slug_fields: list[str] | None,
    fields_map: dict[str, str],
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
            value = extract_field(item, spec)
            if value is None:
                continue
            if target.startswith("metadata."):
                metadata_fields[target.removeprefix("metadata.")] = value
            elif target in ("title", "description", "employment_type", "job_location_type", "date_posted"):
                kwargs[target] = value
            elif target == "locations":
                kwargs["locations"] = value if isinstance(value, list) else [value]
            else:
                metadata_fields[target] = value

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


register("nextdata", discover, cost=20, can_handle=can_handle)
