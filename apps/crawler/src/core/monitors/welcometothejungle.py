"""Welcome to the Jungle public API monitor.

WTTJ company pages are backed by two public read-only APIs:

* the organization endpoint resolves the visible company slug to WTTJ's
  internal (and sometimes legacy) organization slug;
* the public Algolia jobs index lists active postings, while the organization
  job endpoint returns the complete posting body.

The list index contains one row per publishing website, so the same posting
may appear several times.  This monitor deduplicates those mirrors by WTTJ
job reference before fetching details.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlencode, urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type, normalize_salary_unit
from src.core.monitors import DiscoveredJob, register
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

MAX_JOBS = 1_000  # Algolia's public search pagination ceiling.
_DETAIL_CONCURRENCY = 10

_SITE = "https://www.welcometothejungle.com"
_API = "https://api.welcometothejungle.com/api/v1"
_ALGOLIA_URL = "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries"
_ALGOLIA_INDEX = "wk_cms_jobs_production"

# This is WTTJ's public, search-only browser key.  It is shipped to every
# anonymous visitor and cannot write to the index.
_ALGOLIA_HEADERS = {
    "x-algolia-application-id": "CSEKHVMS53",
    "x-algolia-api-key": "4bd8f6215d0cc52b26430765769e65a0",
    # WTTJ's browser client sends JSON with this content type; Algolia's key
    # referer restriction also requires the public site origin/referer.
    "content-type": "application/x-www-form-urlencoded",
    "origin": _SITE,
    "referer": f"{_SITE}/",
}

_HOSTS = frozenset({"welcometothejungle.com", "www.welcometothejungle.com"})
_COMPANY_PATH_RE = re.compile(r"^/([a-z]{2})/companies/([^/]+)(?:/|$)", re.IGNORECASE)


def _identity_from_url(url: str) -> tuple[str, str] | None:
    """Return ``(locale, public company slug)`` for a WTTJ company URL."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in _HOSTS:
        return None
    match = _COMPANY_PATH_RE.match(parsed.path)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def _organization_url(slug: str) -> str:
    return f"{_API}/organizations/{slug}"


def _detail_url(slug: str, job_slug: str) -> str:
    return f"{_API}/organizations/{slug}/jobs/{job_slug}"


def _public_job_url(locale: str, slug: str, job_slug: str) -> str:
    return f"{_SITE}/{locale}/companies/{slug}/jobs/{job_slug}"


async def _fetch_organization(slug: str, client: httpx.AsyncClient) -> dict | None:
    response = await client.get(_organization_url(slug), follow_redirects=True)
    if response.status_code in (404, 410):
        return None
    response.raise_for_status()
    organization = response.json().get("organization")
    return organization if isinstance(organization, dict) else None


