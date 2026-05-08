"""Hireology Careers API monitor.

Public API: GET https://api.hireology.com/v2/public/careers/{slug}?page_size=500
Returns full job data in a single request — no detail calls needed.
No authentication required.

Career page domains:
  - careers.hireology.com/{slug}
  - {slug}.hireology.careers
"""

from __future__ import annotations

import asyncio
import random
import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url
from src.shared.http_retry import PaginationFetchError, is_retryable_status

log = structlog.get_logger()

MAX_JOBS = 50_000
PAGE_SIZE = 500

# Pagination retry budget. Symmetric with workday (#2748), lever (#2749),
# smartrecruiters (#2749), api_sniffer (#2733), accenture (#2735) and
# PCSX (#2734): 3 total attempts, exponential backoff with full jitter
# starting at 0.5s.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5

_CAREERS_DOMAIN_RE = re.compile(r"^careers\.hireology\.com$")
_NEW_DOMAIN_RE = re.compile(r"^([\w-]+)\.hireology\.careers$")

_PAGE_PATTERNS = [
    re.compile(r"careers\.hireology\.com/([\w-]+)"),
    re.compile(r"([\w-]+)\.hireology\.careers"),
    re.compile(r"api\.hireology\.com/v[12]/(?:public/)?careers/([\w-]+)"),
]

_IGNORE_SLUGS = frozenset({"api", "www", "app", "static", "assets", "js", "css"})


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Hireology careers slug from a known URL pattern."""
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()

    # {slug}.hireology.careers
    match = _NEW_DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug

    # careers.hireology.com/{slug}
    if _CAREERS_DOMAIN_RE.match(host):
        path = parsed.path.strip("/")
        # Extract first path segment (ignore /123/description etc.)
        parts = path.split("/")
        if parts and parts[0] and parts[0] not in _IGNORE_SLUGS:
            return parts[0]

    return None


def _api_url(slug: str) -> str:
    return f"https://api.hireology.com/v2/public/careers/{slug}"


def _parse_locations(job: dict) -> list[str] | None:
    """Extract locations from a Hireology job."""
    raw = job.get("locations")
    if not raw:
        return None

    locations: list[str] = []
    for loc in raw:
        if isinstance(loc, dict):
            city = loc.get("city", "")
            state = loc.get("state", "")
            parts = [p for p in (city, state) if p]
            name = ", ".join(parts)
            if name:
                locations.append(name)
        elif isinstance(loc, str) and loc:
            locations.append(loc)

    return locations or None


def _parse_job(job: dict) -> DiscoveredJob | None:
    """Map a Hireology job to a DiscoveredJob."""
    url = job.get("career_site_url")
    if not url:
        return None

    # Metadata
    metadata: dict = {}
    org = job.get("organization")
    if isinstance(org, dict) and org.get("name"):
        metadata["organization"] = org["name"]
    job_family = job.get("job_family")
    if isinstance(job_family, dict) and job_family.get("name"):
        metadata["job_family"] = job_family["name"]
    job_id = job.get("id")
    if job_id:
        metadata["id"] = job_id

    # job_location_type
    job_location_type = "remote" if job.get("remote") else None

    return DiscoveredJob(
        url=url,
        title=job.get("name"),
        description=job.get("job_description"),
        locations=_parse_locations(job),
        employment_type=job.get("employment_status"),
        job_location_type=job_location_type,
        date_posted=job.get("created_at"),
        metadata=metadata or None,
    )


async def _probe_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Hireology API for a slug. Returns (found, job_count)."""
    try:
        resp = await client.get(_api_url(slug), params={"page_size": 1})
        if resp.status_code != 200:
            return False, None
        data = resp.json()
        count = data.get("count")
        if isinstance(count, int):
            return True, count
        # Check if data list exists
        items = data.get("data")
        if isinstance(items, list):
            return True, len(items)
        return False, None
    except Exception:
        return False, None


