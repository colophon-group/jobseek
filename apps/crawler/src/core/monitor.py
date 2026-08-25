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

from src.core.monitors import DiscoveredJob, get_discoverer, get_save_raw, get_stream_fn

if TYPE_CHECKING:
    import httpx


@dataclass(slots=True)
class MonitorResult:
    """Result of monitoring a single board."""

    urls: set[str] = field(default_factory=set)
    jobs_by_url: dict[str, DiscoveredJob] | None = None
    new_sitemap_url: str | None = None
    filtered_count: int = 0
    # URLs rejected by an explicit provider-boundary allowlist. Unlike the
    # ordinary extraction filter (which commonly removes non-job sitemap
    # pages), any non-zero value is a security signal for one-shot migrations.
    security_filtered_count: int = 0
    #: JSONB patch to merge into ``job_board.metadata`` after a successful
    #: batch (e.g. incremental monitors use this to persist a high-water mark).
    metadata_updates: dict | None = None
    #: Set to True by hybrid monitors (e.g. eightfold) that provide rich data
    #: only for a subset of ``urls``. The pipeline uses this flag to skip the
    #: content-update path for "touched" jobs (which would otherwise overwrite
    #: previously-scraped fields with nulls from partial rich data).
    hybrid: bool = False
    #: Set to True when the monitor's discovery hit its ``MAX_JOBS`` cap and
    #: returned a truncated list (#3216). The pipeline treats the run as a
    #: partial success: it inserts the URLs that ARE in the batch but skips
    #: ``_MARK_GONE_BY_TIMESTAMP`` for the cycle, so the unseen tail beyond
    #: the cap is not falsely tombstoned. Any single truncated batch in a
    #: streamed monitor run is sufficient to flip this for the cycle.
    truncated: bool = False


def _normalize_discovered(
    discovered,
) -> MonitorResult:
    """Normalize discover results into a MonitorResult.

    Sitemap returns (set[str], str | None).
    Rich monitors return list[DiscoveredJob].
    URL-only monitors return set[str].
    Hybrid monitors pre-build and return a MonitorResult directly.
    """
    if isinstance(discovered, MonitorResult):
        return discovered
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
        filtered_count=result.filtered_count + removed,
        security_filtered_count=result.security_filtered_count,
        metadata_updates=result.metadata_updates,
        hybrid=result.hybrid,
        truncated=result.truncated,
    )


def _apply_job_filter(result: MonitorResult, config: dict) -> MonitorResult:
    """Filter rich jobs by regexes over their discovered content.

    ``job_filter`` accepts the same string or ``{include, exclude}`` shape as
    ``url_filter``.  The searchable text contains the title, description,
    locations, and metadata.  URL-only monitors cannot apply this filter and
    are left unchanged with a warning.
    """
    raw_filter = config.get("job_filter")
    if not raw_filter:
        return result

    if result.jobs_by_url is None:
        structlog.get_logger().warning("monitor.job_filter_unavailable")
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
        structlog.get_logger().warning("monitor.job_filter_invalid", error=str(e))
        return result

    filtered_jobs: dict[str, DiscoveredJob] = {}
    for url, job in result.jobs_by_url.items():
        content = "\n".join(
            part
            for part in (
                job.title,
                job.description,
                "\n".join(job.locations or []),
                json.dumps(job.metadata, ensure_ascii=False, sort_keys=True)
                if job.metadata
                else None,
            )
            if part
        )
        if include_re and not include_re.search(content):
            continue
        if exclude_re and exclude_re.search(content):
            continue
        filtered_jobs[url] = job

    # Hybrid monitors may carry URL-only entries alongside their rich subset.
    # Those entries have no content to evaluate, so preserve them fail-open.
    url_only = result.urls - result.jobs_by_url.keys()
    filtered_urls = url_only | (result.urls & filtered_jobs.keys())
    removed = len(result.urls) - len(filtered_urls)
    return MonitorResult(
        urls=filtered_urls,
        jobs_by_url=filtered_jobs,
        new_sitemap_url=result.new_sitemap_url,
        filtered_count=result.filtered_count + removed,
        security_filtered_count=result.security_filtered_count,
        metadata_updates=result.metadata_updates,
        hybrid=result.hybrid,
        truncated=result.truncated,
    )


