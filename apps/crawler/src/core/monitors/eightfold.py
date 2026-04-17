"""Eightfold AI careers portal monitor (hybrid sitemap + PCSX).

Every Eightfold portal exposes a sitemap at ``/careers/sitemap.xml`` with
canonical job URLs of the form
``https://{host}/careers/job/{id}-{title-slug}?domain={tenant}``. Eightfold
also exposes a private search API at ``/api/pcsx/search`` that returns rich
job metadata (title, clean locations, ``workLocationOption``, ``postedTs``,
``department``, ``atsJobId``) sorted by ``postedTs DESC``.

This monitor runs both sources per cycle and correlates them:

- **Sitemap** → full URL set. Drives gone detection via the pipeline's
  ``_DIFF_BATCH`` + ``_MARK_GONE_BY_TIMESTAMP`` path. Unchanged from the
  pre-refactor behaviour.
- **PCSX** → rich data for new and updated jobs only. On first run (or
  weekly re-sync cadence) we do a full paginated crawl. Subsequent runs
  use :func:`_pcsx.fetch_incremental` with a high-water mark on
  ``postedTs`` so only the first few pages are fetched.

The two sources are joined by the numeric job id embedded in both URL
formats (see :func:`_pcsx.parse_job_id`). The sitemap URL stays canonical
— PCSX data is attached to the existing sitemap URL so that refactoring
the monitor does not cause re-indexing of existing rows.

Tenants that return ``"PCSX is not enabled for this user."`` degrade to
sitemap-only mode and the json-ld scraper continues to fill job content,
unchanged from the pre-refactor behaviour.

The board's CSV config must include ``scraper_config: {"enrich":
["description"]}`` to trigger the pipeline's enrichment path — PCSX does
not return descriptions, so a one-shot json-ld scrape fills them at insert
time (reusing the same pattern as ``allps`` and ``cerrion``).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, _pcsx, _watermark, fetch_page_text, register
from src.core.monitors.sitemap import discover as sitemap_discover

log = structlog.get_logger()

_EIGHTFOLD_SUBDOMAIN_RE = re.compile(r"^(?:[\w-]+)\.eightfold\.ai$", re.IGNORECASE)
_WATERMARK_KEY = "pcsx_watermark"


def _is_eightfold_domain(url: str) -> bool:
    """Return True when the URL is on an ``*.eightfold.ai`` subdomain."""
    host = (urlparse(url).hostname or "").lower()
    return bool(_EIGHTFOLD_SUBDOMAIN_RE.match(host))


def _sitemap_url(board_url: str) -> str:
    """Derive the sitemap URL from a board URL."""
    parsed = urlparse(board_url)
    return f"{parsed.scheme}://{parsed.netloc}/careers/sitemap.xml"


# ── discover / discover_stream ─────────────────────────────────────────


async def _sitemap_urls(
    board: dict, client: httpx.AsyncClient, pw=None
) -> tuple[set[str], str | None]:
    """Fetch the sitemap URL set using the existing sitemap monitor.

    Delegated to ``sitemap.discover`` so we don't duplicate its XML
    parsing or URL extraction logic.
    """
    metadata = board.get("metadata") or {}
    if not metadata.get("sitemap_url"):
        metadata = {**metadata, "sitemap_url": _sitemap_url(board["board_url"])}
    sitemap_board = {**board, "metadata": metadata}
    result = await sitemap_discover(sitemap_board, client, pw=pw)
    if isinstance(result, tuple):
        urls, new_sitemap_url = result
    else:
        urls, new_sitemap_url = result, None
    # Filter to job URLs only — sitemaps often list non-job pages as well.
    job_urls = {u for u in urls if "/careers/job/" in u}
    return job_urls, new_sitemap_url


def _map_pcsx_to_discovered(
    raw_positions: list[dict],
    id_to_url: dict[str, str],
    *,
    board_host: str,
) -> tuple[dict[str, DiscoveredJob], int, int]:
    """Correlate PCSX positions to sitemap URLs by numeric id.

    Returns ``(jobs_by_url, unmatched, new_max_ts)``. Unmatched positions
    (PCSX returned an id the sitemap hasn't caught up to yet) are logged
    and skipped — they'll be picked up next cycle when the sitemap
    regenerates.
    """
    jobs_by_url: dict[str, DiscoveredJob] = {}
    unmatched = 0
    new_max_ts = 0
    for raw in raw_positions:
        pid = _pcsx.parse_job_id(raw.get("positionUrl"))
        if pid is None:
            unmatched += 1
            continue
        sitemap_url = id_to_url.get(pid)
        if sitemap_url is None:
            unmatched += 1
            continue
        job = _pcsx.pcsx_to_discovered(raw, sitemap_url)
        jobs_by_url[sitemap_url] = job
        ts = raw.get("postedTs")
        try:
            ts_int = int(ts) if ts is not None else 0
        except (ValueError, TypeError):
            ts_int = 0
        if ts_int > new_max_ts:
            new_max_ts = ts_int
    if unmatched:
        # Usually benign — sitemap lags behind PCSX by a few hours when
        # new jobs are posted. If this number is a large fraction of the
        # PCSX total, it signals a sitemap fetch problem or a format
        # drift (log at warning level so operators see it in alerts).
        total = len(jobs_by_url) + unmatched
        unmatched_ratio = unmatched / total if total else 0.0
        if unmatched_ratio > 0.3:
            log.warning(
                "eightfold.pcsx_unmatched_high",
                host=board_host,
                count=unmatched,
                matched=len(jobs_by_url),
                ratio=round(unmatched_ratio, 3),
            )
        else:
            log.info(
                "eightfold.pcsx_unmatched",
                host=board_host,
                count=unmatched,
                matched=len(jobs_by_url),
                ratio=round(unmatched_ratio, 3),
            )
    return jobs_by_url, unmatched, new_max_ts


async def discover_stream(board: dict, client: httpx.AsyncClient, pw=None):
    """Hybrid sitemap + PCSX streamer. Yields a single MonitorResult per run.

    See the module docstring for the high-level flow. This function is
    deliberately single-batch: the sitemap URL set is the join key, so
    rich data cannot be emitted before the full sitemap is loaded, and
    splitting across batches would break ``_DIFF_BATCH`` URL classification
    (jobs would flip between touched/new across batches within the same
    board cycle).
    """
    # Local import to break the circular dependency: src.core.monitor
    # imports from src.core.monitors (this package), and this module is
    # in src.core.monitors. Importing MonitorResult at module level would
    # create a circular import chain during package initialization.
    from src.core.monitor import MonitorResult

    metadata = board.get("metadata") or {}

    # --- Step 1: sitemap (authoritative URL set) ---
    sitemap_urls, new_sitemap_url = await _sitemap_urls(board, client, pw=pw)

    if not sitemap_urls:
        # Nothing to correlate — yield an empty result and let the
        # pipeline's "empty check" branch handle the signal.
        yield MonitorResult(urls=sitemap_urls, new_sitemap_url=new_sitemap_url)
        return

    # --- Step 2: derive PCSX host + tenant domain ---
    host_domain = _pcsx.extract_host_and_domain(sitemap_urls)
    if host_domain is None:
        # No ``?domain=X`` in sitemap URLs — fall back to host-only.
        parsed = urlparse(next(iter(sitemap_urls)))
        host_domain = ((parsed.hostname or ""), (parsed.hostname or ""))
    host, domain = host_domain

    # --- Step 3: load watermark state ---
    wm = _watermark.read(metadata, _WATERMARK_KEY)
    now = datetime.now(UTC)
    force_full = bool(metadata.get("pcsx_force_full_crawl"))

    # --- Step 4: probe PCSX when enabled-state is unknown or on full-crawl cycle ---
    needs_probe = wm.enabled is None or wm.needs_full_crawl(now=now) or force_full
    probe_result: str | None = None
    if needs_probe:
        try:
            probe_result = await _pcsx.probe_detail(host, domain, client)
        except Exception as exc:  # noqa: BLE001 — probe must fail closed
            log.warning("eightfold.probe_exception", host=host, domain=domain, error=str(exc))
            probe_result = _pcsx.ProbeResult.TRANSIENT
        # Distinguish confirmed disabled from transient failures:
        # - DISABLED: cache ``enabled=False`` permanently (next run stays
        #   on sitemap, skipping the probe entirely until the weekly
        #   full-crawl cycle re-probes).
        # - TRANSIENT: do NOT cache as disabled — leave ``enabled`` at its
        #   previous value (possibly ``None`` or ``True``) so the next
        #   run retries the probe. The current run falls back to
        #   sitemap-only but doesn't poison future runs.
        # - ENABLED: clear the flag, proceed with rich discovery.
        if probe_result == _pcsx.ProbeResult.DISABLED:
            wm.enabled = False
        elif probe_result == _pcsx.ProbeResult.ENABLED:
            wm.enabled = True
        # else TRANSIENT: leave wm.enabled as-is

    # --- Step 5: PCSX-disabled tenant → sitemap-only, cache the probe result ---
    # A transient probe failure (wm.enabled still None or still True but
    # probe_result was TRANSIENT) also falls through here for this run,
    # but we do NOT write metadata_updates in that case so the watermark
    # is preserved.
    if probe_result == _pcsx.ProbeResult.DISABLED or wm.enabled is False:
        wm.extra = {**wm.extra, "host": host, "domain": domain}
        log.info("eightfold.pcsx_disabled", host=host, domain=domain)
        yield MonitorResult(
            urls=sitemap_urls,
            new_sitemap_url=new_sitemap_url,
            metadata_updates=_watermark.to_metadata_patch(wm),
        )
        return

    if probe_result == _pcsx.ProbeResult.TRANSIENT:
        # Transient probe failure — fall back to sitemap-only for this
        # run but DO NOT cache enabled=False. Next run re-probes.
        log.warning(
            "eightfold.probe_transient",
            host=host,
            domain=domain,
            note="sitemap-only for this run; next run will re-probe",
        )
        yield MonitorResult(
            urls=sitemap_urls,
            new_sitemap_url=new_sitemap_url,
            hybrid=True,  # same reason as PcsxFetchError path
        )
        return

    # --- Step 6: decide mode (full vs incremental) ---
    # A board can pre-configure ``auto_full_crawl: false`` in its watermark
    # to prevent scheduled runs from starting a long-running full crawl.
    # In that state, sitemap-only runs continue until an operator triggers
    # a manual backfill via ``--pcsx-full-crawl``.
    needs_full = force_full or wm.needs_full_crawl(now=now)
    if needs_full and wm.max_ts == 0 and not wm.auto_full_crawl and not force_full:
        log.warning(
            "eightfold.awaiting_manual_backfill",
            host=host,
            domain=domain,
            note=(
                "pcsx_watermark.auto_full_crawl=false; run `crawler board <slug> --pcsx-full-crawl`"
            ),
        )
        yield MonitorResult(
            urls=sitemap_urls,
            new_sitemap_url=new_sitemap_url,
            # Cache host/domain so next run doesn't need to derive them again.
            metadata_updates=_watermark.to_metadata_patch(
                _watermark.WatermarkState(
                    key=wm.key,
                    max_ts=wm.max_ts,
                    last_full_at=wm.last_full_at,
                    last_incremental_at=wm.last_incremental_at,
                    interval_days=wm.interval_days,
                    enabled=True,
                    auto_full_crawl=wm.auto_full_crawl,
                    extra={**wm.extra, "host": host, "domain": domain},
                )
            ),
        )
        return

    # --- Step 7: fetch PCSX ---
    try:
        if needs_full:
            log.info("eightfold.full_crawl_start", host=host, domain=domain)
            raw_positions = await _pcsx.fetch_all(host, domain, client)
            log.info(
                "eightfold.full_crawl_done",
                host=host,
                domain=domain,
                fetched=len(raw_positions),
            )
        else:
            log.info(
                "eightfold.incremental_start",
                host=host,
                domain=domain,
                max_posted_ts=wm.max_ts,
            )
            raw_positions = await _pcsx.fetch_incremental(
                host, domain, client, max_posted_ts=wm.max_ts
            )
            log.info(
                "eightfold.incremental_done",
                host=host,
                domain=domain,
                fetched=len(raw_positions),
            )
    except _pcsx.PcsxDisabled:
        # Tenant flipped disabled mid-run. Cache and fall back.
        wm.enabled = False
        wm.extra = {**wm.extra, "host": host, "domain": domain}
        log.warning("eightfold.pcsx_disabled", host=host, domain=domain, mid_run=True)
        yield MonitorResult(
            urls=sitemap_urls,
            new_sitemap_url=new_sitemap_url,
            metadata_updates=_watermark.to_metadata_patch(wm),
        )
        return
    except _pcsx.PcsxStableBlock as exc:
        # HTTP 405 from the tenant's WAF — the same behaviour we already
        # handle for "PCSX not enabled" tenants (bayer/american-express/…).
        # Retrying every cycle just burns requests and logs identical
        # errors; flip ``enabled=False`` so future runs take the
        # sitemap-only path until an operator resets the watermark.
        # Checked before ``PcsxFetchError`` because it's a subclass.
        wm.enabled = False
        wm.extra = {**wm.extra, "host": host, "domain": domain}
        log.warning(
            "eightfold.pcsx_stable_block",
            host=host,
            domain=domain,
            error=str(exc),
        )
        yield MonitorResult(
            urls=sitemap_urls,
            new_sitemap_url=new_sitemap_url,
            metadata_updates=_watermark.to_metadata_patch(wm),
        )
        return
    except _pcsx.PcsxFetchError as exc:
        # Transient failure (rate limit, 5xx). Emit sitemap-only result
        # with NO metadata_updates so the watermark is preserved and the
        # next run retries from the same point. Log at error level so
        # this shows up in operator alert dashboards — it represents
        # genuine data freshness loss for this cycle.
        log.error(
            "eightfold.pcsx_fetch_failed",
            host=host,
            domain=domain,
            error=str(exc),
        )
        yield MonitorResult(
            urls=sitemap_urls,
            new_sitemap_url=new_sitemap_url,
            hybrid=True,  # hybrid flag still set so touched-update is skipped
        )
        return

    # --- Step 8: correlate PCSX → sitemap URLs ---
    id_to_url = _pcsx.build_sitemap_id_map(sitemap_urls)
    jobs_by_url, unmatched, new_max_ts = _map_pcsx_to_discovered(
        raw_positions, id_to_url, board_host=host
    )

    # --- Step 9: build updated watermark (only on success) ---
    advanced_max_ts = max(wm.max_ts, new_max_ts)
    updated_wm = _watermark.WatermarkState(
        key=wm.key,
        max_ts=advanced_max_ts,
        last_full_at=now if needs_full else wm.last_full_at,
        last_incremental_at=now,
        interval_days=wm.interval_days,
        enabled=True,
        auto_full_crawl=wm.auto_full_crawl,
        extra={**wm.extra, "host": host, "domain": domain},
    )

    log.info(
        "eightfold.discover_done",
        host=host,
        sitemap_urls=len(sitemap_urls),
        pcsx_positions=len(raw_positions),
        matched=len(jobs_by_url),
        unmatched=unmatched,
        max_ts=advanced_max_ts,
        mode="full" if needs_full else "incremental",
    )

    yield MonitorResult(
        urls=sitemap_urls,
        jobs_by_url=jobs_by_url,
        new_sitemap_url=new_sitemap_url,
        hybrid=True,
        metadata_updates=_watermark.to_metadata_patch(updated_wm),
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Entry point for single-shot discovery (used by ``ws probe`` and tests).

    Returns the first (and only) batch yielded by :func:`discover_stream`.
    """
    from src.core.monitor import MonitorResult

    async for result in discover_stream(board, client, pw=pw):
        return result
    return MonitorResult()


# ── can_handle (unchanged behaviour) ───────────────────────────────────


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Eightfold: domain pattern, page HTML markers, or PCSX API probe."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # Fast path: *.eightfold.ai subdomain
    if _is_eightfold_domain(url):
        sitemap = _sitemap_url(url)
        result: dict = {"sitemap_url": sitemap}
        if client:
            from src.core.monitors.sitemap import _extract_urls, _try_fetch_xml

            root = await _try_fetch_xml(sitemap, client)
            if root is not None:
                urls = _extract_urls(root)
                job_urls = [u for u in urls if "/careers/job/" in u]
                result["urls"] = len(job_urls)
        return result

    if client is None:
        return None

    # Check page HTML for Eightfold markers
    html = await fetch_page_text(url, client)
    if html:
        lower = html.lower()
        if "eightfold.ai" in lower or "pcsx" in lower or "eightfoldai" in lower:
            sitemap = _sitemap_url(url)
            from src.core.monitors.sitemap import _extract_urls, _try_fetch_xml

            root = await _try_fetch_xml(sitemap, client)
            if root is not None:
                urls = _extract_urls(root)
                job_urls = [u for u in urls if "/careers/job/" in u]
                return {"sitemap_url": sitemap, "urls": len(job_urls)}

    # Last resort: probe the PCSX API on the host itself (lenient mode —
    # a 403 "PCSX is not enabled" response still confirms the tenant is
    # Eightfold, even though the runtime discover flow can't use PCSX
    # for that tenant).
    if await _pcsx.probe(host, host, client, strict=False):
        sitemap = _sitemap_url(url)
        return {"sitemap_url": sitemap}

    return None


# NOTE: Registered with ``stream=discover_stream`` only, NOT ``rich=True``.
# Setting rich=True would add ``eightfold`` to ``api_monitor_types()``, which
# changes the board-processing throttle key to a single shared bucket for
# all eightfold tenants — breaking per-tenant rate limits. The pipeline's
# ``is_rich = result.jobs_by_url is not None`` check detects rich mode at
# runtime from the actual result, not from the registration flag, so the
# hybrid data path still works correctly. Leaving eightfold out of
# ``api_monitor_types()`` also keeps the delist threshold at the safer
# ``_DELIST_THRESHOLD_FRAGILE = 2`` rather than the authoritative 1.
register("eightfold", discover, cost=8, can_handle=can_handle, stream=discover_stream)
