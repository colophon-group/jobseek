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
        "amazon",
        "apify_meta",
        "ashby",
        "dvinci",
        "gem",
        "greenhouse",
        "hireology",
        "lever",
        "pinpoint",
        "recruitee",
        "rss",
        "traffit",
    }
)

# Personio is conditionally rich: XML feed provides descriptions,
# but the HTML fallback does not.  Richness is determined at runtime
# by ws run monitor based on actual description coverage.

_ALL_MONITOR_TYPES: frozenset[str] = _RICH_MONITORS | {
    "bite",
    "breezy",
    "join",
    "personio",
    "rippling",
    "smartrecruiters",
    "softgarden",
    "umantis",
    "workable",
    "workday",
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
    host = (parsed.hostname or "").lower()

    # Exact host prefixes
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") or (
        host.startswith("job-boards.") and host.endswith(".greenhouse.io")
    ):
        return "greenhouse"
    if host == "jobs.lever.co":
        return "lever"
    if host == "jobs.ashbyhq.com":
        return "ashby"
    if host == "jobs.gem.com":
        return "gem"
    if host == "apply.workable.com":
        return "workable"
    if host == "careers.smartrecruiters.com":
        return "smartrecruiters"
    if host.endswith(".breezy.hr"):
        return "breezy"

    # Suffix-based patterns
    if host.endswith(".recruitee.com"):
        return "recruitee"
    if ".jobs.personio." in host:
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
    if host.endswith(".dvinci-hr.com"):
        return "dvinci"
    if host.endswith(".softgarden.io"):
        return "softgarden"
    if host.endswith(".traffit.com"):
        return "traffit"

    # JOIN — join.com/companies/{slug}
    if host in ("join.com", "www.join.com"):
        return "join"

    # Umantis — recruitingapp-{ID}[.de|.ch].umantis.com
    if host.endswith(".umantis.com"):
        return "umantis"

    # Teamtailor — career sites on *.teamtailor.com
    if host.endswith(".teamtailor.com"):
        return "rss"

    # SAP SuccessFactors — career{N}.successfactors.eu / .com
    if ".successfactors." in host:
        return "rss"

    return None


_BREEZY_SCRAPER_CONFIG: dict = {
    "fallback": {
        "type": "dom",
        "config": {
            "render": False,
            "steps": [
                {"tag": "h1", "field": "title"},
                {
                    "tag": "li",
                    "attr": "class=location",
                    "field": "locations",
                    "regex": r"([A-Za-z .-]+,\s*[A-Z]{2})",
                },
                {
                    "tag": "p",
                    "field": "description",
                    "stop": "%BUTTON_APPLY_TO_POSITION%",
                    "html": True,
                },
            ],
        },
    },
}


def auto_scraper_type(
    monitor_type: str,
    config: dict | None = None,
) -> tuple[str, dict | None] | None:
    """Return the auto-configured scraper (type, config) for a monitor, or None.

    Some monitors automatically determine the scraper:
    - Rich monitors (greenhouse, lever, etc.) → ("skip", None)
    - Workday → ("workday", None)
    - Breezy → ("json-ld", {fallback dom config})
    - api_sniffer/nextdata with ``fields`` → ("skip", None)

    Returns None when manual scraper selection is needed.
    """
    if monitor_type in _RICH_MONITORS:
        return ("skip", None)
    if monitor_type == "join":
        return ("nextdata", None)
    if monitor_type == "breezy":
        return ("json-ld", _BREEZY_SCRAPER_CONFIG)
    if monitor_type == "bite":
        return ("bite", None)
    if monitor_type == "rippling":
        return ("rippling", None)
    if monitor_type == "smartrecruiters":
        return ("smartrecruiters", None)
    if monitor_type == "workable":
        return ("workable", None)
    if monitor_type == "workday":
        return ("workday", None)
    if monitor_type == "softgarden":
        return ("json-ld", None)
    if monitor_type in ("api_sniffer", "nextdata") and bool((config or {}).get("fields")):
        return ("skip", None)
    return None


def is_rich_monitor(monitor_type: str, config: dict | None = None) -> bool:
    """Check if a monitor type returns rich data (scraper not needed).

    Statically-rich monitors (greenhouse, lever, etc.) always return True.
    api_sniffer is rich only when ``fields`` is present in config.

    Note: this is narrower than ``auto_scraper_type``. Workday has an
    auto-configured scraper but is NOT rich (monitor returns URLs only).
    """
    return monitor_type in _RICH_MONITORS or (
        monitor_type in ("api_sniffer", "nextdata") and bool((config or {}).get("fields"))
    )
