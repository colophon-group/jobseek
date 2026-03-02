"""Monitor registry and shared types.

Monitors discover which jobs exist on a board. They return either:
- list[DiscoveredJob]: full job data (API monitors like greenhouse, lever)
- set[str]: URL set only (page monitors like sitemap, discover)
- tuple[set[str], str | None]: URL set + discovered metadata (sitemap)
"""

from __future__ import annotations

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
    """

    url: str
    title: str | None = None
    description: str | None = None
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    skills: list[str] | None = None
    responsibilities: list[str] | None = None
    qualifications: list[str] | None = None
    metadata: dict | None = None


# Discover functions return either set[str] (URL-only) or list[DiscoveredJob] (rich).
DiscoverFunc = Callable[..., Awaitable[set[str] | list[DiscoveredJob]]]

# can_handle: (url, client) -> dict | None.
# Returns metadata dict (truthy) when the monitor can handle the URL,
# or None when it cannot.
CanHandleFunc = Callable[..., dict | None | Awaitable[dict | None]]


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
) -> tuple[str, dict] | None:
    """Determine the best monitor type for a URL, trying cheapest first.

    Returns (monitor_name, metadata) or None if no monitor can handle the URL.
    """
    for monitor in _REGISTRY:
        if monitor.can_handle is None:
            continue
        result = monitor.can_handle(url, client)
        if hasattr(result, "__await__"):
            result = await result
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


# Import modules to trigger registration
from src.core.monitors import (  # noqa: E402
    greenhouse,  # noqa: F401
    lever,  # noqa: F401
    sitemap,  # noqa: F401
)
