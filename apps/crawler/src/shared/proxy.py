"""Per-domain proxy routing.

Reads ``settings.proxy_map`` (populated from the ``PROXY_MAP`` env var) and
provides helpers that wire proxy transports into httpx and Playwright.
"""

from __future__ import annotations

from urllib.parse import urlparse

import structlog

log = structlog.get_logger()


def _get_proxy_map() -> dict[str, str]:
    from src.config import settings

    return settings.proxy_map


def proxy_for_url(url: str) -> str | None:
    """Return the proxy URL for *url*'s hostname, or ``None``."""
    proxy_map = _get_proxy_map()
    if not proxy_map:
        return None
    hostname = urlparse(url).hostname
    return proxy_map.get(hostname) if hostname else None


def build_httpx_mounts() -> dict | None:
    """Build httpx ``mounts`` dict for all configured proxies.

    Returns ``None`` when the proxy map is empty.
    """
    import httpx

    proxy_map = _get_proxy_map()
    if not proxy_map:
        return None

    mounts: dict[str, httpx.AsyncHTTPTransport] = {}
    for domain, proxy_url in proxy_map.items():
        key = f"all://{domain}"
        mounts[key] = httpx.AsyncHTTPTransport(proxy=proxy_url)
        log.info("proxy.mount", domain=domain, proxy=proxy_url.split("@")[-1])
    return mounts


def build_playwright_proxy(url: str) -> dict | None:
    """Return a Playwright ``proxy`` dict for *url*, or ``None``."""
    proxy_url = proxy_for_url(url)
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    result: dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        result["username"] = parsed.username
    if parsed.password:
        result["password"] = parsed.password

    log.info("proxy.playwright", domain=urlparse(url).hostname, server=result["server"])
    return result
