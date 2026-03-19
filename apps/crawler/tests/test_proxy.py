"""Tests for src.shared.proxy — per-domain proxy routing."""

from __future__ import annotations

import pytest

from src.shared.proxy import build_httpx_mounts, build_playwright_proxy, proxy_for_url


@pytest.fixture(autouse=True)
def _patch_proxy_map(monkeypatch):
    """Default: proxy map with one entry."""
    from src import config

    monkeypatch.setattr(
        config.settings,
        "proxy_map",
        {"apply.workable.com": "http://user:pass@gate.smartproxy.com:7777"},
    )


class TestProxyForUrl:
    def test_exact_match(self):
        result = proxy_for_url("https://apply.workable.com/company/j/ABC123/")
        assert result == "http://user:pass@gate.smartproxy.com:7777"

    def test_no_match(self):
        assert proxy_for_url("https://example.com/jobs") is None

    def test_empty_map(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_map", {})
        assert proxy_for_url("https://apply.workable.com/foo") is None


class TestBuildHttpxMounts:
    def test_builds_correct_mount_keys(self):
        mounts = build_httpx_mounts()
        assert mounts is not None
        assert "all://apply.workable.com" in mounts

    def test_returns_none_when_empty(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_map", {})
        assert build_httpx_mounts() is None


class TestBuildPlaywrightProxy:
    def test_returns_dict_with_credentials(self):
        result = build_playwright_proxy("https://apply.workable.com/company/")
        assert result is not None
        assert result["server"] == "http://gate.smartproxy.com:7777"
        assert result["username"] == "user"
        assert result["password"] == "pass"

    def test_returns_none_when_no_match(self):
        assert build_playwright_proxy("https://example.com/jobs") is None

    def test_returns_none_when_empty_map(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_map", {})
        assert build_playwright_proxy("https://apply.workable.com/foo") is None

    def test_no_credentials(self, monkeypatch):
        from src import config

        monkeypatch.setattr(
            config.settings,
            "proxy_map",
            {"example.com": "http://proxy.example.com:8080"},
        )
        result = build_playwright_proxy("https://example.com/jobs")
        assert result is not None
        assert result["server"] == "http://proxy.example.com:8080"
        assert "username" not in result
        assert "password" not in result
