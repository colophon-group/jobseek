"""SSRF guard for board fetches (closes #3214).

The crawler fetches arbitrary URLs sourced from ``data/boards.csv`` and
the agent-driven workspace flow. Without a guard, a board URL such as
``http://10.0.0.5/admin`` (or any of the Hetzner private-network IPs)
would be followed straight to internal services. Worse: an attacker who
controls the DNS for a permitted hostname can swing the A record to a
private IP between the validation step and the actual connection — the
classic DNS-rebinding twist — and a redirect to a private IP slips past
any host-string check.

The guard is implemented at the **httpx transport layer**:

  1. :func:`is_private_ip` — pure classifier matching the CIDR set
     called out by the issue (10/8, 172.16/12, 192.168/16, 127/8,
     169.254/16, ::1/128, fc00::/7) plus the IETF-reserved ranges that
     are equally dangerous (unspecified, multicast, CGNAT, link-local
     v6, IPv4-mapped v6, metadata).
  2. :func:`resolve_host_or_raise` — runs ``socket.getaddrinfo`` and rejects
     when *any* returned address is private. The sole exception is an
     unscoped IPv6 link-local DNS answer alongside a public address: without
     an interface scope it is not a routable destination. The async transport
     retries only temporary ``EAI_AGAIN`` resolver failures with a bounded
     backoff; every successful answer is still validated fail-closed.
  3. :func:`is_internal_host_allowed` — opt-in exemption list, sourced
     from ``INTERNAL_HOSTS_ALLOW`` env (comma-separated host[:port]
     entries) plus the proxy / Typesense / Postgres / Redis hosts the
     deployment legitimately routes onto the private network.
  4. :class:`SSRFGuardedTransport` — an ``httpx.AsyncBaseTransport``
     wrapper that runs :func:`validate_request_url` before every
     ``handle_async_request`` call. httpx's redirect engine re-enters
     ``transport.handle_async_request`` for every hop, so this single
     hook catches both direct connections *and* redirect targets.

Threat model:

  - Untrusted board URL points to a private IP directly        → blocked
  - Untrusted host resolves to a private IP (rebinding)        → blocked
  - Permitted host that 30x-redirects to ``http://127.0.0.1/`` → blocked
  - Webshare proxy URL on the private network → NOT blocked
    (the proxy URL is supplied via httpx's ``proxy=`` kwarg, not the
    request URL, so it never reaches :meth:`handle_async_request` as
    the URL to validate)
  - Typesense / Postgres / Redis traffic                       → NOT blocked
    (these go through dedicated clients — ``typesense.Client``,
    ``asyncpg``, ``redis-py`` — never the shared httpx clients)

The proxy-routed transport (``apps/crawler/AGENTS.md`` "Proxy-routed
transport") legitimately uses HTTP proxies on the Hetzner private
network. We don't validate the proxy URL itself, only the request URL —
so private-IP proxies keep working. The internal-host allowlist is a
last-resort knob for, e.g., a local dev Typesense board URL during
testing; it is not required for the live deployment.

@see colophon-group/jobseek#3214
@see apps/murmur-shim/src/lib/murmur/ssrf.ts (TS sibling — same model)
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import Iterable

log = structlog.get_logger()

DNS_RETRY_BACKOFF_SECONDS = (0.05, 0.1)


class SSRFError(Exception):
    """Raised when a URL fails the SSRF guard.

    Surfaces as an ``httpx.RequestError`` subclass-style exception
    propagated through ``handle_async_request``. Monitors / scrapers
    that ``except Exception`` already route this through their failure
    path (``_RECORD_FAILURE``); callers that need the structured
    code can ``isinstance(exc, SSRFError)``.
    """

    def __init__(self, url: str, reason: str, *, host: str | None = None) -> None:
        self.url = url
        self.host = host
        self.reason = reason
        super().__init__(f"SSRF guard refused {url!r}: {reason}")


def _ip_in_blocklist(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return a reason string when ``ip`` is in a blocked range, else ``None``.

    Mirrors the issue's required block list plus a handful of IANA-reserved
    ranges that ``ipaddress`` already classifies for us. Using the stdlib
    properties avoids re-encoding CIDR arithmetic in Python.
    """
    if ip.is_loopback:
        # 127.0.0.0/8 and ::1/128
        return "loopback"
    if ip.is_link_local:
        # 169.254.0.0/16 and fe80::/10. Covers cloud metadata
        # (169.254.169.254) and IPv6 link-local — never legitimate
        # destinations for a public-Internet fetch.
        return "link_local"
    if ip.is_private:
        # 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 for v4; fc00::/7 for v6.
        # ``ipaddress.is_private`` also flags the unspecified address and a
        # few IETF-reserved ranges, which we want to block anyway.
        return "private"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        # 240.0.0.0/4, etc. — never a real Internet target.
        return "reserved"
    if ip.is_unspecified:
        # 0.0.0.0, :: — kernels route these to loopback / "any" on bind.
        return "unspecified"
    # IPv4-mapped IPv6: re-classify the embedded v4 so an attacker
    # can't smuggle 10.0.0.1 in as ``::ffff:10.0.0.1``.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _ip_in_blocklist(ip.ipv4_mapped)
    return None


