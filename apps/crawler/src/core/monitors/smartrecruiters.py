"""SmartRecruiters Posting API monitor.

Public API:
  List:   GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=0

The list endpoint returns posting-publication IDs and metadata. By default,
the monitor returns publication URLs for the scraper. Opt-in modes can fetch
details and collapse localized publications by exact provider identities.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register, slugs_from_url
from src.core.monitors._ats_template import ProbeResult, ats_can_handle
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.truncation import truncated_rich_result, truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
PAGE_SIZE = 100

# SmartRecruiters exposes a stable requisition ``jobId`` only on its detail
# endpoint.  A single requisition can have multiple language publications,
# while a reused requisition can also carry genuinely distinct locations.
# This opt-in contract therefore uses ``jobId`` plus provider-owned location
# identity, never refNumber/title/address text. The durable internal identity
# is kept separate from the real, fetchable outbound publication URL.
CANONICAL_IDENTITY_JOB_LOCATION_V1 = "job-location-v1"
MAX_CANONICAL_POSTINGS = 500
_DETAIL_CONCURRENCY = 12
_LIST_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
_DETAIL_RESPONSE_MAX_BYTES = 1024 * 1024
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_JOB_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_COORD_RE = re.compile(r"-?(?:0|[1-9]\d{0,2})(?:\.\d{1,16})?")
_LANGUAGE_RE = re.compile(r"[a-z]{2}")
_JOB_ID_LANGUAGE_RE = re.compile(r"([a-z]{2})(?:[-_][a-z]{2})?", re.IGNORECASE)
_DEFAULT_LANGUAGE_PREFERENCE = ("en", "de", "fr", "it")

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


def _canonical_source_identity(token: str, components: tuple[str, str, str]) -> str:
    """Build a tenant-bound identity separate from the outbound publication URL."""
    if _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("SmartRecruiters token is unsafe for canonical identities")
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
    return f"smartrecruiters:{token.casefold()}:{job_id}/{location_kind}/{digest}"


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


def _validate_list_page(data: dict, expected_total: int | None) -> tuple[list[dict], int]:
    """Validate the provider's pagination envelope before accepting a page."""
    content = data.get("content")
    total_found = data.get("totalFound")
    if not isinstance(content, list):
        raise ValueError("SmartRecruiters list response content must be a list")
    if not isinstance(total_found, int) or isinstance(total_found, bool) or total_found < 0:
        raise ValueError("SmartRecruiters list response totalFound must be a non-negative integer")
    if expected_total is not None and total_found != expected_total:
        raise ValueError(
            "SmartRecruiters totalFound changed during pagination "
            f"({expected_total} -> {total_found})"
        )
    if any(not isinstance(item, dict) for item in content):
        raise ValueError("SmartRecruiters list response contains a non-object posting")
    return content, total_found


async def _fetch_publications(
    token: str,
    client: httpx.AsyncClient,
) -> tuple[list[dict], bool, int]:
    """Fetch a complete, duplicate-free publication inventory."""
    publications: list[dict] = []
    publication_ids: set[str] = set()
    expected_total: int | None = None
    list_url = _api_list_url(token)
    offset = 0

    while True:
        data = await _get_page_with_retry(
            client,
            list_url,
            {"limit": PAGE_SIZE, "offset": offset},
            max_bytes=_LIST_RESPONSE_MAX_BYTES,
        )
        content, total_found = _validate_list_page(data, expected_total)
        if expected_total is None:
            expected_total = total_found

        for item in content:
            raw_id = item.get("id")
            if not isinstance(raw_id, (str, int)) or isinstance(raw_id, bool):
                raise ValueError("SmartRecruiters publication is missing a valid id")
            publication_id = str(raw_id).strip()
            if not publication_id:
                raise ValueError("SmartRecruiters publication has an empty id")
            if publication_id in publication_ids:
                raise ValueError(
                    f"SmartRecruiters repeated publication id {publication_id!r} across list pages"
                )
            publication_ids.add(publication_id)
            publications.append(item)

        if len(publications) >= total_found:
            if len(publications) != total_found:
                raise ValueError(
                    "SmartRecruiters returned more publications than totalFound "
                    f"({len(publications)} > {total_found})"
                )
            return publications, False, total_found

        if len(publications) >= MAX_JOBS:
            return publications, True, total_found

        if not content or len(content) < PAGE_SIZE:
            raise ValueError(
                "SmartRecruiters pagination ended before totalFound "
                f"({len(publications)} < {total_found})"
            )
        offset += PAGE_SIZE


def _canonical_template(metadata: dict) -> str | None:
    raw = metadata.get("canonical_job_id_url_template")
    if raw is None:
        return None
    if not isinstance(raw, str) or raw.count("{job_id}") != 1:
        raise ValueError(
            "canonical_job_id_url_template must be a string containing exactly one {job_id}"
        )
    remainder = raw.replace("{job_id}", "")
    if "{" in remainder or "}" in remainder:
        raise ValueError("canonical_job_id_url_template contains an unsupported placeholder")
    probe = raw.replace("{job_id}", "00000000-0000-4000-8000-000000000000")
    parsed = urlparse(probe)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "canonical_job_id_url_template must produce an absolute HTTPS URL "
            "without credentials, query, or fragment"
        )
    return raw


