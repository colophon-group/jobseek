from __future__ import annotations

import asyncio

import httpx
import pytest

from src.runtime_cost.egress import (
    bind_runtime_egress,
    capability_executions_total,
    current_egress_attribution,
    origin_attempts_total,
    origin_outcomes_total,
    record_runtime_capability,
    response_body_bytes_total,
    seed_runtime_capabilities,
)
from src.shared.http import RequestHostTrackingTransport


def _value(counter, **labels: str) -> float:
    return float(counter.labels(**labels)._value.get())


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


class _ResponseTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=_Chunks(b"abc", b"defg"))


class _ErrorTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("test failure", request=request)


@pytest.mark.asyncio
async def test_transport_attributes_attempt_outcome_and_only_consumed_stream_bytes() -> None:
    labels = {"stage": "monitor", "execution_class": "http", "egress": "proxy"}
    attempts_before = _value(origin_attempts_total, **labels)
    responses_before = _value(origin_outcomes_total, **labels, outcome="response")
    bytes_before = _value(response_body_bytes_total, **labels)

    transport = RequestHostTrackingTransport(_ResponseTransport(), egress="proxy")
    async with httpx.AsyncClient(transport=transport) as client:
        with bind_runtime_egress("monitor", "http"):
            async with client.stream("GET", "https://example.com/jobs") as response:
                chunks = response.aiter_raw()
                assert await anext(chunks) == b"abc"

    assert _value(origin_attempts_total, **labels) - attempts_before == 1
    assert _value(origin_outcomes_total, **labels, outcome="response") - responses_before == 1
    assert _value(response_body_bytes_total, **labels) - bytes_before == 3


@pytest.mark.asyncio
async def test_transport_error_is_conserved_and_context_is_inherited_by_child_task() -> None:
    labels = {"stage": "detail", "execution_class": "browser", "egress": "direct"}
    attempts_before = _value(origin_attempts_total, **labels)
    errors_before = _value(origin_outcomes_total, **labels, outcome="transport_error")

    transport = RequestHostTrackingTransport(_ErrorTransport())
    async with httpx.AsyncClient(transport=transport) as client:
        with bind_runtime_egress("detail", "browser"):
            task = asyncio.create_task(client.get("https://example.com/job/1"))
            with pytest.raises(httpx.ConnectError):
                await task

    assert _value(origin_attempts_total, **labels) - attempts_before == 1
    assert _value(origin_outcomes_total, **labels, outcome="transport_error") - errors_before == 1
    assert current_egress_attribution() is None


@pytest.mark.asyncio
async def test_transport_is_a_noop_without_worker_attribution() -> None:
    labels = {"stage": "monitor", "execution_class": "http", "egress": "direct"}
    attempts_before = _value(origin_attempts_total, **labels)
    bytes_before = _value(response_body_bytes_total, **labels)

    transport = RequestHostTrackingTransport(_ResponseTransport())
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://example.com/jobs")
        assert response.content == b"abcdefg"

    assert _value(origin_attempts_total, **labels) == attempts_before
    assert _value(response_body_bytes_total, **labels) == bytes_before


def test_runtime_capability_labels_are_registry_bounded() -> None:
    allowed = frozenset({"known"})
    seed_runtime_capabilities(
        stage="scrape",
        implementation="test-egress",
        capabilities=allowed,
    )
    known_labels = {
        "stage": "scrape",
        "implementation": "test-egress",
        "capability": "known",
        "outcome": "success",
    }
    unknown_labels = {
        "stage": "scrape",
        "implementation": "test-egress",
        "capability": "_unknown",
        "outcome": "error",
    }
    known_before = _value(capability_executions_total, **known_labels)
    unknown_before = _value(capability_executions_total, **unknown_labels)

    record_runtime_capability(
        stage="scrape",
        implementation="test-egress",
        capability="known",
        allowed_capabilities=allowed,
        outcome="success",
    )
    record_runtime_capability(
        stage="scrape",
        implementation="test-egress",
        capability="arbitrary-config-text",
        allowed_capabilities=allowed,
        outcome="error",
    )

    assert _value(capability_executions_total, **known_labels) - known_before == 1
    assert _value(capability_executions_total, **unknown_labels) - unknown_before == 1


@pytest.mark.parametrize(
    ("stage", "execution_class"),
    [("unknown", "http"), ("monitor", "support")],
)
def test_egress_context_rejects_unbounded_labels(stage: str, execution_class: str) -> None:
    with pytest.raises(ValueError), bind_runtime_egress(stage, execution_class):
        pass
