from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitors.oracle_hcm import _RETRY_ATTEMPTS, _get_with_retry


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "https://example.com/"))


class TestGetWithRetry:
    @pytest.mark.parametrize("status", [200, 204, 404, 410])
    async def test_returns_immediately_on_non_transient_status(self, status):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(status))

        resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == status
        assert client.get.await_count == 1

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    async def test_retries_on_transient_status_then_succeeds(self, status):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[_response(status), _response(status), _response(200)])

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", new_callable=AsyncMock):
            resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == 200
        assert client.get.await_count == 3

    async def test_returns_final_transient_response_after_exhaustion(self):
        """After _RETRY_ATTEMPTS transient responses, return the last one (not
        raise) so the caller's raise_for_status() still triggers the board-level
        _RECORD_FAILURE path with the correct status code."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(503))

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", new_callable=AsyncMock):
            resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == 503
        assert client.get.await_count == _RETRY_ATTEMPTS

    async def test_does_not_sleep_after_final_failed_attempt(self):
        """Sleep is for back-off between attempts — sleeping after the last
        attempt (when we're giving up anyway) just pointlessly delays the
        caller's error handling."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(503))
        sleep = AsyncMock()

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", sleep):
            await _get_with_retry(client, "https://example.com/")

        # _RETRY_ATTEMPTS attempts → _RETRY_ATTEMPTS - 1 sleeps between them
        assert sleep.await_count == _RETRY_ATTEMPTS - 1
