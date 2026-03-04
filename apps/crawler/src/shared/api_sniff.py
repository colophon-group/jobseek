"""Shared utilities for API sniffing — detecting job-list APIs via XHR/fetch capture.

Extracted from ``scripts/discover_jobs.py``.  Pure functions operate on
dataclass structures and require no Playwright; Playwright-dependent helpers
are grouped at the bottom of the module.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_PATTERNS: tuple[str, ...] = (
    "google-analytics",
    "analytics",
    "dataplane.rum",
    "doubleclick",
    "facebook",
    "hotjar",
    "sentry",
    "segment",
    "amplitude",
    "mixpanel",
    "newrelic",
    "cloudwatch",
    "boomerang",
    "demdex.net",
    "omtrdc.net",
    "googlesyndication",
    "googletagmanager",
    "gtag",
)

JOB_KEYWORDS = re.compile(
    r"job|career|position|opening|vacanc|posting|requisition|listing|rolle|stellen",
    re.IGNORECASE,
)

TITLE_FIELDS = re.compile(
    r"^(title|name|job_?title|position_?title|label|heading|role|job_?name)$",
    re.IGNORECASE,
)

URL_FIELDS = re.compile(
    r"(url|link|href|path|slug|uri|canonical|apply|detail)",
    re.IGNORECASE,
)

COUNT_FIELDS = re.compile(
    r"^(total|count|total_?count|total_?results|hits|num_?found|result_?count"
    r"|size|totalCount|totalResults|totalHits|nbHits)$",
    re.IGNORECASE,
)

ID_FIELDS = re.compile(
    r"^(id|positionId|position_id|jobId|job_id|reqId|req_id"
    r"|requisitionId|posting_id|postingId|externalId)$",
    re.IGNORECASE,
)

SLUG_FIELDS = re.compile(
    r"(slug|transformedPostingTitle|transformed_title|url_?slug|seo_?title|url_?title)",
    re.IGNORECASE,
)

SIZE_PARAMS: tuple[str, ...] = (
    "result_limit",
    "limit",
    "pageSize",
    "page_size",
    "size",
    "per_page",
    "perPage",
    "count",
    "rows",
    "hitsPerPage",
    "num",
    "rpp",
    "resultsPerPage",
    "results_per_page",
    "itemsPerPage",
    "items_per_page",
    "maxResults",
    "max_results",
)

PAGINATION_PARAM_DEFAULTS: dict[str, int] = {
    "page": 1,
    "pagenumber": 1,
    "p": 1,
    "pageno": 1,
    "offset": 0,
    "start": 0,
    "skip": 0,
    "from": 0,
}

_DESIRED_PAGE_SIZE = 100

# Headers to strip when replaying requests
_SKIP_HEADERS = frozenset(
    {
        "host",
        "connection",
        "content-length",
        "accept-encoding",
        "transfer-encoding",
    }
)

# ---------------------------------------------------------------------------
# Field auto-mapping patterns
# ---------------------------------------------------------------------------

FIELD_PATTERNS: dict[str, re.Pattern] = {
    "title": re.compile(
        r"^(title|name|job_?title|position_?title|label|heading|role|job_?name)$",
        re.I,
    ),
    "description": re.compile(
        r"^(description|body|content|bodyHtml|body_?html|descriptionHtml"
        r"|description_?html|text|details|job_?description|summary)$",
        re.I,
    ),
    "employment_type": re.compile(
        r"^(employment_?type|type|job_?type|work_?type|contract_?type"
        r"|employmentType|workType)$",
        re.I,
    ),
    "date_posted": re.compile(
        r"^(date_?posted|posted_?at|posted_?date|published_?at|created_?at"
        r"|datePosted|publishedAt|createdAt|publish_?date)$",
        re.I,
    ),
    "job_location_type": re.compile(
        r"^(job_?location_?type|workplace_?type|remote_?type|location_?type"
        r"|workplaceType|locationType|isRemote|remote)$",
        re.I,
    ),
}

# Location patterns — match both simple keys and array-of-object patterns
_LOCATION_KEY_PATTERNS = re.compile(
    r"^(location|locations|office|offices|city|cities|place|places)$",
    re.I,
)
_LOCATION_SUBFIELD_PATTERNS = re.compile(
    r"^(name|title|city|label|display_?name|displayName|value)$",
    re.I,
)

# Metadata patterns — department/team
_METADATA_PATTERNS: dict[str, re.Pattern] = {
    "metadata.team": re.compile(
        r"^(team|department|group|division|org|organization|category"
        r"|departmentName|teamName|team_?name|department_?name)$",
        re.I,
    ),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Exchange:
    """A captured request-response pair."""

    method: str
    url: str
    request_headers: dict
    post_data: str | None
    status: int
    body: object  # parsed JSON or None
    content_type: str
    phase: str  # "load" or "interaction"


@dataclass
class ArrayCandidate:
    """A JSON array-of-dicts found in a response, with its score."""

    exchange: Exchange
    json_path: str  # dot path to the array inside the response body
    items: list[dict]
    score: int = 0


@dataclass
class PaginationInfo:
    param_name: str
    style: str  # "offset" or "page"
    start_value: int
    increment: int
    location: str  # "query" or "body"
    observed_value: int | None = None


@dataclass
class JobListResult:
    candidate: ArrayCandidate
    url_field: str | None
    total_count: int | None
    pagination: PaginationInfo | None


# ---------------------------------------------------------------------------
# Pure functions — no Playwright dependency
# ---------------------------------------------------------------------------


def find_arrays(obj: object, path: str = "") -> list[tuple[str, list[dict]]]:
    """Recursively find arrays of 3+ dicts in any JSON structure."""
    results: list[tuple[str, list[dict]]] = []
    if isinstance(obj, list):
        dicts = [x for x in obj if isinstance(x, dict)]
        if len(dicts) >= 3:
            results.append((path or "$", dicts))
    if isinstance(obj, dict):
        for key, val in obj.items():
            child_path = f"{path}.{key}" if path else key
            results.extend(find_arrays(val, child_path))
    return results


def _looks_like_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return bool(value.startswith(("http://", "https://", "/")) and len(value) > 5)


def find_url_field(items: list[dict]) -> str | None:
    """Detect which field holds job URLs — first by name, then by value pattern."""
    if not items:
        return None
    sample = items[:5]

    # By field name
    for item in sample:
        for key in item:
            if URL_FIELDS.search(key) and any(
                _looks_like_url(str(it.get(key, ""))) for it in sample
            ):
                return key

    # By value pattern
    for key in sample[0]:
        if all(_looks_like_url(str(it.get(key, ""))) for it in sample if key in it):
            return key

    return None


def find_total_count(body: object, array_path: str) -> int | None:
    """Find a count/total sibling field near the array."""
    if not isinstance(body, dict):
        return None

    from src.shared.nextdata import resolve_path

    # Walk to parent of the array
    parts = array_path.split(".")
    parent_path = ".".join(parts[:-1])
    obj = resolve_path(body, parent_path) if parent_path else body
    if obj is None:
        obj = body

    if isinstance(obj, dict):
        for key, val in obj.items():
            if COUNT_FIELDS.match(key) and isinstance(val, (int, float)):
                return int(val)

    # Also check top-level
    if isinstance(body, dict) and obj is not body:
        for key, val in body.items():
            if COUNT_FIELDS.match(key) and isinstance(val, (int, float)):
                return int(val)

    return None


def _schema_uniformity(items: list[dict]) -> float:
    """Return 0-1 how uniform the keys are across items."""
    if len(items) < 2:
        return 1.0
    key_sets = [frozenset(it.keys()) for it in items[:20]]
    base = key_sets[0]
    matches = sum(1 for ks in key_sets[1:] if ks == base)
    return matches / (len(key_sets) - 1)


def score_candidate(cand: ArrayCandidate, page_url: str) -> int:
    """Score an array-of-dicts candidate as a job list."""
    score = 0
    items = cand.items
    ex = cand.exchange

    # URL field
    url_field = find_url_field(items)
    if url_field:
        score += 30

    # Title field
    sample_keys: set[str] = set()
    for it in items[:5]:
        sample_keys.update(it.keys())
    if any(TITLE_FIELDS.match(k) for k in sample_keys):
        score += 15

    # Job keyword in API URL or JSON path
    if JOB_KEYWORDS.search(ex.url) or JOB_KEYWORDS.search(cand.json_path):
        score += 10

    # Total count sibling
    total = find_total_count(ex.body, cand.json_path)
    if total is not None:
        score += 10

    # Schema uniformity
    if _schema_uniformity(items) > 0.8:
        score += 10

    # Array size
    if len(items) >= 10:
        score += 5

    # Same origin
    if urlparse(ex.url).netloc == urlparse(page_url).netloc:
        score += 5

    # Penalty: items with < 3 keys (likely config/nav, not jobs)
    avg_keys = sum(len(it) for it in items[:10]) / min(len(items), 10)
    if avg_keys < 3:
        score -= 20

    cand.score = score
    return score


def detect_job_list(exchanges: list[Exchange], page_url: str) -> JobListResult | None:
    """Score all JSON arrays across all exchanges, return the best match."""
    candidates: list[ArrayCandidate] = []

    for ex in exchanges:
        if ex.body is None:
            continue
        arrays = find_arrays(ex.body)
        for path, items in arrays:
            cand = ArrayCandidate(exchange=ex, json_path=path, items=items)
            score_candidate(cand, page_url)
            candidates.append(cand)

    if not candidates:
        return None

    candidates.sort(key=lambda c: c.score, reverse=True)

    for c in candidates[:5]:
        log.debug(
            "api_sniff.candidate",
            score=c.score,
            path=c.json_path,
            items=len(c.items),
            url=c.exchange.url[:100],
        )

    best = candidates[0]
    if best.score < 10:
        log.debug("api_sniff.score_too_low", score=best.score)
        return None

    url_field = find_url_field(best.items)
    total_count = find_total_count(best.exchange.body, best.json_path)

    return JobListResult(
        candidate=best,
        url_field=url_field,
        total_count=total_count,
        pagination=None,
    )


def extract_urls(items: list[dict], url_field: str | None, page_url: str) -> list[str]:
    """Normalize relative->absolute URLs from JSON items."""
    urls = []
    if url_field:
        for item in items:
            val = item.get(url_field)
            if isinstance(val, str) and val:
                urls.append(urljoin(page_url, val))
    else:
        for item in items:
            for val in item.values():
                if _looks_like_url(str(val)):
                    urls.append(urljoin(page_url, str(val)))
                    break
    return urls


# ---------------------------------------------------------------------------
# Pagination inference — pure
# ---------------------------------------------------------------------------


def _diff_query_params(url1: str, url2: str) -> tuple[str, int, int] | None:
    """Find the single query param that changed numerically between two URLs."""
    p1 = parse_qs(urlparse(url1).query)
    p2 = parse_qs(urlparse(url2).query)
    all_keys = set(p1) | set(p2)
    diffs = []
    for key in all_keys:
        v1_raw = p1.get(key, [""])[0]
        v2_raw = p2.get(key, [""])[0]
        if v1_raw == v2_raw:
            continue
        try:
            v1 = int(v1_raw) if v1_raw != "" else None
            v2 = int(v2_raw) if v2_raw != "" else None
        except (ValueError, TypeError):
            continue
        if v1 is None or v2 is None:
            default = PAGINATION_PARAM_DEFAULTS.get(key.lower())
            if default is not None:
                v1 = v1 if v1 is not None else default
                v2 = v2 if v2 is not None else default
            else:
                continue
        diffs.append((key, v1, v2))
    if len(diffs) == 1:
        return diffs[0]
    return None


def _diff_json_bodies(body1: str | None, body2: str | None) -> tuple[str, int, int] | None:
    """Deep-diff two JSON bodies to find the single changed numeric field."""
    if not body1 or not body2:
        return None
    try:
        j1 = json.loads(body1) if isinstance(body1, str) else body1
        j2 = json.loads(body2) if isinstance(body2, str) else body2
    except (json.JSONDecodeError, TypeError):
        return None

    diffs: list[tuple[str, int, int]] = []

    def walk(a: object, b: object, path: str = "") -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in set(a) | set(b):
                walk(a.get(key), b.get(key), f"{path}.{key}" if path else key)
        elif a != b:
            with contextlib.suppress(ValueError, TypeError):
                diffs.append((path, int(a), int(b)))  # type: ignore[arg-type]

    walk(j1, j2)
    if len(diffs) == 1:
        return diffs[0]
    return None


def infer_pagination(
    exchanges: list[Exchange],
    best_url: str,
    page_size: int,
) -> PaginationInfo | None:
    """Infer pagination from two exchanges to the same endpoint, or from URL patterns."""
    parsed = urlparse(best_url)
    matching = [
        ex
        for ex in exchanges
        if urlparse(ex.url).path == parsed.path and urlparse(ex.url).netloc == parsed.netloc
    ]

    # Deduplicate by URL + post_data
    seen_keys: set[tuple[str, str | None]] = set()
    unique_matching: list[Exchange] = []
    for ex in matching:
        key = (ex.url, ex.post_data)
        if key not in seen_keys:
            seen_keys.add(key)
            unique_matching.append(ex)

    if len(unique_matching) >= 2:
        pairs = []
        for i, ex1 in enumerate(unique_matching):
            for ex2 in unique_matching[i + 1 :]:
                priority = 0 if ex1.phase != ex2.phase else 1
                pairs.append((priority, ex1, ex2))
        pairs.sort(key=lambda x: x[0])

        for _, ex1, ex2 in pairs:
            diff = _diff_query_params(ex1.url, ex2.url)
            if diff:
                name, v1, v2 = diff
                inc = abs(v2 - v1)
                style = "offset" if inc == page_size else "page"
                return PaginationInfo(
                    param_name=name,
                    style=style,
                    start_value=min(v1, v2),
                    increment=inc,
                    location="query",
                )

            diff = _diff_json_bodies(ex1.post_data, ex2.post_data)
            if diff:
                name, v1, v2 = diff
                inc = abs(v2 - v1)
                style = "offset" if inc == page_size else "page"
                return PaginationInfo(
                    param_name=name,
                    style=style,
                    start_value=min(v1, v2),
                    increment=inc,
                    location="body",
                )

    # Single exchange fallback: look for obvious params
    best_ex = next(
        (ex for ex in matching if ex.url == best_url),
        matching[0] if matching else None,
    )
    if best_ex is None:
        return None

    qs = parse_qs(urlparse(best_ex.url).query)

    for param in ("offset", "start", "skip", "from"):
        if param in qs:
            try:
                val = int(qs[param][0])
                return PaginationInfo(
                    param_name=param,
                    style="offset",
                    start_value=0,
                    increment=page_size,
                    location="query",
                    observed_value=val,
                )
            except (ValueError, TypeError):
                pass

    for param in ("page", "pageNumber", "p", "pageNo"):
        if param in qs:
            try:
                val = int(qs[param][0])
                return PaginationInfo(
                    param_name=param,
                    style="page",
                    start_value=1,
                    increment=1,
                    location="query",
                    observed_value=val,
                )
            except (ValueError, TypeError):
                pass

    # Check POST body
    if best_ex.post_data:
        try:
            body = json.loads(best_ex.post_data)
            if isinstance(body, dict):
                for param in ("offset", "start", "skip", "from"):
                    if param in body:
                        try:
                            val = int(body[param])
                            return PaginationInfo(
                                param_name=param,
                                style="offset",
                                start_value=0,
                                increment=page_size,
                                location="body",
                                observed_value=val,
                            )
                        except (ValueError, TypeError):
                            pass
                for param in ("page", "pageNumber", "p", "pageNo"):
                    if param in body:
                        try:
                            val = int(body[param])
                            return PaginationInfo(
                                param_name=param,
                                style="page",
                                start_value=1,
                                increment=1,
                                location="body",
                                observed_value=val,
                            )
                        except (ValueError, TypeError):
                            pass
        except (json.JSONDecodeError, TypeError):
            pass

    return None


# ---------------------------------------------------------------------------
# URL / body parameter helpers — pure
# ---------------------------------------------------------------------------


def set_url_param(url: str, param: str, value: object) -> str:
    """Set a single query param in a URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs[param] = [str(value)]
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


