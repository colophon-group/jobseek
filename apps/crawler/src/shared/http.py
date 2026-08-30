from __future__ import annotations

import asyncio
import contextvars
import re
import ssl
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from typing import Any
from urllib.parse import urlparse

import httpx

from src.shared.proxy import (
    ProxyProvider,
    ProxySelection,
    report_proxy_failure,
    report_proxy_success,
    require_provider,
)
from src.shared.ssrf import SSRFGuardedTransport


def _make_ssl_context() -> ssl.SSLContext:
    """Create an SSL context compatible with CDNs that mishandle TLS session tickets.

    Some CDNs (notably Akamai) send TLS 1.3 session tickets that cause
    httpcore's async I/O to hang indefinitely.  Setting ``OP_NO_TICKET``
    prevents this by disabling session ticket negotiation — the same
    approach urllib3 uses by default.

    Also enables legacy server connect for servers that require TLS
    renegotiation (e.g. career.abchina.com.cn).  OpenSSL 3.0+ disables
    this by default.

    Uses certifi's CA bundle instead of the system store for broader
    coverage of intermediate CA certificates.
    """
    import certifi

    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.options |= ssl.OP_NO_TICKET
    # Allow connections to servers that require legacy TLS renegotiation.
    # The constant may not exist on older Python/OpenSSL builds.
    OP_LEGACY_SERVER_CONNECT = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    ctx.options |= OP_LEGACY_SERVER_CONNECT
    return ctx


# Default UA mimics a recent Chrome on Windows. Keep the major version current:
# iCIMS tenants reject obsolete Chrome 131-139 fingerprints with HTTP 405 even
# though the same public pages remain available to current browsers.
# The previous value
# ``jobseek-crawler/0.1`` was a unique fingerprint that WAF vendors
# trivially match — it produced the anti-bot /Error and /404/ redirects
# documented in issue #2193 on apply.deloitte.com, digitalcareers.infosys,
# careers.loreal.com, careers.tsmc.com, careers.bain.com, and
# recruitingapp-1619.umantis.com. Individual monitors/scrapers that need a
# different UA still override via ``headers=`` on the request.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

# Default Accept matches a real Chrome HTML fetch. httpx's own default is
# ``*/*``, which is a bot-fingerprint signal — ``www.uber.com`` returns
# HTTP 406 for ``Accept: */*`` on its HTML job pages (issue #2214: 809 ×
# 406 per 12h on Uber alone). Keeping ``*/*;q=0.8`` at the tail means any
# endpoint that prefers JSON or another content-type still matches via
# the wildcard; per-request ``Accept`` overrides from monitor/scraper
# configs still win (httpx merges client + request headers, with the
# per-request entry winning on conflict).
DEFAULT_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

# Avature's branded tenants commonly use a ``<something>Careers/*Detail``
# route even when the public hostname does not mention Avature. During the
# incident in #5710 those routes returned short-lived 406 bursts across five
# unrelated tenants, while the exact same URLs subsequently returned their
# normal job pages. Keep this predicate URL-specific: a generic 406 can still
# be a permanent content-negotiation/configuration error.
_AVATURE_DETAIL_PATH_RE = re.compile(
    r"/[^/]*careers[^/]*/(?:job|folder|pipeline)detail(?:/|$)",
    re.IGNORECASE,
)
_AVATURE_UNIQUE_DETAIL_PATH_RE = re.compile(
    r"/(?:folder|pipeline)detail(?:/|$)",
    re.IGNORECASE,
)

# Stable incident identifier shared by Workday's list monitor and the worker
# cohort circuit. Keep this provider/status-specific: ordinary Workday
# failures must never be able to pause every tenant (#5715).
WORKDAY_LIST_303_INCIDENT = "workday-list-303"


def is_avature_job_detail_url(url: str) -> bool:
    """Return whether *url* is a recognizable Avature detail page."""

    parsed = urlparse(url)
    path = parsed.path
    host = (parsed.hostname or "").lower()
    return bool(
        _AVATURE_DETAIL_PATH_RE.search(path)
        or _AVATURE_UNIQUE_DETAIL_PATH_RE.search(path)
        or (
            host.endswith(".avature.net")
            and re.search(r"/(?:job|folder|pipeline)detail(?:/|$)", path, re.IGNORECASE)
        )
    )


