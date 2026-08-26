"""Workday Job Board API monitor.

Discovers job URLs via the Workday list API.  Does **not** fetch individual
job details — that is handled by the ``workday`` scraper which hits the
detail endpoint on a daily scrape schedule.

Public API:
  List: POST https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs

Max ``limit`` per request is **20** (higher values return 400).

Some tenants cap results at **2000** per query. When `total` reaches 2000 the
monitor automatically splits into per-facet queries (e.g. by job category)
so that each sub-query stays below the cap, then deduplicates. Tenants without
a safe split fall back to verified direct pagination beyond offset 2000.

Multi-site discovery
--------------------
Workday tenants expose all their job board sites in ``robots.txt`` as
``Sitemap:`` entries.  By default the monitor discovers **all** sites for
the tenant and aggregates jobs from every site in a single run. Tenants can
publish the same requisition on multiple sites. Workday may add a site-specific
numeric suffix to an otherwise identical external path; those mirrors are
collapsed before URLs are emitted. To monitor only the configured site, set
``"all_sites": false`` in board metadata.

Some tenants combine jobs for distinct brands in one site. Set
``"search_text": "Brand Name"`` together with ``"all_sites": false`` to
preserve that Workday search in every direct or faceted pagination request.
"""

from __future__ import annotations

import asyncio
import math
import re

import httpx
import structlog

from src.core.monitors import fetch_page_text, register
from src.shared.http import WORKDAY_LIST_303_INCIDENT, mark_provider_incident
from src.shared.http_retry import PaginationFetchError, fetch_json_page_with_retry
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000
PAGE_SIZE = 20
_LIST_CONCURRENCY = 5  # Parallel site listing during multi-site discovery
_QUERY_CONCURRENCY = 5  # Shared bound across sites and facet/direct queries
_API_RESULT_CAP = 2000  # Workday caps list results at 2000 per query
_MAX_FACET_ID_LENGTH = 512
# Large appliedFacets arrays fail with tenant-dependent 4xx/5xx responses.
# O'Reilly accepts 100 location IDs; larger arrays fail on some tenants.
_MAX_FACET_VALUES_PER_QUERY = 100
# Pagination retry budget. Symmetric with the accenture monitor (#2735)
# and api_sniffer monitor (#2733): 3 total attempts, exponential backoff
# with full jitter starting at 1s. Slightly more relaxed than dom's
# 0.5s because Workday tenants do honour 429 Retry-After hints and a
# thundering herd of sub-second retries can entrench the rate limit.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0
# Workday returned a provider-wide burst of 303 responses without a usable
# canonical redirect across 21 boards (#5715). Following 303 would convert the
# list endpoint's POST into GET. Retry the original POST instead, then let the
# board backoff/shared host and provider circuits handle a sustained incident.
_TRANSIENT_REDIRECT_STATUSES = frozenset({303})

# In-stream sentinel used by ``_api_list_stream`` and ``_list_all_sites_stream``
# to signal that the MAX_JOBS cap was hit (#3216). Distinct from any real
# Workday path; consumers drop it and flip the cycle to partial.
# The ``_api_list_stream`` (path-only) variant yields just the path string;
# ``_list_all_sites_stream`` yields the ``(site, path)`` tuple form.
_TRUNCATED_PATH = "__workday_truncated__"
_TRUNCATED_SENTINEL = ("__workday_truncated__", _TRUNCATED_PATH)

_SITEMAP_RE = re.compile(r"myworkdayjobs\.com/([^/]+)/siteMap")
# Workday appends ``-1``, ``-2``, etc. after an already numeric requisition
# token when one posting is published through multiple tenant sites (for
# example ``Engineer_123456-2`` or ``Engineer_R-123456-2``). Requiring a digit
# before the candidate suffix is important: many tenants use stable IDs such
# as ``Engineer_R-123456``, whose numeric component is not a copy suffix.
_SITE_COPY_SUFFIX_RE = re.compile(r"(?P<stable>_[^/]*\d)-\d+$")

# Matches Workday board URLs, optionally with locale prefix (e.g. /en-US/)
_URL_RE = re.compile(
    r"([\w-]+)\.wd(\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?"
    r"(.+?)/?$"
)

_PAGE_PATTERNS = [
    re.compile(r"([\w-]+)\.wd\d+\.myworkdayjobs\.com"),
    re.compile(r"window\.workday"),
    re.compile(r"workdaycdn\.com"),
]


