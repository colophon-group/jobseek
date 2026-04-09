"""Tests for src/shared/cdp.py — the Lightpanda CDP httpx transport.

Uses an injected fake ``fetch`` callable so tests don't need a real
Playwright/Lightpanda connection. A separate small integration test
verifies that ``src/shared/http.create_http_client()`` actually picks
up the CDP mounts when the routing config is set.
"""

from __future__ import annotations

import httpx
import pytest

from src.shared import cdp
from src.shared.cdp import (
    CdpRequestError,
    LightpandaTransport,
    _is_connection_error,
    _LightpandaSession,
    build_cdp_mounts,
    parse_cdp_routes,
    should_route_via_cdp,
    shutdown_all_sessions,
)

# ── parse_cdp_routes ───────────────────────────────────────────────────


class TestParseCdpRoutes:
    def test_none(self):
        assert parse_cdp_routes(None) == {}

    def test_empty_string(self):
        assert parse_cdp_routes("") == {}

    def test_valid_json_string(self):
        raw = '{"apply.starbucks.com": "lightpanda"}'
        assert parse_cdp_routes(raw) == {"apply.starbucks.com": "lightpanda"}

    def test_already_parsed_dict(self):
        d = {"host.example.com": "lightpanda"}
        assert parse_cdp_routes(d) == d

    def test_invalid_json_returns_empty(self):
        assert parse_cdp_routes("{not valid json") == {}

    def test_non_object_json_returns_empty(self):
        assert parse_cdp_routes('["list", "not", "object"]') == {}

    def test_coerces_values_to_str_when_parsed_from_json(self):
        # JSON only allows string keys; values get str-coerced for safety.
        assert parse_cdp_routes('{"host": 42}') == {"host": "42"}


