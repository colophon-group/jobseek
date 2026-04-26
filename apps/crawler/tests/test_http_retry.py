"""Tests for ``src.shared.http_retry`` — the bounded retry utility used by
paginating monitors (#2722) to distinguish transient errors from
legitimate end-of-pagination."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.shared.http_retry import (
    _RETRYABLE_STATUSES,
    END_OF_PAGINATION_STATUSES,
    PaginationFetchError,
    fetch_with_retry,
)


def _resp(status: int, text: str = "") -> httpx.Response:
    """Build an httpx.Response for stubbing AsyncMock returns."""
    return httpx.Response(status, text=text, request=httpx.Request("GET", "https://x"))


class TestFetchWithRetry:
    async def test_returns_text_on_200(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "<html>ok</html>"))

        out = await fetch_with_retry(client, "https://example.com/p2")

        assert out == "<html>ok</html>"
        assert client.get.await_count == 1

    async def test_truncates_to_max_chars(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "x" * 1000))

        out = await fetch_with_retry(client, "https://example.com", max_chars=10)

        assert out == "x" * 10

    async def test_returns_none_on_404(self):
        """404 / 410 are legitimate end-of-pagination — return None, no retry."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))

        out = await fetch_with_retry(client, "https://example.com/past-end")

        assert out is None
        assert client.get.await_count == 1

    async def test_returns_none_on_410(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(410))

        out = await fetch_with_retry(client, "https://example.com/gone")

        assert out is None

    async def test_returns_none_on_non_retryable_4xx(self):
        """Non-retryable 4xx (403, etc.) returns None — same lenient
        semantics as the prior ``fetch_page_text``. Logged as anomaly."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(403))

        out = await fetch_with_retry(client, "https://example.com/forbidden")

        assert out is None
        assert client.get.await_count == 1

    async def test_retries_on_503_then_succeeds(self):
        """Transient 503 retries, then 200 returns text."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[_resp(503), _resp(503), _resp(200, "<html>recovered</html>")]
        )

        out = await fetch_with_retry(client, "https://example.com", base_delay=0.001)

        assert out == "<html>recovered</html>"
        assert client.get.await_count == 3

    async def test_retries_on_429_then_succeeds(self):
        """429 (rate-limited) is retryable."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[_resp(429), _resp(200, "ok")])

        out = await fetch_with_retry(client, "https://example.com", base_delay=0.001)

        assert out == "ok"
        assert client.get.await_count == 2

    async def test_raises_after_persistent_5xx(self):
        """Persistent 5xx exhausts retries -> raises PaginationFetchError."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(503))

        with pytest.raises(PaginationFetchError) as exc_info:
            await fetch_with_retry(client, "https://example.com/flaky", retries=3, base_delay=0.001)

        assert exc_info.value.url == "https://example.com/flaky"
        assert exc_info.value.attempts == 3
        assert exc_info.value.last_status == 503
        assert client.get.await_count == 3

    async def test_raises_after_persistent_timeout(self):
        """Timeout exhausts retries -> raises PaginationFetchError with last_error."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("read timeout"))

        with pytest.raises(PaginationFetchError) as exc_info:
            await fetch_with_retry(client, "https://example.com", retries=3, base_delay=0.001)

        assert exc_info.value.last_error == "TimeoutException"
        assert exc_info.value.last_status is None
        assert client.get.await_count == 3

    async def test_raises_after_persistent_network_error(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("conn refused"))

        with pytest.raises(PaginationFetchError):
            await fetch_with_retry(client, "https://example.com", retries=2, base_delay=0.001)

        assert client.get.await_count == 2

    async def test_recovers_from_timeout(self):
        """Transient timeout, then success on retry."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[httpx.TimeoutException("t/o"), _resp(200, "ok")])

        out = await fetch_with_retry(client, "https://example.com", base_delay=0.001)

        assert out == "ok"
        assert client.get.await_count == 2

    async def test_passes_custom_headers(self):
        """``headers`` kwarg is forwarded to client.get — needed for
        sitemap monitor's bot-friendly UA override (#2624)."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "ok"))

        await fetch_with_retry(
            client,
            "https://example.com",
            headers={"User-Agent": "jobseek-crawler"},
        )

        call_kwargs = client.get.await_args.kwargs
        assert call_kwargs["headers"] == {"User-Agent": "jobseek-crawler"}

    async def test_constants_disjoint(self):
        """Sanity: a status can't be both retryable and end-of-pagination."""
        assert set() == _RETRYABLE_STATUSES & END_OF_PAGINATION_STATUSES

    async def test_cloudflare_5xx_codes_retry(self):
        """Cloudflare-origin 5xx codes (520-526, 530) are retried — they
        showed up as a silent-truncation hole in PR #2736 review when
        the explicit allow-list missed them. Range-based check now
        covers any 5xx; this test pins that contract.
        """
        for status in (520, 521, 522, 523, 524, 525, 526, 530):
            client = AsyncMock()
            client.get = AsyncMock(side_effect=[_resp(status), _resp(200, "ok")])

            out = await fetch_with_retry(client, "https://example.com/cf", base_delay=0.001)

            assert out == "ok", f"status {status} should be retried"
            assert client.get.await_count == 2

    async def test_zero_retries_raises_immediately(self):
        """``retries=0`` means no attempts are made — function raises
        without consulting the network. Defensive: callers shouldn't
        configure 0, but the contract is at least predictable.
        """
        client = AsyncMock()
        client.get = AsyncMock()

        with pytest.raises(PaginationFetchError):
            await fetch_with_retry(client, "https://example.com", retries=0)

        assert client.get.await_count == 0
