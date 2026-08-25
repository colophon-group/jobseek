"""SmartRecruiters Posting API monitor.

Public API:
  List:   GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=0

The list endpoint returns posting IDs and metadata.  The monitor constructs
posting URLs from the token + ID and returns a URL set.  Detail fetching
is handled by the scraper on the daily schedule.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register, slugs_from_url
from src.core.monitors._ats_template import ProbeResult, ats_can_handle
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
PAGE_SIZE = 100

# SmartRecruiters exposes a stable requisition ``jobId`` only on its detail
# endpoint.  A single requisition can have multiple language publications,
# while a reused requisition can also carry genuinely distinct locations.
# This opt-in contract therefore uses ``jobId`` plus provider-owned location
# identity, never refNumber/title/address text.  The hash is placed on the
# real, fetchable official careers listing, following the repository's
# existing private-query identity pattern (response fingerprints).
CANONICAL_IDENTITY_JOB_LOCATION_V1 = "job-location-v1"
CANONICAL_IDENTITY_QUERY_PARAM = "_jobseek_sr_identity"
CANONICAL_IDENTITY_QUERY_PREFIX = "v1."
MAX_CANONICAL_POSTINGS = 500
_DETAIL_CONCURRENCY = 12
_LIST_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
_DETAIL_RESPONSE_MAX_BYTES = 1024 * 1024
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_COORD_RE = re.compile(r"-?(?:0|[1-9]\d{0,2})(?:\.\d{1,16})?")
_LANGUAGE_RE = re.compile(r"[a-z]{2}")

# These are tenant-defined IDs, not labels.  Their valueIds remain invariant
# when SmartRecruiters renders a publication in another language.
_BRAND_FIELD_ID = "642d47ae571c9c5746eeeec4"
_DEPARTMENT_FIELD_ID = "642d47ae571c9c5746eeeec5"

# Pagination retry budget. Symmetric with workday (#2748), lever (#2749),
# api_sniffer (#2733), accenture (#2735) and PCSX (#2734): 3 total
# attempts, exponential backoff with full jitter starting at 0.5s.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5

_PAGE_PATTERNS = [
    re.compile(r"api\.smartrecruiters\.com/v1/companies/([\w-]+)"),
    re.compile(r"jobs\.smartrecruiters\.com/([\w-]+)"),
    re.compile(r"careers\.smartrecruiters\.com/([\w-]+)"),
]

_IGNORE_TOKENS = frozenset({"api", "v1", "js", "css", "assets", "postings", "companies"})
_HTML_SIGNAL_RE = re.compile(r"\b(?:smartrecruiters\.com|smartrecruiters)\b", re.IGNORECASE)
_CUSTOM_API_SIGNAL_RE = re.compile(r'["\'`]/?api/jobs(?:[?"\'`])', re.IGNORECASE)
_ONECLICK_TOKEN_RE = re.compile(
    r"(?:jobs|careers)\.smartrecruiters\.com/oneclick-ui/company/([\w-]+)/(?:publication|job)/",
    re.IGNORECASE,
)


def _is_smartrecruiters_host(host: str) -> bool:
    return host == "smartrecruiters.com" or host.endswith(".smartrecruiters.com")


def _has_smartrecruiters_signal(url: str, html: str | None) -> bool:
    """Return True when URL or page HTML indicates SmartRecruiters presence."""
    host = (urlparse(url).hostname or "").lower()
    if _is_smartrecruiters_host(host):
        return True
    if not html:
        return False
    return bool(_HTML_SIGNAL_RE.search(html))


def _token_from_url(board_url: str) -> str | None:
    """Extract company identifier from a SmartRecruiters URL."""
    for pattern in _PAGE_PATTERNS:
        match = pattern.search(board_url)
        if match:
            token = match.group(1)
            if token not in _IGNORE_TOKENS:
                return token
    return None


def _api_list_url(token: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{token}/postings"


def _posting_url(token: str, posting_id: str) -> str:
    """Build a canonical posting URL from token + ID."""
    return f"https://jobs.smartrecruiters.com/{token}/{posting_id}"


def _canonical_identity_enabled(metadata: dict) -> bool:
    value = metadata.get("canonical_identity")
    if value is None:
        return False
    if value != CANONICAL_IDENTITY_JOB_LOCATION_V1:
        raise ValueError(
            f"SmartRecruiters canonical_identity must be {CANONICAL_IDENTITY_JOB_LOCATION_V1!r}"
        )
    return True


def _normalize_coordinate(value: object, *, latitude: bool) -> str:
    """Validate and normalize one provider coordinate without rounding it."""
    name = "latitude" if latitude else "longitude"
    if not isinstance(value, str) or not _COORD_RE.fullmatch(value):
        raise ValueError(f"SmartRecruiters {name} must be a bounded decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:  # defensive; regex already excludes these
        raise ValueError(f"SmartRecruiters {name} must be a decimal") from exc
    limit = Decimal(90 if latitude else 180)
    if not number.is_finite() or number < -limit or number > limit:
        raise ValueError(f"SmartRecruiters {name} is outside its geographic range")
    normalized = format(number.normalize(), "f")
    return "0" if Decimal(normalized) == 0 else normalized


def _custom_value_id(detail: dict, field_id: str) -> str | None:
    values: set[str] = set()
    custom_fields = detail.get("customField")
    if not isinstance(custom_fields, list):
        return None
    for field in custom_fields:
        if not isinstance(field, dict) or field.get("fieldId") != field_id:
            continue
        value = field.get("valueId")
        if isinstance(value, str) and 0 < len(value) <= 128 and "\x00" not in value:
            values.add(value)
    if len(values) > 1:
        raise ValueError(f"SmartRecruiters field {field_id} has conflicting valueIds")
    return next(iter(values), None)


def _stable_location_identity(detail: dict) -> tuple[str, str]:
    """Return ``(kind, value)`` using only nonlocalized provider fields.

    Exact coordinates are preferred.  The fallback is limited to provider
    IDs and geographic codes; localized city/address/fullLocation strings are
    intentionally excluded.  Multiple publications sharing one jobId are not
    allowed to use the fallback because it cannot prove that two same-campus
    jobs are translations rather than distinct vacancies.
    """
    location = detail.get("location")
    if not isinstance(location, dict):
        raise ValueError("SmartRecruiters canonical identity requires a location mapping")
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if (latitude is None) != (longitude is None):
        raise ValueError("SmartRecruiters canonical identity requires both coordinates")
    if latitude is not None:
        lat = _normalize_coordinate(latitude, latitude=True)
        lon = _normalize_coordinate(longitude, latitude=False)
        return "geo", f"{lat},{lon}"

    country = location.get("country")
    postal_code = location.get("postalCode")
    if not isinstance(country, str) or re.fullmatch(r"[A-Za-z]{2}", country) is None:
        raise ValueError("SmartRecruiters location fallback requires a country code")
    if (
        not isinstance(postal_code, str)
        or not postal_code.strip()
        or len(postal_code) > 32
        or "\x00" in postal_code
    ):
        raise ValueError("SmartRecruiters location fallback requires a postal code")
    brand_id = _custom_value_id(detail, _BRAND_FIELD_ID)
    department_value_id = _custom_value_id(detail, _DEPARTMENT_FIELD_ID)
    department = detail.get("department")
    department_id = department.get("id") if isinstance(department, dict) else None
    department_id = str(department_id) if department_id is not None else ""
    if not brand_id or not department_value_id or not department_id:
        raise ValueError(
            "SmartRecruiters location fallback requires brand and department provider IDs"
        )
    payload = {
        "brand": brand_id,
        "country": country.lower(),
        "department": department_id,
        "department_value": department_value_id,
        "hybrid": location.get("hybrid") is True,
        "postal_code": postal_code.strip().casefold(),
        "remote": location.get("remote") is True,
    }
    return "provider-codes", json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _canonical_identity_components(detail: dict) -> tuple[str, str, str]:
    job_id = detail.get("jobId")
    if not isinstance(job_id, str) or _UUID_RE.fullmatch(job_id) is None:
        raise ValueError("SmartRecruiters canonical identity requires a UUID jobId")
    location_kind, location_value = _stable_location_identity(detail)
    return job_id.lower(), location_kind, location_value


def _canonical_source_url(token: str, components: tuple[str, str, str]) -> str:
    """Build a collision-resistant identity on a fetchable official URL."""
    if _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("SmartRecruiters token is unsafe for canonical identity URLs")
    job_id, location_kind, location_value = components
    encoded = json.dumps(
        {
            "location_kind": location_kind,
            "location_value": location_value,
            "provider": "smartrecruiters",
            "tenant": token,
            "version": 1,
            "job_id": job_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    query = urlencode(
        {CANONICAL_IDENTITY_QUERY_PARAM: f"{CANONICAL_IDENTITY_QUERY_PREFIX}{digest}"}
    )
    return f"https://careers.smartrecruiters.com/{quote(token, safe='')}?{query}"


def _detail_url(token: str, posting_id: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}"


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
    max_bytes: int | None = None,
) -> dict:
    """GET a SmartRecruiters list-API page with bounded retries (#2749)."""
    return await fetch_json_page_with_retry(
        client,
        url,
        params=params,
        expect_shape=dict,
        retries=retries,
        base_delay=base_delay,
        max_bytes=max_bytes,
        log_event="smartrecruiters.list_backoff",
        sleep=asyncio.sleep,
    )


async def _get_detail_with_retry(
    client: httpx.AsyncClient,
    token: str,
    posting_id: str,
) -> dict:
    """Fetch one identity-bearing detail or fail the complete monitor cycle."""
    return await fetch_json_page_with_retry(
        client,
        _detail_url(token, posting_id),
        expect_shape=dict,
        retries=_RETRY_ATTEMPTS,
        base_delay=_RETRY_BASE_DELAY,
        max_bytes=_DETAIL_RESPONSE_MAX_BYTES,
        log_event="smartrecruiters.detail_backoff",
        sleep=asyncio.sleep,
    )


async def _list_posting_items(
    client: httpx.AsyncClient,
    token: str,
) -> list[dict]:
    """Return an exact, unique list inventory for canonical identity mode."""
    list_url = _api_list_url(token)
    items: list[dict] = []
    seen_ids: set[str] = set()
    expected_total: int | None = None
    offset = 0
    while True:
        data = await _get_page_with_retry(
            client,
            list_url,
            {"limit": PAGE_SIZE, "offset": offset},
            max_bytes=_LIST_RESPONSE_MAX_BYTES,
        )
        total_found = data.get("totalFound")
        content = data.get("content")
        if (
            not isinstance(total_found, int)
            or isinstance(total_found, bool)
            or total_found < 0
            or total_found > MAX_CANONICAL_POSTINGS
        ):
            raise ValueError(
                "SmartRecruiters canonical identity requires an exact total no greater than "
                f"{MAX_CANONICAL_POSTINGS}"
            )
        if expected_total is None:
            expected_total = total_found
        elif total_found != expected_total:
            raise ValueError("SmartRecruiters advertised total changed during pagination")
        if not isinstance(content, list):
            raise ValueError("SmartRecruiters list response omitted its content list")
        for item in content:
            if not isinstance(item, dict):
                raise ValueError("SmartRecruiters list response contained a non-object posting")
            posting_id = item.get("id")
            if (
                not isinstance(posting_id, str)
                or not posting_id.isdecimal()
                or len(posting_id) > 32
            ):
                raise ValueError("SmartRecruiters list posting omitted a bounded numeric id")
            if posting_id in seen_ids:
                raise ValueError("SmartRecruiters list repeated one publication id")
            seen_ids.add(posting_id)
            items.append(item)
        if len(items) >= total_found:
            break
        if not content:
            raise ValueError("SmartRecruiters pagination ended below its advertised total")
        offset += PAGE_SIZE
    if len(items) != expected_total:
        raise ValueError(
            f"SmartRecruiters returned {len(items)} publications but advertised {expected_total}"
        )
    return items


def _stable_mapping_subset(
    listed: object,
    detail: object,
    *,
    name: str,
    normalize_id: bool = False,
) -> None:
    """Require every list-snapshot field to agree with the detail snapshot."""
    if not isinstance(listed, dict) or not isinstance(detail, dict) or not listed:
        raise ValueError(f"SmartRecruiters {name} overlap must be nonempty mappings")
    for key, listed_value in listed.items():
        if key not in detail:
            raise ValueError(f"SmartRecruiters detail omitted listed {name}.{key}")
        detail_value = detail[key]
        if normalize_id and key == "id":
            listed_value = str(listed_value)
            detail_value = str(detail_value)
        if listed_value != detail_value:
            raise ValueError(f"SmartRecruiters list/detail drifted at {name}.{key}")


def _stable_custom_field_ids(value: object, *, source: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"SmartRecruiters {source} customField must be a nonempty list")
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field in value:
        if not isinstance(field, dict):
            raise ValueError(f"SmartRecruiters {source} customField contained a non-object")
        field_id = field.get("fieldId")
        value_id = field.get("valueId")
        if (
            not isinstance(field_id, str)
            or not field_id
            or len(field_id) > 128
            or not isinstance(value_id, str)
            or not value_id
            or len(value_id) > 128
        ):
            raise ValueError(f"SmartRecruiters {source} customField omitted provider IDs")
        if field_id in seen:
            raise ValueError(f"SmartRecruiters {source} repeated a custom field ID")
        seen.add(field_id)
        pairs.append((field_id, value_id))
    return tuple(sorted(pairs))


def _validate_detail_identity(token: str, listed: dict, detail: dict) -> None:
    posting_id = listed["id"]
    company = detail.get("company")
    if detail.get("id") != posting_id:
        raise ValueError("SmartRecruiters detail publication id did not match its list item")
    if not isinstance(company, dict) or company.get("identifier") != token:
        raise ValueError("SmartRecruiters detail company did not match its listed tenant")
    if detail.get("active") is not True:
        raise ValueError("SmartRecruiters detail publication was not active")
    if detail.get("visibility") != "PUBLIC":
        raise ValueError("SmartRecruiters detail visibility was not public")

    for key in (
        "uuid",
        "jobAdId",
        "defaultJobAd",
        "refNumber",
        "releasedDate",
        "visibility",
    ):
        if key not in listed or key not in detail or listed[key] != detail[key]:
            raise ValueError(f"SmartRecruiters list/detail drifted at {key}")
    for key in ("uuid", "jobAdId"):
        if not isinstance(listed[key], str) or _UUID_RE.fullmatch(listed[key]) is None:
            raise ValueError(f"SmartRecruiters listed {key} must be a UUID")
    if not isinstance(listed["defaultJobAd"], bool):
        raise ValueError("SmartRecruiters listed defaultJobAd must be boolean")
    for key in ("refNumber", "releasedDate"):
        if not isinstance(listed[key], str) or not listed[key] or len(listed[key]) > 128:
            raise ValueError(f"SmartRecruiters listed {key} must be a bounded string")

    listed_ref = listed.get("ref")
    if listed_ref != _detail_url(token, posting_id):
        raise ValueError("SmartRecruiters listed ref did not match its publication endpoint")

    _stable_mapping_subset(listed.get("company"), company, name="company")
    _stable_mapping_subset(listed.get("language"), detail.get("language"), name="language")
    _stable_mapping_subset(listed.get("location"), detail.get("location"), name="location")
    # Department/custom-field values are identity-bearing only for the
    # coordinate-free fallback. SmartRecruiters' list API is observably stale
    # for these mutable classification fields on some coordinate-backed ads,
    # while UUID/jobAdId/ref/location/release remain consistent. Comparing the
    # classifications there would turn known provider cache lag into a
    # permanent board failure without adding identity proof.
    listed_location = listed["location"]
    detail_location = detail["location"]
    for coordinate in ("latitude", "longitude"):
        if (coordinate in listed_location) != (coordinate in detail_location):
            raise ValueError(
                f"SmartRecruiters list/detail drifted at location.{coordinate} presence"
            )
    if listed_location.get("latitude") is None:
        _stable_mapping_subset(
            listed.get("department"),
            detail.get("department"),
            name="department",
            normalize_id=True,
        )
        listed_fields = dict(_stable_custom_field_ids(listed.get("customField"), source="list"))
        detail_fields = dict(_stable_custom_field_ids(detail.get("customField"), source="detail"))
        for field_id in (_BRAND_FIELD_ID, _DEPARTMENT_FIELD_ID):
            if not listed_fields.get(field_id) or listed_fields[field_id] != detail_fields.get(
                field_id
            ):
                raise ValueError("SmartRecruiters list/detail drifted at customField provider IDs")


async def _fetch_canonical_details(
    client: httpx.AsyncClient,
    token: str,
    items: list[dict],
) -> list[dict]:
    """Fetch every listed detail concurrently; one failure aborts the cycle."""
    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def fetch(item: dict) -> dict:
        posting_id = item["id"]
        async with semaphore:
            detail = await _get_detail_with_retry(client, token, posting_id)
        _validate_detail_identity(token, item, detail)
        return detail

    tasks = [asyncio.create_task(fetch(item)) for item in items]
    try:
        details = await asyncio.gather(*tasks)
        detail_ids = [detail.get("id") for detail in details]
        if len(detail_ids) != len(set(detail_ids)):
            raise ValueError("SmartRecruiters detail responses repeated one publication id")
        return details
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _detail_language(detail: dict) -> str | None:
    language = detail.get("language")
    code = language.get("code") if isinstance(language, dict) else None
    if not isinstance(code, str):
        return None
    code = code.casefold()
    return code if _LANGUAGE_RE.fullmatch(code) else None


def _variant_sort_key(detail: dict) -> tuple[int, str, str]:
    """Provider default first, then stable language/publication tie-breakers."""
    return (
        0 if detail.get("defaultJobAd") is True else 1,
        _detail_language(detail) or "zz",
        str(detail.get("id") or ""),
    )


def _canonical_job(
    token: str,
    components: tuple[str, str, str],
    variants: list[dict],
) -> DiscoveredJob:
    """Merge locale publications for one stable requisition/location."""
    from src.core.scrapers.smartrecruiters import _parse_detail

    ordered = sorted(variants, key=_variant_sort_key)
    defaults = [item for item in ordered if item.get("defaultJobAd") is True]
    if len(defaults) > 1:
        raise ValueError("SmartRecruiters identity has multiple default publications")
    parsed = [(detail, _parse_detail(detail)) for detail in ordered]
    primary_detail, primary = parsed[0]
    if not primary.title or not primary.description or not primary.locations:
        raise ValueError("SmartRecruiters canonical detail omitted required rich content")

    localizations: dict[str, dict] = {}
    for detail, content in parsed:
        language = _detail_language(detail)
        if language is None:
            raise ValueError("SmartRecruiters canonical publication omitted a language code")
        if language in localizations:
            raise ValueError("SmartRecruiters canonical identity repeated one language")
        if not content.title or not content.description or not content.locations:
            raise ValueError("SmartRecruiters locale detail omitted required rich content")
        localizations[language] = {
            "title": content.title,
            "description": content.description,
            "locations": content.locations,
        }

    aliases = sorted(_posting_url(token, str(detail["id"])) for detail in ordered)
    metadata = dict(primary.metadata or {})
    metadata.update(
        {
            "smartrecruiters_job_id": components[0],
            "smartrecruiters_publication_ids": [str(detail["id"]) for detail in ordered],
        }
    )
    return DiscoveredJob(
        url=_canonical_source_url(token, components),
        title=primary.title,
        description=primary.description,
        locations=primary.locations,
        employment_type=primary.employment_type,
        job_location_type=primary.job_location_type,
        date_posted=primary.date_posted,
        base_salary=primary.base_salary,
        language=_detail_language(primary_detail),
        localizations=localizations,
        extras=primary.extras,
        metadata=metadata,
        source_aliases=aliases,
    )


def _canonicalize_details(token: str, details: list[dict]) -> list[DiscoveredJob]:
    """Collapse proven locale variants and reject ambiguous location proof."""
    by_job_id: dict[str, list[tuple[tuple[str, str, str], dict]]] = {}
    for detail in details:
        components = _canonical_identity_components(detail)
        by_job_id.setdefault(components[0], []).append((components, detail))

    jobs: list[DiscoveredJob] = []
    for job_id, records in by_job_id.items():
        if len(records) > 1 and any(parts[1] != "geo" for parts, _detail in records):
            raise ValueError(
                f"SmartRecruiters repeated jobId without unambiguous provider coordinates: {job_id}"
            )
        variants_by_identity: dict[tuple[str, str, str], list[dict]] = {}
        for components, detail in records:
            variants_by_identity.setdefault(components, []).append(detail)
        for components, variants in variants_by_identity.items():
            jobs.append(_canonical_job(token, components, variants))

    urls = [job.url for job in jobs]
    if len(urls) != len(set(urls)):
        raise ValueError("SmartRecruiters canonical identity URL collision")
    return sorted(jobs, key=lambda job: job.url)


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch job listing URLs from the SmartRecruiters public API.

    Paginates the list endpoint and constructs posting URLs from token + ID.

    Failure semantics (#2749). Each page GET is wrapped by
    :func:`_get_page_with_retry`, which raises
    :class:`PaginationFetchError` on persistent transient failures or
    non-retryable 4xx. The exception propagates out of this function
    (no intervening try/except) and lands in
    ``_process_one_board_streaming``'s generic ``except Exception``,
    which records the run as a failure rather than a partial success
    — preventing ``_MARK_GONE_BY_TIMESTAMP`` from tombstoning the
    URLs that live on the unfetched pages (same shape of bug as
    #2722, #2737, #2748).

    Truncation semantics (#3216). When ``MAX_JOBS`` is reached the
    monitor returns a :class:`MonitorResult` with ``truncated=True``
    so the pipeline marks the cycle as partial and skips gone-detection
    — the unseen tail beyond the cap must not be tombstoned.
    """
    metadata = board.get("metadata") or {}
    token = metadata.get("token") or _token_from_url(board["board_url"])

    if not token:
        raise ValueError(
            f"Cannot derive SmartRecruiters token from board URL {board['board_url']!r} "
            "and no token in metadata"
        )
    if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("SmartRecruiters token is not a bounded provider identifier")

    if _canonical_identity_enabled(metadata):
        from src.core.monitor import MonitorResult

        items = await _list_posting_items(client, token)
        details = await _fetch_canonical_details(client, token, items)
        jobs = _canonicalize_details(token, details)
        log.info(
            "smartrecruiters.canonical_listed",
            token=token,
            publications=len(items),
            postings=len(jobs),
        )
        jobs_by_url = {job.url: job for job in jobs}
        return MonitorResult(urls=set(jobs_by_url), jobs_by_url=jobs_by_url)

    urls: set[str] = set()
    offset = 0
    list_url = _api_list_url(token)
    truncated = False

    while True:
        data = await _get_page_with_retry(client, list_url, {"limit": PAGE_SIZE, "offset": offset})

        content = data.get("content", [])
        for item in content:
            pid = item.get("id")
            if pid:
                urls.add(_posting_url(token, str(pid)))

        total_found = data.get("totalFound", 0)
        offset += PAGE_SIZE

        if offset >= total_found or len(content) < PAGE_SIZE:
            break

        if len(urls) >= MAX_JOBS:
            log.warning(
                "smartrecruiters.truncated",
                token=token,
                total=len(urls),
                cap=MAX_JOBS,
            )
            truncated = True
            break

    log.info("smartrecruiters.listed", token=token, postings=len(urls))
    if truncated:
        return truncated_url_result(urls)
    return urls


async def _probe_token(
    token: str,
    client: httpx.AsyncClient,
    context: None = None,
) -> tuple[bool, int | None]:
    """Probe the SmartRecruiters API for a token. Returns (found, job_count)."""
    _ = context
    try:
        resp = await client.get(
            _api_list_url(token),
            params={"limit": 1, "offset": 0},
        )
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        total = data.get("totalFound")
        if isinstance(total, int):
            return True, total
        # Check if content exists at all
        content = data.get("content")
        if isinstance(content, list):
            return True, len(content)
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(
    token: str,
    client: httpx.AsyncClient,
    context: None = None,
) -> int | None:
    """Lightweight API call to get the job count for a token."""
    _ = context
    try:
        resp = await client.get(
            _api_list_url(token),
            params={"limit": 1, "offset": 0},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("totalFound")
        return total if isinstance(total, int) else None
    except Exception:
        return None


async def _probe_token_with_count(
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    count = await _fetch_job_count(token, client, context)
    return count is not None, count


async def _detect_custom_portal(
    url: str,
    client: httpx.AsyncClient,
) -> dict | None:
    """Detect branded portals backed by SmartRecruiters one-click URLs.

    Some companies put a custom frontend in front of SmartRecruiters.  The
    page itself contains no SmartRecruiters hostname, but loads a same-origin
    ``/api/jobs`` endpoint whose records link to the public one-click UI.
    Only probe that endpoint when the page explicitly references it, then
    validate the extracted tenant against SmartRecruiters' public API.
    """
    try:
        page = await client.get(url, follow_redirects=True)
        if page.status_code != 200 or not _CUSTOM_API_SIGNAL_RE.search(page.text):
            return None

        api_url = urljoin(str(page.url), "/api/jobs")
        response = await client.get(api_url, params={"page": 1})
        if response.status_code != 200:
            return None
        data = response.json()
        jobs = data.get("jobs")
        if not isinstance(jobs, list):
            return None

        tokens: set[str] = set()
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_url = job.get("url")
            if not isinstance(job_url, str):
                continue
            match = _ONECLICK_TOKEN_RE.search(job_url)
            if match:
                tokens.add(match.group(1))
        if len(tokens) != 1:
            if len(tokens) > 1:
                log.debug(
                    "smartrecruiters.custom_portal_ambiguous",
                    url=url,
                    tenants=sorted(tokens),
                )
            return None
        token = next(iter(tokens))

        count = await _fetch_job_count(token, client)
        if count is None:
            return None
        return {"token": token, "jobs": count}
    except (httpx.HTTPError, ValueError, TypeError):
        return None


async def _resolve_direct_token(
    url: str,
    token: str,
    client: httpx.AsyncClient,
    context: None,
) -> tuple[str, None] | None:
    _ = context
    final_url = url
    html: str | None = None
    try:
        resp = await client.get(url, follow_redirects=True)
        final_url = str(resp.url)
        if resp.status_code == 200:
            html = resp.text
    except Exception:
        # Network failures leave only the original URL token to validate below.
        return token, None

    final_token = _token_from_url(final_url)
    if final_token:
        return final_token, None
    if not _has_smartrecruiters_signal(final_url, html):
        return None
    return token, None


def _signal_slug_candidates(url: str, html: str, context: None) -> tuple[str, ...]:
    _ = context
    if not _has_smartrecruiters_signal(url, html):
        return ()
    return tuple(slug for slug in slugs_from_url(url) if slug not in _IGNORE_TOKENS)


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect SmartRecruiters: URL pattern -> page HTML scan -> slug-based API probe."""
    _ = pw
    result = await ats_can_handle(
        url,
        client,
        monitor_name="smartrecruiters",
        token_from_url=_token_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=_IGNORE_TOKENS,
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_token,
        initial_context=None,
        direct_token_resolver=_resolve_direct_token,
        require_direct_count=True,
        page_token_probe=_probe_token_with_count,
        extra_probe_tokens=_signal_slug_candidates,
        extra_probe_log_event="smartrecruiters.detected_by_probe",
        allow_slug_guess=False,
    )
    if result is not None or client is None:
        return result
    return await _detect_custom_portal(url, client)


register("smartrecruiters", discover, cost=10, can_handle=can_handle, rich=False)