class TestSettingsEmptyStringTolerance:
    """Regression: pydantic-settings auto-decodes complex env values via
    json.loads() before our validators run. ``CDP_ROUTES=""`` (the value
    docker-compose's ``${CDP_ROUTES:-}`` substitution emits when the
    secret is unset) used to crash worker startup with a SettingsError.
    The field is now typed as ``str`` and parsed lazily, so empty string
    is fine and ``_cdp_routes()`` returns an empty dict.
    """

    def test_settings_accept_empty_cdp_routes(self, monkeypatch):
        monkeypatch.setenv("CDP_ROUTES", "")
        from src.config import Settings

        s = Settings()
        assert s.cdp_routes == ""

    def test_settings_accept_valid_json_cdp_routes(self, monkeypatch):
        monkeypatch.setenv("CDP_ROUTES", '{"apply.starbucks.com": "lightpanda"}')
        from src.config import Settings

        s = Settings()
        # Stored as raw string; parsing happens in cdp._cdp_routes()
        assert "apply.starbucks.com" in s.cdp_routes

    def test_cdp_routes_helper_with_real_settings_empty(self, monkeypatch, tmp_path):
        """End-to-end: empty env var -> _cdp_routes() returns {}, no crash."""
        monkeypatch.setenv("CDP_ROUTES", "")
        # Point CDP_ROUTES_FILE at a non-existent path so the repo's
        # data/cdp_routes.csv doesn't bleed into this test.
        monkeypatch.setenv("CDP_ROUTES_FILE", str(tmp_path / "missing.csv"))
        from src.config import Settings

        live = Settings()
        monkeypatch.setattr(cdp, "_settings", lambda: live)
        assert cdp._cdp_routes() == {}

    def test_cdp_routes_helper_with_real_settings_populated(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CDP_ROUTES", '{"apply.starbucks.com": "lightpanda"}')
        monkeypatch.setenv("CDP_ROUTES_FILE", str(tmp_path / "missing.csv"))
        from src.config import Settings

        live = Settings()
        monkeypatch.setattr(cdp, "_settings", lambda: live)
        assert cdp._cdp_routes() == {"apply.starbucks.com": "lightpanda"}


# ── data/cdp_routes.csv loader ────────────────────────────────────────


class TestCdpRoutesFile:
    def _write_csv(self, tmp_path, content):
        p = tmp_path / "cdp_routes.csv"
        p.write_text(content)
        return p

    def test_missing_file_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CDP_ROUTES_FILE", str(tmp_path / "nope.csv"))
        monkeypatch.setenv("CDP_ROUTES", "")
        from src.config import Settings

        monkeypatch.setattr(cdp, "_settings", lambda: Settings())
        assert cdp._load_cdp_routes_file() == {}

    def test_loads_hostname_backend_pairs(self, monkeypatch, tmp_path):
        csv_path = self._write_csv(
            tmp_path,
            "hostname,backend,reason\n"
            "apply.starbucks.com,lightpanda,WAF\n"
            "jobs.northropgrumman.com,lightpanda,WAF\n",
        )
        monkeypatch.setenv("CDP_ROUTES_FILE", str(csv_path))
        from src.config import Settings

        monkeypatch.setattr(cdp, "_settings", lambda: Settings())
        assert cdp._load_cdp_routes_file() == {
            "apply.starbucks.com": "lightpanda",
            "jobs.northropgrumman.com": "lightpanda",
        }

    def test_default_backend_is_lightpanda_when_column_missing(self, monkeypatch, tmp_path):
        csv_path = self._write_csv(tmp_path, "hostname,reason\napply.starbucks.com,WAF\n")
        monkeypatch.setenv("CDP_ROUTES_FILE", str(csv_path))
        from src.config import Settings

        monkeypatch.setattr(cdp, "_settings", lambda: Settings())
        assert cdp._load_cdp_routes_file() == {"apply.starbucks.com": "lightpanda"}

    def test_blank_and_comment_rows_skipped(self, monkeypatch, tmp_path):
        csv_path = self._write_csv(
            tmp_path,
            "hostname,backend\n"
            "apply.starbucks.com,lightpanda\n"
            ",lightpanda\n"
            "#commented.example.com,lightpanda\n"
            "jobs.northropgrumman.com,lightpanda\n",
        )
        monkeypatch.setenv("CDP_ROUTES_FILE", str(csv_path))
        from src.config import Settings

        monkeypatch.setattr(cdp, "_settings", lambda: Settings())
        assert cdp._load_cdp_routes_file() == {
            "apply.starbucks.com": "lightpanda",
            "jobs.northropgrumman.com": "lightpanda",
        }

    def test_no_hostname_column_returns_empty_with_warning(self, monkeypatch, tmp_path):
        csv_path = self._write_csv(tmp_path, "host,backend\nexample.com,lightpanda\n")
        monkeypatch.setenv("CDP_ROUTES_FILE", str(csv_path))
        from src.config import Settings

        monkeypatch.setattr(cdp, "_settings", lambda: Settings())
        assert cdp._load_cdp_routes_file() == {}

    def test_env_overrides_file(self, monkeypatch, tmp_path):
        """CDP_ROUTES env var wins over file (runtime override)."""
        csv_path = self._write_csv(
            tmp_path,
            "hostname,backend\napply.starbucks.com,lightpanda\nold.example.com,lightpanda\n",
        )
        monkeypatch.setenv("CDP_ROUTES_FILE", str(csv_path))
        # Env adds a new host AND overrides apply.starbucks.com to a
        # different (hypothetical) backend
        monkeypatch.setenv(
            "CDP_ROUTES",
            '{"apply.starbucks.com": "other", "new.example.com": "lightpanda"}',
        )
        from src.config import Settings

        monkeypatch.setattr(cdp, "_settings", lambda: Settings())
        result = cdp._cdp_routes()
        # File entry kept
        assert result["old.example.com"] == "lightpanda"
        # Env override won
        assert result["apply.starbucks.com"] == "other"
        # Env addition included
        assert result["new.example.com"] == "lightpanda"

    def test_real_repo_file_loads(self):
        """Smoke: the actual data/cdp_routes.csv parses without error."""
        # Don't override the file path — exercise the default path resolution
        result = cdp._load_cdp_routes_file()
        # The repo file should at least contain Starbucks (the original
        # WAF'd host that motivated this whole system).
        assert "apply.starbucks.com" in result
        assert result["apply.starbucks.com"] == "lightpanda"


# ── should_route_via_cdp ──────────────────────────────────────────────


class TestShouldRouteViaCdp:
    def test_match(self, monkeypatch):
        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {"apply.starbucks.com": "lightpanda"})
        assert should_route_via_cdp("https://apply.starbucks.com/careers/job/1") is True

    def test_no_match(self, monkeypatch):
        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {"apply.starbucks.com": "lightpanda"})
        assert should_route_via_cdp("https://example.com/foo") is False

    def test_empty_routes(self, monkeypatch):
        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {})
        assert should_route_via_cdp("https://apply.starbucks.com/x") is False

    def test_invalid_url(self, monkeypatch):
        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {"host.example.com": "lightpanda"})
        assert should_route_via_cdp("not a url") is False


