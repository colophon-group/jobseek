from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    log_level: str = "INFO"
    crawler_batch_limit: int = 200
    crawler_poll_interval: int = 15
    ws_git_retries: int = 2
    ws_gh_retries: int = 2
    ws_retry_delay: float = 2.0
    ws_file_lock_timeout: float = 10.0
    ws_preflight_enabled: bool = True
    ws_preflight_check_branch: bool = True

    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")


settings = Settings()
