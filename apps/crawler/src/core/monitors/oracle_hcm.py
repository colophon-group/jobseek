"""Oracle Cloud HCM monitor.

Constructs Oracle HCM REST API URLs from a ``host`` and ``site`` in the
board metadata. Supports pagination via the ``finder`` param's ``offset``
suffix.

Board metadata:
    host        Oracle HCM tenant hostname (e.g. "jpmc.fa.oraclecloud.com")
    site        Career site identifier (e.g. "CX_1001", "CampusHiring")
    organization_id
                Optional Oracle organization facet ID for shared career sites
    fields      Optional field mapping override (defaults provided)

The monitor returns rich data (title, location, date, employment_type).
Pair with the oracle_hcm scraper for description enrichment.
"""

from __future__ import annotations

import asyncio
import random
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

# Transient upstream failures are common on ``*.fa.em2.oraclecloud.com`` because
# dozens of Oracle HCM tenants share the same backend. A single Oracle-side
# hiccup returns 503 for every tenant we hit during that window (issue #2217:
# 15 distinct boards all 503'd inside a 2m15s window on 2026-04-17 14:31:59Z
# — one Oracle infra burp, not 15 separate board failures). Oracle also emitted
# transient 302 responses with no usable redirect for two tenants in #5715.
# Albertsons (#6394) additionally returns intermittent Akamai 403 responses
# for valid finder-pagination requests; retrying the identical request
# succeeds, so these are upstream/WAF transients rather than authorization
# failures.
#
# Retry in-place with jittered exponential backoff before giving up to the
# board-level backoff (``_RECORD_FAILURE``, which doubles the next-check
# interval). If Oracle recovers within a few seconds, the monitor run still
# completes successfully and no ``batch.monitor.error`` fires.
#
# Attempts chosen conservatively — 3 × ~6-18s covers Oracle's typical burp
# window without stretching a single monitor beyond the 10-min lease budget.
_TRANSIENT_STATUS = frozenset({302, 403, 429, 500, 502, 503, 504})
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 3.0
_PAGE_SIZE = 200
_RESULT_WINDOW_LIMIT = 10_000

# Oracle's public search endpoint stops serving unfiltered offsets at 10,000.
# Category and organization facets are mutually exclusive on the tenants we
# support, but only trust them when their counts prove that they form a complete
# partition of the advertised result set. This lets very large tenants be read
# in full without making assumptions about the provider's taxonomy.
_PARTITION_FACETS = (
    ("categoriesFacet", "selectedCategoriesFacet"),
    ("organizationsFacet", "selectedOrganizationsFacet"),
)


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, *, timeout: float = 30.0
) -> httpx.Response:
    """GET with exponential-jitter backoff on transient 302/403/429/5xx.

    On a non-transient status or after exhausting retries, returns the final
    response — the caller should still call ``raise_for_status()`` on it so
    persistent upstream failures still propagate to ``_RECORD_FAILURE``.
    """
    resp: httpx.Response | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        resp = await client.get(url, timeout=timeout)
        if resp.status_code not in _TRANSIENT_STATUS:
            return resp
        if attempt == _RETRY_ATTEMPTS - 1:
            break
        base_delay = _RETRY_BASE_DELAY_S * (2**attempt)
        jittered = base_delay * random.uniform(0.8, 1.2)
        log.warning(
            "oracle_hcm.transient_retry",
            url=url,
            status=resp.status_code,
            attempt=attempt + 1,
            backoff_s=round(jittered, 2),
        )
        await asyncio.sleep(jittered)
    # resp is guaranteed non-None: the first request always assigns it or raises,
    # and the loop only breaks on transient status after at least one assignment.
    assert resp is not None
    return resp


_DEFAULT_FIELDS = {
    "title": "Title",
    "locations": "PrimaryLocation",
    "date_posted": "PostedDate",
    "employment_type": "JobSchedule",
}

_PAGE_SIZE = 200

_ORACLE_HCM_HOST_RE = re.compile(
    r"^(?:[a-z0-9-]{1,63}\.)+fa\.(?:(?:[a-z]{2}\d+|ocs)\.)?"
    r"oraclecloud(?:\d{1,3})?\.(?:com|eu)$",
    re.IGNORECASE,
)
_ORACLE_SITE_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_ORACLE_JOB_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
_ORACLE_FACET_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,256}")