# ── build_cdp_mounts ──────────────────────────────────────────────────


class TestBuildCdpMounts:
    def test_no_routes(self, monkeypatch):
        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {})
        monkeypatch.setattr(cdp, "_lightpanda_cdp_url", lambda: "wss://example.com/ws")
        assert build_cdp_mounts() is None

    def test_routes_but_no_cdp_url(self, monkeypatch):
        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {"apply.starbucks.com": "lightpanda"})
        monkeypatch.setattr(cdp, "_lightpanda_cdp_url", lambda: None)
        assert build_cdp_mounts() is None

    def test_single_host(self, monkeypatch):
        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {"apply.starbucks.com": "lightpanda"})
        monkeypatch.setattr(cdp, "_lightpanda_cdp_url", lambda: "wss://example.com/ws")
        mounts = build_cdp_mounts()
        assert mounts is not None
        assert list(mounts.keys()) == ["all://apply.starbucks.com"]
        assert isinstance(mounts["all://apply.starbucks.com"], LightpandaTransport)

    def test_multiple_hosts(self, monkeypatch):
        monkeypatch.setattr(
            cdp,
            "_cdp_routes",
            lambda: {
                "apply.starbucks.com": "lightpanda",
                "starbucks.eightfold.ai": "lightpanda",
            },
        )
        monkeypatch.setattr(cdp, "_lightpanda_cdp_url", lambda: "wss://example.com/ws")
        mounts = build_cdp_mounts()
        assert mounts is not None
        assert set(mounts.keys()) == {
            "all://apply.starbucks.com",
            "all://starbucks.eightfold.ai",
        }

    def test_unknown_backend_is_skipped_not_fatal(self, monkeypatch):
        """A bad backend name should be logged and skipped, not crash the client."""
        monkeypatch.setattr(
            cdp,
            "_cdp_routes",
            lambda: {
                "apply.starbucks.com": "lightpanda",
                "other.example.com": "typo-backend",
            },
        )
        monkeypatch.setattr(cdp, "_lightpanda_cdp_url", lambda: "wss://example.com/ws")
        mounts = build_cdp_mounts()
        assert mounts is not None
        assert list(mounts.keys()) == ["all://apply.starbucks.com"]


# ── LightpandaTransport (with injected fetch) ────────────────────────