def set_body_param(body_str: str, param: str, value: object) -> str:
    """Set a single field in a JSON POST body."""
    try:
        body = json.loads(body_str)
    except (json.JSONDecodeError, TypeError):
        return body_str
    parts = param.split(".")
    obj = body
    for part in parts[:-1]:
        obj = obj[part]
    obj[parts[-1]] = value
    return json.dumps(body)


def detect_size_param(url: str, post_data: str | None) -> tuple[str, str, int] | None:
    """Find a page-size param in the request. Returns (name, location, value)."""
    qs = parse_qs(urlparse(url).query)
    for param in SIZE_PARAMS:
        if param in qs:
            try:
                return (param, "query", int(qs[param][0]))
            except (ValueError, TypeError):
                pass
    if post_data:
        try:
            body = json.loads(post_data)
            if isinstance(body, dict):
                for param in SIZE_PARAMS:
                    if param in body:
                        try:
                            return (param, "body", int(body[param]))
                        except (ValueError, TypeError):
                            pass
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def extract_items(data: object, target_path: str) -> list[dict]:
    """Extract the job array from a parsed JSON response."""
    arrays = find_arrays(data)
    for path, arr in arrays:
        if path == target_path:
            return arr
    # Fallback: largest array
    if arrays:
        return max(arrays, key=lambda x: len(x[1]))[1]
    return []


