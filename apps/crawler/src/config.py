from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = ""
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    log_level: str = "INFO"
    crawler_batch_limit: int = 200
    crawler_poll_interval: int = 15
    crawler_max_concurrent: int = 20

    apify_token: str = ""

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
