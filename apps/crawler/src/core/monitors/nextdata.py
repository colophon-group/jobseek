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
import re
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import structlog

from src.core.monitors import (
    DiscoveredJob,
    fetch_page_text,
    register,
    validate_explicit_source_identity,
)
from src.shared.browser import BROWSER_KEYS, NAVIGATE_KEYS
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
MAX_HTML_CHARS = 2_000_000
_MAX_CONCURRENT_PAGES = 5
_PAGE_FETCH_ATTEMPTS = 3
_PAGE_FETCH_BASE_DELAY = 0.5
_MAX_IDENTITY_FIELD_LENGTH = 256
_MAX_HIRING_ORGANIZATION_LENGTH = 256
_MAX_URL_ALLOWLIST_LENGTH = 2_048
_MAX_PAGE_TITLE_LENGTH = 256
_MAX_ITEM_REQUIREMENTS = 16
_MAX_REQUIRED_VALUE_LENGTH = 256

_TITLE_RE = re.compile(r"<title(?:\s[^>]*)?>(.*?)</title>", re.IGNORECASE | re.DOTALL)

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
    "jobsData.data",
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


def _validated_source_identity_config(
    value: object,
) -> tuple[str, str, str] | None:
    """Validate an opt-in provider identity extracted separately from the URL."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"provider", "tenant", "field"}:
        raise ValueError("nextdata source_identity must contain provider, tenant, and field")
    provider = value.get("provider")
    tenant = value.get("tenant")
    field = value.get("field")
    if not all(isinstance(part, str) and part for part in (provider, tenant, field)):
        raise ValueError("nextdata source_identity values must be non-empty strings")
    assert isinstance(provider, str) and isinstance(tenant, str) and isinstance(field, str)
    if len(field) > _MAX_IDENTITY_FIELD_LENGTH or "\x00" in field:
        raise ValueError("nextdata source_identity.field must be a bounded field path")
    validate_explicit_source_identity(f"{provider}:{tenant}:1")
    return provider, tenant, field


def _source_identity(
    item: dict,
    config: tuple[str, str, str],
) -> str:
    provider, tenant, field = config
    raw_identity = resolve_path(item, field)
    if (
        isinstance(raw_identity, bool)
        or not isinstance(raw_identity, (str, int))
        or not str(raw_identity).strip()
    ):
        raise ValueError("nextdata source identity field was missing or invalid")
    return validate_explicit_source_identity(f"{provider}:{tenant}:{str(raw_identity).strip()}")


def _validated_url_allowlist(value: object) -> re.Pattern[str] | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_URL_ALLOWLIST_LENGTH
        or "\x00" in value
    ):
        raise ValueError("nextdata url_allowlist must be a bounded regular expression")
    try:
        return re.compile(value)
    except re.error as exc:
        raise ValueError("nextdata url_allowlist is invalid") from exc


def _assert_urls_allowed(
    jobs: list[DiscoveredJob],
    url_allowlist: re.Pattern[str],
) -> None:
    if any(url_allowlist.fullmatch(job.url) is None for job in jobs):
        raise ValueError("nextdata discovered a job URL outside its configured allowlist")


def _validated_page_title(value: object) -> str | None:
    """Validate an optional exact page-title contract."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_PAGE_TITLE_LENGTH
        or "\x00" in value
    ):
        raise ValueError("nextdata expected_page_title must be bounded non-empty text")
    return value.strip()


def _assert_page_title(html: str, expected: str) -> None:
    match = _TITLE_RE.search(html)
    actual = " ".join(unescape(match.group(1)).split()) if match is not None else None
    if actual != expected:
        raise ValueError("nextdata page title did not match its configured tenant")


