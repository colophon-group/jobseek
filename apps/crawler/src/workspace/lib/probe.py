"""Pure async probe functions: ``probe_monitor`` and ``probe_scraper``.

Lifted from CLI bindings in ``src.workspace.commands.crawl``.  These
functions exercise upstream probe machinery (``probe_all_monitors``,
``probe_scrapers``) and return structured, JSON-serializable results.

They:

- never write to disk
- never mutate the input :class:`BoardConfigState`
- never call ``out.*`` / ``sys.exit``

The CLI adapter is responsible for artifact persistence, board state
write-back, and human-readable formatting.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from src.workspace.lib.board_config import BoardConfigState
from src.workspace.lib.exceptions import WsProbeFailed

# ── Cost scoring (lifted verbatim — same constants as the CLI module) ──

_SUSPICIOUS_ROUND_THRESHOLDS = {1000, 5000, 10000, 50000, 100000}

_SCRAPER_COST_PER_JOB: dict[str, float] = {
    "json-ld": 0.3,
    "nextdata": 0.3,
    "embedded": 0.3,
    "dom": 0.5,
    "dom_render": 4.0,
    "api_sniffer": 3.0,
}

_DEFAULT_SCRAPER_COST = 0.3


# ── Result types ──────────────────────────────────────────────────────


@dataclass
class ProbeEntry:
    """One probe result row.

    ``metadata is None`` indicates the probe did not detect this monitor /
    scraper type for the given URL.  ``comment`` carries a human-readable
    diagnostic returned by the probe.
    """

    name: str
    metadata: dict[str, Any] | None
    comment: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "metadata": self.metadata, "comment": self.comment}


@dataclass
class ScoredProbeEntry:
    """A :class:`ProbeEntry` with cost scoring fields."""

    name: str
    metadata: dict[str, Any] | None
    comment: str
    monitor_cost: float | None
    initial_load: float
    rich: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProbeMonitorResult:
    """Result of :func:`probe_monitor`.

    ``entries`` is the raw probe output (one row per monitor type).
    ``scored`` is the cost-scored view used by the CLI to print
    high/low priority groups.
    """

    board_url: str
    current_jobs: int
    entries: list[ProbeEntry] = field(default_factory=list)
    scored: list[ScoredProbeEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "board_url": self.board_url,
            "current_jobs": self.current_jobs,
            "entries": [e.to_dict() for e in self.entries],
            "scored": [s.to_dict() for s in self.scored],
        }


@dataclass
class ProbeScraperResult:
    """Result of :func:`probe_scraper`.

    ``spa_suspect`` flags pages with very little static text (likely
    JS-rendered SPAs whose static probes are unreliable).
    """

    sample_urls: list[str]
    entries: list[ProbeEntry] = field(default_factory=list)
    spa_suspect: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_urls": list(self.sample_urls),
            "entries": [e.to_dict() for e in self.entries],
            "spa_suspect": self.spa_suspect,
        }


# ── Cost helpers ──────────────────────────────────────────────────────


def estimate_monitor_cost(name: str, n_jobs: int, metadata: dict | None = None) -> float:
    """Estimate seconds per single monitor invocation (one polling cycle).

    Mirrors the legacy ``_estimate_monitor_cost`` heuristic exactly — used
    by both the lib (for ``ProbeMonitorResult.scored``) and the CLI side
    (for cost write-back into ``cfg["cost"]``).
    """
    from src.workspace._compat import api_monitor_types

    if name in api_monitor_types():
        return 1.0
    if name == "api_sniffer":
        page_size = (metadata or {}).get("items", 50)
        pages = max(1, math.ceil(n_jobs / page_size))
        if (metadata or {}).get("browser"):
            return 5.0 + 0.5 * pages
        return 0.3 * pages
    if name == "sitemap":
        return 1.5
    if name in ("dom", "nextdata"):
        return 1.0
    return 2.0


def estimate_initial_load(n_jobs: int, scraper_per_job: float = _DEFAULT_SCRAPER_COST) -> float:
    """One-time cost to scrape all existing jobs on first run (URL-only monitors)."""
    return n_jobs * scraper_per_job


def score_probe_entries(entries: list[ProbeEntry], current_jobs: int) -> list[ScoredProbeEntry]:
    """Compute cost scores and rich-classification for each probe entry."""
    from src.workspace._compat import api_monitor_types

    n_jobs = current_jobs or 200
    scored: list[ScoredProbeEntry] = []
    for entry in entries:
        metadata = entry.metadata
        if metadata is not None:
            rich = entry.name in api_monitor_types() or (
                entry.name == "api_sniffer" and bool((metadata or {}).get("fields"))
            )
            mon_cost = estimate_monitor_cost(entry.name, n_jobs, metadata)
            init_load = 0.0 if rich else estimate_initial_load(n_jobs)
            scored.append(
                ScoredProbeEntry(
                    name=entry.name,
                    metadata=metadata,
                    comment=entry.comment,
                    monitor_cost=mon_cost,
                    initial_load=init_load,
                    rich=rich,
                )
            )
        else:
            scored.append(
                ScoredProbeEntry(
                    name=entry.name,
                    metadata=None,
                    comment=entry.comment,
                    monitor_cost=None,
                    initial_load=0.0,
                    rich=False,
                )
            )
    return scored


# ── Public lib functions ──────────────────────────────────────────────


async def probe_monitor(
    state: BoardConfigState,
    expected_count: int,
) -> ProbeMonitorResult:
    """Probe all monitor types for ``state.board_url``.

    Args:
        state: Frozen board snapshot. Only ``board_url`` is required;
            ``alias`` / ``slug`` are not used by this function.
        expected_count: The number of jobs the user reported visible on the
            careers page. Used solely for cost scoring; pass ``0`` for
            "unknown".

    Returns:
        :class:`ProbeMonitorResult` with raw entries and scored entries.

    Raises:
        WsProbeFailed: if Playwright / HTTP plumbing fails to start.
    """
    # Lazy imports keep this module fast to import (and avoid pulling
    # Playwright into every CLI startup).
    try:
        from playwright.async_api import async_playwright

        from src.core.monitors import probe_all_monitors
        from src.shared.http import create_http_client
    except ImportError as exc:  # pragma: no cover — environment issue
        raise WsProbeFailed(f"probe_monitor: missing dependency: {exc}") from exc

    http = create_http_client()
    try:
        async with async_playwright() as pw:
            raw = await probe_all_monitors(state.board_url, http, pw=pw)
    except Exception as exc:
        raise WsProbeFailed(f"probe_monitor failed: {exc}") from exc
    finally:
        await http.aclose()

    entries = [
        ProbeEntry(name=name, metadata=metadata, comment=comment) for name, metadata, comment in raw
    ]
    scored = score_probe_entries(entries, expected_count)
    return ProbeMonitorResult(
        board_url=state.board_url,
        current_jobs=expected_count,
        entries=entries,
        scored=scored,
    )


async def probe_scraper(
    state: BoardConfigState,
    sample_url: str | None = None,
    sample_urls: list[str] | None = None,
) -> ProbeScraperResult:
    """Probe all scraper types against the provided sample URLs.

    Args:
        state: Frozen board snapshot. ``state.sample_urls`` provides the
            default URL list (capped at 10).
        sample_url: Convenience shorthand — a single URL override.
        sample_urls: Multi-URL override. Wins over both ``sample_url`` and
            ``state.sample_urls`` when provided.

    Returns:
        :class:`ProbeScraperResult` with entries and ``spa_suspect`` flag.

    Raises:
        WsConfigMissing: if no sample URLs are available.
        WsProbeFailed: on upstream fatal errors.
    """
    from src.workspace.lib.exceptions import WsConfigMissing

    # Resolve target URL list. Explicit overrides win.
    targets: list[str]
    if sample_urls is not None and sample_urls:
        targets = list(sample_urls)
    elif sample_url:
        targets = [sample_url]
    else:
        targets = list(state.sample_urls)
    targets = targets[:10]

    if not targets:
        raise WsConfigMissing(
            "probe_scraper: no sample URLs available. Run a monitor first or pass sample_url(s)."
        )

    try:
        from playwright.async_api import async_playwright

        from src.core.scrapers import probe_scrapers
        from src.shared.http import create_http_client
    except ImportError as exc:  # pragma: no cover
        raise WsProbeFailed(f"probe_scraper: missing dependency: {exc}") from exc

    http = create_http_client()
    try:
        async with async_playwright() as pw:
            raw, spa_suspect = await probe_scrapers(targets, http, pw=pw)
    except Exception as exc:
        raise WsProbeFailed(f"probe_scraper failed: {exc}") from exc
    finally:
        await http.aclose()

    entries = [
        ProbeEntry(name=name, metadata=metadata, comment=comment) for name, metadata, comment in raw
    ]
    return ProbeScraperResult(
        sample_urls=targets,
        entries=entries,
        spa_suspect=bool(spa_suspect),
    )