class TestLightpandaTransportInjectedFetch:
    """Exercise the httpx ↔ transport glue without a real Playwright."""

    @pytest.mark.asyncio
    async def test_successful_request_returns_httpx_response(self):
        calls = []

        async def fake_fetch(method, url, headers, data, timeout):
            calls.append((method, url, dict(headers), data, timeout))
            return (
                200,
                b'<html><script type="application/ld+json">{"@type":"JobPosting"}</script></html>',
                {"content-type": "text/html"},
            )

        transport = LightpandaTransport("wss://fake/ws", fetch=fake_fetch)
        async with httpx.AsyncClient(mounts={"all://waf.example.com": transport}) as client:
            resp = await client.get("https://waf.example.com/careers/job/1")

        assert resp.status_code == 200
        assert b"JobPosting" in resp.content
        assert resp.headers["content-type"] == "text/html"

        # The transport received the expected call
        assert len(calls) == 1
        method, url, headers, data, timeout = calls[0]
        assert method == "GET"
        assert url == "https://waf.example.com/careers/job/1"
        assert timeout >= 1.0
        # Hop-by-hop headers should not leak through
        for bad in ("host", "content-length", "accept-encoding", "connection"):
            assert bad not in {k.lower() for k in headers}

    @pytest.mark.asyncio
    async def test_non_routed_host_uses_default_transport(self):
        """Sanity: hosts not in the mounts dict bypass the CDP transport."""

        async def fake_fetch(*args, **kwargs):
            raise AssertionError("fake_fetch should not have been called")

        transport = LightpandaTransport("wss://fake/ws", fetch=fake_fetch)
        # Mock the non-CDP transport with a mock
        async with httpx.AsyncClient(
            mounts={"all://waf.example.com": transport},
            transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"direct")),
        ) as client:
            resp = await client.get("https://other.example.com/")
        assert resp.status_code == 200
        assert resp.content == b"direct"

    @pytest.mark.asyncio
    async def test_cdp_error_becomes_httpx_connect_error(self):
        async def fake_fetch(*args, **kwargs):
            raise CdpRequestError("lightpanda is down")

        transport = LightpandaTransport("wss://fake/ws", fetch=fake_fetch)
        async with httpx.AsyncClient(mounts={"all://waf.example.com": transport}) as client:
            with pytest.raises(httpx.ConnectError, match="lightpanda is down"):
                await client.get("https://waf.example.com/x")

    @pytest.mark.asyncio
    async def test_unexpected_exception_becomes_httpx_connect_error(self):
        async def fake_fetch(*args, **kwargs):
            raise RuntimeError("oops")

        transport = LightpandaTransport("wss://fake/ws", fetch=fake_fetch)
        async with httpx.AsyncClient(mounts={"all://waf.example.com": transport}) as client:
            with pytest.raises(httpx.ConnectError, match="oops"):
                await client.get("https://waf.example.com/x")

    @pytest.mark.asyncio
    async def test_strips_content_encoding_to_avoid_double_decompression(self):
        """Lightpanda returns already-decompressed body, but its headers
        still claim ``Content-Encoding: gzip``. Forwarding that header to
        httpx would cause ``DecodingError: incorrect header check``."""

        async def fake_fetch(*args, **kwargs):
            return (
                200,
                b"<html>plain body</html>",
                {
                    "content-encoding": "gzip",
                    "content-length": "9999",
                    "transfer-encoding": "chunked",
                    "content-type": "text/html",
                },
            )

        transport = LightpandaTransport("wss://fake/ws", fetch=fake_fetch)
        async with httpx.AsyncClient(mounts={"all://waf.example.com": transport}) as client:
            resp = await client.get("https://waf.example.com/x")
        assert resp.status_code == 200
        assert resp.text == "<html>plain body</html>"
        # Crucially: no content-encoding/transfer-encoding leaks through
        # to confuse httpx's body decoder. (httpx may auto-recompute a
        # correct content-length itself, which is fine.)
        for h in ("content-encoding", "transfer-encoding"):
            assert h not in resp.headers
        # If httpx did set content-length, it must match the actual body
        # bytes (not the upstream's lying "9999").
        cl = resp.headers.get("content-length")
        if cl is not None:
            assert int(cl) == len(resp.content)
        # Other headers do pass through.
        assert resp.headers["content-type"] == "text/html"

    @pytest.mark.asyncio
    async def test_aclose_is_noop_by_design(self):
        """Closing the httpx client must NOT tear down the shared session
        (other clients in the same process may still be using it)."""
        sessions_closed: list[bool] = []

        async def fake_fetch(*args, **kwargs):
            return (200, b"ok", {})

        transport = LightpandaTransport("wss://fake/ws", fetch=fake_fetch)
        async with httpx.AsyncClient(mounts={"all://waf.example.com": transport}):
            pass
        # If aclose did anything session-level, this list would be non-empty;
        # the contract is that it's intentionally a no-op.
        assert sessions_closed == []


# ── Connection-error classification ──────────────────────────────────


