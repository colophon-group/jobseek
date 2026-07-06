"""Board processing — monitor cycles, streaming, dry-run, single-board."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import asyncpg
import httpx
import structlog

from src.core.description_store import content_hash
from src.core.enum_normalize import normalize_employment_type
from src.core.monitors import BoardGoneError, api_monitor_types
from src.core.scrapers import enrich_description
from src.core.scrapers import scraper_needs_browser as _scraper_needs_browser
from src.metrics import (
    monitor_dedup_total,
    monitor_gone_skipped_total,
    monitor_jobs_discovered,
    monitor_skipped_tdm_total,
    monitor_truncated_total,
    monitor_url_filtered_total,
    tasks_total,
)
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
    _is_skip_no_scrape,
    _PipelineResult,
)
from src.queries.monitor import (
    _BATCH_UPDATE_RICH_CONTENT,
    _BLAST_RADIUS_FLOOR_DEFAULT,
    _COUNT_BOARD_ACTIVE_AND_MISSING,
    _CREATE_RICH_UPDATES_TEMP,
    _DELIST_BOARD_POSTINGS,
    _DELIST_THRESHOLD_AUTHORITATIVE,
    _DELIST_THRESHOLD_FRAGILE,
    _DIFF_BATCH,
    _DROP_GUARD_HISTORY_WINDOW,
    _DROP_GUARD_MIN_HISTORY,
    _DROP_GUARD_THRESHOLD_DEFAULT,
    _EXTEND_BOARD_LEASE,
    _FETCH_DUE_BOARDS,
    _INSERT_RICH_JOB,
    _INSERT_RICH_JOB_ENRICH,
    _INSERT_URL_ONLY_JOBS,
    _MARK_GONE_BY_TIMESTAMP,
    _RECORD_BOARD_GONE,
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
from src.shared.tdm import TDMReservedError

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


# ── URL sanity check ─────────────────────────────────────────────────


def _classify_job_url(url: str, board_url: str | None = None) -> str | None:
    """Return a rejection reason for *url*, or None if it looks plausible.

    Catches data-quality bugs where DOM-based monitors emit site-root or
    navigation URLs (e.g. ``https://krb-sjobs.brassring.com/``, ``.../#``)
    that every monitor run re-discovers and every insert then collides on
    ``job_posting_source_url_key``. Returning the reason (instead of a
    plain bool) lets the caller break the dropped-URL counter down by
    rule so a single noisy DOM monitor is easy to spot in Grafana.

    Rejection reasons (stable metric label values):

    - ``"invalid"`` — empty, malformed, or missing scheme/host.
    - ``"bare_host"`` — path is empty, ``/``, or a bare hash fragment.
    - ``"board_homepage"`` — host matches the board's own host and the
      path (after ``rstrip("/")``) equals the board's own path, which
      catches hash-only variants like ``.../#0``. The rule is skipped
      when the discovered URL carries a non-empty query string, since
      query-keyed job URLs legitimately share the board's listing path
      (e.g. Lufthansa's ``index.php?ac=jobad&id=...``).
    """
    if not url:
        return "invalid"
    try:
        p = urlparse(url)
    except ValueError:
        return "invalid"
    if not p.scheme or not p.netloc:
        return "invalid"
    path = (p.path or "").rstrip("/")
    if not path:
        return "bare_host"
    if board_url:
        try:
            bp = urlparse(board_url)
        except ValueError:
            bp = None
        if (
            bp
            and bp.netloc.lower() == p.netloc.lower()
            and (bp.path or "").rstrip("/") == path
            and not p.query
        ):
            return "board_homepage"
    return None


def _is_plausible_job_url(url: str, board_url: str | None = None) -> bool:
    """Thin bool wrapper around :func:`_classify_job_url` for readability."""
    return _classify_job_url(url, board_url) is None


# ── URL canonicalization ──────────────────────────────────────────────
#
# Some ATS platforms render anchor ``href`` values that embed a
# session-scoped CSRF token in the query string. Each monitor cycle
# produces a different token, so ``ON CONFLICT (source_url) DO NOTHING``
# sees the row as new every time and ``_enqueue_scrapes_for_new``
# re-enqueues the same posting into ``scrapes_browser:<domain>``. One
# Pictet Group board on SuccessFactors inflated its browser scrape
# queue to 27,825 entries for ~a few hundred real postings this way
# before the pattern was caught.
#
# Strip params that are session state (not identity) on known-affected
# platforms; leave everything else alone.

_SUCCESSFACTORS_VOLATILE_PARAMS = frozenset(
    {
        "_s.crb",  # per-render CSRF token
        "jobAlertController_jobAlertId",
        "jobAlertController_jobAlertName",
        "browserTimeZone",
    }
)


# tal.net (TalentLink ATS) embeds a per-render CSRF/session token as a
# *path* segment of the form ``/xf-<12 hex chars>/`` (e.g.
# ``/brand-6/xf-767829ced96c/candidate/...``). Each Playwright render
# emits a fresh token, so the same opportunity ID (``opp/2968-...``)
# produces a different ``source_url`` every monitor cycle. Without
# stripping, ``ON CONFLICT (source_url) DO NOTHING`` inflated Evercore
# to ~12,340 rows for ~40 real postings before the pattern was caught
# (issue #2941). The token is a path segment, not a query param, so
# the SuccessFactors branch above doesn't touch it.
_TAL_NET_XF_SEGMENT = re.compile(r"/xf-[a-f0-9]+(?=/)")


def _canonicalize_url(url: str) -> str:
    """Strip session-scoped tokens from URLs on platforms where a
    ``<a href>`` embeds a per-render CSRF/session value that otherwise
    makes every monitor cycle rediscover the same posting as "new".

    Currently handles two ATS platforms:

    - **SuccessFactors family** (``*.successfactors.*`` / ``*.sapsf.*``)
      — token is a *query param*; drop the ones listed in
      :data:`_SUCCESSFACTORS_VOLATILE_PARAMS`. Identity-carrying params
      (``career_job_req_id``, ``company``, ``rcm_site_locale`` …) stay.
    - **tal.net / TalentLink** (``*.tal.net``) — token is a *path*
      segment matching :data:`_TAL_NET_XF_SEGMENT` (``/xf-<hex>/``);
      drop it. Everything else in the path (``/brand-N/``,
      ``/opp/<id>``, ``/en-GB``, …) carries identity and stays.
    """
    if not url:
        return url
    try:
        p = urlparse(url)
    except ValueError:
        return url
    host = (p.netloc or "").lower()
    if ".successfactors." in host or ".sapsf." in host:
        # keep_blank_values=True preserves stable no-value keys like
        # ``jobAlertController_jobAlertId=`` — filtering here would
        # silently reshape URLs on boards we haven't analyzed yet.
        params = parse_qsl(p.query, keep_blank_values=True)
        kept = [(k, v) for k, v in params if k not in _SUCCESSFACTORS_VOLATILE_PARAMS]
        if len(kept) == len(params):
            return url
        return urlunparse(p._replace(query=urlencode(kept)))
    if host.endswith(".tal.net") or host == "tal.net":
        new_path = _TAL_NET_XF_SEGMENT.sub("", p.path or "")
        if new_path == p.path:
            return url
        return urlunparse(p._replace(path=new_path))
    return url


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


async def _delist_board_postings(conn: asyncpg.Connection, board_id: str) -> int:
    """Run ``_DELIST_BOARD_POSTINGS`` and return the row count.

    Used by the three silent-delist paths that bypass the normal
    ``_MARK_GONE_BY_TIMESTAMP`` flow: empty-check threshold reached,
    BoardGoneError (upstream 404), and 5-strike failure auto-disable.
    Without these paths emitting the matching ``gone`` counter, the
    Grafana panel showed ``new >> gone`` even when the DB was balanced.

    Critically, the Prometheus increment is intentionally NOT done here:
    the counter must only fire AFTER the surrounding transaction
    commits, otherwise a rollback would leave the metric over-reporting
    deletions that never happened.
    """
    rows = await conn.fetch(_DELIST_BOARD_POSTINGS, board_id)
    return len(rows)


def _emit_gone_counter(gone_count: int) -> None:
    """Increment ``monitor_jobs_discovered{action="gone"}`` after a
    delist transaction has committed. Caller must invoke this OUTSIDE
    any ``conn.transaction()`` block — see ``_delist_board_postings``.
    """
    if gone_count:
        monitor_jobs_discovered.labels(profile="simple", action="gone").inc(gone_count)


def _resolve_delist_threshold(metadata: dict | None, crawler_type: str) -> int:
    """Pick the miss-count threshold for ``_MARK_GONE_BY_TIMESTAMP``.

    Default: ``_DELIST_THRESHOLD_AUTHORITATIVE`` (1) for API monitors with
    definitive list semantics (greenhouse, lever, ashby, …),
    ``_DELIST_THRESHOLD_FRAGILE`` (4 since #2725) for URL-only monitors
    where a single missed cycle is often a transient pagination flap.

    Per-board override (#2725): ``metadata.delist_threshold`` accepts an
    integer ≥ 1. Bool is excluded because ``isinstance(True, int)`` is
    True in Python and we don't want a spurious ``True`` flag to silently
    mean ``threshold=1``. Anything invalid (negative, zero, non-numeric,
    bool) falls through to the type-based default rather than raising,
    so a malformed CSV row never breaks the monitor cycle.

    Floats truncate via ``int()``: ``4.7 -> 4``, ``0.9 -> 0`` (which then
    falls back to the default). JSON has a single number type; CSV
    operators writing ``"delist_threshold": 4`` get an int, ``4.0`` an
    int (whole), ``4.7`` truncated. Strictness in the float case isn't
    worth the operator-friction.

    Caveat for ``delist_threshold = 1`` on paginated URL-only monitors:
    a single failed page during pagination would tombstone every URL
    beyond the failure point on the same cycle. Pair with the drop /
    blast-radius guards from #2729 (``metadata.drop_threshold``,
    ``metadata.blast_radius_floor``) when overriding to 1.

    Clearing an existing override: edit ``boards.csv`` to omit the key
    AND run a SQL ``UPDATE job_board SET metadata = metadata - 'delist_threshold'``
    on the affected row, since ``_UPSERT_BOARD_LOCAL`` preserves
    runtime overrides across CSV-only resyncs (COALESCE pattern).
    """
    default = (
        _DELIST_THRESHOLD_AUTHORITATIVE
        if crawler_type in _API_MONITOR_TYPES
        else _DELIST_THRESHOLD_FRAGILE
    )
    val = (metadata or {}).get("delist_threshold")
    if isinstance(val, bool) or val is None:
        return default
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return n if n >= 1 else default


def _setting(md: dict, key: str, default: float) -> float:
    """Read a per-board float override, or fall back to *default*.

    Explicit ``None`` check (rather than ``md.get(key) or default``)
    so a legitimate override of ``0.0`` survives — e.g. an operator
    setting ``drop_threshold = 0.0`` to disable the proportional check
    on a board with naturally volatile counts.
    """
    val = md.get(key)
    return default if val is None else float(val)


async def _mark_gone_with_guards(
    conn: asyncpg.Connection,
    board_id: str,
    discovered: int,
    monitor_start_ts,
    metadata: dict | None,
    delist_threshold: int,
    board_log: structlog.stdlib.BoundLogger,
) -> tuple[int, str | None]:
    """Run :data:`_MARK_GONE_BY_TIMESTAMP` behind two resilience guards.

    Both guards exist because a paginating monitor (dom, sitemap-multi-shard,
    eightfold PCSX, api_sniffer) that silently truncates returns a
    success-shaped partial URL set. Without these checks the missing URLs
    get ``missing_count++`` and tombstone after the fragile threshold.

    1. **Drop guard (#2723)** — when ``discovered`` falls more than
       ``metadata.drop_threshold`` (default :data:`_DROP_GUARD_THRESHOLD_DEFAULT`)
       below the median of ``metadata.recent_discovered_counts``, skip
       gone-detection. Needs at least :data:`_DROP_GUARD_MIN_HISTORY` past
       runs in the rolling window — fresh boards rely on (2).
    2. **Blast-radius guard (#2724)** — when more than
       ``metadata.blast_radius_floor`` (default
       :data:`_BLAST_RADIUS_FLOOR_DEFAULT`) of the board's active postings
       would be marked missing this cycle, skip. Last-line defense.

    On a skipped cycle the board's metadata gets ``suspect_streak`` bumped
    so consecutive flaps are visible in the dashboard. On a passing cycle
    the streak resets to zero and ``recent_discovered_counts`` rolls
    forward (cap :data:`_DROP_GUARD_HISTORY_WINDOW`).

    Returns ``(gone_count, skip_reason)``. ``skip_reason`` is one of
    ``"drop"`` / ``"blast_radius"`` / ``None``. Caller must increment
    :data:`monitor_gone_skipped_total` *after* the surrounding transaction
    commits — same pattern as :func:`_emit_gone_counter`.
    """
    md = metadata or {}
    history = list(md.get("recent_discovered_counts") or [])
    streak = int(md.get("suspect_streak") or 0)

    drop_threshold = _setting(md, "drop_threshold", _DROP_GUARD_THRESHOLD_DEFAULT)
    blast_floor = _setting(md, "blast_radius_floor", _BLAST_RADIUS_FLOOR_DEFAULT)

    skip_reason: str | None = None

    # (1) Drop guard — only fires once we have enough history to compute
    # a stable expected count.
    if len(history) >= _DROP_GUARD_MIN_HISTORY:
        from statistics import median

        expected = median(history)
        if expected > 0 and discovered < expected * (1.0 - drop_threshold):
            board_log.warning(
                "batch.monitor.suspect_drop",
                discovered=discovered,
                expected=int(expected),
                drop_threshold=drop_threshold,
                history=history,
                streak=streak + 1,
            )
            skip_reason = "drop"

    # (2) Blast-radius guard. Always run when (1) didn't fire so a fresh
    # board (no history yet) is still protected against catastrophic
    # truncation.
    if skip_reason is None:
        row = await conn.fetchrow(
            _COUNT_BOARD_ACTIVE_AND_MISSING,
            board_id,
            monitor_start_ts,
        )
        # Production: asyncpg.Record with int ``active`` / ``missing``
        # columns (COUNT(*)). Tests wire the same shape via the fixture's
        # default ``conn.fetchrow`` side_effect dispatcher.
        active = int(row["active"]) if row is not None else 0
        missing = int(row["missing"]) if row is not None else 0
        if active > 0 and missing / active > blast_floor:
            board_log.warning(
                "batch.monitor.blast_radius_exceeded",
                active=active,
                missing=missing,
                ratio=round(missing / active, 3),
                blast_radius_floor=blast_floor,
                streak=streak + 1,
            )
            skip_reason = "blast_radius"

    if skip_reason is not None:
        await conn.execute(
            _UPDATE_METADATA,
            board_id,
            json.dumps({"suspect_streak": streak + 1}),
        )
        return 0, skip_reason

    # Both guards passed — perform gone-detection and roll the baseline.
    gone_rows = await conn.fetch(
        _MARK_GONE_BY_TIMESTAMP,
        board_id,
        monitor_start_ts,
        delist_threshold,
    )
    new_history = (history + [discovered])[-_DROP_GUARD_HISTORY_WINDOW:]
    await conn.execute(
        _UPDATE_METADATA,
        board_id,
        json.dumps({"recent_discovered_counts": new_history, "suspect_streak": 0}),
    )
    return len(gone_rows), None


# A 5-strike auto-disable doesn't necessarily mean the board is dead.
# With exponential backoff (5 * 2^n minutes capped at 24h), strike #5
# fires after ~155 minutes — well inside a single provider outage. If
# we delisted on every disable, a 3-hour greenhouse blip would tombstone
# tens of thousands of postings that would all flap back as ``relisted``
# on recovery, churning search and IndexNow. Gate the delist on
# ``last_success_at`` so transient outages back off without data loss;
# only boards that have been failing past this window get delisted.
_DELIST_AFTER_FAILURE_AGE = timedelta(hours=24)


async def _maybe_delist_after_disable(
    conn: asyncpg.Connection,
    board_id: str,
    last_success_at: datetime | None,
    board_log: structlog.stdlib.BoundLogger,
) -> int:
    """Delist a 5-strike-disabled board's postings ONLY if the board
    has been silent past ``_DELIST_AFTER_FAILURE_AGE``. Returns the
    count of rows flipped (0 if the recency gate skipped the delist).
    """
    # asyncpg returns timezone-aware timestamps for TIMESTAMPTZ
    now = datetime.now(tz=UTC)
    if last_success_at is not None and now - last_success_at < _DELIST_AFTER_FAILURE_AGE:
        board_log.warning(
            "batch.monitor.five_strike_disable_kept_active",
            last_success_age_s=int((now - last_success_at).total_seconds()),
            reason="recent success — likely transient outage",
        )
        return 0
    return await _delist_board_postings(conn, board_id)


async def _enqueue_scrapes_for_new(
    posting_rows: list,
    board_id: str,
    metadata: dict,
    board_log: structlog.stdlib.BoundLogger,
    *,
    crawler_type: str | None = None,
) -> None:
    """Enqueue scrapes for newly inserted postings into Redis."""
    if not posting_rows:
        return
    # Rich monitors provide full job data; never route them through the
    # scrape pipeline or the placeholder ``skip`` scraper will fire.
    # Pass ``crawler_type`` so implicit rich monitors (no explicit
    # scraper_type but rich crawler_type) are caught too.
    if _is_skip_no_scrape(metadata, crawler_type):
        board_log.debug(
            "batch.enqueue_scrape.skipped_rich",
            count=len(posting_rows),
            reason="rich monitor, no enrich",
        )
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
    *,
    crawler_type: str | None = None,
) -> None:
    """Enqueue scrapes for relisted postings (came back after gone)."""
    if not relisted:
        return
    # Rich monitors provide full job data; never route them through the
    # scrape pipeline or the placeholder ``skip`` scraper will fire.
    if _is_skip_no_scrape(metadata, crawler_type):
        board_log.debug(
            "batch.enqueue_scrape.skipped_rich",
            count=len(relisted),
            reason="rich monitor, no enrich",
        )
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
    pw=None,
) -> tuple[bool, float]:
    """Run a streaming monitor cycle for a single board. Returns (success, duration_s).

    Yields batches from the monitor, processing each incrementally:
    - Extends the DB lease and pulses the deadline extender on each batch
    - Runs _DIFF_BATCH (new/touched/relisted only) per batch
    - Fires R2 uploads as background tasks overlapping with discovery
    - Runs _MARK_GONE once after all batches complete

    When *pw* is provided (a running Playwright instance), it is reused
    instead of spawning a new Playwright server process per monitor cycle.
    """
    board_id = str(board["id"])
    company_id = str(board["company_id"])
    board_url = board["board_url"]
    crawler_type = board["crawler_type"]

    board_log = log.bind(board_id=board_id, board_url=board_url, crawler_type=crawler_type)
    t0 = monotonic()

    pw_owned = False  # True when we created pw ourselves and must stop it
    effective_http = http

    try:
        metadata = board["metadata"] if board["metadata"] else {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        enrich_fields = _board_has_enrich(metadata)

        # Use a per-board http client when the monitor opts out of SSL
        # verification or into the proxy provider. We reuse the shared
        # client otherwise.
        ssl_verify = metadata.get("ssl_verify", True)
        use_proxy = bool(metadata.get("proxy"))
        if not ssl_verify or use_proxy:
            from src.shared.http import create_http_client

            effective_http = create_http_client(verify=ssl_verify, use_proxy=use_proxy)

        # Start Playwright if this monitor needs a browser and none was provided
        if pw is None and _batch.monitor_needs_browser(crawler_type, metadata):
            try:
                from playwright.async_api import async_playwright

                pw_ctx = async_playwright()
                pw = await pw_ctx.start()
                pw_owned = True
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
        total_processed = 0
        total_new = 0
        total_relisted = 0
        batch_count = 0
        # Any truncated batch flips the cycle to "partial" and suppresses
        # gone-detection (#3216). The MAX_JOBS cap means the unseen tail
        # would otherwise be tombstoned by _MARK_GONE_BY_TIMESTAMP.
        any_truncated = False

        async for result in _batch.monitor_one_stream(
            board_url, crawler_type, metadata, effective_http, pw=pw
        ):
            batch_count += 1
            total_discovered += len(result.urls)
            is_rich = result.jobs_by_url is not None
            if getattr(result, "truncated", False):
                any_truncated = True

            # Pulse heartbeat + extend DB lease (shielded to avoid
            # destroying the pool connection on task cancellation)
            extender.pulse()
            with contextlib.suppress(Exception):
                await asyncio.shield(pool.execute(_EXTEND_BOARD_LEASE, board_id))

            # Drop implausible URLs (site roots, bare-hash variants) before
            # they reach _DIFF_BATCH. These otherwise collide on the
            # job_posting.source_url unique index every monitor cycle.
            #
            # Also canonicalize URLs that embed session-scoped query
            # params — the reverse problem, where every monitor cycle
            # produces a URL the uniqueness check treats as _new_ and
            # re-enqueues a duplicate scrape (see :func:`_canonicalize_url`
            # for platform coverage and the Pictet/SuccessFactors case).
            filtered_urls: list[str] = []
            drop_reasons: dict[str, int] = {}
            seen: set[str] = set()
            for raw in result.urls:
                u = _canonicalize_url(raw)
                reason = _classify_job_url(u, board_url)
                if reason is not None:
                    drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
                    continue
                # De-dup after canonicalization — without this, a single
                # monitor batch that emits two query-variants of the same
                # posting would collide inside _DIFF_BATCH.
                if u in seen:
                    continue
                seen.add(u)
                filtered_urls.append(u)
            for reason, count in drop_reasons.items():
                # ``board_id`` label added in #2704 so a noisy board is
                # attributable without grepping logs. ``board_id`` is the
                # primary key UUID, in scope from the enclosing function.
                monitor_url_filtered_total.labels(reason=reason, board_id=board_id).inc(count)
                board_log.info(
                    "batch.monitor.url_filtered",
                    reason=reason,
                    count=count,
                )

            if not filtered_urls:
                continue
            total_processed += len(filtered_urls)

            # Match rich-data keys against the canonicalized URL set so
            # a rich monitor targeting a canonicalized platform (none
            # today, but cheap to future-proof) doesn't silently drop
            # the per-posting data.
            filtered_jobs_by_url = (
                {
                    _canonicalize_url(u): v
                    for u, v in result.jobs_by_url.items()
                    if _canonicalize_url(u) in seen
                }
                if result.jobs_by_url is not None
                else None
            )

            # Sub-chunk large batches to keep _DIFF_BATCH within the
            # 60s asyncpg command_timeout (e.g. Amazon USA = 8,900 URLs).
            _DB_CHUNK = 500
            all_urls = filtered_urls
            all_new_urls: list[str] = []

            for _chunk_start in range(0, len(all_urls), _DB_CHUNK):
                chunk_urls = all_urls[_chunk_start : _chunk_start + _DB_CHUNK]
                chunk_jobs = (
                    {u: filtered_jobs_by_url[u] for u in chunk_urls if u in filtered_jobs_by_url}
                    if filtered_jobs_by_url is not None
                    else None
                )

                async with pool.acquire() as conn, conn.transaction():
                    # Persist monitor-signalled metadata updates once per batch.
                    # Merges both the sitemap URL (for monitors that discover it
                    # dynamically) and arbitrary metadata_updates (used by
                    # incremental monitors to persist a watermark / probe result)
                    # into a single JSONB shallow-merge call.
                    if _chunk_start == 0:
                        meta_patch: dict = {}
                        new_sitemap_url = getattr(result, "new_sitemap_url", None)
                        if new_sitemap_url:
                            meta_patch["sitemap_url"] = new_sitemap_url
                        metadata_updates = getattr(result, "metadata_updates", None)
                        if metadata_updates:
                            meta_patch.update(metadata_updates)
                        if meta_patch:
                            await conn.execute(
                                _UPDATE_METADATA,
                                board_id,
                                json.dumps(meta_patch),
                            )

                    is_rich_no_scrape = is_rich and not enrich_fields
                    rows = await conn.fetch(
                        _DIFF_BATCH,
                        chunk_urls,
                        board_id,
                        is_rich_no_scrape,
                    )

                    new_urls: list[str] = []
                    relisted: list[dict] = []
                    touched: list[dict] = []
                    n_foreign = 0

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
                        elif action == "foreign":
                            # Cross-tenant duplicate — the row is already
                            # owned by another board and _DIFF_BATCH has
                            # refreshed its last_seen_at. We don't insert
                            # and don't enqueue; just count the signal.
                            n_foreign += 1

                    if n_foreign:
                        monitor_dedup_total.labels(path="cross_board").inc(n_foreign)
                        board_log.info(
                            "batch.monitor.cross_board_duplicate",
                            count=n_foreign,
                        )

                    total_new += len(new_urls)
                    total_relisted += len(relisted)
                    all_new_urls.extend(new_urls)

                    # Hybrid monitors (eightfold) return a partial chunk_jobs
                    # dict — rich data only for some new URLs. Split new_urls
                    # so URLs with rich data go to the rich insert path and
                    # URLs without rich data fall through to the URL-only stub
                    # insert path (which enqueues a scrape to fill content).
                    # When chunk_jobs is None, stub_new_urls == new_urls and
                    # the behaviour is identical to pre-refactor.
                    if chunk_jobs is not None:
                        rich_new_urls = [u for u in new_urls if u in chunk_jobs]
                        stub_new_urls = [u for u in new_urls if u not in chunk_jobs]
                    else:
                        rich_new_urls = []
                        stub_new_urls = list(new_urls)

                    if chunk_jobs:
                        new_jobs = [chunk_jobs[u] for u in rich_new_urls]

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
                                            normalize_employment_type(
                                                _coerce_text(j.employment_type)
                                            ),
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

                            # Batch insert all new jobs. ON CONFLICT (source_url)
                            # DO NOTHING is a belt-and-braces safety net for
                            # the rare race where two workers running DIFF
                            # concurrently both classify the same URL as new
                            # (the bulk cross-tenant case is handled upstream
                            # in _DIFF_BATCH.foreign_touched). We must pair
                            # each r2_staging entry with its own insert
                            # outcome in the same pass — a trailing zip would
                            # silently misalign descriptions with posting ids.
                            insert_sql = (
                                _INSERT_RICH_JOB_ENRICH if enrich_fields else _INSERT_RICH_JOB
                            )
                            inserted_rich: list[tuple[object, object, str]] = []
                            n_rich_dedup = 0
                            for rec, (j, t_ids) in zip(records, r2_staging, strict=True):
                                row = await conn.fetchrow(insert_sql, *rec)
                                if row is None:
                                    n_rich_dedup += 1
                                    continue
                                new_posting_id = str(row["id"])
                                inserted_rich.append((j, t_ids, new_posting_id))
                                # Lifecycle anchor: per-posting discovery event so
                                # operators can grep Loki by posting_id from the
                                # URL to find when (and from which board) the row
                                # first entered the pipeline (#3192).
                                board_log.info(
                                    "posting.discovered",
                                    posting_id=new_posting_id,
                                    board_id=board_id,
                                    source_url=j.url,
                                    path="rich",
                                )
                            if n_rich_dedup:
                                monitor_dedup_total.labels(path="rich").inc(n_rich_dedup)
                                board_log.info(
                                    "batch.monitor.duplicate_source_url",
                                    path="rich",
                                    count=n_rich_dedup,
                                )

                            # Write descriptions for inserted jobs.
                            # Rich-monitor path uses a plain HTML-only hash
                            # (no extras, no metadata), so there's no legacy
                            # vs new-algo split — pass the same value for
                            # both hash params.
                            for j, _t_ids, posting_id in inserted_rich:
                                desc_html = _coerce_text(j.description)
                                if desc_html:
                                    locale = _coerce_text(j.language) or "en"
                                    _h = content_hash(desc_html)
                                    await conn.execute(
                                        _UPSERT_DESCRIPTION,
                                        posting_id,
                                        locale,
                                        desc_html,
                                        _h,
                                        _h,
                                    )

                            # Enqueue scrapes for rich jobs that need enrichment
                            if enrich_fields and inserted_rich:
                                rich_rows = [
                                    {"id": pid, "source_url": j.url}
                                    for j, _t_ids, pid in inserted_rich
                                ]
                                await _enqueue_scrapes_for_new(
                                    rich_rows,
                                    board_id,
                                    metadata,
                                    board_log,
                                    crawler_type=crawler_type,
                                )

                        # Update content for relisted and touched.
                        # Hybrid monitors (partial rich data) skip BOTH the
                        # touched AND relisted update paths because
                        # _BATCH_UPDATE_RICH_CONTENT uses plain SET (not
                        # COALESCE) for core fields — feeding partial rich
                        # data would null out previously-scraped fields
                        # (employment_type, salary_*, experience_*). Relisted
                        # jobs still get refreshed content via the enrich
                        # scrape path: _DIFF_BATCH resets ``next_scrape_at
                        # = now()`` for relisted when ``is_rich_no_scrape``
                        # is False, and the subsequent json-ld scrape fills
                        # missing fields via ``_UPDATE_ENRICH_CONTENT``
                        # (which DOES use COALESCE, so it's safe on partial
                        # data). See ``_enqueue_scrapes_for_relisted`` below.
                        if getattr(result, "hybrid", False):
                            update_triples = []
                        else:
                            update_triples = [
                                (item["id"], chunk_jobs[item["url"]], item.get("r2_hash"))
                                for item in relisted + touched
                                if item["url"] in chunk_jobs
                            ]
                        if update_triples:
                            for _, j, _ in update_triples:
                                j.description = normalize_description_html(j.description)
                                enrich_description(j)
                                if not j.language and j.description:
                                    j.language = detect_language(j.description)

                            # Resolve locations with the cache-only sync helper
                            # so the per-row loop doesn't await one DB round
                            # trip per miss (#3206). A single batched backfill
                            # runs after the loop, mirroring the NEW-jobs path
                            # above (board.py:893-963).
                            #
                            # Unlike that path (INSERT with NULL is fine, the
                            # next cycle fills it via touched-update),
                            # _BATCH_UPDATE_RICH_CONTENT uses plain SET (not
                            # COALESCE) for location_ids/location_types -- a
                            # None here would null out a previously-resolved
                            # value. So if backfill added new names, we re-
                            # resolve every row in a second sync pass (pure
                            # cache, zero DB round trips).
                            resolved: list[tuple[list[int] | None, list[str] | None]] = [
                                _resolve_locations_sync(
                                    loc_resolver,
                                    _coerce_locations(j.locations),
                                    _coerce_text(j.job_location_type),
                                    _coerce_text(j.language),
                                )
                                for _, j, _ in update_triples
                            ]

                            # Single DB round trip for any cache misses (rare).
                            if await loc_resolver.backfill_misses():
                                loc_resolver.drain_location_misses()
                                resolved = [
                                    _resolve_locations_sync(
                                        loc_resolver,
                                        _coerce_locations(j.locations),
                                        _coerce_text(j.job_location_type),
                                        _coerce_text(j.language),
                                    )
                                    for _, j, _ in update_triples
                                ]

                            await conn.execute(_CREATE_RICH_UPDATES_TEMP)
                            records = []
                            for (pid, j, _), (loc_ids, loc_types) in zip(
                                update_triples, resolved, strict=True
                            ):
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
                            # (rich-monitor path — see insert branch above
                            # for the $4/$5 "same hash twice" rationale).
                            for pid, j, _existing_hash in update_triples:
                                desc_html = _coerce_text(j.description)
                                if desc_html:
                                    locale = _coerce_text(j.language) or "en"
                                    _h = content_hash(desc_html)
                                    await conn.execute(
                                        _UPSERT_DESCRIPTION,
                                        str(pid),
                                        locale,
                                        desc_html,
                                        _h,
                                        _h,
                                    )

                    # URL-only path -- insert stubs with next_scrape_at.
                    # Runs when chunk_jobs is None (traditional URL-only
                    # monitor) OR when it's a partial dict and has new URLs
                    # without rich data (hybrid monitors like eightfold).
                    if stub_new_urls:
                        # If a rich-monitor board falls into this path (e.g. an
                        # API fallback that returns URLs only for a cycle), the
                        # runtime ``is_rich_no_scrape`` flag is False because
                        # ``is_rich`` is False. We need the metadata-level
                        # classifier to catch it too — otherwise ``next_scrape_at``
                        # gets set to now() and the posting re-enters the skip-
                        # scraper loop.
                        never_scrape = is_rich_no_scrape or _is_skip_no_scrape(
                            metadata, crawler_type
                        )
                        inserted = await conn.fetch(
                            _INSERT_URL_ONLY_JOBS,
                            company_id,
                            board_id,
                            stub_new_urls,
                            never_scrape,
                        )
                        # _INSERT_URL_ONLY_JOBS uses ON CONFLICT (source_url)
                        # DO NOTHING — some rows may silently no-op when the
                        # same URL is already owned by another board.
                        n_deduped = len(new_urls) - len(inserted)
                        if n_deduped:
                            monitor_dedup_total.labels(path="url_only").inc(n_deduped)
                            board_log.info(
                                "batch.monitor.duplicate_source_url",
                                path="url_only",
                                count=n_deduped,
                            )
                        board_log.info("batch.inserted_for_scrape", count=len(inserted))
                        # Lifecycle anchor: per-posting discovery event so an
                        # operator with only the posting_id (from the public
                        # URL) can grep Loki to find when and from which board
                        # the row entered the pipeline (#3192). URL-only path
                        # — rich data arrives later via the scrape branch's
                        # ``posting.scraped`` event.
                        for ins in inserted:
                            board_log.info(
                                "posting.discovered",
                                posting_id=str(ins["id"]),
                                board_id=board_id,
                                source_url=ins["source_url"],
                                path="url_only",
                            )
                        await _enqueue_scrapes_for_new(
                            inserted, board_id, metadata, board_log, crawler_type=crawler_type
                        )

                    # Enqueue scrapes for relisted jobs (came back after gone)
                    # Skip for rich monitors without enrichment — they already have full data
                    if not is_rich_no_scrape:
                        await _enqueue_scrapes_for_relisted(
                            relisted,
                            board_id,
                            metadata,
                            board_log,
                            crawler_type=crawler_type,
                        )

            board_log.info(
                "batch.monitor.stream_batch",
                batch=batch_count,
                discovered=len(result.urls),
                new=len(all_new_urls),
            )

        # After all batches: mark gone postings
        if total_processed == 0:
            # Nothing reached _DIFF_BATCH — either the monitor yielded 0 URLs
            # or every discovered URL was filtered out as implausible. Treat
            # both as an empty check so we don't mark every active posting
            # as gone based on a garbage-only run.
            elapsed = monotonic() - t0
            board_log.warning(
                "batch.monitor.empty",
                duration_s=round(elapsed, 2),
                raw_discovered=total_discovered,
            )
            # _RECORD_EMPTY_CHECK + delist must be atomic: if the
            # record commits but the delist fails we'd be back to the
            # exact phantom-active orphan state this PR fixes.
            empty_gone_count = 0
            try:
                async with pool.acquire() as conn, conn.transaction():
                    rows = await conn.fetch(_RECORD_EMPTY_CHECK, board_id)
                    if rows and rows[0]["board_status"] == "gone":
                        empty_gone_count = await _delist_board_postings(conn, board_id)
            except (asyncpg.PostgresError, ConnectionError):
                board_log.exception("batch.monitor.empty_check_failed")
            else:
                # Only emit the metric AFTER the transaction commits —
                # see ``_delist_board_postings`` docstring.
                if empty_gone_count:
                    _emit_gone_counter(empty_gone_count)
                    board_log.warning("batch.monitor.board_gone", gone=empty_gone_count)
                    with contextlib.suppress(Exception):
                        await _batch.get_redis().delete("cache:platform-stats")
            return True, elapsed

        # Mark as gone any active posting not seen during this monitor run.
        # Per-board override (#2725): ``metadata.delist_threshold`` lets
        # operators raise/lower the miss count needed to tombstone a posting
        # for boards that flap (NHS pagination) or that we want stricter
        # (a known-stable greenhouse override could go to 1).
        # Wrapped in resilience guards (#2723 drop, #2724 blast-radius) so a
        # silently-truncated paginating monitor (#2722) cannot mass-delist
        # live postings — see ``_mark_gone_with_guards``.
        gone_count = 0
        gone_skipped_reason: str | None = None
        delist_threshold = _resolve_delist_threshold(metadata, crawler_type)
        # Truncation override (#3216) — when the monitor returned a partial
        # discovery (any batch hit its MAX_JOBS cap), skip gone-detection
        # entirely for this cycle. The 30% drop guard catches catastrophic
        # under-counts but not smaller truncations: a board with 60k jobs
        # capped at 50k still reports 50k discovered, well within tolerance,
        # so the next ``_MARK_GONE_BY_TIMESTAMP`` would tombstone the 10k
        # unseen tail. The run is still recorded as success so the failure
        # budget doesn't escalate on a working-but-large board; the next
        # cycle proceeds normally.
        if any_truncated:
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute(_RECORD_SUCCESS_NONEMPTY, board_id)
            board_log.warning(
                "batch.monitor.truncated_partial",
                discovered=total_discovered,
                note="MAX_JOBS cap hit; suppressing gone-detection this cycle",
            )
            monitor_truncated_total.labels(board_id=board_id).inc()
        else:
            async with pool.acquire() as conn, conn.transaction():
                gone_count, gone_skipped_reason = await _mark_gone_with_guards(
                    conn,
                    board_id,
                    total_discovered,
                    monitor_start_ts,
                    metadata,
                    delist_threshold,
                    board_log,
                )
                await conn.execute(_RECORD_SUCCESS_NONEMPTY, board_id)

            # Emit the skip metric AFTER the transaction commits — same
            # pattern as ``_emit_gone_counter`` (a rollback would otherwise
            # over-report skipped cycles).
            if gone_skipped_reason:
                monitor_gone_skipped_total.labels(reason=gone_skipped_reason).inc()

        # Flush location misses to taxonomy_miss table
        await _batch._flush_location_misses(loc_resolver, pool)

        elapsed = monotonic() - t0
        board_log.info(
            "batch.monitor.success",
            discovered=total_discovered,
            processed=total_processed,
            new=total_new,
            relisted=total_relisted,
            gone=gone_count,
            gone_skipped_reason=gone_skipped_reason,
            truncated=any_truncated,
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

    except TDMReservedError as exc:
        # Publisher emitted the W3C TDM-Reservation opt-out signal (#2842).
        # Treat the run as a clean skip — log + counter increment, no
        # tombstoning, no consecutive_failures bump, no _RECORD_FAILURE
        # ramp. Distinct from the failure path because the upstream
        # technically responded successfully; what they declined is
        # text-and-data-mining, not the request itself.
        elapsed = monotonic() - t0
        board_log.info(
            "batch.monitor.tdm_reserved",
            url=getattr(exc, "url", None),
            source=getattr(exc, "source", None),
            tdm_policy_url=getattr(exc, "policy_url", None),
            duration_s=round(elapsed, 2),
        )
        monitor_skipped_tdm_total.labels(
            board_id=board_id,
            source=getattr(exc, "source", "unknown"),
        ).inc()
        tasks_total.labels(kind="monitor", status="tdm_reserved").inc()
        # Discard stale location misses from this skipped board. Mirrors
        # the cleanup the failure path does.
        loc_resolver.drain_location_misses()
        # Return success-shaped: the run was not a failure.
        return True, elapsed

    except BoardGoneError as exc:
        # Upstream confirmed the board no longer exists (404 from
        # greenhouse/lever/recruitee/ashby per-board API). Skip the
        # 5-strike `_RECORD_FAILURE` ramp and disable in one shot —
        # otherwise the Redis monitor task keeps re-firing the dead
        # endpoint every cycle until sync removes it (which only runs
        # on CSV pushes). See issue #2215.
        elapsed = monotonic() - t0
        board_log.warning(
            "batch.monitor.board_gone",
            error=str(exc),
            url=getattr(exc, "url", None),
            duration_s=round(elapsed, 2),
        )
        tasks_total.labels(kind="monitor", status="board_gone").inc()
        loc_resolver.drain_location_misses()
        board_gone_count = 0
        try:
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute(_RECORD_BOARD_GONE, board_id)
                board_gone_count = await _delist_board_postings(conn, board_id)
        except (asyncpg.PostgresError, ConnectionError):
            board_log.exception("batch.monitor.board_gone_record_failed")
        else:
            if board_gone_count:
                _emit_gone_counter(board_gone_count)
                with contextlib.suppress(Exception):
                    await _batch.get_redis().delete("cache:platform-stats")
        # Re-raise so the worker can drop the Redis task instead of
        # rescheduling — otherwise the dead board keeps cycling.
        raise

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        board_log.exception("batch.monitor.error", error=error_msg, duration_s=round(elapsed, 2))
        tasks_total.labels(kind="monitor", status="failed").inc()
        # Discard stale location misses from this failed board
        loc_resolver.drain_location_misses()
        # A 5-strike failure flips ``is_enabled=false`` + ``board_status='disabled'``.
        # _FETCH_DUE_BOARDS won't pick the board again, so active postings
        # would otherwise sit orphaned (``is_active=true``, never to be
        # refreshed or marked gone). Detect the transition (the pre-fetch
        # row was ``is_enabled=true``, so any post-update ``false`` is a
        # fresh disable) and gate the delist on ``last_success_at`` so a
        # provider outage doesn't mass-tombstone postings that would all
        # come back on recovery.
        failure_gone_count = 0
        try:
            async with pool.acquire() as conn, conn.transaction():
                row = await conn.fetchrow(_RECORD_FAILURE, board_id, error_msg)
                just_disabled = row is not None and not row["is_enabled"]
                if just_disabled:
                    failure_gone_count = await _maybe_delist_after_disable(
                        conn, board_id, row["last_success_at"], board_log
                    )
                    if failure_gone_count:
                        board_log.warning(
                            "batch.monitor.five_strike_disable",
                            gone=failure_gone_count,
                        )
        except (asyncpg.PostgresError, ConnectionError):
            board_log.exception("batch.monitor.record_failure_failed")
        else:
            if failure_gone_count:
                _emit_gone_counter(failure_gone_count)
                with contextlib.suppress(Exception):
                    await _batch.get_redis().delete("cache:platform-stats")
        return False, elapsed
    finally:
        if pw and pw_owned:
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
    pcsx_force_full_crawl: bool = False,
) -> None:
    """Dry-run a single board: monitor + scrape without any DB writes.

    Runs monitor_one() to discover jobs, then scrape_one() on a sample of URLs
    to show what the scraper would produce.  Useful for testing config changes.

    When *pw* is provided, Playwright is available for monitors/scrapers that
    require browser rendering (e.g. replay-mode api_sniffer, rendered nextdata).

    When *pcsx_force_full_crawl* is True, the eightfold hybrid monitor forces
    a full PCSX crawl regardless of its watermark state. Used for manual
    backfills of large boards (Starbucks) before enabling incremental mode.
    """
    from dataclasses import fields as dc_fields

    board = await pool.fetchrow(_FETCH_BOARD_BY_SLUG, board_slug)
    if not board:
        log.error("dry_run.not_found", board_slug=board_slug)
        return
    crawler_type = board["crawler_type"]
    metadata = _parse_metadata(board["metadata"])
    if pcsx_force_full_crawl:
        metadata = {**metadata, "pcsx_force_full_crawl": True}
    enrich_fields = _board_has_enrich(metadata)

    log.info(
        "dry_run.start",
        board_slug=board_slug,
        crawler_type=crawler_type,
        enrich=enrich_fields or "(none)",
    )

    # -- Monitor --
    # Catch failures (e.g. ApiSnifferFallbackError from a broken sniffer) so
    # `crawler board <slug> --dry-run` reports a clean log line instead of
    # exiting with an unhandled traceback that noises up agent troubleshooting.
    try:
        result = await _batch.monitor_one(board["board_url"], crawler_type, metadata, http, pw=pw)
    except Exception as exc:
        log.error(
            "dry_run.monitor.failed",
            board_slug=board_slug,
            error=_error_message(exc),
            exc_info=True,
        )
        return

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
    pcsx_force_full_crawl: bool = False,
) -> None:
    """Process a single board end-to-end: monitor then scrape.

    Bypasses scheduling -- fetches the board directly by slug and processes
    all due scrape items for that board after the monitor run.
    When *force_rescrape* is True, scrapes all active jobs regardless of schedule.
    When *pcsx_force_full_crawl* is True, the eightfold hybrid monitor forces
    a full PCSX crawl regardless of its watermark state.
    """
    board = await pool.fetchrow(_FETCH_BOARD_BY_SLUG, board_slug)
    if not board:
        log.error("single_board.not_found", board_slug=board_slug)
        return

    # asyncpg.Record is immutable — rebuild as a dict so we can inject
    # the CLI override into the monitor metadata for this run only.
    board = dict(board)
    if pcsx_force_full_crawl:
        md = _parse_metadata(board["metadata"])
        md["pcsx_force_full_crawl"] = True
        board["metadata"] = json.dumps(md)

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