def is_private_ip(addr: str) -> bool:
    """Whether ``addr`` is in any blocked range (private/loopback/etc).

    Public surface for tests and operator scripts. Accepts any v4 or
    v6 literal that ``ipaddress.ip_address`` understands. Unparseable
    input returns ``True`` (fail-closed) — an SSRF guard that lets a
    malformed address through is worse than one that rejects it.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    return _ip_in_blocklist(ip) is not None


def _normalize_host_entry(entry: str) -> str:
    """Normalise an allowlist entry — strip whitespace, lowercase, drop port."""
    entry = entry.strip().lower()
    if not entry:
        return ""
    # Support ``host`` and ``host:port`` — only the host portion matters
    # for matching, since the guard is keyed on what was resolved.
    if entry.startswith("["):
        # IPv6 literal in brackets: ``[fe80::1]:8108``
        end = entry.find("]")
        return entry[1:end] if end > 0 else entry.strip("[]")
    if ":" in entry and not entry.replace(":", "").replace(".", "").isalnum():
        # Probably a bare IPv6 literal like ``fe80::1``. Keep as-is.
        return entry
    return entry.split(":", 1)[0]


def _extract_host(url: str) -> str:
    """Pull the host out of ``url``. Empty string if unparseable."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = parsed.hostname or ""
    return host.lower()


def _gather_internal_hosts() -> frozenset[str]:
    """Hosts the deployment legitimately speaks to on the private network.

    Comes from three sources (union):

      1. ``INTERNAL_HOSTS_ALLOW`` env — comma-separated ``host[:port]``.
         Operator-facing override; required for a board URL that
         genuinely points at an internal service.
      2. The Webshare proxy URLs (``WEBSHARE_PROXY_URLS`` /
         ``WEBSHARE_PROXY_URL``).
         Defence-in-depth only: the proxy URL is supplied via httpx's
         ``proxy=`` kwarg and isn't the request URL, so it can't
         actually trip the guard — but if a future caller ever passes
         it as a request URL, it should still be allowed.
      3. ``TYPESENSE_HOST`` / ``LOCAL_DATABASE_URL`` / ``REDIS_URL``.
         These services do not go through ``src.shared.http`` today
         (they use ``typesense.Client``, ``asyncpg``, ``redis-py``),
         but seeding them here means an inadvertent future migration
         to an httpx-based client won't suddenly start blocking
         intra-cluster traffic.

    Read lazily so tests can monkeypatch ``settings`` / env without
    a module-import dance.
    """
    out: set[str] = set()

    # 1. Operator override — settings first, env as fallback so test
    # monkeypatching of ``settings.internal_hosts_allow`` works without
    # also mutating ``os.environ``. Both are read so a deploy-time
    # environment variable still wins when pydantic-settings hasn't
    # been re-instantiated (e.g. inside a long-running container after
    # an env change).
    raw_entries: list[str] = []
    try:
        from src.config import settings as _settings
    except Exception:
        _settings = None
    if _settings is not None:
        configured = getattr(_settings, "internal_hosts_allow", "") or ""
        if configured:
            raw_entries.append(configured)
    env_value = os.environ.get("INTERNAL_HOSTS_ALLOW", "")
    if env_value:
        raw_entries.append(env_value)

    for raw in raw_entries:
        for entry in raw.split(","):
            host = _normalize_host_entry(entry)
            if host:
                out.add(host)

    # 2 + 3. Deployment-derived. ``src.config`` may not be importable in
    # test contexts that haven't seeded ``DATABASE_URL``; swallow the
    # error and continue with whatever we have.
    if _settings is None:
        return frozenset(out)

    deployment_urls = [
        *getattr(_settings, "webshare_proxy_urls", []),
        getattr(_settings, "webshare_proxy_url", ""),
        getattr(_settings, "local_database_url", ""),
        getattr(_settings, "database_url", ""),
        getattr(_settings, "redis_url", ""),
    ]
    for url in deployment_urls:
        host = _extract_host(url)
        if host:
            out.add(host)

    ts_host = getattr(_settings, "typesense_host", "")
    if ts_host:
        host = _normalize_host_entry(ts_host)
        if host:
            out.add(host)

    return frozenset(out)


