"""Eightfold PCSX API helpers.

Narrow HTTP layer on top of Eightfold's career-site API (``/api/pcsx/search``).
Used by the eightfold monitor in hybrid mode to fetch rich job data that
complements the sitemap-based URL discovery.

PCSX quirks we handle:

- **Per-tenant enablement**: some tenants return HTTP 403 with
  ``{"message": "PCSX is not enabled for this user."}``. Detected by
  :func:`probe` and surfaced as :class:`PcsxDisabled`.
- **Page-size hard cap**: the ``num`` query param is silently capped at 10
  items per page regardless of its value.
- **Rate limiting**: sustained pagination from a single IP can trigger
  HTTP 405 (Starbucks) — treat as a stable block, not a transient error.
- **Sort order**: PCSX returns jobs sorted ``postedTs DESC`` by default.
  This enables incremental polling via :func:`fetch_incremental`.
- **URL mismatch**: PCSX returns ``positionUrl: /careers/job/{id}`` while
  the sitemap returns ``/careers/job/{id}-{title-slug}?domain={tenant}``.
  The numeric id is the join key; see :func:`parse_job_id` and
  :func:`build_sitemap_id_map`.

Pagination loops delegate to ``_incremental.paginate_until_old`` and
``_incremental.paginate_all`` — only the PCSX-specific HTTP and field
mapping lives here.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob
from src.core.monitors._incremental import paginate_all, paginate_until_old

log = structlog.get_logger()

# ── Constants ──────────────────────────────────────────────────────────

#: PCSX silently caps ``num`` at 10 regardless of the param value.
PCSX_PAGE_SIZE = 10
#: Max retry attempts on transient failures (429, 5xx).
PCSX_DEFAULT_RETRY_MAX = 3
#: Per-request HTTP timeout (seconds).
PCSX_TIMEOUT_S = 15
#: Polite inter-page delay (seconds) to stay under rate limits.
PCSX_PAGE_SLEEP_S = 0.2
#: Hard page cap for full crawls (50,000 jobs max, generous safety margin).
PCSX_FULL_HARD_PAGE_CAP = 5000
#: Hard page cap for incremental crawls (rarely needs more than ~30 pages).
PCSX_INCREMENTAL_HARD_PAGE_CAP = 500
#: Numeric job id embedded in ``/careers/job/{id}`` paths.
PCSX_JOB_ID_RE = re.compile(r"/careers/job/(\d+)")


class PcsxDisabled(Exception):
    """Probe detected that PCSX is disabled for this tenant."""


class PcsxFetchError(Exception):
    """PCSX request failed after retries, or hit a stable block (405)."""


# ── HTTP plumbing ──────────────────────────────────────────────────────


def _api_url(host: str) -> str:
    return f"https://{host}/api/pcsx/search"


async def _fetch_page(
    host: str,
    domain: str,
    http: httpx.AsyncClient,
    *,
    offset: int,
    num: int = PCSX_PAGE_SIZE,
    query: str = "",
    location: str = "",
) -> list[dict]:
    """Fetch a single page of PCSX positions with retry.

    Returns the list of raw position dicts from ``data.positions``.
    Raises :class:`PcsxDisabled` on 403 with "PCSX is not enabled".
    Raises :class:`PcsxFetchError` on 405 (stable block) or after
    exhausted retries on 429/5xx.
    """
    params = {
        "domain": domain,
        "query": query,
        "location": location,
        "start": offset,
        "num": num,
    }
    last_status = None
    last_exc: Exception | None = None
    for attempt in range(PCSX_DEFAULT_RETRY_MAX):
        try:
            resp = await http.get(_api_url(host), params=params, timeout=PCSX_TIMEOUT_S)
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(1.0 * (attempt + 1))
            continue
        last_status = resp.status_code
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001 — log and retry
                last_exc = exc
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            positions = (data.get("data") or {}).get("positions") or []
            return positions
        if resp.status_code == 403:
            # Distinguish "PCSX not enabled" from other 403s.
            try:
                body = resp.json()
                msg = (body.get("message") or "").lower()
                if "pcsx" in msg and "not enabled" in msg:
                    raise PcsxDisabled(f"PCSX disabled on {host}: {body.get('message')}")
            except PcsxDisabled:
                raise
            except Exception:
                pass
            raise PcsxFetchError(f"403 from {host}")
        if resp.status_code == 405:
            # Stable block (Starbucks pattern). Don't retry — the server
            # is refusing the method entirely. Bubble up so the caller can
            # fall back to sitemap-only.
            raise PcsxFetchError(f"405 from {host} (rate-limited / blocked)")
        if resp.status_code in (429, 500, 502, 503, 504):
            # Transient — exponential backoff then retry.
            await asyncio.sleep(5.0 * (2**attempt))
            continue
        # Other unexpected status — fail fast.
        raise PcsxFetchError(f"HTTP {resp.status_code} from {host}")

    raise PcsxFetchError(
        f"PCSX fetch failed after {PCSX_DEFAULT_RETRY_MAX} attempts "
        f"(last_status={last_status}, last_exc={last_exc!r})"
    )


async def probe(
    host: str,
    domain: str,
    http: httpx.AsyncClient,
    *,
    strict: bool = True,
) -> bool:
    """Check whether the tenant has PCSX enabled.

    Two semantic modes, distinguished by the ``strict`` flag:

    - ``strict=True`` (default, used by the hybrid discover flow):
      returns True only if PCSX can actually be used for discovery.
      A 403 "PCSX is not enabled for this user." response returns
      False — we can't use PCSX, fall back to sitemap-only.

    - ``strict=False`` (used by ``can_handle`` for Eightfold detection):
      returns True if the response confirms this is an Eightfold tenant
      AT ALL, even when PCSX is disabled. A 403 "PCSX is not enabled"
      response still returns True because the message itself confirms
      Eightfold. Used to classify a URL during ``ws probe monitor``.
    """
    try:
        positions = await _fetch_page(host, domain, http, offset=0, num=1)
    except PcsxDisabled:
        return not strict  # lenient mode: 403 confirms Eightfold
    except PcsxFetchError as exc:
        log.warning("pcsx.probe_failed", host=host, error=str(exc))
        return False
    except Exception as exc:  # noqa: BLE001 — be defensive on probe
        log.warning("pcsx.probe_exception", host=host, error=str(exc))
        return False
    return positions is not None


async def get_count(
    host: str,
    domain: str,
    http: httpx.AsyncClient,
    *,
    query: str = "",
    location: str = "",
) -> int:
    """Fetch ``data.count`` via a single ``num=1`` request.

    Returns 0 if the response doesn't contain a count.
    """
    params = {
        "domain": domain,
        "query": query,
        "location": location,
        "start": 0,
        "num": 1,
    }
    resp = await http.get(_api_url(host), params=params, timeout=PCSX_TIMEOUT_S)
    if resp.status_code != 200:
        return 0
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return 0
    return int((data.get("data") or {}).get("count") or 0)


# ── Pagination wrappers ────────────────────────────────────────────────


async def fetch_all(
    host: str,
    domain: str,
    http: httpx.AsyncClient,
    *,
    max_jobs: int | None = None,
) -> list[dict]:
    """Full linear pagination (for first runs and weekly re-syncs).

    Returns raw PCSX position dicts. Sleeps ``PCSX_PAGE_SLEEP_S`` between
    pages to stay polite on rate-limited tenants.
    """

    async def _page(offset: int) -> list[dict]:
        items = await _fetch_page(host, domain, http, offset=offset)
        if items:
            await asyncio.sleep(PCSX_PAGE_SLEEP_S)
        return items

    return await paginate_all(
        _page,
        page_size=PCSX_PAGE_SIZE,
        max_items=max_jobs,
        hard_page_cap=PCSX_FULL_HARD_PAGE_CAP,
    )


async def fetch_incremental(
    host: str,
    domain: str,
    http: httpx.AsyncClient,
    *,
    max_posted_ts: int,
    safety_pages: int = 3,
) -> list[dict]:
    """Incremental pagination — stop after hitting the watermark.

    Uses ``_incremental.paginate_until_old`` with a ``get_timestamp``
    extractor that reads ``postedTs`` from each raw position. Missing or
    zero timestamps are treated as "newer than the watermark" and never
    trigger early termination (defensive against upstream schema changes).
    """

    async def _page(offset: int) -> list[dict]:
        items = await _fetch_page(host, domain, http, offset=offset)
        if items:
            await asyncio.sleep(PCSX_PAGE_SLEEP_S)
        return items

    def _get_ts(item: dict) -> int | None:
        ts = item.get("postedTs")
        if ts is None:
            return None
        try:
            ts_int = int(ts)
        except (ValueError, TypeError):
            return None
        return ts_int if ts_int > 0 else None

    return await paginate_until_old(
        _page,
        _get_ts,
        max_watermark=max_posted_ts,
        page_size=PCSX_PAGE_SIZE,
        safety_pages=safety_pages,
        hard_page_cap=PCSX_INCREMENTAL_HARD_PAGE_CAP,
    )


# ── URL correlation helpers ────────────────────────────────────────────


def parse_job_id(position_url: str | None) -> str | None:
    """Extract the numeric job id from a ``positionUrl`` or full sitemap URL.

    Accepts:
    - ``/careers/job/563705876642261`` (PCSX ``positionUrl`` form)
    - ``https://careers.kering.com/careers/job/563705876642261-slug-title?domain=kering``
      (sitemap URL form)

    Returns the id as a string (not int), since id lookups are string-keyed.
    Returns ``None`` on unrecognised inputs.
    """
    if not position_url:
        return None
    match = PCSX_JOB_ID_RE.search(position_url)
    if match is None:
        return None
    return match.group(1)


def build_sitemap_id_map(urls: Iterable[str]) -> dict[str, str]:
    """Build a map from numeric job id → canonical sitemap URL.

    The canonical URL is what gets stored in ``job_posting.source_url``
    (matching the current sitemap-based eightfold behaviour so existing
    rows don't orphan on refactor).

    On duplicate ids, the first URL seen wins and a warning is logged.
    """
    out: dict[str, str] = {}
    for url in urls:
        job_id = parse_job_id(url)
        if job_id is None:
            continue
        if job_id in out:
            log.warning(
                "pcsx.duplicate_sitemap_id",
                job_id=job_id,
                existing=out[job_id],
                duplicate=url,
            )
            continue
        out[job_id] = url
    return out


def extract_host_and_domain(sitemap_urls: Iterable[str]) -> tuple[str, str] | None:
    """Derive ``(host, tenant_domain)`` from a sample sitemap URL.

    Eightfold sitemap URLs look like::

        https://careers.kering.com/careers/job/123-foo?domain=kering

    where ``kering`` is the ``domain`` query param PCSX expects.
    Returns ``None`` if no sitemap URL carries a ``domain=`` query param
    — callers should fall back to using the host itself.

    The query-parameter key match is **case-insensitive** (``?domain=``,
    ``?Domain=``, ``?DOMAIN=`` all work) because we've seen malformed
    sitemaps in the wild that use non-canonical capitalisation.
    """
    for url in sitemap_urls:
        parsed = urlparse(url)
        if not parsed.hostname:
            continue
        qs = parse_qs(parsed.query)
        # Case-insensitive lookup: walk keys and compare lowered form.
        for key, values in qs.items():
            if key.lower() == "domain" and values:
                return parsed.hostname, values[0]
    return None


# ── Field mapping ──────────────────────────────────────────────────────


def _ts_to_iso_date(ts: object) -> str | None:
    """Convert a unix timestamp to an ISO-8601 date string (no time)."""
    if ts is None:
        return None
    try:
        ts_int = int(ts)
    except (ValueError, TypeError):
        return None
    if ts_int <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts_int, tz=UTC).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def pcsx_to_discovered(raw: dict, sitemap_url: str) -> DiscoveredJob:
    """Map a raw PCSX position dict to a :class:`DiscoveredJob`.

    Uses ``sitemap_url`` as the canonical URL (matches what the existing
    sitemap-based path writes to ``job_posting.source_url``) so that
    switching to the hybrid flow doesn't cause URL-mismatch re-indexing.

    Field mapping:

    - ``name`` → ``title``
    - ``standardizedLocations`` (list) → ``locations``
    - ``workLocationOption`` → ``job_location_type`` (already in the
      canonical ``onsite``/``hybrid``/``remote`` format used elsewhere)
    - ``postedTs`` → ``date_posted`` (ISO date, or None if missing/zero)
    - ``department`` → ``metadata["department"]``
    - ``atsJobId`` → ``metadata["ats_job_id"]``
    - ``description`` is left ``None`` — filled by the json-ld enrich scrape.
    """
    locations = raw.get("standardizedLocations")
    if isinstance(locations, str):
        # Some tenants return a JSON-encoded string; parse defensively.
        try:
            import json as _json

            parsed = _json.loads(locations)
            locations = parsed if isinstance(parsed, list) else [locations]
        except (ValueError, TypeError):
            locations = [locations]
    if not isinstance(locations, list) or not locations:
        locations = None

    metadata: dict = {}
    if raw.get("department"):
        metadata["department"] = raw["department"]
    if raw.get("atsJobId"):
        metadata["ats_job_id"] = raw["atsJobId"]

    work_loc_raw = raw.get("workLocationOption")
    work_loc = work_loc_raw.lower() or None if isinstance(work_loc_raw, str) else None

    return DiscoveredJob(
        url=sitemap_url,
        title=raw.get("name") or None,
        description=None,  # json-ld scraper fills this later via enrich
        locations=locations,
        employment_type=None,  # not provided by PCSX listing
        job_location_type=work_loc,
        date_posted=_ts_to_iso_date(raw.get("postedTs")),
        metadata=metadata or None,
    )