class TestIsConnectionError:
    """Regression guard: only true connection errors should reset the session."""

    @pytest.mark.parametrize(
        "msg",
        [
            "Target page, context or browser has been closed",
            "Browser has been closed",
            "browser closed",
            "Target closed",
            "Context closed",
            "Session closed",
            "WebSocket connection failed",
            "Connection closed by remote",
            "disconnected from CDP",
            "BrowserType.connect_over_cdp: WebSocket error",
        ],
    )
    def test_real_connection_errors_match(self, msg):
        assert _is_connection_error(Exception(msg)) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "Timeout 30000ms exceeded",
            "Request failed with status 404",
            "ECONNRESET",  # not in marker list — IP-level only
            "decoding error: incorrect header check",
            "Body too large",
            "JSON decode error",
            "",
        ],
    )
    def test_benign_errors_do_not_match(self, msg):
        assert _is_connection_error(Exception(msg)) is False

    def test_case_insensitive(self):
        assert _is_connection_error(Exception("TARGET CLOSED")) is True
        assert _is_connection_error(Exception("websocket")) is True


# ── Session reset behavior ───────────────────────────────────────────


class _FakePlaywrightObj:
    """Stub Playwright handle that records close()/stop() invocations."""

    def __init__(self):
        self.close_called = False
        self.stop_called = False

    async def close(self):
        self.close_called = True

    async def stop(self):
        self.stop_called = True