def _language_code(detail: dict) -> str | None:
    language = detail.get("language")
    raw = language.get("code") if isinstance(language, dict) else language
    if not isinstance(raw, str):
        return None
    match = _JOB_ID_LANGUAGE_RE.fullmatch(raw.strip())
    return match.group(1).lower() if match else None


def _language_preference(metadata: dict) -> tuple[str, ...]:
    raw = metadata.get("language_preference", _DEFAULT_LANGUAGE_PREFERENCE)
    if not isinstance(raw, list):
        if raw == _DEFAULT_LANGUAGE_PREFERENCE:
            return _DEFAULT_LANGUAGE_PREFERENCE
        raise ValueError("language_preference must be a list of ISO 639-1 codes")
    result: list[str] = []
    for value in raw:
        if not isinstance(value, str) or re.fullmatch(r"[a-zA-Z]{2}", value) is None:
            raise ValueError("language_preference must contain only ISO 639-1 codes")
        code = value.lower()
        if code not in result:
            result.append(code)
    return tuple(result)


def _validate_detail(detail: dict, *, token: str, publication_id: str) -> str:
    detail_id = detail.get("id")
    if str(detail_id) != publication_id:
        raise ValueError(
            f"SmartRecruiters detail id mismatch for {publication_id!r}: {detail_id!r}"
        )
    company = detail.get("company")
    identifier = company.get("identifier") if isinstance(company, dict) else None
    if identifier != token:
        raise ValueError(
            f"SmartRecruiters detail tenant mismatch for {publication_id!r}: {identifier!r}"
        )
    if detail.get("active") is not True:
        raise ValueError(
            f"SmartRecruiters detail is not affirmatively active for {publication_id!r}"
        )
    raw_job_id = detail.get("jobId")
    if not isinstance(raw_job_id, str) or _JOB_ID_RE.fullmatch(raw_job_id) is None:
        raise ValueError(
            f"SmartRecruiters detail has invalid jobId for {publication_id!r}: {raw_job_id!r}"
        )
    return raw_job_id.lower()


async def _fetch_details(
    token: str,
    publications: list[dict],
    client: httpx.AsyncClient,
) -> list[dict]:
    """Fetch all publication details concurrently; any failure aborts the cycle."""
    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def fetch_one(publication: dict) -> dict:
        publication_id = str(publication["id"]).strip()
        async with semaphore:
            detail = await _get_detail_with_retry(client, token, publication_id)
        _validate_detail(detail, token=token, publication_id=publication_id)
        return detail

    return list(await asyncio.gather(*(fetch_one(item) for item in publications)))


def _job_id_variant_key(detail: dict, language_preference: tuple[str, ...]) -> tuple:
    language = _language_code(detail)
    try:
        language_rank = (
            language_preference.index(language) if language else len(language_preference)
        )
    except ValueError:
        language_rank = len(language_preference)
    return (
        language_rank,
        language or "zz",
        0 if detail.get("defaultJobAd") is True else 1,
        str(detail["id"]),
    )


def _job_id_localization(detail: dict) -> dict:
    from src.core.scrapers.smartrecruiters import _parse_detail

    parsed = _parse_detail(detail)
    return {
        "title": parsed.title,
        "description": parsed.description,
        "locations": parsed.locations,
    }


def _collapse_details(
    details: list[dict],
    *,
    template: str,
    language_preference: tuple[str, ...],
) -> list[DiscoveredJob]:
    """Collapse locale publications by exact provider ``jobId``."""
    from src.core.scrapers.smartrecruiters import _parse_detail

    grouped: dict[str, list[dict]] = {}
    for detail in details:
        job_id = str(detail["jobId"]).lower()
        grouped.setdefault(job_id, []).append(detail)

    jobs: list[DiscoveredJob] = []
    for job_id, variants in sorted(grouped.items()):
        ordered = sorted(
            variants,
            key=lambda item: _job_id_variant_key(item, language_preference),
        )
        primary = ordered[0]
        parsed = _parse_detail(primary)
        per_language: dict[str, dict] = {}
        for variant in ordered:
            language = _language_code(variant)
            if language and language not in per_language:
                per_language[language] = _job_id_localization(variant)

        metadata = dict(parsed.metadata or {})
        metadata.update(
            {
                "smartrecruiters_job_id": job_id,
                "smartrecruiters_publication_id": str(primary["id"]),
                "smartrecruiters_publication_ids": sorted(str(item["id"]) for item in variants),
                "smartrecruiters_ref_number": primary.get("refNumber"),
            }
        )
        jobs.append(
            DiscoveredJob(
                url=template.replace("{job_id}", job_id),
                title=parsed.title,
                description=parsed.description,
                locations=parsed.locations,
                employment_type=parsed.employment_type,
                job_location_type=parsed.job_location_type,
                date_posted=parsed.date_posted,
                base_salary=parsed.base_salary,
                language=_language_code(primary),
                localizations=per_language or None,
                extras=parsed.extras,
                metadata=metadata,
            )
        )
    return jobs


