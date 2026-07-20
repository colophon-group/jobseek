from __future__ import annotations

import ssl

import httpx
import pytest

from src.shared.http import (
    DEFAULT_ACCEPT,
    DEFAULT_USER_AGENT,
    RequestHostTrackingTransport,
    _client_kwargs,
    _make_ssl_context,
    client_for,
    create_http_client,
    create_logging_http_client,
    create_nossl_http_client,
    is_avature_job_detail_url,
    track_request_hosts,
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


class TestRequestHostTracking:
    async def test_transport_records_actual_redirect_hosts_without_network(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "8.8.8.8":
                return httpx.Response(302, headers={"location": "http://1.1.1.1/final"})
            return httpx.Response(200, text="ok")

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            with track_request_hosts() as tracker:
                response = await client.get("http://8.8.8.8/start")

        assert response.status_code == 200
        assert tracker.hosts == {"8.8.8.8", "1.1.1.1"}
        assert tracker.last_host == "1.1.1.1"

    async def test_tracker_classifies_only_transient_upstream_statuses(self):
        statuses = iter((503, 200))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(next(statuses), request=request)

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                response = await client.get("https://outage.example/first")
                assert response.status_code == 503
                assert tracker.transient_failure_host == "outage.example"

                response = await client.get("https://outage.example/recovered")
                assert response.status_code == 200
                assert tracker.transient_failure_host is None

    async def test_tracker_classifies_transport_errors(self):
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("upstream timed out", request=request)

        transport = RequestHostTrackingTransport(httpx.MockTransport(timeout))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                with pytest.raises(httpx.ConnectTimeout):
                    await client.get("https://timeout.example/jobs")

        assert tracker.transient_failure_host == "timeout.example"
        assert tracker.last_transport_error == "ConnectTimeout"

    async def test_tracker_classifies_avature_job_detail_406_as_transient(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(406, request=request)

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                await client.get("https://jobs.totalenergies.com/en_US/careers/JobDetail/Role/123")

        assert tracker.transient_failure_host == "jobs.totalenergies.com"
        assert tracker.last_url and "/JobDetail/" in tracker.last_url

    async def test_tracker_keeps_generic_406_non_transient(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(406, request=request)

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                await client.get("https://api.example.com/v1/search")

        assert tracker.transient_failure_host is None


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.totalenergies.com/en_US/careers/JobDetail/Role/123",
        "https://apply.deloitte.co.uk/UKCareers/JobDetail/Role/123",
        "https://careers.tesco.com/en_GB/careersmarketplace/JobDetail/Role/123",
        "https://bloomberg.avature.net/jobs/JobDetail/Role/123",
    ],
)
def test_recognizes_avature_job_detail_routes(url: str) -> None:
    assert is_avature_job_detail_url(url) is True


def test_does_not_treat_generic_job_detail_route_as_avature() -> None:
    assert is_avature_job_detail_url("https://example.com/jobs/JobDetail/Role/123") is False


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


class TestClientFor:
    """``client_for(http, config)`` is a thin async-context-manager that
    dedupes the skip_ssl branch across monitor_one / monitor_one_stream /
    scrape_one (#2705). Two branches: skip_ssl truthy -> a fresh nossl
    client (proxied when ``proxy`` is also truthy); falsy -> the outer
    client passed in, unchanged."""

    async def test_no_skip_ssl_yields_outer_client(self):
        outer = httpx.AsyncClient()
        try:
            async with client_for(outer, {}) as client:
                assert client is outer
            async with client_for(outer, {"skip_ssl": False}) as client:
                assert client is outer
        finally:
            await outer.aclose()

    async def test_skip_ssl_yields_fresh_nossl_client(self, monkeypatch):
        nossl_clients: list[httpx.AsyncClient] = []
        observed_use_proxy: list[bool] = []
        real_factory = create_nossl_http_client

        def tracking_factory(*, use_proxy: bool = False) -> httpx.AsyncClient:
            observed_use_proxy.append(use_proxy)
            client = real_factory(use_proxy=use_proxy)
            nossl_clients.append(client)
            return client

        monkeypatch.setattr("src.shared.http.create_nossl_http_client", tracking_factory)

        outer = httpx.AsyncClient()
        try:
            async with client_for(outer, {"skip_ssl": True}) as client:
                assert client is not outer
                assert client is nossl_clients[0]
        finally:
            await outer.aclose()

        assert observed_use_proxy == [False]

    async def test_skip_ssl_with_proxy_threads_use_proxy(self, monkeypatch):
        """Regression guard for #2659 (the bug PR #2682 fixed): when both
        skip_ssl and proxy are set, the nossl client must be built with
        use_proxy=True so the API request still routes through the proxy."""
        observed_use_proxy: list[bool] = []
        real_factory = create_nossl_http_client

        def tracking_factory(*, use_proxy: bool = False) -> httpx.AsyncClient:
            observed_use_proxy.append(use_proxy)
            return real_factory(use_proxy=use_proxy)

        monkeypatch.setattr("src.shared.http.create_nossl_http_client", tracking_factory)

        outer = httpx.AsyncClient()
        try:
            async with client_for(outer, {"skip_ssl": True, "proxy": True}):
                pass
        finally:
            await outer.aclose()

        assert observed_use_proxy == [True]


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
