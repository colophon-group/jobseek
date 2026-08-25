"""JobStreet employer-profile monitor.

JobStreet's legacy keyword search routes are protected by a Cloudflare
challenge on crawler egress, while canonical employer profiles expose two
public read-only data surfaces used by the anonymous web application:

* ``/api/jobsearch/v5/search`` lists active jobs for one employer ID;
* ``/graphql`` returns the complete body for one listed job to the companion
  detail scraper.

The hourly monitor uses only the employer-scoped list. The normal scraper
schedule hydrates full descriptions, avoiding an N+1 detail fan-out every
monitor cycle and never accepting the one-line search teaser as content.
"""

from __future__ import annotations

import re
from math import ceil
from urllib.parse import urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type
from src.core.monitors import DiscoveredJob, register
from src.core.salary_extract import parse_salary_text
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

PAGE_SIZE = 100
MAX_JOBS = 10_000

_HOST_CONFIG = {
    "my.jobstreet.com": {
        "site_key": "my",
        "locale": "en-MY",
        "language": "en",
    },
    "sg.jobstreet.com": {
        "site_key": "sg",
        "locale": "en-SG",
        "language": "en",
    },
}
_COMPANY_PATH_RE = re.compile(
    r"^/companies/[a-z0-9][a-z0-9._-]*-(\d{12,18})(?:/jobs)?/?$",
    re.IGNORECASE,
)
_NUMERIC_ID_RE = re.compile(r"^\d{1,18}$")

_COMPANY_QUERY = """
query Company($id: CompanyId!) {
  companyDetails(id: $id) {
    companyProfile {
      organisationId
    }
  }
}
"""