def _stable_mapping_id(
    listed: object,
    detail: object,
    *,
    name: str,
) -> None:
    """Compare one provider mapping by ID while ignoring localized labels."""
    if not isinstance(listed, dict) or not isinstance(detail, dict) or not listed:
        raise ValueError(f"SmartRecruiters {name} overlap must be nonempty mappings")
    listed_id = listed.get("id")
    detail_id = detail.get("id")
    if listed_id is None or detail_id is None or str(listed_id) != str(detail_id):
        raise ValueError(f"SmartRecruiters list/detail drifted at {name}.id")


def _validate_stable_location_overlap(listed: object, detail: object) -> None:
    """Compare only identity-bearing codes, flags, and provider coordinates."""
    if not isinstance(listed, dict) or not isinstance(detail, dict):
        raise ValueError("SmartRecruiters location overlap must be mappings")

    for key in ("country", "postalCode", "remote", "hybrid"):
        if (key in listed) != (key in detail) or listed.get(key) != detail.get(key):
            raise ValueError(f"SmartRecruiters list/detail drifted at location.{key}")
    country = listed.get("country")
    if not isinstance(country, str) or re.fullmatch(r"[A-Za-z]{2}", country) is None:
        raise ValueError("SmartRecruiters listed location.country must be a country code")
    postal_code = listed.get("postalCode")
    if postal_code is not None and (
        not isinstance(postal_code, str)
        or not postal_code.strip()
        or len(postal_code) > 32
        or "\x00" in postal_code
    ):
        raise ValueError("SmartRecruiters listed location.postalCode must be bounded")
    for key in ("remote", "hybrid"):
        if not isinstance(listed.get(key), bool):
            raise ValueError(f"SmartRecruiters listed location.{key} must be boolean")

    listed_has_coordinates = "latitude" in listed or "longitude" in listed
    detail_has_coordinates = "latitude" in detail or "longitude" in detail
    if listed_has_coordinates != detail_has_coordinates:
        raise ValueError("SmartRecruiters list/detail drifted at location coordinate presence")
    if listed_has_coordinates:
        listed_lat = _normalize_coordinate(listed.get("latitude"), latitude=True)
        listed_lon = _normalize_coordinate(listed.get("longitude"), latitude=False)
        detail_lat = _normalize_coordinate(detail.get("latitude"), latitude=True)
        detail_lon = _normalize_coordinate(detail.get("longitude"), latitude=False)
        if (listed_lat, listed_lon) != (detail_lat, detail_lon):
            raise ValueError("SmartRecruiters list/detail drifted at location coordinates")


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

    listed_company = listed.get("company")
    if not isinstance(listed_company, dict) or listed_company.get("identifier") != token:
        raise ValueError("SmartRecruiters listed company did not match its tenant")
    listed_language = _detail_language(listed)
    detail_language = _detail_language(detail)
    if listed_language is None or listed_language != detail_language:
        raise ValueError("SmartRecruiters list/detail drifted at language.code")
    _validate_stable_location_overlap(listed.get("location"), detail.get("location"))
    # Department/custom-field values are identity-bearing only for the
    # coordinate-free fallback. SmartRecruiters' list API is observably stale
    # for these mutable classification fields on some coordinate-backed ads,
    # while UUID/jobAdId/ref/location/release remain consistent. Comparing the
    # classifications there would turn known provider cache lag into a
    # permanent board failure without adding identity proof.
    listed_location = listed["location"]
    if listed_location.get("latitude") is None:
        _stable_mapping_id(
            listed.get("department"),
            detail.get("department"),
            name="department",
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

    metadata = dict(primary.metadata or {})
    metadata.update(
        {
            "smartrecruiters_job_id": components[0],
            "smartrecruiters_publication_ids": [str(detail["id"]) for detail in ordered],
        }
    )
    return DiscoveredJob(
        url=_posting_url(token, str(primary_detail["id"])),
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
        source_identity=_canonical_source_identity(token, components),
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

    canonical_identity = _canonical_identity_enabled(metadata)
    template = _canonical_template(metadata)
    if canonical_identity and template is not None:
        raise ValueError(
            "SmartRecruiters canonical_identity and canonical_job_id_url_template "
            "cannot be combined"
        )

    if canonical_identity:
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

    publications, truncated, total_found = await _fetch_publications(token, client)
    if template is not None:
        details = await _fetch_details(token, publications, client)
        jobs = _collapse_details(
            details,
            template=template,
            language_preference=_language_preference(metadata),
        )
        log.info(
            "smartrecruiters.localized_listed",
            token=token,
            publications=len(publications),
            postings=len(jobs),
            collapsed=len(publications) - len(jobs),
            total_found=total_found,
        )
        if truncated:
            return truncated_rich_result(jobs)
        return jobs

    urls = {_posting_url(token, str(item["id"]).strip()) for item in publications}
    log.info(
        "smartrecruiters.listed",
        token=token,
        postings=len(urls),
        total_found=total_found,
    )
    if truncated:
        log.warning(
            "smartrecruiters.truncated",
            token=token,
            total=len(urls),
            cap=MAX_JOBS,
        )
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
