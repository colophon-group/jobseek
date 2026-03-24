from __future__ import annotations

import ssl
import time
from typing import Any

import httpx


def _make_ssl_context() -> ssl.SSLContext:
    """Create an SSL context compatible with CDNs that mishandle TLS session tickets.

    Some CDNs (notably Akamai) send TLS 1.3 session tickets that cause
    httpcore's async I/O to hang indefinitely.  Setting ``OP_NO_TICKET``
    prevents this by disabling session ticket negotiation — the same
    approach urllib3 uses by default.

    Uses certifi's CA bundle instead of the system store for broader
    coverage of intermediate CA certificates.
    """
    import certifi

    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.options |= ssl.OP_NO_TICKET
    return ctx


_CLIENT_DEFAULTS = {
    "timeout": httpx.Timeout(30.0),
    "follow_redirects": True,
    "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
    "headers": {"User-Agent": "jobseek-crawler/0.1"},
    "verify": _make_ssl_context(),
}


def create_http_client(*, verify: bool = True) -> httpx.AsyncClient:
    from src.shared.proxy import build_httpx_mounts

    mounts = build_httpx_mounts()
    kwargs = {**_CLIENT_DEFAULTS}
    if not verify:
        kwargs["verify"] = False
    return httpx.AsyncClient(**kwargs, **({"mounts": mounts} if mounts else {}))


def create_nossl_http_client() -> httpx.AsyncClient:
    """Create an HTTP client that skips SSL certificate verification.

    Used for boards whose servers have broken certificate chains
    (e.g. missing intermediate CA).  Enabled per-board via
    ``skip_ssl: true`` in scraper_config.
    """
    defaults = {**_CLIENT_DEFAULTS, "verify": False}
    from src.shared.proxy import build_httpx_mounts

    mounts = build_httpx_mounts()
    return httpx.AsyncClient(**defaults, **({"mounts": mounts} if mounts else {}))


def create_logging_http_client(
    *,
    verify: bool = True,
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

    from src.shared.proxy import build_httpx_mounts

    mounts = build_httpx_mounts()
    kwargs = {**_CLIENT_DEFAULTS}
    if not verify:
        kwargs["verify"] = False
    client = httpx.AsyncClient(
        **kwargs,
        event_hooks={"request": [_on_request], "response": [_on_response]},
        **({"mounts": mounts} if mounts else {}),
    )
    return client, log_entries
