"""Board processing — monitor cycles, streaming, dry-run, single-board."""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
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
    monitor_db_transaction_retries_total,
    monitor_dedup_total,
    monitor_foreign_discovery_total,
    monitor_gone_events_total,
    monitor_gone_skipped_total,
    monitor_jobs_discovered,
    monitor_quarantine_events_total,
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
from src.processing.gone_policy import evaluate_gone_confirmation
from src.processing.scrape import (
    _UPSERT_DESCRIPTION,
    ScrapeItem,
    _apply_defaults,
    _effective_board_enrich,
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
    _FETCH_BOARD_GONE_STATE,
    _FETCH_DUE_BOARDS,
    _INSERT_RICH_JOB,
    _INSERT_RICH_JOB_ENRICH,
    _INSERT_URL_ONLY_JOBS,
    _MARK_GONE_BY_TIMESTAMP,
    _RECORD_BOARD_GONE,
    _RECORD_EMPTY_CHECK,
    _RECORD_FAILURE,
    _RECORD_SUCCESS_NONEMPTY,
    _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES,
    _UPDATE_METADATA,
)
from src.queries.scrape import (
    _FETCH_BOARD_ALL_ACTIVE,
    _FETCH_BOARD_BY_SLUG,
    _FETCH_BOARD_SCRAPE_ITEMS,
)
from src.redis_queue import enqueue_scrape as _enqueue_scrape
from src.runtime.extraction import MonitorRuntime, PythonMonitorRuntime
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

_MERCK_IDENTITY_MIGRATION = "merck-phenom-stable-id-v1"
_MERCK_IDENTITY_MIGRATION_VERSION = 1
_ECOM_IDENTITY_MIGRATION = "ecom-teamtailor-stable-id-v1"
_ECOM_IDENTITY_MIGRATION_VERSION = 1
_IDENTITY_MIGRATION_RECEIPT_KEY = "_identity_migration_receipt"
_MERCK_IDENTITY_MIGRATION_CONTRACTS = {
    "merck-br-pt": (
        "https://careers.merckgroup.com/br/pt/search-results",
        "sitemap",
        "f51f3576f00babffbae97b9f4f05789c4ecfd805f98c292f942726618e238ed5",
    ),
    "merck-cn-zh": (
        "https://careers.merckgroup.com/cn/zh/search-results",
        "sitemap",
        "ac58ba6af909b1b5a114fb9b8cd851c7be692c53c7aff5a7c5f979564a22293a",
    ),
    "merck-de-de": (
        "https://careers.merckgroup.com/de/de/search-results",
        "sitemap",
        "deb59407fc098e145c3b40c401fcfe52c7b1af62ac76133c61bc9d0476bdafcf",
    ),
    "merck-es-es": (
        "https://careers.merckgroup.com/es/es/search-results",
        "sitemap",
        "f12e78673e9e63cbd29b8597bd06fcda2bb9e08fdbb6c20f7dad637df9fb6cd0",
    ),
    "merck-fr-fr": (
        "https://careers.merckgroup.com/fr/fr/search-results",
        "sitemap",
        "e303762dc3bd34383d43012183e23f54448a7383604e39ca90514299b30595a1",
    ),
    "merck-global-en": (
        "https://careers.merckgroup.com/global/en/search-results",
        "sitemap",
        "ecdc9747101db5d3b9fe53a3fa86ae3793d43ab6d664f48b4c84e73771c69e13",
    ),
    "merck-it-it": (
        "https://careers.merckgroup.com/it/it/search-results",
        "sitemap",
        "aadff6acd244a07bd21c5ad7437002f94d8f6d2fe5a37572b8f89bd044395c20",
    ),
    "merck-jp-ja": (
        "https://careers.merckgroup.com/jp/ja/search-results",
        "sitemap",
        "81ff3b5be322c5ef7aa943fc9d46b6767004bed4a220bf09f77c0ee5c6fccd02",
    ),
    "merck-kr-ko": (
        "https://careers.merckgroup.com/kr/ko/search-results",
        "sitemap",
        "49238743ffd8f50743921e4ee0ae72fe3c99c640cecff13ab9108341e818fa00",
    ),
    "merck-tw-zh": (
        "https://careers.merckgroup.com/tw/zh/search-results",
        "sitemap",
        "e0c8c6cf184dc1a4d653321b0cec671db6adb202999666ae2cc88bc3bff3d4f6",
    ),
}
_MERCK_LEGACY_URL_PATTERN = (
    r"^https://careers[.]merckgroup[.]com/"
    r"(br/pt|cn/zh|de/de|es/es|fr/fr|global/en|it/it|jp/ja|kr/ko|tw/zh)/"
    r"job/([0-9]+)(/[^/?#]*)?$"
)
_MERCK_CANONICAL_URL_PATTERN = r"^https://careers[.]emdgroup[.]com/us/en/job/[0-9]+$"
_ECOM_IDENTITY_MIGRATION_CONTRACT = (
    "ecom-agroindustrial-global",
    "https://ecomtradinggroup.teamtailor.com/jobs",
    "rss",
    "f8b18c8f6ec72fe6fd48e29e6aaca9666f3742d5baec41b191dc07c961adeb52",
)
_ECOM_CURRENT_HOST_PATTERN = (
    r"(careerslatam[.]ecomtrading[.]com|"
    r"careerswestafrica[.]ecomtrading[.]com|"
    r"careersasiapacific[.]ecomtrading[.]com|"
    r"careersbrazil[.]ecomtrading[.]com|"
    r"careersmexico[.]ecomtrading[.]com|"
    r"ecomeurope[.]teamtailor[.]com)"
)
_ECOM_OLD_EUROPE_HOST_PATTERN = r"careerseurope[.]ecomtrading[.]com"
_ECOM_ALL_SOURCE_HOST_PATTERN = (
    _ECOM_CURRENT_HOST_PATTERN[:-1] + "|" + _ECOM_OLD_EUROPE_HOST_PATTERN + ")"
)
_ECOM_LEGACY_URL_PATTERN = (
    rf"^https://{_ECOM_ALL_SOURCE_HOST_PATTERN}/((de|fr|it|en)/)?"
    r"jobs/[0-9]+-[^/?#]+$|"
    rf"^https://{_ECOM_ALL_SOURCE_HOST_PATTERN}/(de|fr|it|en)/jobs/[0-9]+$|"
    rf"^https://{_ECOM_OLD_EUROPE_HOST_PATTERN}/jobs/[0-9]+$"
)
_ECOM_CANONICAL_URL_PATTERN = rf"^https://{_ECOM_CURRENT_HOST_PATTERN}/jobs/[0-9]+$"
_MERCK_IDENTITY_MIGRATION_MAX_ROWS = 2_000
_ECOM_IDENTITY_MIGRATION_MAX_ROWS = 100
# Kept as the public hard-cap alias used by the Merck safety tests.
_IDENTITY_MIGRATION_MAX_ROWS = _MERCK_IDENTITY_MIGRATION_MAX_ROWS
_SUPPORTED_IDENTITY_MIGRATIONS = frozenset({_MERCK_IDENTITY_MIGRATION, _ECOM_IDENTITY_MIGRATION})


