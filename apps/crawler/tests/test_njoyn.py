from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitors import monitor_needs_browser
from src.core.monitors.dom import BotChallengeError
from src.core.monitors.njoyn import (
    _discover_page,
    _expected_count,
    _is_job_detail_url,
    can_handle,
)


def _job(job_id: str, brid: int) -> str:
    return (
        "https://cgi.njoyn.com/corp/xweb/XWeb.asp?"
        f"CLID=21001&Page=JobDetails&Jobid={job_id}&BRID={brid}&lang=1"
    )


class _FakeLocator:
    def __init__(self, page, selector: str):
        self.page = page
        self.selector = selector
        self.first = self

    async def count(self) -> int:
        is_njoyn_next = self.selector.startswith('input[type="submit"]')
        return int(is_njoyn_next and self.page.has_next)

    async def click(self) -> None:
        if not self.page.repeat:
            self.page.index += 1


class _FakePage:
    def __init__(self, pages: list[list[str]], expected: int | None, *, repeat: bool = False):
        self.pages = pages
        self.expected = expected
        self.repeat = repeat
        self.index = 0
        self.url = "https://cgi.njoyn.com/corp/xweb/XWeb.asp?page=joblisting"

    @property
    def has_next(self) -> bool:
        return self.repeat or self.index < len(self.pages) - 1

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    async def evaluate(self, _script: str):
        result_text = "" if self.expected is None else f"Search Results ({self.expected})"
        return {
            "links": self.pages[self.index],
            "text": f"Current opportunities\n{result_text}",
        }

    async def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None


def test_recognizes_njoyn_detail_urls_case_insensitively() -> None:
    assert _is_job_detail_url(_job("J0826-0527", 1324213))
    assert not _is_job_detail_url(
        "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&page=joblisting"
    )
    assert not _is_job_detail_url("https://example.com/?Page=JobDetails&Jobid=1&BRID=2")


def test_parses_advertised_result_count() -> None:
    assert _expected_count("Search Results (3,071)") == 3071
    assert _expected_count("Current opportunities") is None


async def test_can_handle_returns_hardened_browser_defaults() -> None:
    async with httpx.AsyncClient() as client:
        config = await can_handle(
            "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&page=joblisting",
            client,
        )

    assert config == {
        "wait": "domcontentloaded",
        "timeout": 60_000,
        "persistent_context": True,
        "headless": False,
        "stealth": True,
        "proxy": True,
    }
    assert monitor_needs_browser("njoyn", config)


async def test_can_handle_rejects_job_detail_url() -> None:
    async with httpx.AsyncClient() as client:
        assert await can_handle(_job("J0826-0527", 1324213), client) is None


async def test_collects_form_paginated_listing_and_checks_total() -> None:
    page = _FakePage(
        [[_job("J1", 1)], [_job("J2", 2)], [_job("J3", 3)]],
        expected=3,
    )
    with (
        patch("src.core.monitors.njoyn.navigate", new_callable=AsyncMock),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
    ):
        urls = await _discover_page(page, page.url, {"page_wait_ms": 1})

    assert urls == {_job("J1", 1), _job("J2", 2), _job("J3", 3)}
    assert page.index == 2


async def test_fails_closed_when_next_control_disappears_before_total() -> None:
    page = _FakePage([[_job("J1", 1)]], expected=2)
    with (
        patch("src.core.monitors.njoyn.navigate", new_callable=AsyncMock),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        pytest.raises(RuntimeError, match="collected 1 of 2"),
    ):
        await _discover_page(page, page.url, {})


async def test_fails_closed_without_advertised_total() -> None:
    page = _FakePage([[_job("J1", 1)]], expected=None)
    with (
        patch("src.core.monitors.njoyn.navigate", new_callable=AsyncMock),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        pytest.raises(RuntimeError, match="missing its Search Results total"),
    ):
        await _discover_page(page, page.url, {})


async def test_fails_closed_when_next_repeats_same_page() -> None:
    page = _FakePage([[_job("J1", 1)]], expected=2, repeat=True)
    with (
        patch("src.core.monitors.njoyn.navigate", new_callable=AsyncMock),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(RuntimeError, match="repeated page"),
    ):
        await _discover_page(page, page.url, {"page_wait_ms": 1})


async def test_fails_closed_on_radware_challenge() -> None:
    page = _FakePage([[_job("J1", 1)]], expected=1)
    page.url = "https://validate.perfdrive.com/?ssk=botmanager_support@radware.com"
    with (
        patch("src.core.monitors.njoyn.navigate", new_callable=AsyncMock),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html><head><title>Radware Captcha Page</title></head></html>",
        ),
        pytest.raises(BotChallengeError, match="proxy transport"),
    ):
        await _discover_page(page, page.url, {})
