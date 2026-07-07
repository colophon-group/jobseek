"""Export-surface tests for private-looking compatibility globals.

These modules intentionally expose underscore-prefixed names to callers in
``processing`` / ``batch`` and to focused tests. Keep that surface explicit so
CodeQL and future refactors can distinguish real exports from dead globals.
"""

from __future__ import annotations

from src import exporter
from src.core.enrich import taxonomy
from src.queries import monitor as monitor_queries
from src.queries import scrape as scrape_queries
from src.shared import http_retry
from src.shared import redis as redis_module

MONITOR_QUERY_EXPORTS = [
    "_BATCH_UPDATE_RICH_CONTENT",
    "_BLAST_RADIUS_FLOOR_DEFAULT",
    "_COUNT_BOARD_ACTIVE_AND_MISSING",
    "_CREATE_RICH_UPDATES_TEMP",
    "_DELIST_BOARD_POSTINGS",
    "_DELIST_THRESHOLD_AUTHORITATIVE",
    "_DELIST_THRESHOLD_FRAGILE",
    "_DIFF_BATCH",
    "_DROP_GUARD_HISTORY_WINDOW",
    "_DROP_GUARD_MIN_HISTORY",
    "_DROP_GUARD_THRESHOLD_DEFAULT",
    "_EXTEND_BOARD_LEASE",
    "_FETCH_DUE_BOARDS",
    "_INSERT_RICH_JOB",
    "_INSERT_RICH_JOB_ENRICH",
    "_INSERT_URL_ONLY_JOBS",
    "_MARK_GONE",
    "_MARK_GONE_BY_TIMESTAMP",
    "_RECORD_BOARD_GONE",
    "_RECORD_EMPTY_CHECK",
    "_RECORD_FAILURE",
    "_RECORD_SUCCESS_NONEMPTY",
    "_RELEASE_BOARD_LEASE",
    "_RELEASE_BOARD_LEASES",
    "_RELEASE_POSTING_LEASES",
    "_UPDATE_METADATA",
    "_UPSERT_LOCATION_MISSES",
]

SCRAPE_QUERY_EXPORTS = [
    "_CLEAR_SCRAPE_FOR_RICH",
    "_EXTEND_SCRAPE_LEASE",
    "_FETCH_BOARD_ALL_ACTIVE",
    "_FETCH_BOARD_BY_SLUG",
    "_FETCH_BOARD_SCRAPE_ITEMS",
    "_FETCH_BOARD_SCRAPERS",
    "_FETCH_DUE_JOB_POSTINGS",
    "_FETCH_POSTING_FOR_ENRICH",
    "_RECORD_SCRAPE_FAILURE",
    "_RECORD_SCRAPE_SUCCESS",
    "_RECORD_SCRAPE_TRANSIENT",
    "_UPDATE_ENRICH_CONTENT",
    "_UPDATE_JOB_CONTENT",
    "_build_skip_no_scrape_predicate",
]


def test_monitor_query_exports_are_explicit_and_available():
    assert monitor_queries.__all__ == MONITOR_QUERY_EXPORTS
    for name in MONITOR_QUERY_EXPORTS:
        assert hasattr(monitor_queries, name)


def test_scrape_query_exports_are_explicit_and_available():
    assert scrape_queries.__all__ == SCRAPE_QUERY_EXPORTS
    for name in SCRAPE_QUERY_EXPORTS:
        assert hasattr(scrape_queries, name)


def test_shared_compatibility_globals_are_explicit_exports():
    assert "_RETRYABLE_STATUSES" in http_retry.__all__
    assert "_checked" in redis_module.__all__
    assert "_warned_empty" in taxonomy.__all__


def test_exporter_posting_schema_aliases_are_explicit_exports():
    assert "_POSTING_COLUMNS" in exporter.__all__
    assert "_POSTING_UPSERT_SET" in exporter.__all__
    assert exporter.PostingSchema.column_list() == exporter._POSTING_COLUMNS
    assert exporter.PostingSchema.upsert_set() == exporter._POSTING_UPSERT_SET