def _identity_migration_canonical_url_pattern(migration: object) -> str | None:
    if migration == _MERCK_IDENTITY_MIGRATION:
        return _MERCK_CANONICAL_URL_PATTERN
    if migration == _ECOM_IDENTITY_MIGRATION:
        return _ECOM_CANONICAL_URL_PATTERN
    return None


def _identity_migration_receipt_matches(
    receipt: object,
    *,
    migration: str,
    migration_version: int,
    expected_fingerprint: str,
    max_rows: int,
) -> bool:
    expected_keys = {
        "id",
        "version",
        "config_fingerprint",
        "completed_at",
        "retired_count",
    }
    if migration == _ECOM_IDENTITY_MIGRATION:
        expected_keys.add("rollback_rows")
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        return False
    retired_count = receipt.get("retired_count")
    base_matches = (
        receipt.get("id") == migration
        and receipt.get("version") == migration_version
        and receipt.get("config_fingerprint") == expected_fingerprint
        and isinstance(receipt.get("completed_at"), str)
        and bool(receipt.get("completed_at"))
        and isinstance(retired_count, int)
        and not isinstance(retired_count, bool)
        and 0 <= retired_count <= max_rows
    )
    if not base_matches or migration != _ECOM_IDENTITY_MIGRATION:
        return base_matches
    rollback_rows = receipt.get("rollback_rows")
    if not isinstance(rollback_rows, list) or len(rollback_rows) > max_rows:
        return False
    return all(
        isinstance(row, dict)
        and set(row) == {"id", "source_url", "is_active", "missing_count", "next_scrape_at"}
        and isinstance(row.get("id"), str)
        and bool(row.get("id"))
        and isinstance(row.get("source_url"), str)
        and bool(row.get("source_url"))
        and isinstance(row.get("is_active"), bool)
        and isinstance(row.get("missing_count"), int)
        and not isinstance(row.get("missing_count"), bool)
        and (row.get("next_scrape_at") is None or isinstance(row.get("next_scrape_at"), str))
        for row in rollback_rows
    )


# API monitor types share a single API host per type (throttle-domain keys).
_API_MONITOR_TYPES = api_monitor_types()

# Max R2 backfill uploads per board run (touched postings without hashes).
# Prevents huge first-time runs from timing out. Backfill completes incrementally.
_SLOW_MONITOR_SECONDS = 30.0
_DIFF_TRANSACTION_MAX_ATTEMPTS = 3
_DIFF_TRANSACTION_RETRY_BASE_SECONDS = 0.05


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


# Overwolf's Comeet ``url_active_page`` has used both a marketing-source
# parameter and a numeric cache-busting timestamp for the same posting UID.
# The values are not identity, but keeping them made ``source_url`` uniqueness
# insert the same opportunity again on later monitor cycles (issue #5807).
_OVERWOLF_VOLATILE_PARAMS = frozenset({"src"})


def _canonicalize_url(url: str) -> str:
    """Strip session-scoped tokens from URLs on platforms where a
    ``<a href>`` embeds a per-render CSRF/session value that otherwise
    makes every monitor cycle rediscover the same posting as "new".

    Currently handles three known URL shapes:

    - **SuccessFactors family** (``*.successfactors.*`` / ``*.sapsf.*``)
      — token is a *query param*; drop the ones listed in
      :data:`_SUCCESSFACTORS_VOLATILE_PARAMS`. Identity-carrying params
      (``career_job_req_id``, ``company``, ``rcm_site_locale`` …) stay.
    - **tal.net / TalentLink** (``*.tal.net``) — token is a *path*
      segment matching :data:`_TAL_NET_XF_SEGMENT` (``/xf-<hex>/``);
      drop it. Everything else in the path (``/brand-N/``,
      ``/opp/<id>``, ``/en-GB``, …) carries identity and stays.
    - **Overwolf's Comeet active pages** (``careers.overwolf.com``) —
      drop the confirmed marketing ``src`` parameter and numeric ``t``
      cache-buster while preserving every other query parameter.
    """
    if not url:
        return url
    try:
        p = urlparse(url)
    except ValueError:
        return url
    host = (p.netloc or "").lower()
    if host == "careers.overwolf.com":
        params = parse_qsl(p.query, keep_blank_values=True)
        kept = [
            (key, value)
            for key, value in params
            if key.casefold() not in _OVERWOLF_VOLATILE_PARAMS
            and not (key.casefold() == "t" and value.isdecimal())
        ]
        if len(kept) == len(params):
            return url
        return urlunparse(p._replace(query=urlencode(kept)))
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

    Used by the two silent-delist paths that bypass the normal
    ``_MARK_GONE_BY_TIMESTAMP`` flow: empty-check threshold reached,
    and BoardGoneError (upstream 404).
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


def _emit_board_recovery(
    recovered_from: str | None,
    board_log: structlog.stdlib.BoundLogger,
    *,
    discovered: int,
) -> None:
    """Emit the bounded lifecycle metric only after recovery commits."""

    if recovered_from == "quarantined":
        monitor_quarantine_events_total.labels(event="recovered").inc()
        board_log.info("batch.monitor.quarantine_recovered", discovered=discovered)
    elif recovered_from == "provider_gone":
        monitor_gone_events_total.labels(event="recovered").inc()
        board_log.info("batch.monitor.gone_recovered", discovered=discovered)


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


