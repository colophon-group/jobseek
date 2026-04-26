from __future__ import annotations

import ssl
import time
from typing import Any

import httpx

from src.shared.proxy import httpx_proxy_for


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


# Default UA mimics a recent Chrome on Windows. The previous value
# ``jobseek-crawler/0.1`` was a unique fingerprint that WAF vendors
# trivially match — it produced the anti-bot /Error and /404/ redirects
# documented in issue #2193 on apply.deloitte.com, digitalcareers.infosys,
# careers.loreal.com, careers.tsmc.com, careers.bain.com, and
# recruitingapp-1619.umantis.com. Individual monitors/scrapers that need a
# different UA still override via ``headers=`` on the request.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
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

# Hosts on the Avature ATS platform whose WAF (Akamai BMP on at least
# the Deloitte deployment) rejects our minimal browser headers with
# HTTP 403/406 (issue #2708: 251 × apply.deloitte.com failures in 1h,
# 169×403 + 82×406; 406 specifically signals Accept rejection). These
# hosts get a richer Chrome-shape header set applied per-request via
# ``_avature_request_hook``.
#
# Match policy:
#   * Any hostname ending in ``.avature.net`` (the canonical Avature
#     domain for tenants like ``bloomberg.avature.net``,
#     ``dhlconsulting.avature.net``, ``deloitteus.avature.net`` —
#     apply.deloitte.com 302's into here).
#   * Explicit allowlist of Deloitte's white-labeled Avature hosts that
#     do NOT live under ``.avature.net`` directly.
#
# A naive ``apply.*`` pattern would bleed to apply.workable.com,
# apply.refline.ch, applyglobal.deloitte.com, etc. which use unrelated
# platforms, so we keep the list explicit.
_AVATURE_HOST_SUFFIXES = (".avature.net",)
_AVATURE_EXACT_HOSTS = frozenset(
    {
        "apply.deloitte.com",
        "apply.deloitte.ch",
        "apply.deloitte.co.uk",
        "apply.deloittece.com",
    }
)

# Richer Accept matches what a real Chrome navigation sends — image/avif,
# image/webp, application/signed-exchange. Some WAF rules fingerprint the
# absence of these tokens.
_AVATURE_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8,"
    "application/signed-exchange;v=b3;q=0.7"
)

# Browser-shape ancillary headers Chrome always sends on a top-level
# navigation. The full Sec-Fetch-* triad is the strongest cheap signal
# that the request originated from a real navigation rather than a script.
_AVATURE_EXTRA_HEADERS: dict[str, str] = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _is_avature_host(host: str | None) -> bool:
    """Return True when *host* belongs to the Avature ATS platform."""
    if not host:
        return False
    host = host.lower()
    if host in _AVATURE_EXACT_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in _AVATURE_HOST_SUFFIXES)


async def _avature_request_hook(request: httpx.Request) -> None:
    """Apply richer Chrome-shape headers when targeting Avature-hosted boards.

    Hypothesis-driven mitigation for issue #2708 (apply.deloitte.com
    403/406 cluster). The minimal default Accept header is one of the
    cheapest signals the WAF uses to distinguish scripts from real
    browsers; supplying the full Chrome navigation header set raises the
    bar without requiring a browser fallback.

    Only Accept is upgraded when the caller has not already set a custom
    Accept (e.g. api_sniffer sending ``application/json`` must still
    win), matching the per-request override contract documented on
    ``DEFAULT_ACCEPT``. Sec-Fetch-* headers are added unconditionally
    via ``setdefault`` — no existing caller sets them.
    """
    if not _is_avature_host(request.url.host):
        return
    if request.headers.get("accept", "") == DEFAULT_ACCEPT:
        request.headers["Accept"] = _AVATURE_ACCEPT
    for key, value in _AVATURE_EXTRA_HEADERS.items():
        request.headers.setdefault(key, value)


_CLIENT_DEFAULTS = {
    "timeout": httpx.Timeout(30.0),
    "follow_redirects": True,
    "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
    "headers": {"User-Agent": DEFAULT_USER_AGENT, "Accept": DEFAULT_ACCEPT},
    "verify": _make_ssl_context(),
}


def _client_kwargs(*, verify: bool, use_proxy: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {**_CLIENT_DEFAULTS}
    if not verify:
        kwargs["verify"] = False
    proxy = httpx_proxy_for(use_proxy=use_proxy)
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


def create_http_client(*, verify: bool = True, use_proxy: bool = False) -> httpx.AsyncClient:
    """Create an httpx client, optionally routed through the active proxy provider."""
    return httpx.AsyncClient(
        **_client_kwargs(verify=verify, use_proxy=use_proxy),
        event_hooks={"request": [_avature_request_hook]},
    )


def create_nossl_http_client(*, use_proxy: bool = False) -> httpx.AsyncClient:
    """HTTP client that skips SSL certificate verification.

    Used for boards whose servers have broken certificate chains
    (e.g. missing intermediate CA). Enabled per-board via
    ``skip_ssl: true`` in scraper_config.
    """
    return create_http_client(verify=False, use_proxy=use_proxy)


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

    client = httpx.AsyncClient(
        **_client_kwargs(verify=verify, use_proxy=use_proxy),
        event_hooks={
            "request": [_avature_request_hook, _on_request],
            "response": [_on_response],
        },
    )
    return client, log_entries