def _parse_components(url: str) -> tuple[str, str, str] | None:
    """Extract (company, wd_instance, site) from a Workday board URL.

    Example: https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
      -> ("nvidia", "wd5", "NVIDIAExternalCareerSite")
    """
    match = _URL_RE.search(url)
    if not match:
        return None
    company = match.group(1)
    wd_instance = f"wd{match.group(2)}"
    site = match.group(3)
    return company, wd_instance, site


def _api_base(company: str, wd_instance: str) -> str:
    return f"https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}"


def _api_list_url(company: str, wd_instance: str, site: str) -> str:
    return f"{_api_base(company, wd_instance)}/{site}/jobs"


def _job_url(company: str, wd_instance: str, site: str, external_path: str) -> str:
    return f"https://{company}.{wd_instance}.myworkdayjobs.com/{site}{external_path}"


def _cross_site_path_key(external_path: str) -> str:
    """Return the stable identity for a Workday path mirrored across sites."""
    return _SITE_COPY_SUFFIX_RE.sub(r"\g<stable>", external_path)


def _configured_search_text(metadata: dict, *, all_sites: bool) -> str | None:
    """Return a validated single-site Workday search constraint."""
    search_text = metadata.get("search_text")
    if search_text is None:
        return None
    if not isinstance(search_text, str) or not search_text.strip():
        raise ValueError("Workday search_text must be a non-empty string")
    if all_sites:
        raise ValueError("Workday search_text requires all_sites=false")
    return search_text


def _configured_split_facet(metadata: dict) -> str | None:
    """Return an optional tenant-proven exhaustive Workday facet."""
    split_facet = metadata.get("split_facet")
    if split_facet is None:
        return None
    if (
        not isinstance(split_facet, str)
        or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", split_facet) is None
    ):
        raise ValueError("Workday split_facet must be a provider facet name up to 128 characters")
    return split_facet


# ── List pagination ──────────────────────────────────────────────────


