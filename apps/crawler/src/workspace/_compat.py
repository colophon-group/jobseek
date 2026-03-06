"""Static monitor type classification for standalone use.

Mirrors the runtime registry in ``src.core.monitors`` so that workspace
commands can classify monitor types without importing the full crawler core
(which pulls in asyncpg, playwright, etc.).

A sync test in ``tests/test_compat.py`` asserts these sets stay in sync
with the actual registry.
"""

from __future__ import annotations

_RICH_MONITORS: frozenset[str] = frozenset(
    {
        "ashby",
        "greenhouse",
        "hireology",
        "lever",
        "personio",
        "pinpoint",
        "recruitee",
        "rippling",
        "rss",
        "smartrecruiters",
        "workable",
        "workday",
    }
)

_ALL_MONITOR_TYPES: frozenset[str] = _RICH_MONITORS | {
    "sitemap",
    "nextdata",
    "dom",
    "api_sniffer",
}


def api_monitor_types() -> frozenset[str]:
    """Return the set of monitor type names that return rich (full) job data."""
    return _RICH_MONITORS


def all_monitor_types() -> frozenset[str]:
    """Return the set of all known monitor type names."""
    return _ALL_MONITOR_TYPES


def detect_ats_from_url(url: str) -> str | None:
    """Detect known ATS monitor type from a board URL, or None if unknown."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    # Exact host prefixes
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        return "greenhouse"
    if host == "jobs.lever.co":
        return "lever"
    if host == "jobs.ashbyhq.com":
        return "ashby"
    if host == "apply.workable.com":
        return "workable"
    if host == "careers.smartrecruiters.com":
        return "smartrecruiters"

    # Suffix-based patterns
    if host.endswith(".recruitee.com"):
        return "recruitee"
    if host.endswith(".jobs.personio.com") or host.endswith(".jobs.personio.de"):
        return "personio"
    if host.endswith(".pinpointhq.com"):
        return "pinpoint"
    if host.endswith(".mysmartrecruiters.com"):
        return "smartrecruiters"
    if host.endswith(".myworkdayjobs.com"):
        return "workday"
    if host.endswith(".rippling.com"):
        return "rippling"
    if host.endswith(".hireology.com"):
        return "hireology"

    return None


def is_rich_monitor(monitor_type: str, config: dict | None = None) -> bool:
    """Check if a monitor type returns rich data (scraper not needed).

    Statically-rich monitors (greenhouse, lever, etc.) always return True.
    api_sniffer is rich only when ``fields`` is present in config.
    """
    return monitor_type in _RICH_MONITORS or (
        monitor_type == "api_sniffer" and bool((config or {}).get("fields"))
    )
