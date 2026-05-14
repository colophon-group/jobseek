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
import uuid

import asyncpg
import httpx
import structlog

from src.config import settings
from src.metrics import (
    inflight_deadletter_depth,
    inflight_depth,
    inflight_heartbeat_total,
    inflight_reaped_total,
    monitor_duration_seconds,
    monitor_failed_per_board_total,
    scrape_duration_seconds,
    tasks_total,
    worker_heartbeat_ts,
)
from src.redis_queue import (
    BoardWork,
    ScrapeWork,
    claim_work,
    complete_task,
    enqueue_monitor,
    enqueue_scrape,
    get_deadletter_depth,
    get_inflight_depth,
    heartbeat_task,
    reap_expired,
    reschedule_task,
)

log = structlog.get_logger()

# Backoff applied on processing errors (seconds).
_ERROR_BACKOFF_S = 300  # 5 minutes

# Idle backoff when no work is available (seconds).
_IDLE_BACKOFF_S = 2.0


# ---------------------------------------------------------------------------
# Inflight lease heartbeat (#3159 / #3173)
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _lease_heartbeat(
    task_type: str,
    domain: str,
    task_id: str,
    *,
    browser: bool,
    worker_log: structlog.stdlib.BoundLogger,
):
    """Run a background heartbeat for an in-flight task.

    Atomically extends the inflight lease every
    ``inflight_heartbeat_interval_seconds``. The heartbeat returns 0
    when the reaper has already reclaimed the lease — at that point the
    work is effectively orphaned (another worker may have re-claimed
    it) and continuing is unsafe, so the heartbeat exits silently and
    the caller's own work continues until completion. We don't cancel
    the parent task: orphaned writes are harmless because the
    underlying SQL is idempotent (UPSERTs / ON CONFLICT) and the second
    worker just replays them.

    Safety net (#3159 / #3173): on exit, this context manager calls
    ``complete_task`` to make sure the inflight lease entry is cleared
    even if the work code returned through an early-drop path without
    calling ``reschedule_task``. ``complete_task`` is idempotent (a
    successful ``reschedule_task`` already removed the entry, so the
    second ZREM is a no-op). This guarantees: anything we successfully
    finished — for any value of "finished" — leaves no orphan in the
    inflight ZSET, even if a future drop path is added without
    explicit cleanup.
    """
    interval = max(1.0, float(settings.inflight_heartbeat_interval_seconds))
    wtype = "browser" if browser else "simple"
    stop = asyncio.Event()

    async def _beat():
        try:
            while not stop.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                if stop.is_set():
                    return
                try:
                    extended = await heartbeat_task(
                        domain,
                        task_id,
                        task_type,
                        browser=browser,
                    )
                except Exception:
                    worker_log.warning("pipeline.heartbeat.error", exc_info=True)
                    inflight_heartbeat_total.labels(wtype=wtype, outcome="lost").inc()
                    continue
                if extended:
                    inflight_heartbeat_total.labels(wtype=wtype, outcome="extended").inc()
                else:
                    inflight_heartbeat_total.labels(wtype=wtype, outcome="lost").inc()
                    worker_log.warning(
                        "pipeline.heartbeat.lost",
                        task_type=task_type,
                        domain=domain,
                        task_id=task_id,
                    )
                    return
        except asyncio.CancelledError:
            return

    beat_task = asyncio.create_task(_beat(), name=f"hb-{task_type}-{task_id[:8]}")
    try:
        yield
    finally:
        stop.set()
        beat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await beat_task
        # Safety-net cleanup — idempotent if ``reschedule_task`` already
        # cleared the inflight entry. Suppress errors so a Redis blip on
        # cleanup never escalates into a worker crash.
        try:
            await complete_task(domain, task_id, task_type, browser=browser)
        except Exception:
            worker_log.warning("pipeline.complete_task.failed", exc_info=True)


# ---------------------------------------------------------------------------
# Reaper coroutine (#3159 / #3173)
# ---------------------------------------------------------------------------


