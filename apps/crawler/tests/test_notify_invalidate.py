"""Tests for notify_invalidate_typeahead — the crawler-side caller of the
web-app invalidation endpoint.
"""

from __future__ import annotations

import httpx
import pytest

from src.notify_invalidate import notify_invalidate_typeahead


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_INVALIDATE_URL", raising=False)
    monkeypatch.delenv("INTERNAL_REVALIDATE_TOKEN", raising=False)


async def test_no_op_when_env_unset() -> None:
    """Local dev / CI: missing env vars are not an error — log + skip."""
    async with httpx.AsyncClient() as http:
        ok = await notify_invalidate_typeahead(http)
    assert ok is False


async def test_posts_with_bearer_token_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_INVALIDATE_URL", "https://jseek.co/api/internal/invalidate-typeahead")
    monkeypatch.setenv("INTERNAL_REVALIDATE_TOKEN", "s3cr3t")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"ok": True, "deleted": {"loc-suggest:": 5}, "total": 5},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        ok = await notify_invalidate_typeahead(http)

    assert ok is True
    assert captured["url"] == "https://jseek.co/api/internal/invalidate-typeahead"
    assert captured["auth"] == "Bearer s3cr3t"


async def test_returns_false_on_4xx_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_INVALIDATE_URL", "https://x/y")
    monkeypatch.setenv("INTERNAL_REVALIDATE_TOKEN", "wrong")

    transport = httpx.MockTransport(
        lambda _req: httpx.Response(401, json={"error": "unauthorized"})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        ok = await notify_invalidate_typeahead(http)
    assert ok is False


async def test_returns_false_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network failures must NOT raise — TTL backstop catches the staleness."""
    monkeypatch.setenv("WEB_INVALIDATE_URL", "https://x/y")
    monkeypatch.setenv("INTERNAL_REVALIDATE_TOKEN", "tok")

    def boom(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns fail")

    transport = httpx.MockTransport(boom)
    async with httpx.AsyncClient(transport=transport) as http:
        ok = await notify_invalidate_typeahead(http)
    assert ok is False