async def _retire_canonicalized_provider_identities(
    conn: asyncpg.Connection,
    *,
    board_id: str,
    company_id: str,
    board_slug: str | None,
    board_url: str,
    crawler_type: str,
    monitor_start_ts,
    metadata: dict | None,
    discovered: int,
    canonical_urls: set[str],
    truncated: bool,
    extraction_filtered: int,
    security_filtered: int,
    processing_filtered: int,
    all_canonical: bool,
    board_log: structlog.stdlib.BoundLogger,
) -> int:
    """Run a code-owned, receipt-backed provider identity migration.

    Eligibility is bound to a code-owned board URL/type/config fingerprint,
    healthy complete discovery, and an ordinary rolling-count drop check. The
    SQL additionally binds the owning company to the code-owned company slug,
    classifies every
    active board-owned source URL, and independently requires every canonical
    URL discovered this cycle to exist as an active same-company row touched
    during this cycle. It then retires all strict legacy rows, including stale
    rows whose jobs are no longer in the current discovery.

    The caller runs this inside the ordinary board-success transaction before
    :func:`_mark_gone_with_guards`. Retired duplicates no longer inflate that
    guard's active/missing ratio, while every unrelated active row remains
    protected by the unchanged global guard logic. Retirement and its durable
    receipt are one transaction. An exact receipt makes every replay a
    permanent no-op; any mismatched receipt fails closed.
    """
    md = metadata or {}
    migration = md.get("identity_migration")
    if migration not in _SUPPORTED_IDENTITY_MIGRATIONS:
        return 0

    if migration == _MERCK_IDENTITY_MIGRATION:
        contract = _MERCK_IDENTITY_MIGRATION_CONTRACTS.get(board_slug or "")
        migration_version = _MERCK_IDENTITY_MIGRATION_VERSION
        company_slug = "merck"
        legacy_url_pattern = _MERCK_LEGACY_URL_PATTERN
        canonical_url_pattern = _MERCK_CANONICAL_URL_PATTERN
        max_rows = _MERCK_IDENTITY_MIGRATION_MAX_ROWS
    else:
        ecom_slug, ecom_url, ecom_type, ecom_fingerprint = _ECOM_IDENTITY_MIGRATION_CONTRACT
        contract = (ecom_url, ecom_type, ecom_fingerprint) if board_slug == ecom_slug else None
        migration_version = _ECOM_IDENTITY_MIGRATION_VERSION
        company_slug = "ecom-agroindustrial"
        legacy_url_pattern = _ECOM_LEGACY_URL_PATTERN
        canonical_url_pattern = _ECOM_CANONICAL_URL_PATTERN
        max_rows = _ECOM_IDENTITY_MIGRATION_MAX_ROWS

    fingerprint = md.get("_monitor_config_fingerprint")
    if contract is None or (board_url, crawler_type, fingerprint) != contract:
        board_log.warning(
            "batch.monitor.identity_migration_contract_mismatch",
            migration=migration,
            board_slug=board_slug,
        )
        return 0

    expected_fingerprint = contract[2]

    configured_receipt = md.get(_IDENTITY_MIGRATION_RECEIPT_KEY)
    if configured_receipt is not None:
        if _identity_migration_receipt_matches(
            configured_receipt,
            migration=migration,
            migration_version=migration_version,
            expected_fingerprint=expected_fingerprint,
            max_rows=max_rows,
        ):
            return 0
        board_log.warning(
            "batch.monitor.identity_migration_receipt_mismatch",
            migration=migration,
            source="board_metadata",
        )
        return 0
    if migration == _ECOM_IDENTITY_MIGRATION:
        # ECOM rollback must restore aliases that existed before canonical
        # rows were inserted. Its recovery lane therefore runs revision 0022
        # before discovery; the post-discovery Merck retirement query cannot
        # safely manufacture an equivalent receipt.
        board_log.warning("batch.monitor.ecom_identity_receipt_missing_before_discovery")
        return 0

    history = list(md.get("recent_discovered_counts") or [])
    exact_canonical_urls = sorted(set(canonical_urls))
    precondition_reason: str | None = None
    if discovered <= 0:
        precondition_reason = "empty"
    elif truncated:
        precondition_reason = "truncated"
    elif security_filtered:
        precondition_reason = "security_filtered"
    elif processing_filtered:
        precondition_reason = "processing_filtered"
    elif not all_canonical:
        precondition_reason = "noncanonical_output"
    elif not exact_canonical_urls:
        precondition_reason = "empty_canonical_set"
    elif discovered != len(exact_canonical_urls):
        precondition_reason = "nonunique_canonical_output"
    elif len(exact_canonical_urls) > max_rows:
        precondition_reason = "canonical_set_over_cap"
    elif len(history) < _DROP_GUARD_MIN_HISTORY:
        precondition_reason = "insufficient_history"
    else:
        from statistics import median

        expected = median(history)
        drop_threshold = _setting(md, "drop_threshold", _DROP_GUARD_THRESHOLD_DEFAULT)
        if expected <= 0 or discovered < expected * (1.0 - drop_threshold):
            precondition_reason = "drop"

    if precondition_reason is not None:
        board_log.warning(
            "batch.monitor.identity_migration_precondition_failed",
            migration=migration,
            reason=precondition_reason,
            discovered=discovered,
            history=history,
        )
        return 0

    base_receipt = {
        "id": migration,
        "version": migration_version,
        "config_fingerprint": expected_fingerprint,
    }

    row = await conn.fetchrow(
        _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES,
        board_id,
        company_id,
        monitor_start_ts,
        max_rows,
        exact_canonical_urls,
        legacy_url_pattern,
        canonical_url_pattern,
        json.dumps(base_receipt),
        company_slug,
    )
    if row is None:
        board_log.warning("batch.monitor.identity_migration_no_result")
        return 0

    existing_receipt = row["existing_receipt"]
    if isinstance(existing_receipt, str):
        existing_receipt = json.loads(existing_receipt)
    if existing_receipt is not None:
        if _identity_migration_receipt_matches(
            existing_receipt,
            migration=migration,
            migration_version=migration_version,
            expected_fingerprint=expected_fingerprint,
            max_rows=max_rows,
        ):
            return 0
        board_log.warning(
            "batch.monitor.identity_migration_receipt_mismatch",
            migration=migration,
            source="database",
        )
        return 0

    legacy = int(row["legacy"])
    unknown = int(row["unknown"])
    discovered_count = int(row["discovered"])
    validated_count = int(row["validated"])
    retired = int(row["retired"])
    receipt_written = bool(row["receipt_written"])
    if not receipt_written:
        board_log.warning(
            "batch.monitor.identity_migration_blocked",
            migration=migration,
            active=int(row["active"]),
            legacy=legacy,
            canonical=int(row["canonical"]),
            unknown=unknown,
            discovered=discovered_count,
            validated=validated_count,
            cap=max_rows,
        )
        return 0
    if (
        unknown
        or discovered_count != len(exact_canonical_urls)
        or validated_count != discovered_count
        or retired != legacy
    ):
        raise RuntimeError(
            "identity migration receipt was written without exact retirement "
            f"(legacy={legacy}, unknown={unknown}, discovered={discovered_count}, "
            f"validated={validated_count}, retired={retired})"
        )
    board_log.info(
        "batch.monitor.identity_migration_completed",
        migration=migration,
        retired=retired,
    )
    return retired


