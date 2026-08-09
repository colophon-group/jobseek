"""Regression coverage for Playwright protocol-call cancellation (#6081)."""

from __future__ import annotations

import asyncio
import gc
from types import MethodType, SimpleNamespace

import pytest
from playwright._impl._connection import Channel, Connection
from playwright._impl._helper import Error


async def test_cancelled_protocol_call_is_aborted_and_exception_is_retrieved():
    """A cancelled API call must drain the driver's eventual error response.

    Playwright <1.62 cancelled only its local callback. The driver operation
    kept running and could later produce an unobserved ``Future`` exception
    when navigation destroyed the JavaScript execution context. This fake
    transport exercises the upstream cancellation contract without launching
    a browser: ``Channel._inner_send`` must send ``__abort__`` and consume the
    error response before it propagates ``CancelledError``.
    """
    loop = asyncio.get_running_loop()
    callback = SimpleNamespace(id=17, future=loop.create_future())
    unhandled: list[dict] = []
    previous_handler = loop.get_exception_handler()

    class FakeTransport:
        def __init__(self):
            self.on_error_future = loop.create_future()
            self.messages: list[dict] = []

        def send(self, message: dict) -> None:
            self.messages.append(message)
            callback.future.set_exception(Error("Execution context was destroyed"))

    transport = FakeTransport()
    connection = SimpleNamespace(
        _error=None,
        _transport=transport,
        _send_message_to_server=lambda *_args: callback,
    )
    connection._abort = MethodType(Connection._abort, connection)
    channel = object.__new__(Channel)
    channel._connection = connection
    channel._object = SimpleNamespace(_guid="page@1")

    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        task = asyncio.create_task(
            Channel._inner_send(channel, "evaluateExpression", None, {}, False)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        del task
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)
        if not transport.on_error_future.done():
            transport.on_error_future.cancel()

    assert transport.messages == [
        {
            "guid": "page@1",
            "method": "__abort__",
            "params": {"id": 17, "reason": "Task was cancelled"},
        }
    ]
    assert unhandled == []
