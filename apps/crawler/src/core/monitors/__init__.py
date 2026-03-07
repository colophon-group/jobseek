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
    #: ISO 639-1 language code (e.g. "en", "de"). Detected or monitor-provided.
    language: str | None = None
    #: All language versions: {"en": {"title": ..., "description": ..., "locations": [...]}, ...}
    localizations: dict | None = None
    #: Optional structured data (skills, responsibilities, qualifications, etc.)
    extras: dict | None = None
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
    rich: bool = False  # True for API monitors that return full job data


_REGISTRY: list[MonitorType] = []


def register(
    name: str,
    discover: DiscoverFunc,
    cost: int,
    can_handle: CanHandleFunc | None = None,
    *,
    rich: bool = False,
) -> None:
    """Register a monitor type. Registry stays sorted by cost (cheapest first)."""
    _REGISTRY.append(
        MonitorType(
            name=name,
            cost=cost,
            discover=discover,
            can_handle=can_handle,
            rich=rich,
        )
    )
    _REGISTRY.sort(key=lambda m: m.cost)


def api_monitor_types() -> frozenset[str]:
    """Return the set of monitor type names that return rich (full) job data."""
    return frozenset(m.name for m in _REGISTRY if m.rich)


def is_rich_monitor(monitor_type: str, config: dict | None = None) -> bool:
    """Check if a monitor type returns rich data (scraper not needed).

    Statically-rich monitors (greenhouse, lever, etc.) always return True.
    api_sniffer is rich only when ``fields`` is present in config.
    """
    return monitor_type in api_monitor_types() or (
        monitor_type == "api_sniffer" and bool((config or {}).get("fields"))
    )


def all_monitor_types() -> frozenset[str]:
    """Return the set of all registered monitor type names."""
    return frozenset(m.name for m in _REGISTRY)


def get_discoverer(name: str) -> DiscoverFunc:
    """Look up a discover function by monitor type name."""
    for monitor in _REGISTRY:
        if monitor.name == name:
            return monitor.discover
    available = [m.name for m in _REGISTRY]
    raise ValueError(f"Unknown monitor type: {name!r}. Available: {available}")


