from __future__ import annotations

import httpx

from src.shared.http import create_http_client, create_logging_http_client


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


class TestLoggingHttpClient:
    async def test_returns_client_and_log(self):
        client, log = create_logging_http_client()
        assert isinstance(client, httpx.AsyncClient)
        assert isinstance(log, list)
        assert len(log) == 0
        await client.aclose()

    async def test_logs_requests(self):
        def handler(request):
            return httpx.Response(200, text="OK", headers={"content-type": "text/plain"})

        client, log = create_logging_http_client()
        client._transport = httpx.MockTransport(handler)
        await client.get("https://example.com/test")
        await client.aclose()

        assert len(log) == 1
        entry = log[0]
        assert entry["method"] == "GET"
        assert "example.com" in entry["url"]
        assert entry["status"] == 200
        assert entry["content_type"] == "text/plain"
        assert entry["elapsed"] is not None
        assert entry["elapsed"] >= 0

    async def test_logs_multiple_requests(self):
        def handler(request):
            if "404" in str(request.url):
                return httpx.Response(404)
            return httpx.Response(200, text="OK")

        client, log = create_logging_http_client()
        client._transport = httpx.MockTransport(handler)
        await client.get("https://example.com/ok")
        await client.get("https://example.com/404")
        await client.aclose()

        assert len(log) == 2
        assert log[0]["status"] == 200
        assert log[1]["status"] == 404