async def _post_page_with_retry(
    client: httpx.AsyncClient,
    list_url: str,
    payload: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> dict:
    """POST a Workday list-API page with bounded retries (#2748)."""
    try:
        return await fetch_json_page_with_retry(
            client,
            list_url,
            method="POST",
            json_body=payload,
            headers={"Content-Type": "application/json"},
            expect_shape=dict,
            retryable_statuses=_TRANSIENT_REDIRECT_STATUSES,
            retries=retries,
            base_delay=base_delay,
            log_event="workday.list_backoff",
            sleep=asyncio.sleep,
        )
    except PaginationFetchError as exc:
        # Only an exhausted 303 retry budget is safe evidence of the
        # provider-wide incident seen in #5715. A recovered 303, ordinary
        # HTTP error, parser failure, or configuration failure cannot mark it.
        if exc.last_status == 303:
            mark_provider_incident(exc.url, incident=WORKDAY_LIST_303_INCIDENT)
        raise


async def _paginate_query(
    list_url: str,
    body: dict,
    client: httpx.AsyncClient,
    *,
    cap_abort: int = 0,
) -> tuple[list[str], int, list[dict]]:
    """Paginate a single list query. Returns (paths, total, facets).

    When *cap_abort* > 0 and ``total >= cap_abort`` after the first page,
    return immediately with only the first page's results.  This avoids
    fetching up to 100 pages that will be discarded when the caller is
    only interested in the total and facets for splitting.

    Failure semantics (#2748). Each page POST is wrapped by
    :func:`_post_page_with_retry`, which raises
    :class:`PaginationFetchError` on persistent transient failures or
    non-retryable 4xx. The exception propagates out of this function;
    callers (``_api_list``, ``_api_list_stream``,
    ``_list_all_sites``) do not have a try/except around the call,
    so the run surfaces in ``_process_one_board_streaming``'s generic
    ``except Exception`` and is recorded as a failure (no silent
    truncation — same shape of bug as #2722, #2737).
    """
    paths: list[str] = []
    total = 0
    facets: list[dict] = []
    offset = body.get("offset", 0)

    while True:
        payload = {**body, "limit": PAGE_SIZE, "offset": offset}
        data = await _post_page_with_retry(client, list_url, payload)

        if offset == 0:
            total = data.get("total", 0)
            facets = data.get("facets", [])

        postings = data.get("jobPostings", [])
        for item in postings:
            path = item.get("externalPath")
            if path:
                paths.append(path)

        offset += len(postings)
        if not postings or offset >= total:
            break

        # Early abort: we only needed total + facets from the first page
        if cap_abort and total >= cap_abort:
            log.info("workday.cap_abort", total=total, cap=cap_abort, fetched=len(paths))
            break

        if len(paths) >= MAX_JOBS:
            break

    return paths, total, facets


def _iter_facets(facets: list[dict]):
    """Yield Workday facets, including nested facet groups.

    Most tenants return a flat list, but some wrap usable facets in a group
    value (for example ``locationMainGroup`` -> ``locations``).  Treat those
    wrappers as containers so large boards can still find a safe partition.
    """
    for facet in facets:
        if not isinstance(facet, dict):
            continue
        yield facet
        nested = [
            value
            for value in facet.get("values", [])
            if isinstance(value, dict) and value.get("facetParameter")
        ]
        yield from _iter_facets(nested)


def _pick_split_facet(
    facets: list[dict],
    preferred: str | None = None,
) -> tuple[str, list[str]] | None:
    """Choose a facet to split on when results hit the 2000 cap.

    Picks the facet with the most values where no single value >= cap,
    so each sub-query stays under the limit. A configured *preferred* facet
    is an operator assertion that the named dimension is exhaustive for this
    tenant. Fail closed when Workday stops exposing that facet or one of its
    values is itself capped.
    """
    if preferred is not None:
        facet = next(
            (
                candidate
                for candidate in _iter_facets(facets)
                if candidate.get("facetParameter") == preferred
            ),
            None,
        )
        if facet is None:
            raise ValueError(f"Workday split_facet {preferred!r} was not advertised")
        values = facet.get("values", [])
        if not isinstance(values, list) or not values:
            raise ValueError(f"Workday split_facet {preferred!r} advertised no values")

        ids: list[str] = []
        seen_ids: set[str] = set()
        for value in values:
            if not isinstance(value, dict):
                raise ValueError(f"Workday split_facet {preferred!r} contains a malformed value")
            facet_id = value.get("id")
            if (
                not isinstance(facet_id, str)
                or not facet_id
                or len(facet_id) > _MAX_FACET_ID_LENGTH
                or facet_id.strip() != facet_id
                or "\x00" in facet_id
            ):
                raise ValueError(f"Workday split_facet {preferred!r} contains an invalid value id")
            count = value.get("count")
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
                or count >= _API_RESULT_CAP
            ):
                raise ValueError(
                    f"Workday split_facet {preferred!r} contains an unsafe value count"
                )
            if facet_id in seen_ids:
                raise ValueError(f"Workday split_facet {preferred!r} contains a duplicate value id")
            seen_ids.add(facet_id)
            ids.append(facet_id)
        return preferred, ids

    best: tuple[str, list[str]] | None = None
    best_count = 0

    for facet in _iter_facets(facets):
        param = facet.get("facetParameter")
        values = facet.get("values", [])
        if not param or not values:
            continue
        # Skip facets where any single value is >= cap
        if any(v.get("count", 0) >= _API_RESULT_CAP for v in values):
            continue
        ids = [v["id"] for v in values if "id" in v]
        if len(ids) > best_count:
            best = (param, ids)
            best_count = len(ids)

    return best


def _group_split_facet_values(
    facets: list[dict],
    facet_param: str,
    facet_ids: list[str],
) -> list[list[str]]:
    """Group facet IDs into OR queries that remain below Workday's cap.

    Querying every value separately is prohibitively expensive for facets
    such as location, which can contain thousands of values.  Workday ORs
    values within one applied facet, and the advertised counts provide a
    safe upper bound for the combined result count.  Unknown counts retain
    the previous one-value-per-query behaviour.
    """
    counts: dict[str, int] = {}
    for facet in _iter_facets(facets):
        if facet.get("facetParameter") != facet_param:
            continue
        for value in facet.get("values", []):
            if not isinstance(value, dict) or "id" not in value:
                continue
            count = value.get("count")
            if isinstance(count, int) and count >= 0:
                counts[value["id"]] = count

    groups: list[list[str]] = []
    current: list[str] = []
    current_count = 0
    for facet_id in facet_ids:
        # An unknown count cannot safely share a capped query with another
        # value.  Giving it the full per-query budget keeps it isolated.
        count = counts.get(facet_id, _API_RESULT_CAP - 1)
        if current and (
            current_count + count >= _API_RESULT_CAP or len(current) >= _MAX_FACET_VALUES_PER_QUERY
        ):
            groups.append(current)
            current = []
            current_count = 0
        current.append(facet_id)
        current_count += count

    if current:
        groups.append(current)
    return groups


