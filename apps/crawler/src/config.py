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

    # Redis (local instance, not Upstash)
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 20
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