def _apply_url_allowlist(result: MonitorResult, config: dict) -> MonitorResult:
    """Apply an exact, fail-closed provider-boundary URL allowlist.

    ``url_filter`` remains the broad extraction mechanism and intentionally
    fails open on malformed operator regexes for backward compatibility.
    ``url_allowlist`` is for security-sensitive identity transforms: it is a
    single regex evaluated with ``fullmatch`` and rejects every URL when the
    configured contract is empty, non-string, or invalid.
    """
    if "url_allowlist" not in config:
        return result

    raw_allowlist = config.get("url_allowlist")
    try:
        if not isinstance(raw_allowlist, str) or not raw_allowlist:
            raise ValueError("url_allowlist must be a non-empty regex string")
        pattern = re.compile(raw_allowlist)
    except (ValueError, re.error) as exc:
        structlog.get_logger().warning("monitor.url_allowlist_invalid", error=str(exc))
        return MonitorResult(
            urls=set(),
            jobs_by_url={} if result.jobs_by_url is not None else None,
            new_sitemap_url=result.new_sitemap_url,
            filtered_count=result.filtered_count,
            security_filtered_count=result.security_filtered_count + len(result.urls),
            metadata_updates=result.metadata_updates,
            hybrid=result.hybrid,
            truncated=result.truncated,
        )

    filtered_urls = {url for url in result.urls if pattern.fullmatch(url)}
    filtered_jobs = None
    if result.jobs_by_url is not None:
        filtered_jobs = {
            url: job for url, job in result.jobs_by_url.items() if url in filtered_urls
        }

    return MonitorResult(
        urls=filtered_urls,
        jobs_by_url=filtered_jobs,
        new_sitemap_url=result.new_sitemap_url,
        filtered_count=result.filtered_count,
        security_filtered_count=(
            result.security_filtered_count + len(result.urls) - len(filtered_urls)
        ),
        metadata_updates=result.metadata_updates,
        hybrid=result.hybrid,
        truncated=result.truncated,
    )


def _apply_url_transform(result: MonitorResult, config: dict) -> MonitorResult:
    """Rewrite URLs using url_transform {find, replace} from config."""
    transform = config.get("url_transform")
    if not transform:
        return result
    find = transform.get("find", "")
    replace = transform.get("replace", "")
    if not find:
        return result

    try:
        pattern = re.compile(find)
    except re.error as e:
        structlog.get_logger().warning("monitor.url_transform_invalid", error=str(e))
        return result

    new_urls: set[str] = set()
    url_map: dict[str, str] = {}  # old -> new
    for url in result.urls:
        new_url = pattern.sub(replace, url)
        new_urls.add(new_url)
        url_map[url] = new_url

    new_jobs = None
    if result.jobs_by_url is not None:
        new_jobs = {}
        for old_url, job in result.jobs_by_url.items():
            new_url = url_map.get(old_url, old_url)
            job.url = new_url
            new_jobs[new_url] = job

    return MonitorResult(
        urls=new_urls,
        jobs_by_url=new_jobs,
        new_sitemap_url=result.new_sitemap_url,
        filtered_count=result.filtered_count,
        security_filtered_count=result.security_filtered_count,
        metadata_updates=result.metadata_updates,
        hybrid=result.hybrid,
        truncated=result.truncated,
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
    save_raw = get_save_raw(monitor_type)
    if save_raw is None:
        return

    try:
        await save_raw(artifact_dir, board_url, monitor_config, http)
    except Exception:
        structlog.get_logger().warning(
            "monitor.save_raw_failed",
            monitor_type=monitor_type,
            board_url=board_url,
            exc_info=True,
        )


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

    from src.shared.http import client_for

    async with client_for(http, config) as client:
        discovered = await discoverer(board, client, pw=pw)
    result = _normalize_discovered(discovered)
    before_filter = len(result.urls)
    result = _apply_url_filter(result, config)
    url_removed = before_filter - len(result.urls)
    if url_removed:
        structlog.get_logger().info(
            "monitor.url_filter",
            kept=len(result.urls),
            removed=url_removed,
        )
    before_filter = len(result.urls)
    result = _apply_job_filter(result, config)
    job_removed = before_filter - len(result.urls)
    if job_removed:
        structlog.get_logger().info(
            "monitor.job_filter",
            kept=len(result.urls),
            removed=job_removed,
        )
    before_allowlist = len(result.urls)
    result = _apply_url_allowlist(result, config)
    allowlist_removed = before_allowlist - len(result.urls)
    if allowlist_removed:
        structlog.get_logger().warning(
            "monitor.url_allowlist_rejected",
            kept=len(result.urls),
            removed=allowlist_removed,
        )
    result = _apply_url_transform(result, config)

    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        await _save_raw(artifact_dir, board_url, monitor_type, config, http)

    return result


async def monitor_one_stream(
    board_url: str,
    monitor_type: str,
    monitor_config: dict | None,
    http: httpx.AsyncClient,
    *,
    pw=None,
):
    """Async generator yielding MonitorResult per batch."""
    stream_fn = get_stream_fn(monitor_type)
    config = monitor_config or {}

    if stream_fn is None:
        yield await monitor_one(board_url, monitor_type, monitor_config, http, pw=pw)
        return

    board = {"board_url": board_url, "metadata": config}
    from src.shared.http import client_for

    async with client_for(http, config) as client:
        async for batch in stream_fn(board, client, pw=pw):
            result = _normalize_discovered(batch)
            result = _apply_url_filter(result, config)
            result = _apply_job_filter(result, config)
            result = _apply_url_allowlist(result, config)
            result = _apply_url_transform(result, config)
            yield result