def _materially_below_advertised_total(discovered: int, advertised: int) -> bool:
    """Allow normal in-crawl churn, but never accept a materially partial list."""
    tolerance = max(1, math.ceil(advertised * 0.01))
    return discovered < advertised - tolerance


def _assert_complete_inventory(
    *,
    discovered: int,
    advertised: int,
    company: str,
    site: str,
    strategy: str,
) -> None:
    if _materially_below_advertised_total(discovered, advertised):
        raise RuntimeError(
            "Workday "
            f"{strategy} returned {discovered} of {advertised} "
            f"advertised unique jobs for {company}/{site}"
        )


async def _direct_pagination_stream(
    list_url: str,
    advertised_total: int,
    company: str,
    site: str,
    client: httpx.AsyncClient,
    base_body: dict | None = None,
    known_paths: set[str] | None = None,
):
    """Yield an unfaceted query page by page and verify complete coverage.

    ``known_paths`` carries a bounded first-pass inventory into a recovery
    pass. This lets two individually churned snapshots reconcile to the
    advertised total without weakening the final completeness assertion.
    """
    expected = min(advertised_total, MAX_JOBS)
    offset = 0
    seen = set(known_paths or ())

    while offset < expected:
        payload = {**(base_body or {}), "limit": PAGE_SIZE, "offset": offset}
        data = await _post_page_with_retry(client, list_url, payload)
        postings = data.get("jobPostings", [])
        if not postings:
            break

        batch: list[str] = []
        for item in postings:
            path = item.get("externalPath")
            if path and path not in seen:
                seen.add(path)
                batch.append(path)

        offset += len(postings)
        if batch:
            yield batch

    _assert_complete_inventory(
        discovered=len(seen),
        advertised=expected,
        company=company,
        site=site,
        strategy="direct pagination",
    )

    if advertised_total > MAX_JOBS:
        yield [_TRUNCATED_PATH]


async def _api_list(
    company: str,
    wd_instance: str,
    site: str,
    client: httpx.AsyncClient,
    *,
    query_sem: asyncio.Semaphore | None = None,
    search_text: str | None = None,
    split_facet: str | None = None,
) -> tuple[list[str], bool]:
    """Collect all externalPaths, splitting by facet if the 2000 cap is hit.

    Returns ``(paths, truncated)``. ``truncated`` is True iff the stream
    yielded :data:`_TRUNCATED_PATH` — i.e. the MAX_JOBS cap was hit. The
    sentinel is stripped from ``paths`` so callers can ignore it; callers
    that care about the partial-cycle signal (e.g. the non-streaming
    ``discover``) consume the bool instead.
    """
    paths: list[str] = []
    truncated = False
    async for batch in _api_list_stream(
        company,
        wd_instance,
        site,
        client,
        query_sem=query_sem,
        search_text=search_text,
        split_facet=split_facet,
    ):
        for p in batch:
            if p == _TRUNCATED_PATH:
                truncated = True
            else:
                paths.append(p)
    return paths, truncated


