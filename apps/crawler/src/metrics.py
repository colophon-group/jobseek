from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# ── Worker metrics (per profile) ────────────────────────────────────

tasks_total = Counter(
    "crawler_tasks_total",
    "Total tasks processed",
    ["kind", "status"],
)

task_duration_seconds = Histogram(
    "crawler_task_duration_seconds",
    "Task duration in seconds",
    ["kind"],
    buckets=[1, 2, 5, 10, 15, 30, 60, 120, 300],
)

monitor_processed_total = Counter(
    "crawler_monitor_processed_total",
    "Boards processed by monitor workers",
    ["profile", "status"],
)

monitor_duration_seconds = Histogram(
    "crawler_monitor_duration_seconds",
    "Monitor processing duration per board",
    ["profile"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120, 300],
)

monitor_jobs_discovered = Counter(
    "crawler_monitor_jobs_discovered_total",
    "Jobs discovered by monitors",
    ["profile", "action"],
)

# Cycles where _MARK_GONE_BY_TIMESTAMP was bypassed by the resilience guards
# in ``processing/board.py`` (#2723 drop guard, #2724 blast-radius guard).
# A non-zero rate is the early signal of a paginating monitor truncating
# silently (#2722) — the alert in #2726 fires off this counter.
monitor_gone_skipped_total = Counter(
    "crawler_monitor_gone_skipped_total",
    "Cycles where gone-detection was skipped by a resilience guard",
    ["reason"],
)

monitor_url_filtered_total = Counter(
    "crawler_monitor_url_filtered_total",
    "URLs dropped by monitor pre-insert sanity checks",
    # ``board_id`` added in #2704 so a noisy board can be attributed
    # without grepping logs. Cardinality stays bounded — the counter
    # only emits when at least one URL is filtered, which in normal
    # operation is a small minority of boards (URL filters are
    # symptomatic, not steady-state). The pre-existing ``reason``
    # aggregation continues to work via PromQL ``sum by (reason)``.
    ["reason", "board_id"],
)

monitor_dedup_total = Counter(
    "crawler_monitor_dedup_total",
    "Insert attempts silently skipped by ON CONFLICT (source_url) DO NOTHING",
    ["path"],
)

api_sniffer_fallback_failed_total = Counter(
    "crawler_api_sniffer_fallback_failed_total",
    "api_sniffer replay paths that ended with no data (raised ApiSnifferFallbackError)",
    ["reason"],
)

monitor_idle_seconds = Counter(
    "crawler_monitor_idle_seconds_total",
    "Time workers spent idle (no work in queue)",
    ["profile"],
)

# Per-board monitor failure attribution (#2704). Emitted from the monitor
# pipeline's outer ``except Exception`` handler — i.e. exactly when an
# unhandled exception escapes ``_process_one_board_streaming``. Bounded
# cardinality: only failing boards emit, realistically <100 series in a
# normal week. The existing per-profile aggregates (``tasks_total``,
# ``monitor_duration_seconds``) are left untouched so dashboards keep
# working; this metric strictly adds a new failure-attribution dimension.
monitor_failed_per_board_total = Counter(
    "crawler_monitor_failed_per_board_total",
    "Monitor pipeline failures attributed to a specific board",
    ["board_id"],
)

# TDM-Reservation respect (#2842). Emitted when a fetch helper observes
# the W3C Text-and-Data-Mining opt-out signal (``tdm-reservation: 1``
# response header, or ``<meta name="tdm-reservation" content="1">`` in
# the HTML body). Distinct from the failure counter so an opted-out
# board doesn't pollute the failure ramp / consecutive_failures logic
# in ``_RECORD_FAILURE`` — it's a publisher policy decision, not a
# transient upstream incident. Bounded cardinality: per ``board_id``,
# only emits for boards that actually declare the signal (0 of 4709
# active boards as of 2026-05-09 per #2842 blast-radius probe).
monitor_skipped_tdm_total = Counter(
    "crawler_monitor_skipped_tdm_total",
    "Boards skipped by TDM-Reservation opt-out signal",
    ["board_id", "source"],
)

