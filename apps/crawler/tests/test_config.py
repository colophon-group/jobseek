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
        assert s.crawler_db_role == "oneoff"
        assert s.crawler_db_pool_min == 0
        assert s.crawler_db_pool_max == 4

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

    def test_web_database_url_is_optional_without_mirror_fallback(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://mirror@localhost/mirror")
        monkeypatch.delenv("WEB_DATABASE_URL", raising=False)
        s = Settings(_env_file=None)
        assert s.database_url.endswith("/mirror")
        assert s.web_database_url == ""

    def test_web_database_url_is_independent(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://mirror@localhost/mirror")
        monkeypatch.setenv("WEB_DATABASE_URL", "postgresql://web@localhost/web")
        s = Settings(_env_file=None)
        assert s.database_url.endswith("/mirror")
        assert s.web_database_url.endswith("/web")

    def test_webshare_backbone_pool_is_normalized(self):
        settings = Settings(
            _env_file=None,
            proxy_provider=" WebShare ",
            webshare_proxy_urls=[
                "http://user-a:secret@p.webshare.io:10000/",
                "http://user-b:secret@p.webshare.io:10001",
            ],
            webshare_expected_client_ips=["2001:0db8::1", "192.0.2.10"],
            webshare_proxy_canary_slot=1,
        )

        assert settings.proxy_provider == "webshare"
        assert settings.webshare_proxy_urls == [
            "http://user-a:secret@p.webshare.io:10000",
            "http://user-b:secret@p.webshare.io:10001",
        ]
        assert settings.webshare_expected_client_ips == ["192.0.2.10", "2001:db8::1"]

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"proxy_provider": "mystery"}, "PROXY_PROVIDER"),
            (
                {"webshare_proxy_urls": ["http://user:secret@192.0.2.10:7000"]},
                "p.webshare.io",
            ),
            (
                {"webshare_proxy_urls": ["http://p.webshare.io:10000"]},
                "credentialed",
            ),
            (
                {
                    "webshare_proxy_urls": [
                        "http://user:secret@p.webshare.io:10000",
                        "http://user:secret@p.webshare.io:10000",
                    ]
                },
                "duplicate",
            ),
            ({"webshare_expected_client_ips": ["not-an-ip"]}, "IPv4 or IPv6"),
            (
                {
                    "webshare_proxy_canary_slot": 0,
                    "webshare_proxy_urls": [],
                    "webshare_proxy_url": "",
                },
                "CANARY_SLOT",
            ),
        ],
    )
    def test_invalid_proxy_settings_are_rejected(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            Settings(_env_file=None, **kwargs)

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

    @pytest.mark.parametrize(
        ("role", "minimum", "maximum", "idle", "message"),
        [
            ("Worker_1", 0, 4, 60, "CRAWLER_DB_ROLE"),
            ("x" * 41, 0, 4, 60, "CRAWLER_DB_ROLE"),
            ("worker-1", -1, 4, 60, "CRAWLER_DB_POOL_MIN"),
            ("worker-1", 5, 4, 60, "CRAWLER_DB_POOL_MIN"),
            ("worker-1", 0, 0, 60, "CRAWLER_DB_POOL_MAX"),
            ("worker-1", 0, 9, 60, "CRAWLER_DB_POOL_MAX"),
            ("worker-1", 0, 4, 0, "CRAWLER_DB_POOL_IDLE_SECONDS"),
            ("worker-1", 0, 4, 61, "CRAWLER_DB_POOL_IDLE_SECONDS"),
            ("worker-1", 0, 4, float("nan"), "CRAWLER_DB_POOL_IDLE_SECONDS"),
            ("worker-1", 0, 4, float("inf"), "CRAWLER_DB_POOL_IDLE_SECONDS"),
        ],
    )
    def test_invalid_postgresql_pool_budget_is_rejected(
        self, role, minimum, maximum, idle, message
    ):
        with pytest.raises(ValueError, match=message):
            Settings(
                _env_file=None,
                crawler_db_role=role,
                crawler_db_pool_min=minimum,
                crawler_db_pool_max=maximum,
                crawler_db_pool_idle_seconds=idle,
            )