async def _ensure_ecom_identity_cutover_receipt(
    pool: asyncpg.Pool,
    *,
    board_id: str,
    company_id: str,
    board_slug: str | None,
    board_url: str,
    crawler_type: str,
    metadata: dict,
    board_log: structlog.stdlib.BoundLogger,
) -> dict:
    """Create ECOM's reversible receipt before canonical URLs can be inserted.

    Deploy normally runs revision 0022 while writers are quiesced and reapplies
    it after config sync. This is the worker-side recovery lane for a board that
    becomes due between those steps or after a repaired database restore. It
    reuses the exact bounded revision SQL before discovery, then requires the
    full rollback receipt before allowing the monitor to continue.
    """
    if metadata.get("identity_migration") != _ECOM_IDENTITY_MIGRATION:
        return metadata

    expected_slug, expected_url, expected_type, expected_fingerprint = (
        _ECOM_IDENTITY_MIGRATION_CONTRACT
    )
    if (
        board_slug,
        board_url,
        crawler_type,
        metadata.get("_monitor_config_fingerprint"),
    ) != (expected_slug, expected_url, expected_type, expected_fingerprint):
        board_log.warning("batch.monitor.ecom_identity_recovery_contract_mismatch")
        raise RuntimeError("ECOM identity recovery found a mismatched board contract")

    receipt = metadata.get(_IDENTITY_MIGRATION_RECEIPT_KEY)
    if receipt is not None:
        if _identity_migration_receipt_matches(
            receipt,
            migration=_ECOM_IDENTITY_MIGRATION,
            migration_version=_ECOM_IDENTITY_MIGRATION_VERSION,
            expected_fingerprint=expected_fingerprint,
            max_rows=_ECOM_IDENTITY_MIGRATION_MAX_ROWS,
        ):
            return metadata
        raise RuntimeError("ECOM identity recovery found a mismatched rollback receipt")

    from src.ecom_teamtailor_cutover import apply_ecom_teamtailor_cutover

    async with pool.acquire() as conn, conn.transaction():
        await apply_ecom_teamtailor_cutover(conn)
        refreshed = await conn.fetchval(
            "SELECT metadata FROM job_board WHERE id = $1 AND company_id = $2",
            board_id,
            company_id,
        )
    if isinstance(refreshed, str):
        refreshed = json.loads(refreshed)
    if not isinstance(refreshed, dict) or not _identity_migration_receipt_matches(
        refreshed.get(_IDENTITY_MIGRATION_RECEIPT_KEY),
        migration=_ECOM_IDENTITY_MIGRATION,
        migration_version=_ECOM_IDENTITY_MIGRATION_VERSION,
        expected_fingerprint=expected_fingerprint,
        max_rows=_ECOM_IDENTITY_MIGRATION_MAX_ROWS,
    ):
        raise RuntimeError("ECOM identity recovery did not produce an exact rollback receipt")

    board_log.info("batch.monitor.ecom_identity_recovery_completed")
    return refreshed


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
        try:
            row = await conn.fetchrow(
                _COUNT_BOARD_ACTIVE_AND_MISSING,
                board_id,
                monitor_start_ts,
            )
        except asyncpg.QueryCanceledError:
            # Preserve safe board-cycle context when PostgreSQL cancels this
            # guard at statement_timeout. The transaction still fails closed;
            # this event only makes the next daily review attributable.
            board_log.warning(
                "batch.monitor.gone_guard_count_timeout",
                discovered=discovered,
                history_points=len(history),
                monitor_started_at=str(monitor_start_ts),
            )
            raise
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


async def _enqueue_scrapes_for_touched_missing(
    touched: list[dict],
    board_id: str,
    metadata: dict,
    board_log: structlog.stdlib.BoundLogger,
    *,
    crawler_type: str | None = None,
) -> None:
    """Re-enqueue touched rows that are still missing content and due."""
    rows = [r for r in touched if r.get("needs_scrape_enqueue") and r.get("r2_hash") is None]
    if not rows:
        return
    if _is_skip_no_scrape(metadata, crawler_type):
        board_log.debug(
            "batch.enqueue_scrape.skipped_rich",
            count=len(rows),
            reason="rich monitor, no enrich",
        )
        return
    scraper_type = metadata.get("scraper_type", "json-ld")
    scraper_config = metadata.get("scraper_config")
    if not isinstance(scraper_config, dict):
        scraper_config = None
    needs_browser = _scraper_needs_browser(scraper_type, scraper_config)
    for r in rows:
        url = r["url"]
        domain = urlparse(url).hostname or ""
        await _enqueue_scrape(
            domain,
            r["id"],
            0,
            {
                "source_url": url,
                "board_id": board_id,
                "description_r2_hash": "",
                "scrape_step": "0",
            },
            browser=needs_browser,
            first_time=True,
        )
    board_log.info(
        "batch.enqueued_scrapes",
        count=len(rows),
        touched_missing=True,
        first_time=True,
    )


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
    if crawler_type == "darwinbox":
        from src.shared.darwinbox import darwinbox_board_from_metadata, darwinbox_board_from_url

        metadata = board["metadata"] or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        configured = darwinbox_board_from_metadata(metadata) if isinstance(metadata, dict) else None
        resolved = configured or darwinbox_board_from_url(board["board_url"])
        if resolved is not None:
            return resolved.host
    if crawler_type == "avature":
        from src.shared.avature import avature_request_host

        metadata = board["metadata"] or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        if isinstance(metadata, dict):
            resolved_host = avature_request_host(board["board_url"], metadata)
            if resolved_host:
                return resolved_host
    if crawler_type == "pageup":
        return "careers.pageuppeople.com"
    if crawler_type in _API_MONITOR_TYPES:
        return crawler_type
    if crawler_type == "taleo":
        from src.shared.taleo import taleo_request_host

        metadata = board["metadata"] or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        if isinstance(metadata, dict):
            resolved_host = taleo_request_host(board["board_url"], metadata)
            if resolved_host:
                return resolved_host
    return urlparse(board["board_url"]).hostname or board["board_url"]