def _normalize_oracle_host(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    host = value.strip().lower().rstrip(".")
    return host if _ORACLE_HCM_HOST_RE.fullmatch(host) else None


def _normalize_oracle_site(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if _ORACLE_SITE_RE.fullmatch(value) else None


def _normalize_oracle_facet_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return None
    return value if _ORACLE_FACET_ID_RE.fullmatch(value) else None


def _organization_id_from_url(url: str) -> str | None:
    """Return a validated organization facet from a public board URL."""
    try:
        values = parse_qs(urlparse(url).query, keep_blank_values=True).get(
            "selectedOrganizationsFacet"
        )
    except ValueError as exc:
        raise ValueError("Oracle HCM board URL has an invalid query string") from exc
    if values is None:
        return None
    if len(values) != 1:
        raise ValueError("Oracle HCM board URL must select exactly one organization facet")
    organization_id = _normalize_oracle_facet_id(values[0])
    if organization_id is None:
        raise ValueError("Oracle HCM board URL has an invalid organization facet ID")
    return organization_id


def _configured_organization_id(metadata: dict, board_url: str) -> str | None:
    """Resolve one company-scoping organization facet, failing on conflicts."""
    raw_organization_id = metadata.get("organization_id")
    organization_id = _normalize_oracle_facet_id(raw_organization_id)
    if raw_organization_id is not None and organization_id is None:
        raise ValueError("Oracle HCM organization_id metadata is invalid")

    url_organization_id = _organization_id_from_url(board_url)
    if (
        organization_id is not None
        and url_organization_id is not None
        and organization_id != url_organization_id
    ):
        raise ValueError("Oracle HCM organization_id conflicts with the board URL facet")
    return organization_id or url_organization_id


def _parse_candidate_url(
    url: str, *, require_job: bool = False
) -> tuple[str, str, str | None] | None:
    """Parse a trusted Oracle Candidate Experience URL."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = _normalize_oracle_host(parsed.hostname)
    if (
        parsed.scheme != "https"
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) < 5
        or parts[0:2] != ["hcmUI", "CandidateExperience"]
        or re.fullmatch(r"[A-Za-z]{2}(?:[-_][A-Za-z]{2})?", parts[2]) is None
        or parts[3] != "sites"
    ):
        return None
    site = _normalize_oracle_site(parts[4])
    if site is None:
        return None
    tail = parts[5:]
    job_id: str | None = None
    if not tail or tail in (["jobs"], ["requisitions"]):
        pass
    elif len(tail) == 2 and tail[0] == "job" and _ORACLE_JOB_ID_RE.fullmatch(tail[1]):
        job_id = tail[1]
    elif (
        len(tail) == 3
        and tail[0:2] == ["requisitions", "preview"]
        and _ORACLE_JOB_ID_RE.fullmatch(tail[2])
    ):
        job_id = tail[2]
    else:
        return None
    if require_job and job_id is None:
        return None
    return host, site, job_id


def _build_api_url(host: str, site: str, organization_id: str | None = None) -> str:
    url = (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        f"?onlyData=true"
        f"&expand=requisitionList.workLocation,requisitionList.secondaryLocations"
        f"&finder=findReqs;siteNumber={site}"
        f",facetsList=LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES"
        f"%3BCATEGORIES%3BORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS"
        f",limit=200,sortBy=POSTING_DATES_DESC"
    )
    if organization_id is not None:
        url += f",selectedOrganizationsFacet={quote(organization_id, safe='')}"
    return url


def _build_url_template(host: str, site: str) -> str:
    return f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{{Id}}"


def _complete_facet_partition(wrapper: dict, total: int) -> list[tuple[str, str, int]] | None:
    """Return a safe set of finder filters for a result set above 10,000.

    A facet is usable only when every bucket is well formed, no bucket exceeds
    Oracle's result-window limit, and the bucket counts add up exactly to the
    unfiltered total. Otherwise the ordinary pagination path is retained and
    will report a truncated cycle at Oracle's cap.
    """
    for response_field, finder_parameter in _PARTITION_FACETS:
        facets = wrapper.get(response_field)
        if not isinstance(facets, list) or not facets:
            continue

        partitions: list[tuple[str, str, int]] = []
        seen_ids: set[str] = set()
        valid = True
        for facet in facets:
            if not isinstance(facet, dict):
                valid = False
                break
            facet_id = facet.get("Id")
            count = facet.get("TotalCount")
            if (
                facet_id is None
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count <= 0
                or count > _RESULT_WINDOW_LIMIT
            ):
                valid = False
                break
            facet_id = str(facet_id)
            if facet_id in seen_ids:
                valid = False
                break
            seen_ids.add(facet_id)
            partitions.append((finder_parameter, facet_id, count))

        if valid and sum(count for _, _, count in partitions) == total:
            return partitions

    return None


def _validate_organization_filter(wrapper: dict, organization_id: str, total: int) -> None:
    """Prove that Oracle applied the requested company organization facet."""
    selected = _normalize_oracle_facet_id(wrapper.get("SelectedOrganizationsFacet"))
    facets = wrapper.get("organizationsFacet")
    if selected != organization_id or not isinstance(facets, list):
        raise ValueError("Oracle HCM did not apply the configured organization filter")

    matching = [
        facet
        for facet in facets
        if isinstance(facet, dict)
        and _normalize_oracle_facet_id(facet.get("Id")) == organization_id
    ]
    if len(matching) != 1 or matching[0].get("TotalCount") != total:
        raise ValueError("Oracle HCM organization facet does not match the filtered total")


async def can_handle(
    url: str,
    client: httpx.AsyncClient,
    pw=None,
) -> dict | None:
    """Detect Oracle Cloud HCM career sites."""
    candidate = _parse_candidate_url(url)
    if candidate is None:
        return None
    host, site, _job_id = candidate
    try:
        organization_id = _organization_id_from_url(url)
    except ValueError:
        return None

    # Verify API is accessible
    api_url = _build_api_url(host, site, organization_id)
    try:
        resp = await _get_with_retry(client, api_url, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        wrapper = data.get("items", [{}])[0]
        total = wrapper.get("TotalJobsCount", 0)
        if total == 0:
            return None
        if organization_id is not None:
            _validate_organization_filter(wrapper, organization_id, total)
    except Exception:
        return None

    config = {"host": host, "site": site, "jobs_count": total}
    if organization_id is not None:
        config["organization_id"] = organization_id
    return config


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | MonitorResult:
    """Discover available jobs via the monitor's native streaming implementation.

    Keeping the non-streaming entry point on the same Oracle-specific path is
    important for workspace validation, which calls ``discover`` directly.
    The generic API sniffer cannot represent Oracle's compound ``finder``
    pagination reliably and previously replayed the first page until its page
    cap, returning only a small changing subset for large tenants.
    """
    jobs: list[DiscoveredJob] = []
    truncated = False
    async for batch in discover_stream(board, client, pw=pw):
        if isinstance(batch, list):
            jobs.extend(batch)
        elif batch.truncated:
            truncated = True
    return truncated_rich_result(jobs) if truncated else jobs


async def discover_stream(board: dict, client: httpx.AsyncClient, pw=None):
    """Yield batches of DiscoveredJob per API page, pulsing heartbeats."""
    metadata = board.get("metadata") or {}
    raw_host = metadata.get("host")
    raw_site = metadata.get("site")
    host = _normalize_oracle_host(raw_host)
    site = _normalize_oracle_site(raw_site)
    if raw_host is not None and host is None:
        raise ValueError("Oracle HCM host metadata is invalid")
    if raw_site is not None and site is None:
        raise ValueError("Oracle HCM site metadata is invalid")

    if not host or not site:
        candidate = _parse_candidate_url(board["board_url"])
        if candidate is None:
            log.error("oracle_hcm.missing_host_or_site", board_url=board["board_url"])
            return
        host = host or candidate[0]
        site = site or candidate[1]

    fields = metadata.get("fields") or _DEFAULT_FIELDS
    organization_id = _configured_organization_id(metadata, board["board_url"])
    offset_overlap = metadata.get("offset_overlap", 0)
    if (
        isinstance(offset_overlap, bool)
        or not isinstance(offset_overlap, int)
        or not 0 <= offset_overlap < _PAGE_SIZE
    ):
        raise ValueError(f"offset_overlap must be an integer from 0 to {_PAGE_SIZE - 1}")
    total_count_tolerance = metadata.get("total_count_tolerance", 0)
    if (
        isinstance(total_count_tolerance, bool)
        or not isinstance(total_count_tolerance, int)
        or total_count_tolerance < 0
    ):
        raise ValueError("total_count_tolerance must be a non-negative integer")
    page_shortfall_tolerance = metadata.get("page_shortfall_tolerance", 0)
    if (
        isinstance(page_shortfall_tolerance, bool)
        or not isinstance(page_shortfall_tolerance, int)
        or not 0 <= page_shortfall_tolerance < _PAGE_SIZE
    ):
        raise ValueError(f"page_shortfall_tolerance must be an integer from 0 to {_PAGE_SIZE - 1}")
    duplicate_row_tolerance = metadata.get("duplicate_row_tolerance", 0)
    if (
        isinstance(duplicate_row_tolerance, bool)
        or not isinstance(duplicate_row_tolerance, int)
        or duplicate_row_tolerance < 0
    ):
        raise ValueError("duplicate_row_tolerance must be a non-negative integer")
    offset_step = _PAGE_SIZE - offset_overlap
    url_template = _build_url_template(host, site)
    api_url = _build_api_url(host, site, organization_id)

    resp = await _get_with_retry(client, api_url, timeout=30)
    resp.raise_for_status()
    initial_wrapper = (resp.json().get("items") or [{}])[0]
    total = initial_wrapper.get("TotalJobsCount") or 0
    if total == 0:
        return
    if organization_id is not None:
        _validate_organization_filter(initial_wrapper, organization_id, total)

    partitions = (
        _complete_facet_partition(initial_wrapper, total) if total > _RESULT_WINDOW_LIMIT else None
    )
    if partitions:
        page_sources: list[tuple[str, int, dict | None, str | None]] = [
            (
                f"{api_url},{parameter}={quote(facet_id, safe='')}",
                count,
                None,
                facet_id,
            )
            for parameter, facet_id, count in partitions
        ]
        log.info(
            "oracle_hcm.partitioned_search",
            host=host,
            site=site,
            advertised_total=total,
            facet=partitions[0][0],
            partitions=len(partitions),
        )
    else:
        page_sources = [(api_url, total, initial_wrapper, None)]

    seen_job_ids: set[str] = set()
    minimum_advertised_total = 0
    duplicate_rows = 0
    partial = False
    for source_url, source_total, cached_wrapper, partition_id in page_sources:
        offset = 0
        latest_source_total = source_total
        minimum_source_total = source_total
        source_seen_job_ids: set[str] = set()
        while offset < latest_source_total:
            if offset == 0 and cached_wrapper is not None:
                wrapper = cached_wrapper
            else:
                page_url = f"{source_url},offset={offset}" if offset else source_url
                resp = await _get_with_retry(client, page_url, timeout=30)
                resp.raise_for_status()
                wrapper = (resp.json().get("items") or [{}])[0]

            page_total = wrapper.get("TotalJobsCount")
            if organization_id is not None:
                _validate_organization_filter(wrapper, organization_id, page_total)
            if page_total is not None:
                if (
                    isinstance(page_total, bool)
                    or not isinstance(page_total, int)
                    or page_total < 0
                ):
                    partial = True
                elif page_total != latest_source_total:
                    total_drop = latest_source_total - page_total
                    # Plain offset pagination cannot absorb a boundary shift.
                    # An explicit overlap safely covers deletions up to that
                    # margin; insertions only repeat already-seen rows and can
                    # wait until the next cycle.
                    if offset_overlap == 0 or total_drop > offset_overlap:
                        partial = True
                    previous_total = latest_source_total
                    latest_source_total = page_total
                    minimum_source_total = min(minimum_source_total, page_total)
                    log.warning(
                        "oracle_hcm.total_changed",
                        host=host,
                        site=site,
                        initial_total=source_total,
                        previous_total=previous_total,
                        page_total=page_total,
                        offset=offset,
                        partition_id=partition_id,
                    )

            items = wrapper.get("requisitionList", [])
            expected_page_size = min(_PAGE_SIZE, max(latest_source_total - offset, 0))
            page_shortfall = expected_page_size - len(items)
            if len(items) > _PAGE_SIZE or (
                page_shortfall > 0
                and (
                    (
                        offset + _PAGE_SIZE < latest_source_total
                        and page_shortfall > page_shortfall_tolerance
                    )
                    or (
                        offset + _PAGE_SIZE >= latest_source_total
                        and page_shortfall > total_count_tolerance
                    )
                )
            ):
                partial = True
            if not items:
                if offset < latest_source_total:
                    log.warning(
                        "oracle_hcm.truncated",
                        host=host,
                        site=site,
                        discovered=len(seen_job_ids),
                        advertised_total=total,
                        partition_id=partition_id,
                    )
                break

            jobs: list[DiscoveredJob] = []
            page_job_ids: set[str] = set()
            page_duplicate_rows = 0
            for item in items:
                job_id = item.get("Id")
                if not job_id:
                    partial = True
                    continue
                job_id = str(job_id)
                if job_id in page_job_ids:
                    # Some verified tenants return duplicate database rows
                    # inside one response page and include them in
                    # TotalJobsCount. Keep this separate from cross-page
                    # overlap so ordinary boards remain fail-closed.
                    duplicate_rows += 1
                    page_duplicate_rows += 1
                    if duplicate_rows > duplicate_row_tolerance:
                        partial = True
                    continue
                page_job_ids.add(job_id)
                if job_id in source_seen_job_ids:
                    # Cross-page duplicates are the expected overlap margin.
                    # Without overlap they prove that Oracle reshuffled an
                    # offset boundary during the cycle.
                    if offset_overlap == 0:
                        partial = True
                    continue
                source_seen_job_ids.add(job_id)
                if job_id in seen_job_ids:
                    # Verified facet partitions must not overlap each other.
                    partial = True
                    continue
                seen_job_ids.add(job_id)
                url = url_template.format(Id=job_id)
                jobs.append(
                    DiscoveredJob(
                        url=url,
                        title=item.get(fields.get("title", "Title")),
                        locations=[item[fields["locations"]]]
                        if item.get(fields.get("locations", "PrimaryLocation"))
                        else None,
                        date_posted=item.get(fields.get("date_posted", "PostedDate")),
                        employment_type=item.get(fields.get("employment_type", "JobSchedule")),
                    )
                )

            if page_duplicate_rows:
                log.warning(
                    "oracle_hcm.page_duplicates",
                    host=host,
                    site=site,
                    offset=offset,
                    duplicates=page_duplicate_rows,
                    duplicates_total=duplicate_rows,
                    duplicate_row_tolerance=duplicate_row_tolerance,
                    partition_id=partition_id,
                )

            if jobs:
                yield jobs
                log.debug(
                    "oracle_hcm.stream_batch",
                    offset=offset,
                    batch=len(jobs),
                    total=latest_source_total,
                    partition_id=partition_id,
                )

            offset += offset_step

        minimum_advertised_total += minimum_source_total

    if len(seen_job_ids) < minimum_advertised_total - total_count_tolerance:
        partial = True
    if partial:
        log.warning(
            "oracle_hcm.partial_cycle",
            host=host,
            site=site,
            discovered=len(seen_job_ids),
            advertised_total=total,
            minimum_total=minimum_advertised_total,
            offset_overlap=offset_overlap,
            total_count_tolerance=total_count_tolerance,
            page_shortfall_tolerance=page_shortfall_tolerance,
            duplicate_rows=duplicate_rows,
            duplicate_row_tolerance=duplicate_row_tolerance,
        )
        yield truncated_rich_result([])

    log.info(
        "oracle_hcm.stream_done",
        host=host,
        site=site,
        total=total,
        discovered=len(seen_job_ids),
    )


register("oracle_hcm", discover, cost=15, can_handle=can_handle, rich=True, stream=discover_stream)