async def _api_list_stream(
    company: str,
    wd_instance: str,
    site: str,
    client: httpx.AsyncClient,
    *,
    query_sem: asyncio.Semaphore | None = None,
    search_text: str | None = None,
    split_facet: str | None = None,
):
    """Yield batches of externalPaths, splitting by facet if the 2000 cap is hit."""
    list_url = _api_list_url(company, wd_instance, site)
    query_sem = query_sem or asyncio.Semaphore(_QUERY_CONCURRENCY)
    base_body = {"searchText": search_text} if search_text else {}

    # First, try unfaceted query (abort early if over cap — we only need facets)
    async with query_sem:
        paths, total, facets = await _paginate_query(
            list_url,
            base_body,
            client,
            cap_abort=_API_RESULT_CAP,
        )

    if total < _API_RESULT_CAP:
        unique_paths = list(dict.fromkeys(paths))
        expected = min(total, MAX_JOBS)
        if _materially_below_advertised_total(len(unique_paths), expected):
            # Workday inventories can churn between page requests and some
            # tenants occasionally repeat rows at an offset. Reconcile one
            # bounded unfaceted pass with the first snapshot, then retain the
            # same fail-closed completeness assertion in the direct helper.
            log.warning(
                "workday.pagination_incomplete_direct_recovery",
                company=company,
                site=site,
                discovered=len(unique_paths),
                advertised=expected,
                deficit=expected - len(unique_paths),
            )
            recovered = list(unique_paths)
            recovered_seen = set(unique_paths)
            async with query_sem:
                async for direct_batch in _direct_pagination_stream(
                    list_url,
                    total,
                    company,
                    site,
                    client,
                    base_body=base_body,
                    known_paths=recovered_seen,
                ):
                    for path in direct_batch:
                        if path not in recovered_seen:
                            recovered_seen.add(path)
                            recovered.append(path)
            unique_paths = recovered

        _assert_complete_inventory(
            discovered=len(unique_paths),
            advertised=expected,
            company=company,
            site=site,
            strategy="pagination",
        )
        yield unique_paths[:MAX_JOBS]
        if total > MAX_JOBS:
            yield [_TRUNCATED_PATH]
        return

    # Hit the cap — split by facet to get all jobs
    split = _pick_split_facet(facets, preferred=split_facet)
    if not split:
        # The 2,000-result cap is tenant-specific. Some tenants expose no
        # safe facet but accept offsets beyond 2,000. Verify direct pagination
        # rather than accepting the first page as a complete inventory.
        log.info(
            "workday.direct_pagination_fallback",
            company=company,
            site=site,
            total=total,
        )
        async with query_sem:
            async for direct_batch in _direct_pagination_stream(
                list_url,
                total,
                company,
                site,
                client,
                base_body=base_body,
            ):
                yield direct_batch
        return

    facet_param, facet_ids = split
    facet_groups = _group_split_facet_values(facets, facet_param, facet_ids)
    log.info(
        "workday.splitting_by_facet",
        company=company,
        site=site,
        facet=facet_param,
        values=len(facet_ids),
        queries=len(facet_groups),
    )

    async def _paginate_facet_group(
        facet_group: list[str],
    ) -> tuple[list[str], int, int]:
        body = {
            **base_body,
            "appliedFacets": {facet_param: facet_group},
        }
        async with query_sem:
            sub_paths, sub_total, _ = await _paginate_query(list_url, body, client)
        if sub_total >= _API_RESULT_CAP:
            raise RuntimeError(
                "Workday split facet group remained capped at "
                f"{sub_total} jobs for {company}/{site}"
            )
        _assert_complete_inventory(
            discovered=len(sub_paths),
            advertised=sub_total,
            company=company,
            site=site,
            strategy=f"{facet_param} facet group",
        )
        return sub_paths, sub_total, len(facet_group)

    seen: set[str] = set()
    tasks = [asyncio.create_task(_paginate_facet_group(group)) for group in facet_groups]
    try:
        for completed in asyncio.as_completed(tasks):
            sub_paths, sub_total, group_values = await completed
            sub_unique = len(set(sub_paths))
            log.info(
                "workday.facet_group_total",
                company=company,
                site=site,
                facet=facet_param,
                values=group_values,
                advertised=sub_total,
                rows=len(sub_paths),
                unique=sub_unique,
            )
            new_paths: list[str] = []
            for path in sub_paths:
                if path not in seen:
                    seen.add(path)
                    new_paths.append(path)
                    if total > MAX_JOBS and len(seen) >= MAX_JOBS:
                        break

            if new_paths:
                yield new_paths

            if total > MAX_JOBS and len(seen) >= MAX_JOBS:
                log.warning(
                    "workday.truncated",
                    company=company,
                    site=site,
                    total=len(seen),
                    cap=MAX_JOBS,
                )
                yield [_TRUNCATED_PATH]
                return
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    expected = min(total, MAX_JOBS)
    if _materially_below_advertised_total(len(seen), expected):
        # Some Workday facets are neither exhaustive nor mutually exclusive.
        # PWC, for example, advertises overlapping location groups that omit
        # jobs without a location facet, while the same tenant supports deep
        # direct offsets. Preserve the fast facet path for complete tenants,
        # then use the already verified direct-pagination path only when the
        # deduplicated facet inventory is materially short. The direct helper
        # retains the same completeness assertion, so capped tenants still
        # fail closed instead of accepting a partial inventory.
        log.warning(
            "workday.faceted_incomplete_direct_fallback",
            company=company,
            site=site,
            discovered=len(seen),
            advertised=expected,
            deficit=expected - len(seen),
        )
        async with query_sem:
            async for direct_batch in _direct_pagination_stream(
                list_url,
                total,
                company,
                site,
                client,
                base_body=base_body,
                known_paths=seen,
            ):
                if direct_batch == [_TRUNCATED_PATH]:
                    yield direct_batch
                    continue
                new_paths = [path for path in direct_batch if path not in seen]
                seen.update(new_paths)
                if new_paths:
                    yield new_paths

    _assert_complete_inventory(
        discovered=len(seen),
        advertised=expected,
        company=company,
        site=site,
        strategy="faceted pagination",
    )

    log.info("workday.faceted_total", company=company, site=site, jobs=len(seen))


