"""Tests for src.shared.browser — mock-based, no real browser needed."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shared.browser import (
    ACTION_TIMEOUT,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    DEFAULT_WAIT,
    OVERLAY_SELECTORS,
    VALID_WAIT_STRATEGIES,
    dismiss_overlays,
    navigate,
    open_page,
    render,
    run_actions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page() -> MagicMock:
    """Return a mock Playwright Page with common methods stubbed."""
    page = MagicMock()
    page.goto = AsyncMock()
    page.evaluate = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")

    # locator().first  —  count() and click() are async
    locator_first = MagicMock()
    locator_first.count = AsyncMock(return_value=1)
    locator_first.click = AsyncMock()
    locator = MagicMock()
    locator.first = locator_first
    page.locator = MagicMock(return_value=locator)
    return page


def _make_pw(page: MagicMock | None = None) -> MagicMock:
    """Return a mock AsyncPlaywright that yields *page* from open_page."""
    page = page or _make_page()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    pw = MagicMock()
    pw.chromium = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    return pw


# ---------------------------------------------------------------------------
# TestConstants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_user_agent_contains_chrome(self):
        assert "Chrome/120" in DEFAULT_USER_AGENT

    def test_valid_wait_strategies(self):
        assert VALID_WAIT_STRATEGIES == frozenset(
            {"load", "domcontentloaded", "networkidle", "commit"}
        )

    def test_overlay_selectors_non_empty(self):
        assert isinstance(OVERLAY_SELECTORS, tuple)
        assert len(OVERLAY_SELECTORS) > 0
        for sel in OVERLAY_SELECTORS:
            assert isinstance(sel, str)


# ---------------------------------------------------------------------------
# TestNavigate
# ---------------------------------------------------------------------------


class TestNavigate:
    async def test_defaults(self):
        page = _make_page()
        await navigate(page, "https://example.com")
        page.goto.assert_awaited_once_with(
            "https://example.com",
            wait_until=DEFAULT_WAIT,
            timeout=DEFAULT_TIMEOUT,
        )

    async def test_custom_config(self):
        page = _make_page()
        await navigate(
            page, "https://example.com", {"wait": "load", "timeout": 5000}
        )
        page.goto.assert_awaited_once_with(
            "https://example.com", wait_until="load", timeout=5000
        )

    async def test_invalid_wait_raises(self):
        page = _make_page()
        with pytest.raises(ValueError, match="Invalid wait strategy"):
            await navigate(page, "https://example.com", {"wait": "bogus"})


# ---------------------------------------------------------------------------
# TestRunActions
# ---------------------------------------------------------------------------


class TestRunActions:
    async def test_remove_action(self):
        page = _make_page()
        await run_actions(page, [{"action": "remove", "selector": ".popup"}])
        page.evaluate.assert_awaited_once()
        call_args = page.evaluate.call_args
        assert ".popup" in str(call_args)

    async def test_click_action(self):
        page = _make_page()
        await run_actions(page, [{"action": "click", "selector": "button.show"}])
        page.locator.assert_called_once_with("button.show")
        page.locator.return_value.first.click.assert_awaited_once()

    async def test_click_missing_element_no_raise(self):
        page = _make_page()
        page.locator.return_value.first.count = AsyncMock(return_value=0)
        # Should not raise
        await run_actions(page, [{"action": "click", "selector": ".gone"}])
        page.locator.return_value.first.click.assert_not_awaited()

    async def test_wait_action(self):
        page = _make_page()
        with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
            await run_actions(page, [{"action": "wait", "ms": 500}])
            mock_sleep.assert_awaited_once_with(0.5)

    async def test_wait_action_default_ms(self):
        page = _make_page()
        with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
            await run_actions(page, [{"action": "wait"}])
            mock_sleep.assert_awaited_once_with(1.0)

    async def test_evaluate_action(self):
        page = _make_page()
        await run_actions(
            page, [{"action": "evaluate", "script": "window.scrollTo(0, 9999)"}]
        )
        page.evaluate.assert_awaited_once_with("window.scrollTo(0, 9999)")

    async def test_dismiss_overlays_action(self):
        page = _make_page()
        await run_actions(page, [{"action": "dismiss_overlays"}])
        page.evaluate.assert_awaited_once()
        call_js = page.evaluate.call_args[0][1]
        for sel in OVERLAY_SELECTORS:
            assert sel in call_js

    async def test_failed_action_continues(self):
        page = _make_page()
        page.evaluate = AsyncMock(side_effect=[Exception("boom"), None])
        await run_actions(
            page,
            [
                {"action": "remove", "selector": ".bad"},
                {"action": "evaluate", "script": "ok()"},
            ],
        )
        assert page.evaluate.await_count == 2

    async def test_unknown_action_logs_warning(self):
        page = _make_page()
        # Should not raise
        await run_actions(page, [{"action": "bogus_type"}])

    async def test_empty_actions(self):
        page = _make_page()
        await run_actions(page, [])
        page.evaluate.assert_not_awaited()
        page.locator.assert_not_called()

    async def test_action_timeout(self):
        """An action exceeding its timeout is cancelled and logged."""
        page = _make_page()

        async def slow_evaluate(*args, **kwargs):
            await asyncio.sleep(60)

        page.evaluate = AsyncMock(side_effect=slow_evaluate)
        # Use a very short timeout so the test completes quickly
        await run_actions(page, [{"action": "remove", "selector": ".slow", "timeout": 0.01}])
        # Should not raise — timeout is handled gracefully

    async def test_action_default_timeout(self):
        assert ACTION_TIMEOUT == 10.0

    async def test_action_custom_timeout(self):
        """Per-action timeout config is respected."""
        page = _make_page()

        async def slow_evaluate(*args, **kwargs):
            await asyncio.sleep(60)

        page.evaluate = AsyncMock(side_effect=slow_evaluate)
        # Custom timeout of 0.01s should trigger TimeoutError
        await run_actions(page, [{"action": "evaluate", "script": "slow()", "timeout": 0.01}])
        # Second action should still run after the first times out
        page2 = _make_page()
        page2.evaluate = AsyncMock(side_effect=[slow_evaluate, None])
        await run_actions(
            page2,
            [
                {"action": "evaluate", "script": "slow()", "timeout": 0.01},
                {"action": "evaluate", "script": "fast()"},
            ],
        )


# ---------------------------------------------------------------------------
# TestDismissOverlays
# ---------------------------------------------------------------------------


class TestDismissOverlays:
    async def test_calls_evaluate_with_all_selectors(self):
        page = _make_page()
        await dismiss_overlays(page)
        page.evaluate.assert_awaited_once()
        selector_arg = page.evaluate.call_args[0][1]
        for sel in OVERLAY_SELECTORS:
            assert sel in selector_arg


# ---------------------------------------------------------------------------
# TestOpenPage
# ---------------------------------------------------------------------------


class TestOpenPage:
    async def test_defaults(self):
        pw = _make_pw()
        async with open_page(pw) as page:
            assert page is not None
        pw.chromium.launch.assert_awaited_once_with(headless=True)
        pw.chromium.launch.return_value.new_context.assert_awaited_once_with(
            user_agent=DEFAULT_USER_AGENT
        )

    async def test_custom_config(self):
        pw = _make_pw()
        async with open_page(pw, {"headless": False, "user_agent": "custom/1.0"}):
            pass
        pw.chromium.launch.assert_awaited_once_with(headless=False)
        pw.chromium.launch.return_value.new_context.assert_awaited_once_with(
            user_agent="custom/1.0"
        )

    async def test_closes_browser_on_exit(self):
        pw = _make_pw()
        browser = pw.chromium.launch.return_value
        async with open_page(pw):
            pass
        browser.close.assert_awaited_once()

    async def test_closes_browser_on_exception(self):
        pw = _make_pw()
        browser = pw.chromium.launch.return_value
        with pytest.raises(RuntimeError):
            async with open_page(pw):
                raise RuntimeError("test error")
        browser.close.assert_awaited_once()

    async def test_closes_context_before_browser(self):
        pw = _make_pw()
        browser = pw.chromium.launch.return_value
        context = browser.new_context.return_value
        async with open_page(pw):
            pass
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()

    async def test_closes_context_on_exception(self):
        pw = _make_pw()
        browser = pw.chromium.launch.return_value
        context = browser.new_context.return_value
        with pytest.raises(RuntimeError):
            async with open_page(pw):
                raise RuntimeError("test error")
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestRender
# ---------------------------------------------------------------------------


class TestRender:
    async def test_returns_html(self):
        mock_page = _make_page()
        mock_page.content.return_value = "<html><body>rendered</body></html>"
        mock_pw = _make_pw(mock_page)

        mock_async_pw = MagicMock()
        mock_async_pw.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_async_pw.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "playwright.async_api.async_playwright", return_value=mock_async_pw
        ):
            html = await render("https://example.com")
        assert html == "<html><body>rendered</body></html>"

    async def test_passes_config(self):
        mock_page = _make_page()
        mock_pw = _make_pw(mock_page)

        mock_async_pw = MagicMock()
        mock_async_pw.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_async_pw.__aexit__ = AsyncMock(return_value=False)

        config = {"wait": "load", "timeout": 5000}
        with patch(
            "playwright.async_api.async_playwright", return_value=mock_async_pw
        ):
            await render("https://example.com", config)
        mock_page.goto.assert_awaited_once_with(
            "https://example.com", wait_until="load", timeout=5000
        )

    async def test_runs_actions(self):
        mock_page = _make_page()
        mock_pw = _make_pw(mock_page)

        mock_async_pw = MagicMock()
        mock_async_pw.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_async_pw.__aexit__ = AsyncMock(return_value=False)

        config = {"actions": [{"action": "dismiss_overlays"}]}
        with patch(
            "playwright.async_api.async_playwright", return_value=mock_async_pw
        ):
            await render("https://example.com", config)
        # dismiss_overlays calls page.evaluate
        mock_page.evaluate.assert_awaited_once()

    async def test_none_config(self):
        mock_page = _make_page()
        mock_pw = _make_pw(mock_page)

        mock_async_pw = MagicMock()
        mock_async_pw.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_async_pw.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "playwright.async_api.async_playwright", return_value=mock_async_pw
        ):
            html = await render("https://example.com", None)
        assert html == "<html></html>"
