"""API sniffer scraper.

For individual job pages that load content via XHR/fetch when the monitor
returns URL-only results.  Captures JSON responses on page load and extracts
job data from the best single-job response.

Auto-probed via Playwright when ``ws probe scraper`` runs.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import structlog

from src.core.scrapers import JobContent, register
from src.shared.api_sniff import (
    TITLE_FIELDS,
    auto_map_fields,
    capture_exchanges,
)
from src.shared.nextdata import extract_field

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()


# Fields we look for in a single-job JSON response
_DESCRIPTION_FIELDS = (
    "description",
    "body",
    "content",
    "bodyHtml",
    "body_html",
    "descriptionHtml",
    "description_html",
    "text",
    "details",
    "job_description",
    "jobDescription",
    "position_description_html",
    "summary",
)

_LOCATION_FIELDS = (
    "location",
    "locations",
    "locationName",
    "location_name",
    "office",
    "offices",
    "city",
    "cities",
    "place",
)

_EMPLOYMENT_TYPE_FIELDS = (
    "employment_type",
    "employmentType",
    "type",
    "job_type",
    "jobType",
    "work_type",
    "workType",
    "contract_type",
    "contractType",
)

_DATE_FIELDS = (
    "date_posted",
    "datePosted",
    "posted_at",
    "postedAt",
    "published_at",
    "publishedAt",
    "created_at",
    "createdAt",
    "publish_date",
)

_WORKPLACE_TYPE_FIELDS = (
    "job_location_type",
    "jobLocationType",
    "workplace_type",
    "workplaceType",
    "remote_type",
    "locationType",
    "isRemote",
    "remote",
)

# Defaults for Playwright navigation — configurable via scraper_config
_DEFAULT_WAIT = "load"
_DEFAULT_TIMEOUT = 20_000
_DEFAULT_SETTLE = 3  # seconds to wait after navigation for XHRs to complete

_SEEK_MARKETS = {
    "au.seek.com": ("en-AU", "anz-1"),
    "www.seek.com.au": ("en-AU", "anz-1"),
    "nz.seek.com": ("en-NZ", "anz-1"),
    "www.seek.co.nz": ("en-NZ", "anz-1"),
}
_SEEK_JOB_PATH_RE = re.compile(r"^/job/(?P<id>\d+)/?$")
_AJINGA_JOB_PATH_RE = re.compile(r"^/job-description/(?P<id>[A-Za-z0-9_-]{8,64})/?$")
_SEEK_JOB_DETAILS_QUERY = """\
query JobDetails($id: ID!) {
  jobDetails(id: $id) {
    job {
      id
      title
      content
      abstract
      location { label(locale: \"{locale}\") }
      advertiser { id name(locale: \"{locale}\") }
      workTypes { label(locale: \"{locale}\") }
      createdAt { dateTimeUtc }
      expiresAt { dateTimeUtc }
      isExpired
      status
      url(zone: \"{zone}\", locale: \"{locale}\")
    }
  }
}
"""


def _seek_http_config(urls: list[str]) -> dict | None:
    """Return SEEK's public GraphQL detail config for canonical job URLs.

    SEEK's company-profile listing is public, but direct ``/job/<id>``
    navigation can be replaced by a Cloudflare verification page for crawler
    egress. The same detail data is available from the first-party GraphQL
    endpoint used by SEEK's candidate UI. Recognize only canonical AU/NZ SEEK
    job URLs, and require every probe sample to belong to the same market.
    """

    if not urls:
        return None

    market: tuple[str, str] | None = None
    api_host: str | None = None
    for url in urls:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError:
            return None
        host = (parsed.hostname or "").casefold()
        current_market = _SEEK_MARKETS.get(host)
        if (
            parsed.scheme.casefold() != "https"
            or current_market is None
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or _SEEK_JOB_PATH_RE.fullmatch(parsed.path) is None
        ):
            return None
        if market is None:
            market = current_market
            api_host = "au.seek.com" if current_market[0] == "en-AU" else "nz.seek.com"
        elif market != current_market:
            return None

    assert market is not None and api_host is not None
    locale, zone = market
    query = _SEEK_JOB_DETAILS_QUERY.replace("{locale}", locale).replace("{zone}", zone)
    post_body = json.dumps(
        {
            "operationName": "JobDetails",
            "variables": {"id": "{id}"},
            "query": query,
        },
        separators=(",", ":"),
    )
    return {
        "api_url": f"https://{api_host}/graphql",
        "method": "POST",
        "post_body": post_body,
        "url_pattern": r"/job/(?P<id>\d+)",
        "json_path": "data.jobDetails.job",
        "request_headers": {
            "content-type": "application/json",
            "referer": f"https://{api_host}/",
        },
        "fields": {
            "title": "title",
            "description": "content",
            "locations": "location.label",
            "employment_type": "workTypes.label",
            "date_posted": "createdAt.dateTimeUtc",
            "valid_through": "expiresAt.dateTimeUtc",
            "metadata.seek_id": "id",
            "metadata.advertiser": "advertiser.name",
            "metadata.advertiser_id": "advertiser.id",
            "metadata.abstract": "abstract",
            "metadata.status": "status",
            "metadata.is_expired": "isExpired",
        },
    }


def _ajinga_http_config(urls: list[str]) -> dict | None:
    """Return Ajinga's public detail-API config for short-ID job URLs.

    Ajinga's desktop job pages are client-rendered and may redirect between
    legacy and company-scoped routes. Its public mobile route keeps the stable
    ``short_unique_id`` in the URL, which the same first-party SPA sends to the
    JSON detail endpoint. Restrict this preset to canonical HTTPS Ajinga URLs
    so unrelated ``job-description`` routes never receive Ajinga requests.
    """

    if not urls:
        return None

    for url in urls:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme.casefold() != "https"
            or (parsed.hostname or "").casefold() not in {"ajinga.com", "www.ajinga.com"}
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or _AJINGA_JOB_PATH_RE.fullmatch(parsed.path) is None
        ):
            return None

    return {
        "api_url": "https://www.ajinga.com/django_rest/job-detail/info/{id}/",
        "method": "GET",
        "url_pattern": _AJINGA_JOB_PATH_RE.pattern,
        "json_path": "data.data.job",
        "fields": {
            "title": "en_title || cn_title",
            "description": "description || cn_description",
            "locations": "cities[].name",
            "employment_type": {
                "path": "role_type",
                "map": {
                    "F": "full_time",
                    "P": "part_time",
                    "I": "internship",
                    "T": "temporary",
                },
            },
            "metadata.ajinga_id": "id",
            "metadata.short_unique_id": "short_unique_id",
            "metadata.company": "company.i18n_name",
            "metadata.experience": "experience",
            "metadata.compensation": "compensation.en_name || compensation.cn_name",
            "metadata.work_time": "work_time.en_name || work_time.cn_name",
            "metadata.updated_at": "updated_time",
        },
    }


def _find_single_job(exchanges: list, *, json_path: str | None = None) -> dict | None:
    """Find the best JSON response containing single-job data.

    Looks for a dict with title + description-like keys (not an array of jobs).

    When *json_path* is set (jmespath expression), each response body is
    navigated via that path before scoring.  This handles APIs that nest
    the job object under a non-standard key (e.g. ``data.job_post_detail``).
    """
    import jmespath as _jmespath

    candidates: list[tuple[dict, int]] = []

    for ex in exchanges:
        if ex.body is None:
            continue

        body = ex.body

        # When json_path is configured, try navigating each response directly
        if json_path and isinstance(body, dict):
            resolved = _jmespath.search(json_path, body)
            if isinstance(resolved, dict):
                s = _score_job_object(resolved)
                if s > 0:
                    candidates.append((resolved, s + 50))  # strong preference
                    continue

        if isinstance(body, dict):
            score = _score_job_object(body)
            if score > 0:
                candidates.append((body, score))

            # Check nested: common patterns like {data: {...}} or {result: {...}}
            for key in (
                "data",
                "result",
                "job",
                "posting",
                "position",
                "details",
                "funnel",
            ):
                nested = body.get(key)
                if isinstance(nested, dict):
                    s = _score_job_object(nested)
                    if s > 0:
                        candidates.append((nested, s))
                    # Check two-level nesting: data.jobPosting, data.job, etc.
                    for subkey in ("jobPosting", "job", "posting", "position", "result"):
                        deep = nested.get(subkey)
                        if isinstance(deep, dict):
                            s2 = _score_job_object(deep)
                            if s2 > 0:
                                candidates.append((deep, s2))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _score_job_object(obj: dict) -> int:
    """Score a dict as a single job object."""
    score = 0
    keys = set(obj.keys())

    # Must have a title-like field
    has_title = any(TITLE_FIELDS.match(k) for k in keys)
    if not has_title:
        return 0

    score += 10

    # Description field
    for f in _DESCRIPTION_FIELDS:
        if f in keys:
            val = obj[f]
            if isinstance(val, str) and len(val) > 50:
                score += 20
                break

    # Location field
    for f in _LOCATION_FIELDS:
        if f in keys:
            score += 5
            break

    # Enough keys to be a real object
    if len(keys) >= 5:
        score += 5

    # Arrays of dicts with many items is probably a list page, not single job
    for val in obj.values():
        if isinstance(val, list) and len(val) > 10:
            dicts = [x for x in val if isinstance(x, dict)]
            if len(dicts) > 10:
                score -= 15

    return score


def _extract_from_object(obj: dict, config: dict) -> JobContent:
    """Extract JobContent fields from a single job object."""
    fields_map: dict[str, str] = config.get("fields") or {}

    if fields_map:
        return _extract_with_mapping(obj, fields_map)
    return _extract_heuristic(obj)


def _extract_with_mapping(obj: dict, fields_map: dict[str, str]) -> JobContent:
    """Extract using explicit field mapping."""
    kwargs: dict = {}
    metadata_fields: dict = {}
    extras: dict = {}

    for target, spec in fields_map.items():
        value = extract_field(obj, spec)
        if value is None:
            continue
        if target.startswith("metadata."):
            metadata_fields[target.removeprefix("metadata.")] = value
        elif target == "locations":
            kwargs["locations"] = value if isinstance(value, list) else [value]
        elif target in ("skills", "responsibilities", "qualifications"):
            items = value if isinstance(value, list) else [value]
            items = [it for it in items if it and (not isinstance(it, str) or it.strip())]
            if items:
                extras[target] = items
        elif target == "valid_through":
            extras["valid_through"] = value
        elif target in (
            "title",
            "description",
            "employment_type",
            "job_location_type",
            "date_posted",
            "base_salary",
        ):
            kwargs[target] = value
        else:
            metadata_fields[target] = value

    if metadata_fields:
        kwargs["metadata"] = metadata_fields
    if extras:
        kwargs["extras"] = extras

    return JobContent(**kwargs)


def _extract_heuristic(obj: dict) -> JobContent:
    """Extract fields using heuristic key matching."""
    kwargs: dict = {}
    keys = set(obj.keys())

    # Title
    for k in keys:
        if TITLE_FIELDS.match(k):
            val = obj[k]
            if isinstance(val, str):
                kwargs["title"] = val
            break

    # Description
    for f in _DESCRIPTION_FIELDS:
        if f in keys:
            val = obj[f]
            if isinstance(val, str) and len(val) > 10:
                kwargs["description"] = val
                break

    # Locations
    for f in _LOCATION_FIELDS:
        if f in keys:
            val = obj[f]
            if isinstance(val, str):
                kwargs["locations"] = [val]
            elif isinstance(val, list):
                if val and isinstance(val[0], str):
                    kwargs["locations"] = val
                elif val and isinstance(val[0], dict):
                    names = []
                    for loc in val:
                        for subkey in ("name", "title", "city", "label", "displayName"):
                            if subkey in loc and isinstance(loc[subkey], str):
                                names.append(loc[subkey])
                                break
                    if names:
                        kwargs["locations"] = names
            break

    # Employment type
    for f in _EMPLOYMENT_TYPE_FIELDS:
        if f in keys:
            val = obj[f]
            if isinstance(val, str):
                kwargs["employment_type"] = val
            break

    # Date posted
    for f in _DATE_FIELDS:
        if f in keys:
            val = obj[f]
            if isinstance(val, str):
                kwargs["date_posted"] = val
            break

    # Workplace type
    for f in _WORKPLACE_TYPE_FIELDS:
        if f in keys:
            val = obj[f]
            if isinstance(val, (str, bool)):
                kwargs["job_location_type"] = str(val)
            break

    return JobContent(**kwargs)


async def _probe_http_preset(
    urls: list[str],
    config: dict,
    *,
    label: str,
) -> tuple[dict | None, str]:
    """Verify a provider preset against sampled jobs through direct HTTP."""

    from src.shared.http import create_http_client
    from src.shared.tdm import TDMReservedError

    quality_fields = (
        "title",
        "description",
        "locations",
        "employment_type",
        "job_location_type",
        "date_posted",
    )
    http = create_http_client()
    try:
        raw_results = await asyncio.gather(
            *[_scrape_http(url, config, http) for url in urls],
            return_exceptions=True,
        )
    finally:
        await http.aclose()

    for result in raw_results:
        if isinstance(result, TDMReservedError):
            raise result

    contents = [result for result in raw_results if isinstance(result, JobContent)]
    detected_contents = [
        item for item in contents if item.title and item.description and item.locations
    ]
    detected = len(detected_contents)
    total = len(urls)
    if detected == 0 or detected / total < 0.5:
        return None, f"{label} not detected ({detected}/{total} complete jobs)"

    field_counts = {
        field: sum(bool(getattr(item, field, None)) for item in detected_contents)
        for field in quality_fields
    }
    metadata = {
        "config": config,
        "total": detected,
        "titles": field_counts["title"],
        "descriptions": field_counts["description"],
        "locations": field_counts["locations"],
        "fields": {field: count for field, count in field_counts.items() if count > 0},
    }
    comment = (
        f"{label} — "
        f"{field_counts['title']}/{detected} titles, "
        f"{field_counts['description']}/{detected} desc, "
        f"{field_counts['locations']}/{detected} locations"
    )
    return metadata, comment


async def _probe_seek_http(urls: list[str], config: dict) -> tuple[dict | None, str]:
    """Verify the SEEK preset against each sampled job through direct HTTP."""

    return await _probe_http_preset(urls, config, label="SEEK GraphQL")


async def _probe_ajinga_http(urls: list[str], config: dict) -> tuple[dict | None, str]:
    """Verify the Ajinga preset against each sampled job through direct HTTP."""

    return await _probe_http_preset(urls, config, label="Ajinga detail API")


async def probe_pw(
    urls: list[str],
    pw,
) -> tuple[dict | None, str]:
    """Probe sample URLs via Playwright to detect single-job API responses.

    Opens each URL, captures XHR/fetch exchanges, and checks for single-job
    JSON responses.  Returns ``(metadata, comment)`` or ``(None, comment)``.
    """
    seek_config = _seek_http_config(urls)
    if seek_config is not None:
        # Do not navigate to SEEK detail pages: Cloudflare may replace them
        # with a 403 verification document even though the public first-party
        # GraphQL detail endpoint remains available.
        return await _probe_seek_http(urls, seek_config)

    ajinga_config = _ajinga_http_config(urls)
    if ajinga_config is not None:
        # Ajinga's SPA does not expose useful static HTML and its redirects can
        # hide the detail XHR from the generic capture path. Probe the public
        # first-party JSON endpoint directly instead.
        return await _probe_ajinga_http(urls, ajinga_config)

    from src.shared.browser import BrowserNavigationHTTPStatusError, navigate, open_page

    wait = _DEFAULT_WAIT
    timeout = _DEFAULT_TIMEOUT
    settle = _DEFAULT_SETTLE

    _QUALITY_FIELDS = [
        "title",
        "description",
        "locations",
        "employment_type",
        "job_location_type",
        "date_posted",
    ]

    async def _probe_one(url: str) -> tuple[JobContent | None, dict | None]:
        """Probe a single URL, return (content, job_obj) or (None, None)."""
        try:
            async with open_page(pw, {}, target_url=url) as page:
                page_host = urlparse(url).netloc
                exchanges = await capture_exchanges(page, page_host)
                await navigate(page, url, {"wait": wait, "timeout": timeout})
                await asyncio.sleep(settle)

                job_obj = _find_single_job(exchanges)
                if job_obj is None:
                    return None, None
                return _extract_heuristic(job_obj), job_obj
        except BrowserNavigationHTTPStatusError:
            raise
        except Exception:
            log.debug("api_sniffer_scraper.probe_pw_error", url=url, exc_info=True)
            return None, None

    # Probe all URLs concurrently
    results = await asyncio.gather(*[_probe_one(u) for u in urls])

    total = len(urls)
    detected = 0
    field_counts: dict[str, int] = {f: 0 for f in _QUALITY_FIELDS}
    sample_config: dict | None = None

    for content, job_obj in results:
        if content is None:
            continue
        detected += 1
        for f in _QUALITY_FIELDS:
            if getattr(content, f, None):
                field_counts[f] += 1
        if sample_config is None and job_obj is not None:
            sample_config = {"fields": auto_map_fields([job_obj])}

    # Require >= 50% of pages to have detected job data
    if detected == 0 or detected / total < 0.5:
        return None, f"Not detected ({detected}/{total} pages had XHR job data)"

    core_parts = [
        f"{field_counts['title']}/{detected} titles",
        f"{field_counts['description']}/{detected} desc",
        f"{field_counts['locations']}/{detected} locations",
    ]
    comment = ", ".join(core_parts)

    metadata: dict = {
        "config": sample_config or {},
        "total": detected,
        "titles": field_counts["title"],
        "descriptions": field_counts["description"],
        "locations": field_counts["locations"],
        "fields": {f: c for f, c in field_counts.items() if c > 0},
    }

    return metadata, comment


async def _scrape_http(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
) -> JobContent:
    """Scrape via direct HTTP call to a detail API endpoint.

    Used when ``api_url`` is configured — no Playwright needed.
    Constructs the request body from ``post_body`` (with placeholders
    filled from the job URL) and navigates the response via ``json_path``
    before extracting fields. ``url_pattern`` may provide named capture
    groups for IDs stored outside the final URL path segment.
    """
    from src.core.monitors.api_sniffer import clean_headers, http_fetch_with_retry
    from src.shared.http_retry import PaginationFetchError

    api_url = config["api_url"]
    method = config.get("method", "GET")
    request_headers = config.get("request_headers") or config.get("headers") or {}
    headers = clean_headers(request_headers)
    json_path = config.get("json_path")

    # Substitute placeholders in api_url and post_body from the job URL.
    # url_pattern (regex with named groups) extracts values from the URL;
    # {id} is always available as the last path segment for convenience.
    url_pattern = config.get("url_pattern")
    placeholders: dict[str, str] = {}
    if url_pattern:
        m = re.search(url_pattern, url)
        if m:
            placeholders = m.groupdict()
    if "id" not in placeholders:
        path = urlparse(url).path.rstrip("/")
        last_seg = path.rsplit("/", 1)[-1]
        if last_seg:
            placeholders["id"] = last_seg

    for key, val in placeholders.items():
        api_url = api_url.replace(f"{{{key}}}", val)

    post_body = config.get("post_body") or config.get("post_data")
    if post_body:
        for key, val in placeholders.items():
            post_body = post_body.replace(f"{{{key}}}", val)

    auth_request = config.get("auth_request")
    if auth_request is not None:
        if not isinstance(auth_request, dict):
            raise ValueError("api_sniffer auth_request must be a mapping")
        auth_url = auth_request.get("api_url")
        header_fields = auth_request.get("header_fields")
        if not isinstance(auth_url, str) or not auth_url:
            raise ValueError("api_sniffer auth_request requires api_url")
        if not isinstance(header_fields, dict) or not header_fields or len(header_fields) > 32:
            raise ValueError(
                "api_sniffer auth_request requires a bounded non-empty header_fields mapping"
            )
        auth_method = auth_request.get("method", "GET")
        if auth_method not in {"GET", "POST"}:
            raise ValueError("api_sniffer auth_request method must be GET or POST")
        auth_headers = clean_headers(auth_request.get("request_headers") or {})
        auth_body = auth_request.get("post_body") or auth_request.get("post_data")
        if auth_body is not None and not isinstance(auth_body, str):
            raise ValueError("api_sniffer auth_request post body must be a string")
        auth_data = await http_fetch_with_retry(
            http,
            auth_method,
            auth_url,
            auth_headers,
            auth_body,
            raise_non_retryable=True,
        )
        auth_path = auth_request.get("json_path")
        if auth_path:
            import jmespath as _jmespath

            auth_data = _jmespath.search(auth_path, auth_data)
        if not isinstance(auth_data, dict):
            raise RuntimeError("api_sniffer auth_request returned no header data")
        for header_name, field_spec in header_fields.items():
            if (
                not isinstance(header_name, str)
                or not header_name
                or "\r" in header_name
                or "\n" in header_name
                or not isinstance(field_spec, (str, list, dict))
            ):
                raise ValueError("api_sniffer auth_request header_fields is invalid")
            header_value = extract_field(auth_data, field_spec)
            if header_value is None or isinstance(header_value, list):
                raise RuntimeError(
                    f"api_sniffer auth_request omitted scalar field for {header_name!r}"
                )
            normalized_value = str(header_value)
            if "\r" in normalized_value or "\n" in normalized_value:
                raise ValueError("api_sniffer auth_request produced an invalid header value")
            headers[header_name] = normalized_value

    try:
        data = await http_fetch_with_retry(
            http,
            method,
            api_url,
            headers,
            post_body,
            raise_non_retryable=True,
        )
    except PaginationFetchError as exc:
        log.warning(
            "api_sniffer_scraper.http_fetch_failed",
            url=url,
            error_class=type(exc).__name__,
            attempts=exc.attempts,
            last_status=exc.last_status,
            last_error=exc.last_error,
        )
        raise
    if data is None:
        log.info("api_sniffer_scraper.empty_result", url=url, reason="gone")
        return JobContent()

    # Navigate to job object via json_path
    if json_path:
        import jmespath as _jmespath

        data = _jmespath.search(json_path, data)

    if not isinstance(data, dict):
        # Distinguish "requisition gone" (json_path resolved to None, e.g.
        # `items[0]` against `items: []`) from "scraper got unexpected shape".
        # Oracle HCM returns 200 + empty items for closed requisitions — that's
        # the steady-state churn for large boards, not a scraper bug. Keep
        # WARN for genuinely unexpected payloads so real regressions stand out.
        if data is None:
            log.info("api_sniffer_scraper.empty_result", url=url)
        else:
            log.warning(
                "api_sniffer_scraper.no_job_data",
                url=url,
                shape=type(data).__name__,
            )
        return JobContent()

    return _extract_from_object(data, config)


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    pw=None,
    **kwargs,
) -> JobContent:
    """Scrape a single job page by capturing XHR/fetch JSON responses.

    - **HTTP mode** (config has ``api_url``): direct httpx call — no
      Playwright needed.  Same pattern as the monitor's HTTP mode.
    - **Capture mode** (default): opens page in Playwright and captures
      XHR/fetch JSON responses.
    """
    # HTTP mode — direct API call, no browser
    if config.get("api_url"):
        return await _scrape_http(url, config, http)

    from src.shared.browser import (
        BROWSER_KEYS,
        NAVIGATE_KEYS,
        BrowserNavigationHTTPStatusError,
        navigate,
        open_page,
    )

    use_proxy = bool(config.get("proxy"))

    async def _do_scrape(p):
        settle = config.get("settle", _DEFAULT_SETTLE)
        # Narrow projection: wait / wait_fallback / timeout / actions.
        nav_config = {k: v for k, v in config.items() if k in NAVIGATE_KEYS}
        nav_config.setdefault("wait", _DEFAULT_WAIT)
        nav_config.setdefault("timeout", _DEFAULT_TIMEOUT)
        # Forward launch-time browser keys (channel, persistent_context,
        # user_agent, warmup_url, cookies, etc.) so scraper configs can
        # reuse the same Akamai-bypass shape as the monitor. This used to
        # pass {} and silently dropped keys like persistent_context.
        browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}

        async with open_page(
            p,
            browser_config,
            use_proxy=use_proxy,
            target_url=url,
        ) as page:
            page_host = urlparse(url).netloc
            exchanges = await capture_exchanges(page, page_host)

            await navigate(page, url, nav_config)
            await asyncio.sleep(settle)

            json_path = config.get("json_path")
            job_obj = _find_single_job(exchanges, json_path=json_path)
            if job_obj is None:
                log.warning("api_sniffer_scraper.no_job_data", url=url)
                return JobContent()

            return _extract_from_object(job_obj, config)

    try:
        if pw is not None:
            return await _do_scrape(pw)

        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            return await _do_scrape(p)

    except BrowserNavigationHTTPStatusError:
        raise
    except Exception:
        log.error("api_sniffer_scraper.failed", url=url, exc_info=True)
        return JobContent()


register("api_sniffer", scrape, probe_pw=probe_pw, needs_browser=True)