async def _fetch_diff_batch(
    pool: asyncpg.Pool,
    urls: list[str],
    board_id: str,
    is_rich_no_scrape: bool,
    board_log: structlog.stdlib.BoundLogger,
) -> list[asyncpg.Record]:
    """Classify one URL chunk with a bounded deadlock retry.

    ``_DIFF_BATCH`` is atomic and idempotent after rollback, so retrying this
    narrow transaction cannot duplicate an insert or Redis publication.  The
    SQL takes posting locks in deterministic order; this retry is the safety
    net for a conflicting transaction from another posting workflow.
    """
    for attempt in range(1, _DIFF_TRANSACTION_MAX_ATTEMPTS + 1):
        try:
            async with pool.acquire() as conn, conn.transaction():
                return await conn.fetch(
                    _DIFF_BATCH,
                    urls,
                    board_id,
                    is_rich_no_scrape,
                )
        except asyncpg.DeadlockDetectedError:
            if attempt >= _DIFF_TRANSACTION_MAX_ATTEMPTS:
                raise
            ceiling = _DIFF_TRANSACTION_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            retry_in = random.uniform(ceiling / 2, ceiling)
            monitor_db_transaction_retries_total.labels(phase="diff_batch").inc()
            board_log.warning(
                "batch.monitor.db_transaction_retry",
                phase="diff_batch",
                attempt=attempt,
                max_attempts=_DIFF_TRANSACTION_MAX_ATTEMPTS,
                retry_in_s=round(retry_in, 3),
            )
            await asyncio.sleep(retry_in)

    raise AssertionError("unreachable")


# ── Monitor Processing ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BoardMonitorResult:
    """Processing result returned to the task-owning scheduler.

    ``tasks_total`` is deliberately not emitted by the board processor. The
    Redis or database scheduler owns the terminal task outcome, after its
    reschedule/cleanup work has also succeeded. Iteration preserves the
    historical two-value unpacking contract for internal callers and tests.
    """

    success: bool
    duration_seconds: float
    status: Literal["succeeded", "failed", "tdm_reserved"]

    def __iter__(self) -> Iterator[bool | float]:
        yield self.success
        yield self.duration_seconds


