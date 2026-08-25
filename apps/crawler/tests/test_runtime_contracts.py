from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from src.core.monitor import MonitorResult
from src.core.monitors import DiscoveredJob
from src.core.scrapers import JobContent, get_scraper_type
from src.runtime.config import BoardRuntimeConfig
from src.runtime.extraction import (
    PythonMonitorRuntime,
    monitor_result_payload,
    scrape_result_payload,
)
from src.shared.browser import BrowserBackend, open_page

_CONTRACTS = Path(__file__).parents[1] / "contracts" / "v1"


def _validate(schema_name: str, payload: dict) -> None:
    schemas = [json.loads(path.read_text()) for path in _CONTRACTS.glob("*.schema.json")]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    schema = next(schema for schema in schemas if schema["$id"].endswith(schema_name))
    jsonschema.Draft202012Validator(schema, registry=registry).validate(payload)


def test_all_runtime_contract_schemas_are_valid_draft_2020_12() -> None:
    for path in _CONTRACTS.glob("*.schema.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text()))


def test_board_runtime_config_normalizes_storage_types_and_validates() -> None:
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
    _validate("board-runtime-config.schema.json", config.as_contract_payload())


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
    config = BoardRuntimeConfig.from_mapping(
        {
            "board_url": "https://example.com/jobs/1",
            "crawler_type": "dom",
            "metadata": json.dumps({"scraper_type": scraper_type, "scraper_config": {}}),
        }
    )

    assert get_scraper_type(scraper_type) is not None
    _validate("board-runtime-config.schema.json", config.as_contract_payload())


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


def test_board_runtime_config_rejects_nonpositive_contract_intervals() -> None:
    config = BoardRuntimeConfig.from_mapping({"check_interval_minutes": "0"})

    with pytest.raises(ValueError, match="must be positive"):
        config.as_contract_payload()


def test_monitor_and_scrape_payloads_validate() -> None:
    job = DiscoveredJob(
        url="https://example.com/jobs/1",
        title="Engineer",
        description="<p>Build systems</p>",
        locations=["Zürich"],
        language="en",
    )
    monitor_payload = monitor_result_payload(
        MonitorResult(
            urls={job.url},
            jobs_by_url={job.url: job},
            metadata_updates={"watermark": "42"},
        )
    )
    scrape_payload = scrape_result_payload(
        JobContent(
            title="Engineer",
            description="<p>Build systems</p>",
            locations=["Zürich"],
            language="en",
        )
    )

    _validate("monitor-result.schema.json", monitor_payload)
    _validate("scrape-result.schema.json", scrape_payload)


def test_execution_request_and_stream_frames_validate() -> None:
    board_config = BoardRuntimeConfig.from_mapping(
        {
            "board_url": "https://example.com/jobs",
            "crawler_type": "sitemap",
            "metadata": {},
        }
    ).as_contract_payload()
    request = {
        "contract_version": "crawler.runtime/v1",
        "request_id": "request-1",
        "origin_request_id": "monitor:board-1:revision-1:due-1",
        "attempt_id": "attempt-1",
        "kind": "monitor",
        "deadline": "2026-08-25T12:00:00Z",
        "traceparent": None,
        "config_revision": "revision-1",
        "config_fingerprint": "sha256:abc",
        "board_config": board_config,
        "input": {"monitor_type": "sitemap"},
    }
    result = monitor_result_payload(MonitorResult(urls={"https://example.com/jobs/1"}))
    batch_frame = {
        "contract_version": "crawler.runtime/v1",
        "request_id": "request-1",
        "frame_type": "monitor_batch",
        "sequence": 0,
        "result": result,
    }
    terminal_frame = {
        "contract_version": "crawler.runtime/v1",
        "request_id": "request-1",
        "frame_type": "terminal",
        "status": "success",
        "frames": 1,
        "output_items": 1,
        "active_duration_ms": 12.5,
    }

    _validate("execution-request.schema.json", request)
    _validate("execution-frame.schema.json", batch_frame)
    _validate("execution-frame.schema.json", terminal_frame)


def test_execution_request_kind_selects_the_matching_input_shape() -> None:
    board_config = BoardRuntimeConfig.from_mapping(
        {"board_url": "https://example.com/jobs", "crawler_type": "sitemap"}
    ).as_contract_payload()
    request = {
        "contract_version": "crawler.runtime/v1",
        "request_id": "request-1",
        "origin_request_id": "monitor:board-1:revision-1:due-1",
        "attempt_id": "attempt-1",
        "kind": "monitor",
        "deadline": "2026-08-25T12:00:00Z",
        "traceparent": None,
        "config_revision": "revision-1",
        "config_fingerprint": "sha256:abc",
        "board_config": board_config,
        "input": {
            "source_url": "https://example.com/jobs/1",
            "scraper_type": "dom",
            "scrape_step": 0,
        },
    }

    with pytest.raises(jsonschema.ValidationError):
        _validate("execution-request.schema.json", request)


def test_browser_result_makes_unsupported_capabilities_non_authoritative() -> None:
    unsupported = {
        "contract_version": "crawler.runtime/v1",
        "outcome": "unsupported",
        "backend": "lightpanda",
        "final_url": "https://example.com/jobs",
        "status": None,
        "html": None,
        "action_outcomes": [],
        "captures": [],
        "evaluations": {},
        "unsupported_capabilities": ["persistent_session"],
        "error": None,
    }
    _validate("browser-result.schema.json", unsupported)

    unsupported["html"] = "<html>partial</html>"
    with pytest.raises(jsonschema.ValidationError):
        _validate("browser-result.schema.json", unsupported)


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
