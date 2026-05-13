"""Tests for the SSRF guard (closes #3214).

Two layers:

  - Unit tests on the pure classifier (:func:`is_private_ip`) and the
    transport wrapper (:class:`SSRFGuardedTransport`). No real network.
  - Integration tests on ``create_http_client`` — assert that a board
    URL pointing at ``http://127.0.0.1`` is rejected through the same
    factory monitors / scrapers use.

The transport-level tests stub out :func:`socket.getaddrinfo` so we can
deterministically simulate a hostname that resolves to a private IP
(the classic DNS-rebinding precursor) without hitting the resolver.

@see colophon-group/jobseek#3214
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import httpx
import pytest

from src.shared.http import create_http_client
from src.shared.ssrf import (
    SSRFError,
    SSRFGuardedTransport,
    _gather_internal_hosts,
    is_internal_host_allowed,
    is_private_ip,
    resolve_host_or_raise,
    validate_request_url,
)


class TestIsPrivateIp:
    """The classifier must cover every range called out by #3214."""

    @pytest.mark.parametrize(
        "addr",
        [
            # Loopback v4
            "127.0.0.1",
            "127.255.255.254",
            # RFC1918 — 10/8
            "10.0.0.5",
            "10.255.255.255",
            # RFC1918 — 172.16/12
            "172.16.0.1",
            "172.31.255.254",
            # RFC1918 — 192.168/16
            "192.168.0.1",
            "192.168.255.254",
            # Link-local v4 — covers cloud metadata 169.254.169.254
            "169.254.0.1",
            "169.254.169.254",
            # IPv6 loopback
            "::1",
            # IPv6 ULA — fc00::/7
            "fc00::1",
            "fd12:3456:789a::1",
            # IPv6 link-local — fe80::/10
            "fe80::1",
            # IPv4-mapped IPv6 — must re-classify the embedded v4
            "::ffff:10.0.0.1",
            "::ffff:127.0.0.1",
            # Unspecified
            "0.0.0.0",
            "::",
        ],
    )
    def test_blocked_ranges(self, addr: str) -> None:
        assert is_private_ip(addr), f"{addr} should be flagged private"

    @pytest.mark.parametrize(
        "addr",
        [
            "8.8.8.8",  # Google DNS
            "1.1.1.1",  # Cloudflare DNS
            "93.184.216.34",  # example.com
            "2606:4700:4700::1111",  # Cloudflare v6
        ],
    )
    def test_public_addresses(self, addr: str) -> None:
        assert not is_private_ip(addr), f"{addr} should be considered public"

    def test_malformed_input_fails_closed(self) -> None:
        """Unparseable input is rejected — a permissive default would
        defeat the whole point of the guard."""
        assert is_private_ip("not-an-ip")
        assert is_private_ip("")
        assert is_private_ip("999.999.999.999")