# ── Multi-site discovery ─────────────────────────────────────────────


async def _discover_sites(company: str, wd_instance: str, client: httpx.AsyncClient) -> list[str]:
    """Discover all job board sites for a Workday tenant via robots.txt."""
    url = f"https://{company}.{wd_instance}.myworkdayjobs.com/robots.txt"
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            log.warning("workday.robots_failed", company=company, status=resp.status_code)
            return []
    except Exception as exc:
        log.warning("workday.robots_error", company=company, error=str(exc))
        return []

    sites: list[str] = []
    for line in resp.text.splitlines():
        if line.startswith("Sitemap:"):
            m = _SITEMAP_RE.search(line)
            if m:
                sites.append(m.group(1))
    return sites


async def _list_all_sites(
    company: str,
    wd_instance: str,
    sites: list[str],
    client: httpx.AsyncClient,
    *,
    split_facet: str | None = None,
) -> tuple[list[tuple[str, str]], bool]:
    """List jobs from all sites concurrently. Returns ``(site_paths, truncated)``.

    ``truncated`` is True iff the aggregate exceeded ``MAX_JOBS``. Caller
    (the non-streaming ``discover``) wraps the result in a partial
    ``MonitorResult`` so the pipeline suppresses gone-detection (#3216).
    """
    sem = asyncio.Semaphore(_LIST_CONCURRENCY)
    query_sem = asyncio.Semaphore(_QUERY_CONCURRENCY)

    async def _list_one(site: str) -> tuple[list[tuple[str, str]], bool]:
        async with sem:
            if split_facet is None:
                paths, was_truncated = await _api_list(
                    company,
                    wd_instance,
                    site,
                    client,
                    query_sem=query_sem,
                )
            else:
                paths, was_truncated = await _api_list(
                    company,
                    wd_instance,
                    site,
                    client,
                    query_sem=query_sem,
                    split_facet=split_facet,
                )
            return [(site, p) for p in paths], was_truncated

    results = await asyncio.gather(*[_list_one(s) for s in sites], return_exceptions=True)

    site_paths: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    any_site_truncated = False
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            log.warning("workday.site_list_error", site=sites[i], error=str(result))
            raise result
        else:
            pairs, was_truncated = result
            for pair in pairs:
                _, path = pair
                path_key = _cross_site_path_key(path)
                if path_key in seen_paths:
                    continue
                seen_paths.add(path_key)
                site_paths.append(pair)
            if was_truncated:
                any_site_truncated = True
    truncated = any_site_truncated or len(site_paths) > MAX_JOBS
    return site_paths[:MAX_JOBS], truncated


