"""HireHive public Jobs API monitor.

Public API:
  GET https://{tenant}.hirehive.com/api/v2/jobs?page=1&page_size=100

The hosted careers page is aggressively rate-limited by Cloudflare, while the
documented tenant API is cacheable and returns complete published job objects.
No authentication is required.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type, normalize_salary_unit
from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.raw import save_json_response
from src.core.salary_extract import parse_salary_text
from src.shared.http_retry import PaginationFetchError, fetch_json_page_with_retry
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

MAX_JOBS = 50_000
PAGE_SIZE = 100
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5

_DOMAIN_RE = re.compile(r"^([a-z0-9-]+)\.hirehive\.com$")
_PAGE_PATTERNS = [re.compile(r"([a-z0-9-]+)\.hirehive\.com", re.IGNORECASE)]
_IGNORE_SLUGS = frozenset(
    {"api", "app", "assets", "docs", "help", "hirehive-testing-account", "static", "www"}
)


def _slug_from_url(board_url: str) -> str | None:
    """Extract the tenant name from a hosted HireHive careers URL."""
    host = (urlparse(board_url).hostname or "").lower()
    match = _DOMAIN_RE.fullmatch(host)
    if not match:
        return None
    slug = match.group(1)
    return None if slug in _IGNORE_SLUGS else slug


def _api_url(slug: str) -> str:
    return f"https://{slug}.hirehive.com/api/v2/jobs"


def _parse_description(raw: dict) -> str | None:
    description = raw.get("description")
    if isinstance(description, dict):
        return description.get("html") or description.get("text") or None
    if isinstance(description, str):
        return description or None
    return None


def _parse_locations(raw: dict) -> list[str] | None:
    location = raw.get("location")
    if isinstance(location, str) and location.strip():
        return [location.strip()]

    # Some tenants omit the free-form location while retaining structured
    # state/country fields. Keep that as a useful fallback only.
    state = raw.get("state_code")
    country = raw.get("country")
    country_name = country.get("name") if isinstance(country, dict) else None
    parts = [part for part in (state, country_name) if isinstance(part, str) and part]
    return [", ".join(parts)] if parts else None


def _parse_language(raw: dict) -> str | None:
    language = raw.get("language")
    code = language.get("code") if isinstance(language, dict) else None
    if not isinstance(code, str) or not code:
        return None
    primary = re.split(r"[-_]", code, maxsplit=1)[0].lower()
    return primary if len(primary) == 2 else None


def _parse_salary(raw: dict) -> dict | None:
    tiers = raw.get("compensation_tiers")
    if isinstance(tiers, list):
        for tier in tiers:
            if not isinstance(tier, dict):
                continue
            minimum = tier.get("min_value")
            maximum = tier.get("max_value")
            if minimum is None and maximum is None:
                continue
            unit = normalize_salary_unit(tier.get("interval")) or "year"
            return {
                "currency": tier.get("currency_code"),
                "min": minimum,
                "max": maximum,
                "unit": unit,
            }

    salary = raw.get("salary")
    return parse_salary_text(salary) if isinstance(salary, str) and salary.strip() else None


def _parse_job(
    raw: dict,
    *,
    default_job_location_type: str | None = None,
) -> DiscoveredJob | None:
    url = raw.get("hosted_url")
    if not isinstance(url, str) or not url:
        return None

    job_type = raw.get("type")
    employment_type = None
    if isinstance(job_type, dict):
        employment_type = job_type.get("type") or job_type.get("name") or None

    metadata: dict = {}
    if raw.get("id"):
        metadata["id"] = raw["id"]
    category = raw.get("category")
    if isinstance(category, dict) and category.get("name"):
        metadata["category"] = category["name"]
    experience = raw.get("experience")
    if isinstance(experience, dict) and experience.get("type"):
        metadata["experience"] = experience["type"]

    return DiscoveredJob(
        url=url,
        title=raw.get("title"),
        description=_parse_description(raw),
        locations=_parse_locations(raw),
        employment_type=employment_type,
        job_location_type=normalize_job_location_type(default_job_location_type, default=None),
        date_posted=raw.get("published_date"),
        base_salary=_parse_salary(raw),
        language=_parse_language(raw),
        metadata=metadata or None,
    )


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> dict:
    """GET a HireHive list page with bounded transient-error retries."""
    return await fetch_json_page_with_retry(
        client,
        url,
        params=params,
        expect_shape=dict,
        retries=retries,
        base_delay=base_delay,
        log_event="hirehive.list_backoff",
        sleep=asyncio.sleep,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch all published jobs from a tenant's public Jobs API."""
    _ = pw
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])
    if not slug:
        raise ValueError(
            f"Cannot derive HireHive slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    url = _api_url(slug)
    defaults = metadata.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError(f"HireHive metadata defaults for {slug!r} must be an object")
    default_job_location_type = defaults.get("job_location_type")
    if default_job_location_type is not None and not isinstance(default_job_location_type, str):
        raise ValueError(f"HireHive default job_location_type for {slug!r} must be a string")

    jobs: list[DiscoveredJob] = []
    page = 1
    max_pages = max(1, (MAX_JOBS + PAGE_SIZE - 1) // PAGE_SIZE)

    while True:
        try:
            data = await _get_page_with_retry(
                client,
                url,
                {"page": page, "page_size": PAGE_SIZE},
            )
        except PaginationFetchError as exc:
            if page == 1 and exc.last_status in {404, 410}:
                raise BoardGoneError(
                    f"HireHive tenant {slug!r} no longer exists",
                    url=url,
                ) from exc
            raise

        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError(f"HireHive jobs response for {slug!r} has invalid items")

        for raw in items:
            if isinstance(raw, dict):
                job = _parse_job(
                    raw,
                    default_job_location_type=default_job_location_type,
                )
                if job:
                    jobs.append(job)

        meta = data.get("meta")
        if not isinstance(meta, dict) or not isinstance(meta.get("has_next_page"), bool):
            raise ValueError(f"HireHive jobs response for {slug!r} has invalid meta")
        has_next = meta["has_next_page"]

        over_job_cap = len(jobs) > MAX_JOBS
        exhausted_with_more = has_next and (len(jobs) >= MAX_JOBS or page >= max_pages)
        if over_job_cap or exhausted_with_more:
            log.warning(
                "hirehive.truncated",
                slug=slug,
                total=len(jobs),
                cap=MAX_JOBS,
                page=page,
            )
            return truncated_rich_result(jobs[:MAX_JOBS])

        if not has_next:
            break

        page += 1

    return jobs


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    try:
        response = await client.get(_api_url(slug), params={"page": 1, "page_size": 1})
        if response.status_code != 200:
            return False, None
        data = response.json()
        meta = data.get("meta")
        items = data.get("items")
        if not isinstance(meta, dict) or not isinstance(items, list):
            return False, None
        total = meta.get("total_items")
        return True, total if isinstance(total, int) else len(items)
    except Exception:
        log.debug(
            "hirehive.probe_failed",
            probe="slug",
            slug=slug,
            url=_api_url(slug),
            exc_info=True,
        )
        return False, None


async def _fetch_job_count(
    slug: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_slug(slug, client)
    return count if found else None


async def _probe_template_slug(
    slug: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_slug(slug, client)


def _build_result(slug: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    result: dict = {"slug": slug}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect HireHive from its hosted domain or an embedded careers URL."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="hirehive",
        token_from_url=_slug_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=_IGNORE_SLUGS,
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_template_slug,
        initial_context=None,
        result_builder=_build_result,
        page_token_probe=_probe_template_slug,
        log_token_field="slug",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    slug = metadata.get("slug") or _slug_from_url(board_url)
    if not slug:
        return
    await save_json_response(
        artifact_dir,
        client,
        _api_url(slug),
        params={"page": 1, "page_size": PAGE_SIZE},
    )


register("hirehive", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