class TestValidateRequestUrl:
    """Direct guard surface — used by the transport wrapper."""

    def test_rejects_loopback_literal(self) -> None:
        with pytest.raises(SSRFError) as exc:
            validate_request_url("http://127.0.0.1/admin")
        assert "loopback" in exc.value.reason

    def test_rejects_rfc1918_literal(self) -> None:
        with pytest.raises(SSRFError) as exc:
            validate_request_url("http://10.0.0.5/admin")
        assert "private" in exc.value.reason

    def test_rejects_link_local_metadata(self) -> None:
        """169.254.169.254 is the EC2 / GCP metadata endpoint — a
        canonical SSRF target. Must be blocked even though the issue
        only calls out 169.254/16 as a CIDR."""
        with pytest.raises(SSRFError) as exc:
            validate_request_url("http://169.254.169.254/latest/meta-data/")
        assert exc.value.reason.startswith("private_ip_literal:")

    def test_rejects_ipv6_loopback(self) -> None:
        with pytest.raises(SSRFError):
            validate_request_url("http://[::1]/admin")

    def test_rejects_ipv6_ula(self) -> None:
        with pytest.raises(SSRFError):
            validate_request_url("http://[fc00::1]/admin")

    def test_rejects_non_http_scheme(self) -> None:
        for url in ("file:///etc/passwd", "gopher://example.com/", "ftp://example.com/"):
            with pytest.raises(SSRFError) as exc:
                validate_request_url(url)
            assert "scheme" in exc.value.reason

    def test_allows_public_literal(self) -> None:
        # No DNS lookup, no exception.
        validate_request_url("http://8.8.8.8/")

    def test_hostname_path_goes_through_resolver(self) -> None:
        """When the host is not an IP literal we must consult the
        resolver — and reject if the resolver returns a private IP."""

        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("10.0.0.5", port or 0),
                )
            ]

        with patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo):
            with pytest.raises(SSRFError) as exc:
                validate_request_url("http://attacker.example/")
            assert "resolves_to_private_ip" in exc.value.reason

    def test_hostname_resolver_failure_fails_closed(self) -> None:
        def fake_getaddrinfo(host, port, *args, **kwargs):
            raise socket.gaierror(-2, "Name or service not known")

        with patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo):
            with pytest.raises(SSRFError) as exc:
                validate_request_url("http://does-not-exist.example/")
            assert exc.value.reason.startswith("dns_failed")


class TestInternalHostAllowlist:
    """The deployment routes Typesense / Postgres / Redis traffic over
    the Hetzner private network. The guard must not block those."""

    def test_env_override_allows_private_host(self, monkeypatch) -> None:
        from src import config

        monkeypatch.setattr(config.settings, "internal_hosts_allow", "internal.example,10.0.0.5")
        assert is_internal_host_allowed("internal.example")
        assert is_internal_host_allowed("10.0.0.5")

    def test_proxy_url_seeds_allowlist(self, monkeypatch) -> None:
        from src import config

        monkeypatch.setattr(config.settings, "internal_hosts_allow", "")
        monkeypatch.setattr(
            config.settings,
            "webshare_proxy_url",
            "http://user:pass@10.0.0.7:6716",
        )
        assert "10.0.0.7" in _gather_internal_hosts()

    def test_typesense_host_seeds_allowlist(self, monkeypatch) -> None:
        from src import config

        monkeypatch.setattr(config.settings, "internal_hosts_allow", "")
        monkeypatch.setattr(config.settings, "typesense_host", "typesense.internal")
        assert "typesense.internal" in _gather_internal_hosts()

    def test_internal_host_bypasses_private_ip_check(self, monkeypatch) -> None:
        """A host on the allowlist must pass validate_request_url even
        when its hostname is itself a private IP literal."""
        from src import config

        monkeypatch.setattr(config.settings, "internal_hosts_allow", "10.0.0.7,typesense.internal")
        # Literal IP that would otherwise trip the classifier.
        validate_request_url("http://10.0.0.7:8108/health")

        # Hostname that resolves to a private IP — but the host string
        # itself is on the allowlist, so we skip the resolver.
        def fake_getaddrinfo(host, port, *args, **kwargs):
            raise AssertionError("resolver should not be called for allowlisted host")

        with patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo):
            validate_request_url("http://typesense.internal:8108/health")


class TestResolveHostOrRaise:
    def test_returns_public_address(self) -> None:
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port or 0))]

        with patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo):
            addr = resolve_host_or_raise("http://example.com/", "example.com")
        assert addr == "93.184.216.34"

    def test_rejects_when_any_address_is_private(self) -> None:
        """An attacker who controls authoritative DNS can return a mixed
        public + private answer hoping we pick the public one. We
        reject when *any* address in the answer is private."""

        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port or 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", port or 0)),
            ]

        with (
            patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo),
            pytest.raises(SSRFError),
        ):
            resolve_host_or_raise("http://attacker.example/", "attacker.example")