def is_internal_host_allowed(host: str) -> bool:
    """Whether ``host`` is in the deployment's internal-services allowlist."""
    h = host.strip().lower()
    if not h:
        return False
    return h in _gather_internal_hosts()


def _public_address_from_infos(
    url: str,
    host: str,
    infos: Iterable[tuple[Any, ...]],
) -> str:
    """Validate one resolver answer and choose its first public address."""
    chosen: str | None = None
    unscoped_link_local: str | None = None
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        has_textual_scope = False
        if family == socket.AF_INET:
            addr = sockaddr[0]
        elif family == socket.AF_INET6:
            addr = sockaddr[0]
            # Strip the zone-id suffix some IPv6 link-local entries
            # carry (``fe80::1%en0``) before classifying.
            has_textual_scope = "%" in addr
            addr = addr.split("%", 1)[0]
        else:
            continue
        if is_private_ip(addr):
            ip = ipaddress.ip_address(addr)
            scope_id = sockaddr[3] if family == socket.AF_INET6 and len(sockaddr) > 3 else 0
            if (
                isinstance(ip, ipaddress.IPv6Address)
                and ip.is_link_local
                and not scope_id
                and not has_textual_scope
            ):
                # A DNS AAAA record cannot supply the interface scope required
                # to route an IPv6 link-local destination. Linux rejects a
                # connect to this sockaddr with EINVAL. Some public sites
                # nevertheless publish such broken AAAA records alongside a
                # valid A record, so defer this one unreachable answer and use
                # the public address when available. Scoped link-local answers
                # remain blocked because they identify a reachable interface.
                unscoped_link_local = addr
                continue
            raise SSRFError(
                url,
                f"resolves_to_private_ip:{addr}",
                host=host,
            )
        if chosen is None:
            chosen = addr

    if chosen is None and unscoped_link_local is not None:
        raise SSRFError(
            url,
            f"resolves_to_private_ip:{unscoped_link_local}",
            host=host,
        )
    if chosen is None:
        raise SSRFError(url, "no_address_record", host=host)
    return chosen


def _raise_dns_failure(url: str, host: str, exc: socket.gaierror) -> None:
    raise SSRFError(url, f"dns_failed: {exc}", host=host) from exc