async def _list_all_sites_stream(
    company: str,
    wd_instance: str,
    sites: list[str],
    client: httpx.AsyncClient,
    *,
    split_facet: str | None = None,
):
    """Yield (site, path) batches per site for heartbeat-aware streaming.

    On reaching ``MAX_JOBS`` yields a final sentinel batch containing
    :data:`_TRUNCATED_SENTINEL` and stops. The outer ``discover_stream``
    detects the sentinel, drops it, and emits a flagged
    :class:`MonitorResult` so the pipeline marks the run partial and
    skips gone-detection (#3216).
    """
    query_sem = asyncio.Semaphore(_QUERY_CONCURRENCY)
    total_count = 0
    seen_paths: set[str] = set()

    for site in sites:
        if split_facet is None:
            batches = _api_list_stream(
                company,
                wd_instance,
                site,
                client,
                query_sem=query_sem,
            )
        else:
            batches = _api_list_stream(
                company,
                wd_instance,
                site,
                client,
                query_sem=query_sem,
                split_facet=split_facet,
            )
        async for batch in batches:
            pairs: list[tuple[str, str]] = []
            for path in batch:
                if path == _TRUNCATED_PATH:
                    pairs.append((site, path))
                    continue
                path_key = _cross_site_path_key(path)
                if path_key in seen_paths:
                    continue
                seen_paths.add(path_key)
                pairs.append((site, path))
                total_count += 1
            if pairs:
                yield pairs
            if total_count >= MAX_JOBS:
                yield [_TRUNCATED_SENTINEL]
                return


