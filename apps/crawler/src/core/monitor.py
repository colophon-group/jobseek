"""Single-job monitor dispatcher.

Pure function — takes board config and HTTP client, returns discovered jobs.
No database awareness, no side effects beyond HTTP requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.core.monitors import DiscoveredJob, get_discoverer

if TYPE_CHECKING:
    import httpx


@dataclass(slots=True)
class MonitorResult:
    """Result of monitoring a single board."""

    urls: set[str] = field(default_factory=set)
    jobs_by_url: dict[str, DiscoveredJob] | None = None
    new_sitemap_url: str | None = None


def _normalize_discovered(
    discovered,
) -> MonitorResult:
    """Normalize discover results into a MonitorResult.

    Sitemap returns (set[str], str | None).
    Rich monitors return list[DiscoveredJob].
    URL-only monitors return set[str].
    """
    if isinstance(discovered, tuple):
        urls, sitemap_url = discovered
        return MonitorResult(urls=urls, new_sitemap_url=sitemap_url)
    if isinstance(discovered, set):
        return MonitorResult(urls=discovered)
    # list[DiscoveredJob]
    urls = {j.url for j in discovered}
    jobs_by_url = {j.url: j for j in discovered}
    return MonitorResult(urls=urls, jobs_by_url=jobs_by_url)


async def monitor_one(
    board_url: str,
    monitor_type: str,
    monitor_config: dict | None,
    http: httpx.AsyncClient,
) -> MonitorResult:
    """Discover jobs on one board.

    This is the single-job layer — a pure function with no DB awareness.
    """
    discoverer = get_discoverer(monitor_type)

    # Build the board dict expected by discover functions
    board = {
        "board_url": board_url,
        "metadata": monitor_config or {},
    }

    discovered = await discoverer(board, http)
    return _normalize_discovered(discovered)
