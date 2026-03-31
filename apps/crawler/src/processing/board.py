"""Board processing — monitor cycles, streaming, dry-run, single-board."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlparse

import asyncpg
import httpx
import structlog

from src.core.description_store import content_hash
from src.core.enum_normalize import normalize_employment_type
from src.core.monitors import api_monitor_types
from src.core.scrapers import enrich_description
from src.core.scrapers import scraper_needs_browser as _scraper_needs_browser
from src.metrics import monitor_jobs_discovered, tasks_total
from src.processing.cpu import (
    BatchResult,
    JobCPUResult,
    _build_locales,
    _build_titles,
    _coerce_locations,
    _coerce_text,
    _error_message,
    _extract_experience_fields,
    _extract_salary_fields,
    _parse_metadata,
    _resolve_locations_sync,
    _resolve_occupation_seniority,
    _resolve_technology_ids,
)
from src.processing.scrape import (
    _UPSERT_DESCRIPTION,
    ScrapeItem,
    _apply_defaults,
    _board_has_enrich,
    _PipelineResult,
)
from src.queries.monitor import (
    _BATCH_UPDATE_RICH_CONTENT,
    _CREATE_RICH_UPDATES_TEMP,
    _DELIST_BOARD_POSTINGS,
    _DELIST_THRESHOLD_AUTHORITATIVE,
    _DELIST_THRESHOLD_FRAGILE,
    _DIFF_BATCH,
    _EXTEND_BOARD_LEASE,
    _FETCH_DUE_BOARDS,
    _INSERT_RICH_JOB,
    _INSERT_RICH_JOB_ENRICH,
    _INSERT_URL_ONLY_JOBS,
    _MARK_GONE_BY_TIMESTAMP,
    _RECORD_EMPTY_CHECK,
    _RECORD_FAILURE,
    _RECORD_SUCCESS_NONEMPTY,
    _UPDATE_METADATA,
)
from src.queries.scrape import (
    _FETCH_BOARD_ALL_ACTIVE,
    _FETCH_BOARD_BY_SLUG,
    _FETCH_BOARD_SCRAPE_ITEMS,
)
from src.redis_queue import enqueue_scrape as _enqueue_scrape
from src.shared.html_normalize import normalize_description_html
from src.shared.langdetect import detect_all_languages, detect_language

log = structlog.get_logger()


class _BatchLookups:
    """Late-binding proxy so monkeypatch on src.batch propagates."""

    def __getattr__(self, name):
        import src.batch  # noqa: F811

        return getattr(src.batch, name)


_batch = _BatchLookups()

# ── Constants ────────────────────────────────────────────────────────

# API monitor types share a single API host per type (throttle-domain keys).
_API_MONITOR_TYPES = api_monitor_types()

# Max R2 backfill uploads per board run (touched postings without hashes).
# Prevents huge first-time runs from timing out. Backfill completes incrementally.
_SLOW_MONITOR_SECONDS = 30.0
_SLOW_SCRAPE_SECONDS = 15.0


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class BoardBatch:
    """One batch from a board -> DB writer."""

    board_id: str
    company_id: str
    board_url: str
    enrich_fields: list[str] | None
    urls: set[str]
    jobs_by_url: dict | None  # DiscoveredJob dict, or None for URL-only
    cpu_results: dict[str, JobCPUResult]  # keyed by URL
    delist_threshold: int


@dataclass
class BoardDone:
    """Final signal for a board -> DB writer runs mark_gone + record_success."""

    board_id: str
    board_url: str
    all_urls: set[str]
    delist_threshold: int
    total_new: int
    total_relisted: int


@dataclass
class BoardError:
    """Worker error -> DB writer runs _RECORD_FAILURE."""

    board_id: str
    board_url: str
    error_msg: str


async def _enqueue_scrapes_for_new(
    posting_rows: list,
    board_id: str,
    metadata: dict,
    board_log: structlog.stdlib.BoundLogger,
) -> None:
    """Enqueue scrapes for newly inserted postings into Redis."""
    if not posting_rows:
        return
    scraper_type = metadata.get("scraper_type", "json-ld")
    scraper_config = metadata.get("scraper_config")
    if not isinstance(scraper_config, dict):
        scraper_config = None
    needs_browser = _scraper_needs_browser(scraper_type, scraper_config)
    for row in posting_rows:
        pid = str(row["id"])
        url = row["source_url"]
        domain = urlparse(url).hostname or ""
        await _enqueue_scrape(
            domain,
            pid,
            0,  # score=0 → first-time, always urgent
            {
                "source_url": url,
                "board_id": board_id,
                "description_r2_hash": "",
                "scrape_step": "0",
            },
            browser=needs_browser,
            first_time=True,
        )
    board_log.info("batch.enqueued_scrapes", count=len(posting_rows), first_time=True)


async def _enqueue_scrapes_for_relisted(
    relisted: list[dict],
    board_id: str,
    metadata: dict,
    board_log: structlog.stdlib.BoundLogger,
) -> None:
    """Enqueue scrapes for relisted postings (came back after gone)."""
    if not relisted:
        return
    scraper_type = metadata.get("scraper_type", "json-ld")
    scraper_config = metadata.get("scraper_config")
    if not isinstance(scraper_config, dict):
        scraper_config = None
    needs_browser = _scraper_needs_browser(scraper_type, scraper_config)
    import time

    now = time.time()
    count = 0
    for r in relisted:
        url = r["url"]
        domain = urlparse(url).hostname or ""
        has_content = r.get("r2_hash") is not None
        await _enqueue_scrape(
            domain,
            r["id"],
            0 if not has_content else now,
            {
                "source_url": url,
                "board_id": board_id,
                "description_r2_hash": str(r.get("r2_hash") or ""),
                "scrape_step": "0",
            },
            browser=needs_browser,
            first_time=not has_content,  # never scraped → first-time
        )
        count += 1
    if count:
        board_log.info("batch.enqueued_scrapes", count=count, relisted=True)


class DeadlineExtender:
    """Shared between work item and pool to extend the timeout deadline.

    The streaming processor calls ``pulse()`` after each batch.  The pool
    loop checks the event to decide whether to renew the deadline or
    declare a true timeout.
    """

    def __init__(self):
        self._event = asyncio.Event()

    def pulse(self):
        """Signal that the work item is still making progress."""
        self._event.set()


def _throttle_key(board: asyncpg.Record) -> str:
    """Return the rate-limit domain for a board.

    API monitors share an API host per type (e.g. all greenhouse boards
    hit boards-api.greenhouse.io), so crawler_type is the key.
    URL-only monitors each hit their own company domain.
    """
    crawler_type = board["crawler_type"]
    if crawler_type in _API_MONITOR_TYPES:
        return crawler_type
    return urlparse(board["board_url"]).hostname or board["board_url"]


# ── Monitor Processing ───────────────────────────────────────────────


async def _process_one_board_streaming(
    board: asyncpg.Record,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    extender: object,
) -> tuple[bool, float]:
    """Run a streaming monitor cycle for a single board. Returns (success, duration_s).

    Yields batches from the monitor, processing each incrementally:
    - Extends the DB lease and pulses the deadline extender on each batch
    - Runs _DIFF_BATCH (new/touched/relisted only) per batch
    - Fires R2 uploads as background tasks overlapping with discovery
    - Runs _MARK_GONE once after all batches complete
    """
    board_id = str(board["id"])
    company_id = str(board["company_id"])
    board_url = board["board_url"]
    crawler_type = board["crawler_type"]

    board_log = log.bind(board_id=board_id, board_url=board_url, crawler_type=crawler_type)
    t0 = monotonic()

    pw = None
    pw_ctx = None
    effective_http = http

    try:
        metadata = board["metadata"] if board["metadata"] else {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        enrich_fields = _board_has_enrich(metadata)

        # Use a per-board insecure client when ssl_verify is disabled
        ssl_verify = metadata.get("ssl_verify", True)
        if not ssl_verify:
            from src.shared.http import create_http_client

            effective_http = create_http_client(verify=False)

        # Start Playwright if this monitor needs a browser
        if _batch.monitor_needs_browser(crawler_type, metadata):
            try:
                from playwright.async_api import async_playwright

                pw_ctx = async_playwright()
                pw = await pw_ctx.start()
                board_log.info("batch.monitor.playwright_started")
            except Exception:
                board_log.warning("batch.monitor.playwright_unavailable", exc_info=True)

        # Pre-load lookup tables once
        loc_resolver = await _batch._get_location_resolver(pool)
        rates = await _batch._get_currency_rates(pool)
        tech_id_map = await _batch._get_technology_ids(pool)
        occ_ids = await _batch._get_occupation_ids(pool)
        sen_ids = await _batch._get_seniority_ids(pool)

        # Capture stable timestamp before any batches for gone detection
        monitor_start_ts = await pool.fetchval("SELECT now()")

        total_discovered = 0
        total_new = 0
        total_relisted = 0
        batch_count = 0

        async for result in _batch.monitor_one_stream(
            board_url, crawler_type, metadata, effective_http, pw=pw
        ):
            batch_count += 1
            total_discovered += len(result.urls)
            is_rich = result.jobs_by_url is not None

            # Pulse heartbeat + extend DB lease (shielded to avoid
            # destroying the pool connection on task cancellation)
            extender.pulse()
            with contextlib.suppress(Exception):
                await asyncio.shield(pool.execute(_EXTEND_BOARD_LEASE, board_id))

            if not result.urls:
                continue

            async with pool.acquire() as conn, conn.transaction():
                # Persist newly discovered sitemap URL (once per board)
                if getattr(result, "new_sitemap_url", None):
                    await conn.execute(
                        _UPDATE_METADATA,
                        board_id,
                        json.dumps({"sitemap_url": result.new_sitemap_url}),
                    )

                is_rich_no_scrape = is_rich and not enrich_fields
                rows = await conn.fetch(
                    _DIFF_BATCH,
                    list(result.urls),
                    board_id,
                    is_rich_no_scrape,
                )

                new_urls: list[str] = []
                relisted: list[dict] = []
                touched: list[dict] = []

                for row in rows:
                    action = row["action"]
                    if action == "new":
                        new_urls.append(row["url"])
                    elif action == "relisted":
                        r2h = row["description_r2_hash"]
                        relisted.append(
                            {
                                "id": row["id"],
                                "url": row["url"],
                                "r2_hash": int(r2h) if r2h is not None else None,
                            }
                        )
                    elif action == "touched":
                        r2h = row["description_r2_hash"]
                        touched.append(
                            {
                                "id": row["id"],
                                "url": row["url"],
                                "r2_hash": int(r2h) if r2h is not None else None,
                            }
                        )

                total_new += len(new_urls)
                total_relisted += len(relisted)

                if result.jobs_by_url:
                    new_jobs = [result.jobs_by_url[u] for u in new_urls if u in result.jobs_by_url]

                    if new_jobs:
                        # CPU-heavy per-job processing -- run off the event loop
                        def _process_new_jobs_cpu(jobs):
                            """Pure CPU: normalize, detect language, resolve, extract."""
                            records = []
                            r2_staging = []
                            for j in jobs:
                                j.description = normalize_description_html(j.description)
                                enrich_description(j)
                                if not j.language and j.description:
                                    j.language = detect_language(j.description)

                                loc_ids_r, loc_types_r = _resolve_locations_sync(
                                    loc_resolver,
                                    _coerce_locations(j.locations),
                                    _coerce_text(j.job_location_type),
                                    _coerce_text(j.language),
                                )
                                desc_text = _coerce_text(j.description)
                                s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(
                                    desc_text, rates
                                )
                                exp_min, exp_max = _extract_experience_fields(desc_text)
                                t_ids = _resolve_technology_ids(desc_text, tech_id_map)
                                title_text = _coerce_text(j.title)
                                all_titles = _build_titles(title_text, j.localizations)
                                occ_id, sen_id = _resolve_occupation_seniority(
                                    all_titles, occ_ids, sen_ids
                                )
                                detected_langs = (
                                    detect_all_languages(j.description) if j.description else []
                                )
                                records.append(
                                    (
                                        company_id,
                                        board_id,
                                        normalize_employment_type(_coerce_text(j.employment_type)),
                                        j.url,
                                        all_titles,
                                        _build_locales(
                                            _coerce_text(j.language),
                                            j.localizations,
                                            detected_languages=detected_langs,
                                        ),
                                        loc_ids_r,
                                        loc_types_r,
                                        s_min,
                                        s_max,
                                        s_cur,
                                        s_per,
                                        s_eur,
                                        exp_min,
                                        exp_max,
                                        t_ids,
                                        occ_id,
                                        sen_id,
                                    )
                                )
                                r2_staging.append((j, t_ids))
                            return records, r2_staging

                        records, r2_staging = _process_new_jobs_cpu(new_jobs)

                        # DB backfill for location cache misses (rare)
                        if await loc_resolver.backfill_misses():
                            loc_resolver.drain_location_misses()

                        # Batch insert all new jobs
                        insert_sql = _INSERT_RICH_JOB_ENRICH if enrich_fields else _INSERT_RICH_JOB
                        inserted_ids = []
                        for rec in records:
                            row = await conn.fetchrow(insert_sql, *rec)
                            if row:
                                inserted_ids.append(str(row["id"]))

                        # Write descriptions for inserted jobs
                        for (j, _t_ids), posting_id in zip(r2_staging, inserted_ids, strict=False):
                            desc_html = _coerce_text(j.description)
                            if desc_html:
                                locale = _coerce_text(j.language) or "en"
                                await conn.execute(
                                    _UPSERT_DESCRIPTION,
                                    posting_id,
                                    locale,
                                    desc_html,
                                    content_hash(desc_html),
                                )

                        # Enqueue scrapes for rich jobs that need enrichment
                        if enrich_fields and inserted_ids:
                            rich_rows = [
                                {"id": pid, "source_url": j.url}
                                for (j, _), pid in zip(r2_staging, inserted_ids, strict=False)
                            ]
                            await _enqueue_scrapes_for_new(
                                rich_rows,
                                board_id,
                                metadata,
                                board_log,
                            )

                    # Update content for relisted and touched
                    update_triples = [
                        (item["id"], result.jobs_by_url[item["url"]], item.get("r2_hash"))
                        for item in relisted + touched
                        if item["url"] in result.jobs_by_url
                    ]
                    if update_triples:
                        for _, j, _ in update_triples:
                            j.description = normalize_description_html(j.description)
                            enrich_description(j)
                            if not j.language and j.description:
                                j.language = detect_language(j.description)

                        await conn.execute(_CREATE_RICH_UPDATES_TEMP)
                        records = []
                        for pid, j, _ in update_triples:
                            loc_ids, loc_types = await _batch._resolve_locations(
                                loc_resolver,
                                _coerce_locations(j.locations),
                                _coerce_text(j.job_location_type),
                                _coerce_text(j.language),
                            )
                            desc_text = _coerce_text(j.description)
                            s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(
                                desc_text, rates
                            )
                            exp_min, exp_max = _extract_experience_fields(desc_text)
                            t_ids = _resolve_technology_ids(desc_text, tech_id_map)
                            title_text = _coerce_text(j.title)
                            all_titles = _build_titles(title_text, j.localizations)
                            occ_id, sen_id = _resolve_occupation_seniority(
                                all_titles, occ_ids, sen_ids
                            )
                            detected_langs = (
                                detect_all_languages(j.description) if j.description else []
                            )
                            records.append(
                                (
                                    pid,
                                    normalize_employment_type(_coerce_text(j.employment_type)),
                                    all_titles,
                                    _build_locales(
                                        _coerce_text(j.language),
                                        j.localizations,
                                        detected_languages=detected_langs,
                                    ),
                                    loc_ids,
                                    loc_types,
                                    s_min,
                                    s_max,
                                    s_cur,
                                    s_per,
                                    s_eur,
                                    exp_min,
                                    exp_max,
                                    t_ids,
                                    occ_id,
                                    sen_id,
                                )
                            )
                        await conn.copy_records_to_table("_rich_updates", records=records)
                        await conn.execute(_BATCH_UPDATE_RICH_CONTENT)

                        # Write descriptions for updated postings
                        for pid, j, _existing_hash in update_triples:
                            desc_html = _coerce_text(j.description)
                            if desc_html:
                                locale = _coerce_text(j.language) or "en"
                                await conn.execute(
                                    _UPSERT_DESCRIPTION,
                                    str(pid),
                                    locale,
                                    desc_html,
                                    content_hash(desc_html),
                                )

                # URL-only path -- insert stubs with next_scrape_at
                if result.jobs_by_url is None and new_urls:
                    inserted = await conn.fetch(
                        _INSERT_URL_ONLY_JOBS,
                        company_id,
                        board_id,
                        new_urls,
                    )
                    board_log.info("batch.inserted_for_scrape", count=len(inserted))
                    await _enqueue_scrapes_for_new(inserted, board_id, metadata, board_log)

                # Enqueue scrapes for relisted jobs (came back after gone)
                # Skip for rich monitors without enrichment — they already have full data
                if not is_rich_no_scrape:
                    await _enqueue_scrapes_for_relisted(relisted, board_id, metadata, board_log)

            board_log.info(
                "batch.monitor.stream_batch",
                batch=batch_count,
                discovered=len(result.urls),
                new=len(new_urls),
            )

        # After all batches: mark gone postings
        if total_discovered == 0:
            # No URLs discovered at all (or all filtered out) -- treat as empty check
            elapsed = monotonic() - t0
            board_log.warning("batch.monitor.empty", duration_s=round(elapsed, 2))
            async with pool.acquire() as conn:
                rows = await conn.fetch(_RECORD_EMPTY_CHECK, board_id)
                if rows and rows[0]["board_status"] == "gone":
                    await conn.execute(_DELIST_BOARD_POSTINGS, board_id)
                    board_log.warning("batch.monitor.board_gone")
            return True, elapsed

        # Mark as gone any active posting not seen during this monitor run
        gone_count = 0
        delist_threshold = (
            _DELIST_THRESHOLD_AUTHORITATIVE
            if crawler_type in _API_MONITOR_TYPES
            else _DELIST_THRESHOLD_FRAGILE
        )
        async with pool.acquire() as conn, conn.transaction():
            gone_rows = await conn.fetch(
                _MARK_GONE_BY_TIMESTAMP,
                board_id,
                monitor_start_ts,
                delist_threshold,
            )
            gone_count = len(gone_rows)
            await conn.execute(_RECORD_SUCCESS_NONEMPTY, board_id)

        # Flush location misses to taxonomy_miss table
        await _batch._flush_location_misses(loc_resolver, pool)

        elapsed = monotonic() - t0
        board_log.info(
            "batch.monitor.success",
            discovered=total_discovered,
            new=total_new,
            relisted=total_relisted,
            gone=gone_count,
            batches=batch_count,
            duration_s=round(elapsed, 2),
        )

        # Emit Prometheus metrics
        tasks_total.labels(kind="monitor", status="succeeded").inc()
        if total_new:
            monitor_jobs_discovered.labels(profile="simple", action="new").inc(total_new)
        if total_relisted:
            monitor_jobs_discovered.labels(profile="simple", action="relisted").inc(total_relisted)
        if gone_count:
            monitor_jobs_discovered.labels(profile="simple", action="gone").inc(gone_count)

        if elapsed >= _SLOW_MONITOR_SECONDS:
            board_log.warning("batch.monitor.slow", duration_s=round(elapsed, 2))

        if total_new or gone_count:
            with contextlib.suppress(Exception):
                await _batch.get_redis().delete("cache:platform-stats")

        return True, elapsed

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        board_log.exception("batch.monitor.error", error=error_msg, duration_s=round(elapsed, 2))
        tasks_total.labels(kind="monitor", status="failed").inc()
        # Discard stale location misses from this failed board
        loc_resolver.drain_location_misses()
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_FAILURE, board_id, error_msg)
        return False, elapsed
    finally:
        if pw:
            await pw.stop()
        if effective_http is not http:
            await effective_http.aclose()


# ── Monitor Batch (--once mode) ──────────────────────────────────────


async def _monitor_pipeline(
    boards: list[asyncpg.Record],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
) -> _PipelineResult:
    """Process boards for one rate-limit domain serially."""
    result = _PipelineResult()
    for board in boards:
        try:
            extender = DeadlineExtender()
            ok, elapsed = await _process_one_board_streaming(board, pool, http, extender)
            result.durations.append(elapsed)
            if ok:
                result.succeeded += 1
        except Exception:
            log.exception("batch.monitor.pipeline_error", board_id=str(board["id"]))
    return result


async def process_monitor_batch(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int = 200,
    worker_id: str = "w",
) -> BatchResult:
    """Claim due boards and process with domain-parallel pipelines.

    Boards sharing a rate-limit domain (same ATS API or hostname) run
    serially to respect politeness.  Different domains run concurrently.
    """
    t0 = monotonic()
    boards = await pool.fetch(_FETCH_DUE_BOARDS, limit, worker_id)

    if not boards:
        return BatchResult()

    # Group by rate-limit domain
    groups: defaultdict[str, list[asyncpg.Record]] = defaultdict(list)
    for board in boards:
        groups[_throttle_key(board)].append(board)

    log.info("batch.monitor.start", boards=len(boards), domains=len(groups))

    # Run domain pipelines concurrently
    tasks: list[asyncio.Task[_PipelineResult]] = []
    async with asyncio.TaskGroup() as tg:
        for group_boards in groups.values():
            tasks.append(tg.create_task(_batch._monitor_pipeline(group_boards, pool, http)))

    pipeline_results = [t.result() for t in tasks]
    succeeded = sum(r.succeeded for r in pipeline_results)
    all_durations = [d for r in pipeline_results for d in r.durations]
    elapsed = monotonic() - t0

    return BatchResult(
        processed=len(boards),
        succeeded=succeeded,
        failed=len(boards) - succeeded,
        duration_s=round(elapsed, 2),
        slow_items=sum(1 for d in all_durations if d >= _SLOW_MONITOR_SECONDS),
        item_durations=all_durations,
    )


# ── Single Board ──────────────────────────────────────────────────────


async def dry_run_single_board(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_slug: str,
    *,
    verbose: bool = False,
    scrape_limit: int = 3,
    pw=None,
) -> None:
    """Dry-run a single board: monitor + scrape without any DB writes.

    Runs monitor_one() to discover jobs, then scrape_one() on a sample of URLs
    to show what the scraper would produce.  Useful for testing config changes.

    When *pw* is provided, Playwright is available for monitors/scrapers that
    require browser rendering (e.g. replay-mode api_sniffer, rendered nextdata).
    """
    from dataclasses import fields as dc_fields

    board = await pool.fetchrow(_FETCH_BOARD_BY_SLUG, board_slug)
    if not board:
        log.error("dry_run.not_found", board_slug=board_slug)
        return
    crawler_type = board["crawler_type"]
    metadata = _parse_metadata(board["metadata"])
    enrich_fields = _board_has_enrich(metadata)

    log.info(
        "dry_run.start",
        board_slug=board_slug,
        crawler_type=crawler_type,
        enrich=enrich_fields or "(none)",
    )

    # -- Monitor --
    result = await _batch.monitor_one(board["board_url"], crawler_type, metadata, http, pw=pw)

    is_rich = result.jobs_by_url is not None
    log.info(
        "dry_run.monitor.done",
        urls=len(result.urls),
        rich=is_rich,
        enrich=enrich_fields or "(none)",
    )

    if not result.urls:
        log.warning("dry_run.monitor.empty")
        return

    if is_rich and verbose:
        sample_url = next(iter(result.urls))
        job = result.jobs_by_url[sample_url]
        log.info("dry_run.monitor.sample_url", url=sample_url)
        for f in dc_fields(job):
            val = getattr(job, f.name)
            if val is not None:
                display = val
                if f.name == "description" and isinstance(val, str) and len(val) > 200:
                    display = val[:200] + "..."
                log.info("dry_run.monitor.field", field=f.name, value=display)
            else:
                log.info("dry_run.monitor.field", field=f.name, value="(null)")

    if is_rich and enrich_fields:
        # Show which fields the monitor provides vs what enrich will fill
        sample_url = next(iter(result.urls))
        job = result.jobs_by_url[sample_url]
        provided = [f.name for f in dc_fields(job) if getattr(job, f.name) is not None]
        missing = [f.name for f in dc_fields(job) if getattr(job, f.name) is None]
        log.info("dry_run.monitor.field_coverage", provided=provided, missing=missing)

    # -- Scraper --
    # Determine scraper settings (same logic as _load_board_scrapers)
    explicit_scraper = metadata.get("scraper_type")
    scraper_config = metadata.get("scraper_config")
    if not isinstance(scraper_config, dict):
        scraper_config = None

    if not explicit_scraper or explicit_scraper == "skip":
        if enrich_fields:
            scraper_type = "json-ld"
        else:
            from src.workspace._compat import auto_scraper_type

            auto = auto_scraper_type(crawler_type, metadata)
            if auto and auto[0] != "skip":
                scraper_type = auto[0]
                scraper_config = scraper_config or auto[1]
            elif auto and auto[0] == "skip":
                log.info("dry_run.scraper.skip", reason="rich monitor, no enrich configured")
                return
            else:
                scraper_type = "json-ld"
    else:
        scraper_type = explicit_scraper

    log.info(
        "dry_run.scraper.config",
        scraper_type=scraper_type,
        scraper_config=scraper_config,
        enrich=enrich_fields or "(none)",
    )

    # Pick sample URLs for scraping
    sample_urls = list(result.urls)[:scrape_limit]
    log.info("dry_run.scraper.start", sample_size=len(sample_urls), total=len(result.urls))

    cfg = scraper_config or {}
    for url in sample_urls:
        try:
            content = await _batch.scrape_one(url, scraper_type, scraper_config, http, pw=pw)
            content = _apply_defaults(content, cfg)
            content.description = normalize_description_html(content.description)

            if enrich_fields:
                has_data = any(getattr(content, f, None) is not None for f in enrich_fields)
                status = "ok" if has_data else "EMPTY (would fail)"
            elif content.title:
                status = "ok"
            else:
                status = "EMPTY (no title)"

            log.info(
                "dry_run.scraper.result",
                url=url,
                status=status,
                title=content.title,
                description_len=len(content.description) if content.description else 0,
                locations=content.locations,
                employment_type=content.employment_type,
            )

            if verbose:
                for f in dc_fields(content):
                    val = getattr(content, f.name)
                    if val is not None:
                        display = val
                        if f.name == "description" and isinstance(val, str) and len(val) > 300:
                            display = val[:300] + "..."
                        log.info("dry_run.scraper.field", url=url, field=f.name, value=display)
                    else:
                        log.info("dry_run.scraper.field", url=url, field=f.name, value="(null)")

        except Exception as exc:
            log.error("dry_run.scraper.error", url=url, error=_error_message(exc))

    log.info("dry_run.complete", board_slug=board_slug)


async def run_single_board(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_slug: str,
    *,
    force_rescrape: bool = False,
) -> None:
    """Process a single board end-to-end: monitor then scrape.

    Bypasses scheduling -- fetches the board directly by slug and processes
    all due scrape items for that board after the monitor run.
    When *force_rescrape* is True, scrapes all active jobs regardless of schedule.
    """
    board = await pool.fetchrow(_FETCH_BOARD_BY_SLUG, board_slug)
    if not board:
        log.error("single_board.not_found", board_slug=board_slug)
        return

    board_id = str(board["id"])
    log.info("single_board.monitor.start", board_slug=board_slug, board_id=board_id)

    # Monitor -- always use streaming path
    extender = DeadlineExtender()
    _ok, monitor_duration = await _process_one_board_streaming(board, pool, http, extender)
    log.info(
        "single_board.monitor.done", board_slug=board_slug, duration_s=round(monitor_duration, 2)
    )

    # Scrape items for this board
    query = _FETCH_BOARD_ALL_ACTIVE if force_rescrape else _FETCH_BOARD_SCRAPE_ITEMS
    rows = await pool.fetch(query, board["id"])
    if not rows:
        log.info("single_board.scrape.none_due", board_slug=board_slug)
        return

    items = [
        ScrapeItem(
            job_posting_id=str(row["id"]),
            url=row["source_url"],
            board_id=board_id,
            description_r2_hash=int(row["description_r2_hash"])
            if row["description_r2_hash"] is not None
            else None,
        )
        for row in rows
    ]

    info = await _batch._load_board_scrapers(pool, {board_id})

    if board_id in info.rich_board_ids:
        log.info("single_board.scrape.skip_rich", board_slug=board_slug)
        return

    groups: defaultdict[str, list[ScrapeItem]] = defaultdict(list)
    for item, row in zip(items, rows, strict=True):
        domain = row["scrape_domain"] or urlparse(item.url).hostname or "unknown"
        groups[domain].append(item)

    log.info("single_board.scrape.start", board_slug=board_slug, items=len(items))

    t0 = monotonic()
    tasks: list[asyncio.Task[_PipelineResult]] = []
    async with asyncio.TaskGroup() as tg:
        for group_items in groups.values():
            tasks.append(
                tg.create_task(_batch._scrape_pipeline(group_items, pool, http, info.scrapers))
            )

    pipeline_results = [t.result() for t in tasks]
    succeeded = sum(r.succeeded for r in pipeline_results)
    failed = len(items) - succeeded
    scrape_duration = monotonic() - t0
    log.info(
        "single_board.complete",
        board_slug=board_slug,
        scraped=len(items),
        succeeded=succeeded,
        failed=failed,
        scrape_duration_s=round(scrape_duration, 2),
    )
