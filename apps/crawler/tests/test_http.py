from __future__ import annotations

import httpx

from src.shared.http import create_http_client


class TestCreateHttpClient:
    async def test_returns_async_client(self):
        client = create_http_client()
        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()

    async def test_user_agent(self):
        client = create_http_client()
        assert client.headers["user-agent"] == "jobseek-crawler/0.1"
        await client.aclose()

    async def test_timeout(self):
        client = create_http_client()
        assert client.timeout.connect == 30.0
        assert client.timeout.read == 30.0
        await client.aclose()

    async def test_follow_redirects(self):
        client = create_http_client()
        assert client.follow_redirects is True
        await client.aclose()