def clean_headers(headers: dict) -> dict:
    """Strip headers that shouldn't be forwarded in replayed requests."""
    return {k: v for k, v in headers.items() if k.lower() not in _SKIP_HEADERS}


# ---------------------------------------------------------------------------
# Field auto-mapping
# ---------------------------------------------------------------------------


def auto_map_fields(items: list[dict]) -> dict[str, str]:
    """Auto-detect field mapping from sample items.

    Returns a dict mapping DiscoveredJob field names to JSON key paths,
    using the same spec notation as nextdata (``key``, ``nested.key``,
    ``array[].field``).
    """
    if not items:
        return {}
    sample = items[:5]
    mapping: dict[str, str] = {}

    # Collect all top-level keys from sample
    all_keys: set[str] = set()
    for item in sample:
        all_keys.update(item.keys())

    # Simple field matching
    for field_name, pattern in FIELD_PATTERNS.items():
        for key in all_keys:
            if pattern.match(key):
                mapping[field_name] = key
                break

    # Location matching — handles both simple strings and array-of-objects
    for key in all_keys:
        if _LOCATION_KEY_PATTERNS.match(key):
            # Check the type of value in sample items
            sample_vals = [item.get(key) for item in sample if key in item]
            if not sample_vals:
                continue
            first = sample_vals[0]
            if isinstance(first, str):
                mapping["locations"] = key
                break
            if isinstance(first, list):
                if first and isinstance(first[0], str):
                    mapping["locations"] = key
                    break
                if first and isinstance(first[0], dict):
                    # Find the name subfield
                    for subkey in first[0]:
                        if _LOCATION_SUBFIELD_PATTERNS.match(subkey):
                            mapping["locations"] = f"{key}[].{subkey}"
                            break
                    else:
                        # Fall back to first string-valued key
                        for subkey, subval in first[0].items():
                            if isinstance(subval, str):
                                mapping["locations"] = f"{key}[].{subkey}"
                                break
                    break

    # Metadata patterns (team/department)
    for field_name, pattern in _METADATA_PATTERNS.items():
        for key in all_keys:
            if pattern.match(key):
                # Check for nested objects
                sample_vals = [item.get(key) for item in sample if key in item]
                if sample_vals and isinstance(sample_vals[0], dict):
                    # Use .name or first string field
                    inner = sample_vals[0]
                    for subkey in ("name", "title", "label"):
                        if subkey in inner:
                            mapping[field_name] = f"{key}.{subkey}"
                            break
                    else:
                        for subkey, subval in inner.items():
                            if isinstance(subval, str):
                                mapping[field_name] = f"{key}.{subkey}"
                                break
                else:
                    mapping[field_name] = key
                break

    return mapping


