from __future__ import annotations

import json

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = ""
    proxy_map: dict[str, str] = {}

    @field_validator("proxy_map", mode="before")
    @classmethod
    def _parse_proxy_map(cls, v):
        if isinstance(v, str):
            return json.loads(v) if v.strip() else {}
        return v

    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    log_level: str = "INFO"
    worker_id_prefix: str = ""
    crawler_batch_limit: int = 200
    crawler_poll_interval: int = 15
    crawler_max_concurrent: int = 20
    crawler_max_browser: int = 3  # separate cap for browser (Playwright) work
    crawler_db_pool_max: int = 10
    crawler_db_writers: int = 1  # number of concurrent pipeline DB writers
    metrics_port: int = 9091
    r2_max_connections: int = 60  # also controls number of drain worker consumers
    r2_drain_producers: int = 1  # number of concurrent DB-fetch producers
    r2_drain_writers: int = 1  # number of concurrent DB-write workers
    r2_drain_batch_size: int = 200
    r2_drain_max_retries: int = 5
    r2_drain_shutdown_timeout: float = 30.0
    r2_queue_max: int = 50000  # skip re-upload staging above this threshold

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
