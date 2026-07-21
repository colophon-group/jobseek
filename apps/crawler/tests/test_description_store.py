"""R2 object upload retry policy tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.description_store import _put_object


def _response(status: int) -> httpx.Response:
    request = httpx.Request("PUT", "https://r2.test/bucket/key")
    return httpx.Response(status, request=request)


async def test_put_retries_one_transient_500_and_resigns() -> None:
    client = MagicMock()
    client.put = AsyncMock(side_effect=[_response(500), _response(200)])

    with (
        patch("src.core.description_store._object_url", return_value="https://r2.test/key"),
        patch("src.core.description_store._get_http", return_value=client),
        patch("src.core.description_store._sign", return_value={}) as sign,
        patch("src.core.description_store.random.uniform", return_value=0.25),
        patch("src.core.description_store.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        await _put_object("key", "body")

    assert client.put.await_count == 2
    assert sign.call_count == 2
    sleep.assert_awaited_once_with(0.25)


async def test_put_retries_one_read_error() -> None:
    request = httpx.Request("PUT", "https://r2.test/bucket/key")
    client = MagicMock()
    client.put = AsyncMock(
        side_effect=[httpx.ReadError("connection reset", request=request), _response(200)]
    )

    with (
        patch("src.core.description_store._object_url", return_value="https://r2.test/key"),
        patch("src.core.description_store._get_http", return_value=client),
        patch("src.core.description_store._sign", return_value={}),
        patch("src.core.description_store.random.uniform", return_value=0.25),
        patch("src.core.description_store.asyncio.sleep", new_callable=AsyncMock),
    ):
        await _put_object("key", "body")

    assert client.put.await_count == 2


async def test_put_does_not_retry_permanent_400() -> None:
    client = MagicMock()
    client.put = AsyncMock(return_value=_response(400))

    with (
        patch("src.core.description_store._object_url", return_value="https://r2.test/key"),
        patch("src.core.description_store._get_http", return_value=client),
        patch("src.core.description_store._sign", return_value={}),
        patch("src.core.description_store.asyncio.sleep", new_callable=AsyncMock) as sleep,
        pytest.raises(httpx.HTTPStatusError),
    ):
        await _put_object("key", "body")

    assert client.put.await_count == 1
    sleep.assert_not_awaited()


async def test_put_caps_transient_attempts_at_two() -> None:
    client = MagicMock()
    client.put = AsyncMock(return_value=_response(500))

    with (
        patch("src.core.description_store._object_url", return_value="https://r2.test/key"),
        patch("src.core.description_store._get_http", return_value=client),
        patch("src.core.description_store._sign", return_value={}),
        patch("src.core.description_store.random.uniform", return_value=0.25),
        patch("src.core.description_store.asyncio.sleep", new_callable=AsyncMock) as sleep,
        pytest.raises(httpx.HTTPStatusError),
    ):
        await _put_object("key", "body")

    assert client.put.await_count == 2
    assert sleep.await_count == 1
