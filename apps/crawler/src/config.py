from __future__ import annotations

from urllib.parse import urlparse

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = ""
    local_database_url: str = "postgresql://crawler:crawler@postgres:5432/crawler"

    # Proxy provider — applies to hosts with ``"proxy": true`` in
    # ``monitor_config`` / ``scraper_config`` (``data/boards.csv``). See
    # ``src.shared.proxy`` for the provider registry. Swap provider by
    # changing ``PROXY_PROVIDER``; credentials for idle providers stay
    # around for ad-hoc testing / quick fallback.
    proxy_provider: str = "none"  # none | webshare | decodo
    webshare_proxy_url: str = ""
    decodo_proxy_url: str = ""

    # SSRF guard — comma-separated list of ``host`` or ``host:port``
    # entries that bypass the private-IP rejection in
    # ``src.shared.ssrf``. The deployment's Postgres / Redis / Typesense
    # hosts are derived automatically from their ``*_URL`` / ``*_HOST``
    # settings; this knob is for ad-hoc boards or test fixtures that
    # legitimately point at an internal service. Leave empty in
    # production. See ``src/shared/ssrf.py`` for the threat model.
    internal_hosts_allow: str = ""

    # Redis (local instance, not Upstash)
    redis_url: str = "redis://localhost:6379/0"
    # Pool size MUST be >= ``discovery_concurrency + monitor_concurrency``
    # for a worker process — otherwise concurrent ``claim_work`` calls
    # exhaust the pool and the 21st task crashes with
    # ``MaxConnectionsError``. Production runs DISCOVERY_CONCURRENCY=30
    # and MONITOR_CONCURRENCY=10 → 40 needed; 60 gives headroom for
    # ad-hoc Redis calls (lookups, metrics) and bursts during reschedule.
    redis_max_connections: int = 60
    throttle_delay_default: float = 2.0
    throttle_delay_ats: float = 0.5

    # Inflight lease + reaper (#3159 / #3173). When ``claim_work``
    # atomically pops a task off the per-domain ZSET it also records
    # an inflight lease entry in ``inflight:<wtype>``. If the worker
    # dies between claim and ``reschedule_task``/``complete_task`` the
    # reaper sweeps expired leases back onto the per-domain queue so
    # the task isn't permanently lost.
    #
    # ``inflight_lease_ttl_seconds`` is the initial budget per task.
    # Long-running monitors/scrapes extend the lease by sending
    # heartbeats every ``inflight_heartbeat_interval_seconds`` — pick
    # a value smaller than the TTL so we have headroom on a slow tick.
    # Default 600 / 120 mirrors the Postgres-side
    # ``leased_until = now() + interval '10 minutes'`` budget used by
    # the legacy batch path (``queries/monitor.py:28``,
    # ``queries/scrape.py:81``).
    inflight_lease_ttl_seconds: int = 600
    inflight_heartbeat_interval_seconds: int = 120
    # Reaper tick interval. The reaper is cheap (one Lua EVALSHA per
    # tick per worker type, capped at ``reaper_batch_size`` entries),
    # but running it too aggressively wastes Redis CPU when the
    # inflight set is empty. 30s gives <30s reaper latency on a
    # SIGKILL'd worker — within the deploy.sh restart window.
    reaper_interval_seconds: int = 30
    reaper_batch_size: int = 200
    # Strikes before a task is dead-lettered. The reaper bumps a
    # per-task counter every time it has to re-enqueue. A genuinely
    # poison task (Playwright always segfaults, etc.) would otherwise
    # loop the reaper forever — at >= ``reaper_max_strikes`` reaps,
    # the entry is moved to ``deadletter:<wtype>`` for operator
    # investigation instead. Default 5: deploy churn / OOM-once
    # patterns won't trip it; persistent failure modes will.
    reaper_max_strikes: int = 5

    # Upstash (web app only, kept for backward compat)
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    log_level: str = "INFO"
    worker_id_prefix: str = ""
    crawler_max_concurrent: int = 20
    crawler_max_browser: int = 3  # separate cap for browser (Playwright) work
    crawler_db_pool_max: int = 10
    metrics_port: int = 9091
    r2_max_connections: int = 60  # controls R2 HTTP client pool size

    # Pipeline concurrency (per-instance)
    discovery_concurrency: int = 20
    monitor_concurrency: int = 5  # max concurrent monitors (bounds peak memory)
    raw_buffer_size: int = 10
    done_buffer_size: int = 10
    writeback_concurrency: int = 5
    cpu_threads: int = 1
    drain_producers: int = 2
    drain_consumers: int = 30
    drain_buffer_size: int = 200
    # Periodic reaper for orphaned r2_uploaded=NULL rows (#3168). The
    # startup reaper always runs once before producers; this sweep
    # catches consumer crashes that happen later in the process
    # lifetime. Default 300s (5 minutes).
    drain_reaper_interval: int = 300

    # Exporter
    export_interval: int = 1
    export_batch_limit: int = 2000
    reconciliation_interval: int = 86400

    # Typesense (disabled when typesense_admin_key is empty)
    typesense_host: str = ""
    typesense_port: int = 8108
    typesense_protocol: str = "http"
    typesense_admin_key: str = ""

    # Enrichment (disabled by default — empty provider means skip)
    enrich_provider: str = ""
    enrich_model: str = ""
    enrich_api_key: str = ""
    enrich_batch_size: int = 500
    enrich_min_batch_size: int = 10
    enrich_max_wait_minutes: int = 60
    enrich_poll_interval: int = 300
    enrich_daily_spend_cap_usd: float = 5.0
    enrich_input_price_per_m: float = 0.10
    enrich_output_price_per_m: float = 0.40

    # IndexNow (disabled when indexnow_key is empty). A single POST to
    # api.indexnow.org propagates to Bing, Yandex, Seznam, Naver, and
    # Microsoft Yep. Google does NOT participate in IndexNow.
    # `indexnow_site_url` is the single source of truth — `indexnow_host`
    # is derived from it on load unless explicitly overridden.
    indexnow_key: str = ""  # 8-128 hex chars
    indexnow_host: str = ""  # derived from site_url unless set
    indexnow_site_url: str = ""  # e.g. "https://jseek.co" (no trailing slash)
    indexnow_key_url: str = ""  # e.g. "https://jseek.co/indexnow-key.txt"
    indexnow_interval: int = 3600  # seconds between ticks
    # Per-tick submission cap. Avoids telling Bing/Yandex/Seznam/Naver/Yep
    # to recrawl N×4 URLs in one blast — the resulting synchronized bot
    # sweep hammers Vercel image transforms. Unsubmitted URLs stay
    # hash-mismatched and return next tick. Set to 0 to disable the cap.
    indexnow_max_urls_per_tick: int = 500

    @model_validator(mode="after")
    def _normalize_indexnow(self) -> Settings:
        # Strip trailing slashes on site_url to avoid double-slash URLs
        # downstream ("https://jseek.co/" + "/en/..." → "...co//en/...").
        if self.indexnow_site_url.endswith("/"):
            self.indexnow_site_url = self.indexnow_site_url.rstrip("/")
        # Derive host from site_url when unset. Explicit host wins so
        # operators can still override, e.g. during cutover to www.
        if not self.indexnow_host and self.indexnow_site_url:
            self.indexnow_host = urlparse(self.indexnow_site_url).netloc
        return self

    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")


settings = Settings()
