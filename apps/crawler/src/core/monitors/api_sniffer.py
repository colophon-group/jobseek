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
import contextlib
import json
import re
from math import ceil
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.api_sniff import (
    JOB_KEYWORDS,
    TITLE_FIELDS,
    auto_map_fields,
    capture_exchanges,
    clean_headers,
    detect_cms,
    detect_job_list,
    extract_items,
    extract_urls,
    extract_urls_via_dom_crossref,
    find_arrays,
    find_total_count,
    find_url_field,
    infer_pagination,
    make_browser_fetcher,
    make_http_fetcher,
    paginate_all,
    scan_page_scripts,
    set_body_param,
    set_url_param,
    trigger_interactions,
)
from src.shared.nextdata import extract_field, resolve_path

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_ITEMS = 10_000
MAX_PAGES = 50
_HTTP_MAX_PAGES = 200  # higher limit for plain httpx (no Playwright overhead)

# Defaults for Playwright navigation — configurable via monitor_config
_DEFAULT_WAIT = "load"
_DEFAULT_TIMEOUT = 20_000
_DEFAULT_SETTLE = 3  # seconds to wait after navigation for XHRs to complete


def _derive_url_match(api_url: str) -> str | None:
    """Derive an ``api_url_match`` glob from a URL with rotating-token segments.

    Replaces path segments that look like rotating tokens (contain mixed
    alphanumeric chars + separators, e.g. ``apigw-x0cceuow60``) with ``*``.
    Returns ``None`` if no token-like segment is found.

    Called during ``can_handle`` (probe) so the pattern is stored in config.
    """
    parsed = urlparse(api_url)
    segments = parsed.path.strip("/").split("/")
    has_token = False
    pattern_segments = []
    for seg in segments:
        is_versioned = bool(re.match(r"^v\d+$", seg, re.I))
        has_mixed = bool(re.search(r"[a-z]", seg, re.I)) and bool(re.search(r"\d", seg))
        has_separator = bool(re.search(r"[-_]", seg)) and len(seg) > 8
        if not is_versioned and (has_mixed and has_separator):
            pattern_segments.append("*")
            has_token = True
        else:
            pattern_segments.append(seg)
    if not has_token:
        return None
    return f"{parsed.netloc}/{'/'.join(pattern_segments)}"


