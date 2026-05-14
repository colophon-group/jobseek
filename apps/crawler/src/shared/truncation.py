"""Helpers for signalling ``MAX_JOBS`` truncation back to the pipeline (#3216).

Several monitors paginate against ATS APIs and cap collection at
``MAX_JOBS`` (50,000 by default) as a safety stop. Before #3216 each
monitor silently returned the truncated list, the pipeline treated the
run as a clean success, and ``_MARK_GONE_BY_TIMESTAMP`` tombstoned every
URL beyond the cap — the exact silent-data-loss shape #2722, #2737,
#2748 fixed for fetch-failure-driven truncation.

These helpers wrap the truncated discovery in a :class:`MonitorResult`
with ``truncated=True``. The board processor sees the flag, marks the
cycle as partial, suppresses gone-detection, and increments the
``crawler_monitor_truncated_total`` counter so ops can spot a board that
has outgrown the cap. The run is still recorded as a success — failing
hard would alert every cycle on the few boards that genuinely have
> 50k postings without telling us anything new about their health.

The helpers are intentionally tiny — the pattern is to drop the
``jobs = sorted(...)[:MAX_JOBS]`` slicing entirely and ``return``
the helper output. The wrapper keeps ALL collected jobs in the result;
the cap was a safety stop, not a quality signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult
    from src.core.monitors import DiscoveredJob


def truncated_rich_result(jobs: list[DiscoveredJob]) -> MonitorResult:
    """Wrap a rich (``list[DiscoveredJob]``) discovery as truncated.

    Used by API monitors whose ``discover()`` returns full job data.
    The list is preserved as-is — the pipeline still inserts every URL
    it received; only gone-detection is suppressed for the cycle.
    """
    # Local import to avoid a top-level cycle with src.core.monitors.
    from src.core.monitor import MonitorResult

    return MonitorResult(
        urls={j.url for j in jobs},
        jobs_by_url={j.url: j for j in jobs},
        truncated=True,
    )


def truncated_url_result(urls: set[str]) -> MonitorResult:
    """Wrap a URL-only (``set[str]``) discovery as truncated.

    Used by URL-list monitors whose ``discover()`` returns just URLs;
    the scraper fetches per-posting content later.
    """
    from src.core.monitor import MonitorResult

    return MonitorResult(urls=set(urls), truncated=True)