# ── Main discover entry point ────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Discover job URLs from the Workday list API.

    By default discovers all sites for the tenant via robots.txt and
    aggregates URLs from every site.  Set ``"all_sites": false`` in board
    metadata to monitor only the configured site.

    Returns a set of job URLs (no detail fetching — that's the scraper's job),
    or a :class:`MonitorResult` with ``truncated=True`` when the MAX_JOBS
    cap was hit (#3216).
    """
    metadata = board.get("metadata") or {}
    company = metadata.get("company")
    wd_instance = metadata.get("wd_instance")
    site = metadata.get("site")

    if not (company and wd_instance and site):
        parsed = _parse_components(board["board_url"])
        if not parsed:
            raise ValueError(
                f"Cannot parse Workday components from board URL {board['board_url']!r} "
                "and no company/wd_instance/site in metadata"
            )
        company, wd_instance, site = parsed

    all_sites = metadata.get("all_sites", True)
    search_text = _configured_search_text(metadata, all_sites=all_sites)
    split_facet = _configured_split_facet(metadata)
    truncated = False

    if all_sites:
        sites = await _discover_sites(company, wd_instance, client)
        if not sites:
            log.warning("workday.no_sites_discovered", company=company, fallback=site)
            sites = [site]

        site_paths, truncated = await _list_all_sites(
            company,
            wd_instance,
            sites,
            client,
            split_facet=split_facet,
        )
        log.info(
            "workday.listed_all",
            company=company,
            sites_total=len(sites),
            sites_with_jobs=len({s for s, _ in site_paths}),
            postings=len(site_paths),
        )
    else:
        paths, truncated = await _api_list(
            company,
            wd_instance,
            site,
            client,
            search_text=search_text,
            split_facet=split_facet,
        )
        site_paths = [(site, p) for p in paths]
        log.info("workday.listed", company=company, site=site, postings=len(site_paths))

    urls = {_job_url(company, wd_instance, s, p) for s, p in site_paths}
    if truncated:
        return truncated_url_result(urls)
    return urls


async def discover_stream(board: dict, client: httpx.AsyncClient, pw=None):
    """Yield URL batches so the caller can pulse heartbeats on large boards.

    Same logic as discover() but yields intermediate sets of URLs after
    each site or facet sub-query completes, preventing worker pool timeouts.

    Strips the :data:`_TRUNCATED_PATH` / :data:`_TRUNCATED_SENTINEL` sentinels
    out of streamed batches and, on truncation, yields a final flagged
    :class:`MonitorResult` so the pipeline marks the cycle partial and
    skips gone-detection (#3216).
    """
    # Local import to avoid the top-level cycle with src.core.monitor.
    from src.core.monitor import MonitorResult as _MR

    metadata = board.get("metadata") or {}
    company = metadata.get("company")
    wd_instance = metadata.get("wd_instance")
    site = metadata.get("site")

    if not (company and wd_instance and site):
        parsed = _parse_components(board["board_url"])
        if not parsed:
            raise ValueError(
                f"Cannot parse Workday components from board URL {board['board_url']!r} "
                "and no company/wd_instance/site in metadata"
            )
        company, wd_instance, site = parsed

    all_sites = metadata.get("all_sites", True)
    search_text = _configured_search_text(metadata, all_sites=all_sites)
    split_facet = _configured_split_facet(metadata)
    truncated = False

    if all_sites:
        sites = await _discover_sites(company, wd_instance, client)
        if not sites:
            log.warning("workday.no_sites_discovered", company=company, fallback=site)
            sites = [site]

        total_urls = 0
        async for batch in _list_all_sites_stream(
            company,
            wd_instance,
            sites,
            client,
            split_facet=split_facet,
        ):
            clean: list[tuple[str, str]] = []
            for s, p in batch:
                if p == _TRUNCATED_PATH:
                    truncated = True
                else:
                    clean.append((s, p))
            if not clean:
                continue
            urls = {_job_url(company, wd_instance, s, p) for s, p in clean}
            total_urls += len(urls)
            yield urls

        log.info("workday.stream_done", company=company, total=total_urls)
    else:
        total_urls = 0
        async for batch in _api_list_stream(
            company,
            wd_instance,
            site,
            client,
            search_text=search_text,
            split_facet=split_facet,
        ):
            clean_paths = [p for p in batch if p != _TRUNCATED_PATH]
            if any(p == _TRUNCATED_PATH for p in batch):
                truncated = True
            if not clean_paths:
                continue
            urls = {_job_url(company, wd_instance, site, p) for p in clean_paths}
            total_urls += len(urls)
            yield urls

        log.info("workday.stream_done", company=company, site=site, total=total_urls)

    if truncated:
        yield _MR(urls=set(), truncated=True)


# ── Detection (used by ws probe) ─────────────────────────────────────


async def _fetch_job_count(
    company: str,
    wd_instance: str,
    site: str,
    client: httpx.AsyncClient,
) -> int | None:
    """Lightweight API call to get the job count.

    If ``total`` hits the 2000 cap, derives the true count from facet sums.
    """
    try:
        resp = await client.post(
            _api_list_url(company, wd_instance, site),
            json={"limit": 1, "offset": 0},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        total = data.get("total")
        if not isinstance(total, int):
            return None

        # If at the cap, derive true count from facet sums
        if total >= _API_RESULT_CAP:
            for facet in _iter_facets(data.get("facets", [])):
                values = facet.get("values", [])
                if values:
                    facet_sum = sum(v.get("count", 0) for v in values)
                    if facet_sum > total:
                        return facet_sum
        return total
    except Exception:
        return None


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Workday: URL pattern match -> page HTML scan.

    No slug-based probe fallback — Workday URLs are too specific to guess.
    """
    # Strategy 1: Direct URL pattern match
    parsed = _parse_components(url)
    if parsed:
        company, wd_instance, site = parsed
        result: dict = {"company": company, "wd_instance": wd_instance, "site": site}
        if client is not None:
            count = await _fetch_job_count(company, wd_instance, site, client)
            if count is not None:
                result["jobs"] = count
            elif "_" in company:
                # Python's ssl module rejects underscores in hostnames even
                # when the wildcard certificate is valid.  Retry without
                # SSL verification and flag the board so downstream
                # clients also disable verification.
                log.info("workday.ssl_retry", company=company)
                async with httpx.AsyncClient(
                    timeout=client.timeout,
                    follow_redirects=True,
                    verify=False,
                ) as insecure:
                    count = await _fetch_job_count(company, wd_instance, site, insecure)
                if count is not None:
                    result["jobs"] = count
                    result["ssl_verify"] = False
        return result

    if client is None:
        return None

    # Strategy 2: Scan page HTML for Workday markers
    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                # Found a Workday reference — try to extract full URL from the page
                full_match = _URL_RE.search(html)
                if full_match:
                    company = full_match.group(1)
                    wd_instance = f"wd{full_match.group(2)}"
                    site = full_match.group(3)
                    log.info(
                        "workday.detected_in_page",
                        url=url,
                        company=company,
                        site=site,
                    )
                    result = {"company": company, "wd_instance": wd_instance, "site": site}
                    count = await _fetch_job_count(company, wd_instance, site, client)
                    if count is not None:
                        result["jobs"] = count
                    elif "_" in company:
                        async with httpx.AsyncClient(
                            timeout=client.timeout,
                            follow_redirects=True,
                            verify=False,
                        ) as insecure:
                            count = await _fetch_job_count(company, wd_instance, site, insecure)
                        if count is not None:
                            result["jobs"] = count
                            result["ssl_verify"] = False
                    return result

    return None


register("workday", discover, cost=10, can_handle=can_handle, rich=False, stream=discover_stream)
