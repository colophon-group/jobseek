from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from src.core.monitor import MonitorResult
from src.core.scrapers import get_scraper_type
from src.runtime.config import BoardRuntimeConfig
from src.runtime.extraction import PythonMonitorRuntime
from src.runtime_contract.v1 import runtime_pb2
from src.shared.browser import BrowserBackend, open_page


def test_board_runtime_config_normalizes_storage_types() -> None:
    config = BoardRuntimeConfig.from_mapping(
        {
            "board_url": "https://example.com/jobs",
            "crawler_type": "dom",
            "company_id": "company-1",
            "domain": "example.com",
            "check_interval_minutes": "30",
            "scrape_interval_hours": "12",
            "monitor_needs_browser": "1",
            "scraper_needs_browser": "false",
            "metadata": '{"render": true, "scraper_config": {}}',
        }
    )

    assert config.check_interval_minutes == 30
    assert config.monitor_needs_browser is True
    assert config.scraper_needs_browser is False
    assert config.scraper_config == {}


def test_board_runtime_config_preserves_worker_fail_open_decoding() -> None:
    config = BoardRuntimeConfig.from_mapping(
        {
            "board_url": "https://example.com/jobs",
            "crawler_type": "dom",
            "metadata": "not-json",
            "check_interval_minutes": "bad",
        }
    )

    assert config.metadata == {}
    assert config.check_interval_minutes == 60


@pytest.mark.parametrize("scraper_type", ["phuketall", "veryeast"])
def test_provider_scrapers_are_registered_for_runtime_dispatch(scraper_type: str) -> None:
    assert get_scraper_type(scraper_type) is not None


def test_board_runtime_config_strict_interval_decoding_preserves_board_record_errors() -> None:
    with pytest.raises(ValueError):
        BoardRuntimeConfig.from_mapping(
            {"check_interval_minutes": "bad"},
            strict_intervals=True,
        )

    config = BoardRuntimeConfig.from_mapping(
        {"check_interval_minutes": "60", "scrape_interval_hours": "bad"},
        strict_intervals=True,
    )
    assert config.scrape_interval_hours == 24


class _FakeBrowserBackend(BrowserBackend):
    implementation = "fake"

    async def start(self) -> _FakeBrowserBackend:
        return self

    @asynccontextmanager
    async def open_page(self, config=None, *, use_proxy=False):
        yield {"config": config, "use_proxy": use_proxy}

    async def stop(self) -> None:
        return None


async def test_open_page_dispatches_through_browser_backend() -> None:
    async with open_page(_FakeBrowserBackend(), {"wait": "load"}, use_proxy=True) as page:
        assert page == {"config": {"wait": "load"}, "use_proxy": True}


async def test_python_monitor_runtime_closes_nested_stream_when_abandoned() -> None:
    closed = False

    async def source():
        nonlocal closed
        try:
            yield MonitorResult(urls={"https://example.com/jobs/1"})
            yield MonitorResult(urls={"https://example.com/jobs/2"})
        finally:
            closed = True

    runtime = PythonMonitorRuntime(lambda *_args, **_kwargs: source())
    stream = runtime.stream(
        "https://example.com/jobs",
        "sitemap",
        {},
        None,  # type: ignore[arg-type]
    )

    first = await anext(stream)
    assert first.urls == {"https://example.com/jobs/1"}
    await stream.aclose()
    assert closed is True


def test_packaged_runtime_v1_binding_imports_from_crawler_package_root() -> None:
    assert runtime_pb2.DESCRIPTOR.package == "jobseek.crawler.runtime.v1"
    request = runtime_pb2.ExecutionRequest(contract_version="crawler.runtime/v1")
    assert request.contract_version == "crawler.runtime/v1"
