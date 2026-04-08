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

monitor_idle_seconds = Counter(
    "crawler_monitor_idle_seconds_total",
    "Time workers spent idle (no work in queue)",
    ["profile"],
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

redis_connected = Gauge(
    "crawler_redis_connected",
    "Redis connection status (1=connected, 0=disconnected)",
)

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

typesense_healthy = Gauge(
    "crawler_typesense_healthy",
    "Typesense health status (1=healthy, 0=unhealthy)",
)

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


def start_metrics_server(port: int) -> None:
    start_http_server(port)