class TestSSRFGuardedTransport:
    """The transport must reject blocked requests BEFORE delegating to
    the inner transport. Verified by asserting the inner transport's
    handler was never called."""

    async def test_blocks_private_ip_request(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, text="should not get here")

        inner = httpx.MockTransport(handler)
        guarded = SSRFGuardedTransport(inner)
        async with httpx.AsyncClient(transport=guarded) as client:
            with pytest.raises(SSRFError):
                await client.get("http://127.0.0.1/admin")
        assert calls == []

    async def test_allows_public_host(self, monkeypatch) -> None:
        """A public IP literal passes through to the inner transport."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        inner = httpx.MockTransport(handler)
        guarded = SSRFGuardedTransport(inner)
        async with httpx.AsyncClient(transport=guarded) as client:
            resp = await client.get("http://8.8.8.8/")
        assert resp.status_code == 200

    async def test_blocks_redirect_to_private_ip(self) -> None:
        """A response carrying ``Location: http://127.0.0.1/`` must be
        rejected on the redirect hop. httpx invokes the transport per
        hop, so the guard fires when validate_request_url runs on the
        redirected request.

        Regression for the explicit ask in #3214: "Block both direct
        connections AND redirects."
        """
        hops: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hops.append(str(request.url))
            # First hop: a public IP literal that 302s to loopback.
            if request.url.host == "8.8.8.8":
                return httpx.Response(
                    302,
                    headers={"location": "http://127.0.0.1/admin"},
                )
            # Second hop must never run — the guard should refuse to
            # dispatch it.
            return httpx.Response(200, text="leaked!")

        inner = httpx.MockTransport(handler)
        guarded = SSRFGuardedTransport(inner)
        async with httpx.AsyncClient(transport=guarded, follow_redirects=True) as client:
            with pytest.raises(SSRFError):
                await client.get("http://8.8.8.8/")

        # Exactly one hop reached the inner transport — the redirect
        # target was blocked by the guard before the inner saw it.
        assert hops == ["http://8.8.8.8/"]


class TestCreateHttpClientIsGuarded:
    """End-to-end regression: the public factory monitors / scrapers
    actually use must install the guard. A board URL pointing at
    ``127.0.0.1`` fails through ``create_http_client`` — closes #3214."""

    async def test_loopback_board_url_is_refused(self) -> None:
        client = create_http_client()
        try:
            with pytest.raises(SSRFError):
                await client.get("http://127.0.0.1/admin")
        finally:
            await client.aclose()

    async def test_rfc1918_board_url_is_refused(self) -> None:
        client = create_http_client()
        try:
            with pytest.raises(SSRFError) as exc:
                await client.get("http://10.0.0.5/admin")
            assert "private" in exc.value.reason
        finally:
            await client.aclose()

    async def test_metadata_endpoint_is_refused(self) -> None:
        """169.254.169.254 — the canonical EC2 / GCP IMDS endpoint —
        must be refused by the same factory call chain monitors use."""
        client = create_http_client()
        try:
            with pytest.raises(SSRFError):
                await client.get("http://169.254.169.254/latest/meta-data/")
        finally:
            await client.aclose()

    async def test_public_host_passes_when_resolver_returns_public(self, monkeypatch) -> None:
        """Sanity: a public-looking hostname makes it to the connect
        attempt (which will then fail with a network error from
        MockTransport, but never with SSRFError)."""

        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port or 0))]

        # We patch socket.getaddrinfo so the guard's resolver step
        # returns a known-public IP, and stub the inner transport so
        # the test doesn't touch the network.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        client = create_http_client()
        client._transport._inner = httpx.MockTransport(handler)
        try:
            with patch.object(socket, "getaddrinfo", side_effect=fake_getaddrinfo):
                resp = await client.get("http://example.com/")
            assert resp.status_code == 200
        finally:
            await client.aclose()
