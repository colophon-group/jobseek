"""Extraction runtime ports and the current Python adapters.

Orchestration and persistence call these in-process ports instead of reaching
directly into the Python monitor/scraper registries.  A client for the framed
language-neutral contract can therefore replace monitor and scrape execution
independently while preserving scheduler and persistence behavior.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, Protocol

from src.metrics import (
    runtime_execution_duration_seconds,
    runtime_executions_total,
    runtime_output_items_total,
)
from src.runtime_cost.egress import (
    record_runtime_capability,
    seed_runtime_capabilities,
)

if TYPE_CHECKING:
    import httpx

    from src.core.monitor import MonitorResult
    from src.core.scrapers import JobContent


class MonitorRuntime(Protocol):
    implementation: str

    def stream(
        self,
        board_url: str,
        monitor_type: str,
        monitor_config: dict | None,
        http: httpx.AsyncClient,
        *,
        pw: object | None = None,
    ) -> AsyncIterator[MonitorResult]: ...


class ScrapeRuntime(Protocol):
    implementation: str

    async def scrape(
        self,
        url: str,
        scraper_type: str,
        scraper_config: dict | None,
        http: httpx.AsyncClient,
        *,
        pw: object | None = None,
        artifact_dir: Path | None = None,
    ) -> JobContent: ...


class PythonMonitorRuntime:
    implementation = "python"

    def __init__(self, streamer: Any | None = None) -> None:
        self._streamer = streamer

    async def stream(
        self,
        board_url: str,
        monitor_type: str,
        monitor_config: dict | None,
        http: httpx.AsyncClient,
        *,
        pw: object | None = None,
    ) -> AsyncIterator[MonitorResult]:
        # Import through the compatibility hub so existing monkeypatch-based
        # tests and workspace probes keep observing the same dispatcher.
        if self._streamer is None:
            from src import batch

            streamer = batch.monitor_one_stream
        else:
            streamer = self._streamer
        active_seconds = 0.0
        output_items = 0
        outcome = "incomplete"
        try:
            source = streamer(
                board_url,
                monitor_type,
                monitor_config,
                http,
                pw=pw,
            ).__aiter__()
            try:
                while True:
                    started = monotonic()
                    try:
                        result = await anext(source)
                    except StopAsyncIteration:
                        active_seconds += monotonic() - started
                        outcome = "success"
                        break
                    except BaseException:
                        active_seconds += monotonic() - started
                        raise
                    active_seconds += monotonic() - started
                    output_items += len(result.urls)
                    yield result
            finally:
                close_source = getattr(source, "aclose", None)
                if close_source is not None:
                    await close_source()
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except GeneratorExit:
            raise
        except BaseException:
            outcome = "error"
            raise
        finally:
            from src.core.monitors import all_monitor_types

            runtime_execution_duration_seconds.labels(
                stage="monitor",
                implementation=self.implementation,
            ).observe(active_seconds)
            runtime_executions_total.labels(
                stage="monitor",
                implementation=self.implementation,
                outcome=outcome,
            ).inc()
            record_runtime_capability(
                stage="monitor",
                implementation=self.implementation,
                capability=monitor_type,
                allowed_capabilities=all_monitor_types(),
                outcome=outcome,
            )
            if output_items:
                runtime_output_items_total.labels(
                    stage="monitor",
                    implementation=self.implementation,
                ).inc(output_items)


class PythonScrapeRuntime:
    implementation = "python"

    def __init__(self, scraper: Any | None = None) -> None:
        self._scraper = scraper

    async def scrape(
        self,
        url: str,
        scraper_type: str,
        scraper_config: dict | None,
        http: httpx.AsyncClient,
        *,
        pw: object | None = None,
        artifact_dir: Path | None = None,
    ) -> JobContent:
        if self._scraper is None:
            from src import batch

            scraper = batch.scrape_one
        else:
            scraper = self._scraper

        started = monotonic()
        outcome = "error"
        try:
            content = await scraper(
                url,
                scraper_type,
                scraper_config,
                http,
                pw=pw,
                artifact_dir=artifact_dir,
            )
            outcome = "success"
            runtime_output_items_total.labels(
                stage="scrape",
                implementation=self.implementation,
            ).inc()
            return content
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            from src.core.scrapers import all_scraper_types

            runtime_execution_duration_seconds.labels(
                stage="scrape",
                implementation=self.implementation,
            ).observe(monotonic() - started)
            runtime_executions_total.labels(
                stage="scrape",
                implementation=self.implementation,
                outcome=outcome,
            ).inc()
            record_runtime_capability(
                stage="scrape",
                implementation=self.implementation,
                capability=scraper_type,
                allowed_capabilities=all_scraper_types(),
                outcome=outcome,
            )


def seed_registered_runtime_capabilities() -> None:
    """Expose every registry-bounded Python capability at worker startup."""

    from src.core.monitors import all_monitor_types
    from src.core.scrapers import all_scraper_types

    seed_runtime_capabilities(
        stage="monitor",
        implementation="python",
        capabilities=all_monitor_types(),
    )
    seed_runtime_capabilities(
        stage="scrape",
        implementation="python",
        capabilities=all_scraper_types(),
    )


PYTHON_MONITOR_RUNTIME: MonitorRuntime = PythonMonitorRuntime()
PYTHON_SCRAPE_RUNTIME: ScrapeRuntime = PythonScrapeRuntime()


def monitor_result_payload(result: MonitorResult) -> dict[str, Any]:
    """Serialize a monitor result for Go/Python parity replay."""

    jobs = []
    if result.jobs_by_url is not None:
        jobs = [asdict(result.jobs_by_url[url]) for url in sorted(result.jobs_by_url)]
    return {
        "contract_version": "crawler.runtime/v1",
        "urls": sorted(result.urls),
        "jobs": jobs,
        "new_sitemap_url": result.new_sitemap_url,
        "filtered_count": result.filtered_count,
        "metadata_updates": result.metadata_updates,
        "hybrid": result.hybrid,
        "truncated": result.truncated,
    }


def scrape_result_payload(content: JobContent) -> dict[str, Any]:
    """Serialize extracted content for Go/Python parity replay."""

    return {
        "contract_version": "crawler.runtime/v1",
        **asdict(content),
    }