scrape_processed_total = Counter(
    "crawler_scrape_processed_total",
    "Scrapes processed",
    ["profile", "status"],
)

scrape_duration_seconds = Histogram(
    "crawler_scrape_duration_seconds",
    "Scrape processing duration per posting",
    ["profile"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)

# ── Exporter CDC metrics ────────────────────────────────────────────

exporter_flush_duration = Histogram(
    "crawler_exporter_flush_duration_seconds",
    "Exporter flush cycle duration",
    buckets=[0.5, 1, 2, 5, 10, 15, 30, 60],
)

exporter_rows_exported = Counter(
    "crawler_exporter_rows_exported_total",
    "Rows exported from local Postgres to Supabase",
    ["table"],
)

exporter_export_lag = Gauge(
    "crawler_exporter_export_lag",
    "Rows in local Postgres changed since last export (CDC lag)",
    ["table"],
)

exporter_last_flush_ts = Gauge(
    "crawler_exporter_last_flush_ts",
    "Unix timestamp of last successful exporter flush",
)

export_errors_total = Counter(
    "crawler_export_errors_total",
    "Exporter flush errors (Supabase write failures)",
)

# ── Reconciliation metrics ──────────────────────────────────────────

reconciliation_duration = Histogram(
    "crawler_reconciliation_duration_seconds",
    "Reconciliation cycle duration",
    buckets=[1, 5, 10, 30, 60, 300],
)

reconciliation_discrepancies = Counter(
    "crawler_reconciliation_discrepancies_total",
    "Discrepancies found during reconciliation",
)

# ── Redis queue metrics ─────────────────────────────────────────────

redis_queue_depth = Gauge(
    "crawler_redis_queue_depth",
    "Items in Redis queue",
    ["queue"],
)

redis_r2_stream_length = Gauge(
    "crawler_redis_r2_stream_length",
    "Pending R2 uploads in Redis stream",
)

# ``crawler_redis_connected`` and ``crawler_typesense_healthy`` are only set
# by the exporter (see ``exporter.py``), so they live there instead of here.
# Defining them at module level would make every container that imports
# ``metrics`` export a default-0 sample, which is misleading in queries.

# ── R2 drain metrics ────────────────────────────────────────────────

r2_uploaded_total = Counter(
    "crawler_r2_uploaded_total",
    "R2 uploads completed",
    ["status"],
)

r2_upload_duration = Histogram(
    "crawler_r2_upload_duration_seconds",
    "R2 PUT duration per file",
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)

r2_upload_bytes = Counter(
    "crawler_r2_upload_bytes_total",
    "Total bytes uploaded to R2",
)

r2_pending_gauge = Gauge(
    "crawler_r2_pending",
    "Job postings with pending R2 uploads",
)

# ── Infrastructure metrics ──────────────────────────────────────────

local_db_pool_size = Gauge("crawler_local_db_pool_size", "Local Postgres pool total connections")
local_db_pool_idle = Gauge("crawler_local_db_pool_idle", "Local Postgres pool idle connections")

supa_db_pool_size = Gauge("crawler_supa_db_pool_size", "Supabase pool total connections")
supa_db_pool_idle = Gauge("crawler_supa_db_pool_idle", "Supabase pool idle connections")

# ── Backward compat aliases ─────────────────────────────────────────

db_pool_size = Gauge("crawler_db_pool_size", "Total connections in pool")
db_pool_idle = Gauge("crawler_db_pool_idle", "Idle connections in pool")
queue_depth = Gauge(
    "crawler_queue_depth",
    "Number of items due for processing in the DB",
    ["kind", "browser", "initial"],
)
tick_skip_total = Counter(
    "crawler_tick_skip_total",
    "Scheduler ticks skipped due to resource saturation",
    ["reason"],
)

# ── Sync metrics ────────────────────────────────────────────────────

sync_duration = Histogram(
    "crawler_sync_duration_seconds",
    "sync.py execution duration",
    buckets=[1, 5, 10, 30, 60],
)

sync_boards_total = Gauge(
    "crawler_sync_boards_total",
    "Total boards synced to Redis + local Postgres",
)

# ── Typesense export metrics ───────────────────────────────────────

typesense_export_docs_total = Counter(
    "crawler_typesense_export_docs_total",
    "Documents upserted to Typesense",
    ["status"],
)

typesense_export_lag = Gauge(
    "crawler_typesense_export_lag",
    "Rows behind the Typesense export cursor",
)

typesense_export_duration_seconds = Histogram(
    "crawler_typesense_export_duration_seconds",
    "Time per Typesense upsert batch",
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 30],
)