async def _process_one_board_streaming(
    board: asyncpg.Record,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    extender: object,
    pw=None,
    monitor_runtime: MonitorRuntime | None = None,
) -> BoardMonitorResult:
    """Run a streaming monitor cycle and return its scheduler-facing result.

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
    board_slug = board["board_slug"]
    board_url = board["board_url"]
    crawler_type = board["crawler_type"]

    board_log = log.bind(board_id=board_id, board_url=board_url, crawler_type=crawler_type)
    t0 = monotonic()

    pw_owned = False  # True when we created pw ourselves and must stop it
    effective_http = http
    monitor_stream = None

    try:
        metadata = board["metadata"] if board["metadata"] else {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        metadata = await _ensure_ecom_identity_cutover_receipt(
            pool,
            board_id=board_id,
            company_id=company_id,
            board_slug=board_slug,
            board_url=board_url,
            crawler_type=crawler_type,
            metadata=metadata,
            board_log=board_log,
        )

        enrich_fields = _effective_board_enrich(metadata, crawler_type)

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
                from src.shared.browser import ChromiumBrowserBackend

                pw = await ChromiumBrowserBackend().start()
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
        extraction_filtered = 0
        security_filtered = 0
        processing_filtered = 0
        all_canonical = True
        canonical_urls: set[str] = set()
        migration_canonical_pattern = _identity_migration_canonical_url_pattern(
            metadata.get("identity_migration")
        )
        # A streamed monitor may emit state on an empty batch before it emits
        # any URLs. Hold that patch until the first posting batch commits, or
        # until a valid empty run records its empty-check transition.
        pending_metadata_patch: dict = {}

        runtime = monitor_runtime or PythonMonitorRuntime(_batch.monitor_one_stream)
        monitor_stream = runtime.stream(
            board_url,
            crawler_type,
            metadata,
            effective_http,
            pw=pw,
        )
        async for result in monitor_stream:
            batch_count += 1
            total_discovered += len(result.urls)
            extraction_filtered += result.filtered_count
            rejected_provider_urls = result.security_filtered_count
            security_filtered += rejected_provider_urls
            if rejected_provider_urls:
                # ``url_allowlist`` is an opt-in provider-boundary contract,
                # not an extraction hint. Attribute every violation to its
                # board immediately; after accepted URLs from the stream have
                # committed, the cycle fails before empty/gone processing.
                monitor_url_filtered_total.labels(
                    reason="provider_boundary",
                    board_id=board_id,
                ).inc(rejected_provider_urls)
                board_log.error(
                    "batch.monitor.provider_boundary_rejected",
                    batch=batch_count,
                    rejected=rejected_provider_urls,
                    accepted=len(result.urls),
                )
            if migration_canonical_pattern is not None:
                for url in result.urls:
                    if re.fullmatch(migration_canonical_pattern, url) is None:
                        all_canonical = False
                    else:
                        canonical_urls.add(url)
            is_rich = result.jobs_by_url is not None
            if getattr(result, "truncated", False):
                any_truncated = True
            new_sitemap_url = getattr(result, "new_sitemap_url", None)
            if new_sitemap_url:
                pending_metadata_patch["sitemap_url"] = new_sitemap_url
            metadata_updates = getattr(result, "metadata_updates", None)
            if metadata_updates:
                pending_metadata_patch.update(metadata_updates)

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
                processing_filtered += count
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

                # Keep database ownership narrow.  This first transaction only
                # classifies the batch.  Rich-data
                # normalization, location backfills, and Redis queue writes can
                # take seconds; holding a pool connection across them starves
                # unrelated scrape workers and eventually surfaces as a bare
                # pool-acquire TimeoutError (#5489).
                meta_patch: dict = {}
                if _chunk_start == 0:
                    meta_patch.update(pending_metadata_patch)

                is_rich_no_scrape = is_rich and not enrich_fields
                rows = await _fetch_diff_batch(
                    pool,
                    chunk_urls,
                    board_id,
                    is_rich_no_scrape,
                    board_log,
                )

                new_urls: list[str] = []
                relisted: list[dict] = []
                touched: list[dict] = []
                n_foreign = 0
                n_foreign_relisted = 0

                for row in rows:
                    action = row["action"]
                    if action == "new":
                        new_urls.append(row["url"])
                    elif action in {"relisted", "foreign_relisted"}:
                        r2h = row["description_r2_hash"]
                        relisted.append(
                            {
                                "id": row["id"],
                                "url": row["url"],
                                "r2_hash": int(r2h) if r2h is not None else None,
                            }
                        )
                        if action == "foreign_relisted":
                            n_foreign_relisted += 1
                    elif action == "touched":
                        r2h = row["description_r2_hash"]
                        touched.append(
                            {
                                "id": row["id"],
                                "url": row["url"],
                                "r2_hash": int(r2h) if r2h is not None else None,
                                "needs_scrape_enqueue": bool(row["needs_scrape_enqueue"]),
                            }
                        )
                    elif action == "foreign":
                        n_foreign += 1

                if n_foreign:
                    monitor_dedup_total.labels(path="cross_board").inc(n_foreign)
                    monitor_foreign_discovery_total.labels(outcome="active_touch").inc(n_foreign)
                    board_log.info(
                        "batch.monitor.cross_board_duplicate",
                        count=n_foreign,
                    )
                if n_foreign_relisted:
                    monitor_dedup_total.labels(path="cross_board").inc(n_foreign_relisted)
                    monitor_foreign_discovery_total.labels(outcome="inactive_relisted").inc(
                        n_foreign_relisted
                    )
                    board_log.info(
                        "batch.monitor.cross_board_relisted",
                        count=n_foreign_relisted,
                    )

                total_new += len(new_urls)
                total_relisted += len(relisted)
                all_new_urls.extend(new_urls)

                # Hybrid monitors return rich data for only some URLs.  Keep
                # the remainder on the URL-only insert path.
                if chunk_jobs is not None:
                    rich_new_urls = [u for u in new_urls if u in chunk_jobs]
                    stub_new_urls = [u for u in new_urls if u not in chunk_jobs]
                else:
                    rich_new_urls = []
                    stub_new_urls = list(new_urls)

                new_records: list[tuple] = []
                r2_staging: list[tuple[object, object]] = []
                update_triples: list[tuple] = []
                rich_update_records: list[tuple] = []
                update_descriptions: list[tuple[str, str, str, int]] = []

                # All normalization and location-cache work happens without a
                # checked-out Postgres connection.  backfill_misses() acquires
                # its own short-lived connection only when the local cache has
                # misses.
                if chunk_jobs:
                    new_jobs = [chunk_jobs[u] for u in rich_new_urls]

                    if new_jobs:

                        def _process_new_jobs_cpu(jobs):
                            """Pure CPU: normalize, detect language, resolve, extract."""
                            records = []
                            staging = []
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
                                staging.append((j, t_ids))
                            return records, staging

                        new_records, r2_staging = _process_new_jobs_cpu(new_jobs)
                        if await loc_resolver.backfill_misses():
                            loc_resolver.drain_location_misses()

                    # Partial rich data must not overwrite fully scraped rows.
                    if not getattr(result, "hybrid", False):
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

                        resolved: list[tuple[list[int] | None, list[str] | None]] = [
                            _resolve_locations_sync(
                                loc_resolver,
                                _coerce_locations(j.locations),
                                _coerce_text(j.job_location_type),
                                _coerce_text(j.language),
                            )
                            for _, j, _ in update_triples
                        ]
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
                            rich_update_records.append(
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
                            if desc_text:
                                locale = _coerce_text(j.language) or "en"
                                update_descriptions.append(
                                    (str(pid), locale, desc_text, content_hash(desc_text))
                                )

                inserted_rich: list[tuple[object, object, str]] = []
                inserted: list = []
                n_rich_dedup = 0
                never_scrape = is_rich_no_scrape or _is_skip_no_scrape(metadata, crawler_type)

                # The second transaction contains database writes only.  Queue
                # publication follows commit so Redis can never advertise a
                # posting that was rolled back in Postgres.
                if meta_patch or new_records or rich_update_records or stub_new_urls:
                    async with pool.acquire() as conn, conn.transaction():
                        if new_records:
                            insert_sql = (
                                _INSERT_RICH_JOB_ENRICH if enrich_fields else _INSERT_RICH_JOB
                            )
                            for rec, (j, t_ids) in zip(new_records, r2_staging, strict=True):
                                row = await conn.fetchrow(insert_sql, *rec)
                                if row is None:
                                    n_rich_dedup += 1
                                    continue
                                new_posting_id = str(row["id"])
                                inserted_rich.append((j, t_ids, new_posting_id))

                            for j, _t_ids, posting_id in inserted_rich:
                                desc_html = _coerce_text(j.description)
                                if desc_html:
                                    locale = _coerce_text(j.language) or "en"
                                    desc_hash = content_hash(desc_html)
                                    await conn.execute(
                                        _UPSERT_DESCRIPTION,
                                        posting_id,
                                        locale,
                                        desc_html,
                                        desc_hash,
                                        desc_hash,
                                    )

                        if rich_update_records:
                            await conn.execute(_CREATE_RICH_UPDATES_TEMP)
                            await conn.copy_records_to_table(
                                "_rich_updates", records=rich_update_records
                            )
                            await conn.execute(_BATCH_UPDATE_RICH_CONTENT)
                            for posting_id, locale, desc_html, desc_hash in update_descriptions:
                                await conn.execute(
                                    _UPSERT_DESCRIPTION,
                                    posting_id,
                                    locale,
                                    desc_html,
                                    desc_hash,
                                    desc_hash,
                                )

                        if stub_new_urls:
                            inserted = await conn.fetch(
                                _INSERT_URL_ONLY_JOBS,
                                company_id,
                                board_id,
                                stub_new_urls,
                                never_scrape,
                            )

                        # Incremental monitor watermarks must commit atomically
                        # with inserts.  If any DB write fails, leaving this
                        # patch unapplied makes the monitor rediscover the same
                        # range on its next run instead of skipping jobs.
                        if meta_patch:
                            await conn.execute(
                                _UPDATE_METADATA,
                                board_id,
                                json.dumps(meta_patch),
                            )
                    pending_metadata_patch.clear()

                # Metrics, lifecycle logs, and Redis all run after commit.
                if n_rich_dedup:
                    monitor_dedup_total.labels(path="rich").inc(n_rich_dedup)
                    board_log.info(
                        "batch.monitor.duplicate_source_url",
                        path="rich",
                        count=n_rich_dedup,
                    )
                for j, _t_ids, posting_id in inserted_rich:
                    board_log.info(
                        "posting.discovered",
                        posting_id=posting_id,
                        board_id=board_id,
                        source_url=j.url,
                        path="rich",
                    )
                if enrich_fields and inserted_rich:
                    rich_rows = [
                        {"id": pid, "source_url": j.url} for j, _t_ids, pid in inserted_rich
                    ]
                    await _enqueue_scrapes_for_new(
                        rich_rows,
                        board_id,
                        metadata,
                        board_log,
                        crawler_type=crawler_type,
                    )

                if stub_new_urls:
                    n_deduped = len(stub_new_urls) - len(inserted)
                    if n_deduped:
                        monitor_dedup_total.labels(path="url_only").inc(n_deduped)
                        board_log.info(
                            "batch.monitor.duplicate_source_url",
                            path="url_only",
                            count=n_deduped,
                        )
                    board_log.info("batch.inserted_for_scrape", count=len(inserted))
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

                if not is_rich_no_scrape:
                    await _enqueue_scrapes_for_relisted(
                        relisted,
                        board_id,
                        metadata,
                        board_log,
                        crawler_type=crawler_type,
                    )
                    await _enqueue_scrapes_for_touched_missing(
                        touched,
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

        if security_filtered:
            # Accepted discovery writes above are deliberately retained, but
            # a provider-boundary violation must never be interpreted as an
            # authoritative empty/partial inventory. Raising here enters the
            # ordinary board failure accounting and suppresses empty checks,
            # identity migration, gone guards, and terminal delisting.
            raise RuntimeError(
                "provider boundary allowlist rejected "
                f"{security_filtered} URL(s); empty and gone detection suppressed"
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
            # Empty is a valid successful state: a company can stop hiring
            # and start again later.  Keep the board enabled and polling, but
            # delist its stale postings after the existing six-check
            # confirmation window.  The state update + delist stay atomic so
            # a failed delist cannot leave the confirmation recorded without
            # applying its posting-state consequence.
            empty_delisted_count = 0
            recovered_from: str | None = None
            try:
                async with pool.acquire() as conn, conn.transaction():
                    # Persist metadata for a genuinely empty inventory, such
                    # as a recovered but currently vacant sitemap. Do not
                    # advance state when raw URLs were present but all were
                    # rejected as implausible.
                    if total_discovered == 0 and pending_metadata_patch:
                        await conn.execute(
                            _UPDATE_METADATA,
                            board_id,
                            json.dumps(pending_metadata_patch),
                        )
                    rows = await conn.fetch(_RECORD_EMPTY_CHECK, board_id)
                    if rows:
                        recovered_from = rows[0]["recovered_from"]
                        if rows[0]["should_delist"]:
                            empty_delisted_count = await _delist_board_postings(conn, board_id)
            except (asyncpg.PostgresError, ConnectionError):
                board_log.exception("batch.monitor.empty_check_failed")
            else:
                # Only emit the metric AFTER the transaction commits —
                # see ``_delist_board_postings`` docstring.
                if empty_delisted_count:
                    _emit_gone_counter(empty_delisted_count)
                    board_log.warning(
                        "batch.monitor.empty_confirmed",
                        delisted=empty_delisted_count,
                    )
                    with contextlib.suppress(Exception):
                        await _batch.get_redis().delete("cache:platform-stats")
                _emit_board_recovery(recovered_from, board_log, discovered=0)
            return BoardMonitorResult(True, elapsed, "succeeded")

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
                recovered_from = await conn.fetchval(_RECORD_SUCCESS_NONEMPTY, board_id)
            _emit_board_recovery(recovered_from, board_log, discovered=total_discovered)
            board_log.warning(
                "batch.monitor.truncated_partial",
                discovered=total_discovered,
                note="MAX_JOBS cap hit; suppressing gone-detection this cycle",
            )
            monitor_truncated_total.labels(board_id=board_id).inc()
        else:
            async with pool.acquire() as conn, conn.transaction():
                identity_migration_gone = await _retire_canonicalized_provider_identities(
                    conn,
                    board_id=board_id,
                    company_id=company_id,
                    board_slug=board_slug,
                    board_url=board_url,
                    crawler_type=crawler_type,
                    monitor_start_ts=monitor_start_ts,
                    metadata=metadata,
                    discovered=total_discovered,
                    canonical_urls=canonical_urls,
                    truncated=any_truncated,
                    extraction_filtered=extraction_filtered,
                    security_filtered=security_filtered,
                    processing_filtered=processing_filtered,
                    all_canonical=all_canonical,
                    board_log=board_log,
                )
                guarded_gone_count, gone_skipped_reason = await _mark_gone_with_guards(
                    conn,
                    board_id,
                    total_discovered,
                    monitor_start_ts,
                    metadata,
                    delist_threshold,
                    board_log,
                )
                gone_count = identity_migration_gone + guarded_gone_count
                recovered_from = await conn.fetchval(_RECORD_SUCCESS_NONEMPTY, board_id)

            _emit_board_recovery(recovered_from, board_log, discovered=total_discovered)

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

        return BoardMonitorResult(True, elapsed, "succeeded")

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
        # Discard stale location misses from this skipped board. Mirrors
        # the cleanup the failure path does.
        loc_resolver.drain_location_misses()
        # Return success-shaped: the run was not a failure.
        return BoardMonitorResult(True, elapsed, "tdm_reserved")

    except BoardGoneError as exc:
        # A provider-native gone signal enters a recoverable confirmation
        # window. Recent success raises the bar to three confirmations, every
        # confirmation is spaced, and even terminal configured boards retain a
        # daily recovery probe. See #6156 (revises the one-shot #2215 path).
        elapsed = monotonic() - t0
        board_log.warning(
            "batch.monitor.board_gone",
            error=str(exc),
            url=getattr(exc, "url", None),
            status_code=getattr(exc, "status_code", None),
            duration_s=round(elapsed, 2),
        )
        loc_resolver.drain_location_misses()
        board_gone_count = 0
        decision = None
        try:
            async with pool.acquire() as conn, conn.transaction():
                state = await conn.fetchrow(_FETCH_BOARD_GONE_STATE, board_id)
                if state is None:
                    raise RuntimeError(f"board {board_id} disappeared before gone recording")
                decision = evaluate_gone_confirmation(
                    board_status=state["board_status"],
                    confirmation_count=state["gone_confirmation_count"],
                    first_confirmed_at=state["gone_first_confirmed_at"],
                    last_confirmed_at=state["gone_last_confirmed_at"],
                    last_success_at=state["last_success_at"],
                    gone_at=state["gone_at"],
                    now=datetime.now(UTC),
                )
                await conn.fetchrow(
                    _RECORD_BOARD_GONE,
                    board_id,
                    decision.board_status,
                    decision.confirmation_count,
                    decision.first_confirmed_at,
                    decision.last_confirmed_at,
                    decision.gone_at,
                    decision.next_check_at,
                    _error_message(exc),
                    getattr(exc, "url", None),
                    getattr(exc, "status_code", None),
                    decision.terminal_transition,
                )
                if decision.terminal_transition:
                    board_gone_count = await _delist_board_postings(conn, board_id)
        except (asyncpg.PostgresError, ConnectionError):
            board_log.exception("batch.monitor.board_gone_record_failed")
        else:
            if decision and decision.terminal_transition:
                monitor_gone_events_total.labels(event="terminal").inc()
                board_log.warning(
                    "batch.monitor.gone_confirmed",
                    confirmations=decision.confirmation_count,
                    next_probe_at=decision.next_check_at.isoformat(),
                )
            elif decision and decision.confirmation_advanced:
                monitor_gone_events_total.labels(event="confirmation").inc()
                board_log.warning(
                    "batch.monitor.gone_pending",
                    confirmations=decision.confirmation_count,
                    required_confirmations=decision.required_confirmations,
                    next_confirmation_at=decision.next_check_at.isoformat(),
                )
            if board_gone_count:
                _emit_gone_counter(board_gone_count)
                with contextlib.suppress(Exception):
                    await _batch.get_redis().delete("cache:platform-stats")
        # Re-raise so the Redis worker uses the durable confirmation/recovery
        # timestamp written above rather than its ordinary success cadence.
        raise

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        board_log.exception("batch.monitor.error", error=error_msg, duration_s=round(elapsed, 2))
        # Discard stale location misses from this failed board
        loc_resolver.drain_location_misses()
        # Five strikes enter a recoverable quarantine. The board stays enabled
        # and Redis mirrors the durable daily-capped next_check_at, so an
        # upstream, code, or config repair can prove itself without SQL.
        entered_quarantine = False
        quarantine_probe_failed = False
        try:
            async with pool.acquire() as conn, conn.transaction():
                row = await conn.fetchrow(_RECORD_FAILURE, board_id, error_msg)
                entered_quarantine = bool(row and row["entered_quarantine"])
                quarantine_probe_failed = bool(
                    row and row["board_status"] == "quarantined" and not entered_quarantine
                )
        except (asyncpg.PostgresError, ConnectionError):
            board_log.exception("batch.monitor.record_failure_failed")
        else:
            if entered_quarantine:
                monitor_quarantine_events_total.labels(event="entered").inc()
                board_log.warning(
                    "batch.monitor.quarantined",
                    retry="daily_capped_backoff",
                )
            elif quarantine_probe_failed:
                monitor_quarantine_events_total.labels(event="probe_failed").inc()
                board_log.warning(
                    "batch.monitor.quarantine_probe_failed",
                    retry="daily_capped_backoff",
                )
        return BoardMonitorResult(False, elapsed, "failed")
    finally:
        if monitor_stream is not None:
            close_stream = getattr(monitor_stream, "aclose", None)
            if close_stream is not None:
                with contextlib.suppress(Exception):
                    await close_stream()
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
            outcome = await _process_one_board_streaming(board, pool, http, extender)
            result.durations.append(outcome.duration_seconds)
            tasks_total.labels(kind="monitor", status=outcome.status).inc()
            if outcome.success:
                result.succeeded += 1
        except BoardGoneError:
            tasks_total.labels(kind="monitor", status="gone").inc()
            log.warning("batch.monitor.pipeline_gone", board_id=str(board["id"]))
        except Exception:
            tasks_total.labels(kind="monitor", status="failed").inc()
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
    enrich_fields = _effective_board_enrich(metadata, crawler_type)

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
    monitor_http = http
    monitor_http_owned: httpx.AsyncClient | None = None
    monitor_ssl_verify = metadata.get("ssl_verify", True)
    monitor_use_proxy = bool(metadata.get("proxy"))
    if not monitor_ssl_verify or monitor_use_proxy:
        from src.shared.http import create_http_client

        monitor_http_owned = create_http_client(
            verify=monitor_ssl_verify,
            use_proxy=monitor_use_proxy,
        )
        monitor_http = monitor_http_owned

    try:
        result = await _batch.monitor_one(
            board["board_url"],
            crawler_type,
            metadata,
            monitor_http,
            pw=pw,
        )
    except Exception as exc:
        log.error(
            "dry_run.monitor.failed",
            board_slug=board_slug,
            error=_error_message(exc),
            exc_info=True,
        )
        return
    finally:
        if monitor_http_owned is not None:
            await monitor_http_owned.aclose()

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
    scrape_http = http
    scrape_http_owned: httpx.AsyncClient | None = None
    scrape_ssl_verify = metadata.get("ssl_verify", True)
    scrape_use_proxy = bool(cfg.get("proxy"))
    if not scrape_ssl_verify or scrape_use_proxy:
        from src.shared.http import create_http_client

        scrape_http_owned = create_http_client(
            verify=scrape_ssl_verify,
            use_proxy=scrape_use_proxy,
        )
        scrape_http = scrape_http_owned

    try:
        for url in sample_urls:
            try:
                content = await _batch.scrape_one(
                    url,
                    scraper_type,
                    scraper_config,
                    scrape_http,
                    pw=pw,
                )
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
                            log.info(
                                "dry_run.scraper.field",
                                url=url,
                                field=f.name,
                                value=display,
                            )
                        else:
                            log.info(
                                "dry_run.scraper.field",
                                url=url,
                                field=f.name,
                                value="(null)",
                            )

            except Exception as exc:
                log.error("dry_run.scraper.error", url=url, error=_error_message(exc))
    finally:
        if scrape_http_owned is not None:
            await scrape_http_owned.aclose()

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
    monitor_result = await _process_one_board_streaming(board, pool, http, extender)
    log.info(
        "single_board.monitor.done",
        board_slug=board_slug,
        duration_s=round(monitor_result.duration_seconds, 2),
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
