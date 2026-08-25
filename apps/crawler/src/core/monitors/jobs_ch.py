"""JobCloud employer-profile monitor for jobs.ch and jobup.ch.

Employer profiles expose their active inventory through JobCloud's public
search endpoints.  Each portal uses its own company identifiers and localized
detail paths, but exposes the same pagination contract.  The API is explicitly
scoped by the company ID and returns an authoritative ``totalHits`` value,
including a valid zero for an empty board.  Detail pages provide ``JobPosting``
JSON-LD and are handled by the existing JSON-LD scraper.

Migrated profiles occasionally require a UUID as the search filter while each
document retains a legacy numeric company ID. Those boards explicitly pin both
identities; every non-empty response still has to match the document identity.
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

ROWS = 100
MAX_PAGES = 500

_UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PORTALS = {
    "jobs_ch": {
        "hosts": {"jobs.ch", "www.jobs.ch"},
        "company_path": re.compile(
            r"^/(?P<locale>de|fr|en)/(?:firmen|entreprises|companies)/"
            rf"(?P<company_id>(?:{_UUID_PATTERN}|\d{{1,20}}))(?:[-/]|$)",
            re.IGNORECASE,
        ),
        "api_url": "https://job-search-api.jobs.ch/search",
        "site_host": "www.jobs.ch",
        "detail_paths": {
            "de": "stellenangebote",
            "fr": "offres-emplois",
            "en": "vacancies",
        },
    },
    "jobup": {
        "hosts": {"jobup.ch", "www.jobup.ch"},
        "company_path": re.compile(
            r"^/(?P<locale>fr|en)/(?:societes|enterprises)/"
            rf"(?P<company_id>(?:{_UUID_PATTERN}|\d{{1,20}}))(?:[-/]|$)",
            re.IGNORECASE,
        ),
        "api_url": "https://job-search-api.jobup.ch/search",
        "site_host": "www.jobup.ch",
        "detail_paths": {
            "fr": "emplois",
            "en": "jobs",
        },
    },
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


def _profile_from_url(url: str) -> tuple[str | None, str | None, str | None]:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None, None, None
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
        return None, None, None
    if port not in {None, 443}:
        return None, None, None
    host = (parsed.hostname or "").lower()
    portal = next(
        (name for name, config in _PORTALS.items() if host in config["hosts"]),
        None,
    )
    if portal is None:
        return None, None, None
    match = _PORTALS[portal]["company_path"].match(parsed.path)
    if not match:
        return None, None, None
    company_id = _normalize_company_id(match.group("company_id"))
    if company_id is None:
        return None, None, None
    return company_id, match.group("locale").lower(), portal


def _ids_from_url(url: str) -> tuple[str | None, str | None]:
    company_id, locale, _ = _profile_from_url(url)
    return company_id, locale


def _validate_document_company_alias(company_id: str, document_company_id: str) -> None:
    """Allow only the provider's observed UUID-to-legacy-numeric migration shape."""
    if document_company_id == company_id:
        return
    if company_id.isdigit() or not document_company_id.isdigit():
        raise ValueError(
            "jobs_ch document_company_id aliases require a UUID profile ID "
            "and a numeric legacy document ID"
        )


def _board_metadata(board: dict) -> tuple[str, str, str, str]:
    metadata = board.get("metadata") or {}
    url_company_id, url_locale, url_portal = _profile_from_url(board["board_url"])
    company_id = _normalize_company_id(metadata.get("company_id") or url_company_id)
    if "document_company_id" in metadata:
        document_company_id = _normalize_company_id(metadata.get("document_company_id"))
        if document_company_id is None:
            raise ValueError("jobs_ch document_company_id must be a numeric or UUID company ID")
    else:
        document_company_id = company_id
    locale = str(metadata.get("locale") or url_locale or "de").lower()
    portal = str(metadata.get("portal") or url_portal or "jobs_ch").lower()
    if company_id is None:
        raise ValueError("jobs_ch requires a numeric or UUID company_id or JobCloud employer URL")
    if portal not in _PORTALS:
        raise ValueError("jobs_ch portal must be one of: jobs_ch, jobup")
    if locale not in _PORTALS[portal]["detail_paths"]:
        raise ValueError(f"jobs_ch locale is not supported by the {portal} portal")
    assert document_company_id is not None
    _validate_document_company_alias(company_id, document_company_id)
    return company_id, document_company_id, locale, portal