async def _reaper_loop(
    shutdown_event: asyncio.Event,
    *,
    browser: bool,
) -> None:
    """Periodically sweep expired inflight leases back to per-domain queues.

    Runs once per pipeline (not per worker) to avoid stampedes on the
    Lua reaper. Sweeps both worker types' inflight ZSETs every tick so
    a simple/browser worker doing the sweep covers the cross-type case
    (e.g. a slim worker reaping a browser task that's been orphaned by
    a Playwright OOM, and vice-versa).
    """
    interval = max(1.0, float(settings.reaper_interval_seconds))
    reaper_log = log.bind(component="reaper", browser=browser)
    reaper_log.info("pipeline.reaper.started")
    try:
        while not shutdown_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            if shutdown_event.is_set():
                break
            for wtype, is_browser in (("simple", False), ("browser", True)):
                try:
                    result = await reap_expired(browser=is_browser)
                except Exception:
                    reaper_log.warning("pipeline.reaper.error", wtype=wtype, exc_info=True)
                    continue
                if result["reenqueued"]:
                    inflight_reaped_total.labels(wtype=wtype, outcome="reenqueued").inc(
                        result["reenqueued"]
                    )
                if result["dead_lettered"]:
                    inflight_reaped_total.labels(wtype=wtype, outcome="dead_lettered").inc(
                        result["dead_lettered"]
                    )
                if result["missing_config"]:
                    inflight_reaped_total.labels(wtype=wtype, outcome="missing_config").inc(
                        result["missing_config"]
                    )
                if result["reenqueued"] or result["dead_lettered"] or result["missing_config"]:
                    reaper_log.info(
                        "pipeline.reaper.swept",
                        wtype=wtype,
                        **result,
                    )
                # Refresh observability gauges every tick.
                try:
                    inflight_depth.labels(wtype=wtype).set(
                        await get_inflight_depth(browser=is_browser)
                    )
                    inflight_deadletter_depth.labels(wtype=wtype).set(
                        await get_deadletter_depth(browser=is_browser)
                    )
                except Exception:
                    pass
    finally:
        reaper_log.info("pipeline.reaper.stopped")


# ---------------------------------------------------------------------------
# Scraper resolution from Redis board hash
# ---------------------------------------------------------------------------