async def _get_page_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    *,
    retries: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
) -> dict:
    """GET a Hireology list-API page with bounded retries (#2749).

    Mirrors the contract used by ``fetch_with_retry`` (#2722) and the
    sibling monitor helpers (workday #2748, lever #2749, smartrecruiters
    #2749, accenture #2735, PCSX #2734, api_sniffer #2733): retryable
    failures back off exponentially and, on budget exhaustion, raise
    :class:`PaginationFetchError` so the run is recorded as a failure
    rather than silently truncating to whatever pages happened to
    succeed.

    Hireology's end-of-pagination signal is
    ``page * PAGE_SIZE >= count or len(data) < PAGE_SIZE``. Pre-fix, a
    CDN/anti-bot incident dropping the body and returning ``200 {}``
    decoded to an empty dict whose ``count`` defaulted to 0 and whose
    ``data`` defaulted to ``[]`` — the loop broke after the first page
    and the caller treated the partial discovery as success, which fed
    ``_MARK_GONE_BY_TIMESTAMP`` for the missing URLs (same shape as
    #2722 / #2737 / #2748 / smartrecruiters in this PR).

    Retried:
      - HTTP 5xx (Cloudflare 520-526/530 included), 408, 425, 429.
      - Arbitrary network exceptions (timeout, connection reset, JSON
        parse error on a captcha/HTML body served as 200).
      - HTTP 200 with a body that decodes to a non-dict shape — e.g.,
        a CDN error envelope or ``null`` served as 200 (same shape as
        the workday ``null``-body guard).

    Fail-fast (non-retryable 4xx — auth-expired 401, misconfigured 400,
    forbidden 403, board-removed 404): raises
    :class:`PaginationFetchError` on the first attempt.

    Backoff: ``base_delay × 2^attempt × (0.5 + random())`` — exponential
    with full jitter, identical cadence to workday (#2748).
    """
    last_exc: BaseException | None = None
    last_status: int | None = None

    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params)
            last_status = resp.status_code
            if resp.status_code == 200:
                # ``resp.json()`` may raise ``json.JSONDecodeError`` on a
                # captcha/HTML body served as 200 — falls into the
                # ``except Exception`` branch below, retried, then
                # surfaced as ``PaginationFetchError``. No silent break.
                data = resp.json()
                # Hireology's list endpoint returns a dict with ``data``
                # / ``count``. A body that decodes to ``null`` or to a
                # non-dict shape (e.g., an unexpected error envelope) is
                # treated as a transient failure: retry, then raise.
                # Without this guard, ``data.get(...)`` on a list/None
                # silently falls back to defaults which fake a legitimate
                # end-of-pagination — same shape of silent-break bug the
                # issue (#2749) is fixing.
                if not isinstance(data, dict):
                    raise ValueError(
                        f"hireology list endpoint returned non-dict body: {type(data).__name__}"
                    )
                return data
            if is_retryable_status(resp.status_code):
                last_exc = None  # status-only, no exception
            else:
                # Non-retryable 4xx — fail fast. ``resp.raise_for_status``
                # would raise ``HTTPStatusError`` which the caller doesn't
                # uniformly handle; raise ``PaginationFetchError`` directly
                # for cross-monitor symmetry.
                raise PaginationFetchError(
                    url,
                    attempts=attempt + 1,
                    last_status=resp.status_code,
                )
        except PaginationFetchError:
            raise
        except Exception as exc:  # noqa: BLE001 — timeout, network, JSON parse
            last_exc = exc
            last_status = None

        if attempt < retries - 1:
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "hireology.list_backoff",
                url=url,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                last_status=last_status,
                last_error=type(last_exc).__name__ if last_exc else None,
            )
            await asyncio.sleep(delay)

    raise PaginationFetchError(
        url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_exc).__name__ if last_exc else None,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from the Hireology public API.

    Failure semantics (#2749). Each page GET is wrapped by
    :func:`_get_page_with_retry`, which raises
    :class:`PaginationFetchError` on persistent transient failures or
    non-retryable 4xx. The exception propagates out of this function
    (no intervening try/except) and lands in
    ``_process_one_board_streaming``'s generic ``except Exception``,
    which records the run as a failure rather than a partial success
    — preventing ``_MARK_GONE_BY_TIMESTAMP`` from tombstoning the URLs
    that live on the unfetched pages (same shape of bug as #2722,
    #2737, #2748).
    """
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Hireology slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    # Paginate through all jobs
    jobs: list[DiscoveredJob] = []
    page = 1
    list_url = _api_url(slug)

    while True:
        data = await _get_page_with_retry(
            client,
            list_url,
            {"page_size": PAGE_SIZE, "page": page},
        )

        raw_jobs = data.get("data", [])

        for raw in raw_jobs:
            if raw.get("status") != "Open":
                continue
            parsed = _parse_job(raw)
            if parsed:
                jobs.append(parsed)

        total = data.get("count", 0)
        if page * PAGE_SIZE >= total or len(raw_jobs) < PAGE_SIZE:
            break

        page += 1

        if len(jobs) >= MAX_JOBS:
            log.warning("hireology.truncated", slug=slug, total=len(jobs), cap=MAX_JOBS)
            jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]
            break

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Hireology: URL pattern -> page HTML scan -> slug-based API probe."""
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count = await _probe_slug(slug, client)
            if found:
                result: dict = {"slug": slug}
                if count is not None:
                    result["jobs"] = count
                return result
        return {"slug": slug}

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in _PAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                found_slug = match.group(1)
                if found_slug not in _IGNORE_SLUGS:
                    log.info("hireology.detected_in_page", url=url, slug=found_slug)
                    found, count = await _probe_slug(found_slug, client)
                    if found:
                        result = {"slug": found_slug}
                        if count is not None:
                            result["jobs"] = count
                        return result

    for slug_candidate in slugs_from_url(url):
        found, count = await _probe_slug(slug_candidate, client)
        if found:
            log.info("hireology.detected_by_probe", url=url, slug=slug_candidate)
            result = {"slug": slug_candidate}
            if count is not None:
                result["jobs"] = count
            return result

    return None


register("hireology", discover, cost=10, can_handle=can_handle, rich=True)