typesense_backfill_docs_total = Counter(
    "crawler_typesense_backfill_docs_total",
    "Documents backfilled to Typesense",
)

typesense_reconciliation_discrepancies = Gauge(
    "crawler_typesense_reconciliation_discrepancies",
    "Discrepancies from last Typesense reconciliation run",
)

# ``crawler_typesense_healthy`` is defined in ``exporter.py`` — see comment
# next to ``redis_connected`` above.

typesense_memory_bytes = Gauge(
    "crawler_typesense_memory_bytes",
    "Typesense process memory usage in bytes",
)


worker_heartbeat_ts = Gauge(
    "crawler_worker_heartbeat_timestamp_seconds",
    "Unix timestamp of each worker's last loop iteration",
    ["worker_id"],
)

# ── Browser metrics ─────────────────────────────────────────────────

browser_navigate_fallback_total = Counter(
    "crawler_browser_navigate_fallback_total",
    # Outcomes: success = fallback recovered the navigation; failed = fallback
    # also timed out or errored; disabled = board opted out via
    # wait_fallback=None; match = fallback strategy equals primary so no
    # retry was attempted.
    "Browser navigate() fallback retries after primary wait-strategy timeout",
    ["primary", "fallback", "outcome"],
)

browser_content_retry_total = Counter(
    "crawler_browser_content_retry_total",
    # Outcomes: retry = page.content() raised the navigation-race error and a
    # retry was scheduled; recovered = a subsequent retry succeeded; failed =
    # all retries exhausted and the error propagated.
    "page.content() retries after the 'page is navigating' race error",
    ["outcome"],
)

browser_headless_coerced_total = Counter(
    "crawler_browser_headless_coerced_total",
    # ``headless: false`` is an Akamai-bypass opt-in that requires an X server.
    # When DISPLAY is unset at runtime (xvfb entrypoint missing, docker-run
    # entrypoint override) open_page flips to headless=True instead of
    # crashing. Any nonzero rate on ``browser-1`` in prod is a deploy/infra
    # regression — investigate the entrypoint chain. See #2431.
    "Launches where headless=False was requested but coerced to True (DISPLAY unset)",
    ["reason"],
)


# Build info — emitted once at startup so Grafana can confirm which
# ``apps/crawler/VERSION`` each container is running without SSH-ing in.
# Use via: ``crawler_build_info{version="0.8.13"} 1``.
build_info = Gauge(
    "crawler_build_info",
    "Crawler build info (always 1; inspect the ``version`` label).",
    ["version"],
)


def _read_version() -> str:
    """Read ``apps/crawler/VERSION`` relative to this module, or "unknown"."""
    import pathlib

    # src/metrics.py → src/../VERSION
    version_file = pathlib.Path(__file__).resolve().parent.parent / "VERSION"
    try:
        return version_file.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def start_metrics_server(port: int) -> None:
    build_info.labels(version=_read_version()).set(1)
    start_http_server(port)
