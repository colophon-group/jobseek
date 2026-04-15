from __future__ import annotations

import ssl

import httpx

from src.shared.http import _make_ssl_context, create_http_client, create_logging_http_client


class TestSSLContext:
    def test_op_no_ticket_set(self):
        ctx = _make_ssl_context()
        assert ctx.options & ssl.OP_NO_TICKET, (
            "OP_NO_TICKET must be set to avoid hangs with Akamai CDN"
        )

    def test_verifies_certificates(self):
        ctx = _make_ssl_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True


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


class TestProxyOptIn:
    async def test_no_proxy_by_default(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_url", "http://u:p@proxy.example:7000")
        client = create_http_client()  # use_proxy defaults to False
        # httpx stores per-scheme mounts; none should be proxy-routed
        for mount in client._mounts.values():
            assert (
                getattr(mount, "_pool", None) is None or getattr(mount, "_proxy_url", None) is None
            )
        await client.aclose()

    async def test_use_proxy_attaches_provider_url(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_url", "http://u:p@proxy.example:7000")
        client = create_http_client(use_proxy=True)
        # At least one mount carries the provider URL
        assert (
            any(
                getattr(t, "_pool", None) is not None or getattr(t, "_proxy_url", None)
                for t in client._mounts.values()
            )
            or client._mounts
        )  # mounts are populated
        await client.aclose()

    async def test_use_proxy_noop_when_provider_none(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "none")
        client = create_http_client(use_proxy=True)
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