# ---------------------------------------------------------------------------
# Playwright-dependent functions
# ---------------------------------------------------------------------------


async def capture_exchanges(page, page_host: str) -> list[Exchange]:
    """Attach a response listener that captures JSON XHR/fetch pairs.

    Call this *before* navigation so that load-time requests are captured.
    Returns a mutable list that grows as responses arrive.
    """
    exchanges: list[Exchange] = []

    async def on_response(resp) -> None:
        req = resp.request
        if req.resource_type not in ("xhr", "fetch"):
            return
        if any(p in req.url.lower() for p in SKIP_PATTERNS):
            return
        ct = resp.headers.get("content-type", "")
        try:
            text = await resp.text()
            if not text or len(text) < 2:
                return
            body = json.loads(text)
        except Exception:
            return
        exchanges.append(
            Exchange(
                method=req.method,
                url=req.url,
                request_headers=dict(req.headers),
                post_data=req.post_data,
                status=resp.status,
                body=body,
                content_type=ct,
                phase="load",
            )
        )

    page.on("response", on_response)
    return exchanges


async def trigger_interactions(page, exchanges: list[Exchange]) -> None:
    """Dismiss overlays and trigger pagination / load-more to capture more exchanges."""
    before = len(exchanges)

    # Phase A: search button click for Taleo/Workday-style pages
    if len(exchanges) <= 1:
        searched = await page.evaluate("""() => {
            const searchBtn = document.querySelector(
                'button[id*="earch"], input[type="submit"], button[type="submit"], '
                + '[class*="search-btn"], [class*="SearchBtn"], [class*="searchButton"], '
                + 'button[class*="search"], [id*="btnSearch"]'
            );
            if (searchBtn) { searchBtn.click(); return 'search-btn'; }
            const allBtns = [...document.querySelectorAll(
                'a, button, input[type="button"]')];
            const srch = allBtns.find(el =>
                /^search$/i.test(el.textContent.trim()) || el.value === 'Search');
            if (srch) { srch.click(); return 'search-text'; }
            return null;
        }""")
        if searched:
            log.debug("api_sniff.search_clicked", type=searched)
            await asyncio.sleep(5)

    before_pagination = len(exchanges)

    # Phase B: Pagination clicks
    clicked = await page.evaluate("""() => {
        const byAria = document.querySelector(
            '[aria-label*="page 2"], [aria-label*="Page 2"], [aria-label="Next"]'
        );
        if (byAria) { byAria.click(); return 'aria'; }
        const allLinks = [...document.querySelectorAll(
            'a, button, span[role="link"], span[tabindex]')];
        const page2 = allLinks.find(el =>
            el.textContent.trim() === '2' &&
            el.closest('[class*="pagin"], [class*="pager"], [role="navigation"]')
        );
        if (page2) { page2.click(); return 'page2'; }
        const next = allLinks.find(el =>
            /^(Next|Load more|Show more|View more)$/i.test(el.textContent.trim())
        );
        if (next) { next.click(); return 'next'; }
        const showMore = allLinks.find(el =>
            /show\\s*more|load\\s*more|view\\s*more/i.test(el.textContent.trim())
        );
        if (showMore) { showMore.click(); return 'show-more'; }
        return null;
    }""")
    if clicked:
        log.debug("api_sniff.pagination_clicked", type=clicked)
        await asyncio.sleep(3)

    # CSS-based fallback
    if len(exchanges) == before_pagination:
        for sel in [
            '[aria-label*="page 2"]',
            '[aria-label*="Page 2"]',
            'a[href*="page=2"]',
            ".pagination li:nth-child(2) a",
            'button:has-text("Next")',
            'a:has-text("Next")',
            '[aria-label="Next"]',
            ".next a",
            ".next button",
            'button:has-text("Load more")',
            'button:has-text("Show more")',
            'button:has-text("View more")',
            'a:has-text("View more")',
        ]:
            try:
                await page.click(sel, timeout=2000, force=True)
            except Exception:
                continue
            await asyncio.sleep(3)
            if len(exchanges) > before_pagination:
                log.debug("api_sniff.css_clicked", selector=sel)
                break

    # Scroll to bottom for infinite-scroll triggers
    if len(exchanges) == before_pagination:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(3)

    for ex in exchanges[before:]:
        ex.phase = "interaction"

    log.debug("api_sniff.interactions", new_exchanges=len(exchanges) - before)


