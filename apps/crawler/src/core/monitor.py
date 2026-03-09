"""Single-job monitor dispatcher.

Pure function — takes board config and HTTP client, returns discovered jobs.
No database awareness, no side effects beyond HTTP requests.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.core.monitors import DiscoveredJob, get_discoverer

if TYPE_CHECKING:
    import httpx


@dataclass(slots=True)
class MonitorResult:
    """Result of monitoring a single board."""

    urls: set[str] = field(default_factory=set)
    jobs_by_url: dict[str, DiscoveredJob] | None = None
    new_sitemap_url: str | None = None
    filtered_count: int = 0


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


def _apply_url_filter(result: MonitorResult, config: dict) -> MonitorResult:
    """Filter URLs using url_filter from config. Returns new MonitorResult."""
    raw_filter = config.get("url_filter")
    if not raw_filter:
        return result

    if isinstance(raw_filter, str):
        include, exclude = raw_filter, None
    else:
        include = raw_filter.get("include")
        exclude = raw_filter.get("exclude")

    try:
        include_re = re.compile(include) if include else None
        exclude_re = re.compile(exclude) if exclude else None
    except re.error as e:
        structlog.get_logger().warning("monitor.url_filter_invalid", error=str(e))
        return result

    filtered_urls = set()
    for url in result.urls:
        if include_re and not include_re.search(url):
            continue
        if exclude_re and exclude_re.search(url):
            continue
        filtered_urls.add(url)

    filtered_jobs = None
    if result.jobs_by_url is not None:
        filtered_jobs = {u: j for u, j in result.jobs_by_url.items() if u in filtered_urls}

    removed = len(result.urls) - len(filtered_urls)
    return MonitorResult(
        urls=filtered_urls,
        jobs_by_url=filtered_jobs,
        new_sitemap_url=result.new_sitemap_url,
        filtered_count=removed,
    )


async def _save_raw(
    artifact_dir: Path,
    board_url: str,
    monitor_type: str,
    monitor_config: dict,
    http: httpx.AsyncClient,
) -> None:
    """Fetch and save raw monitor source data to *artifact_dir*.

    Called after the main discover pass.  The extra fetch is cheap (single
    HTTP request) and only happens during interactive workspace runs.
    """
    try:
        if monitor_type == "sitemap":
            sitemap_url = monitor_config.get("sitemap_url")
            if sitemap_url:
                resp = await http.get(sitemap_url)
                if resp.status_code == 200:
                    (artifact_dir / "sitemap.xml").write_text(resp.text)
        elif monitor_type == "nextdata":
            from src.shared.nextdata import extract_next_data

            resp = await http.get(board_url, follow_redirects=True)
            if resp.status_code == 200:
                data = extract_next_data(resp.text)
                if data:
                    (artifact_dir / "nextdata.json").write_text(
                        json.dumps(data, indent=2, default=str)
                    )
        elif monitor_type == "recruitee":
            api_base = monitor_config.get("api_base", "")
            slug = monitor_config.get("slug", "")
            if api_base:
                api_url = f"{api_base}/api/offers"
            elif slug:
                api_url = f"https://{slug}.recruitee.com/api/offers"
            else:
                api_url = None
            if api_url:
                resp = await http.get(api_url, follow_redirects=True)
                if resp.status_code == 200:
                    (artifact_dir / "response.json").write_text(
                        json.dumps(resp.json(), indent=2, default=str)
                    )
        elif monitor_type == "breezy":
            portal_url = monitor_config.get("portal_url")
            if not portal_url:
                slug = monitor_config.get("slug", "")
                if slug:
                    portal_url = f"https://{slug}.breezy.hr"
            if portal_url:
                api_url = f"{portal_url.rstrip('/')}/json"
                resp = await http.get(api_url, follow_redirects=True)
                if resp.status_code == 200:
                    (artifact_dir / "response.json").write_text(
                        json.dumps(resp.json(), indent=2, default=str)
                    )
        elif monitor_type == "hireology":
            slug = monitor_config.get("slug", "")
            if slug:
                api_url = f"https://api.hireology.com/v2/public/careers/{slug}?page_size=500"
                resp = await http.get(api_url)
                if resp.status_code == 200:
                    (artifact_dir / "response.json").write_text(
                        json.dumps(resp.json(), indent=2, default=str)
                    )
        elif monitor_type == "rippling":
            slug = monitor_config.get("slug", "")
            if slug:
                api_url = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
                resp = await http.get(api_url)
                if resp.status_code == 200:
                    (artifact_dir / "response.json").write_text(
                        json.dumps(resp.json(), indent=2, default=str)
                    )
        elif monitor_type in ("ashby", "greenhouse", "lever"):
            token = monitor_config.get("token", "")
            if monitor_type == "ashby":
                api_url = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
            elif monitor_type == "greenhouse":
                api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
            else:
                api_url = f"https://api.lever.co/v0/postings/{token}?limit=100"
            resp = await http.get(api_url)
            if resp.status_code == 200:
                (artifact_dir / "response.json").write_text(
                    json.dumps(resp.json(), indent=2, default=str)
                )
        elif monitor_type == "personio":
            slug = monitor_config.get("slug", "")
            if slug:
                api_url = f"https://{slug}.jobs.personio.de/xml?language=en"
                resp = await http.get(api_url, follow_redirects=True)
                if resp.status_code == 200:
                    (artifact_dir / "response.xml").write_text(resp.text)
        elif monitor_type == "rss":
            feed = monitor_config.get("feed_url")
            if not feed:
                preset = monitor_config.get("preset", "generic")
                from src.core.monitors.rss import _PRESETS, _build_feed_url

                p = _PRESETS.get(preset)
                if p:
                    feed = _build_feed_url(board_url, p.feed_paths[0])
            if feed:
                resp = await http.get(feed, follow_redirects=True)
                if resp.status_code == 200:
                    (artifact_dir / "response.xml").write_text(resp.text)
        elif monitor_type == "dom":
            resp = await http.get(board_url, follow_redirects=True)
            if resp.status_code == 200:
                (artifact_dir / "page.html").write_text(resp.text)
        elif monitor_type == "api_sniffer":
            # Raw data captured during Playwright session in discover();
            # re-fetch API response if api_url is in config.
            api_url = monitor_config.get("api_url")
            if api_url:
                resp = await http.get(api_url, follow_redirects=True)
                if resp.status_code == 200:
                    (artifact_dir / "response.json").write_text(
                        json.dumps(resp.json(), indent=2, default=str)
                    )
    except Exception:
        pass  # Best-effort — don't fail the monitor run


async def monitor_one(
    board_url: str,
    monitor_type: str,
    monitor_config: dict | None,
    http: httpx.AsyncClient,
    artifact_dir: Path | None = None,
    pw=None,
) -> MonitorResult:
    """Discover jobs on one board.

    This is the single-job layer — a pure function with no DB awareness.

    When *artifact_dir* is provided (workspace runs), raw source data
    (sitemap XML, API JSON, __NEXT_DATA__) is saved there for debugging.

    When *pw* is provided (an ``AsyncPlaywright`` instance), it is forwarded
    to the discover function to reuse a shared browser process.
    """
    discoverer = get_discoverer(monitor_type)
    config = monitor_config or {}

    # Build the board dict expected by discover functions
    board = {
        "board_url": board_url,
        "metadata": config,
    }

    discovered = await discoverer(board, http, pw=pw)
    result = _normalize_discovered(discovered)
    result = _apply_url_filter(result, config)
    if result.filtered_count:
        structlog.get_logger().info(
            "monitor.url_filter",
            kept=len(result.urls),
            removed=result.filtered_count,
        )

    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        await _save_raw(artifact_dir, board_url, monitor_type, config, http)

    return result