def _page_document_company_id(payload: dict, expected_documents: int) -> str | None:
    """Return the single employer identity carried by a complete API page."""
    documents = payload["documents"]
    if len(documents) != expected_documents:
        raise ValueError(
            f"JobCloud page {payload['currentPage']} returned {len(documents)} documents; "
            f"expected {expected_documents}"
        )
    if not documents:
        return None

    company_ids: set[str] = set()
    for document in documents:
        document_company = document.get("company") if isinstance(document, dict) else None
        document_company_id = (
            _normalize_company_id(document_company.get("id"))
            if isinstance(document_company, dict)
            else None
        )
        if document_company_id is None:
            raise ValueError("JobCloud returned a vacancy without a valid company identity")
        company_ids.add(document_company_id)
    if len(company_ids) != 1:
        raise ValueError("JobCloud returned vacancies for multiple companies")
    return company_ids.pop()


async def _fetch_page(
    client: httpx.AsyncClient,
    company_id: str,
    page: int,
    portal: str = "jobs_ch",
) -> dict:
    payload = await fetch_json_page_with_retry(
        client,
        _PORTALS[portal]["api_url"],
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
        raise ValueError("JobCloud search payload is missing its documents array")
    for key in ("numPages", "currentPage", "totalHits"):
        if type(payload.get(key)) is not int or payload[key] < 0:
            raise ValueError(f"JobCloud search payload has invalid {key}")
    if payload["currentPage"] != page:
        raise ValueError(
            f"JobCloud returned page {payload['currentPage']} while page {page} was requested"
        )
    if payload["numPages"] > MAX_PAGES:
        raise ValueError(
            f"JobCloud board exceeds pagination safety cap ({payload['numPages']} pages)"
        )
    if payload.get("rows") != ROWS or payload.get("start") != (page - 1) * ROWS:
        raise ValueError("JobCloud search payload has inconsistent pagination metadata")
    return payload


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> set[str]:
    """Return every active detail URL for one JobCloud employer profile."""
    _ = pw
    company_id, document_company_id, locale, portal = _board_metadata(board)
    first = await _fetch_page(client, company_id, 1, portal)
    pages = first["numPages"]
    total_hits = first["totalHits"]
    expected_pages = ceil(total_hits / ROWS)
    if pages != expected_pages:
        raise ValueError(
            f"JobCloud reported {pages} pages for {total_hits} jobs; expected {expected_pages}"
        )
    payloads = [first]
    for page in range(2, pages + 1):
        payload = await _fetch_page(client, company_id, page, portal)
        if payload["numPages"] != pages or payload["totalHits"] != total_hits:
            raise ValueError("JobCloud pagination totals changed during discovery")
        payloads.append(payload)

    urls: set[str] = set()
    for index, payload in enumerate(payloads, start=1):
        expected_documents = min(ROWS, max(0, total_hits - (index - 1) * ROWS))
        actual_company_id = _page_document_company_id(payload, expected_documents)
        if actual_company_id is not None and actual_company_id != document_company_id:
            raise ValueError("JobCloud returned a vacancy outside the configured company")
        for document in payload["documents"]:
            job_id = document.get("id") if isinstance(document, dict) else None
            if not isinstance(job_id, str):
                raise ValueError("JobCloud search result is missing a job id")
            try:
                canonical_job_id = str(UUID(job_id))
            except (ValueError, AttributeError):
                raise ValueError("JobCloud search result has an invalid job id") from None
            if canonical_job_id != job_id.lower():
                raise ValueError("JobCloud search result has a non-canonical job id")
            urls.add(
                f"https://{_PORTALS[portal]['site_host']}/"
                f"{locale}/{_PORTALS[portal]['detail_paths'][locale]}/detail/{canonical_job_id}/"
            )

    if len(urls) != total_hits:
        raise ValueError(
            f"JobCloud pagination returned {len(urls)} unique jobs, expected {total_hits}"
        )
    log.info(
        "jobs_ch.discovered",
        company_id=company_id,
        document_company_id=document_company_id,
        portal=portal,
        jobs=len(urls),
        pages=pages,
    )
    return urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize a JobCloud employer profile and validate its scoped feed."""
    _ = pw
    company_id, locale, portal = _profile_from_url(url)
    if company_id is None or locale is None or portal is None or client is None:
        return None
    try:
        payload = await _fetch_page(client, company_id, 1, portal)
        expected_documents = min(ROWS, payload["totalHits"])
        document_company_id = _page_document_company_id(payload, expected_documents)
        if document_company_id is not None:
            _validate_document_company_alias(company_id, document_company_id)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("jobs_ch.probe_failed", url=url, exc_info=True)
        return None
    result = {
        "company_id": company_id,
        "locale": locale,
        "jobs": payload["totalHits"],
    }
    if portal != "jobs_ch":
        result["portal"] = portal
    if document_company_id is not None and document_company_id != company_id:
        result["document_company_id"] = document_company_id
    return result


register("jobs_ch", discover, cost=10, can_handle=can_handle)