def _resolve_scraper(
    metadata: dict,
    crawler_type: str | None,
    scraper_config: dict | None,
) -> tuple[str, dict | None]:
    """Resolve (scraper_type, scraper_config) from a board's Redis metadata.

    Precedence: explicit ``metadata.scraper_type`` > monitor's auto-configured
    scraper (``auto_scraper_type``) > default ``"dom"``.

    Falling straight through to ``crawler_type`` as the scraper name is
    unsafe — many crawler types (``greenhouse``, ``lever``, ``personio`` …)
    aren't registered scrapers. Issue #2186 was caused by exactly that
    fallback: a personio board with no explicit ``scraper_type`` crashed
    with ``Unknown scraper type: 'personio'``.

    ``auto_scraper_type`` returning ``("skip", None)`` signals a rich
    monitor — ``_is_skip_no_scrape`` handles those callers separately, so
    we never invoke the ``skip`` scraper here.  A caller-supplied
    ``scraper_config`` wins over the auto-configured default, preserving
    board-level overrides.
    """
    from src.workspace._compat import auto_scraper_type

    explicit = metadata.get("scraper_type")
    if explicit:
        return explicit, scraper_config

    if crawler_type:
        auto = auto_scraper_type(crawler_type, metadata)
        if auto and auto[0] != "skip":
            resolved_config = scraper_config if scraper_config is not None else auto[1]
            return auto[0], resolved_config

    return "dom", scraper_config


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
                    async with _lease_heartbeat(
                        "monitor",
                        work.board_work.domain,
                        work.board_work.board_id,
                        browser=browser,
                        worker_log=worker_log,
                    ):
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
                    async with _lease_heartbeat(
                        "scrape",
                        work.scrape_work.domain,
                        work.scrape_work.posting_id,
                        browser=browser,
                        worker_log=worker_log,
                    ):
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
                    # Unknown kind — drop the lease so reaper doesn't loop on it.
                    try:
                        await complete_task(
                            work.domain or "",
                            work.task_id or "",
                            "monitor",
                            browser=browser,
                        )
                        await complete_task(
                            work.domain or "",
                            work.task_id or "",
                            "scrape",
                            browser=browser,
                        )
                    except Exception:
                        pass
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
        # Self-heal: a board may have already been marked `disabled` /
        # `gone` in Postgres but the Redis monitor task still exists
        # (sync only purges between CSV pushes — can be days). Drop the
        # task without rescheduling so the dead-board loop drains.
        # See issue #2215.
        async with local_pool.acquire() as conn:
            board_status = await conn.fetchval(
                "SELECT board_status FROM job_board WHERE id = $1::uuid",
                board_id,
            )
        if board_status in ("disabled", "gone"):
            tasks_total.labels(kind="monitor", status="skipped_disabled").inc()
            worker_log.info(
                "pipeline.monitor.skipped_disabled",
                board_status=board_status,
            )
            return

        # Self-heal: a slim worker that claimed a monitor whose CURRENT
        # config needs a browser would otherwise crash on Playwright launch
        # (see issue #2250 — same architectural failure mode as the scrape
        # path). Re-enqueue to the browser monitor queue and return.
        if not browser:
            from src.core.monitors import monitor_needs_browser

            crawler_type = config.get("crawler_type") or ""
            try:
                metadata_raw = config.get("metadata", "{}")
                metadata = (
                    json.loads(metadata_raw)
                    if isinstance(metadata_raw, str)
                    else (metadata_raw or {})
                )
            except (json.JSONDecodeError, TypeError):
                metadata = {}
            if monitor_needs_browser(crawler_type, metadata):
                try:
                    reroute_payload = dict(config)
                    reroute_payload.pop("domain", None)
                    await enqueue_monitor(
                        domain,
                        board_id,
                        time.time(),
                        reroute_payload,
                        browser=True,
                        first_time=False,
                    )
                    tasks_total.labels(kind="monitor", status="rerouted_to_browser").inc()
                    worker_log.info(
                        "pipeline.monitor.rerouted_to_browser",
                        domain=domain,
                        crawler_type=crawler_type,
                    )
                    return
                except Exception:
                    worker_log.warning("pipeline.monitor.reroute_failed", exc_info=True)

        board_record = _BoardRecord(board_id, config)

        from src.processing.board import (
            BoardGoneError,
            DeadlineExtender,
            _process_one_board_streaming,
        )

        extender = DeadlineExtender()
        try:
            success, duration = await _process_one_board_streaming(
                board_record, local_pool, http, extender, pw=pw
            )
        except BoardGoneError:
            # board.py already recorded gone + delisted postings.
            # Drop the Redis task instead of rescheduling — the board
            # is now filtered by the self-heal check above so it
            # won't return to the queue.
            return

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
        # Per-board failure attribution (#2704). Increment first so a
        # downstream Redis failure in the reschedule path doesn't hide
        # the original monitor failure from the metric.
        monitor_failed_per_board_total.labels(board_id=board_id).inc()
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

    # Bind posting_id as a contextvar so every downstream log line —
    # including from third-party code that uses structlog — carries the
    # correlation id (#3192). The merge_contextvars processor is already
    # configured in shared/logging.py. We unbind on the way out to keep
    # the next claim's events clean.
    structlog.contextvars.bind_contextvars(posting_id=posting_id)
    try:
        # Self-heal: production scheduling is via Redis ZSETs, NOT
        # Postgres ``next_scrape_at``. Without this check, the worker
        # ignores the Postgres delisting state — a posting that the
        # scrape side has tombstoned (is_active=false) or exhausted
        # the 3-failure backoff (next_scrape_at=NULL) would still
        # fire every ``scrape_interval_hours`` (default 24h) forever
        # because ``reschedule_task`` re-adds to the ZSET on every
        # claim. That defeats the dual-authority delisting model
        # documented in docs/03-crawler-architecture.md.
        #
        # Read the Postgres state once before doing any work; if the
        # row is tombstoned or has next_scrape_at=NULL, return WITHOUT
        # calling ``reschedule_task`` so the ZSET entry stays drained.
        #
        # ``posting_id`` validation: it comes from a Redis claim and is
        # supposed to be a UUID string, but a Lua bug or a manual ZADD
        # could push a non-UUID. Guard the cast — if it fails, drop
        # the bad work without rescheduling rather than letting the
        # ``$1::uuid`` cast crash the SELECT and fall through to the
        # outer except (which would reschedule and reintroduce the
        # original loop).
        #
        # SELECT failure: catch DB errors here too. If the SELECT
        # crashes (pool exhaustion, query timeout, connection drop),
        # we still must NOT fall through to the outer except's
        # reschedule path — that re-fires the work indefinitely. Drop
        # the work for this cycle; if the posting is still due, the
        # monitor's relisted path will re-enqueue when the URL is
        # next discovered.
        #
        # Hash-delete is deliberately skipped on the tombstoned path:
        # if the monitor relists this URL between our SELECT and a
        # ``r.delete``, the relisted_scrapes enqueue would write a
        # fresh ``scrape:<id>`` hash that we'd then wipe — silently
        # losing the relist until the next monitor cycle. The hash
        # leaks instead (one entry per tombstoned posting, ~100B
        # each); steady-state cost is bounded.
        #
        # Recovery: when Postgres is reachable, the monitor's
        # ``relisted`` CTE re-enqueues to Redis via
        # ``_enqueue_scrapes_for_relisted``, so a posting we self-heal
        # here can come back through the monitor side. (If Postgres is
        # sustained-down the monitor side also can't write — but
        # everything else is broken in that scenario too.)
        try:
            uuid.UUID(posting_id)
        except (ValueError, AttributeError, TypeError):
            # AttributeError = non-string lacking ``.replace``.
            # TypeError = None or other non-stringlike (rare; would be a
            # Lua bug returning the wrong type from the claim).
            tasks_total.labels(kind="scrape", status="skipped_invalid_id").inc()
            worker_log.warning("pipeline.scrape.skipped_invalid_id", posting_id=posting_id)
            return

        try:
            async with local_pool.acquire() as conn:
                posting_state = await conn.fetchrow(
                    "SELECT is_active, next_scrape_at FROM job_posting WHERE id = $1::uuid",
                    posting_id,
                )
        except Exception:
            tasks_total.labels(kind="scrape", status="skipped_db_error").inc()
            worker_log.warning("pipeline.scrape.self_heal_db_error", exc_info=True)
            return

        if posting_state is None:
            tasks_total.labels(kind="scrape", status="skipped_missing").inc()
            worker_log.info("pipeline.scrape.skipped_missing")
            return

        if not posting_state["is_active"] or posting_state["next_scrape_at"] is None:
            tasks_total.labels(kind="scrape", status="skipped_tombstoned").inc()
            worker_log.info(
                "pipeline.scrape.skipped_tombstoned",
                is_active=posting_state["is_active"],
                next_scrape_at_null=posting_state["next_scrape_at"] is None,
            )
            return

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
            scraper_config = metadata.get("scraper_config")
            if isinstance(scraper_config, str):
                try:
                    scraper_config = json.loads(scraper_config)
                except (json.JSONDecodeError, TypeError):
                    scraper_config = None
            if not isinstance(scraper_config, dict):
                scraper_config = None
            scraper_type, scraper_config = _resolve_scraper(metadata, crawler_type, scraper_config)
        else:
            metadata = {}
            crawler_type = None
            scraper_type = "dom"
            scraper_config = None

        from src.core.scrapers import scraper_needs_browser
        from src.processing.scrape import (
            _CLEAR_SCRAPE_FOR_RICH,
            _is_skip_no_scrape,
            _process_one_scrape,
        )

        async def _delete_scrape_hash() -> None:
            try:
                await r.delete(f"scrape:{posting_id}")
            except Exception:
                worker_log.warning("pipeline.scrape.scrape_hash_delete_failed", exc_info=True)

        async def _reroute_to_browser(reason: str) -> None:
            """Self-heal: a slim worker claimed a task whose current scraper
            config requires a browser. Re-enqueue to the browser queue so a
            browser-equipped worker can process it, then drop the in-flight
            slim claim. Avoids the Playwright-Executable-doesn't-exist
            failure path on slim images that ship without Chromium.

            Triggers when a board's scraper config flips render/needs_browser
            after tasks were already enqueued to the simple queue (sync race),
            or when stale pre-routing-fix tasks linger. See issue #2250.
            """
            existing = dict(await r.hgetall(f"scrape:{posting_id}"))
            # Drop ``domain`` since enqueue_scrape re-injects it from the arg.
            existing.pop("domain", None)
            try:
                await enqueue_scrape(
                    domain,
                    posting_id,
                    time.time(),
                    existing,
                    browser=True,
                    first_time=False,
                )
            except Exception:
                worker_log.warning("pipeline.scrape.reroute_failed", exc_info=True)
                return
            tasks_total.labels(kind="scrape", status="rerouted_to_browser").inc()
            worker_log.info(
                "pipeline.scrape.rerouted_to_browser",
                board_id=scrape_work.board_id,
                domain=domain,
                reason=reason,
                scraper_type=scraper_type,
            )

        async def _drop_rich(reason: str) -> None:
            """Rich-monitor path: scoped Postgres clear + drop Redis task.

            Uses ``_CLEAR_SCRAPE_FOR_RICH`` which requires the board to
            STILL be rich-no-scrape (race guard for config drift). Does
            NOT reschedule in Redis — the claim already removed the task
            from the per-domain ZSET, so returning drains one entry.
            """
            async with local_pool.acquire() as conn:
                await conn.execute(_CLEAR_SCRAPE_FOR_RICH, [posting_id])
            await _delete_scrape_hash()
            tasks_total.labels(kind="scrape", status="skipped_rich").inc()
            worker_log.info(
                "pipeline.scrape.skipped_rich",
                board_id=scrape_work.board_id,
                reason=reason,
            )

        async def _fail_stale_task(reason: str) -> None:
            """Fail-safe path: Redis board hash is missing or corrupt.

            We don't know if the board is rich or not, so we can't use the
            scoped rich clear (which would no-op on a non-rich board and
            leave the posting re-claim looping). Use the transient SQL
            so the existing 30 / 60 / 90-min backoff applies WITHOUT
            counting toward the tombstone budget — three Redis-eviction
            blips on the same posting must not flip a live posting to
            ``is_active = false``.
            """
            from src.processing.scrape import _RECORD_SCRAPE_TRANSIENT

            async with local_pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_TRANSIENT, posting_id)
            await _delete_scrape_hash()
            tasks_total.labels(kind="scrape", status="stale_config").inc()
            worker_log.warning(
                "pipeline.scrape.stale_config",
                board_id=scrape_work.board_id,
                reason=reason,
            )

        # Defense in depth: rich monitors must never invoke the scraper
        # pipeline. If a stale task (pre-fix data, drift, or a rich-monitor
        # fallback) reaches this worker, clear the Postgres schedule and
        # drop the Redis task without rescheduling so the loop drains.
        if _is_skip_no_scrape(metadata, crawler_type):
            await _drop_rich("rich monitor, no enrich")
            return

        # Fail-safe: an empty board config means Redis lost the board hash
        # (eviction, missing sync). The legacy fallback used to pass
        # ``crawler_type`` as the scraper name, raising ``KeyError`` for
        # names like "greenhouse" that aren't registered scrapers. Fail
        # the task softly so the worker doesn't crash AND doesn't wipe a
        # legitimate schedule.
        if not board_config:
            await _fail_stale_task("missing board config in Redis")
            return

        # Self-heal: a slim worker that claimed a scrape whose CURRENT
        # board config needs a browser would otherwise call into Playwright
        # and crash with "Executable doesn't exist". Re-enqueue to the
        # browser queue and let a browser-equipped worker pick it up. This
        # absorbs the post-#2237 stale-task tail and any future sync race
        # where the queue routing was decided before the config change. See
        # issue #2250.
        if not browser and scraper_needs_browser(scraper_type, scraper_config):
            await _reroute_to_browser(
                "scraper needs browser, re-routing from simple to browser queue"
            )
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

        # Lifecycle anchor: emit ``posting.scraped`` only on success so an
        # operator with the posting_id can confirm "yes, this URL completed
        # a scrape cycle" without filtering out failure noise (#3192). The
        # adjacent ``pipeline.scrape.done`` line above is intentionally kept
        # — it still carries success/failure for the existing dashboards.
        if success:
            worker_log.info(
                "posting.scraped",
                posting_id=posting_id,
                board_id=scrape_work.board_id,
                source_url=scrape_work.source_url,
                duration_s=round(duration, 2),
            )

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
    finally:
        # Clear the contextvar so the next claim's events don't inherit
        # this posting_id (#3192).
        structlog.contextvars.unbind_contextvars("posting_id")


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
            # Reaper: one per pipeline. Sweeps inflight leases for
            # tasks orphaned by worker SIGKILL / OOM / segfault back
            # onto the per-domain queue. See #3159 / #3173.
            tg.create_task(
                _reaper_loop(shutdown_event, browser=browser),
                name="reaper",
            )
    except* Exception as eg:
        # Log any worker exceptions that escaped
        for exc in eg.exceptions:
            log.error("pipeline.worker_exception", error=str(exc), exc_info=exc)

    log.info("pipeline.stopped", browser=browser)
