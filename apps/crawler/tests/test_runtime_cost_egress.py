from __future__ import annotations

import asyncio

import httpx
import pytest

from src.shared.egress import (
    bind_runtime_egress,
    capability_executions_total,
    current_egress_attribution,
    origin_attempts_total,
    origin_outcomes_total,
    record_runtime_capability,
    response_body_bytes_total,
    seed_runtime_capabilities,
)
from src.shared.http import (
    RequestHostTrackingTransport,
    RotatingProxyTransport,
    _build_async_client,
    create_http_client,
)
from src.shared.proxy import (
    PoolProxyProvider,
    ProxyConfigurationError,
    ProxyPoolExhaustedError,
    report_proxy_failure,
)


def _value(counter, **labels: str) -> float:
    return float(counter.labels(**labels)._value.get())


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks
        self.closed = 0

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed += 1


class _BrokenChunks(_Chunks):
    def __init__(self, request: httpx.Request) -> None:
        super().__init__(b"abc")
        self._request = request

    async def __aiter__(self):
        yield b"abc"
        raise httpx.ReadError("body failed", request=self._request)


class _CancelledChunks(_Chunks):
    def __init__(self) -> None:
        super().__init__(b"abc")
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        yield b"abc"
        self.waiting.set()
        await asyncio.Event().wait()


class _ResponseTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=_Chunks(b"abc", b"defg"))


class _ErrorTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("test failure", request=request)


def _snapshot(egress: str) -> tuple[float, float, float, float]:
    labels = {"stage": "monitor", "execution_class": "http", "egress": egress}
    return (
        _value(origin_attempts_total, **labels),
        _value(origin_outcomes_total, **labels, outcome="response"),
        _value(origin_outcomes_total, **labels, outcome="transport_error"),
        _value(response_body_bytes_total, **labels),
    )


