from __future__ import annotations

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


def test_get_redis_uses_settings_when_env_missing(monkeypatch):
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    monkeypatch.setattr(
        redis_module.settings, "upstash_redis_rest_url", "https://example.upstash.io"
    )
    monkeypatch.setattr(redis_module.settings, "upstash_redis_rest_token", "token-123")

    redis_ctor = MagicMock(return_value=object())
    monkeypatch.setattr(redis_module, "Redis", redis_ctor)

    client = redis_module.get_redis()

    assert client is not None
    redis_ctor.assert_called_once_with(url="https://example.upstash.io", token="token-123")


def test_get_redis_prefers_env_over_settings(monkeypatch):
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://env.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "env-token")
    monkeypatch.setattr(
        redis_module.settings, "upstash_redis_rest_url", "https://settings.upstash.io"
    )
    monkeypatch.setattr(redis_module.settings, "upstash_redis_rest_token", "settings-token")

    redis_ctor = MagicMock(return_value=object())
    monkeypatch.setattr(redis_module, "Redis", redis_ctor)

    client = redis_module.get_redis()

    assert client is not None
    redis_ctor.assert_called_once_with(url="https://env.upstash.io", token="env-token")


def test_get_redis_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    monkeypatch.setattr(redis_module.settings, "upstash_redis_rest_url", "")
    monkeypatch.setattr(redis_module.settings, "upstash_redis_rest_token", "")

    redis_ctor = MagicMock(return_value=object())
    monkeypatch.setattr(redis_module, "Redis", redis_ctor)

    client = redis_module.get_redis()

    assert client is None
    redis_ctor.assert_not_called()