def resolve_host_or_raise(url: str, host: str) -> str:
    """Synchronously resolve ``host`` and reject private answer records.

    Returns the first resolved address. The ``url`` is only used for
    error reporting / log context.

    DNS-rebinding posture: we inspect *every* address in each successful
    answer. An attacker who flips the authoritative
    DNS between this call and the eventual socket connect can still
    swing the answer — Python's stdlib has no zero-cost way to pin
    httpx's underlying ``httpcore.AsyncHTTPConnection`` to a captured
    IP. The first-line defence is the host-resolution check (catches
    almost every accidental misconfiguration plus a static-DNS
    attack); the second-line defence is the network firewall on the
    Hetzner box (port 5432 / 6379 / 8108 accept only the private
    network, not the public Internet). The Murmur shim's TS sibling
    pins the captured IP because Node exposes the ``lookup`` hook;
    httpx does not. If httpx ever exposes one, lift it from
    ``apps/murmur-shim/src/lib/murmur/ssrf.ts``.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        _raise_dns_failure(url, host, exc)
    return _public_address_from_infos(url, host, infos)


async def _resolve_host_or_raise_with_retry(url: str, host: str) -> str:
    """Resolve off-loop and retry only temporary resolver unavailability."""
    max_attempts = len(DNS_RETRY_BACKOFF_SECONDS) + 1
    for attempt in range(1, max_attempts + 1):
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except socket.gaierror as exc:
            if exc.errno != socket.EAI_AGAIN or attempt == max_attempts:
                _raise_dns_failure(url, host, exc)
            log.warning(
                "ssrf.dns.retry",
                host=host,
                attempt=attempt,
                max_attempts=max_attempts,
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(DNS_RETRY_BACKOFF_SECONDS[attempt - 1])
            continue
        return _public_address_from_infos(url, host, infos)
    raise AssertionError("bounded DNS retry loop exhausted without a result")


def _host_requiring_dns_validation(url: str | httpx.URL) -> tuple[str, str] | None:
    """Validate URL shape/literals and return a hostname that needs DNS."""
    url_str = str(url)
    try:
        parsed = urlparse(url_str)
    except ValueError as exc:
        raise SSRFError(url_str, f"parse_failed: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SSRFError(url_str, f"scheme_not_allowed:{scheme or 'empty'}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise SSRFError(url_str, "host_missing")

    if is_internal_host_allowed(host):
        return None

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url_str, host

    reason = _ip_in_blocklist(ip)
    if reason is not None:
        raise SSRFError(url_str, f"private_ip_literal:{reason}", host=host)
    return None


def validate_request_url(url: str | httpx.URL) -> None:
    """Public guard. Raises :class:`SSRFError` when ``url`` would SSRF.

    Called from :class:`SSRFGuardedTransport` for every request that
    httpx dispatches (including each redirect hop). Also exported for
    callers that want to fail-fast before queuing work.
    """
    target = _host_requiring_dns_validation(url)
    if target is not None:
        resolve_host_or_raise(*target)


async def _validate_request_url_with_dns_retry(url: str | httpx.URL) -> None:
    target = _host_requiring_dns_validation(url)
    if target is not None:
        await _resolve_host_or_raise_with_retry(*target)


class SSRFGuardedTransport(httpx.AsyncBaseTransport):
    """httpx transport wrapper that runs the SSRF guard per request.

    httpx calls ``handle_async_request`` once per request and **once
    per redirect hop** (see ``AsyncClient._send_handling_redirects``).
    That makes the transport the right place to enforce the guard:
    a single hook catches the initial connection AND every redirect
    target, with no need to thread the check through dozens of
    monitor / scraper call sites.

    The wrapper delegates to an inner transport (typically
    :class:`httpx.AsyncHTTPTransport`) for the actual fetch.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            await _validate_request_url_with_dns_retry(request.url)
        except SSRFError as exc:
            log.warning(
                "ssrf.guard.blocked",
                url=str(request.url),
                host=exc.host,
                reason=exc.reason,
            )
            raise
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def wrap_transport_for_kwargs(
    kwargs: dict[str, Any],
    *,
    verify: Any = True,
) -> dict[str, Any]:
    """Patch a ``httpx.AsyncClient(**kwargs)`` dict to install the guard.

    The wrapper preserves any caller-supplied ``transport``; otherwise
    it constructs the standard :class:`httpx.AsyncHTTPTransport` with
    the same ``verify`` setting and wraps it.

    Returns the same dict for chaining.
    """
    inner = kwargs.pop("transport", None)
    if inner is None:
        inner = httpx.AsyncHTTPTransport(verify=verify)
    kwargs["transport"] = SSRFGuardedTransport(inner)
    return kwargs


def collect_internal_hosts() -> Iterable[str]:
    """Debug helper — return the current internal-host allowlist."""
    return sorted(_gather_internal_hosts())