_CLIENT_DEFAULTS = {
    "timeout": httpx.Timeout(30.0),
    "follow_redirects": True,
    "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
    "headers": {"User-Agent": DEFAULT_USER_AGENT, "Accept": DEFAULT_ACCEPT},
    "verify": _make_ssl_context(),
}


_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_COOKIE_OCTET_RE = re.compile(r"^[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*$")


def _is_rfc6265_cookie(cookie: Cookie) -> bool:
    """Return whether a parsed cookie can be emitted under RFC 6265."""
    if not _COOKIE_NAME_RE.fullmatch(cookie.name):
        return False

    value = cookie.value
    if value is None:
        return False
    if value.startswith('"') or value.endswith('"'):
        if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
            return False
        value = value[1:-1]
    return _COOKIE_OCTET_RE.fullmatch(value) is not None


class _Rfc6265CookiePolicy(DefaultCookiePolicy):
    """Reject upstream cookies that cannot be serialized as RFC 6265 cookies.

    RFC 6265 cookie octets are ASCII. A few legacy recruiting portals put
    locale-dependent month names in a tracking cookie (for example French
    ``AOÛT``). ``http.cookiejar`` accepts that response value, but httpx then
    raises ``UnicodeEncodeError`` while constructing the next request and the
    otherwise-public board becomes impossible to paginate or scrape. Ignore
    only the malformed cookie; valid session cookies from the same response
    remain available to subsequent requests.
    """

    def set_ok(self, cookie: Cookie, request: Any) -> bool:
        return _is_rfc6265_cookie(cookie) and super().set_ok(cookie, request)


@dataclass
class RequestHostTracker:
    """Task-local record of the real hosts touched by one crawler run.

    The object itself is mutable so child tasks created by a concurrent
    monitor inherit and update the same tracker through ``contextvars``.
    Keeping a set plus the latest host bounds memory even for monitors that
    make thousands of paginated requests.
    """

    hosts: set[str] = field(default_factory=set)
    last_host: str | None = None
    last_url: str | None = None
    last_status_code: int | None = None
    last_transport_error: str | None = None
    last_application_error: str | None = None
    last_provider_incident: str | None = None
    last_provider_incident_host: str | None = None

    def note(self, host: str) -> None:
        normalized = host.rstrip(".").lower()
        if not normalized:
            return
        self.hosts.add(normalized)
        self.last_host = normalized

    def note_request(self, host: str, url: str | None = None) -> None:
        """Start a new network outcome, superseding the previous request."""

        self.note(host)
        self.last_url = url
        self.last_status_code = None
        self.last_transport_error = None
        self.last_application_error = None

    def note_response(self, host: str, status_code: int) -> None:
        self.note(host)
        self.last_status_code = status_code
        self.last_transport_error = None
        self.last_application_error = None

    def note_transport_error(self, host: str, exc: httpx.TransportError) -> None:
        self.note(host)
        self.last_status_code = None
        self.last_transport_error = type(exc).__name__
        self.last_application_error = None

    def note_transient_response_failure(self, host: str, url: str, reason: str) -> None:
        """Promote a provider-verified response failure to a transient outcome.

        Some upstreams return a successful HTTP status with a temporary
        non-API body during provider incidents. Callers must only use this
        after exhausting their provider-specific validation and retries; a
        generic parser failure must remain a reachable-host outcome.
        """

        self.note(host)
        self.last_url = url
        self.last_transport_error = None
        self.last_application_error = reason

    def note_provider_incident(self, host: str, url: str, incident: str) -> None:
        """Record a provider-specific exhausted response on the final host.

        The worker may use this marker for a distinct-origin cohort circuit.
        It deliberately does not change the generic host classification: the
        monitor path already accounts for every failed run once.
        """

        self.note(host)
        self.last_url = url
        self.last_transport_error = None
        self.last_application_error = None
        self.last_provider_incident = incident
        self.last_provider_incident_host = self.last_host

    @property
    def transient_failure_host(self) -> str | None:
        """Return the final host only for an upstream-transient outcome.

        Parser/configuration failures after a successful response must not
        open a host-wide circuit. Network transport errors, overload/
        availability responses, access-denied cohorts, and explicitly
        promoted provider incidents are safe to coalesce across postings.
        """

        if self.last_application_error is not None:
            return self.last_host
        if self.last_transport_error is not None:
            return self.last_host
        status = self.last_status_code
        if status in (408, 425, 429) or (status is not None and 500 <= status <= 599):
            return self.last_host
        # A sustained 403 cohort is normally a host-level WAF/bot block. Let
        # the shared circuit defer sibling scrapes instead of retrying every
        # affected posting roughly hourly. Keep Avature JobDetail 403s out:
        # that provider also uses 403 for individual archived requisitions,
        # so treating those as host-wide would let a few stale listing URLs
        # pause otherwise-live jobs on the same tenant (#2708).
        if status == 403 and not (self.last_url and is_avature_job_detail_url(self.last_url)):
            return self.last_host
        # Avature uses 406 as a temporary overload/throttle response on live
        # JobDetail pages (#5710). Restrict this to the provider route so a
        # genuine generic content-negotiation failure cannot open a host-wide
        # circuit.
        if status == 406 and self.last_url and is_avature_job_detail_url(self.last_url):
            return self.last_host
        return None


