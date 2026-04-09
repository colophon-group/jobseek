from __future__ import annotations

import json

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = ""
    local_database_url: str = "postgresql://crawler:crawler@postgres:5432/crawler"
    proxy_map: dict[str, str] = {}

    @field_validator("proxy_map", mode="before")
    @classmethod
    def _parse_proxy_map(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v.strip() else {}
        return v

    # CDP-routed HTTP transport (see src/shared/cdp.py). The shared httpx
    # client routes hosts in the CDP route list through a headless-browser
    # network stack (Lightpanda cloud) to bypass datacenter-IP anti-bot
    # blocks like AWS WAF. Source of truth is ``data/cdp_routes.csv``;
    # ``cdp_routes`` (env var) is an optional runtime override that wins
    # over the file.
    #
    # ``cdp_routes`` is intentionally typed as ``str`` (not ``dict``) so
    # pydantic-settings does NOT auto-JSON-parse it before our validator
    # runs — that auto-parser raises on the empty string the docker-compose
    # ``${CDP_ROUTES:-}`` substitution produces when the secret is unset.
    # The empty/JSON parsing happens lazily in
    # ``src.shared.cdp.parse_cdp_routes`` instead.
    lightpanda_cdp_url: str = ""
    cdp_routes: str = ""
    cdp_routes_file: str = ""  # override path to data/cdp_routes.csv

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

    # Exporter
    export_interval: int = 1
    export_batch_limit: int = 2000
    reconciliation_interval: int = 86400

    # Typesense (disabled when typesense_admin_key is empty)
    typesense_host: str = ""
    typesense_port: int = 8108
    typesense_protocol: str = "http"
    typesense_admin_key: str = ""

    apify_token: str = ""
    anthropic_api_key: str = ""

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

    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")


settings = Settings()
