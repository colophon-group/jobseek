"""Instance pipeline — claim work from Redis, process, write to local Postgres.

Each worker instance runs N discovery coroutines concurrently. Each coroutine
claims work from Redis via ``claim_work(browser=...)``, processes it using
the existing board/scrape functions, and loops. Processing writes directly
to local Postgres; no staging tables or sharded DB writers.

Usage::

    await run_pipeline(local_pool, http, shutdown_event, browser=False)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import asyncpg
import httpx
import structlog

from src.config import settings
from src.metrics import (
    monitor_duration_seconds,
    scrape_duration_seconds,
    tasks_total,
    worker_heartbeat_ts,
)
from src.redis_queue import (
    BoardWork,
    ScrapeWork,
    claim_work,
    reschedule_task,
)

log = structlog.get_logger()

# Backoff applied on processing errors (seconds).
_ERROR_BACKOFF_S = 300  # 5 minutes

# Idle backoff when no work is available (seconds).
_IDLE_BACKOFF_S = 2.0


# ---------------------------------------------------------------------------
# Board record reconstruction from Redis config hash
# ---------------------------------------------------------------------------


class _BoardRecord:
    """Minimal dict-like wrapper that mimics an asyncpg.Record for board processing.

    The existing ``_process_one_board`` / ``_process_one_board_streaming``
    functions read board fields via ``board["field"]``.  This class
    reconstructs that interface from the Redis config hash.
    """

    def __init__(self, board_id: str, config: dict) -> None:
        metadata_raw = config.get("metadata", "{}")
        try:
            metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        self._data = {
            "id": board_id,
            "company_id": config.get("company_id", ""),
            "board_url": config.get("board_url", ""),
            "crawler_type": config.get("crawler_type", ""),
            "metadata": metadata,
            "check_interval_minutes": int(config.get("check_interval_minutes", "60")),
            "scraper_type": config.get("scraper_type"),
            "scraper_config": config.get("scraper_config"),
            "throttle_key": config.get("throttle_key", ""),
        }

    def __getitem__(self, key: str):
        return self._data[key]

    def get(self, key: str, default=None):
        return self._data.get(key, default)


# ---------------------------------------------------------------------------
# Scrape item reconstruction from Redis config hash
# ---------------------------------------------------------------------------


def _scrape_item_from_redis(work: ScrapeWork):
    """Build a ``ScrapeItem`` compatible object from a Redis ScrapeWork claim.

    Returns ``(ScrapeItem, scrape_step)`` so the caller can pass the step
    through to ``_process_one_scrape``.
    """
    from src.processing.scrape import ScrapeItem

    item = ScrapeItem(
        job_posting_id=work.posting_id,
        url=work.source_url,
        board_id=work.board_id,
        description_r2_hash=work.description_r2_hash,
    )
    return item, work.scrape_step


# ---------------------------------------------------------------------------
# Discovery worker
# ---------------------------------------------------------------------------


async def _ensure_playwright(worker_log):
    """Start a Playwright server process. Returns ``(pw, pw_ctx)``."""
    from playwright.async_api import async_playwright

    pw_ctx = async_playwright()
    pw = await pw_ctx.start()
    worker_log.info("pipeline.worker.playwright_started")
    return pw, pw_ctx


async def _stop_playwright(pw, worker_log):
    """Stop a Playwright server process, suppressing errors."""
    try:
        await pw.stop()
    except Exception:
        worker_log.warning("pipeline.worker.playwright_stop_error", exc_info=True)


async def _discovery_worker(
    worker_id: int,
    local_pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    shutdown_event: asyncio.Event,
    *,
    browser: bool = False,
    monitor_semaphore: asyncio.Semaphore | None = None,
) -> None:
    """Single discovery worker coroutine.

    Claims work from Redis, dispatches to the appropriate processing
    function, reschedules in Redis, and loops until shutdown.

    ``monitor_semaphore`` caps concurrent monitor processing to bound
    peak memory (monitors hold full board results in memory).  Scrapes
    are lightweight and not limited.

    Browser workers create a shared Playwright server process per worker
    to avoid spawning (and leaking) a new process on every task.
    """
    worker_log = log.bind(worker_id=worker_id, browser=browser)
    worker_log.info("pipeline.worker.started")

    # Browser workers share one Playwright server per worker coroutine.
    pw = None
    if browser:
        try:
            pw, _pw_ctx = await _ensure_playwright(worker_log)
        except Exception:
            worker_log.warning("pipeline.worker.playwright_unavailable", exc_info=True)

    try:
        while not shutdown_event.is_set():
            worker_heartbeat_ts.labels(worker_id=str(worker_id)).set_to_current_time()
            try:
                work = await claim_work(browser=browser)
            except Exception:
                worker_log.warning("pipeline.claim_error", exc_info=True)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(shutdown_event.wait(), timeout=_IDLE_BACKOFF_S)
                continue

            if work is None:
                # No work available — back off
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(shutdown_event.wait(), timeout=_IDLE_BACKOFF_S)
                continue

            try:
                if work.kind == "monitor" and work.board_work is not None:
                    if monitor_semaphore is not None:
                        async with monitor_semaphore:
                            await _process_monitor_work(
                                worker_log,
                                work.board_work,
                                local_pool,
                                http,
                                browser=browser,
                                pw=pw,
                            )
                    else:
                        await _process_monitor_work(
                            worker_log,
                            work.board_work,
                            local_pool,
                            http,
                            browser=browser,
                            pw=pw,
                        )
                elif work.kind == "scrape" and work.scrape_work is not None:
                    await _process_scrape_work(
                        worker_log,
                        work.scrape_work,
                        local_pool,
                        http,
                        browser=browser,
                        pw=pw,
                    )
                else:
                    worker_log.warning("pipeline.unknown_work_kind", kind=work.kind)
            except Exception:
                worker_log.exception("pipeline.worker.task_escaped")
    finally:
        if pw:
            await _stop_playwright(pw, worker_log)

    worker_log.info("pipeline.worker.stopped")


# ---------------------------------------------------------------------------
# Monitor processing
# ---------------------------------------------------------------------------


async def _process_monitor_work(
    worker_log: structlog.stdlib.BoundLogger,
    board_work: BoardWork,
    local_pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    browser: bool = False,
    pw=None,
) -> None:
    """Process a single monitor work item claimed from Redis."""
    board_id = board_work.board_id
    config = board_work.config
    domain = board_work.domain

    worker_log = worker_log.bind(board_id=board_id, crawler_type=config.get("crawler_type"))

    try:
        board_record = _BoardRecord(board_id, config)

        from src.processing.board import (
            DeadlineExtender,
            _process_one_board_streaming,
        )

        extender = DeadlineExtender()
        success, duration = await _process_one_board_streaming(
            board_record, local_pool, http, extender, pw=pw
        )

        profile = "browser" if browser else "simple"
        monitor_duration_seconds.labels(profile=profile).observe(duration)

        # Reschedule in Redis with next check time
        check_interval = int(config.get("check_interval_minutes", "60"))
        next_check_at = time.time() + check_interval * 60
        await reschedule_task(domain, board_id, "monitor", next_check_at, browser=browser)

        worker_log.info(
            "pipeline.monitor.done",
            success=success,
            duration_s=round(duration, 2),
        )

    except Exception:
        worker_log.exception("pipeline.monitor.error", board_id=board_id)
        # Reschedule with backoff — guard so Redis errors don't kill the worker
        try:
            backoff_ts = time.time() + _ERROR_BACKOFF_S
            await reschedule_task(domain, board_id, "monitor", backoff_ts, browser=browser)
        except Exception:
            worker_log.warning("pipeline.monitor.reschedule_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Scrape processing
# ---------------------------------------------------------------------------


async def _process_scrape_work(
    worker_log: structlog.stdlib.BoundLogger,
    scrape_work: ScrapeWork,
    local_pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    browser: bool = False,
    pw=None,
) -> None:
    """Process a single scrape work item claimed from Redis."""
    posting_id = scrape_work.posting_id
    domain = scrape_work.domain
    worker_log = worker_log.bind(posting_id=posting_id, url=scrape_work.source_url)

    try:
        item, scrape_step = _scrape_item_from_redis(scrape_work)

        # Load scraper config from the board's Redis hash
        from src.redis_queue import get_redis

        r = get_redis()
        board_config = await r.hgetall(f"board:{scrape_work.board_id}")

        if board_config:
            metadata_raw = board_config.get("metadata", "{}")
            try:
                metadata = (
                    json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
                )
            except (json.JSONDecodeError, TypeError):
                metadata = {}

            crawler_type = board_config.get("crawler_type") or None
            scraper_type = metadata.get("scraper_type") or crawler_type or "dom"
            scraper_config = metadata.get("scraper_config")
            if isinstance(scraper_config, str):
                try:
                    scraper_config = json.loads(scraper_config)
                except (json.JSONDecodeError, TypeError):
                    scraper_config = None
        else:
            metadata = {}
            crawler_type = None
            scraper_type = "dom"
            scraper_config = None

        from src.processing.scrape import (
            _CLEAR_SCRAPE_FOR_RICH,
            _is_skip_no_scrape,
            _process_one_scrape,
        )

        async def _drop_skipped(reason: str) -> None:
            """Clear Postgres + delete the Redis scrape hash and return.

            Does NOT reschedule in Redis — the claim already removed the task
            from the per-domain ZSET, so simply returning drains one entry
            from the loop.
            """
            async with local_pool.acquire() as conn:
                await conn.execute(_CLEAR_SCRAPE_FOR_RICH, [posting_id])
            # Clean up the per-posting scrape config hash; leaving it creates
            # an orphan key (40k orphans across a drain is wasteful).
            try:
                await r.delete(f"scrape:{posting_id}")
            except Exception:
                worker_log.warning("pipeline.scrape.scrape_hash_delete_failed", exc_info=True)
            tasks_total.labels(kind="scrape", status="skipped_rich").inc()
            worker_log.info(
                "pipeline.scrape.skipped_rich",
                board_id=scrape_work.board_id,
                reason=reason,
            )

        # Defense in depth: rich monitors must never invoke the scraper
        # pipeline. If a stale task (pre-fix data, drift, or a rich-monitor
        # fallback) reaches this worker, clear the Postgres schedule and
        # drop the Redis task without rescheduling so the loop drains.
        if _is_skip_no_scrape(metadata, crawler_type):
            await _drop_skipped("rich monitor, no enrich")
            return

        # Fail-safe: an empty board config means Redis lost the board hash
        # (eviction, missing sync). The legacy fallback here used to pass
        # ``crawler_type`` as the scraper name, which is not a registered
        # scraper and raises ``KeyError``. Drop the task the same way we
        # drop rich-monitor stragglers so the worker doesn't crash.
        if not board_config:
            await _drop_skipped("missing board config in Redis")
            return

        success, duration = await _process_one_scrape(
            item,
            local_pool,
            http,
            scraper_type,
            scraper_config,
            pw=pw,
            scrape_step=scrape_step,
            scrape_interval=scrape_work.scrape_interval_hours,
        )

        profile = "browser" if browser else "simple"
        scrape_duration_seconds.labels(profile=profile).observe(duration)
        status = "succeeded" if success else "failed"
        tasks_total.labels(kind="scrape", status=status).inc()

        # Reschedule in Redis
        next_scrape_at = time.time() + scrape_work.scrape_interval_hours * 3600
        await reschedule_task(domain, posting_id, "scrape", next_scrape_at, browser=browser)

        worker_log.info(
            "pipeline.scrape.done",
            success=success,
            duration_s=round(duration, 2),
        )

    except Exception:
        worker_log.exception("pipeline.scrape.error", posting_id=posting_id)
        tasks_total.labels(kind="scrape", status="failed").inc()
        # Reschedule with backoff — guard so Redis errors don't kill the worker
        try:
            backoff_ts = time.time() + _ERROR_BACKOFF_S
            await reschedule_task(domain, posting_id, "scrape", backoff_ts, browser=browser)
        except Exception:
            worker_log.warning("pipeline.scrape.reschedule_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


async def run_pipeline(
    local_pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    shutdown_event: asyncio.Event,
    *,
    browser: bool = False,
) -> None:
    """Run the worker instance pipeline.

    Starts ``discovery_concurrency`` coroutines that claim work from Redis,
    process it using the existing board/scrape functions, and write results
    to local Postgres.  Runs until ``shutdown_event`` is set.

    Args:
        local_pool: asyncpg connection pool for local Postgres.
        http: Shared httpx client for HTTP requests.
        shutdown_event: Set this event to trigger graceful shutdown.
        browser: If True, claim from browser queues only.
    """
    concurrency = settings.discovery_concurrency
    monitor_cap = settings.monitor_concurrency
    monitor_sem = asyncio.Semaphore(monitor_cap) if monitor_cap > 0 else None
    log.info(
        "pipeline.starting",
        concurrency=concurrency,
        monitor_concurrency=monitor_cap,
        browser=browser,
    )

    try:
        async with asyncio.TaskGroup() as tg:
            for i in range(concurrency):
                tg.create_task(
                    _discovery_worker(
                        i,
                        local_pool,
                        http,
                        shutdown_event,
                        browser=browser,
                        monitor_semaphore=monitor_sem,
                    ),
                    name=f"discovery-{i}",
                )
    except* Exception as eg:
        # Log any worker exceptions that escaped
        for exc in eg.exceptions:
            log.error("pipeline.worker_exception", error=str(exc), exc_info=exc)

    log.info("pipeline.stopped", browser=browser)
