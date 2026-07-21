from __future__ import annotations

import pytest

from src.config import Settings


class TestSettings:
    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        s = Settings()
        assert s.log_level == "INFO"
        assert s.crawler_max_concurrent == 20
        assert s.metrics_port == 9091
        assert s.browser_playwright_recycle_seconds == 6 * 60 * 60

    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://custom@localhost/custom")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("CRAWLER_MAX_CONCURRENT", "50")
        s = Settings()
        assert s.database_url == "postgresql://custom@localhost/custom"
        assert s.log_level == "DEBUG"
        assert s.crawler_max_concurrent == 50

    def test_database_url_defaults_to_empty(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        s = Settings(_env_file=None)
        assert s.database_url == ""

    @pytest.mark.parametrize(
        ("base", "maximum"),
        [(0, 900), (-1, 900), (10, 5)],
    )
    def test_invalid_drain_retry_window_is_rejected(self, base, maximum):
        with pytest.raises(ValueError, match="DRAIN_RETRY_BASE_SECONDS"):
            Settings(
                _env_file=None,
                drain_retry_base_seconds=base,
                drain_retry_max_seconds=maximum,
            )
