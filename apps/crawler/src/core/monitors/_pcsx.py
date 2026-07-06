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
import random
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob
from src.core.monitors._incremental import paginate_all, paginate_until_old
from src.shared.http_retry import PaginationFetchError, is_retryable_status

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
#: Safety cap on response body size. Anything larger signals a malicious
#: or misconfigured upstream — fail fast instead of buffering GB-scale
#: responses in memory. 10 MB is ~50x bigger than the largest legitimate
#: PCSX page (a full 10-item response is typically <200 KB).
PCSX_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
#: Log progress every N pages during a full crawl so operators can see
#: forward motion on long-running manual backfills (e.g. Starbucks ~2000
#: pages). At one log event per 50 pages, a Starbucks backfill emits ~40
#: progress events.
PCSX_FULL_CRAWL_PROGRESS_EVERY = 50
#: Numeric job id embedded in ``/careers/job/{id}`` paths.
PCSX_JOB_ID_RE = re.compile(r"/careers/job/(\d+)")


class PcsxDisabled(Exception):
    """Probe detected that PCSX is disabled for this tenant."""


class PcsxFetchError(PaginationFetchError):
    """PCSX request failed after retries on 429/5xx (transient).

    Subclasses :class:`PaginationFetchError` (#2734) so PCSX failures are
    interchangeable with the static dom/sitemap pagination failure shape:
    callers that ``except PaginationFetchError`` will catch this too,
    keeping operator-facing semantics symmetric across all paginating
    monitors. Constructed with a free-form message (legacy contract);
    optional ``url`` / ``last_status`` kwargs carry context for the
    base-class fields when callers have it.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str = "<pcsx>",
        last_status: int | None = None,
    ) -> None:
        # Skip ``PaginationFetchError.__init__`` — its formatted message
        # ("pagination fetch failed for {url} after {N} attempts ({detail})")
        # would shadow PCSX's caller-supplied diagnostic. Set the
        # base-class attributes directly so ``isinstance`` checks and
        # ``exc.last_status`` access still work.
        Exception.__init__(self, message)
        self.url = url
        self.attempts = 0
        self.last_status = last_status
        self.last_error = None


class PcsxStableBlock(PcsxFetchError):
    """PCSX endpoint is stably refusing us (HTTP 405).

    The tenant's WAF is blocking the PCSX method outright — retrying on the
    next cycle just produces the same 405. The caller should treat this like
    :class:`PcsxDisabled` and flip the watermark's ``enabled`` flag off so
    the board falls back to the sitemap-only path permanently (until the
    block is reviewed and reset).

    Subclasses ``PcsxFetchError`` so existing ``except PcsxFetchError``
    callers still catch it — newer callers that want the distinction can
    pattern-match on the subclass before the base class.
    """


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
    # Retry observability (#3210). Same counter as ``http_retry.py`` so
    # cross-monitor "retry storm" queries aggregate PCSX in. The PCSX
    # ``host`` arg is already the tenant hostname (e.g. ``careers.kering.com``)
    # — pass it through ``http_retry_host`` to normalize (lowercase, no port).
    from src.metrics import http_retry_attempts_total, http_retry_host
    from src.shared.tdm import check_response as _tdm_check

    metric_host = http_retry_host(_api_url(host))
    retried = False

    last_status = None
    last_exc: Exception | None = None
    for attempt in range(PCSX_DEFAULT_RETRY_MAX):
        try:
            resp = await http.get(_api_url(host), params=params, timeout=PCSX_TIMEOUT_S)
        except Exception as exc:
            last_exc = exc
            http_retry_attempts_total.labels(host=metric_host, outcome="retry").inc()
            retried = True
            await asyncio.sleep(1.0 * (attempt + 1))
            continue
        last_status = resp.status_code
        if resp.status_code == 200:
            # TDM-Reservation respect (#2842). Header-only check; PCSX
            # is a JSON API. ``TDMReservedError`` propagates out of the
            # retry loop — publisher policy is not a transient error.
            _tdm_check(resp)
            # Safety valve against a malicious / broken tenant returning
            # an enormous response body. ``resp.content`` is the already-
            # buffered body; httpx streams it up to this point regardless.
            # We rely on the default body size being bounded at httpx level
            # via response limits, but add an explicit cap here so a
            # misconfigured tenant can't OOM the worker by returning an
            # uncommonly large payload (expected size is <200 KB).
            if len(resp.content) > PCSX_MAX_RESPONSE_BYTES:
                raise PcsxFetchError(
                    f"PCSX response from {host} exceeds "
                    f"{PCSX_MAX_RESPONSE_BYTES} bytes "
                    f"(got {len(resp.content)})",
                    url=_api_url(host),
                    last_status=200,
                )
            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001 — log and retry
                last_exc = exc
                http_retry_attempts_total.labels(host=metric_host, outcome="retry").inc()
                retried = True
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            # Defensive: PCSX could respond with an unexpected shape.
            if not isinstance(data, dict):
                raise PcsxFetchError(
                    f"PCSX response from {host} is not a JSON object (got {type(data).__name__})",
                    url=_api_url(host),
                    last_status=200,
                )
            data_inner = data.get("data")
            if data_inner is not None and not isinstance(data_inner, dict):
                raise PcsxFetchError(
                    f"PCSX response.data from {host} is not a JSON object "
                    f"(got {type(data_inner).__name__})",
                    url=_api_url(host),
                    last_status=200,
                )
            positions = (data_inner or {}).get("positions") or []
            if retried:
                http_retry_attempts_total.labels(host=metric_host, outcome="recovered").inc()
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
                # Non-JSON 403 bodies still fall through to the generic 403 error.
                pass
            raise PcsxFetchError(
                f"403 from {host}",
                url=_api_url(host),
                last_status=403,
            )
        if resp.status_code == 405:
            # Stable block (Starbucks pattern). Don't retry — the server
            # is refusing the method entirely. Bubble up as PcsxStableBlock
            # so the caller can flip the watermark to ``enabled=False`` and
            # stop re-trying every cycle.
            raise PcsxStableBlock(
                f"405 from {host} (rate-limited / blocked)",
                url=_api_url(host),
                last_status=405,
            )
        if is_retryable_status(resp.status_code):
            # Transient — exponential backoff + jitter then retry.
            # ``is_retryable_status`` covers 408/425/429 plus any 5xx in
            # range — Cloudflare 520-526/530 origin errors included.
            #
            # Backoff cadence (5/10/20s × narrow jitter) is intentionally
            # heavier than the dom/sitemap path's (~0.5/1/2s × full
            # jitter): PCSX is a paginated, expensive endpoint and the
            # large tenants (Starbucks, Citi) have rate-limit budgets
            # that don't tolerate a thundering-herd of fast retries from
            # 3 workers. ``random.uniform(0.8, 1.2)`` gives ±20% spread
            # — wide enough to decorrelate workers, narrow enough that
            # the polite-cadence intent is preserved.
            base_delay = 5.0 * (2**attempt)
            jittered = base_delay * random.uniform(0.8, 1.2)
            http_retry_attempts_total.labels(host=metric_host, outcome="retry").inc()
            retried = True
            await asyncio.sleep(jittered)
            continue
        # Other unexpected status — fail fast.
        raise PcsxFetchError(
            f"HTTP {resp.status_code} from {host}",
            url=_api_url(host),
            last_status=resp.status_code,
        )

    http_retry_attempts_total.labels(host=metric_host, outcome="exhausted").inc()
    raise PcsxFetchError(
        f"PCSX fetch failed after {PCSX_DEFAULT_RETRY_MAX} attempts "
        f"(last_status={last_status}, last_exc={last_exc!r})",
        url=_api_url(host),
        last_status=last_status,
    )


class ProbeResult:
    """Outcome of a PCSX probe.

    Distinguishes between:

    - ``ENABLED``   — PCSX answered with a valid response; tenant is usable.
    - ``DISABLED``  — tenant returned 403 "PCSX is not enabled for this user."
                      or the confirmed strict==False Eightfold-detected case.
                      This is a STABLE signal that PCSX can't be used for
                      discovery — safe to cache as ``enabled=False``.
    - ``TRANSIENT`` — probe failed for a non-stable reason (5xx, timeout,
                      JSON parse error, rate-limit block). The caller
                      should NOT cache this as a permanent disable state —
                      retry on the next cycle.
    """

    ENABLED = "enabled"
    DISABLED = "disabled"
    TRANSIENT = "transient"


async def probe(
    host: str,
    domain: str,
    http: httpx.AsyncClient,
    *,
    strict: bool = True,
) -> bool:
    """Back-compat shim over :func:`probe_detail`.

    Returns a bool matching the previous contract:
    - ``strict=True``: True iff ENABLED.
    - ``strict=False``: True if ENABLED or DISABLED (both confirm Eightfold).

    Callers that need to distinguish TRANSIENT from DISABLED (e.g. so
    they don't cache a transient failure as ``enabled=False``) should use
    :func:`probe_detail` directly.
    """
    result = await probe_detail(host, domain, http)
    if result == ProbeResult.ENABLED:
        return True
    if result == ProbeResult.DISABLED:
        return not strict
    return False  # TRANSIENT → False under both modes


async def probe_detail(
    host: str,
    domain: str,
    http: httpx.AsyncClient,
) -> str:
    """Return a tri-state :class:`ProbeResult` for a PCSX probe.

    Used by the eightfold hybrid discover flow to decide whether to cache
    the ``enabled`` flag on the board watermark. Only ``DISABLED`` should
    be cached as a permanent ``enabled=False``; ``TRANSIENT`` should be
    left as ``None`` so the next run re-probes.
    """
    try:
        positions = await _fetch_page(host, domain, http, offset=0, num=1)
    except PcsxDisabled:
        return ProbeResult.DISABLED
    except PcsxFetchError as exc:
        log.warning("pcsx.probe_failed", host=host, error=str(exc))
        return ProbeResult.TRANSIENT
    except Exception as exc:  # noqa: BLE001 — be defensive on probe
        log.warning("pcsx.probe_exception", host=host, error=str(exc))
        return ProbeResult.TRANSIENT
    return ProbeResult.ENABLED if positions is not None else ProbeResult.TRANSIENT


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
    pages to stay polite on rate-limited tenants. Logs a progress event
    every ``PCSX_FULL_CRAWL_PROGRESS_EVERY`` pages so operators can tell
    if a long-running manual backfill is still making forward progress
    or has gotten stuck.
    """
    pages_fetched = 0
    items_fetched = 0

    async def _page(offset: int) -> list[dict]:
        nonlocal pages_fetched, items_fetched
        items = await _fetch_page(host, domain, http, offset=offset)
        pages_fetched += 1
        items_fetched += len(items)
        if pages_fetched % PCSX_FULL_CRAWL_PROGRESS_EVERY == 0:
            log.info(
                "pcsx.full_crawl_progress",
                host=host,
                domain=domain,
                pages=pages_fetched,
                items=items_fetched,
                offset=offset,
            )
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