async def _fetch_summaries(
    organization_slug: str,
    client: httpx.AsyncClient,
) -> tuple[list[dict], bool]:
    params = urlencode(
        {
            "hitsPerPage": MAX_JOBS,
            "page": 0,
            "filters": f"organization.slug:{organization_slug}",
        }
    )
    body = {
        "requests": [
            {
                "indexName": _ALGOLIA_INDEX,
                "params": params,
            }
        ]
    }
    response = await client.post(
        _ALGOLIA_URL,
        headers=_ALGOLIA_HEADERS,
        content=json.dumps(body, separators=(",", ":")),
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ValueError("WTTJ Algolia response is missing its results array")

    result = results[0]
    raw_hits = result.get("hits")
    if not isinstance(raw_hits, list):
        raise ValueError("WTTJ Algolia response is missing its hits array")
    hits = [hit for hit in raw_hits if isinstance(hit, dict)]
    total = result.get("nbHits")
    truncated = isinstance(total, int) and total > MAX_JOBS
    return hits, truncated


def _deduplicate_summaries(hits: list[dict], public_slug: str) -> list[dict]:
    """Collapse WTTJ marketplace/language mirrors to one posting summary."""
    deduped: dict[str, dict] = {}
    for hit in hits:
        job_slug = hit.get("slug")
        if not isinstance(job_slug, str) or not job_slug:
            continue
        reference = hit.get("reference")
        key = reference if isinstance(reference, str) and reference else job_slug

        current = deduped.get(key)
        website = hit.get("website")
        is_company_copy = isinstance(website, dict) and website.get("reference") == public_slug
        if current is None:
            deduped[key] = hit
            continue
        current_website = current.get("website")
        current_is_company_copy = (
            isinstance(current_website, dict) and current_website.get("reference") == public_slug
        )
        if is_company_copy and not current_is_company_copy:
            deduped[key] = hit
    return list(deduped.values())


def _localized(value: object, language: str | None) -> str | None:
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None
    for key in (language, "en", "fr", "es", "cs", "sk"):
        if key and isinstance(value.get(key), str) and value[key]:
            return value[key]
    return None


def _locations(raw: dict) -> list[str] | None:
    offices = raw.get("offices")
    if not isinstance(offices, list):
        office = raw.get("office")
        offices = [office] if isinstance(office, dict) else []

    locations: list[str] = []
    seen: set[str] = set()
    for office in offices:
        if not isinstance(office, dict):
            continue
        value = office.get("local_address") or office.get("address") or office.get("city")
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            locations.append(value)
    return locations or None


def _salary(raw: dict) -> dict | None:
    minimum = raw.get("salary_min")
    maximum = raw.get("salary_max")
    if minimum is None and maximum is None:
        return None
    return {
        "currency": raw.get("salary_currency"),
        "min": minimum,
        "max": maximum,
        "unit": normalize_salary_unit(raw.get("salary_period")) or "year",
    }


def _parse_job(raw: dict, *, locale: str, public_slug: str) -> DiscoveredJob | None:
    # Republished WTTJ jobs retain their historical ``archived_at`` value,
    # so current status is authoritative for terminal filtering.
    if raw.get("status") in {"archived", "expired", "closed"}:
        return None
    job_slug = raw.get("slug")
    title = raw.get("name")
    if not isinstance(job_slug, str) or not job_slug or not isinstance(title, str) or not title:
        return None

    language = raw.get("language") if isinstance(raw.get("language"), str) else locale
    extras: dict[str, object] = {}
    profile = raw.get("profile")
    if isinstance(profile, str) and profile:
        extras["qualifications"] = profile
    missions = raw.get("key_missions")
    if isinstance(missions, list) and missions:
        extras["responsibilities"] = missions
    skills = raw.get("skills")
    if isinstance(skills, list):
        names = [
            name
            for skill in skills
            if isinstance(skill, dict)
            if (name := _localized(skill.get("name"), language))
        ]
        if names:
            extras["skills"] = names

    metadata: dict[str, object] = {}
    for source, target in (("reference", "reference"), ("start_date", "start_date")):
        value = raw.get(source)
        if value not in (None, ""):
            metadata[target] = value
    profession = raw.get("profession")
    if isinstance(profession, dict):
        profession_name = _localized(profession.get("name"), language)
        if profession_name:
            metadata["profession"] = profession_name

    return DiscoveredJob(
        url=_public_job_url(locale, public_slug, job_slug),
        title=title,
        description=raw.get("description") or None,
        locations=_locations(raw),
        employment_type=raw.get("contract_type") or None,
        job_location_type=normalize_job_location_type(raw.get("remote"), default=None),
        date_posted=raw.get("published_at") or None,
        base_salary=_salary(raw),
        language=language,
        extras=extras or None,
        metadata=metadata or None,
    )


async def _fetch_detail(
    public_slug: str,
    job_slug: str,
    client: httpx.AsyncClient,
) -> dict | None:
    response = await client.get(_detail_url(public_slug, job_slug), follow_redirects=True)
    # A posting can close between the Algolia list request and the detail GET.
    if response.status_code in (404, 410):
        log.info("welcometothejungle.detail_gone", slug=public_slug, job_slug=job_slug)
        return None
    response.raise_for_status()
    job = response.json().get("job")
    if not isinstance(job, dict):
        raise ValueError("WTTJ detail response is missing its job object")
    return job


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return every active WTTJ posting with complete detail data."""
    _ = pw
    identity = _identity_from_url(board["board_url"])
    metadata = board.get("metadata") or {}
    public_slug = metadata.get("slug")
    locale = metadata.get("locale")
    if identity:
        locale = locale or identity[0]
        public_slug = public_slug or identity[1]
    if not isinstance(public_slug, str) or not public_slug:
        raise ValueError(f"Cannot derive WTTJ company slug from {board['board_url']!r}")
    if not isinstance(locale, str) or not locale:
        locale = "en"

    organization_slug = metadata.get("organization_slug")
    if not isinstance(organization_slug, str) or not organization_slug:
        organization = await _fetch_organization(public_slug, client)
        if not organization or not isinstance(organization.get("slug"), str):
            raise ValueError(f"WTTJ organization {public_slug!r} was not found")
        organization_slug = organization["slug"]

    raw_hits, truncated = await _fetch_summaries(organization_slug, client)
    summaries = _deduplicate_summaries(raw_hits, public_slug)

    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def hydrate(summary: dict) -> dict | None:
        async with semaphore:
            return await _fetch_detail(public_slug, summary["slug"], client)

    details = await asyncio.gather(*(hydrate(summary) for summary in summaries))
    jobs = [
        job
        for raw in details
        if raw is not None
        if (job := _parse_job(raw, locale=locale, public_slug=public_slug)) is not None
    ]
    log.info(
        "welcometothejungle.discovered",
        slug=public_slug,
        organization_slug=organization_slug,
        jobs=len(jobs),
        mirrors=len(raw_hits) - len(summaries),
    )
    return truncated_rich_result(jobs) if truncated else jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect WTTJ company pages using the public organization/list APIs."""
    _ = pw
    identity = _identity_from_url(url)
    if identity is None:
        return None
    locale, public_slug = identity
    if client is None:
        return {"slug": public_slug, "locale": locale}

    try:
        organization = await _fetch_organization(public_slug, client)
        if not organization or not isinstance(organization.get("slug"), str):
            return None
        organization_slug = organization["slug"]
        hits, _ = await _fetch_summaries(organization_slug, client)
        jobs = len(_deduplicate_summaries(hits, public_slug))
        return {
            "slug": public_slug,
            "locale": locale,
            "organization_slug": organization_slug,
            "jobs": jobs,
        }
    except Exception:
        log.debug("welcometothejungle.probe_failed", url=url, exc_info=True)
        return None


register("welcometothejungle", discover, cost=10, can_handle=can_handle, rich=True)
