"""jobs.ch employer-profile monitor.

Employer profiles expose their active inventory through JobCloud's public
search endpoint.  The API is explicitly scoped by the numeric company ID and
returns an authoritative ``totalHits`` value, including a valid zero for an
empty board.  Detail pages provide ``JobPosting`` JSON-LD and are handled by
the existing JSON-LD scraper.
"""

from __future__ import annotations

import re
from math import ceil
from urllib.parse import urlparse
from uuid import UUID

import httpx
import structlog

from src.core.monitors import register
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

API_URL = "https://job-search-api.jobs.ch/search"
ROWS = 100
MAX_PAGES = 500

_UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_COMPANY_PATH_RE = re.compile(
    r"^/(?P<locale>de|fr|en)/(?:firmen|entreprises|companies)/"
    rf"(?P<company_id>(?:{_UUID_PATTERN}|\d{{1,20}}))(?:[-/]|$)",
    re.IGNORECASE,
)
_DETAIL_PATHS = {
    "de": "stellenangebote",
    "fr": "offres-emplois",
    "en": "vacancies",
}


def _normalize_company_id(value: object) -> str | None:
    company_id = str(value).strip() if isinstance(value, (str, int)) else ""
    if company_id.isdigit():
        return company_id if len(company_id) <= 20 else None
    try:
        canonical = str(UUID(company_id))
    except (ValueError, AttributeError):
        return None
    return canonical if canonical == company_id.lower() else None


def _ids_from_url(url: str) -> tuple[str | None, str | None]:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None, None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in {"jobs.ch", "www.jobs.ch"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None, None
    match = _COMPANY_PATH_RE.match(parsed.path)
    if not match:
        return None, None
    company_id = _normalize_company_id(match.group("company_id"))
    if company_id is None:
        return None, None
    return company_id, match.group("locale").lower()


def _board_metadata(board: dict) -> tuple[str, str]:
    metadata = board.get("metadata") or {}
    url_company_id, url_locale = _ids_from_url(board["board_url"])
    company_id = _normalize_company_id(metadata.get("company_id") or url_company_id)
    locale = str(metadata.get("locale") or url_locale or "de").lower()
    if company_id is None:
        raise ValueError("jobs_ch requires a numeric or UUID company_id or employer URL")
    if locale not in _DETAIL_PATHS:
        raise ValueError("jobs_ch locale must be one of: de, fr, en")
    return company_id, locale


async def _fetch_page(
    client: httpx.AsyncClient,
    company_id: str,
    page: int,
) -> dict:
    payload = await fetch_json_page_with_retry(
        client,
        API_URL,
        expect_shape=dict,
        params={
            "companyIds": company_id,
            "page": page,
            "publishedOn": ["SEARCH", "SEARCH_COMPANY_PROFILE"],
            "rows": ROWS,
        },
        follow_redirects=True,
        log_event="jobs_ch.list_backoff",
    )
    if not isinstance(payload.get("documents"), list):
        raise ValueError("jobs.ch search payload is missing its documents array")
    for key in ("numPages", "currentPage", "totalHits"):
        if type(payload.get(key)) is not int or payload[key] < 0:
            raise ValueError(f"jobs.ch search payload has invalid {key}")
    if payload["currentPage"] != page:
        raise ValueError(
            f"jobs.ch returned page {payload['currentPage']} while page {page} was requested"
        )
    if payload["numPages"] > MAX_PAGES:
        raise ValueError(
            f"jobs.ch board exceeds pagination safety cap ({payload['numPages']} pages)"
        )
    if payload.get("rows") != ROWS or payload.get("start") != (page - 1) * ROWS:
        raise ValueError("jobs.ch search payload has inconsistent pagination metadata")
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
    total_hits = first["totalHits"]
    expected_pages = ceil(total_hits / ROWS)
    if pages != expected_pages:
        raise ValueError(
            f"jobs.ch reported {pages} pages for {total_hits} jobs; expected {expected_pages}"
        )
    payloads = [first]
    for page in range(2, pages + 1):
        payload = await _fetch_page(client, company_id, page)
        if payload["numPages"] != pages or payload["totalHits"] != total_hits:
            raise ValueError("jobs.ch pagination totals changed during discovery")
        payloads.append(payload)

    urls: set[str] = set()
    for index, payload in enumerate(payloads, start=1):
        expected_documents = min(ROWS, max(0, total_hits - (index - 1) * ROWS))
        if len(payload["documents"]) != expected_documents:
            raise ValueError(
                f"jobs.ch page {index} returned {len(payload['documents'])} documents; "
                f"expected {expected_documents}"
            )
        for document in payload["documents"]:
            job_id = document.get("id") if isinstance(document, dict) else None
            if not isinstance(job_id, str):
                raise ValueError("jobs.ch search result is missing a job id")
            try:
                canonical_job_id = str(UUID(job_id))
            except (ValueError, AttributeError):
                raise ValueError("jobs.ch search result has an invalid job id") from None
            if canonical_job_id != job_id.lower():
                raise ValueError("jobs.ch search result has a non-canonical job id")
            urls.add(
                f"https://www.jobs.ch/{locale}/{_DETAIL_PATHS[locale]}/detail/{canonical_job_id}/"
            )

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
    except TDMReservedError:
        raise
    except Exception:
        log.debug("jobs_ch.probe_failed", url=url, exc_info=True)
        return None
    return {
        "company_id": company_id,
        "locale": locale,
        "jobs": payload["totalHits"],
    }


register("jobs_ch", discover, cost=10, can_handle=can_handle)
