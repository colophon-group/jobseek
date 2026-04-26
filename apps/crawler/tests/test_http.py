from __future__ import annotations

import ssl

import httpx

from src.shared.http import (
    _AVATURE_ACCEPT,
    DEFAULT_ACCEPT,
    DEFAULT_USER_AGENT,
    _client_kwargs,
    _is_avature_host,
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


class TestAvatureHeaderOverride:
    """Regression for #2708: apply.deloitte.com 403/406 cluster.

    Avature ATS hosts sit behind a WAF that fingerprints scripts by
    their thin browser headers (406 specifically signals Accept
    rejection). We send a richer Chrome navigation header set when
    targeting these hosts."""

    AVATURE_HOSTS = [
        "apply.deloitte.com",
        "apply.deloitte.ch",
        "apply.deloitte.co.uk",
        "apply.deloittece.com",
        "bloomberg.avature.net",
        "dhlconsulting.avature.net",
        "tescoinsuranceandmoneyservices.avature.net",
    ]
    NON_AVATURE_HOSTS = [
        "apply.workable.com",
        "apply.refline.ch",
        "applyglobal.deloitte.com",
        "wantapply.com",
        "api.lever.co",
        "example.com",
    ]

    def test_is_avature_host_matches(self):
        for host in self.AVATURE_HOSTS:
            assert _is_avature_host(host), host

    def test_is_avature_host_does_not_bleed(self):
        for host in self.NON_AVATURE_HOSTS:
            assert not _is_avature_host(host), host

    def test_is_avature_host_handles_empty(self):
        assert not _is_avature_host(None)
        assert not _is_avature_host("")

    async def test_avature_request_gets_richer_accept(self):
        """apply.deloitte.com requests must carry the rich browser
        Accept header (image/avif, signed-exchange) — not the thin
        default."""
        captured: dict[str, str] = {}

        def handler(request):
            captured.update(dict(request.headers))
            return httpx.Response(200, text="OK")

        client = create_http_client()
        client._transport = httpx.MockTransport(handler)
        await client.get("https://apply.deloitte.com/en_US/careers/JobDetail/X/1")
        await client.aclose()

        assert captured["accept"] == _AVATURE_ACCEPT
        assert "image/avif" in captured["accept"]
        assert "signed-exchange" in captured["accept"]
        # Sec-Fetch-* triad — strongest cheap navigation signal
        assert captured["sec-fetch-dest"] == "document"
        assert captured["sec-fetch-mode"] == "navigate"
        assert captured["sec-fetch-site"] == "none"
        assert captured["sec-fetch-user"] == "?1"
        assert captured["upgrade-insecure-requests"] == "1"
        assert captured["accept-language"].startswith("en-US")

    async def test_avature_net_subdomain_also_gets_override(self):
        """Other Avature-hosted boards (Bloomberg, DHL Consulting,
        Tesco) share the same WAF and need the same headers."""
        captured: dict[str, str] = {}

        def handler(request):
            captured["accept"] = request.headers.get("accept", "")
            captured["sec-fetch-dest"] = request.headers.get("sec-fetch-dest", "")
            return httpx.Response(200, text="OK")

        client = create_http_client()
        client._transport = httpx.MockTransport(handler)
        await client.get("https://bloomberg.avature.net/careers/JobDetail/X/1")
        await client.aclose()

        assert captured["accept"] == _AVATURE_ACCEPT
        assert captured["sec-fetch-dest"] == "document"

    async def test_non_avature_request_keeps_default_accept(self):
        """Don't bleed the override to other hosts — example.com keeps
        the project-default Accept and gets no Sec-Fetch-* headers."""
        captured: dict[str, str] = {}

        def handler(request):
            captured["accept"] = request.headers.get("accept", "")
            captured["sec-fetch-dest"] = request.headers.get("sec-fetch-dest", "")
            return httpx.Response(200, text="OK")

        client = create_http_client()
        client._transport = httpx.MockTransport(handler)
        await client.get("https://example.com/")
        await client.aclose()

        assert captured["accept"] == DEFAULT_ACCEPT
        assert captured["sec-fetch-dest"] == ""

    async def test_avature_does_not_clobber_explicit_accept(self):
        """Per-request Accept overrides (api_sniffer's
        ``application/json``, Lever's forced JSON) must still win even
        on Avature hosts."""
        captured: dict[str, str] = {}

        def handler(request):
            captured["accept"] = request.headers.get("accept", "")
            return httpx.Response(200, text="OK")

        client = create_http_client()
        client._transport = httpx.MockTransport(handler)
        await client.get(
            "https://bloomberg.avature.net/api/jobs",
            headers={"Accept": "application/json"},
        )
        await client.aclose()

        assert captured["accept"] == "application/json"

    async def test_logging_client_also_applies_avature_headers(self):
        """Workspace command uses ``create_logging_http_client`` —
        must have the same per-host header policy."""
        captured: dict[str, str] = {}

        def handler(request):
            captured["accept"] = request.headers.get("accept", "")
            return httpx.Response(200, text="OK")

        client, _log = create_logging_http_client()
        client._transport = httpx.MockTransport(handler)
        await client.get("https://apply.deloitte.com/CHCareers/JobDetail/X")
        await client.aclose()

        assert captured["accept"] == _AVATURE_ACCEPT


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
