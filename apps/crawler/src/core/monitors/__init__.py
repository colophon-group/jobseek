"""Monitor registry and shared types.

Monitors discover which jobs exist on a board. They return either:
- list[DiscoveredJob]: full job data (API monitors like greenhouse, lever)
- set[str]: URL set only (page monitors like sitemap, dom)
- tuple[set[str], str | None]: URL set + discovered metadata (sitemap)
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    import httpx


@dataclass(slots=True)
class DiscoveredJob:
    """A job discovered by a monitor.

    URL-only monitors (sitemap) set only ``url``.
    Rich monitors (greenhouse, lever) populate all available fields.

    ``description`` is an HTML fragment preserving the original document
    structure (headings, paragraphs, lists).  API monitors return HTML
    natively; scrapers must produce HTML as well.
    """

    url: str
    title: str | None = None
    #: HTML fragment preserving the original page structure.
    description: str | None = None
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    skills: list[str] | None = None
    #: Plain-text strings, one per bullet point.
    responsibilities: list[str] | None = None
    #: Plain-text strings, one per bullet point.
    qualifications: list[str] | None = None
    metadata: dict | None = None


# Discover functions return either set[str] (URL-only), list[DiscoveredJob] (rich),
# or tuple[set[str], str | None] (URL-only + metadata, e.g. sitemap).
DiscoverFunc = Callable[
    ..., Awaitable[set[str] | list[DiscoveredJob] | tuple[set[str], str | None]]
]

# can_handle: async (url, client) -> dict | None.
# Returns metadata dict (truthy) when the monitor can handle the URL,
# or None when it cannot.
CanHandleFunc = Callable[..., Awaitable[dict | None]]


@dataclass
class MonitorType:
    name: str
    cost: int  # lower = cheaper = tried first
    discover: DiscoverFunc
    can_handle: CanHandleFunc | None = None


_REGISTRY: list[MonitorType] = []


def register(
    name: str,
    discover: DiscoverFunc,
    cost: int,
    can_handle: CanHandleFunc | None = None,
) -> None:
    """Register a monitor type. Registry stays sorted by cost (cheapest first)."""
    _REGISTRY.append(
        MonitorType(
            name=name,
            cost=cost,
            discover=discover,
            can_handle=can_handle,
        )
    )
    _REGISTRY.sort(key=lambda m: m.cost)


def get_discoverer(name: str) -> DiscoverFunc:
    """Look up a discover function by monitor type name."""
    for monitor in _REGISTRY:
        if monitor.name == name:
            return monitor.discover
    available = [m.name for m in _REGISTRY]
    raise ValueError(f"Unknown monitor type: {name!r}. Available: {available}")


async def detect_monitor_type(
    url: str,
    client: httpx.AsyncClient,
    pw=None,
) -> tuple[str, dict] | None:
    """Determine the best monitor type for a URL, trying cheapest first.

    Returns (monitor_name, metadata) or None if no monitor can handle the URL.
    """
    for monitor in _REGISTRY:
        if monitor.can_handle is None:
            continue
        result = await monitor.can_handle(url, client, pw=pw)
        if result is not None:
            return monitor.name, result
    return None


def slugs_from_url(url: str) -> list[str]:
    """Derive candidate ATS board slugs from a URL.

    Extracts the second-level domain label, e.g.
    "https://www.isomorphiclabs.com/job-openings" -> ["isomorphiclabs"]
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 2:
        return [parts[-2]]
    return [parts[0]] if parts else []


async def fetch_page_text(
    url: str,
    client: httpx.AsyncClient,
    max_chars: int = 500_000,
) -> str | None:
    """Fetch a page and return its text content (capped), or None on error."""
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return None
        return resp.text[:max_chars]
    except Exception:
        return None


def _build_comment(name: str, metadata: dict) -> str:
    """Build a human-readable comment from probe metadata."""
    if name == "greenhouse":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Greenhouse API \u2014 token: {token}, {jobs} jobs"
        return f"Greenhouse API \u2014 token: {token}"
    if name == "lever":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Lever API \u2014 token: {token}, {jobs} jobs"
        return f"Lever API \u2014 token: {token}"
    if name == "nextdata":
        path = metadata.get("path", "?")
        count = metadata.get("count")
        render = " (render)" if metadata.get("render") else ""
        if count is not None:
            return f"__NEXT_DATA__ \u2014 {count} items at {path}{render}"
        return f"__NEXT_DATA__ \u2014 {path}{render}"
    if name == "sitemap":
        sitemap_url = metadata.get("sitemap_url", "?")
        urls = metadata.get("urls")
        if urls is not None:
            return f"Sitemap \u2014 {urls} URLs at {sitemap_url}"
        return f"Sitemap \u2014 {sitemap_url}"
    if name == "dom":
        urls = metadata.get("urls")
        if urls is not None:
            return f"DOM \u2014 {urls} job links found (static)"
        return "DOM \u2014 link extraction"
    return str(metadata)


async def probe_all_monitors(
    url: str,
    client: httpx.AsyncClient,
    timeout: float = 30.0,
    pw=None,
) -> list[tuple[str, dict | None, str]]:
    """Run can_handle for every monitor type in parallel.

    Returns [(name, metadata_or_none, comment), ...] sorted by registry order.

    When *pw* is provided, it is forwarded to monitors that use Playwright.
    """

    async def _probe_one(monitor: MonitorType) -> tuple[str, dict | None, str]:
        if monitor.can_handle is None:
            return monitor.name, None, "No probe available"
        try:
            result = await asyncio.wait_for(
                monitor.can_handle(url, client, pw=pw),
                timeout=timeout,
            )
            if result is not None:
                return monitor.name, result, _build_comment(monitor.name, result)
            return monitor.name, None, "Not detected"
        except asyncio.TimeoutError:
            return monitor.name, None, f"Timeout ({timeout:.0f}s)"
        except Exception as exc:
            return monitor.name, None, f"Error: {exc}"

    tasks = [_probe_one(m) for m in _REGISTRY]
    return list(await asyncio.gather(*tasks))


# Import modules to trigger registration
from src.core.monitors import (  # noqa: E402
    dom,  # noqa: F401
    greenhouse,  # noqa: F401
    lever,  # noqa: F401
    nextdata,  # noqa: F401
    sitemap,  # noqa: F401
)