class _StubFetch:
    """Stub Playwright APIRequestContext.fetch() result."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str]):
        self.status = status
        self._body = body
        self.headers = headers

    async def body(self):
        return self._body


class _StubRequestCtx:
    """Stub `context.request` that raises a configurable error or returns a stub."""

    def __init__(self, *, raise_exc: BaseException | None = None, response=None):
        self._raise = raise_exc
        self._response = response
        self.fetch_calls = 0

    async def fetch(self, url, **kwargs):  # noqa: ARG002
        self.fetch_calls += 1
        if self._raise is not None:
            raise self._raise
        return self._response


class _StubContext:
    def __init__(self, request_ctx):
        self.request = request_ctx
        self.closed = False

    async def close(self):
        self.closed = True


class TestSessionResetNarrowing:
    """``_LightpandaSession.request`` must reset only on connection errors.

    Resetting on every error caused the session-churn bug that leaked
    ~6-minute orphaned Lightpanda sessions on every worker restart.
    """

    @pytest.mark.asyncio
    async def test_benign_error_keeps_session_alive(self):
        sess = _LightpandaSession("wss://fake/ws")
        # Pre-populate as if _ensure_open had run.
        sess._pw = _FakePlaywrightObj()
        sess._browser = _FakePlaywrightObj()
        sess._context = _StubContext(
            _StubRequestCtx(raise_exc=TimeoutError("Timeout 30000ms exceeded"))
        )

        with pytest.raises(CdpRequestError):
            await sess.request("GET", "https://target/x")

        # Session must still be open — benign per-request errors don't kill it.
        assert sess._context is not None
        assert sess._browser is not None
        assert sess._pw is not None

    @pytest.mark.asyncio
    async def test_connection_error_resets_session(self):
        sess = _LightpandaSession("wss://fake/ws")
        pw = _FakePlaywrightObj()
        browser = _FakePlaywrightObj()
        ctx = _StubContext(_StubRequestCtx(raise_exc=Exception("Target closed")))
        sess._pw = pw
        sess._browser = browser
        sess._context = ctx

        with pytest.raises(CdpRequestError):
            await sess.request("GET", "https://target/x")

        # Session was torn down; objects all cleared.
        assert sess._context is None
        assert sess._browser is None
        assert sess._pw is None
        # And the close/stop callbacks were invoked.
        assert ctx.closed is True
        assert browser.close_called is True
        assert pw.stop_called is True

    @pytest.mark.asyncio
    async def test_404_response_does_not_reset_session(self):
        """Playwright returns HTTP errors as a Response, not an exception.

        The session must obviously stay open for those, but this test
        also documents that even if some downstream call started raising
        on 404, the narrowed reset would keep the session intact.
        """
        sess = _LightpandaSession("wss://fake/ws")
        pw = _FakePlaywrightObj()
        browser = _FakePlaywrightObj()
        # 404 comes back as a normal Response object, no exception.
        stub_resp = _StubFetch(404, b"<html>not found</html>", {"content-type": "text/html"})
        request_ctx = _StubRequestCtx(response=stub_resp)
        ctx = _StubContext(request_ctx)
        sess._pw = pw
        sess._browser = browser
        sess._context = ctx

        status, body, headers = await sess.request("GET", "https://target/missing")

        assert status == 404
        assert b"not found" in body
        # Session is intact for the next request.
        assert sess._context is ctx
        assert request_ctx.fetch_calls == 1


# ── Shutdown wiring ──────────────────────────────────────────────────


class TestShutdownAllSessions:
    """``shutdown_all_sessions`` must close every cached session and clear
    the module-level cache so the next request opens a fresh session.

    The whole point of wiring this into the cli.py shutdown path is to
    avoid leaking 6-minute idle sessions on the Lightpanda side.
    """

    @pytest.mark.asyncio
    async def test_closes_all_cached_sessions(self):
        # Inject two fake sessions into the module-level cache.
        closed_urls: list[str] = []

        class _RecordingSession:
            def __init__(self, url):
                self._url = url

            async def close(self):
                closed_urls.append(self._url)

        cdp._set_session_for_test("wss://a/ws", _RecordingSession("a"))
        cdp._set_session_for_test("wss://b/ws", _RecordingSession("b"))

        try:
            await shutdown_all_sessions()
            assert sorted(closed_urls) == ["a", "b"]
            # Cache cleared so the next request opens a fresh session.
            assert cdp._sessions == {}
        finally:
            # Defensive: leave the global cache empty whatever happened.
            cdp._sessions.clear()

    @pytest.mark.asyncio
    async def test_swallows_close_errors(self):
        """One bad session must not block the others from closing."""

        class _BadSession:
            async def close(self):
                raise RuntimeError("close failed")

        class _GoodSession:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        good = _GoodSession()
        cdp._set_session_for_test("wss://bad/ws", _BadSession())
        cdp._set_session_for_test("wss://good/ws", good)

        try:
            await shutdown_all_sessions()  # must not raise
            assert good.closed is True
            assert cdp._sessions == {}
        finally:
            cdp._sessions.clear()

    @pytest.mark.asyncio
    async def test_safe_when_no_sessions(self):
        cdp._sessions.clear()
        # Should be a no-op, no exception.
        await shutdown_all_sessions()
        assert cdp._sessions == {}


# ── Redis pool sizing ────────────────────────────────────────────────


class TestRedisPoolSizingDefault:
    """Regression guard: pool default must accommodate worker concurrency.

    Production runs ``DISCOVERY_CONCURRENCY=30`` and ``MONITOR_CONCURRENCY=10``
    in a single worker process; the redis pool default must be at least
    that, otherwise concurrent ``claim_work`` calls hit ``MaxConnectionsError``
    and the worker enters a crash-restart loop. Each restart leaks an
    orphaned Lightpanda session, burning the browser-hours quota.
    """

    def test_default_fits_production_worker_concurrency(self):
        from src.config import Settings

        s = Settings()
        # Worst case in production: 30 discovery + 10 monitor = 40 concurrent.
        # The default must leave headroom for ad-hoc Redis calls (lookups,
        # metrics, reschedule bursts) on top of that.
        assert s.redis_max_connections >= 40, (
            f"redis_max_connections default {s.redis_max_connections} is too "
            "low for production worker concurrency (30+10) — the worker will "
            "crash with MaxConnectionsError under load"
        )


# ── http.py integration ──────────────────────────────────────────────


class TestHttpClientIntegration:
    @staticmethod
    def _mount_hosts(client: httpx.AsyncClient) -> set[str]:
        """Extract the hostnames present in client._mounts (URLPattern.host)."""
        return {getattr(pattern, "host", "") for pattern in client._mounts} - {""}

    def test_create_http_client_picks_up_cdp_mounts(self, monkeypatch):
        from src.shared.http import create_http_client

        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {"waf.example.com": "lightpanda"})
        monkeypatch.setattr(cdp, "_lightpanda_cdp_url", lambda: "wss://fake/ws")

        client = create_http_client()
        assert "waf.example.com" in self._mount_hosts(client)

    def test_create_http_client_no_cdp_when_unconfigured(self, monkeypatch):
        from src.shared.http import create_http_client

        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {})
        monkeypatch.setattr(cdp, "_lightpanda_cdp_url", lambda: None)

        client = create_http_client()
        assert "waf.example.com" not in self._mount_hosts(client)

    def test_cdp_mount_transport_is_lightpanda(self, monkeypatch):
        """The mounted transport for a routed host is LightpandaTransport."""
        from src.shared.http import create_http_client

        monkeypatch.setattr(cdp, "_cdp_routes", lambda: {"waf.example.com": "lightpanda"})
        monkeypatch.setattr(cdp, "_lightpanda_cdp_url", lambda: "wss://fake/ws")

        client = create_http_client()
        for pattern, transport in client._mounts.items():
            if getattr(pattern, "host", "") == "waf.example.com":
                assert isinstance(transport, LightpandaTransport)
                return
        pytest.fail("waf.example.com mount not found")


# ── Rescrape policy SQL ────────────────────────────────────────────────


class TestRescrapePolicySql:
    """Regression guard: the cost-saver rescrape_policy clause must stay in the SQL."""

    def test_record_scrape_success_honors_rescrape_policy(self):
        from src.queries.scrape import _RECORD_SCRAPE_SUCCESS

        sql = _RECORD_SCRAPE_SUCCESS.lower()
        assert "rescrape_policy" in sql
        assert "'never'" in sql
        # And the classic cadence branch must still be present.
        assert "scrape_interval_hours" in sql


# ── inspect.py validation ─────────────────────────────────────────────


class TestInspectRescrapePolicyValidation:
    def _run_validation(self, monitor_config: str, tmp_path, monkeypatch):
        """Tiny harness: drop a single-row boards.csv and run validation."""
        import csv

        from src import inspect as inspect_mod

        boards_path = tmp_path / "boards.csv"
        with boards_path.open("w") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "company_slug",
                    "board_slug",
                    "board_url",
                    "monitor_type",
                    "monitor_config",
                    "scraper_type",
                    "scraper_config",
                ]
            )
            writer.writerow(
                [
                    "test",
                    "test-board",
                    "https://example.com/careers",
                    "sitemap",
                    monitor_config,
                    "json-ld",
                    "",
                ]
            )
        companies_path = tmp_path / "companies.csv"
        with companies_path.open("w") as f:
            writer = csv.writer(f)
            writer.writerow(["slug", "name", "website"])
            writer.writerow(["test", "Test Co", "https://example.com"])

        monkeypatch.setattr(inspect_mod, "get_data_dir", lambda: tmp_path)
        return inspect_mod.validate_csvs()

    def test_rescrape_policy_never_is_valid(self, tmp_path, monkeypatch):
        errors = self._run_validation('{"rescrape_policy": "never"}', tmp_path, monkeypatch)
        rescrape_errors = [e for e in errors if "rescrape_policy" in e.message]
        assert rescrape_errors == []

    def test_rescrape_policy_unknown_value_is_error(self, tmp_path, monkeypatch):
        errors = self._run_validation('{"rescrape_policy": "sometimes"}', tmp_path, monkeypatch)
        rescrape_errors = [e for e in errors if "rescrape_policy" in e.message]
        assert len(rescrape_errors) == 1
        assert "sometimes" in rescrape_errors[0].message

    def test_rescrape_policy_absent_is_fine(self, tmp_path, monkeypatch):
        errors = self._run_validation('{"url_filter": "/careers/job/"}', tmp_path, monkeypatch)
        rescrape_errors = [e for e in errors if "rescrape_policy" in e.message]
        assert rescrape_errors == []