async def fetch_json(
    page,
    method: str,
    url: str,
    headers: dict,
    body: str | None,
) -> object:
    """Execute a fetch inside the browser context, return parsed JSON."""
    text = await page.evaluate(
        """async ([method, url, headers, body]) => {
        const opts = { method, headers: JSON.parse(headers) };
        if (body) opts.body = body;
        const resp = await fetch(url, opts);
        return await resp.text();
    }""",
        [method, url, json.dumps(headers), body],
    )
    return json.loads(text)


async def paginate_all(
    page,
    result: JobListResult,
    max_pages: int,
) -> list[dict]:
    """Replay the API via page.evaluate(fetch(...)) with incremented params."""
    pag = result.pagination
    ex = result.candidate.exchange
    all_items = list(result.candidate.items)

    if pag is None:
        return all_items

    page_size = len(result.candidate.items)
    if page_size == 0:
        return all_items

    headers = clean_headers(ex.request_headers)

    # Try to increase page size
    size_info = detect_size_param(ex.url, ex.post_data)
    if size_info and size_info[2] < _DESIRED_PAGE_SIZE:
        sp_name, sp_loc, _sp_orig = size_info
        probe_url = ex.url
        probe_body = ex.post_data
        if sp_loc == "query":
            probe_url = set_url_param(probe_url, sp_name, _DESIRED_PAGE_SIZE)
        else:
            probe_body = set_body_param(probe_body, sp_name, _DESIRED_PAGE_SIZE)
        if pag.location == "query":
            probe_url = set_url_param(probe_url, pag.param_name, pag.start_value)
        else:
            probe_body = set_body_param(probe_body, pag.param_name, pag.start_value)

        try:
            data = await fetch_json(page, ex.method, probe_url, headers, probe_body)
            probe_items = extract_items(data, result.candidate.json_path)
            if len(probe_items) > page_size:
                log.debug(
                    "api_sniff.page_size_increased",
                    old=page_size,
                    new=len(probe_items),
                )
                page_size = len(probe_items)
                all_items = probe_items
                ex = Exchange(
                    method=ex.method,
                    url=probe_url,
                    request_headers=ex.request_headers,
                    post_data=probe_body,
                    status=ex.status,
                    body=data,
                    content_type=ex.content_type,
                    phase=ex.phase,
                )
                new_total = find_total_count(data, result.candidate.json_path)
                if new_total and new_total > page_size:
                    result.total_count = new_total
                if pag.style == "offset":
                    pag.increment = page_size
        except Exception:
            log.debug("api_sniff.page_size_probe_failed", exc_info=True)

    # Calculate total pages
    if result.total_count and page_size > 0 and result.total_count > page_size:
        total_pages = min(
            (result.total_count + page_size - 1) // page_size,
            max_pages,
        )
    else:
        total_pages = max_pages

    if pag.style == "offset":
        current_value = pag.start_value + pag.increment
    else:
        current_value = pag.start_value + pag.increment

    pages_fetched = 1
    empty_count = 0

    while pages_fetched < total_pages:
        if pag.location == "query":
            fetch_url = set_url_param(ex.url, pag.param_name, current_value)
            fetch_body = ex.post_data
        else:
            fetch_url = ex.url
            fetch_body = set_body_param(ex.post_data, pag.param_name, current_value)

        log.debug(
            "api_sniff.paginate",
            page=pages_fetched + 1,
            param=pag.param_name,
            value=current_value,
        )

        try:
            data = await fetch_json(page, ex.method, fetch_url, headers, fetch_body)
            items = extract_items(data, result.candidate.json_path)

            if not items:
                empty_count += 1
                if empty_count >= 2:
                    log.debug("api_sniff.pagination_stop", reason="empty_pages")
                    break
            else:
                empty_count = 0
                all_items.extend(items)
                if len(items) < page_size:
                    break
        except Exception:
            log.debug("api_sniff.pagination_fetch_failed", exc_info=True)
            break

        pages_fetched += 1
        current_value += pag.increment

    return all_items