def get_can_handle(name: str) -> CanHandleFunc:
    """Look up a can_handle function by monitor type name."""
    for monitor in _REGISTRY:
        if monitor.name == name:
            if monitor.can_handle is None:
                raise ValueError(f"Monitor {name!r} has no can_handle probe")
            return monitor.can_handle
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
    if name == "bite":
        key = metadata.get("key", "?")
        customer = metadata.get("customer")
        jobs = metadata.get("jobs")
        label = f"customer: {customer}" if customer else f"key: {key[:12]}..."
        if jobs is not None:
            return f"BITE API \u2014 {label}, {jobs} jobs"
        return f"BITE API \u2014 {label}"
    if name == "ashby":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Ashby API \u2014 token: {token}, {jobs} jobs"
        return f"Ashby API \u2014 token: {token}"
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
    if name == "dvinci":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"d.vinci API \u2014 slug: {slug}, {jobs} jobs"
        return f"d.vinci API \u2014 slug: {slug}"
    if name == "smartrecruiters":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"SmartRecruiters API \u2014 token: {token}, {jobs} jobs"
        return f"SmartRecruiters API \u2014 token: {token}"
    if name == "softgarden":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Softgarden \u2014 slug: {slug}, {jobs} jobs"
        return f"Softgarden \u2014 slug: {slug}"
    if name == "umantis":
        cname = metadata.get("cname")
        cid = metadata.get("customer_id", "?")
        region = metadata.get("region", "")
        jobs = metadata.get("jobs")
        label = f"CNAME: {cname}" if cname else f"ID: {cid}" + (f" ({region})" if region else "")
        if jobs is not None:
            return f"Umantis \u2014 {label}, {jobs} jobs"
        return f"Umantis \u2014 {label}"
    if name == "traffit":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"TRAFFIT API \u2014 slug: {slug}, {jobs} jobs"
        return f"TRAFFIT API \u2014 slug: {slug}"
    if name == "recruitee":
        slug = metadata.get("slug", "?")
        api_base = metadata.get("api_base", "")
        jobs = metadata.get("jobs")
        label = slug if slug != "?" else api_base
        if jobs is not None:
            return f"Recruitee API \u2014 {label}, {jobs} jobs"
        return f"Recruitee API \u2014 {label}"
    if name == "hireology":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Hireology API \u2014 slug: {slug}, {jobs} jobs"
        return f"Hireology API \u2014 slug: {slug}"
    if name == "rippling":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Rippling API \u2014 slug: {slug}, {jobs} jobs"
        return f"Rippling API \u2014 slug: {slug}"
    if name == "workable":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Workable API \u2014 token: {token}, {jobs} jobs"
        return f"Workable API \u2014 token: {token}"
    if name == "workday":
        company = metadata.get("company", "?")
        site = metadata.get("site", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Workday API \u2014 {company}/{site}, {jobs} jobs"
        return f"Workday API \u2014 {company}/{site}"
    if name == "pinpoint":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Pinpoint API \u2014 slug: {slug}, {jobs} jobs"
        return f"Pinpoint API \u2014 slug: {slug}"
    if name == "personio":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Personio XML \u2014 slug: {slug}, {jobs} jobs"
        return f"Personio XML \u2014 slug: {slug}"
    if name == "rss":
        preset = metadata.get("preset", "generic")
        feed_url = metadata.get("feed_url", "?")
        jobs = metadata.get("jobs")
        label = {
            "successfactors": "SuccessFactors RSS",
            "teamtailor": "Teamtailor RSS",
        }.get(preset, f"RSS ({preset})")
        count_str = f"{jobs}" if jobs is not None else ""
        # For paginated presets, first-page count may be approximate
        if preset == "teamtailor" and jobs is not None:
            from src.core.monitors.rss import _PRESETS

            tt = _PRESETS.get("teamtailor")
            if tt and jobs >= tt.page_size:
                count_str = f"{jobs}+"
        if count_str:
            return f"{label} \u2014 {feed_url}, {count_str} jobs"
        return f"{label} \u2014 {feed_url}"
    if name == "api_sniffer":
        items = metadata.get("items")
        total = metadata.get("total")
        score = metadata.get("score")
        api_url = metadata.get("api_url", "?")
        # Truncate API URL for display
        if len(api_url) > 80:
            api_url = api_url[:77] + "..."
        parts = []
        if items is not None:
            parts.append(f"{items} items")
        if total is not None:
            parts.append(f"total: {total}")
        if score is not None:
            parts.append(f"score: {score}")
        detail = ", ".join(parts) if parts else ""
        if detail:
            return f"API sniffer \u2014 {detail} at {api_url}"
        return f"API sniffer \u2014 {api_url}"
    return str(metadata)


async def probe_all_monitors(
    url: str,
    client: httpx.AsyncClient,
    timeout: float = 60.0,
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
        except TimeoutError:
            return monitor.name, None, f"Timeout ({timeout:.0f}s)"
        except Exception as exc:
            return monitor.name, None, f"Error: {exc}"

    tasks = [_probe_one(m) for m in _REGISTRY]
    return list(await asyncio.gather(*tasks))


# Import modules to trigger registration
from src.core.monitors import (  # noqa: E402
    api_sniffer,  # noqa: F401
    ashby,  # noqa: F401
    bite,  # noqa: F401
    dom,  # noqa: F401
    dvinci,  # noqa: F401
    greenhouse,  # noqa: F401
    hireology,  # noqa: F401
    lever,  # noqa: F401
    nextdata,  # noqa: F401
    personio,  # noqa: F401
    pinpoint,  # noqa: F401
    recruitee,  # noqa: F401
    rippling,  # noqa: F401
    rss,  # noqa: F401
    sitemap,  # noqa: F401
    smartrecruiters,  # noqa: F401
    softgarden,  # noqa: F401
    traffit,  # noqa: F401
    umantis,  # noqa: F401
    workable,  # noqa: F401
    workday,  # noqa: F401
)