def _validated_item_requirements(
    value: object,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Validate exact per-item values used to prove listing ownership."""
    if value is None:
        return ()
    if not isinstance(value, dict) or not value or len(value) > _MAX_ITEM_REQUIREMENTS:
        raise ValueError("nextdata require_item_values must be a bounded non-empty object")

    requirements: list[tuple[str, tuple[str, ...]]] = []
    for field, expected in value.items():
        if (
            not isinstance(field, str)
            or not field
            or len(field) > _MAX_IDENTITY_FIELD_LENGTH
            or "\x00" in field
        ):
            raise ValueError("nextdata require_item_values contains an invalid field path")
        if (
            not isinstance(expected, list)
            or not expected
            or not all(
                isinstance(item, str)
                and item
                and len(item) <= _MAX_REQUIRED_VALUE_LENGTH
                and "\x00" not in item
                for item in expected
            )
        ):
            raise ValueError("nextdata require_item_values expects non-empty string lists")
        requirements.append((field, tuple(expected)))
    return tuple(requirements)


def _assert_item_requirements(
    items: list,
    requirements: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    """Fail closed unless every advertised item proves the configured tenant."""
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("nextdata tenant-scoped inventory contained a non-object item")
        for field, expected in requirements:
            if resolve_path(item, field) != list(expected):
                raise ValueError("nextdata item did not match its configured tenant values")


def _validated_item_inclusions(
    value: object,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Validate exact values used to retain tenant-owned listing items."""
    if value is None:
        return ()
    if not isinstance(value, dict) or not value or len(value) > _MAX_ITEM_REQUIREMENTS:
        raise ValueError("nextdata include_item_values must be a bounded non-empty object")

    inclusions: list[tuple[str, tuple[str, ...]]] = []
    for field, expected in value.items():
        if (
            not isinstance(field, str)
            or not field
            or len(field) > _MAX_IDENTITY_FIELD_LENGTH
            or "\x00" in field
        ):
            raise ValueError("nextdata include_item_values contains an invalid field path")
        if (
            not isinstance(expected, list)
            or not expected
            or not all(
                isinstance(item, str)
                and item
                and len(item) <= _MAX_REQUIRED_VALUE_LENGTH
                and "\x00" not in item
                for item in expected
            )
        ):
            raise ValueError("nextdata include_item_values expects non-empty string lists")
        inclusions.append((field, tuple(expected)))
    return tuple(inclusions)


def _filter_included_items(
    items: list,
    inclusions: tuple[tuple[str, tuple[str, ...]], ...],
) -> list:
    """Retain only items whose configured fields contain exact allowed values."""
    if not inclusions:
        return items

    included: list = []
    for item in items:
        if not isinstance(item, dict):
            continue
        matches = True
        for field, expected in inclusions:
            actual = resolve_path(item, field)
            if isinstance(actual, list):
                matches = any(value in expected for value in actual if isinstance(value, str))
            else:
                matches = isinstance(actual, str) and actual in expected
            if not matches:
                break
        if matches:
            included.append(item)

    log.info(
        "nextdata.item_inclusions_applied",
        input_items=len(items),
        included_items=len(included),
        fields=[field for field, _expected in inclusions],
    )
    return included


def _validated_hiring_organization_config(
    metadata: dict,
) -> tuple[re.Pattern[str], re.Pattern[str]] | None:
    expected = metadata.get("expected_hiring_organization")
    if expected is None:
        return None
    if (
        not isinstance(expected, str)
        or not expected.strip()
        or len(expected) > _MAX_HIRING_ORGANIZATION_LENGTH
        or "\x00" in expected
    ):
        raise ValueError("nextdata expected_hiring_organization must be bounded non-empty text")
    url_allowlist = _validated_url_allowlist(metadata.get("url_allowlist"))
    if url_allowlist is None:
        raise ValueError("nextdata expected_hiring_organization requires a bounded url_allowlist")
    return re.compile(re.escape(expected.strip())), url_allowlist


async def _filter_hiring_organization(
    jobs: list[DiscoveredJob],
    client: httpx.AsyncClient,
    config: tuple[re.Pattern[str], re.Pattern[str]],
) -> list[DiscoveredJob]:
    """Retain only jobs whose detail JSON-LD proves the configured employer."""
    hiring_organization_pattern, url_allowlist = config
    for job in jobs:
        if url_allowlist.fullmatch(job.url) is None:
            raise ValueError(
                "nextdata refused to verify a job URL outside its configured allowlist"
            )

    # Reuse the bounded, retrying verifier used by the DOM monitor. Importing
    # lazily avoids changing monitor registration order.
    from src.core.monitors.dom import _filter_jsonld_job_urls

    allowed_urls = await _filter_jsonld_job_urls(
        {job.url for job in jobs},
        client,
        hiring_organization_pattern,
    )
    return [job for job in jobs if job.url in allowed_urls]


def _detection_metadata(source: str, data: dict, path: str, count: int) -> dict:
    """Build probe metadata, including a ready config for known RSC shapes."""
    metadata: dict = {"path": path, "count": count}
    if source != "nextdata":
        metadata["source"] = source

    # onlyfy's Next.js career pages expose each listing and pagination metadata
    # in the server-rendered RSC payload. Detail pages currently fail client-side,
    # but the stable print endpoint returns the complete posting as a PDF.
    if source == "rsc" and path == "jobsData.data":
        items = resolve_path(data, path)
        sample = items[0] if isinstance(items, list) and items else {}
        if isinstance(sample, dict) and {"jobAdUrl", "title", "cityName"} <= sample.keys():
            metadata.update(
                {
                    "url_template": "{jobAdUrl}",
                    "url_transform": {
                        "find": "/job/",
                        "replace": "/candidate/job/print/",
                    },
                    "fields": {
                        "title": "title",
                        "locations": "cityName",
                        "employment_type": "positionTypeName",
                        "date_posted": "publishedAt",
                    },
                }
            )
            page_count = resolve_path(data, "jobsData.meta.totalPages")
            if page_count is not None:
                metadata["pagination"] = {
                    "path": "jobsData.meta",
                    "page_count": "totalPages",
                    "page_param": "page",
                }
                total = resolve_path(data, "jobsData.meta.totalItems")
                if total is not None:
                    metadata["count"] = int(total)

    return metadata


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


def _board_gone_statuses(metadata: dict) -> frozenset[int]:
    """Validate explicit first-page retirement statuses for provider wrappers."""
    raw = metadata.get("board_gone_statuses", [])
    if (
        not isinstance(raw, list)
        or len(raw) > 8
        or any(isinstance(status, bool) or not isinstance(status, int) for status in raw)
        or not set(raw).issubset({404, 410})
    ):
        raise ValueError("nextdata board_gone_statuses may contain only HTTP 404 and 410")
    return frozenset(raw)


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


def _find_jobs_path(
    data: dict,
    paths: list[str] | None = None,
    *,
    allow_nested: bool = False,
) -> tuple[str, int] | None:
    """Search common paths for a plausible jobs array. Returns (path, count) or None."""
    for path in paths or _COMMON_PATHS:
        arr = resolve_path(data, path)
        if (
            isinstance(arr, list)
            and len(arr) >= 5
            and all(isinstance(item, dict) for item in arr[:5])
        ):
            return path, len(arr)
    if allow_nested:
        return _find_nested_jobs_path(data)
    return None


def _find_nested_jobs_path(data: dict) -> tuple[str, int] | None:
    """Find a job array nested inside an App Router component tree.

    Next.js RSC payloads do not guarantee a stable top-level property for page
    data.  Some applications pass their listing to a deeply nested component
    prop (for example ``children[...].items``).  Keep the normal, explicit
    paths as the fast path, then walk the component tree for arrays whose
    objects consistently look like jobs.

    Requiring an identifier, a description, and either a direct title or a
    named nested position avoids mistaking filter-option arrays for postings.
    """

    def _job_like(item: object) -> bool:
        if not isinstance(item, dict) or item.get("id") is None:
            return False
        if not any(item.get(key) for key in ("description", "jobDescription", "content")):
            return False
        if any(item.get(key) for key in ("title", "name", "jobTitle", "job_title")):
            return True
        position = item.get("position")
        return isinstance(position, dict) and bool(position.get("name") or position.get("title"))

    def _walk(value: object, path: str) -> tuple[str, int] | None:
        if isinstance(value, list):
            if len(value) >= 5 and all(_job_like(item) for item in value[:5]):
                return path, len(value)
            for index, child in enumerate(value):
                found = _walk(child, f"{path}[{index}]")
                if found:
                    return found
            return None

        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                found = _walk(child, child_path)
                if found:
                    return found
        return None

    return _walk(data, "")


def _resolve_items(data: dict, path: str, source: str) -> list | None:
    """Resolve configured items, recovering from shifted RSC component indexes."""
    items = resolve_path(data, path)
    if isinstance(items, list):
        return items
    if source != "rsc":
        return None

    nested = _find_nested_jobs_path(data)
    if not nested:
        return None
    recovered_path, _ = nested
    recovered = resolve_path(data, recovered_path)
    if isinstance(recovered, list):
        log.info(
            "nextdata.rsc_path_recovered",
            configured_path=path,
            recovered_path=recovered_path,
        )
        return recovered
    return None


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Detect whether *url* has embedded JSON with a plausible jobs array.

    Checks for Next.js ``__NEXT_DATA__`` and React Router
    ``__staticRouterHydrationData``.  Tries static HTTP first, then falls
    back to Playwright if neither is found.

    When *pw* is provided, the Playwright fallback reuses that instance.
    """
    html = await fetch_page_text(url, client, max_chars=MAX_HTML_CHARS)
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
            result = _find_jobs_path(data, _RSC_PATHS, allow_nested=True)
            if result:
                path, count = result
                log.info("nextdata.detected", url=url, source="rsc", path=path, count=count)
                return _detection_metadata("rsc", data, path, count)

        # Try Phenom Canvas (phApp.ddo = {...})
        data = extract_phenom_canvas_data(html)
        if data:
            result = _find_jobs_path(data, _PHENOM_CANVAS_PATHS)
            if result:
                path, count = result
                meta = _phenom_canvas_meta(data, path, count, url)
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
                result = _find_jobs_path(data, paths, allow_nested=source == "rsc")
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
                        meta = _phenom_canvas_meta(data, path, count, url)
                        meta["render"] = True
                        return meta
                    meta = _detection_metadata(source, data, path, count)
                    meta["render"] = True
                    return meta
    except Exception:
        log.debug("nextdata.render_fallback_failed", url=url, exc_info=True)

    return None


def _phenom_canvas_meta(data: dict, path: str, count: int, board_url: str) -> dict:
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
    parsed = urlparse(board_url)
    path_without_slash = parsed.path.rstrip("/")
    if path_without_slash.endswith("/search-results"):
        detail_path = f"{path_without_slash.removesuffix('/search-results')}/job/{{jobId}}"
        meta["url_template"] = urlunparse((parsed.scheme, parsed.netloc, detail_path, "", "", ""))
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
    strict_path = metadata.get("strict_path", False)
    if not isinstance(strict_path, bool):
        raise ValueError("nextdata strict_path must be a boolean")

    path = metadata.get("path")
    if not path:
        if strict_path:
            raise ValueError("nextdata strict_path requires path and url_template")
        log.error("nextdata.missing_path", board_url=board_url)
        return set()

    url_template = metadata.get("url_template")
    if not url_template:
        if strict_path:
            raise ValueError("nextdata strict_path requires path and url_template")
        log.error("nextdata.missing_url_template", board_url=board_url)
        return set()

    source: str = metadata.get("source", "nextdata")
    browser_expression = metadata.get("browser_expression")
    fields_map: dict[str, str | dict] = metadata.get("fields") or {}
    slug_fields: list[str] | None = metadata.get("slug_fields")
    source_identity_config = _validated_source_identity_config(metadata.get("source_identity"))
    if source_identity_config is not None and not fields_map:
        raise ValueError("nextdata source_identity requires rich fields")
    url_allowlist = _validated_url_allowlist(metadata.get("url_allowlist"))
    if source_identity_config is not None and url_allowlist is None:
        raise ValueError("nextdata source_identity requires a url_allowlist")
    hiring_organization_config = _validated_hiring_organization_config(metadata)
    expected_page_title = _validated_page_title(metadata.get("expected_page_title"))
    item_requirements = _validated_item_requirements(metadata.get("require_item_values"))
    item_inclusions = _validated_item_inclusions(metadata.get("include_item_values"))
    render = metadata.get("render", False) or source == "browser"
    actions = metadata.get("actions")
    pagination_cfg: dict | None = metadata.get("pagination")
    base_salary_cfg: dict | None = metadata.get("base_salary")
    board_gone_statuses = _board_gone_statuses(metadata)

    if expected_page_title is not None and source == "browser":
        raise ValueError("nextdata expected_page_title does not support browser source")
    if expected_page_title is not None and pagination_cfg:
        raise ValueError("nextdata expected_page_title does not support pagination")
    if item_inclusions and pagination_cfg and pagination_cfg.get("total_records"):
        raise ValueError(
            "nextdata include_item_values cannot validate an unfiltered pagination total"
        )

    if not render and actions:
        log.warning(
            "nextdata.misconfiguration",
            board_url=board_url,
            detail="actions require render=true; overriding render to true",
        )
        render = True

    browser_keys = BROWSER_KEYS if source == "browser" else NAVIGATE_KEYS
    browser_config = {k: v for k, v in metadata.items() if k in browser_keys}

    if source == "browser":
        if not isinstance(browser_expression, str) or not browser_expression.strip():
            raise ValueError("nextdata browser source requires a non-empty browser_expression")
        data = await _evaluate_browser_data(
            board_url,
            browser_expression,
            pw,
            browser_config,
        )
        if not data:
            raise RuntimeError("nextdata browser expression returned no data")
        items = _resolve_items(data, path, source)
        if not isinstance(items, list):
            raise RuntimeError(f"nextdata browser path did not resolve to a list: {path}")
    else:
        if pagination_cfg:
            data, items = await _fetch_embedded_page_with_retry(
                board_url,
                render=render,
                client=client,
                path=path,
                source=source,
                pw=pw,
                browser_config=browser_config,
                allow_empty=True,
                board_gone_statuses=board_gone_statuses,
            )
        else:
            html = await _fetch_html(
                board_url,
                render,
                client,
                pw=pw,
                browser_config=browser_config,
                board_gone_statuses=board_gone_statuses,
            )
            if not html:
                log.warning("nextdata.fetch_failed", board_url=board_url)
                if strict_path:
                    raise RuntimeError("nextdata strict_path fetched no HTML")
                return list() if fields_map else set()
            if expected_page_title is not None:
                _assert_page_title(html, expected_page_title)
            data = extract_embedded_json(html, source)
            if not data:
                log.warning("nextdata.no_data", board_url=board_url, source=source)
                if strict_path:
                    raise ValueError("nextdata strict_path found no embedded data")
                return list() if fields_map else set()
            items = _resolve_items(data, path, source)
            if not isinstance(items, list):
                log.warning("nextdata.path_not_list", board_url=board_url, path=path)
                if strict_path:
                    raise ValueError("nextdata strict_path did not resolve to a list")
                return list() if fields_map else set()

    # Pagination: fetch remaining pages and merge
    if pagination_cfg and source == "browser":
        raise ValueError(
            "nextdata browser source does not support pagination; "
            "browser_expression must return the complete inventory"
        )
    if pagination_cfg:
        _validate_empty_first_page(items, data, pagination_cfg, board_url=board_url)
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

    items = _filter_included_items(items, item_inclusions)

    if item_requirements:
        _assert_item_requirements(items, item_requirements)

    # Cap items
    if len(items) > MAX_URLS and strict_path:
        raise ValueError("nextdata strict_path exceeded the safe inventory limit")
    if len(items) > MAX_URLS:
        log.warning("nextdata.truncated", total=len(items), cap=MAX_URLS)
        items = items[:MAX_URLS]

    if fields_map:
        result = _extract_rich(
            items,
            url_template,
            slug_fields,
            fields_map,
            base_salary_cfg,
            source_identity_config,
        )
        if url_allowlist is not None:
            _assert_urls_allowed(result, url_allowlist)
        if hiring_organization_config is not None:
            result = await _filter_hiring_organization(
                result,
                client,
                hiring_organization_config,
            )
        urls = {job.url for job in result}
    else:
        result = _extract_urls(items, url_template, slug_fields)
        urls = result
    if pagination_cfg:
        _validate_total_records(urls, data, pagination_cfg, board_url=board_url)
    return result


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
    strict_path = metadata.get("strict_path", False)
    if not isinstance(strict_path, bool):
        raise ValueError("nextdata strict_path must be a boolean")

    path = metadata.get("path")
    url_template = metadata.get("url_template")
    if not path or not url_template:
        if strict_path:
            raise ValueError("nextdata strict_path requires path and url_template")
        return

    source: str = metadata.get("source", "nextdata")
    browser_expression = metadata.get("browser_expression")
    fields_map: dict[str, str | dict] = metadata.get("fields") or {}
    slug_fields: list[str] | None = metadata.get("slug_fields")
    source_identity_config = _validated_source_identity_config(metadata.get("source_identity"))
    if source_identity_config is not None and not fields_map:
        raise ValueError("nextdata source_identity requires rich fields")
    url_allowlist = _validated_url_allowlist(metadata.get("url_allowlist"))
    if source_identity_config is not None and url_allowlist is None:
        raise ValueError("nextdata source_identity requires a url_allowlist")
    hiring_organization_config = _validated_hiring_organization_config(metadata)
    expected_page_title = _validated_page_title(metadata.get("expected_page_title"))
    item_requirements = _validated_item_requirements(metadata.get("require_item_values"))
    item_inclusions = _validated_item_inclusions(metadata.get("include_item_values"))
    render = metadata.get("render", False) or source == "browser"
    actions = metadata.get("actions")
    pagination_cfg: dict | None = metadata.get("pagination")
    base_salary_cfg: dict | None = metadata.get("base_salary")
    board_gone_statuses = _board_gone_statuses(metadata)

    if expected_page_title is not None and source == "browser":
        raise ValueError("nextdata expected_page_title does not support browser source")
    if expected_page_title is not None and pagination_cfg:
        raise ValueError("nextdata expected_page_title does not support pagination")
    if item_inclusions and pagination_cfg and pagination_cfg.get("total_records"):
        raise ValueError(
            "nextdata include_item_values cannot validate an unfiltered pagination total"
        )

    if not render and actions:
        render = True

    if pagination_cfg and source == "browser":
        raise ValueError(
            "nextdata browser source does not support pagination; "
            "browser_expression must return the complete inventory"
        )

    browser_keys = BROWSER_KEYS if source == "browser" else NAVIGATE_KEYS
    browser_config = {k: v for k, v in metadata.items() if k in browser_keys}

    if source == "browser":
        if not isinstance(browser_expression, str) or not browser_expression.strip():
            raise ValueError("nextdata browser source requires a non-empty browser_expression")
        data = await _evaluate_browser_data(
            board_url,
            browser_expression,
            pw,
            browser_config,
        )
        if not data:
            raise RuntimeError("nextdata browser expression returned no data")
        items = _resolve_items(data, path, source)
        if not isinstance(items, list):
            raise RuntimeError(f"nextdata browser path did not resolve to a list: {path}")
    else:
        if pagination_cfg:
            data, items = await _fetch_embedded_page_with_retry(
                board_url,
                render=render,
                client=client,
                path=path,
                source=source,
                pw=pw,
                browser_config=browser_config,
                allow_empty=True,
                board_gone_statuses=board_gone_statuses,
            )
        else:
            html = await _fetch_html(
                board_url,
                render,
                client,
                pw=pw,
                browser_config=browser_config,
                board_gone_statuses=board_gone_statuses,
            )
            if not html:
                if strict_path:
                    raise RuntimeError("nextdata strict_path fetched no HTML")
                return
            if expected_page_title is not None:
                _assert_page_title(html, expected_page_title)
            data = extract_embedded_json(html, source)
            if not data:
                if strict_path:
                    raise ValueError("nextdata strict_path found no embedded data")
                return
            items = _resolve_items(data, path, source)
            if not isinstance(items, list):
                if strict_path:
                    raise ValueError("nextdata strict_path did not resolve to a list")
                return

    def _extract_batch(batch_items: list):
        if fields_map:
            return _extract_rich(
                batch_items,
                url_template,
                slug_fields,
                fields_map,
                base_salary_cfg,
                source_identity_config,
            )
        return _extract_urls(batch_items, url_template, slug_fields)

    async def _verified_batch(batch_items: list):
        batch_items = _filter_included_items(batch_items, item_inclusions)
        if item_requirements:
            _assert_item_requirements(batch_items, item_requirements)
        result = _extract_batch(batch_items)
        if fields_map and url_allowlist is not None:
            _assert_urls_allowed(result, url_allowlist)
        if fields_map and hiring_organization_config is not None:
            result = await _filter_hiring_organization(
                result,
                client,
                hiring_organization_config,
            )
        return result

    # No pagination — single yield
    if not pagination_cfg:
        yield await _verified_batch(items)
        return

    # Determine page count
    _validate_empty_first_page(items, data, pagination_cfg, board_url=board_url)
    page_count = _resolve_page_count(data, pagination_cfg)
    if page_count is None:
        raise ValueError("nextdata pagination metadata did not provide a valid page count")

    first_result = await _verified_batch(items)
    seen_urls = {job.url for job in first_result} if fields_map else set(first_result)
    yield first_result
    if page_count <= 1:
        _validate_total_records(seen_urls, data, pagination_cfg, board_url=board_url)
        return

    page_urls = _compute_page_urls(board_url, page_count, pagination_cfg)
    sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

    async def _fetch_page(page_url: str) -> list:
        async with sem:
            _page_data, page_items = await _fetch_embedded_page_with_retry(
                page_url,
                render=render,
                client=client,
                path=path,
                source=source,
                pw=pw,
                browser_config=browser_config,
            )
            return page_items

    # Fetch remaining pages in batches of _STREAM_BATCH_PAGES
    for i in range(0, len(page_urls), _STREAM_BATCH_PAGES):
        chunk = page_urls[i : i + _STREAM_BATCH_PAGES]
        results = await asyncio.gather(*[_fetch_page(u) for u in chunk])
        batch_items: list = []
        for page_items in results:
            batch_items.extend(page_items)
        if batch_items:
            result = await _verified_batch(batch_items)
            if fields_map:
                seen_urls.update(job.url for job in result)
            else:
                seen_urls.update(result)
            yield result

    _validate_total_records(seen_urls, data, pagination_cfg, board_url=board_url)


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
    raw_total = resolve_path(pagination_data, total_records_field)
    if raw_total is None:
        return None
    try:
        import math

        return math.ceil(int(raw_total) / int(page_size))
    except (ValueError, TypeError):
        return None


def _resolve_total_records(data: dict, pagination_cfg: dict) -> int | None:
    """Return an authoritative first-page total when the config exposes one."""
    pagination_path = pagination_cfg.get("path")
    total_records_field = pagination_cfg.get("total_records")
    if not pagination_path or not total_records_field:
        return None
    pagination_data = resolve_path(data, pagination_path)
    if not isinstance(pagination_data, dict):
        return None
    raw_total = resolve_path(pagination_data, total_records_field)
    try:
        total = int(raw_total)
    except (TypeError, ValueError):
        return None
    return total if total >= 0 else None


def _validate_total_records(
    urls: set[str],
    data: dict,
    pagination_cfg: dict,
    *,
    board_url: str,
) -> None:
    """Fail the run when paginated extraction omits or duplicates jobs."""
    expected = _resolve_total_records(data, pagination_cfg)
    if expected is None or expected > MAX_URLS:
        return
    if len(urls) != expected:
        raise RuntimeError(
            f"nextdata discovered {len(urls)} unique jobs for {board_url}; expected {expected}"
        )


async def _fetch_html(
    url: str,
    render: bool,
    client: httpx.AsyncClient,
    pw=None,
    browser_config: dict | None = None,
    board_gone_statuses: frozenset[int] = frozenset(),
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
    return await fetch_page_text(
        url,
        client,
        max_chars=MAX_HTML_CHARS,
        board_gone_statuses=board_gone_statuses,
    )


async def _fetch_embedded_page_with_retry(
    url: str,
    *,
    render: bool,
    client: httpx.AsyncClient,
    path: str,
    source: str,
    pw=None,
    browser_config: dict | None = None,
    allow_empty: bool = False,
    board_gone_statuses: frozenset[int] = frozenset(),
) -> tuple[dict, list]:
    """Fetch and parse one required embedded-data page or fail the run.

    A missing page in a successful monitor cycle would make timestamp-based
    gone detection tombstone that page's live jobs. Retry the complete
    fetch/parse/path operation, then raise instead of returning an empty list.
    """
    failure = "unknown failure"
    for attempt in range(_PAGE_FETCH_ATTEMPTS):
        html = await _fetch_html(
            url,
            render,
            client,
            pw=pw,
            browser_config=browser_config,
            board_gone_statuses=board_gone_statuses,
        )
        if not html:
            failure = "empty or unavailable HTML"
        else:
            data = extract_embedded_json(html, source)
            if not data:
                failure = f"missing {source} embedded data"
            else:
                items = _resolve_items(data, path, source)
                if isinstance(items, list) and (items or allow_empty):
                    return data, items
                failure = (
                    f"path resolved to an empty required page: {path}"
                    if isinstance(items, list)
                    else f"path did not resolve to a list: {path}"
                )

        if attempt < _PAGE_FETCH_ATTEMPTS - 1:
            delay = _PAGE_FETCH_BASE_DELAY * (2**attempt)
            log.info(
                "nextdata.page_retry",
                url=url,
                attempt=attempt + 1,
                delay_s=delay,
                failure=failure,
            )
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"nextdata required page failed after {_PAGE_FETCH_ATTEMPTS} attempts: {url} ({failure})"
    )


def _validate_empty_first_page(
    items: list,
    data: dict,
    pagination_cfg: dict,
    *,
    board_url: str,
) -> None:
    """Allow an empty first page only when pagination proves inventory is empty."""
    if items:
        return

    expected = _resolve_total_records(data, pagination_cfg)
    page_count = _resolve_page_count(data, pagination_cfg)
    if expected == 0 or (
        not pagination_cfg.get("total_records") and page_count is not None and page_count <= 1
    ):
        return

    raise RuntimeError(
        f"nextdata first page was empty for non-empty paginated inventory: {board_url} "
        f"(expected={expected}, page_count={page_count})"
    )


async def _evaluate_browser_data(
    url: str,
    expression: str,
    pw,
    browser_config: dict | None = None,
) -> object | None:
    """Evaluate a JSON-serializable jobs expression in a rendered page.

    Some campaign sites keep their complete job list in a client-side
    JavaScript variable while rendering application links whose destination is
    only a login shell.  Reading the list in the page context preserves the
    structured titles and descriptions without scraping those lossy links.
    """
    if pw is None:
        raise RuntimeError("nextdata browser source requires Playwright")

    from src.shared.browser import navigate, open_page

    config = browser_config or {}
    async with open_page(pw, config, use_proxy=bool(config.get("proxy"))) as page:
        await navigate(page, url, config)
        return await page.evaluate(expression)


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
    if page_count is None:
        raise ValueError("nextdata pagination metadata did not provide a valid page count")
    if page_count <= 1:
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
            _page_data, items = await _fetch_embedded_page_with_retry(
                page_url,
                render=render,
                client=client,
                path=path,
                source=source,
                pw=pw,
                browser_config=browser_config,
            )
            return items

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
    source_identity_config: tuple[str, str, str] | None = None,
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
        if source_identity_config is not None:
            kwargs["source_identity"] = _source_identity(item, source_identity_config)
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
    data = extract_embedded_json(resp.text, metadata.get("source", "nextdata"))
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
