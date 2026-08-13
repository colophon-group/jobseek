"""jobs.ch employer-profile monitor.

Employer profiles expose their active inventory through JobCloud's public
search endpoint.  The API is explicitly scoped by the numeric company ID and
returns an authoritative ``totalHits`` value, including a valid zero for an
empty board.  Detail pages provide ``JobPosting`` JSON-LD and are handled by
the existing JSON-LD scraper.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import register
from src.shared.tdm import check_response as check_tdm_response

log = structlog.get_logger()

API_URL = "https://job-search-api.jobs.ch/search"
ROWS = 100
MAX_PAGES = 500

_COMPANY_PATH_RE = re.compile(
    r"^/(?P<locale>de|fr|en)/(?:firmen|entreprises|companies)/"
    r"(?P<company_id>\d+)(?:[-/]|$)",
    re.IGNORECASE,
)
_DETAIL_PATHS = {
    "de": "stellenangebote",
    "fr": "offres-emplois",
    "en": "vacancies",
}


def _ids_from_url(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in {"jobs.ch", "www.jobs.ch"}:
        return None, None
    match = _COMPANY_PATH_RE.match(parsed.path)
    if not match:
        return None, None
    return match.group("company_id"), match.group("locale").lower()


def _board_metadata(board: dict) -> tuple[str, str]:
    metadata = board.get("metadata") or {}
    url_company_id, url_locale = _ids_from_url(board["board_url"])
    company_id = str(metadata.get("company_id") or url_company_id or "")
    locale = str(metadata.get("locale") or url_locale or "de").lower()
    if not company_id.isdigit():
        raise ValueError("jobs_ch requires a numeric company_id or jobs.ch employer URL")
    if locale not in _DETAIL_PATHS:
        raise ValueError("jobs_ch locale must be one of: de, fr, en")
    return company_id, locale


async def _fetch_page(
    client: httpx.AsyncClient,
    company_id: str,
    page: int,
) -> dict:
    response = await client.get(
        API_URL,
        params=[
            ("companyIds", company_id),
            ("page", str(page)),
            ("publishedOn", "SEARCH"),
            ("publishedOn", "SEARCH_COMPANY_PROFILE"),
            ("rows", str(ROWS)),
        ],
        follow_redirects=True,
    )
    response.raise_for_status()
    check_tdm_response(response, body_excerpt=response.text[:10_000])
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
        raise ValueError("jobs.ch search payload is missing its documents array")
    for key in ("numPages", "currentPage", "totalHits"):
        if not isinstance(payload.get(key), int):
            raise ValueError(f"jobs.ch search payload has invalid {key}")
    if payload["currentPage"] != page:
        raise ValueError(
            f"jobs.ch returned page {payload['currentPage']} while page {page} was requested"
        )
    if payload["numPages"] > MAX_PAGES:
        raise ValueError(
            f"jobs.ch board exceeds pagination safety cap ({payload['numPages']} pages)"
        )
    return payload


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> set[str]:
    """Return every active detail URL for one jobs.ch employer profile."""
    _ = pw
    company_id, locale = _board_metadata(board)
    first = await _fetch_page(client, company_id, 1)
    pages = first["numPages"]
    payloads = [first]
    for page in range(2, pages + 1):
        payloads.append(await _fetch_page(client, company_id, page))

    urls: set[str] = set()
    for payload in payloads:
        for document in payload["documents"]:
            job_id = document.get("id") if isinstance(document, dict) else None
            if not isinstance(job_id, str) or not job_id.strip():
                raise ValueError("jobs.ch search result is missing a job id")
            urls.add(
                f"https://www.jobs.ch/{locale}/{_DETAIL_PATHS[locale]}/detail/{job_id.strip()}/"
            )

    total_hits = first["totalHits"]
    if len(urls) != total_hits:
        raise ValueError(
            f"jobs.ch pagination returned {len(urls)} unique jobs, expected {total_hits}"
        )
    log.info(
        "jobs_ch.discovered",
        company_id=company_id,
        jobs=len(urls),
        pages=pages,
    )
    return urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize a jobs.ch employer profile and validate its scoped feed."""
    _ = pw
    company_id, locale = _ids_from_url(url)
    if company_id is None or locale is None or client is None:
        return None
    try:
        payload = await _fetch_page(client, company_id, 1)
    except Exception:
        log.debug("jobs_ch.probe_failed", url=url, exc_info=True)
        return None
    return {
        "company_id": company_id,
        "locale": locale,
        "jobs": payload["totalHits"],
    }


register("jobs_ch", discover, cost=10, can_handle=can_handle)