def _instrumented_client(
    egress: str,
    inner: httpx.AsyncBaseTransport,
    *,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    transport: httpx.AsyncBaseTransport = inner
    if egress == "proxy":
        provider = PoolProxyProvider(
            "webshare",
            ("http://user:secret@proxy.example:10000",),
        )
        transport = RotatingProxyTransport(
            provider,
            verify=True,
            transport_factory=lambda _selection: inner,
        )
    return _build_async_client({"transport": transport, "follow_redirects": follow_redirects})


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


@pytest.mark.asyncio
@pytest.mark.parametrize("egress", ["direct", "proxy"])
async def test_composed_transport_conserves_redirect_attempts_and_bytes(
    egress: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "8.8.8.8":
            return httpx.Response(
                302,
                headers={"location": "https://1.1.1.1/final"},
                request=request,
            )
        return httpx.Response(
            200,
            request=request,
            stream=_Chunks(b"do", b"ne"),
        )

    before = _snapshot(egress)
    async with _instrumented_client(egress, httpx.MockTransport(handler)) as client:
        with bind_runtime_egress("monitor", "http"):
            response = await client.get("https://8.8.8.8/start")

    assert response.content == b"done"
    assert tuple(after - prior for after, prior in zip(_snapshot(egress), before, strict=True)) == (
        2,
        2,
        0,
        4,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("egress", ["direct", "proxy"])
async def test_composed_transport_counts_partial_consumption_and_early_close(
    egress: str,
) -> None:
    body = _Chunks(b"abc", b"defg")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=body)

    before = _snapshot(egress)
    async with _instrumented_client(egress, httpx.MockTransport(handler)) as client:
        with bind_runtime_egress("monitor", "http"):
            async with client.stream("GET", "https://8.8.8.8/jobs") as response:
                chunks = response.aiter_raw()
                assert await anext(chunks) == b"abc"

    assert body.closed == 1
    assert tuple(after - prior for after, prior in zip(_snapshot(egress), before, strict=True)) == (
        1,
        1,
        0,
        3,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("egress", ["direct", "proxy"])
async def test_composed_transport_keeps_stream_failure_as_one_response(
    egress: str,
) -> None:
    bodies: list[_BrokenChunks] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _BrokenChunks(request)
        bodies.append(body)
        return httpx.Response(200, request=request, stream=body)

    before = _snapshot(egress)
    async with _instrumented_client(egress, httpx.MockTransport(handler)) as client:
        with bind_runtime_egress("monitor", "http"):
            async with client.stream("GET", "https://8.8.8.8/jobs") as response:
                with pytest.raises(httpx.ReadError, match="body failed"):
                    await response.aread()

    assert bodies[0].closed == 1
    assert tuple(after - prior for after, prior in zip(_snapshot(egress), before, strict=True)) == (
        1,
        1,
        0,
        3,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("egress", ["direct", "proxy"])
async def test_composed_transport_keeps_stream_cancellation_as_one_response(
    egress: str,
) -> None:
    body = _CancelledChunks()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=body)

    before = _snapshot(egress)
    async with _instrumented_client(egress, httpx.MockTransport(handler)) as client:
        with bind_runtime_egress("monitor", "http"):
            async with client.stream("GET", "https://8.8.8.8/jobs") as response:
                read_task = asyncio.create_task(response.aread())
                await body.waiting.wait()
                read_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await read_task

    assert body.closed == 1
    assert tuple(after - prior for after, prior in zip(_snapshot(egress), before, strict=True)) == (
        1,
        1,
        0,
        3,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("egress", ["direct", "proxy"])
async def test_composed_transport_is_a_noop_without_attribution(egress: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=_Chunks(b"abc"))

    before = _snapshot(egress)
    async with _instrumented_client(egress, httpx.MockTransport(handler)) as client:
        response = await client.get("https://8.8.8.8/jobs")

    assert response.content == b"abc"
    assert _snapshot(egress) == before


def test_required_proxy_unavailable_records_zero_origin_attempts(monkeypatch) -> None:
    from src import config

    monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
    monkeypatch.setattr(config.settings, "webshare_proxy_urls", [])
    monkeypatch.setattr(config.settings, "webshare_proxy_url", "")
    before = {egress: _snapshot(egress) for egress in ("direct", "proxy")}

    with (
        bind_runtime_egress("monitor", "http"),
        pytest.raises(ProxyConfigurationError, match="no usable endpoint"),
    ):
        create_http_client(use_proxy=True)

    assert {egress: _snapshot(egress) for egress in ("direct", "proxy")} == before


@pytest.mark.asyncio
async def test_runtime_exhausted_required_proxy_records_zero_origin_traffic() -> None:
    origin = "8.8.8.8"
    provider = PoolProxyProvider(
        "webshare",
        ("http://user:secret@proxy.example:10000",),
        clock=lambda: 0.0,
    )
    failed = provider.select(origin=origin, transport="httpx")
    report_proxy_failure(failed, origin=origin, reason="proxy_auth")
    constructed_slots: list[int] = []

    def factory(selection):
        constructed_slots.append(selection.pool_slot)
        return httpx.MockTransport(lambda request: httpx.Response(200, request=request))

    transport = RotatingProxyTransport(
        provider,
        verify=True,
        transport_factory=factory,
    )
    before = {egress: _snapshot(egress) for egress in ("direct", "proxy")}

    async with _build_async_client({"transport": transport}) as client:
        with (
            bind_runtime_egress("monitor", "http"),
            pytest.raises(ProxyPoolExhaustedError),
        ):
            await client.get(f"https://{origin}/jobs")

    assert constructed_slots == []
    assert {egress: _snapshot(egress) for egress in ("direct", "proxy")} == before


@pytest.mark.asyncio
async def test_selected_proxy_auth_failure_conserves_one_transport_attempt() -> None:
    origin = "8.8.8.8"
    provider = PoolProxyProvider(
        "webshare",
        ("http://user:secret@proxy.example:10000",),
        clock=lambda: 0.0,
    )
    constructed_slots: list[int] = []

    def factory(selection):
        constructed_slots.append(selection.pool_slot)

        def reject(request: httpx.Request) -> httpx.Response:
            raise httpx.ProxyError("407 proxy authentication required", request=request)

        return httpx.MockTransport(reject)

    transport = RotatingProxyTransport(
        provider,
        verify=True,
        transport_factory=factory,
    )
    direct_before = _snapshot("direct")
    proxy_before = _snapshot("proxy")

    async with _build_async_client({"transport": transport}) as client:
        with (
            bind_runtime_egress("monitor", "http"),
            pytest.raises(httpx.ProxyError, match="407"),
        ):
            await client.get(f"https://{origin}/jobs")

    assert constructed_slots == [0]
    assert _snapshot("direct") == direct_before
    assert tuple(
        after - prior for after, prior in zip(_snapshot("proxy"), proxy_before, strict=True)
    ) == (1, 0, 1, 0)
    with pytest.raises(ProxyPoolExhaustedError):
        provider.select(origin=origin, transport="httpx")


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