async def extract_urls_via_dom_crossref(
    page,
    items: list[dict],
    page_url: str,
) -> list[str]:
    """Cross-reference JSON item IDs with <a href> links on the page.

    When API items have no URL field, find a DOM link that contains an item ID,
    derive the URL template, then construct URLs for ALL items.
    """
    from src.shared.nextdata import resolve_path

    if not items:
        return []
    sample = items[0]

    id_field = None
    for key in sample:
        if ID_FIELDS.match(key):
            id_field = key
            break
    if not id_field:
        return []

    item_ids = [str(it[id_field]) for it in items if id_field in it]
    if not item_ids:
        return []

    dom_links: list[str] = await page.evaluate("""() => {
        return [...document.querySelectorAll('a[href]')].map(a => a.href);
    }""")

    ref_link = None
    ref_id = None
    page_netloc = urlparse(page_url).netloc
    for item_id in item_ids[:10]:
        for href in dom_links:
            if item_id in href and urlparse(href).netloc == page_netloc:
                ref_link = href
                ref_id = item_id
                break
        if ref_link:
            break

    if not ref_link:
        return []

    ref_parsed = urlparse(ref_link)
    ref_path = ref_parsed.path
    id_start = ref_path.find(ref_id)
    if id_start < 0:
        return []

    prefix = ref_path[:id_start]
    after_id = ref_path[id_start + len(ref_id) :]

    ref_item = next((it for it in items if str(it.get(id_field)) == ref_id), None)
    slug_field = None
    if ref_item and after_id.startswith("/"):
        slug_part = after_id[1:]
        for key, val in ref_item.items():
            if isinstance(val, str) and val and slug_part.startswith(val):
                slug_field = key
                break

    ref_qparams = parse_qs(ref_parsed.query)
    query_field_map: dict[str, str] = {}
    if ref_item:
        for qp, qvals in ref_qparams.items():
            qval = qvals[0]
            for key, val in ref_item.items():
                if isinstance(val, str) and val == qval:
                    query_field_map[qp] = key
                elif isinstance(val, dict):
                    for k2, v2 in val.items():
                        if isinstance(v2, str) and v2 == qval:
                            query_field_map[qp] = f"{key}.{k2}"

    urls = []
    for item in items:
        item_id = str(item.get(id_field, ""))
        if not item_id:
            continue
        path = f"{prefix}{item_id}"
        if slug_field:
            slug = str(item.get(slug_field, ""))
            if slug:
                path += f"/{slug}"
        qparts = {}
        for qp, field_path in query_field_map.items():
            val = resolve_path(item, field_path)
            if val is not None:
                qparts[qp] = str(val)
        full_url = urljoin(page_url, path)
        if qparts:
            full_url += "?" + urlencode(qparts)
        urls.append(full_url)

    return urls
