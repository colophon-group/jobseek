"""SmartRecruiters Posting API monitor.

Public API:
  List:   GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=0

The list endpoint returns posting-publication IDs and metadata.  By default,
the monitor constructs posting URLs from the token + publication ID and
returns a URL set; detail fetching is handled by the scraper on the daily
schedule.

Some tenants publish one locale-specific posting per underlying job.  Those
boards can opt into ``canonical_job_id_url_template``.  The monitor then
fetches every publication detail, groups variants by the provider's exact
``jobId``, and returns one rich, localized job per underlying job.  No title,
reference-number, or location similarity is used for identity.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register, slugs_from_url
from src.core.monitors._ats_template import ProbeResult, ats_can_handle
from src.core.scrapers.smartrecruiters import _detail_url, _parse_detail
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.truncation import truncated_rich_result, truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
PAGE_SIZE = 100
_DETAIL_CONCURRENCY = 16
_JOB_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_LANGUAGE_RE = re.compile(r"([a-z]{2})(?:[-_][a-z]{2})?", re.IGNORECASE)
_DEFAULT_LANGUAGE_PREFERENCE = ("en", "de", "fr", "it")

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


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> dict:
    """GET a SmartRecruiters list-API page with bounded retries (#2749)."""
    return await fetch_json_page_with_retry(
        client,
        url,
        params=params,
        expect_shape=dict,
        retries=retries,
        base_delay=base_delay,
        log_event="smartrecruiters.list_backoff",
        sleep=asyncio.sleep,
    )


async def _get_detail_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> dict:
    """GET a posting detail with the list API's fail-closed retry contract."""
    return await fetch_json_page_with_retry(
        client,
        url,
        expect_shape=dict,
        retries=retries,
        base_delay=base_delay,
        log_event="smartrecruiters.detail_backoff",
        sleep=asyncio.sleep,
    )


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
        data = await _get_page_with_retry(client, list_url, {"limit": PAGE_SIZE, "offset": offset})
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
    match = _LANGUAGE_RE.fullmatch(raw.strip())
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
            detail = await _get_detail_with_retry(client, _detail_url(token, publication_id))
        _validate_detail(detail, token=token, publication_id=publication_id)
        return detail

    return list(await asyncio.gather(*(fetch_one(item) for item in publications)))


def _variant_key(detail: dict, language_preference: tuple[str, ...]) -> tuple:
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


def _localization(detail: dict) -> dict:
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
    grouped: dict[str, list[dict]] = {}
    for detail in details:
        job_id = str(detail["jobId"]).lower()
        grouped.setdefault(job_id, []).append(detail)

    jobs: list[DiscoveredJob] = []
    for job_id, variants in sorted(grouped.items()):
        ordered = sorted(variants, key=lambda item: _variant_key(item, language_preference))
        primary = ordered[0]
        parsed = _parse_detail(primary)
        per_language: dict[str, dict] = {}
        for variant in ordered:
            language = _language_code(variant)
            if language and language not in per_language:
                per_language[language] = _localization(variant)

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


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch jobs from the SmartRecruiters public API.

    Paginates the list endpoint and constructs posting URLs from token + ID.
    Configured locale-collapse mode additionally fetches details and returns
    one rich job per exact provider ``jobId``.

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

    publications, truncated, total_found = await _fetch_publications(token, client)
    template = _canonical_template(metadata)
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
