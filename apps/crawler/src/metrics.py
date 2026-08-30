from __future__ import annotations

import json
import re
import socketserver
import sys
import threading
import time
from importlib.metadata import Distribution, PackageNotFoundError
from importlib.metadata import distribution as get_distribution
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client import Counter, Gauge, Histogram, make_wsgi_app

from src.shared.constants import is_source_checkout

if TYPE_CHECKING:
    from src.runtime_cost.process_tree import ProcessTreeSample

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

# Stable replacement-boundary metrics.  Go runtimes must export the same
# names and bounded labels so a cutover can be compared to, and if necessary
# reversed from, the preceding Python deployment without dashboard rewrites.
runtime_executions_total = Counter(
    "crawler_runtime_executions_total",
    "Extraction runtime executions by stage, implementation, and outcome",
    ["stage", "implementation", "outcome"],
)

runtime_execution_duration_seconds = Histogram(
    "crawler_runtime_execution_duration_seconds",
    "Active extraction runtime duration, excluding downstream persistence",
    ["stage", "implementation"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300],
)

runtime_output_items_total = Counter(
    "crawler_runtime_output_items_total",
    "URLs or content records emitted by an extraction runtime",
    ["stage", "implementation"],
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

# Cycles where a monitor hit its MAX_JOBS cap and returned a truncated
# discovery list (#3216). Each truncation is a known silent-data-loss
# signal: the unseen tail beyond the cap would otherwise be tombstoned
# by ``_MARK_GONE_BY_TIMESTAMP``. The pipeline marks the run as partial
# and suppresses gone-detection for the affected cycle. ``board_id``
# attribution lets ops trace which board breached the cap without
# grepping logs. Cardinality stays bounded — only breaching boards emit.
monitor_truncated_total = Counter(
    "crawler_monitor_truncated_total",
    "Cycles where a monitor truncated discovery at the MAX_JOBS cap",
    ["board_id"],
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

monitor_foreign_discovery_total = Counter(
    "crawler_monitor_foreign_discovery_total",
    "Cross-board discoveries by canonical posting recovery outcome",
    ["outcome"],
)

monitor_db_transaction_retries_total = Counter(
    "crawler_monitor_db_transaction_retries_total",
    "Monitor database transactions retried after a transient PostgreSQL abort",
    ["phase"],
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

# Configured boards that exhaust the normal retry ramp remain schedulable in a
# low-frequency quarantine instead of being terminally disabled (#6157).
# ``event`` is deliberately a three-value allowlist, so the metric remains
# fleet-bounded while exposing entry, failed recovery probes, and recoveries.
monitor_quarantine_events_total = Counter(
    "crawler_monitor_quarantine_events_total",
    "Monitor quarantine state-machine transitions and recovery probes",
    ["event"],
)

# Provider-native 404/retirement signals use a bounded confirmation state
# machine (#6156). The three events expose durable confirmations, terminal
# transitions, and self-recoveries without adding a per-board label.
monitor_gone_events_total = Counter(
    "crawler_monitor_gone_events_total",
    "Monitor provider-gone confirmations, terminal transitions, and recoveries",
    ["event"],
)

# Redis-backed per-upstream-host circuit breaker (#3195). Only hosts that
# fail or are checked by the breaker create a series, keeping cardinality
# bounded to crawler origins rather than individual boards/postings.
host_circuit_state = Gauge(
    "crawler_host_circuit_state",
    "Upstream-host circuit state (1=open, 0.5=half-open probe, 0=closed)",
    ["egress_host"],
)

host_circuit_opened_total = Counter(
    "crawler_host_circuit_opened_total",
    "Times consecutive crawler failures opened an upstream-host circuit",
    ["egress_host"],
)

host_circuit_skipped_total = Counter(
    "crawler_host_circuit_skipped_total",
    "Crawler tasks deferred before network I/O because their upstream-host circuit was open",
    ["egress_host"],
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
    "Rows exported from local Postgres to configured downstreams",
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

exporter_cdc_cutoff_delay = Gauge(
    "crawler_exporter_cdc_cutoff_delay_seconds",
    "Age of the commit-safe CDC cutoff behind the captured database clock",
)

exporter_cdc_active_writers = Gauge(
    "crawler_exporter_cdc_active_writers",
    "Transactions currently holding the shared posting CDC writer marker",
)

exporter_cdc_released_writer_races_total = Counter(
    "crawler_exporter_cdc_released_writer_races_total",
    "Initially observed CDC writer locks released before the activity recheck",
)

exporter_cdc_unknown_writers_total = Counter(
    "crawler_exporter_cdc_unknown_writers_total",
    "Still-held CDC writer locks without an attributable transaction start",
)

export_errors_total = Counter(
    "crawler_export_errors_total",
    # Bumped per row dropped by the per-row fallback path (#3180). The
    # exporter previously stalled forever when a single row tripped a
    # constraint; it now falls back to per-row upserts and drops the
    # offenders so the cursor advances. ``table`` is the local Postgres
    # table being exported (currently always ``job_posting``); ``phase``
    # is ``supabase`` or ``typesense``. Bounded cardinality (<10).
    "Rows dropped by the exporter's per-row fallback path",
    ["table", "phase"],
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

r2_retry_scheduled_total = Counter(
    "crawler_r2_retry_scheduled_total",
    "R2 description retries scheduled after an exhausted upload attempt",
    ["reason"],
)

r2_retry_delay = Histogram(
    "crawler_r2_retry_delay_seconds",
    "Durable per-description delay scheduled after an R2 drain failure",
    buckets=[1, 2.5, 5, 10, 30, 60, 300, 900],
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

postgresql_pool_connections = Gauge(
    "crawler_postgresql_pool_connections",
    "Connections currently owned by a crawler PostgreSQL pool",
    ["role", "pool", "state"],
)

postgresql_pool_limit = Gauge(
    "crawler_postgresql_pool_limit",
    "Configured crawler PostgreSQL pool connection limit",
    ["role", "pool", "limit"],
)

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

# Reaper metrics (#3159 / #3173). The reaper sweeps the inflight ZSET
# for tasks whose lease expired (worker died between claim and
# ``reschedule_task``) and re-enqueues them. Any nonzero rate on
# ``reenqueued`` outside of a deploy window is a signal of worker OOM /
# crash; ``dead_lettered`` is a signal of a poison task that keeps
# re-failing — investigate by ``ZRANGE deadletter:simple 0 -1``.
inflight_reaped_total = Counter(
    "crawler_inflight_reaped_total",
    "Inflight lease entries swept by the reaper",
    ["wtype", "outcome"],  # outcome: reenqueued | dead_lettered | missing_config
)

inflight_depth = Gauge(
    "crawler_inflight_depth",
    "Tasks currently in-flight (leased)",
    ["wtype"],
)

inflight_deadletter_depth = Gauge(
    "crawler_inflight_deadletter_depth",
    "Tasks parked in the dead-letter ZSET",
    ["wtype"],
)

monitor_deadletter_lifecycle_depth = Gauge(
    "crawler_monitor_deadletter_lifecycle_depth",
    "Monitor dead-letter tasks classified against local Postgres lifecycle state",
    ["wtype", "lifecycle"],
)

# Heartbeats: tasks that called ``heartbeat_task`` and got 1 (extended)
# vs 0 (lease already gone — reaper raced us). The "lost" outcome is
# the diagnostic for tuning ``inflight_lease_ttl_seconds`` upward when
# normal processing exceeds the lease budget.
inflight_heartbeat_total = Counter(
    "crawler_inflight_heartbeat_total",
    "Inflight lease heartbeat attempts",
    ["wtype", "outcome"],  # outcome: extended | lost
)

# Graceful drain observability (#3205). Counts SIGTERM / SIGINT
# pipeline shutdowns by whether all in-flight tasks finished within
# ``settings.shutdown_grace_seconds`` (``outcome=drained``) or some
# were cancelled and left to the reaper to re-enqueue from the
# inflight lease (``outcome=timeout``). Any nonzero ``timeout`` rate
# is a signal that the grace budget is too short for the workload or
# that a task is hung — operators can tune ``SHUTDOWN_GRACE_SECONDS``
# or investigate stuck monitors.
shutdown_drain_total = Counter(
    "crawler_shutdown_drain_total",
    "Pipeline shutdowns by drain outcome",
    ["wtype", "outcome"],  # outcome: drained | timeout
)

# Number of in-flight tasks cancelled at shutdown because the drain
# budget expired (#3205). Recovered separately by the reaper from
# the inflight lease (#3259) — this counter is the leading indicator
# that the reaper will see traffic shortly after a deploy.
shutdown_cancelled_total = Counter(
    "crawler_shutdown_cancelled_total",
    "Worker tasks cancelled at shutdown after drain timeout",
    ["wtype"],
)

# ── Browser metrics ─────────────────────────────────────────────────

browser_navigate_fallback_total = Counter(
    "crawler_browser_navigate_fallback_total",
    # Outcomes: success = the current document reached the fallback state;
    # failed = the fallback state also timed out or errored; disabled = board opted out via
    # wait_fallback=None; match = fallback strategy equals primary so no
    # fallback wait was attempted; http_error = fallback reached a concrete
    # HTTP(S) 4xx/5xx document.
    "Browser navigate() fallback waits after primary wait-strategy timeout",
    ["primary", "fallback", "outcome"],
)

browser_navigation_network_retry_total = Counter(
    "crawler_browser_navigation_network_retry_total",
    "Retries of transient Chromium main-document network failures",
    ["reason", "outcome"],
)

browser_resource_blocked_total = Counter(
    "crawler_browser_resource_blocked_total",
    "Browser requests aborted by the context resource policy",
    # Both labels are bounded by constants in shared/browser.py. Hostnames
    # themselves intentionally stay out of Prometheus to protect the fleet's
    # active-series budget; detailed attribution belongs in provider reports.
    ["reason", "resource_type"],
)

proxy_client_selections_total = Counter(
    "crawler_proxy_client_selections_total",
    "Proxy selections made for a top-level HTTP request or browser launch",
    # Bounded in shared/proxy.py. Never label by URL, credentials, proxy IP,
    # client IP, target host, pool slot, or board.
    ["provider", "mode", "transport"],
)

proxy_configuration_failures_total = Counter(
    "crawler_proxy_configuration_failures_total",
    "Proxy-required client creation rejected because the selected provider is unusable",
    ["provider", "reason"],
)

proxy_endpoint_health_events_total = Counter(
    "crawler_proxy_endpoint_health_events_total",
    "Proxy endpoint quarantine, half-open recovery, and exhaustion events",
    # scope: global | origin | pool; event: quarantined | half_open |
    # recovered | exhausted | evicted. Endpoint and origin identities stay out
    # of labels.
    ["provider", "scope", "event"],
)

# HTTP retry observability (#3210). The httpx retry path (and per-monitor
# copies for workday / lever / hirehive / hireology / smartrecruiters / accenture /
# PCSX / api_sniff) all retry transient failures and emit structured logs,
# but had no counter — so operators could not query "what's the retry storm
# rate?" or "is host X 429-throttling us today?" without grepping Loki.
# The 2026-04-26 NHS empty-200 incident (#2722, #2739) was diagnosed via log
# grep; with these counters it would have alerted in 5 minutes.
#
# Cardinality is bounded by the number of distinct hostnames we monitor
# (~1k tops across all boards). The label is ``urlparse(url).hostname``
# lowercased with port stripped — no path / query — so a host that
# paginates over thousands of URLs collapses to a single series.
http_retry_attempts_total = Counter(
    "crawler_http_retry_attempts_total",
    # Outcomes:
    #   retry      — a transient failure was observed and a retry was
    #                scheduled (5xx, 408/425/429, network error, empty-200,
    #                transient-403, non-list/dict body decode).
    #   recovered  — a subsequent attempt succeeded after at least one
    #                retry. Emitted at most once per call.
    #   exhausted  — the retry budget was exhausted; PaginationFetchError
    #                raised. Emitted at most once per call.
    "HTTP fetch retry attempts",
    ["host", "outcome"],
)

# Anti-bot signal counters (#3210). These are sub-categories of the
# generic "retry" outcome above — emitting both lets operators
# distinguish a 5xx storm (infrastructure) from an anti-bot ramp
# (mitigation: proxy/cookie rotation, residential IP). Each retry that
# matches one of these classifications increments BOTH the generic
# ``attempts_total{outcome="retry"}`` AND the specific counter, so PromQL
# queries that aggregate by outcome stay correct.
http_retry_empty_200_total = Counter(
    "crawler_http_retry_empty_200_total",
    "HTTP fetches returning empty 200 (anti-bot suspicion)",
    ["host"],
)
http_retry_transient_403_total = Counter(
    "crawler_http_retry_transient_403_total",
    "HTTP 403 retries (transient anti-bot)",
    ["host"],
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

browser_playwright_recycles_total = Counter(
    "crawler_browser_playwright_recycles_total",
    "Long-lived Playwright driver recycle attempts between browser jobs",
    ["outcome"],
)

browser_backend_lifecycle_total = Counter(
    "crawler_browser_backend_lifecycle_total",
    "Browser backend lifecycle events during Chromium/Lightpanda migration",
    ["backend", "event", "outcome"],
)

browser_cleanup_failures_total = Counter(
    "crawler_browser_cleanup_failures_total",
    "Browser/context cleanup failures that required outer lifecycle recovery",
    ["resource", "outcome"],
)

browser_target_closed_retries_total = Counter(
    "crawler_browser_target_closed_retries_total",
    "Fresh-context scrape retries after Playwright loses a page, context, or browser",
    ["outcome"],
)

# Direct process-tree resource evidence. The default prometheus_client process
# collector exposes only the Python parent and therefore omits Playwright and
# Chromium descendants. These aggregates are label-free and are emitted by
# every long-running crawler role; the runtime-cost capture adapter consumes
# them only after observing successful sampler coverage for every target in a
# role.
runtime_process_tree_cpu_seconds_total = Counter(
    "crawler_runtime_process_tree_cpu_seconds_total",
    "Cgroup CPU seconds consumed by the crawler role container",
)

runtime_process_tree_resident_memory_bytes = Gauge(
    "crawler_runtime_process_tree_resident_memory_bytes",
    "Current aggregate RSS of the crawler process and all descendants",
)

runtime_process_tree_descendants = Gauge(
    "crawler_runtime_process_tree_descendants",
    "Current number of descendant processes attributed to the crawler process",
)

runtime_process_tree_samples_total = Counter(
    "crawler_runtime_process_tree_samples_total",
    "Process-tree resource sampler observations by bounded outcome",
    ["outcome"],
)

runtime_process_tree_sampling_gaps_total = Counter(
    "crawler_runtime_process_tree_sampling_gaps_total",
    "Scheduled process-tree observations missed because the sampler loop was delayed",
)

runtime_process_tree_sampler_starts_total = Counter(
    "crawler_runtime_process_tree_sampler_starts_total",
    "Process-tree sampler thread starts, used to reject restarted capture windows",
)

runtime_process_tree_sample_interval_seconds = Gauge(
    "crawler_runtime_process_tree_sample_interval_seconds",
    "Configured interval between process-tree resource observations",
)

runtime_process_tree_last_sample_unixtime_seconds = Gauge(
    "crawler_runtime_process_tree_last_sample_unixtime_seconds",
    "Unix timestamp of the latest successful or failed process-tree observation",
)


# Build info — emitted once at startup so Grafana can confirm which
# ``apps/crawler/VERSION`` each container is running without SSH-ing in.
# Use via: ``crawler_build_info{version="0.8.13"} 1``.
build_info = Gauge(
    "crawler_build_info",
    "Crawler build info (always 1; inspect the ``version`` label).",
    ["version"],
)


_CRAWLER_DISTRIBUTION = "jobseek-crawler"
_CRAWLER_VERSION_PATTERN = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:\+build\.[0-9]+\.g[0-9a-f]{7,12})?"
)


def _validated_version(value: str, *, authority: str) -> str:
    version = value.strip()
    if not _CRAWLER_VERSION_PATTERN.fullmatch(version):
        raise RuntimeError(f"{authority} version is invalid")
    return version


def _source_checkout_root() -> Path:
    # src/metrics.py → src/..
    return Path(__file__).resolve().parent.parent


def _read_source_version(checkout_root: Path) -> str:
    version_file = checkout_root / "VERSION"
    try:
        value = version_file.read_text()
    except OSError as exc:
        raise RuntimeError("source checkout crawler VERSION is missing") from exc
    return _validated_version(value, authority="source checkout crawler")


def _editable_distribution_matches_checkout(
    installed_distribution: Distribution,
    checkout_root: Path,
) -> bool:
    """Validate PEP 610 provenance and identify this exact editable checkout."""

    try:
        direct_url_text = installed_distribution.read_text("direct_url.json")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("installed crawler direct_url provenance is unreadable") from exc
    if direct_url_text is None:
        return False
    try:
        direct_url = json.loads(direct_url_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("installed crawler direct_url provenance is invalid") from exc
    if not isinstance(direct_url, dict) or not isinstance(direct_url.get("url"), str):
        raise RuntimeError("installed crawler direct_url provenance is invalid")

    directory_info = direct_url.get("dir_info")
    if directory_info is None:
        return False
    if not isinstance(directory_info, dict):
        raise RuntimeError("installed crawler direct_url provenance is invalid")
    editable = directory_info.get("editable", False)
    if not isinstance(editable, bool):
        raise RuntimeError("installed crawler direct_url provenance is invalid")
    if not editable:
        return False

    parsed_url = urlparse(direct_url["url"])
    if (
        parsed_url.scheme != "file"
        or parsed_url.netloc not in ("", "localhost")
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise RuntimeError("installed crawler editable provenance is invalid")
    editable_path = Path(unquote(parsed_url.path))
    if not editable_path.is_absolute():
        raise RuntimeError("installed crawler editable provenance is invalid")
    editable_root = editable_path.resolve()
    if editable_root != checkout_root:
        raise RuntimeError("installed crawler editable provenance does not match checkout")
    return True


def _read_version() -> str:
    """Return the provenance-verified crawler release version.

    Installed metadata is authoritative unless valid PEP 610 metadata proves
    that the imported, structurally verified source tree is that distribution's
    exact editable checkout. A checkout with no installed distribution may use
    its source VERSION. Every ambiguous or invalid identity fails startup.
    """

    checkout_root = _source_checkout_root()
    source_checkout = is_source_checkout()
    try:
        installed_distribution = get_distribution(_CRAWLER_DISTRIBUTION)
    except PackageNotFoundError as exc:
        if source_checkout:
            return _read_source_version(checkout_root)
        raise RuntimeError("installed crawler distribution metadata is missing") from exc

    editable_matches = _editable_distribution_matches_checkout(
        installed_distribution,
        checkout_root,
    )
    if editable_matches:
        if not source_checkout:
            raise RuntimeError("installed crawler editable provenance is not a source checkout")
        return _read_source_version(checkout_root)

    return _validated_version(
        installed_distribution.version,
        authority="installed distribution",
    )


class _SilentMetricsHandler(WSGIRequestHandler):
    """Serve metrics without writing one access-log line per scrape."""

    def log_message(self, format: str, *args: object) -> None:
        pass


class _QuietThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    """Threaded metrics server that ignores expected client disconnects."""

    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Prometheus/Alloy probes can disappear between connect and the first
        # request byte, or while a response is being written. The standard
        # socketserver handler prints these routine disconnects as full
        # tracebacks, which Alloy then ingests as crawler errors (#5354).
        # Preserve the default traceback for every unexpected exception.
        error = sys.exception()
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def _start_metrics_http_server(
    port: int,
    addr: str = "127.0.0.1",
) -> tuple[WSGIServer, threading.Thread]:
    """Start the loopback-only metrics listener and return it for lifecycle tests."""
    server = make_server(
        addr,
        port,
        make_wsgi_app(),
        _QuietThreadingWSGIServer,
        handler_class=_SilentMetricsHandler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


_process_tree_sampler_lock = threading.Lock()
_process_tree_sampler_thread: threading.Thread | None = None
_process_tree_sampler_stop = threading.Event()


def _observe_process_tree(sample: ProcessTreeSample) -> None:
    runtime_process_tree_cpu_seconds_total.inc(sample.process_tree_cpu_delta_seconds)
    runtime_process_tree_resident_memory_bytes.set(sample.process_tree_rss_bytes)
    runtime_process_tree_descendants.set(sample.descendant_count)
    runtime_process_tree_samples_total.labels(outcome="success").inc()
    runtime_process_tree_last_sample_unixtime_seconds.set(time.time())


def _record_process_tree_sample_failure() -> None:
    runtime_process_tree_samples_total.labels(outcome="failure").inc()
    runtime_process_tree_last_sample_unixtime_seconds.set(time.time())


def _record_process_tree_sampling_gap(missed_intervals: int) -> None:
    runtime_process_tree_sampling_gaps_total.inc(missed_intervals)


def _seed_process_tree_sample_outcomes(samples_counter: Counter) -> None:
    """Expose both bounded outcomes, including a healthy explicit zero."""

    for outcome in ("success", "failure"):
        samples_counter.labels(outcome=outcome).inc(0)


def _start_process_tree_sampler(interval_seconds: float = 0.5) -> threading.Thread:
    """Start the process-tree sampler once per crawler process."""

    from src.runtime_cost.process_tree import ProcessTreeSampler, run_process_tree_sampler

    global _process_tree_sampler_thread
    with _process_tree_sampler_lock:
        if _process_tree_sampler_thread is not None and _process_tree_sampler_thread.is_alive():
            return _process_tree_sampler_thread
        _process_tree_sampler_stop.clear()
        _seed_process_tree_sample_outcomes(runtime_process_tree_samples_total)
        runtime_process_tree_sampler_starts_total.inc()
        runtime_process_tree_sample_interval_seconds.set(interval_seconds)
        sampler = ProcessTreeSampler()
        thread = threading.Thread(
            target=run_process_tree_sampler,
            kwargs={
                "sampler": sampler,
                "interval_seconds": interval_seconds,
                "stop_event": _process_tree_sampler_stop,
                "observe": _observe_process_tree,
                "record_failure": _record_process_tree_sample_failure,
                "record_gap": _record_process_tree_sampling_gap,
            },
            name="crawler-process-tree-metrics",
            daemon=True,
        )
        thread.start()
        _process_tree_sampler_thread = thread
        return thread


def start_metrics_server(port: int) -> None:
    build_info.labels(version=_read_version()).set(1)
    _start_process_tree_sampler()
    _start_metrics_http_server(port)


# ── HTTP retry helpers (#3210) ─────────────────────────────────────────


def http_retry_host(url: str) -> str:
    """Extract a Prometheus-safe ``host`` label from ``url``.

    Returns ``urlparse(url).hostname`` lowercased with the port stripped.
    Falls back to ``"unknown"`` when the URL is malformed or hostname-less
    so emission never fails — a counter labelled ``"unknown"`` is a
    legitimate operator signal (something is calling the retry path with
    a non-URL), while a ``LabelError`` raised from the emission site would
    mask the underlying retry storm we are trying to observe.

    Cardinality: bounded by the number of distinct hostnames the crawler
    contacts (~1k tops). No path / query / port component is emitted, so
    a board that paginates over thousands of distinct URLs collapses to
    a single series.
    """
    try:
        host = urlparse(url).hostname
    except (ValueError, TypeError):
        return "unknown"
    if not host:
        return "unknown"
    return host.lower()
