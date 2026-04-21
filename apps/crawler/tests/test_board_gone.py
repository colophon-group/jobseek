"""Regression tests for ``BoardGoneError`` and the dead-board self-heal.

Issue #2215: API monitors (greenhouse, lever, recruitee, ashby) were
producing 404 errors for boards whose upstream slug had been removed,
yet the boards kept being claimed from Redis cycle after cycle. The
fix routes upstream 404s through ``BoardGoneError`` so the board is
recorded as ``board_status='gone'`` in one shot rather than after
five consecutive ``_RECORD_FAILURE`` increments.

These tests pin the contract: a 404 from each ATS monitor MUST raise
``BoardGoneError`` (and a 500 / network error MUST NOT), so a future
refactor can't regress the routing.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.monitors import BoardGoneError


def _mock_transport(status: int, json_body: list | dict | None = None) -> httpx.AsyncClient:
    """Build an httpx AsyncClient that returns a fixed status / body."""

    def handler(request: httpx.Request) -> httpx.Response:
        if json_body is not None:
            return httpx.Response(status, json=json_body)
        return httpx.Response(status, text="")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestBoardGoneError:
    def test_carries_url(self) -> None:
        exc = BoardGoneError("dead board", url="https://example.com/x")
        assert str(exc) == "dead board"
        assert exc.url == "https://example.com/x"

    def test_url_optional(self) -> None:
        exc = BoardGoneError("dead board")
        assert exc.url is None


class TestGreenhouse404:
    @pytest.mark.asyncio
    async def test_404_raises_board_gone(self) -> None:
        from src.core.monitors.greenhouse import discover

        async with _mock_transport(404) as client:
            with pytest.raises(BoardGoneError) as exc_info:
                await discover(
                    {"board_url": "https://job-boards.greenhouse.io/dead-slug"},
                    client,
                )
        assert "dead-slug" in str(exc_info.value)
        assert exc_info.value.url and "dead-slug" in exc_info.value.url

    @pytest.mark.asyncio
    async def test_500_does_not_raise_board_gone(self) -> None:
        from src.core.monitors.greenhouse import discover

        async with _mock_transport(500) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await discover(
                    {"board_url": "https://job-boards.greenhouse.io/some-slug"},
                    client,
                )

    @pytest.mark.asyncio
    async def test_200_does_not_raise_board_gone(self) -> None:
        from src.core.monitors.greenhouse import discover

        async with _mock_transport(200, {"jobs": []}) as client:
            result = await discover(
                {"board_url": "https://job-boards.greenhouse.io/empty"},
                client,
            )
        assert result == []


class TestLever404:
    @pytest.mark.asyncio
    async def test_404_first_page_raises_board_gone(self) -> None:
        from src.core.monitors.lever import discover

        async with _mock_transport(404) as client:
            with pytest.raises(BoardGoneError) as exc_info:
                await discover(
                    {"board_url": "https://jobs.lever.co/dead-slug"},
                    client,
                )
        assert "dead-slug" in str(exc_info.value)


class TestRecruitee404:
    @pytest.mark.asyncio
    async def test_404_raises_board_gone(self) -> None:
        from src.core.monitors.recruitee import discover

        async with _mock_transport(404) as client:
            with pytest.raises(BoardGoneError):
                await discover(
                    {
                        "board_url": "https://dead-slug.recruitee.com",
                        "metadata": {"slug": "dead-slug"},
                    },
                    client,
                )


class TestAshby404:
    @pytest.mark.asyncio
    async def test_404_raises_board_gone(self) -> None:
        from src.core.monitors.ashby import discover

        async with _mock_transport(404) as client:
            with pytest.raises(BoardGoneError) as exc_info:
                await discover(
                    {"board_url": "https://jobs.ashbyhq.com/dead-slug"},
                    client,
                )
        assert "dead-slug" in str(exc_info.value)
