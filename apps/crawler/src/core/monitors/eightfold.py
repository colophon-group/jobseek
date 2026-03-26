"""Eightfold AI careers portal monitor.

Thin wrapper over the sitemap monitor for Eightfold-powered career sites.
Every Eightfold portal exposes a sitemap at ``/careers/sitemap.xml`` with
job URLs matching ``/careers/job/``.  The PCSX search API has a hard 2 000
offset cap, making sitemap the only reliable method for large employers.

Detects both ``*.eightfold.ai`` subdomains and white-label (custom) domains
by probing for the ``/api/pcsx/`` endpoint.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import fetch_page_text, register
from src.core.monitors.sitemap import discover as sitemap_discover

log = structlog.get_logger()

_EIGHTFOLD_SUBDOMAIN_RE = re.compile(
    r"^(?:[\w-]+)\.eightfold\.ai$", re.IGNORECASE
)


def _is_eightfold_domain(url: str) -> bool:
    """Return True when the URL is on an ``*.eightfold.ai`` subdomain."""
    host = (urlparse(url).hostname or "").lower()
    return bool(_EIGHTFOLD_SUBDOMAIN_RE.match(host))


def _sitemap_url(board_url: str) -> str:
    """Derive the sitemap URL from a board URL."""
    parsed = urlparse(board_url)
    return f"{parsed.scheme}://{parsed.netloc}/careers/sitemap.xml"


async def _probe_pcsx(host: str, client: httpx.AsyncClient) -> bool:
    """Check whether the host exposes the Eightfold PCSX API."""
    try:
        resp = await client.get(
            f"https://{host}/api/pcsx/search",
            params={"domain": host, "query": "", "location": "", "start": "0"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return "data" in data and "positions" in (data.get("data") or {})
        # 403 with "PCSX is not enabled" still confirms Eightfold
        if resp.status_code == 403:
            try:
                body = resp.json()
                return "pcsx" in body.get("message", "").lower()
            except Exception:
                pass
    except Exception:
        pass
    return False


async def discover(
    board: dict, client: httpx.AsyncClient, pw=None
) -> tuple[set[str], str | None]:
    """Delegate to the sitemap monitor with pre-configured sitemap URL."""
    metadata = board.get("metadata") or {}
    if not metadata.get("sitemap_url"):
        metadata = {**metadata, "sitemap_url": _sitemap_url(board["board_url"])}
    sitemap_board = {**board, "metadata": metadata}
    return await sitemap_discover(sitemap_board, client, pw=pw)


async def can_handle(
    url: str, client: httpx.AsyncClient | None = None, pw=None
) -> dict | None:
    """Detect Eightfold: domain pattern, page HTML markers, or PCSX API probe."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # Fast path: *.eightfold.ai subdomain
    if _is_eightfold_domain(url):
        sitemap = _sitemap_url(url)
        result: dict = {"sitemap_url": sitemap}
        if client:
            from src.core.monitors.sitemap import _try_fetch_xml, _extract_urls

            root = await _try_fetch_xml(sitemap, client)
            if root is not None:
                urls = _extract_urls(root)
                job_urls = [u for u in urls if "/careers/job/" in u]
                result["urls"] = len(job_urls)
        return result

    if client is None:
        return None

    # Check page HTML for Eightfold markers
    html = await fetch_page_text(url, client)
    if html:
        lower = html.lower()
        if "eightfold.ai" in lower or "pcsx" in lower or "eightfoldai" in lower:
            sitemap = _sitemap_url(url)
            from src.core.monitors.sitemap import _try_fetch_xml, _extract_urls

            root = await _try_fetch_xml(sitemap, client)
            if root is not None:
                urls = _extract_urls(root)
                job_urls = [u for u in urls if "/careers/job/" in u]
                return {"sitemap_url": sitemap, "urls": len(job_urls)}

    # Last resort: probe PCSX API on the host
    if await _probe_pcsx(host, client):
        sitemap = _sitemap_url(url)
        return {"sitemap_url": sitemap}

    return None


register("eightfold", discover, cost=8, can_handle=can_handle)