def _merge_params(url: str, params: dict) -> str:
    """Merge extra query params into a URL."""
    parsed = urlparse(url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    existing.update({k: [v] if isinstance(v, str) else v for k, v in params.items()})
    new_query = urlencode(existing, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


async def can_handle(
    url: str,
    client: httpx.AsyncClient,
    pw=None,
    diagnostics: dict | None = None,
) -> dict | None:
    """Detect whether *url* loads job data via XHR/fetch APIs.

    Returns a metadata dict suitable for use as monitor_config, or None
    if no job-list API is detected.  Requires Playwright (*pw*).

    When *diagnostics* is provided, it is populated with exchange summaries,
    script URL discoveries, and CMS detection results — even when detection
    fails.  This allows callers to show diagnostic output to the user.
    """
    if pw is None:
        return None

    from src.shared.browser import dismiss_overlays, navigate, open_page

    try:
        async with open_page(pw, {}) as page:
            page_host = urlparse(url).netloc
            exchanges = await capture_exchanges(page, page_host)

            await navigate(page, url, {"wait": _DEFAULT_WAIT, "timeout": _DEFAULT_TIMEOUT})
            await asyncio.sleep(_DEFAULT_SETTLE)

            await dismiss_overlays(page)
            await trigger_interactions(page, exchanges)

            # Scan page scripts and detect CMS while page is still open
            if diagnostics is not None:
                try:
                    diagnostics["script_urls"] = await scan_page_scripts(page)
                except Exception:
                    log.debug("api_sniffer.scan_scripts_failed", exc_info=True)
                    diagnostics["script_urls"] = []

                try:
                    diagnostics["cms"] = await detect_cms(page)
                except Exception:
                    log.debug("api_sniffer.detect_cms_failed", exc_info=True)
                    diagnostics["cms"] = None

            result = detect_job_list(exchanges, url)
            if result is None:
                # Populate exchange diagnostics even on failure
                if diagnostics is not None:
                    diagnostics["exchanges"] = [
                        {
                            "method": ex.method,
                            "url": ex.url[:120],
                            "status": ex.status,
                            "phase": ex.phase,
                            "arrays": len(find_arrays(ex.body) if ex.body else []),
                            "best_items": max(
                                (
                                    len(items)
                                    for _, items in (find_arrays(ex.body) if ex.body else [])
                                ),
                                default=0,
                            ),
                        }
                        for ex in exchanges
                    ]
                return None

            ex = result.candidate.exchange
            page_size = len(result.candidate.items)

            # Infer pagination if two matching exchanges exist
            result.pagination = infer_pagination(exchanges, ex.url, page_size)

            # Auto-map fields
            fields = auto_map_fields(result.candidate.items)

            # Split captured URL into clean base + params
            parsed_url = urlparse(ex.url)
            raw_params = parse_qs(parsed_url.query, keep_blank_values=True)

            # Params managed by pagination config — don't duplicate
            pag_params = set()
            if result.pagination:
                pag_params.add(result.pagination.param_name)

            # Separate meaningful params from the URL
            clean_params: dict[str, str | list[str]] = {}
            for k, vals in raw_params.items():
                if k in pag_params:
                    continue
                # Drop empty-valued params
                non_empty = [v for v in vals if v]
                if not non_empty:
                    continue
                clean_params[k] = non_empty[0] if len(non_empty) == 1 else non_empty

            base_url = urlunparse(parsed_url._replace(query=""))

            # Derive api_url_match for URLs with token-like path segments.
            # Stored in config so that _discover_live_url can re-capture the
            # URL at runtime if the token rotates.
            api_url_match = _derive_url_match(base_url)

            # Build metadata
            meta: dict = {
                "api_url": base_url,
                "method": ex.method,
                "json_path": result.candidate.json_path,
                "items": page_size,
                "score": result.candidate.score,
                "browser": True,
            }
            if api_url_match:
                meta["api_url_match"] = api_url_match
            if clean_params:
                meta["params"] = clean_params
            if result.url_field:
                meta["url_field"] = result.url_field
            else:
                # No URL field — try DOM cross-reference to derive url_template
                try:
                    from src.shared.api_sniff import ID_FIELDS as _ID_FIELDS

                    dom_urls = await extract_urls_via_dom_crossref(
                        page,
                        result.candidate.items,
                        url,
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

    - **HTTP mode** (config has ``api_url``, no ``browser``): plain httpx
      fetch — no Playwright needed.
    - **Replay mode** (config has ``api_url`` + ``browser: true``): navigate
      to board_url to establish cookies, then replay via in-browser fetch.
    - **Auto-discover mode** (no ``api_url``): full capture + detect pipeline.
    """
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]
    api_url = metadata.get("api_url")

    # Plain HTTP mode — no Playwright needed (pw passed for api_url_match fallback)
    if api_url and not metadata.get("browser"):
        return await _discover_http(board, client, metadata, pw=pw)

    if api_url:
        # Replay mode — browser preferred, HTTP fallback
        if pw is not None:
            return await _discover_replay(board_url, metadata, pw, client=client)
        else:
            log.warning("api_sniffer.no_playwright_fallback_http", board_url=board_url)
            return await _discover_http(board, client, metadata, pw=pw)

    if pw is None:
        log.error("api_sniffer.no_playwright", board_url=board_url)
        return set()
    return await _discover_auto(board_url, metadata, pw)


# ---------------------------------------------------------------------------
# Plain HTTP helpers
# ---------------------------------------------------------------------------

_DEFAULT_HREF_RE = re.compile(r'href=["\']([^"\'#][^"\']*)["\']')
_HTML_WITH_LINKS_RE = re.compile(r"<[a-z][\s\S]*?href=", re.IGNORECASE)


def score_array(path: str, items: list[dict], api_url: str) -> int:
    """Score an array-of-dicts as a likely job list.

    Uses lightweight heuristics: job keywords in path/URL, presence of
    title and URL fields, and item count as a minor factor.
    """
    score = 0
    sample_keys: set[str] = set()
    for it in items[:5]:
        sample_keys.update(it.keys())

    # Job keywords in array path
    if JOB_KEYWORDS.search(path):
        score += 30
    # Job keywords in API URL
    if JOB_KEYWORDS.search(api_url):
        score += 5
    # Has title-like field
    if any(TITLE_FIELDS.match(k) for k in sample_keys):
        score += 20
    # Has URL-like field
    if find_url_field(items):
        score += 15
    # Reasonable array size (not a tiny filter list)
    if len(items) >= 3:
        score += 5
    return score


def pick_best_array(
    arrays: list[tuple[str, list[dict]]],
    api_url: str,
) -> tuple[str, list[dict]]:
    """Pick the best candidate array from *arrays* using job-list scoring."""
    return max(arrays, key=lambda x: (score_array(x[0], x[1], api_url), len(x[1])))


def find_html_strings(obj: object, path: str = "") -> list[tuple[str, str]]:
    """Find string values in a JSON structure that look like HTML with links.

    Returns ``[(dot_path, html_string), ...]`` sorted by string length
    (longest first — likely the main content).
    """
    results: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            child = f"{path}.{key}" if path else key
            if isinstance(val, str) and len(val) > 100 and _HTML_WITH_LINKS_RE.search(val):
                results.append((child, val))
            elif isinstance(val, (dict, list)):
                results.extend(find_html_strings(val, child))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            results.extend(find_html_strings(val, f"{path}[{i}]"))
    results.sort(key=lambda x: len(x[1]), reverse=True)
    return results


async def http_fetch(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict | None = None,
    body: str | None = None,
) -> dict | None:
    """Fetch JSON via httpx. Returns parsed JSON or None on error."""
    try:
        kw: dict = {"headers": headers or {}, "timeout": 30}
        if method.upper() == "POST" and body:
            kw["content"] = body
            kw["headers"].setdefault("content-type", "application/json")
        resp = await client.request(method.upper(), url, **kw)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        log.warning("api_sniffer.http_fetch_failed", url=url, exc_info=True)
        return None


def _extract_urls_from_html(
    html: str,
    board_url: str,
    url_regex: str | None = None,
) -> set[str]:
    """Extract URLs from an HTML string via regex.

    Default regex captures all ``href`` attribute values.  A custom
    *url_regex* (with one capture group) can be supplied to match other
    patterns.
    """
    pattern = re.compile(url_regex) if url_regex else _DEFAULT_HREF_RE
    urls: set[str] = set()
    for match in pattern.finditer(html):
        raw = match.group(1)
        if raw.startswith(("javascript:", "mailto:")):
            continue
        urls.add(urljoin(board_url, raw))
    return urls


async def _discover_http(
    board: dict,
    client: httpx.AsyncClient,
    config: dict,
    pw=None,
) -> list[DiscoveredJob] | set[str]:
    """Discover jobs via plain httpx — no Playwright needed.

    After fetching JSON from *api_url*, the content at *json_path* is
    inspected:

    - **string** → HTML mode: extract URLs via regex.
    - **list** → items mode: use standard item extraction.

    When *json_path* is omitted, auto-detects the best candidate in the
    response (largest array-of-dicts, or longest HTML string with links).
    Also auto-detects *total_path*, *url_field*, and *fields* when not
    explicitly configured.

    If the initial fetch fails and ``api_url_match`` is configured with
    *pw* available, opens a browser to discover the live URL and retries
    via HTTP.
    """
    board_url = board["board_url"]
    api_url = config["api_url"]
    params = config.get("params")
    if params:
        api_url = _merge_params(api_url, params)
    method = config.get("method", "GET")
    json_path = config.get("json_path")
    url_field = config.get("url_field")
    url_template = config.get("url_template")
    url_regex = config.get("url_regex")
    total_path = config.get("total_path")
    post_data = config.get("post_data") or config.get("post_body")
    request_headers = config.get("request_headers") or config.get("headers") or {}
    fields_map: dict[str, str] = config.get("fields") or {}
    pagination_config = config.get("pagination")

    headers = clean_headers(request_headers)

    # -- first page --------------------------------------------------------
    api_url_match = config.get("api_url_match")
    data = await http_fetch(client, method, api_url, headers, post_data)

    if data is None and api_url_match and pw is not None:
        # Stored URL may be stale (rotating token).  Open browser to discover
        # the live URL, then retry via plain HTTP.
        from src.shared.browser import BROWSER_KEYS, open_page

        wait = config.get("wait", _DEFAULT_WAIT)
        timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        settle = config.get("settle", _DEFAULT_SETTLE)
        browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}

        route_params = config.get("route_params")
        async with open_page(pw, browser_config) as page:
            fresh_url, captured_data = await _discover_live_url(
                page,
                board_url,
                api_url,
                api_url_match,
                wait,
                timeout,
                settle,
                route_params=route_params,
            )
        if captured_data is not None:
            # Use the response the page's own JS already fetched
            log.info("api_sniffer.using_captured_response", url=fresh_url[:80])
            api_url = fresh_url
            data = captured_data
        elif fresh_url != api_url:
            # URL changed but no response captured — retry via HTTP
            log.info("api_sniffer.http_retry_live_url", old=api_url[:80], new=fresh_url[:80])
            api_url = fresh_url
            data = await http_fetch(client, method, api_url, headers, post_data)

    if data is None:
        return list() if fields_map else set()

    # -- auto-detect json_path when not configured -------------------------
    content: object = None
    if json_path is not None:
        content = resolve_path(data, json_path) if json_path else data
    else:
        # Try arrays first (items mode), then HTML strings
        arrays = find_arrays(data)
        if arrays:
            best_path, best_items = pick_best_array(arrays, api_url)
            json_path = best_path
            content = best_items
            log.info("api_sniffer.auto_json_path", path=json_path, items=len(best_items))
        else:
            html_hits = find_html_strings(data)
            if html_hits:
                json_path = html_hits[0][0]
                content = html_hits[0][1]
                log.info("api_sniffer.auto_json_path_html", path=json_path)
            else:
                json_path = ""
                content = data

    # -- auto-detect total_path when not configured ------------------------
    total: int | None = None
    if total_path:
        raw_total = resolve_path(data, total_path)
        if isinstance(raw_total, (int, float)):
            total = int(raw_total)
    elif json_path:
        total = find_total_count(data, json_path)
        if total is not None:
            log.info("api_sniffer.auto_total", total=total)

    # -- HTML string mode --------------------------------------------------
    if isinstance(content, str):
        all_urls = _extract_urls_from_html(content, board_url, url_regex)
        log.info(
            "api_sniffer.http_html_page",
            page=1,
            urls=len(all_urls),
            total=total,
        )

        if pagination_config and all_urls:
            page_size = pagination_config.get("page_size", len(all_urls))
            page_cap = pagination_config.get("max_pages", _HTTP_MAX_PAGES)
            max_pages = page_cap
            if total and page_size:
                max_pages = min(ceil(total / page_size), page_cap)

            pag_param = pagination_config["param_name"]
            pag_start = pagination_config.get("start_value", 0)
            pag_increment = pagination_config.get("increment", 1)
            pag_location = pagination_config.get("location", "query")

            current_value = pag_start + pag_increment
            pages_fetched = 1

            while pages_fetched < max_pages:
                if pag_location == "query":
                    fetch_url = set_url_param(api_url, pag_param, current_value)
                    fetch_body = post_data
                else:
                    fetch_url = api_url
                    fetch_body = set_body_param(post_data, pag_param, current_value)

                page_data = await http_fetch(
                    client,
                    method,
                    fetch_url,
                    headers,
                    fetch_body,
                )
                if page_data is None:
                    break

                page_content = resolve_path(page_data, json_path) if json_path else page_data
                if not isinstance(page_content, str) or not page_content.strip():
                    break

                new_urls = _extract_urls_from_html(page_content, board_url, url_regex)
                if not new_urls - all_urls:
                    break
                all_urls |= new_urls

                pages_fetched += 1
                current_value += pag_increment

            log.info(
                "api_sniffer.http_html_done",
                pages=pages_fetched,
                urls=len(all_urls),
            )

        return all_urls

    # -- list/items mode ---------------------------------------------------
    if isinstance(content, list):
        from src.shared.api_sniff import ArrayCandidate, Exchange, JobListResult, PaginationInfo

        items = [item for item in content if isinstance(item, dict)]

        if pagination_config and items:
            pag = PaginationInfo(
                param_name=pagination_config["param_name"],
                style=pagination_config.get("style", "page"),
                start_value=pagination_config.get("start_value", 0),
                increment=pagination_config.get("increment", 1),
                location=pagination_config.get("location", "query"),
            )
            ex = Exchange(
                method=method,
                url=api_url,
                request_headers=request_headers,
                post_data=post_data,
                status=200,
                body=data,
                content_type="application/json",
                phase="load",
            )
            cand = ArrayCandidate(exchange=ex, json_path=json_path or "$", items=items)
            job_result = JobListResult(
                candidate=cand,
                url_field=url_field,
                total_count=total,
                pagination=pag,
            )
            page_cap = pagination_config.get("max_pages", _HTTP_MAX_PAGES)
            items = await paginate_all(make_http_fetcher(client), job_result, page_cap)

        if len(items) > MAX_ITEMS:
            log.warning("api_sniffer.truncated", total=len(items), cap=MAX_ITEMS)
            items = items[:MAX_ITEMS]

        log.info("api_sniffer.http_items_done", items=len(items))

        # -- auto-detect url_field when not configured ---------------------
        if not url_field and not url_template and items:
            url_field = find_url_field(items)
            if url_field:
                log.info("api_sniffer.auto_url_field", field=url_field)

        # -- auto-detect fields when not configured ------------------------
        if not fields_map and items:
            fields_map = auto_map_fields(items)
            if fields_map:
                log.info("api_sniffer.auto_fields", fields=list(fields_map.keys()))

        if fields_map:
            return _extract_rich(items, fields_map, url_field, url_template, board_url)
        if url_template:
            return _extract_urls_from_template(items, url_template, board_url)
        # Support nested url_field paths (e.g. "data.apply_url")
        if url_field and ("." in url_field or "[" in url_field):
            urls: set[str] = set()
            for item in items:
                raw = extract_field(item, url_field)
                if isinstance(raw, str) and raw:
                    urls.add(urljoin(board_url, raw))
            return urls
        return set(extract_urls(items, url_field, board_url))

    log.warning(
        "api_sniffer.unexpected_content_type",
        json_path=json_path,
        content_type=type(content).__name__,
    )
    return list() if fields_map else set()


async def _discover_live_url(
    page,
    board_url: str,
    api_url: str,
    api_url_match: str,
    wait: str,
    timeout: int,
    settle: float,
    route_params: dict[str, str] | None = None,
) -> tuple[str, object | None]:
    """Navigate and capture the live API URL + response matching *api_url_match*.

    When APIs use rotating tokens in the URL (e.g. ``gateway.example.com/TOKEN/v1/jobs``),
    the stored ``api_url`` goes stale. This helper navigates the page, intercepts
    responses matching the glob, and returns ``(updated_api_url, response_json)``.

    When *route_params* is provided, matching requests are intercepted via
    ``page.route()`` and their query parameters are overridden before the
    page's own JS sends them.  This lets us e.g. increase ``pageSize`` to
    fetch all items in one request — using the page's native request
    mechanism (bypasses bot protection that blocks injected ``fetch()``).

    Falls back to ``(api_url, None)`` if no match.
    """
    from fnmatch import fnmatch

    from src.shared.browser import navigate

    live_response = None  # Playwright Response object

    def _on_response(resp):
        nonlocal live_response
        if live_response:
            return
        parsed = urlparse(resp.url)
        if fnmatch(f"{parsed.netloc}{parsed.path}", api_url_match):
            live_response = resp

    page.on("response", _on_response)

    # Optionally modify the page's own outgoing requests
    if route_params:

        async def _modify_request(route):
            parsed = urlparse(route.request.url)
            params = parse_qs(parsed.query, keep_blank_values=True)
            for k, v in route_params.items():
                params[k] = [str(v)]
            new_query = urlencode(params, doseq=True)
            new_url = urlunparse(parsed._replace(query=new_query))
            log.debug("api_sniffer.route_modified", params=route_params)
            await route.continue_(url=new_url)

        # Convert fnmatch glob to a Playwright glob (** prefix for protocol+host)
        await page.route(f"**/{api_url_match.split('/', 1)[-1]}*", _modify_request)

    try:
        await navigate(page, board_url, {"wait": wait, "timeout": timeout})
    except Exception:
        log.warning("api_sniffer.navigation_failed", board_url=board_url, exc_info=True)
    await asyncio.sleep(settle)

    if live_response:
        live_url = live_response.url
        live_base = urlunparse(urlparse(live_url)._replace(query=""))
        stored_base = urlunparse(urlparse(api_url)._replace(query=""))
        updated_url = api_url
        if live_base != stored_base:
            log.info(
                "api_sniffer.live_url_updated",
                stored=stored_base[:80],
                live=live_base[:80],
            )
            updated_url = api_url.replace(stored_base, live_base)

        # Try to read the response body (already available, page's JS fetched it)
        try:
            data = await live_response.json()
            log.info("api_sniffer.live_response_captured", url=live_url[:80])
            return updated_url, data
        except Exception:
            log.debug("api_sniffer.live_response_read_failed", exc_info=True)
            return updated_url, None

    return api_url, None


async def _discover_replay(
    board_url: str,
    config: dict,
    pw,
    client=None,
) -> list[DiscoveredJob] | set[str]:
    """Replay a stored API call, optionally paginating.

    Supports HTTP fallback: if the in-browser fetch fails and *client* is
    provided, retries with plain httpx.  Browser config keys (``headless``,
    ``user_agent``) are forwarded to Playwright.
    """
    from src.shared.api_sniff import ArrayCandidate, Exchange, JobListResult, PaginationInfo
    from src.shared.browser import BROWSER_KEYS, navigate, open_page

    api_url = config["api_url"]
    params = config.get("params")
    if params:
        api_url = _merge_params(api_url, params)
    method = config.get("method", "GET")
    json_path = config.get("json_path", "$")
    url_field = config.get("url_field")
    url_template = config.get("url_template")
    post_data = config.get("post_data")
    request_headers = config.get("request_headers", {})
    fields_map: dict[str, str] = config.get("fields") or {}
    pagination_config = config.get("pagination")
    api_url_match = config.get("api_url_match")
    route_params = config.get("route_params")

    wait = config.get("wait", _DEFAULT_WAIT)
    timeout = config.get("timeout", _DEFAULT_TIMEOUT)
    settle = config.get("settle", _DEFAULT_SETTLE)

    browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}

    async with open_page(pw, browser_config) as page:
        # route_params requires upfront navigation to intercept the page's
        # own request and modify its params.  Otherwise, navigate just to
        # establish cookies, then try the stored URL via replay first.
        captured_data = None
        if api_url_match and route_params:
            api_url, captured_data = await _discover_live_url(
                page,
                board_url,
                api_url,
                api_url_match,
                wait,
                timeout,
                settle,
                route_params=route_params,
            )
        else:
            # Navigate to board_url to establish cookies/auth context
            try:
                await navigate(page, board_url, {"wait": wait, "timeout": timeout})
            except Exception:
                log.warning("api_sniffer.navigation_failed", board_url=board_url, exc_info=True)
            await asyncio.sleep(settle)

        # Replay the API call — try browser first, fall back to HTTP
        headers = clean_headers(request_headers)
        fetch_fn = make_browser_fetcher(page)
        using_http = False
        data = captured_data  # may already have data from route_params capture
        if data is None:
            try:
                data = await fetch_fn(method, api_url, headers, post_data)
            except Exception:
                # Stored URL may be stale — try live URL discovery
                if api_url_match:
                    log.info("api_sniffer.retry_with_live_url", pattern=api_url_match)
                    fresh_url, fresh_data = await _discover_live_url(
                        page,
                        board_url,
                        api_url,
                        api_url_match,
                        wait,
                        timeout,
                        settle,
                        route_params=route_params,
                    )
                    if fresh_data is not None:
                        api_url = fresh_url
                        data = fresh_data
                    elif fresh_url != api_url:
                        api_url = fresh_url
                        with contextlib.suppress(Exception):
                            data = await fetch_fn(method, api_url, headers, post_data)

            if data is None and client is not None:
                log.warning(
                    "api_sniffer.browser_fetch_failed_fallback_http",
                    api_url=api_url,
                    exc_info=not using_http,
                )
                fetch_fn = make_http_fetcher(client)
                using_http = True
                try:
                    data = await fetch_fn(method, api_url, headers, post_data)
                except Exception:
                    log.error("api_sniffer.http_fallback_failed", api_url=api_url, exc_info=True)
                    return list() if fields_map else set()

            if data is None:
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
                method=method,
                url=api_url,
                request_headers=request_headers,
                post_data=post_data,
                status=200,
                body=data,
                content_type="application/json",
                phase="load",
            )
            total_count = find_total_count(data, json_path)
            cand = ArrayCandidate(exchange=ex, json_path=json_path, items=items)
            job_result = JobListResult(
                candidate=cand,
                url_field=url_field,
                total_count=total_count,
                pagination=pag,
            )
            default_cap = _HTTP_MAX_PAGES if using_http else MAX_PAGES
            max_pg = pagination_config.get("max_pages", default_cap)
            items = await paginate_all(fetch_fn, job_result, max_pg)

        # Cap
        if len(items) > MAX_ITEMS:
            log.warning("api_sniffer.truncated", total=len(items), cap=MAX_ITEMS)
            items = items[:MAX_ITEMS]

        # Build URL map via DOM cross-ref if no url_field and no url_template
        url_map: dict[str, str] | None = None
        if not url_field and not url_template:
            try:
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
                        for item, u in zip(items, dom_urls, strict=False):
                            url_map[str(item.get(id_f, ""))] = u
            except Exception:
                log.debug("api_sniffer.dom_crossref_degraded", exc_info=True)

        if fields_map:
            return _extract_rich(
                items,
                fields_map,
                url_field,
                url_template,
                board_url,
                url_map=url_map,
            )

        # URL-only mode
        if url_template:
            return _extract_urls_from_template(items, url_template, board_url)
        urls = extract_urls(items, url_field, board_url)
        if not urls and url_map:
            return set(url_map.values())
        if not urls:
            try:
                urls = await extract_urls_via_dom_crossref(page, items, board_url)
            except Exception:
                log.debug("api_sniffer.dom_crossref_degraded", exc_info=True)
        return set(urls)


async def _discover_auto(
    board_url: str,
    config: dict,
    pw,
) -> list[DiscoveredJob] | set[str]:
    """Full auto-discover: capture exchanges, detect, paginate."""
    from src.shared.browser import BROWSER_KEYS, dismiss_overlays, navigate, open_page

    fields_map: dict[str, str] = config.get("fields") or {}

    wait = config.get("wait", _DEFAULT_WAIT)
    timeout = config.get("timeout", _DEFAULT_TIMEOUT)
    settle = config.get("settle", _DEFAULT_SETTLE)

    browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}

    async with open_page(pw, browser_config) as page:
        page_host = urlparse(board_url).netloc
        exchanges = await capture_exchanges(page, page_host)

        try:
            await navigate(page, board_url, {"wait": wait, "timeout": timeout})
        except Exception:
            log.warning("api_sniffer.navigation_failed", board_url=board_url, exc_info=True)

        await asyncio.sleep(settle)
        await dismiss_overlays(page)
        await trigger_interactions(page, exchanges)

        result = detect_job_list(exchanges, board_url)
        if result is None:
            log.warning("api_sniffer.no_api_detected", board_url=board_url)
            return list() if fields_map else set()

        page_size = len(result.candidate.items)
        result.pagination = infer_pagination(
            exchanges,
            result.candidate.exchange.url,
            page_size,
        )

        items = await paginate_all(make_browser_fetcher(page), result, MAX_PAGES)

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
                    for item, u in zip(items, dom_urls, strict=False):
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
            needs_extract = "." in url_field or "[" in url_field
            raw = extract_field(item, url_field) if needs_extract else item.get(url_field)
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
        extras: dict[str, object] = {}

        for target, spec in fields_map.items():
            value = extract_field(item, spec)
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
            elif target in ("skills", "responsibilities", "qualifications"):
                extras[target] = value if isinstance(value, list) else [value]
            elif target == "valid_through":
                extras["valid_through"] = value
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
        if extras:
            kwargs["extras"] = extras

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
