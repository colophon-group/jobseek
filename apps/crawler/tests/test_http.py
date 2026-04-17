from __future__ import annotations

import ssl

import httpx

from src.shared.http import (
    DEFAULT_ACCEPT,
    DEFAULT_USER_AGENT,
    _client_kwargs,
    _make_ssl_context,
    create_http_client,
    create_logging_http_client,
)


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
        assert client.headers["user-agent"] == DEFAULT_USER_AGENT
        assert "Chrome/" in client.headers["user-agent"]
        assert "jobseek" not in client.headers["user-agent"].lower()
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

    async def test_accept_header_is_browser_default(self):
        """Regression for #2214: httpx's own default is ``*/*``, which is a
        bot-fingerprint signal that Uber's HTML surface 406s on. We send the
        same Accept Chrome sends, with ``*/*;q=0.8`` at the tail so endpoints
        that prefer JSON still match."""
        client = create_http_client()
        assert client.headers["accept"] == DEFAULT_ACCEPT
        assert "text/html" in client.headers["accept"]
        assert "*/*" in client.headers["accept"]
        await client.aclose()

    async def test_per_request_accept_overrides_default(self):
        """Monitors/scrapers that need a specific Accept (e.g. api_sniffer
        sending ``application/json``) must still win. httpx merges client +
        request headers and the request entry wins on conflict."""
        captured: dict[str, str] = {}

        def handler(request):
            captured["accept"] = request.headers.get("accept", "")
            return httpx.Response(200, text="OK")

        client = create_http_client()
        client._transport = httpx.MockTransport(handler)
        await client.get("https://example.com/", headers={"Accept": "application/json"})
        await client.aclose()

        assert captured["accept"] == "application/json"


class TestProxyOptIn:
    """Test the public contract: ``_client_kwargs`` is the exact dict we
    pass to ``httpx.AsyncClient(**kwargs)``. Asserting on it is robust
    against httpx internal changes (no probing of ``client._mounts``).
    """

    URL = "http://u:p@proxy.example:7000"

    def test_no_proxy_by_default(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_url", self.URL)
        kwargs = _client_kwargs(verify=True, use_proxy=False)
        assert "proxy" not in kwargs

    def test_use_proxy_true_attaches_provider_url(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_url", self.URL)
        kwargs = _client_kwargs(verify=True, use_proxy=True)
        assert kwargs["proxy"] == self.URL

    def test_use_proxy_true_noop_when_provider_none(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "none")
        monkeypatch.setattr(config.settings, "webshare_proxy_url", self.URL)
        kwargs = _client_kwargs(verify=True, use_proxy=True)
        assert "proxy" not in kwargs

    def test_use_proxy_true_noop_when_url_empty(self, monkeypatch):
        """Active provider but empty URL — missing_url ERROR logged, direct egress."""
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_url", "")
        kwargs = _client_kwargs(verify=True, use_proxy=True)
        assert "proxy" not in kwargs

    async def test_create_http_client_accepts_use_proxy_kwarg(self, monkeypatch):
        """Sanity: the factory builds a live AsyncClient with the proxy attached."""
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_url", self.URL)
        client = create_http_client(use_proxy=True)
        try:
            assert isinstance(client, httpx.AsyncClient)
        finally:
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