_request_host_tracker: contextvars.ContextVar[RequestHostTracker | None] = contextvars.ContextVar(
    "request_host_tracker", default=None
)


def mark_transient_response_failure(url: str, *, reason: str) -> None:
    """Mark the current task's final response as a transient provider incident.

    This is a no-op outside :func:`track_request_hosts`, which keeps scrapers
    usable in isolation. The URL should identify the final response origin.
    """

    tracker = _request_host_tracker.get()
    host = urlparse(url).hostname
    if tracker is not None and host:
        tracker.note_transient_response_failure(host, url, reason)


def mark_provider_incident(url: str, *, incident: str) -> None:
    """Attach a verified provider incident to the current task outcome."""

    tracker = _request_host_tracker.get()
    host = urlparse(url).hostname
    if tracker is not None and host:
        tracker.note_provider_incident(host, url, incident)


@contextmanager
def track_request_hosts() -> Iterator[RequestHostTracker]:
    """Track actual outbound hosts for the current monitor/scrape task."""

    tracker = RequestHostTracker()
    token = _request_host_tracker.set(tracker)
    try:
        yield tracker
    finally:
        _request_host_tracker.reset(token)


class RequestHostTrackingTransport(httpx.AsyncBaseTransport):
    """Record every post-SSRF-validation request host, including redirects."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        tracker = _request_host_tracker.get()
        host = request.url.host or ""
        if tracker is not None and host:
            tracker.note_request(host, str(request.url))
        try:
            response = await self._inner.handle_async_request(request)
        except httpx.TransportError as exc:
            if tracker is not None and host:
                tracker.note_transport_error(host, exc)
            raise
        if tracker is not None and host:
            tracker.note_response(host, response.status_code)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


class RotatingProxyTransport(httpx.AsyncBaseTransport):
    """Rotate Webshare endpoints per request with quarantine and recovery.

    One underlying httpx transport is retained per pool slot so connection
    pooling remains efficient. Endpoint selection happens before every
    top-level request (including caller retry attempts). Redirect requests
    inherit the original selection through httpx's request extensions, so an
    authentication/cookie flow never changes IP mid-chain. The provider owns
    bounded process-local health state and skips quarantined slots.
    """

    _SELECTION_EXTENSION = "jobseek.proxy_selection"
    _TRANSPORT_EXTENSION = "jobseek.proxy_transport"
    _OUTCOME_REPORTED_EXTENSION = "jobseek.proxy_outcome_reported"

    def __init__(
        self,
        provider: ProxyProvider,
        *,
        verify: ssl.SSLContext | bool,
        transport_factory: Callable[[ProxySelection], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        self._provider = provider
        self._verify = verify
        self._transport_factory = transport_factory
        self._transports: dict[int, httpx.AsyncBaseTransport] = {}
        self._transport_lock = asyncio.Lock()

    async def _transport_for(self, selection: ProxySelection) -> httpx.AsyncBaseTransport:
        existing = self._transports.get(selection.pool_slot)
        if existing is not None:
            return existing
        async with self._transport_lock:
            existing = self._transports.get(selection.pool_slot)
            if existing is None:
                existing = (
                    self._transport_factory(selection)
                    if self._transport_factory is not None
                    else httpx.AsyncHTTPTransport(
                        proxy=selection.url,
                        verify=self._verify,
                    )
                )
                self._transports[selection.pool_slot] = existing
            return existing

    async def _discard_transport(self, slot: int) -> None:
        async with self._transport_lock:
            transport = self._transports.pop(slot, None)
        if transport is not None:
            await transport.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        origin = (request.url.host or "").lower() or None
        inherited = request.extensions.get(self._SELECTION_EXTENSION)
        selection = (
            inherited
            if isinstance(inherited, ProxySelection) and inherited._owner is self._provider
            else self._provider.select(origin=origin, transport="httpx")
        )
        request.extensions[self._SELECTION_EXTENSION] = selection
        request.extensions[self._TRANSPORT_EXTENSION] = self
        transport = await self._transport_for(selection)
        try:
            response = await transport.handle_async_request(request)
        except httpx.ProxyError as exc:
            reason = "proxy_auth" if "407" in str(exc) else "proxy_transport"
            request.extensions[self._OUTCOME_REPORTED_EXTENSION] = True
            report_proxy_failure(selection, origin=origin, reason=reason)
            await self._discard_transport(selection.pool_slot)
            raise
        except httpx.TransportError:
            # Without a provider error/header this may be a target-specific
            # TLS/connect failure, so do not evict the endpoint globally.
            request.extensions[self._OUTCOME_REPORTED_EXTENSION] = True
            report_proxy_failure(selection, origin=origin, reason="origin_transport")
            raise
        except BaseException:
            # Cancellation or a non-network transport failure is
            # inconclusive. Release only a probe owned by this lease.
            request.extensions[self._OUTCOME_REPORTED_EXTENSION] = True
            self.abandon_request(request)
            raise

        provider_error = response.headers.get("x-webshare-error-reason")
        if response.status_code == 407:
            request.extensions[self._OUTCOME_REPORTED_EXTENSION] = True
            report_proxy_failure(selection, origin=origin, reason="proxy_auth")
            await self._discard_transport(selection.pool_slot)
        elif provider_error:
            request.extensions[self._OUTCOME_REPORTED_EXTENSION] = True
            report_proxy_failure(selection, origin=origin, reason="proxy_transport")
            await self._discard_transport(selection.pool_slot)
        return response

    def finalize_response(self, response: httpx.Response) -> None:
        """Account for the final response after redirects/body consumption."""

        request = response.request
        if request.extensions.get(self._OUTCOME_REPORTED_EXTENSION):
            return
        selection = request.extensions.get(self._SELECTION_EXTENSION)
        if not isinstance(selection, ProxySelection) or selection._owner is not self._provider:
            return
        request.extensions[self._OUTCOME_REPORTED_EXTENSION] = True
        origin = (request.url.host or "").lower() or None
        if response.status_code in {403, 429}:
            # The proxy successfully reached this target, but this exit is
            # currently blocked/rate-limited for the origin. Quarantine only
            # the (slot, origin) pair; other origins may continue using it.
            report_proxy_failure(selection, origin=origin, reason="origin_block")
        else:
            # Only the final response in a redirect chain recovers a probe.
            report_proxy_success(selection, origin=origin)

    def report_stream_failure(self, request: httpx.Request, exc: BaseException) -> None:
        if request.extensions.get(self._OUTCOME_REPORTED_EXTENSION):
            return
        selection = request.extensions.get(self._SELECTION_EXTENSION)
        if not isinstance(selection, ProxySelection) or selection._owner is not self._provider:
            return
        request.extensions[self._OUTCOME_REPORTED_EXTENSION] = True
        origin = (request.url.host or "").lower() or None
        if isinstance(exc, httpx.ProxyError):
            reason = "proxy_auth" if "407" in str(exc) else "proxy_transport"
            report_proxy_failure(selection, origin=origin, reason=reason)
        elif isinstance(exc, httpx.TransportError):
            report_proxy_failure(selection, origin=origin, reason="origin_transport")
        else:
            self.abandon_request(request)

    def abandon_request(self, request: httpx.Request) -> None:
        selection = request.extensions.get(self._SELECTION_EXTENSION)
        if isinstance(selection, ProxySelection) and selection._owner is self._provider:
            origin = (request.url.host or "").lower() or None
            selection._owner.abandon(selection, origin=origin)

    async def aclose(self) -> None:
        async with self._transport_lock:
            transports = list(self._transports.values())
            self._transports.clear()
        for transport in transports:
            await transport.aclose()


class _ProxyOutcomeStream(httpx.AsyncByteStream):
    """Delay proxy recovery until a streamed final response reaches EOF."""

    def __init__(
        self,
        inner: httpx.AsyncByteStream,
        *,
        response: httpx.Response,
        transport: RotatingProxyTransport,
    ) -> None:
        self._inner = inner
        self._response = response
        self._transport = transport
        self._finished = False

    async def __aiter__(self):
        try:
            async for chunk in self._inner:
                yield chunk
        except BaseException as exc:
            self._finished = True
            self._transport.report_stream_failure(self._response.request, exc)
            raise
        else:
            self._finished = True
            self._transport.finalize_response(self._response)

    async def aclose(self) -> None:
        try:
            await self._inner.aclose()
        finally:
            if not self._finished:
                self._finished = True
                self._transport.abandon_request(self._response.request)


class ProxyAwareAsyncClient(httpx.AsyncClient):
    """Finalize one proxy lease only after the top-level request completes."""

    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: Any = httpx.USE_CLIENT_DEFAULT,
        follow_redirects: Any = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        try:
            response = await super().send(
                request,
                stream=stream,
                auth=auth,
                follow_redirects=follow_redirects,
            )
        except BaseException:
            transport = request.extensions.get(RotatingProxyTransport._TRANSPORT_EXTENSION)
            if isinstance(transport, RotatingProxyTransport):
                transport.abandon_request(request)
            raise

        transport = response.request.extensions.get(RotatingProxyTransport._TRANSPORT_EXTENSION)
        if not isinstance(transport, RotatingProxyTransport):
            return response
        if stream:
            if not isinstance(response.stream, httpx.AsyncByteStream):
                transport.abandon_request(response.request)
                return response
            response.stream = _ProxyOutcomeStream(
                response.stream,
                response=response,
                transport=transport,
            )
        else:
            transport.finalize_response(response)
        return response


def _client_kwargs(*, verify: bool, use_proxy: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {**_CLIENT_DEFAULTS}
    if not verify:
        kwargs["verify"] = False
    provider = require_provider(use_proxy=use_proxy)
    if provider is not None:
        kwargs["transport"] = RotatingProxyTransport(
            provider,
            verify=kwargs["verify"],
        )
    return kwargs


def _build_async_client(kwargs: dict[str, Any], **extra: Any) -> httpx.AsyncClient:
    """Construct an ``httpx.AsyncClient`` with the SSRF guard installed.

    httpx's own ``AsyncClient(proxy=...)`` path builds an internal
    ``AsyncHTTPTransport(proxy=...)`` and routes it via ``_mounts``; the
    default (no-proxy) path uses a plain ``AsyncHTTPTransport``. Both
    paths converge on ``transport.handle_async_request`` per request +
    per redirect, so wrapping the resolved transport with
    :class:`~src.shared.ssrf.SSRFGuardedTransport` catches every fetch
    regardless of whether it was proxied.

    ``_client_kwargs`` resolves either a direct transport or the Webshare
    rotating transport. This function wraps that resolved transport with
    request-host accounting and the SSRF guard. The original kwargs dict is
    not mutated.
    """
    kw = dict(kwargs)
    kw.update(extra)
    kw.setdefault("cookies", CookieJar(policy=_Rfc6265CookiePolicy()))
    inner = kw.pop("transport", None)
    proxy_aware = isinstance(inner, RotatingProxyTransport)
    if inner is None:
        verify = kw.pop("verify", True)
        inner = httpx.AsyncHTTPTransport(verify=verify)
    else:
        # An explicit transport was supplied — drop the httpx-managed
        # verify/proxy kwargs so AsyncClient doesn't complain about
        # them being ignored when a transport is provided.
        kw.pop("verify", None)
    # The SSRF guard stays outermost so refused private hosts never enter
    # failure accounting. The tracking layer sees each permitted redirect
    # hop and records the actual egress host instead of guessing from a
    # crawler type or board URL.
    kw["transport"] = SSRFGuardedTransport(RequestHostTrackingTransport(inner))
    client_type = ProxyAwareAsyncClient if proxy_aware else httpx.AsyncClient
    return client_type(**kw)


def create_http_client(*, verify: bool = True, use_proxy: bool = False) -> httpx.AsyncClient:
    """Create an httpx client, optionally routed through the active proxy provider.

    Every client returned here runs through
    :class:`~src.shared.ssrf.SSRFGuardedTransport`, which rejects
    requests whose target host resolves to a private / loopback /
    link-local IP. Redirects to private IPs are blocked too because
    httpx re-enters the transport for every redirect hop. See
    :mod:`src.shared.ssrf` for the guard contract and the deployment
    allowlist (Typesense / Postgres / Redis / proxy URL).
    """
    return _build_async_client(_client_kwargs(verify=verify, use_proxy=use_proxy))


def create_nossl_http_client(*, use_proxy: bool = False) -> httpx.AsyncClient:
    """HTTP client that skips SSL certificate verification.

    Used for boards whose servers have broken certificate chains
    (e.g. missing intermediate CA). Enabled per-board via
    ``skip_ssl: true`` in scraper_config.
    """
    return create_http_client(verify=False, use_proxy=use_proxy)


@asynccontextmanager
async def client_for(http: httpx.AsyncClient, config: dict) -> AsyncIterator[httpx.AsyncClient]:
    """Yield the right httpx client for *config*.

    If ``config["skip_ssl"]`` is truthy, build a fresh no-SSL-verify
    client (routed through the active proxy when ``config["proxy"]``
    is also truthy) and yield it inside an ``async with`` so it gets
    aclosed on exit. Otherwise yield the outer ``http`` client
    unchanged.

    Pure refactor of the duplicated branch at three call sites
    (monitor_one, monitor_one_stream, scrape_one). See #2705.
    """
    if config.get("skip_ssl"):
        async with create_nossl_http_client(use_proxy=bool(config.get("proxy"))) as nossl:
            yield nossl
    else:
        yield http


def create_logging_http_client(
    *,
    verify: bool = True,
    use_proxy: bool = False,
) -> tuple[httpx.AsyncClient, list[dict[str, Any]]]:
    """Create an HTTP client that logs request/response metadata.

    Returns (client, log_entries) where log_entries is populated as
    requests complete.
    """
    log_entries: list[dict[str, Any]] = []
    timings: dict[int, float] = {}

    async def _on_request(request: httpx.Request) -> None:
        timings[id(request)] = time.monotonic()

    async def _on_response(response: httpx.Response) -> None:
        req = response.request
        start = timings.pop(id(req), None)
        elapsed = round(time.monotonic() - start, 3) if start else None
        content_length = response.headers.get("content-length")
        log_entries.append(
            {
                "method": str(req.method),
                "url": str(req.url),
                "status": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "content_length": int(content_length) if content_length else None,
                "elapsed": elapsed,
            }
        )

    client = _build_async_client(
        _client_kwargs(verify=verify, use_proxy=use_proxy),
        event_hooks={"request": [_on_request], "response": [_on_response]},
    )
    return client, log_entries
