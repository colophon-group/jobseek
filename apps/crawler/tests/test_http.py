from __future__ import annotations

import ssl

import httpx
import pytest

from src.shared.http import (
    DEFAULT_ACCEPT,
    DEFAULT_USER_AGENT,
    WORKDAY_LIST_303_INCIDENT,
    ProxyAwareAsyncClient,
    RequestHostTrackingTransport,
    RotatingProxyTransport,
    _client_kwargs,
    _make_ssl_context,
    client_for,
    create_http_client,
    create_logging_http_client,
    create_nossl_http_client,
    is_avature_job_detail_url,
    mark_provider_incident,
    mark_transient_response_failure,
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
        assert "Chrome/151" in client.headers["user-agent"]
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

    async def test_rejects_non_ascii_cookie_without_dropping_valid_session_cookie(self):
        """A malformed locale cookie must not poison the shared client.

        Hireserve portals can emit a French month name in ``Set-Cookie``.
        Python's cookie jar accepts it, while httpx cannot serialize it into
        the next ASCII HTTP header. The valid session cookie beside it must
        still survive because some legacy boards require that state.
        """
        seen_cookie_headers: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_cookie_headers.append(request.headers.get("cookie", ""))
            if len(seen_cookie_headers) == 1:
                return httpx.Response(
                    200,
                    headers=[
                        (b"set-cookie", b"sessionid1=abc123; Path=/"),
                        (b"set-cookie", b"lastaccesstime1=25-AO\xdbT-2026; Path=/"),
                    ],
                    text="first",
                )
            return httpx.Response(200, text="second")

        client = create_http_client()
        client._transport = httpx.MockTransport(handler)
        try:
            await client.get("https://example.com/first")
            await client.get("https://example.com/second")
        finally:
            await client.aclose()

        assert seen_cookie_headers == ["", "sessionid1=abc123"]

    @pytest.mark.parametrize(
        "set_cookie",
        [
            b"barecookie; Path=/",
            b"bad name=value; Path=/",
            b"badvalue=has space; Path=/",
            b"badvalue=has,comma; Path=/",
            b"badvalue=has\\backslash; Path=/",
            b"badvalue=has\x7fcontrol; Path=/",
            b'badvalue="has space"; Path=/',
            b'badvalue="unterminated; Path=/',
        ],
    )
    async def test_rejects_non_rfc6265_cookies(self, set_cookie: bytes):
        seen_cookie_headers: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_cookie_headers.append(request.headers.get("cookie", ""))
            if len(seen_cookie_headers) == 1:
                return httpx.Response(
                    200,
                    headers=[
                        (b"set-cookie", b"sessionid1=abc-._~:/?@[]; Path=/"),
                        (b"set-cookie", set_cookie),
                    ],
                )
            return httpx.Response(200)

        client = create_http_client()
        client._transport = httpx.MockTransport(handler)
        try:
            await client.get("https://example.com/first")
            await client.get("https://example.com/second")
        finally:
            await client.aclose()

        assert seen_cookie_headers == ["", "sessionid1=abc-._~:/?@[]"]

    async def test_accepts_empty_and_quoted_rfc6265_values(self):
        seen_cookie_headers: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_cookie_headers.append(request.headers.get("cookie", ""))
            if len(seen_cookie_headers) == 1:
                return httpx.Response(
                    200,
                    headers=[
                        (b"set-cookie", b"empty=; Path=/"),
                        (b"set-cookie", b'quoted="abc-._~:/?@[]"; Path=/'),
                    ],
                )
            return httpx.Response(200)

        client = create_http_client()
        client._transport = httpx.MockTransport(handler)
        try:
            await client.get("https://example.com/first")
            await client.get("https://example.com/second")
        finally:
            await client.aclose()

        assert seen_cookie_headers == ["", 'empty=; quoted="abc-._~:/?@[]"']


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

    async def test_tracker_classifies_generic_403_as_transient(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, request=request)

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                await client.get("https://blocked.example/jobs/123")

        assert tracker.transient_failure_host == "blocked.example"

    async def test_tracker_keeps_avature_job_detail_403_non_transient(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, request=request)

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                await client.get("https://jobs.example.com/en_US/careers/JobDetail/Role/123")

        assert tracker.transient_failure_host is None

    async def test_new_response_clears_promoted_application_failure(self):
        responses = iter((200, 200))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(next(responses), request=request)

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                first = await client.get("https://degraded.example/detail")
                mark_transient_response_failure(str(first.url), reason="provider_invalid_payload")
                assert tracker.transient_failure_host == "degraded.example"

                await client.get("https://degraded.example/recovered")

        assert tracker.last_application_error is None
        assert tracker.transient_failure_host is None

    async def test_provider_incident_retains_its_origin_across_later_responses(self):
        """Concurrent multi-site requests cannot overwrite terminal evidence."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request)

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                await client.get("https://failed.wd1.example/jobs")
                mark_provider_incident(
                    "https://failed.wd1.example/jobs",
                    incident=WORKDAY_LIST_303_INCIDENT,
                )
                await client.get("https://later.wd2.example/jobs")

        assert tracker.last_provider_incident == WORKDAY_LIST_303_INCIDENT
        assert tracker.last_provider_incident_host == "failed.wd1.example"
        assert tracker.last_host == "later.wd2.example"


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


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.example.com/careers/JobDetail?jobId=123",
        "https://jobs.example.com/jobs/FolderDetail/Role/123",
        "https://jobs.example.com/jobs/PipelineDetail?pipelineId=123",
    ],
)
def test_recognizes_branded_avature_detail_variants(url: str) -> None:
    assert is_avature_job_detail_url(url) is True


class TestProxyOptIn:
    """Test the public contract: ``_client_kwargs`` is the exact dict we
    pass to ``httpx.AsyncClient(**kwargs)``. Asserting on it is robust
    against httpx internal changes (no probing of ``client._mounts``).
    """

    URL = "http://u:p@proxy.example:7000"

    def test_no_proxy_by_default(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_urls", [])
        monkeypatch.setattr(config.settings, "webshare_proxy_url", self.URL)
        kwargs = _client_kwargs(verify=True, use_proxy=False)
        assert "transport" not in kwargs

    def test_use_proxy_true_attaches_rotating_transport(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_urls", [])
        monkeypatch.setattr(config.settings, "webshare_proxy_url", self.URL)
        kwargs = _client_kwargs(verify=True, use_proxy=True)
        assert isinstance(kwargs["transport"], RotatingProxyTransport)

    def test_use_proxy_true_noop_when_provider_none(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "none")
        monkeypatch.setattr(config.settings, "webshare_proxy_urls", [])
        monkeypatch.setattr(config.settings, "webshare_proxy_url", self.URL)
        kwargs = _client_kwargs(verify=True, use_proxy=True)
        assert "transport" not in kwargs

    def test_use_proxy_true_fails_closed_when_url_empty(self, monkeypatch):
        from src import config
        from src.shared.proxy import ProxyConfigurationError

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_urls", [])
        monkeypatch.setattr(config.settings, "webshare_proxy_url", "")
        with pytest.raises(ProxyConfigurationError, match="no usable endpoint"):
            _client_kwargs(verify=True, use_proxy=True)

    async def test_create_http_client_accepts_use_proxy_kwarg(self, monkeypatch):
        """Sanity: the factory builds a live AsyncClient with the proxy attached."""
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_urls", [])
        monkeypatch.setattr(config.settings, "webshare_proxy_url", self.URL)
        client = create_http_client(use_proxy=True)
        try:
            assert isinstance(client, httpx.AsyncClient)
        finally:
            await client.aclose()


class TestRotatingProxyTransport:
    URLS = (
        "http://user:secret@p.webshare.io:10000",
        "http://user:secret@p.webshare.io:10001",
    )

    async def test_rotates_each_top_level_request(self):
        from src.shared.proxy import PoolProxyProvider

        provider = PoolProxyProvider("webshare", self.URLS)
        slots: list[int] = []

        def factory(selection):
            async def handler(request):
                slots.append(selection.pool_slot)
                return httpx.Response(200, request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(
            provider,
            verify=True,
            transport_factory=factory,
        )
        async with ProxyAwareAsyncClient(transport=transport) as client:
            for _ in range(4):
                assert (await client.get("https://target.example/jobs")).status_code == 200

        assert slots == [0, 1, 0, 1]

    async def test_target_block_quarantines_only_slot_origin_pair(self):
        from src.shared.proxy import PoolProxyProvider

        provider = PoolProxyProvider("webshare", self.URLS)
        slots: list[tuple[int, str]] = []

        def factory(selection):
            async def handler(request):
                slots.append((selection.pool_slot, request.url.host))
                status = 403 if len(slots) == 1 else 200
                return httpx.Response(status, request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(
            provider,
            verify=True,
            transport_factory=factory,
        )
        async with ProxyAwareAsyncClient(transport=transport) as client:
            assert (await client.get("https://blocked.example/jobs")).status_code == 403
            assert (await client.get("https://blocked.example/jobs")).status_code == 200
            assert (await client.get("https://blocked.example/jobs")).status_code == 200
            assert (await client.get("https://other.example/jobs")).status_code == 200

        assert slots == [
            (0, "blocked.example"),
            (1, "blocked.example"),
            (1, "blocked.example"),
            (0, "other.example"),
        ]

    async def test_redirect_chain_keeps_one_exit_then_next_call_rotates(self):
        from src.shared.proxy import PoolProxyProvider

        provider = PoolProxyProvider("webshare", self.URLS)
        slots: list[int] = []

        def factory(selection):
            async def handler(request):
                slots.append(selection.pool_slot)
                if request.url.path == "/start":
                    return httpx.Response(
                        302,
                        headers={"location": "/end"},
                        request=request,
                    )
                return httpx.Response(200, request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(
            provider,
            verify=True,
            transport_factory=factory,
        )
        async with ProxyAwareAsyncClient(transport=transport, follow_redirects=True) as client:
            assert (await client.get("https://target.example/start")).status_code == 200
            assert (await client.get("https://target.example/end")).status_code == 200

        assert slots == [0, 0, 1]

    async def test_half_open_redirect_keeps_probe_exclusive_until_final_response(self):
        from src.shared.proxy import PoolProxyProvider, ProxyPoolExhaustedError

        now = [0.0]
        provider = PoolProxyProvider(
            "webshare",
            (self.URLS[0],),
            clock=lambda: now[0],
        )
        failed = provider.select(origin="target.example", transport="httpx")
        provider.report_failure(
            failed,
            origin="target.example",
            reason="proxy_auth",
        )
        now[0] = 60 * 60

        def factory(_selection):
            async def handler(request):
                if request.url.path == "/start":
                    return httpx.Response(302, headers={"location": "/end"}, request=request)
                with pytest.raises(ProxyPoolExhaustedError):
                    provider.select(origin="target.example", transport="httpx")
                return httpx.Response(200, request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(provider, verify=True, transport_factory=factory)
        async with ProxyAwareAsyncClient(transport=transport, follow_redirects=True) as client:
            assert (await client.get("https://target.example/start")).status_code == 200
            assert provider.select(origin="target.example", transport="httpx").half_open is False

    async def test_half_open_redirect_final_block_increases_origin_cooldown(self):
        from src.shared.proxy import PoolProxyProvider, ProxyPoolExhaustedError

        now = [0.0]
        provider = PoolProxyProvider(
            "webshare",
            (self.URLS[0],),
            clock=lambda: now[0],
        )
        failed = provider.select(origin="target.example", transport="httpx")
        provider.report_failure(failed, origin="target.example", reason="origin_block")
        now[0] = 15 * 60

        def factory(_selection):
            async def handler(request):
                if request.url.path == "/start":
                    return httpx.Response(302, headers={"location": "/end"}, request=request)
                return httpx.Response(403, request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(provider, verify=True, transport_factory=factory)
        async with ProxyAwareAsyncClient(transport=transport, follow_redirects=True) as client:
            assert (await client.get("https://target.example/start")).status_code == 403

        now[0] = 30 * 60
        with pytest.raises(ProxyPoolExhaustedError):
            provider.select(origin="target.example", transport="httpx")
        now[0] = 45 * 60
        assert provider.select(origin="target.example", transport="httpx").half_open is True

    async def test_streamed_half_open_probe_recovers_only_after_body_eof(self):
        from src.shared.proxy import PoolProxyProvider, ProxyPoolExhaustedError

        now = [0.0]
        provider = PoolProxyProvider(
            "webshare",
            (self.URLS[0],),
            clock=lambda: now[0],
        )
        failed = provider.select(origin="target.example", transport="httpx")
        provider.report_failure(failed, origin="target.example", reason="proxy_auth")
        now[0] = 60 * 60

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"ok"

        def factory(_selection):
            async def handler(request):
                return httpx.Response(200, stream=Body(), request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(provider, verify=True, transport_factory=factory)
        async with ProxyAwareAsyncClient(transport=transport) as client:
            request = client.build_request("GET", "https://target.example/jobs")
            response = await client.send(request, stream=True)
            with pytest.raises(ProxyPoolExhaustedError):
                provider.select(origin="target.example", transport="httpx")
            assert await response.aread() == b"ok"
            assert provider.select(origin="target.example", transport="httpx").half_open is False

    @staticmethod
    def _cross_origin_half_open_provider():
        from src.shared.proxy import PoolProxyProvider

        now = [0.0]
        provider = PoolProxyProvider(
            "webshare",
            (TestRotatingProxyTransport.URLS[0],),
            clock=lambda: now[0],
        )
        failed = provider.select(origin="a.example", transport="httpx")
        provider.report_failure(failed, origin="a.example", reason="origin_block")
        now[0] = 15 * 60
        return provider

    async def test_cross_origin_redirect_success_recovers_selected_origin_probe(self):
        provider = self._cross_origin_half_open_provider()

        def factory(_selection):
            async def handler(request):
                if request.url.host == "a.example":
                    return httpx.Response(
                        302,
                        headers={"location": "https://b.example/end"},
                        request=request,
                    )
                return httpx.Response(200, request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(provider, verify=True, transport_factory=factory)
        async with ProxyAwareAsyncClient(transport=transport, follow_redirects=True) as client:
            assert (await client.get("https://a.example/start")).status_code == 200

        assert provider.select(origin="a.example", transport="httpx").half_open is False

    async def test_cross_origin_redirect_block_recovers_selected_and_quarantines_final(self):
        from src.shared.proxy import ProxyPoolExhaustedError

        provider = self._cross_origin_half_open_provider()

        def factory(_selection):
            async def handler(request):
                if request.url.host == "a.example":
                    return httpx.Response(
                        302,
                        headers={"location": "https://b.example/end"},
                        request=request,
                    )
                return httpx.Response(403, request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(provider, verify=True, transport_factory=factory)
        async with ProxyAwareAsyncClient(transport=transport, follow_redirects=True) as client:
            assert (await client.get("https://a.example/start")).status_code == 403

        assert provider.select(origin="a.example", transport="httpx").half_open is False
        with pytest.raises(ProxyPoolExhaustedError):
            provider.select(origin="b.example", transport="httpx")

    async def test_cross_origin_stream_early_close_releases_selected_origin_probe(self):
        provider = self._cross_origin_half_open_provider()

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"unused"

        def factory(_selection):
            async def handler(request):
                if request.url.host == "a.example":
                    return httpx.Response(
                        302,
                        headers={"location": "https://b.example/end"},
                        request=request,
                    )
                return httpx.Response(200, stream=Body(), request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(provider, verify=True, transport_factory=factory)
        async with ProxyAwareAsyncClient(transport=transport, follow_redirects=True) as client:
            request = client.build_request("GET", "https://a.example/start")
            response = await client.send(request, stream=True)
            await response.aclose()

        assert provider.select(origin="a.example", transport="httpx").half_open is True

    async def test_cross_origin_stream_error_recovers_selected_and_quarantines_final(self):
        from src.shared.proxy import ProxyPoolExhaustedError

        provider = self._cross_origin_half_open_provider()

        class BrokenBody(httpx.AsyncByteStream):
            def __init__(self, request):
                self._request = request

            async def __aiter__(self):
                raise httpx.ReadError("body failed", request=self._request)
                yield b""  # pragma: no cover

        def factory(_selection):
            async def handler(request):
                if request.url.host == "a.example":
                    return httpx.Response(
                        302,
                        headers={"location": "https://b.example/end"},
                        request=request,
                    )
                return httpx.Response(200, stream=BrokenBody(request), request=request)

            return httpx.MockTransport(handler)

        transport = RotatingProxyTransport(provider, verify=True, transport_factory=factory)
        async with ProxyAwareAsyncClient(transport=transport, follow_redirects=True) as client:
            request = client.build_request("GET", "https://a.example/start")
            response = await client.send(request, stream=True)
            with pytest.raises(httpx.ReadError):
                await response.aread()

        assert provider.select(origin="a.example", transport="httpx").half_open is False
        with pytest.raises(ProxyPoolExhaustedError):
            provider.select(origin="b.example", transport="httpx")

    async def test_origin_transport_failure_does_not_close_shared_slot_transport(self):
        from src.shared.proxy import PoolProxyProvider

        provider = PoolProxyProvider("webshare", (self.URLS[0],))

        class TrackedTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.closed = 0

            async def handle_async_request(self, request):
                raise httpx.ConnectError("target refused", request=request)

            async def aclose(self):
                self.closed += 1

        inner = TrackedTransport()
        transport = RotatingProxyTransport(
            provider,
            verify=True,
            transport_factory=lambda _selection: inner,
        )
        client = ProxyAwareAsyncClient(transport=transport)
        with pytest.raises(httpx.ConnectError):
            await client.get("https://target.example/jobs")
        assert inner.closed == 0
        await client.aclose()
        assert inner.closed == 1


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
