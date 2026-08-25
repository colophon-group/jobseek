"""Single-job monitor dispatcher.

Pure function — takes board config and HTTP client, returns discovered jobs.
No database awareness, no side effects beyond HTTP requests.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
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


@dataclass(frozen=True, slots=True)
class _UrlTransformCollisionConfig:
    """Validated deterministic policy for intentional many-to-one URL transforms."""

    preferred_source_patterns: tuple[re.Pattern[str], ...]
    canonical_identity_pattern: re.Pattern[str]
    identity_metadata_key: str
    stream_buffer_limit: int


def _url_transform_collision_config(
    config: dict,
) -> _UrlTransformCollisionConfig | None:
    """Validate the opt-in cross-batch collision policy."""
    transform = config.get("url_transform")
    if not isinstance(transform, dict):
        return None
    policy = transform.get("collision_policy")
    if policy is None:
        return None
    if policy != "prefer_source_pattern":
        raise ValueError("url_transform.collision_policy must be 'prefer_source_pattern'")

    raw_patterns = transform.get("collision_preferred_source_patterns")
    if (
        not isinstance(raw_patterns, list)
        or not raw_patterns
        or len(raw_patterns) > 32
        or any(
            not isinstance(pattern, str) or not pattern or len(pattern) > 2_048
            for pattern in raw_patterns
        )
    ):
        raise ValueError(
            "url_transform.collision_preferred_source_patterns must be a "
            "non-empty bounded regex list"
        )
    try:
        preferred_patterns = tuple(re.compile(pattern) for pattern in raw_patterns)
    except re.error as exc:
        raise ValueError(f"url_transform collision source pattern is invalid: {exc}") from exc

    raw_identity_pattern = transform.get("collision_canonical_identity_regex")
    if (
        not isinstance(raw_identity_pattern, str)
        or not raw_identity_pattern
        or len(raw_identity_pattern) > 2_048
    ):
        raise ValueError(
            "url_transform.collision_canonical_identity_regex must be a non-empty bounded regex"
        )
    try:
        identity_pattern = re.compile(raw_identity_pattern)
    except re.error as exc:
        raise ValueError(
            f"url_transform collision canonical identity regex is invalid: {exc}"
        ) from exc
    if identity_pattern.groups != 1:
        raise ValueError(
            "url_transform.collision_canonical_identity_regex must contain "
            "exactly one capture group"
        )

    identity_metadata_key = transform.get("collision_identity_metadata_key")
    if (
        not isinstance(identity_metadata_key, str)
        or not identity_metadata_key
        or len(identity_metadata_key) > 128
    ):
        raise ValueError(
            "url_transform.collision_identity_metadata_key must be non-empty bounded text"
        )
    stream_buffer_limit = transform.get("collision_stream_buffer_limit")
    if (
        not isinstance(stream_buffer_limit, int)
        or isinstance(stream_buffer_limit, bool)
        or not 1 <= stream_buffer_limit <= 50_000
    ):
        raise ValueError(
            "url_transform.collision_stream_buffer_limit must be an integer from 1 to 50000"
        )
    return _UrlTransformCollisionConfig(
        preferred_source_patterns=preferred_patterns,
        canonical_identity_pattern=identity_pattern,
        identity_metadata_key=identity_metadata_key,
        stream_buffer_limit=stream_buffer_limit,
    )


def _collision_source_rank(
    source_url: str,
    collision: _UrlTransformCollisionConfig,
) -> tuple[int, str]:
    """Rank aliases by configured source preference, then by the full source URL."""
    preference = next(
        (
            index
            for index, pattern in enumerate(collision.preferred_source_patterns)
            if pattern.search(source_url)
        ),
        len(collision.preferred_source_patterns),
    )
    return preference, source_url


def _validate_collision_identity(
    job: DiscoveredJob,
    canonical_url: str,
    collision: _UrlTransformCollisionConfig,
) -> None:
    """Require rich provider metadata to authenticate the transformed identity."""
    match = collision.canonical_identity_pattern.fullmatch(canonical_url)
    metadata_identity = (
        job.metadata.get(collision.identity_metadata_key)
        if isinstance(job.metadata, dict)
        else None
    )
    if (
        match is None
        or not isinstance(metadata_identity, str)
        or metadata_identity != match.group(1)
    ):
        raise ValueError("url_transform collision provider identity does not match canonical URL")


def _normalize_discovered(
    discovered,
    *,
    reject_conflicting_duplicate_urls: bool = False,
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
    jobs_by_url: dict[str, DiscoveredJob] = {}
    for job in discovered:
        existing = jobs_by_url.get(job.url)
        if reject_conflicting_duplicate_urls and existing is not None and existing != job:
            raise ValueError(
                "url_transform collision batch emitted conflicting content "
                f"for source URL {job.url}"
            )
        jobs_by_url[job.url] = job
    return MonitorResult(urls=set(jobs_by_url), jobs_by_url=jobs_by_url)


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
    """Rewrite URLs with one transform or an ordered transform pipeline."""
    raw_transform = config.get("url_transform")
    if not raw_transform:
        return result

    transforms = raw_transform if isinstance(raw_transform, list) else [raw_transform]
    compiled: list[tuple[re.Pattern[str], str]] = []
    try:
        for transform in transforms:
            if not isinstance(transform, dict):
                raise ValueError("url_transform entries must be objects")
            find = transform.get("find", "")
            replace = transform.get("replace", "")
            if not isinstance(find, str) or not find:
                raise ValueError("url_transform find must be a non-empty regex string")
            if not isinstance(replace, str):
                raise ValueError("url_transform replace must be a string")
            compiled.append((re.compile(find), replace))
    except (ValueError, re.error) as exc:
        structlog.get_logger().warning("monitor.url_transform_invalid", error=str(exc))
        return result
    collision = _url_transform_collision_config(config)

    new_urls: set[str] = set()
    url_map: dict[str, str] = {}  # old -> new
    for url in sorted(result.urls):
        new_url = url
        for pattern, replace in compiled:
            new_url = pattern.sub(replace, new_url)
        new_urls.add(new_url)
        url_map[url] = new_url

    new_jobs = None
    if result.jobs_by_url is not None:
        if collision is None:
            new_jobs = {}
            for old_url, job in result.jobs_by_url.items():
                new_url = url_map.get(old_url, old_url)
                job.url = new_url
                new_jobs[new_url] = job
        else:
            selected: dict[
                str,
                tuple[tuple[int, str], DiscoveredJob],
            ] = {}
            for old_url in sorted(result.jobs_by_url):
                job = result.jobs_by_url[old_url]
                new_url = url_map.get(old_url, old_url)
                _validate_collision_identity(job, new_url, collision)
                rank = _collision_source_rank(old_url, collision)
                existing = selected.get(new_url)
                if existing is None or rank < existing[0]:
                    selected[new_url] = (rank, job)
            new_jobs = {
                new_url: dataclass_replace(selected_job, url=new_url)
                for new_url, (_rank, selected_job) in selected.items()
            }

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


def _merge_collision_stream_result(
    buffered: MonitorResult | None,
    result: MonitorResult,
    collision: _UrlTransformCollisionConfig,
) -> MonitorResult:
    """Accumulate an intentional many-to-one transform across stream batches."""
    if buffered is None:
        buffered = MonitorResult(
            jobs_by_url={} if result.jobs_by_url is not None else None,
        )
    elif (buffered.jobs_by_url is None) != (result.jobs_by_url is None):
        raise ValueError("url_transform collision buffering cannot mix rich and URL-only batches")

    combined_urls = buffered.urls | result.urls
    if len(combined_urls) > collision.stream_buffer_limit:
        raise ValueError(
            "url_transform collision stream exceeded configured buffer limit "
            f"({len(combined_urls)} > {collision.stream_buffer_limit})"
        )
    buffered.urls = combined_urls

    if buffered.jobs_by_url is not None and result.jobs_by_url is not None:
        for source_url, job in result.jobs_by_url.items():
            existing = buffered.jobs_by_url.get(source_url)
            if existing is not None and existing != job:
                raise ValueError(
                    "url_transform collision stream emitted conflicting content "
                    f"for source URL {source_url}"
                )
            buffered.jobs_by_url[source_url] = job

    if result.new_sitemap_url is not None:
        if (
            buffered.new_sitemap_url is not None
            and buffered.new_sitemap_url != result.new_sitemap_url
        ):
            raise ValueError("url_transform collision stream emitted conflicting sitemap URLs")
        buffered.new_sitemap_url = result.new_sitemap_url
    if result.metadata_updates:
        metadata_updates = dict(buffered.metadata_updates or {})
        for key, value in result.metadata_updates.items():
            if key in metadata_updates and metadata_updates[key] != value:
                raise ValueError(
                    f"url_transform collision stream emitted conflicting metadata for key {key}"
                )
            metadata_updates[key] = value
        buffered.metadata_updates = metadata_updates

    buffered.filtered_count += result.filtered_count
    buffered.security_filtered_count += result.security_filtered_count
    buffered.hybrid = buffered.hybrid or result.hybrid
    buffered.truncated = buffered.truncated or result.truncated
    return buffered


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
    collision = _url_transform_collision_config(config)

    # Build the board dict expected by discover functions
    board = {
        "board_url": board_url,
        "metadata": config,
    }

    from src.shared.http import client_for

    async with client_for(http, config) as client:
        discovered = await discoverer(board, client, pw=pw)
    result = _normalize_discovered(
        discovered,
        reject_conflicting_duplicate_urls=collision is not None,
    )
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
    collision = _url_transform_collision_config(config)

    if stream_fn is None:
        yield await monitor_one(board_url, monitor_type, monitor_config, http, pw=pw)
        return

    board = {"board_url": board_url, "metadata": config}
    from src.shared.http import client_for

    buffered: MonitorResult | None = None
    async with client_for(http, config) as client:
        async for batch in stream_fn(board, client, pw=pw):
            result = _normalize_discovered(
                batch,
                reject_conflicting_duplicate_urls=collision is not None,
            )
            result = _apply_url_filter(result, config)
            result = _apply_job_filter(result, config)
            result = _apply_url_allowlist(result, config)
            if collision is not None:
                buffered = _merge_collision_stream_result(
                    buffered,
                    result,
                    collision,
                )
            else:
                result = _apply_url_transform(result, config)
                yield result
    if buffered is not None:
        yield _apply_url_transform(buffered, config)
