from __future__ import annotations

from src.config import Settings


class TestSettings:
    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        s = Settings()
        assert s.log_level == "INFO"
        assert s.crawler_batch_limit == 200
        assert s.crawler_poll_interval == 15

    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://custom@localhost/custom")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("CRAWLER_BATCH_LIMIT", "500")
        monkeypatch.setenv("CRAWLER_POLL_INTERVAL", "30")
        s = Settings()
        assert s.database_url == "postgresql://custom@localhost/custom"
        assert s.log_level == "DEBUG"
        assert s.crawler_batch_limit == 500
        assert s.crawler_poll_interval == 30

    def test_database_url_defaults_to_empty(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        s = Settings(_env_file=None)
        assert s.database_url == ""
