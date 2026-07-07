from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import structlog

import src.core.monitors as monitor_registry
from src.core.monitors import (
    ashby,
    fetch_page_text,
    greenhouse,
    hireology,
    lever,
    recruitee,
    workable,
)

ProbeCall = Callable[[httpx.AsyncClient], Awaitable[Any]]


def _rebind_probe_loggers(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (
        monitor_registry,
        ashby,
        greenhouse,
        hireology,
        lever,
        recruitee,
        workable,
    ):
        monkeypatch.setattr(module, "log", structlog.get_logger())


@contextmanager
def _capture_probe_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    previous_config = structlog.get_config()
    previous_config = {
        **previous_config,
        "processors": list(previous_config["processors"]),
    }
    capture = structlog.testing.LogCapture()
    try:
        structlog.configure(
            processors=[capture],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=False,
        )
        _rebind_probe_loggers(monkeypatch)
        yield capture.entries
    finally:
        structlog.configure(**previous_config)


def _invalid_json_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html>captcha</html>", request=request)


@pytest.mark.parametrize(
    ("event_name", "call", "expected", "expected_context"),
    [
        (
            "greenhouse.probe_failed",
            lambda client: greenhouse._probe_token("acme", client),
            (False, None),
            {"probe": "token", "token": "acme"},
        ),
        (
            "greenhouse.probe_failed",
            lambda client: greenhouse._fetch_job_count("acme", client),
            None,
            {"probe": "job_count", "token": "acme"},
        ),
        (
            "lever.probe_failed",
            lambda client: lever._probe_token("acme", client),
            (False, None),
            {"probe": "token", "token": "acme"},
        ),
        (
            "lever.probe_failed",
            lambda client: lever._fetch_job_count("acme", client),
            None,
            {"probe": "job_count", "token": "acme"},
        ),
        (
            "ashby.probe_failed",
            lambda client: ashby._probe_token("acme", client),
            (False, None),
            {"probe": "token", "token": "acme"},
        ),
        (
            "ashby.probe_failed",
            lambda client: ashby._fetch_job_count("acme", client),
            None,
            {"probe": "job_count", "token": "acme"},
        ),
        (
            "workable.probe_failed",
            lambda client: workable._probe_slug("acme", client),
            (False, None),
            {"probe": "slug", "slug": "acme"},
        ),
        (
            "workable.probe_failed",
            lambda client: workable._fetch_job_count("acme", client),
            None,
            {"probe": "job_count", "slug": "acme"},
        ),
        (
            "hireology.probe_failed",
            lambda client: hireology._probe_slug("acme", client),
            (False, None),
            {"probe": "slug", "slug": "acme"},
        ),
        (
            "recruitee.probe_failed",
            lambda client: recruitee._probe_api("https://acme.recruitee.com", client),
            (False, None),
            {"probe": "api", "api_base": "https://acme.recruitee.com"},
        ),
    ],
)
async def test_api_probe_exceptions_emit_debug_logs(
    monkeypatch: pytest.MonkeyPatch,
    event_name: str,
    call: ProbeCall,
    expected: object,
    expected_context: dict[str, object],
) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_invalid_json_response)) as client:
        with _capture_probe_logs(monkeypatch) as logs:
            result = await call(client)

    assert result == expected

    matching = [event for event in logs if event["event"] == event_name]
    assert len(matching) == 1
    event = matching[0]
    assert event["log_level"] == "debug"
    assert event["exc_info"] is True
    for key, value in expected_context.items():
        assert event[key] == value
    assert event["url"].startswith("https://")


async def test_fetch_page_text_logs_transient_fetch_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with _capture_probe_logs(monkeypatch) as logs:
            result = await fetch_page_text("https://example.com/careers", client)

    assert result is None
    matching = [event for event in logs if event["event"] == "monitors.fetch_page_text_failed"]
    assert len(matching) == 1
    event = matching[0]
    assert event["log_level"] == "debug"
    assert event["exc_info"] is True
    assert event["url"] == "https://example.com/careers"
