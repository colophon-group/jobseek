from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.shared.redis as redis_module


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(redis_module, "_client", None)
    monkeypatch.setattr(redis_module, "_checked", False)
    yield
    monkeypatch.setattr(redis_module, "_client", None)
    monkeypatch.setattr(redis_module, "_checked", False)


@pytest.fixture()
def _mock_settings(monkeypatch):
    """Inject a fake src.config module with a settings object."""
    fake_settings = SimpleNamespace(
        upstash_redis_rest_url="https://example.upstash.io",
        upstash_redis_rest_token="token-123",
    )
    fake_config = SimpleNamespace(settings=fake_settings)
    monkeypatch.setitem(sys.modules, "src.config", fake_config)
    return fake_settings


@pytest.fixture()
def _mock_redis_class(monkeypatch):
    """Inject a fake upstash_redis.asyncio module with a Redis constructor."""
    redis_ctor = MagicMock(return_value=object())
    fake_asyncio_mod = SimpleNamespace(Redis=redis_ctor)
    monkeypatch.setitem(sys.modules, "upstash_redis", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "upstash_redis.asyncio", fake_asyncio_mod)
    return redis_ctor


def test_get_redis_uses_settings_when_env_missing(monkeypatch, _mock_settings, _mock_redis_class):
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)

    client = redis_module.get_redis()

    assert client is not None
    _mock_redis_class.assert_called_once_with(url="https://example.upstash.io", token="token-123")


def test_get_redis_prefers_env_over_settings(monkeypatch, _mock_settings, _mock_redis_class):
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://env.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "env-token")

    client = redis_module.get_redis()

    assert client is not None
    _mock_redis_class.assert_called_once_with(url="https://env.upstash.io", token="env-token")


def test_get_redis_returns_none_when_unconfigured(monkeypatch, _mock_redis_class):
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    # Remove src.config from modules so the lazy import fails gracefully
    monkeypatch.delitem(sys.modules, "src.config", raising=False)

    client = redis_module.get_redis()

    assert client is None
    _mock_redis_class.assert_not_called()