def _identity_from_url(url: str) -> tuple[str, str] | None:
    """Return ``(host, company_id)`` for an exact unfiltered profile URL."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in _HOST_CONFIG
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None
    match = _COMPANY_PATH_RE.fullmatch(parsed.path)
    return (host, match.group(1)) if match else None


def _job_url(host: str, job_id: str) -> str:
    return f"https://{host}/job/{job_id}"


def _graphql_url(host: str) -> str:
    return f"https://{host}/graphql"


def _search_url(host: str) -> str:
    return f"https://{host}/api/jobsearch/v5/search"


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"JobStreet API returned invalid {field}")
    return value


async def _graphql(
    client: httpx.AsyncClient,
    host: str,
    query: str,
    variables: dict[str, str],
) -> dict:
    response = await client.post(
        _graphql_url(host),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("JobStreet GraphQL response is not an object")
    errors = payload.get("errors")
    if errors:
        raise ValueError("JobStreet GraphQL response contains errors")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("JobStreet GraphQL response omitted data")
    return data


async def _resolve_organisation_id(
    client: httpx.AsyncClient,
    host: str,
    company_id: str,
) -> str | None:
    data = await _graphql(client, host, _COMPANY_QUERY, {"id": company_id})
    details = data.get("companyDetails")
    profile = details.get("companyProfile") if isinstance(details, dict) else None
    organisation_id = profile.get("organisationId") if isinstance(profile, dict) else None
    if organisation_id is None:
        return None
    if not isinstance(organisation_id, str) or not _NUMERIC_ID_RE.fullmatch(organisation_id):
        raise ValueError("JobStreet company profile returned invalid organisationId")
    return organisation_id


async def _fetch_page(
    client: httpx.AsyncClient,
    *,
    host: str,
    organisation_id: str,
    page: int,
) -> dict:
    host_config = _HOST_CONFIG[host]
    return await fetch_json_page_with_retry(
        client,
        _search_url(host),
        expect_shape=dict,
        params={
            "companyid": organisation_id,
            "page": page,
            "pagesize": PAGE_SIZE,
            "siteKey": host_config["site_key"],
            "source": "COMPANY",
            "locale": host_config["locale"],
            "include": "nofeatured",
            "sortMode": "Relevance",
        },
        headers={"Accept": "application/json"},
        retryable_statuses={202, 403, 429},
        log_event="jobstreet.list_backoff",
    )


def _parse_page(
    payload: dict,
    *,
    organisation_id: str,
    company_id: str,
    requested_page: int,
) -> tuple[list[dict], int]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("JobStreet search response omitted data")
    total = _required_int(payload.get("totalCount"), "totalCount")
    sol_metadata = payload.get("solMetadata")
    if not isinstance(sol_metadata, dict):
        raise ValueError("JobStreet search response omitted solMetadata")
    page_number = _required_int(sol_metadata.get("pageNumber"), "pageNumber")
    page_size = _required_int(sol_metadata.get("pageSize"), "pageSize")
    reported_total = _required_int(sol_metadata.get("totalJobCount"), "totalJobCount")
    if page_number != requested_page or page_size != PAGE_SIZE or reported_total != total:
        raise ValueError("JobStreet search response pagination does not match the request")

    expected_rows = min(PAGE_SIZE, max(0, total - (requested_page - 1) * PAGE_SIZE))
    if len(rows) != expected_rows:
        raise ValueError(
            f"JobStreet search page {requested_page} returned {len(rows)} rows, "
            f"expected {expected_rows}"
        )

    parsed_rows: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("JobStreet search response contains a non-object job")
        job_id = str(row.get("id") or "")
        employer = row.get("employer")
        actual_employer_id = str(employer.get("id") or "") if isinstance(employer, dict) else ""
        actual_company_id = (
            str(employer.get("companyId") or "") if isinstance(employer, dict) else ""
        )
        if (
            not _NUMERIC_ID_RE.fullmatch(job_id)
            or actual_employer_id != organisation_id
            or actual_company_id != company_id
        ):
            raise ValueError("JobStreet search response contains an invalid job identity")
        if job_id in seen:
            raise ValueError(f"JobStreet search page {requested_page} repeated job {job_id}")
        seen.add(job_id)
        parsed_rows.append(row)
    return parsed_rows, total


async def _fetch_summaries(
    client: httpx.AsyncClient,
    *,
    host: str,
    organisation_id: str,
    company_id: str,
) -> tuple[list[dict], bool]:
    first_payload = await _fetch_page(
        client,
        host=host,
        organisation_id=organisation_id,
        page=1,
    )
    first, total = _parse_page(
        first_payload,
        organisation_id=organisation_id,
        company_id=company_id,
        requested_page=1,
    )
    target = min(total, MAX_JOBS)
    page_count = ceil(target / PAGE_SIZE) if target else 0
    summaries = first[:target]
    seen = {str(row["id"]) for row in summaries}

    for page_number in range(2, page_count + 1):
        payload = await _fetch_page(
            client,
            host=host,
            organisation_id=organisation_id,
            page=page_number,
        )
        rows, page_total = _parse_page(
            payload,
            organisation_id=organisation_id,
            company_id=company_id,
            requested_page=page_number,
        )
        if page_total != total:
            raise ValueError("JobStreet search total changed during pagination")
        overlap = seen & {str(row["id"]) for row in rows}
        if overlap:
            raise ValueError(f"JobStreet search page {page_number} repeated jobs")
        summaries.extend(rows[: max(0, target - len(summaries))])
        seen.update(str(row["id"]) for row in rows)

    if len(summaries) != target:
        raise ValueError(f"JobStreet discovered {len(summaries)} jobs, expected {target}")
    return summaries, total > MAX_JOBS


def _summary_locations(summary: dict) -> list[str] | None:
    values: list[str] = []
    for location in summary.get("locations") or []:
        label = _clean_text(location.get("label")) if isinstance(location, dict) else None
        if label and label not in values:
            values.append(label)
    return values or None


def _summary_employment_type(summary: dict) -> str | None:
    values = summary.get("workTypes")
    if not isinstance(values, list):
        return None
    for value in values:
        if cleaned := _clean_text(value):
            return cleaned
    return None


def _summary_location_type(summary: dict) -> str | None:
    arrangements = summary.get("workArrangements")
    rows = arrangements.get("data") if isinstance(arrangements, dict) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        label = row.get("label") if isinstance(row, dict) else None
        text = label.get("text") if isinstance(label, dict) else None
        if normalized := normalize_job_location_type(text, default=None):
            return normalized
    return None


def _parse_summary(summary: dict, *, host: str, company_id: str) -> DiscoveredJob:
    job_id = str(summary["id"])
    title = _clean_text(summary.get("title"))
    if not title:
        raise ValueError(f"JobStreet job {job_id} omitted its title")
    salary_label = _clean_text(summary.get("salaryLabel"))
    employer = summary.get("employer")
    metadata: dict[str, object] = {
        "jobstreet_job_id": job_id,
        "jobstreet_company_id": company_id,
    }
    if isinstance(employer, dict) and (employer_name := _clean_text(employer.get("name"))):
        metadata["employer"] = employer_name
    classifications: list[str] = []
    for item in summary.get("classifications") or []:
        if not isinstance(item, dict):
            continue
        for key in ("classification", "subclassification", "subClassification"):
            value = item.get(key)
            label = _clean_text(value.get("description")) if isinstance(value, dict) else None
            if label and label not in classifications:
                classifications.append(label)
    if classifications:
        metadata["classifications"] = classifications

    return DiscoveredJob(
        url=_job_url(host, job_id),
        title=title,
        description=None,
        locations=_summary_locations(summary),
        employment_type=_summary_employment_type(summary),
        job_location_type=_summary_location_type(summary),
        date_posted=_clean_text(summary.get("listingDate")),
        base_salary=parse_salary_text(salary_label) if salary_label else None,
        language=_HOST_CONFIG[host]["language"],
        metadata=metadata,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return employer-scoped JobStreet summaries for detail enrichment."""
    _ = pw
    metadata = board.get("metadata") or {}
    identity = _identity_from_url(board["board_url"])
    host = identity[0] if identity else metadata.get("host")
    company_id = identity[1] if identity else metadata.get("company_id")
    if host not in _HOST_CONFIG or not isinstance(company_id, str):
        raise ValueError(f"Cannot derive JobStreet company identity from {board['board_url']!r}")

    organisation_id = metadata.get("organisation_id")
    if not isinstance(organisation_id, str) or not _NUMERIC_ID_RE.fullmatch(organisation_id):
        organisation_id = await _resolve_organisation_id(client, host, company_id)
    if organisation_id is None:
        raise ValueError(f"JobStreet company {company_id!r} was not found")

    summaries, truncated = await _fetch_summaries(
        client,
        host=host,
        organisation_id=organisation_id,
        company_id=company_id,
    )
    jobs = [_parse_summary(summary, host=host, company_id=company_id) for summary in summaries]
    log.info(
        "jobstreet.discovered",
        company_id=company_id,
        organisation_id=organisation_id,
        jobs=len(jobs),
        truncated=truncated,
    )
    return truncated_rich_result(jobs) if truncated else jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize canonical JobStreet employer profiles and verify their API."""
    _ = pw
    identity = _identity_from_url(url)
    if identity is None:
        return None
    host, company_id = identity
    result: dict[str, object] = {"host": host, "company_id": company_id}
    if client is None:
        return result
    try:
        organisation_id = await _resolve_organisation_id(client, host, company_id)
        if organisation_id is None:
            return None
        summaries, _truncated = await _fetch_summaries(
            client,
            host=host,
            organisation_id=organisation_id,
            company_id=company_id,
        )
    except Exception:
        log.debug("jobstreet.probe_failed", url=url, exc_info=True)
        return result
    result.update({"organisation_id": organisation_id, "jobs": len(summaries)})
    return result


register("jobstreet", discover, cost=10, can_handle=can_handle, rich=True)
