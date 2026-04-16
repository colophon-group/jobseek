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

_CLIENT_DEFAULTS = {
    "timeout": httpx.Timeout(30.0),
    "follow_redirects": True,
    "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
    "headers": {"User-Agent": DEFAULT_USER_AGENT},
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
    return httpx.AsyncClient(**_client_kwargs(verify=verify, use_proxy=use_proxy))


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
        event_hooks={"request": [_on_request], "response": [_on_response]},
    )
    return client, log_entries
