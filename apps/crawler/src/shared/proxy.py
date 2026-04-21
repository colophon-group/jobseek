"""Provider-agnostic HTTP proxy layer.

A board opts into the proxy by setting ``"proxy": true`` in its
``monitor_config`` and/or ``scraper_config`` JSON (``data/boards.csv``).
The active provider is selected by ``PROXY_PROVIDER`` and its credential
URL (e.g. ``WEBSHARE_PROXY_URL``) is read from env by
:mod:`src.config`.

Adding a new provider = one entry in :data:`_PROVIDERS`. Adding a
rotating/failover provider = one new class implementing
:class:`ProxyProvider`. Call sites stay unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlparse

import structlog

log = structlog.get_logger()


class ProxyProvider(Protocol):
    name: str

    def proxy_url(self) -> str | None: ...


class StaticProxyProvider:
    """A provider whose egress is a single HTTP proxy URL.

    Fits Webshare (static IP), Decodo ISP, IPRoyal, and any other service
    that exposes ``http://user:pass@host:port``.
    """

    def __init__(self, name: str, url: str) -> None:
        self.name = name
        self._url = url

    def proxy_url(self) -> str | None:
        return self._url or None


def _webshare(settings) -> ProxyProvider | None:
    url = settings.webshare_proxy_url
    if not url:
        log.error("proxy.provider.missing_url", provider="webshare")
        return None
    return StaticProxyProvider("webshare", url)


def _decodo(settings) -> ProxyProvider | None:
    url = settings.decodo_proxy_url
    if not url:
        log.error("proxy.provider.missing_url", provider="decodo")
        return None
    return StaticProxyProvider("decodo", url)


_PROVIDERS: dict[str, Callable[..., ProxyProvider | None]] = {
    "webshare": _webshare,
    "decodo": _decodo,
    "none": lambda _s: None,
}


def get_provider() -> ProxyProvider | None:
    """Return the active provider, or ``None`` when disabled/misconfigured."""
    try:
        from src.config import settings
    except Exception:
        return None
    factory = _PROVIDERS.get(settings.proxy_provider)
    if factory is None:
        log.warning("proxy.provider.unknown", value=settings.proxy_provider)
        return None
    return factory(settings)


def httpx_proxy_for(*, use_proxy: bool) -> str | None:
    """Return the proxy URL to attach to an httpx client, or ``None``."""
    if not use_proxy:
        return None
    provider = get_provider()
    if provider is None:
        return None
    url = provider.proxy_url()
    if url:
        # DEBUG, not INFO: called on every client construction — at scale
        # this fires per-board-cycle × per-worker. Provider identity is a
        # deploy-time constant, so INFO-level surfaces no useful signal.
        log.debug("proxy.httpx", provider=provider.name, host=urlparse(url).hostname)
    return url


def playwright_proxy_for(*, use_proxy: bool) -> dict | None:
    """Return a Playwright ``proxy`` launch dict, or ``None``."""
    url = httpx_proxy_for(use_proxy=use_proxy)
    if not url:
        return None
    parsed = urlparse(url)
    out: dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        out["username"] = parsed.username
    if parsed.password:
        out["password"] = parsed.password
    return out
