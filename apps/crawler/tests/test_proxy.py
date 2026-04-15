"""Tests for src.shared.proxy — provider-based proxy layer."""

from __future__ import annotations

from src.shared.proxy import (
    StaticProxyProvider,
    get_provider,
    httpx_proxy_for,
    playwright_proxy_for,
)


def _set_provider(monkeypatch, name: str, webshare: str = "", decodo: str = "") -> None:
    from src import config

    monkeypatch.setattr(config.settings, "proxy_provider", name)
    monkeypatch.setattr(config.settings, "webshare_proxy_url", webshare)
    monkeypatch.setattr(config.settings, "decodo_proxy_url", decodo)


class TestStaticProxyProvider:
    def test_returns_url(self):
        p = StaticProxyProvider("webshare", "http://u:p@host:1000")
        assert p.name == "webshare"
        assert p.proxy_url() == "http://u:p@host:1000"

    def test_empty_url_returns_none(self):
        p = StaticProxyProvider("webshare", "")
        assert p.proxy_url() is None


class TestGetProvider:
    def test_webshare(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://u:p@ws:7000")
        p = get_provider()
        assert p is not None
        assert p.name == "webshare"
        assert p.proxy_url() == "http://u:p@ws:7000"

    def test_decodo(self, monkeypatch):
        _set_provider(monkeypatch, "decodo", decodo="http://u:p@dc:10001")
        p = get_provider()
        assert p is not None
        assert p.name == "decodo"
        assert p.proxy_url() == "http://u:p@dc:10001"

    def test_none(self, monkeypatch):
        _set_provider(monkeypatch, "none", webshare="http://u:p@ws:7000")
        assert get_provider() is None

    def test_unknown_provider_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "iproyal", webshare="http://u:p@ws:7000")
        assert get_provider() is None

    def test_webshare_without_url_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "webshare")
        assert get_provider() is None


class TestHttpxProxyFor:
    def test_opt_out_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://u:p@ws:7000")
        assert httpx_proxy_for(use_proxy=False) is None

    def test_opt_in_returns_url(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://u:p@ws:7000")
        assert httpx_proxy_for(use_proxy=True) == "http://u:p@ws:7000"

    def test_opt_in_no_provider_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "none")
        assert httpx_proxy_for(use_proxy=True) is None


class TestPlaywrightProxyFor:
    def test_opt_out_returns_none(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://u:p@ws:7000")
        assert playwright_proxy_for(use_proxy=False) is None

    def test_parses_credentials(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://user:pass@host.example:7000")
        result = playwright_proxy_for(use_proxy=True)
        assert result == {
            "server": "http://host.example:7000",
            "username": "user",
            "password": "pass",
        }

    def test_no_credentials(self, monkeypatch):
        _set_provider(monkeypatch, "webshare", webshare="http://host.example:8080")
        result = playwright_proxy_for(use_proxy=True)
        assert result == {"server": "http://host.example:8080"}

    def test_none_when_disabled(self, monkeypatch):
        _set_provider(monkeypatch, "none")
        assert playwright_proxy_for(use_proxy=True) is None
