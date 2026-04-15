"""Batch processor — backward-compatibility re-export hub.

This module re-exports symbols from the processing/ and queries/ sub-packages
so that existing callers (tests, workspace CLI, processing/board.py's
``_BatchLookups`` proxy) continue to work via ``from src.batch import ...``.

Business logic lives in:
- src/processing/board.py  — monitor processing (streaming)
- src/processing/scrape.py — scrape processing
- src/processing/cpu.py    — CPU-bound work
- src/processing/r2_stage.py — R2 staging helpers
- src/queries/             — SQL queries
"""

from __future__ import annotations

import src.queries.lookups as _lookups_mod
from src.core.monitor import monitor_one, monitor_one_stream  # noqa: F401
from src.core.monitors import api_monitor_types, get_stream_fn, monitor_needs_browser  # noqa: F401
from src.core.scrape import scrape_one  # noqa: F401

# ── Re-exports: processing/board.py ──────────────────────────────────
from src.processing.board import (  # noqa: F401
    _SLOW_MONITOR_SECONDS,
    _SLOW_SCRAPE_SECONDS,
    BatchResult,
    BoardBatch,
    BoardDone,
    BoardError,
    DeadlineExtender,
    _monitor_pipeline,
    _process_one_board_streaming,
    _throttle_key,
    dry_run_single_board,
    process_monitor_batch,
    run_single_board,
)

# ── Re-exports: processing/cpu.py ────────────────────────────────────
from src.processing.cpu import (  # noqa: F401
    _GARBAGE_TITLES,
    JobCPUResult,
    _build_locales,
    _build_titles,
    _coerce_datetime,
    _coerce_locations,
    _coerce_text,
    _error_message,
    _extract_experience_fields,
    _extract_salary_fields,
    _is_garbage_title,
    _jsonb,
    _parse_metadata,
    _parse_update_count,
    _process_jobs_cpu,
    _resolve_locations_sync,
    _resolve_occupation_seniority,
    _resolve_technology_ids,
)

# ── Re-exports: processing/r2_stage.py ───────────────────────────────
from src.processing.r2_stage import (  # noqa: F401
    _HASH_VOLATILE_FIELDS,
    _build_r2_extras,
    _compute_r2_hash,
    _deep_sort,
    _serialize_localizations,
    _stable_date,
    _stage_r2_pending,
)

# ── Re-exports: processing/scrape.py ─────────────────────────────────
from src.processing.scrape import (  # noqa: F401
    _JOBCONTENT_FIELDS,
    _UPSERT_DESCRIPTION,
    BoardScraperConfig,
    ScrapeError,
    ScrapeItem,
    ScrapeResult,
    _apply_defaults,
    _board_has_enrich,
    _BoardScraperInfo,
    _do_one_enrich_scrape,
    _do_one_scrape,
    _get_next_fallback,
    _get_scraper_at_step,
    _load_board_scrapers,
    _merge_fields,
    _PipelineResult,
    _process_one_enrich_scrape,
    _process_one_scrape,
    _run_scrape_items,
    _scrape_pipeline,
    _ScrapeWorkItem,
    process_scrape_batch,
)

# ── Re-exports: queries/lookups.py ───────────────────────────────────
from src.queries.lookups import (  # noqa: F401
    _flush_location_misses,
    _get_currency_rates,
    _get_location_resolver,
    _get_occupation_ids,
    _get_seniority_ids,
    _get_technology_ids,
    _resolve_locations,
)

# ── Re-exports: queries/monitor.py ───────────────────────────────────
from src.queries.monitor import (  # noqa: F401
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
    _MARK_GONE,
    _RECORD_BOARD_GONE,
    _RECORD_EMPTY_CHECK,
    _RECORD_FAILURE,
    _RECORD_SUCCESS_NONEMPTY,
    _RELEASE_BOARD_LEASE,
    _RELEASE_BOARD_LEASES,
    _RELEASE_POSTING_LEASES,
    _UPDATE_METADATA,
    _UPSERT_LOCATION_MISSES,
)

# ── Re-exports: queries/scrape.py ────────────────────────────────────
from src.queries.scrape import (  # noqa: F401
    _CLEAR_SCRAPE_FOR_RICH,
    _EXTEND_SCRAPE_LEASE,
    _FETCH_BOARD_ALL_ACTIVE,
    _FETCH_BOARD_BY_SLUG,
    _FETCH_BOARD_SCRAPE_ITEMS,
    _FETCH_BOARD_SCRAPERS,
    _FETCH_DUE_JOB_POSTINGS,
    _FETCH_POSTING_FOR_ENRICH,
    _RECORD_SCRAPE_FAILURE,
    _RECORD_SCRAPE_SUCCESS,
    _UPDATE_ENRICH_CONTENT,
    _UPDATE_JOB_CONTENT,
)
from src.shared.redis import get_redis  # noqa: F401

# ── Backward-compatible module-level singletons ──────────────────────
# Forward reads/writes of mutable singleton variables to src.queries.lookups
# so that tests setting ``src.batch._location_resolver = ...`` propagate.

_FORWARDED_ATTRS = frozenset(
    {
        "_location_resolver",
        "_technology_id_map",
        "_occupation_id_map",
        "_seniority_id_map",
        "_currency_rates",
    }
)


def __getattr__(name: str):
    if name in _FORWARDED_ATTRS:
        return getattr(_lookups_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


import sys as _sys  # noqa: E402


class _ModuleProxy(__import__("types").ModuleType):
    """Module subclass that forwards singleton writes to lookups."""

    def __setattr__(self, name, value):
        if name in _FORWARDED_ATTRS:
            setattr(_lookups_mod, name, value)
        super().__setattr__(name, value)

    def __getattr__(self, name):
        if name in _FORWARDED_ATTRS:
            return getattr(_lookups_mod, name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_sys.modules[__name__].__class__ = _ModuleProxy
