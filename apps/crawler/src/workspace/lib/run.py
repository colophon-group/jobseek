"""Pure async run functions: ``run_monitor`` and ``run_scraper``.

Lifted from CLI bindings in ``src.workspace.commands.crawl``.  These
functions exercise upstream pipelines (``monitor_one``, ``scrape_one``)
and return structured, JSON-serializable results.

They:

- never write to disk (the caller persists ``artifact_dir`` via ``monitor_one``)
- never mutate the input :class:`BoardConfigState`
- never call ``out.*`` / ``sys.exit``

The CLI adapter is responsible for artifact persistence (HTTP log,
events, processed jobs/results, quality reports), board state write-back,
and human-readable formatting.

Note on artifact directory: ``monitor_one`` and ``scrape_one`` both
accept an optional ``artifact_dir`` for raw-data dumps (HTML, raw API
responses).  The lib accepts that path through the function signature
so the *caller* controls where raw data lands; the lib itself does not
construct or own those paths.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.workspace.lib.board_config import BoardConfigState
from src.workspace.lib.exceptions import (
    WsConfigMissing,
    WsMonitorRunFailed,
    WsScraperRunFailed,
)

# ── Result types ──────────────────────────────────────────────────────


@dataclass
class RunMonitorResult:
    """Structured result of :func:`run_monitor`.

    Attributes mirror what the CLI adapter needs to render output, build
    quality reports, and persist board state — all without re-running
    the monitor.

    ``urls`` is sorted for deterministic output (the underlying
    ``MonitorResult.urls`` is a ``set``).  ``jobs_by_url`` is preserved
    as the original dict so callers can run their own iteration order.
    """

    board_url: str
    monitor_type: str
    urls: list[str]
    jobs_by_url: dict[str, Any] | None
    filtered_count: int
    elapsed_seconds: float
    has_rich_data: bool
    sample_urls: list[str]
    description_samples: list[dict[str, Any]] = field(default_factory=list)
    quality: dict[str, Any] | None = None
    http_log: list[dict[str, Any]] = field(default_factory=list)
    log_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # ``jobs_by_url`` may contain DiscoveredJob dataclasses; we only
        # expose URL keys + a length here for JSON safety. Callers that
        # need the rich objects should use the attribute directly.
        return {
            "board_url": self.board_url,
            "monitor_type": self.monitor_type,
            "urls": list(self.urls),
            "job_count": len(self.urls),
            "has_rich_data": self.has_rich_data,
            "filtered_count": self.filtered_count,
            "elapsed_seconds": self.elapsed_seconds,
            "sample_urls": list(self.sample_urls),
            "description_samples": list(self.description_samples),
            "quality": self.quality,
        }


@dataclass
class ScrapedJob:
    """One scraped item from :func:`run_scraper`.

    ``content`` is the raw ``JobContent`` dataclass from the scraper —
    callers can ``dataclasses.asdict(content)`` for JSON output.
    """

    url: str
    content: Any  # core.scrapers.JobContent — kept loose to avoid hard import
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        try:
            content_dict = asdict(self.content) if self.content is not None else None
        except TypeError:
            content_dict = None
        return {
            "url": self.url,
            "content": content_dict,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass
class RunScraperResult:
    """Structured result of :func:`run_scraper`."""

    scraper_type: str
    items: list[ScrapedJob] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    description_samples: list[dict[str, Any]] = field(default_factory=list)
    avg_elapsed_seconds: float = 0.0
    http_log: list[dict[str, Any]] = field(default_factory=list)
    log_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scraper_type": self.scraper_type,
            "count": len(self.items),
            "skipped": [list(s) for s in self.skipped],
            "items": [it.to_dict() for it in self.items],
            "description_samples": list(self.description_samples),
            "avg_elapsed_seconds": self.avg_elapsed_seconds,
        }


# ── Public lib functions ──────────────────────────────────────────────


async def run_monitor(
    state: BoardConfigState,
    config_name: str | None = None,
    *,
    artifact_dir: Path | None = None,
    capture_http_log: list[dict[str, Any]] | None = None,
    log_events: list[dict[str, Any]] | None = None,
) -> RunMonitorResult:
    """Test-run ``monitor_one`` against ``state.board_url``.

    Args:
        state: Frozen board snapshot. Must have ``monitor_type`` set.
        config_name: Informational; carried into the result for logging.
            Lib does not look up configs by name — the caller already
            resolved ``state.monitor_config`` from the named config.
        artifact_dir: Optional path passed to ``monitor_one`` for raw-data
            dumps. The lib does **not** create or write to this directory
            itself — it merely forwards the path.  Pass ``None`` to skip.
        capture_http_log: Pre-allocated list to collect HTTP exchanges.
            If ``None``, the lib creates a fresh list and returns it on
            the result.  Callers (the CLI) typically allocate one so
            they can pass it to artifact persistence after the run.
        log_events: Pre-allocated list to collect structlog events.

    Returns:
        :class:`RunMonitorResult` populated with URLs, jobs, timing, and
        quality data.

    Raises:
        WsConfigMissing: if ``state.monitor_type`` is unset.
        WsMonitorRunFailed: wraps any exception from ``monitor_one``.
    """
    if not state.monitor_type:
        raise WsConfigMissing("run_monitor: no monitor_type in BoardConfigState")

    try:
        from playwright.async_api import async_playwright

        from src.core.monitor import monitor_one
        from src.shared.http import create_logging_http_client
    except ImportError as exc:  # pragma: no cover
        raise WsMonitorRunFailed(f"run_monitor: missing dependency: {exc}") from exc

    http, http_log = create_logging_http_client(
        verify=state.ssl_verify,
        use_proxy=state.use_proxy,
    )
    if capture_http_log is not None:
        # Caller wants their own list as the canonical log; we still rely
        # on ``http_log`` from the factory and copy at the end.
        pass

    try:
        async with async_playwright() as pw:
            start = time.monotonic()
            try:
                raw_result = await monitor_one(
                    state.board_url,
                    state.monitor_type,
                    state.monitor_config or None,
                    http,
                    artifact_dir=artifact_dir,
                    pw=pw,
                )
            except Exception as exc:
                raise WsMonitorRunFailed(str(exc) or exc.__class__.__name__) from exc
            elapsed = time.monotonic() - start
    finally:
        await http.aclose()

    urls = sorted(raw_result.urls)
    has_rich = raw_result.jobs_by_url is not None

    # Description samples for quality validation downstream (legacy parity).
    desc_samples: list[dict[str, Any]] = []
    if raw_result.jobs_by_url:
        for job in list(raw_result.jobs_by_url.values())[:5]:
            desc = getattr(job, "description", None)
            if desc:
                plain = re.sub(r"<[^>]+>", "", desc).strip()
                desc_samples.append({"length": len(plain), "snippet": plain[:200]})

    # Sample URLs (cap at 10, deterministic by sort to make snapshots stable).
    # The legacy CLI used random.sample; we let the *caller* decide sampling
    # if they want randomness — return the sorted top-10 here.  The CLI
    # adapter passes the pre-existing random.sample call through if it wants
    # legacy parity.
    sample = list(urls)[:10]

    # Quality report (rich monitors only).
    quality: dict[str, Any] | None = None
    if raw_result.jobs_by_url:
        quality = _build_monitor_quality(raw_result.jobs_by_url)

    final_http_log = capture_http_log if capture_http_log is not None else list(http_log)
    if capture_http_log is not None:
        capture_http_log.extend(http_log)
    final_events = log_events if log_events is not None else []

    return RunMonitorResult(
        board_url=state.board_url,
        monitor_type=state.monitor_type,
        urls=urls,
        jobs_by_url=raw_result.jobs_by_url,
        filtered_count=raw_result.filtered_count,
        elapsed_seconds=round(elapsed, 4),
        has_rich_data=has_rich,
        sample_urls=sample,
        description_samples=desc_samples,
        quality=quality,
        http_log=final_http_log,
        log_events=final_events,
    )


async def run_scraper(
    state: BoardConfigState,
    config_name: str | None = None,
    sample_urls: list[str] | None = None,
    *,
    artifact_dir: Path | None = None,
    capture_http_log: list[dict[str, Any]] | None = None,
    log_events: list[dict[str, Any]] | None = None,
) -> RunScraperResult:
    """Test-run ``scrape_one`` against the provided / inherited sample URLs.

    Args:
        state: Frozen board snapshot. Must have ``scraper_type`` set.
        config_name: Informational; carried into the result for logging.
        sample_urls: Override list of URLs to scrape. ``None`` falls back
            to ``state.sample_urls``.
        artifact_dir: Optional path passed to ``scrape_one`` for raw HTML
            dumps. Lib never creates or writes here.
        capture_http_log: Pre-allocated list for HTTP exchanges.
        log_events: Pre-allocated list for structlog events.

    Returns:
        :class:`RunScraperResult` with extracted content per URL.

    Raises:
        WsConfigMissing: when ``state.scraper_type`` is unset or no URLs.
        WsScraperRunFailed: on a fatal upstream error (per-URL HTTP errors
            are captured in ``result.skipped`` instead).
    """
    if not state.scraper_type:
        raise WsConfigMissing("run_scraper: no scraper_type in BoardConfigState")

    targets: list[str] = list(sample_urls) if sample_urls else list(state.sample_urls)
    if not targets:
        raise WsConfigMissing(
            "run_scraper: no URLs to scrape. Run the monitor first or pass sample_urls."
        )

    try:
        from httpx import HTTPStatusError
        from playwright.async_api import async_playwright

        from src.core.scrape import scrape_one
        from src.processing.scrape import _apply_defaults
        from src.shared.http import create_logging_http_client
    except ImportError as exc:  # pragma: no cover
        raise WsScraperRunFailed(f"run_scraper: missing dependency: {exc}") from exc

    http, http_log = create_logging_http_client(
        verify=state.ssl_verify,
        use_proxy=state.use_proxy,
    )

    items: list[ScrapedJob] = []
    skipped: list[tuple[str, str]] = []
    try:
        async with async_playwright() as pw:
            for i, url in enumerate(targets):
                job_id = f"sample-{i}"
                start = time.monotonic()
                try:
                    content = await scrape_one(
                        url,
                        state.scraper_type,
                        state.scraper_config or None,
                        http,
                        artifact_dir=artifact_dir,
                        job_id=job_id,
                        pw=pw,
                    )
                except HTTPStatusError as exc:
                    skipped.append((url, str(exc.response.status_code)))
                    continue
                except Exception as exc:
                    raise WsScraperRunFailed(str(exc) or exc.__class__.__name__) from exc
                content = _apply_defaults(content, state.scraper_config or {})
                elapsed = time.monotonic() - start
                items.append(ScrapedJob(url=url, content=content, elapsed_seconds=elapsed))
    finally:
        await http.aclose()

    # Description samples (first 5 with descriptions).
    desc_samples: list[dict[str, Any]] = []
    for it in items:
        desc = getattr(it.content, "description", None)
        if desc and len(desc_samples) < 5:
            plain = re.sub(r"<[^>]+>", "", desc).strip()
            desc_samples.append({"length": len(plain), "snippet": plain[:200]})

    avg_elapsed = sum(it.elapsed_seconds for it in items) / len(items) if items else 0.0

    final_http_log = capture_http_log if capture_http_log is not None else list(http_log)
    if capture_http_log is not None:
        capture_http_log.extend(http_log)
    final_events = log_events if log_events is not None else []

    return RunScraperResult(
        scraper_type=state.scraper_type or "",
        items=items,
        skipped=skipped,
        description_samples=desc_samples,
        avg_elapsed_seconds=round(avg_elapsed, 4),
        http_log=final_http_log,
        log_events=final_events,
    )


# ── Quality helpers ───────────────────────────────────────────────────


_MONITOR_QUALITY_FIELDS = (
    "title",
    "description",
    "locations",
    "employment_type",
    "job_location_type",
    "date_posted",
    "base_salary",
    "skills",
    "responsibilities",
    "qualifications",
)

_EXTRAS_FIELDS = {"skills", "responsibilities", "qualifications", "valid_through"}


def _get_field(obj: object, field_name: str) -> object:
    """Get a quality field value from a job object (extras-aware)."""
    if field_name in _EXTRAS_FIELDS:
        extras = getattr(obj, "extras", None)
        if isinstance(extras, dict):
            return extras.get(field_name)
        return None
    return getattr(obj, field_name, None)


def _build_monitor_quality(jobs_by_url: dict[str, Any]) -> dict[str, Any]:
    """Build the quality summary dict consumed by the CLI / artifacts."""
    jobs = list(jobs_by_url.values())
    total = len(jobs)
    fields_summary: dict[str, dict[str, int]] = {}
    for fname in _MONITOR_QUALITY_FIELDS:
        count = sum(1 for j in jobs if _get_field(j, fname))
        pct = round(count / total * 100) if total else 0
        fields_summary[fname] = {"count": count, "pct": pct}
    return {"total": total, "fields": fields_summary}
