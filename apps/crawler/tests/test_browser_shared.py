"""Tests for src.shared.browser — mock-based, no real browser needed."""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.shared.browser import (
    _REPEAT_TIMEOUT,
    ACTION_TIMEOUT,
    BROWSER_KEYS,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    DEFAULT_WAIT,
    DEFAULT_WAIT_FALLBACK,
    NAVIGATE_KEYS,
    OVERLAY_SELECTORS,
    VALID_WAIT_STRATEGIES,
    _resolve_headless,
    _resolve_placeholders,
    _x_server_alive,
    dismiss_overlays,
    navigate,
    open_page,
    render,
    run_actions,
    safe_content,
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
        assert "Chrome/133" in DEFAULT_USER_AGENT

    def test_valid_wait_strategies(self):
        assert (
            frozenset({"load", "domcontentloaded", "networkidle", "commit"})
            == VALID_WAIT_STRATEGIES
        )

    def test_overlay_selectors_non_empty(self):
        assert isinstance(OVERLAY_SELECTORS, tuple)
        assert len(OVERLAY_SELECTORS) > 0
        for sel in OVERLAY_SELECTORS:
            assert isinstance(sel, str)

    def test_default_wait_fallback_is_valid_strategy(self):
        assert DEFAULT_WAIT_FALLBACK in VALID_WAIT_STRATEGIES

    def test_browser_keys_exact_membership(self):
        # BROWSER_KEYS is the single source of truth for which config keys
        # reach open_page / navigate / run_actions. A missing entry silently
        # drops the key at the monitor/scraper boundary — regression guard.
        expected = frozenset(
            {
                "wait",
                "wait_fallback",
                "timeout",
                "user_agent",
                "headless",
                "stealth",
                "actions",
                "warmup_url",
                "cookies",
                "disable_http2",
                "persistent_context",
                "channel",
                "viewport",
                "locale",
                "skip_ssl",
            }
        )
        assert expected == BROWSER_KEYS

    def test_navigate_keys_is_nav_only_subset(self):
        # NAVIGATE_KEYS is the narrow projection for call sites that should
        # not silently activate open_page launch flags (stealth, cookies,
        # user_agent, etc.) on boards that historically had them dropped.
        expected = frozenset({"wait", "wait_fallback", "timeout", "actions"})
        assert expected == NAVIGATE_KEYS
        assert NAVIGATE_KEYS < BROWSER_KEYS


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
        await navigate(page, "https://example.com", {"wait": "load", "timeout": 5000})
        page.goto.assert_awaited_once_with("https://example.com", wait_until="load", timeout=5000)

    async def test_invalid_wait_raises(self):
        page = _make_page()
        with pytest.raises(ValueError, match="Invalid wait strategy"):
            await navigate(page, "https://example.com", {"wait": "bogus"})


# ---------------------------------------------------------------------------
# TestNavigateFallback
# ---------------------------------------------------------------------------


class TestNavigateFallback:
    """Tests for the wait_fallback retry behaviour added to navigate().

    Background: SPA career sites with persistent analytics/telemetry chatter
    never reach ``networkidle``, so the 30s primary attempt times out. The
    fallback retries once with ``domcontentloaded`` (default) and recovers.
    """

    async def test_fallback_triggers_on_timeout(self):
        """Primary times out → fallback strategy is tried with same timeout."""
        page = _make_page()
        page.goto = AsyncMock(
            side_effect=[
                PlaywrightTimeoutError("Page.goto: Timeout 30000ms exceeded."),
                None,
            ]
        )
        await navigate(
            page,
            "https://example.com",
            {"wait": "networkidle", "wait_fallback": "domcontentloaded", "timeout": 30000},
        )
        assert page.goto.await_count == 2
        first_call = page.goto.await_args_list[0]
        second_call = page.goto.await_args_list[1]
        assert first_call.kwargs["wait_until"] == "networkidle"
        assert second_call.kwargs["wait_until"] == "domcontentloaded"
        assert second_call.kwargs["timeout"] == 30000

    async def test_default_fallback_applied_when_key_absent(self):
        """When wait_fallback is not set in config, DEFAULT_WAIT_FALLBACK is used."""
        page = _make_page()
        page.goto = AsyncMock(side_effect=[PlaywrightTimeoutError("Timeout"), None])
        await navigate(page, "https://example.com", {"wait": "networkidle"})
        assert page.goto.await_count == 2
        assert page.goto.await_args_list[1].kwargs["wait_until"] == DEFAULT_WAIT_FALLBACK

    async def test_explicit_none_disables_fallback(self):
        """wait_fallback: None opts the board out of the default retry."""
        page = _make_page()
        page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("Timeout"))
        with pytest.raises(PlaywrightTimeoutError):
            await navigate(
                page,
                "https://example.com",
                {"wait": "networkidle", "wait_fallback": None},
            )
        assert page.goto.await_count == 1

    async def test_fallback_no_op_when_primary_succeeds(self):
        """When primary succeeds, fallback is never attempted."""
        page = _make_page()
        await navigate(
            page,
            "https://example.com",
            {"wait": "networkidle", "wait_fallback": "domcontentloaded"},
        )
        assert page.goto.await_count == 1
        page.goto.assert_awaited_once_with(
            "https://example.com", wait_until="networkidle", timeout=DEFAULT_TIMEOUT
        )

    async def test_fallback_both_fail_raises(self):
        """When both primary and fallback time out, TimeoutError propagates."""
        page = _make_page()
        page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("Timeout"))
        with pytest.raises(PlaywrightTimeoutError):
            await navigate(
                page,
                "https://example.com",
                {"wait": "networkidle", "wait_fallback": "domcontentloaded"},
            )
        assert page.goto.await_count == 2

    async def test_fallback_primary_domcontentloaded_no_retry(self):
        """Primary already equals DEFAULT_WAIT_FALLBACK — no pointless retry."""
        page = _make_page()
        page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("Timeout"))
        with pytest.raises(PlaywrightTimeoutError):
            await navigate(page, "https://example.com", {"wait": "domcontentloaded"})
        # Default fallback is "domcontentloaded", same as primary → skip retry
        assert page.goto.await_count == 1

    async def test_fallback_same_as_primary_does_not_retry(self):
        """An explicit fallback equal to the primary strategy is a no-op."""
        page = _make_page()
        page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("Timeout"))
        with pytest.raises(PlaywrightTimeoutError):
            await navigate(
                page,
                "https://example.com",
                {"wait": "networkidle", "wait_fallback": "networkidle"},
            )
        assert page.goto.await_count == 1

    async def test_fallback_non_timeout_error_not_retried(self):
        """Non-timeout errors propagate without fallback retry."""
        page = _make_page()
        page.goto = AsyncMock(side_effect=RuntimeError("network unreachable"))
        with pytest.raises(RuntimeError, match="network unreachable"):
            await navigate(
                page,
                "https://example.com",
                {"wait": "networkidle", "wait_fallback": "domcontentloaded"},
            )
        assert page.goto.await_count == 1

    async def test_invalid_fallback_raises(self):
        page = _make_page()
        with pytest.raises(ValueError, match="Invalid wait_fallback strategy"):
            await navigate(
                page,
                "https://example.com",
                {"wait": "networkidle", "wait_fallback": "bogus"},
            )


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
        await run_actions(page, [{"action": "evaluate", "script": "window.scrollTo(0, 9999)"}])
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

    async def test_custom_config(self, monkeypatch):
        # With DISPLAY set *and* xdpyinfo reporting the X server alive,
        # ``headless: false`` is honoured as-is. The unset-DISPLAY and
        # dead-X-server coercion paths are covered in
        # ``TestHeadlessCoercion`` below.
        monkeypatch.setenv("DISPLAY", ":99")
        monkeypatch.setattr("src.shared.browser._x_server_alive", lambda _d: True)
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

    async def test_use_proxy_false_does_not_pass_proxy_kwarg(self):
        pw = _make_pw()
        async with open_page(pw, use_proxy=False):
            pass
        # Default path: no "proxy" key in launch kwargs.
        kwargs = pw.chromium.launch.await_args.kwargs
        assert "proxy" not in kwargs

    async def test_use_proxy_true_attaches_provider_dict(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(
            config.settings, "webshare_proxy_url", "http://user:pass@pxy.example:7000"
        )
        pw = _make_pw()
        async with open_page(pw, use_proxy=True):
            pass
        kwargs = pw.chromium.launch.await_args.kwargs
        assert kwargs.get("proxy") == {
            "server": "http://pxy.example:7000",
            "username": "user",
            "password": "pass",
        }

    async def test_use_proxy_true_but_provider_none_skips_proxy(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "none")
        pw = _make_pw()
        async with open_page(pw, use_proxy=True):
            pass
        kwargs = pw.chromium.launch.await_args.kwargs
        assert "proxy" not in kwargs

    async def test_channel_forwarded_on_vanilla_launch(self):
        """``channel: chrome`` opts into system Chrome over bundled Chromium.

        Real-Chrome has a consistent TLS/JS fingerprint trusted by most bot
        managers; bundled Chromium does not. Regression guard ensures the
        key reaches ``pw.chromium.launch``.
        """
        pw = _make_pw()
        async with open_page(pw, {"channel": "chrome"}):
            pass
        kwargs = pw.chromium.launch.await_args.kwargs
        assert kwargs.get("channel") == "chrome"

    async def test_skip_ssl_enables_ignore_https_errors_on_new_context(self):
        # Boards with broken cert chains (DiDi Intl: missing intermediate
        # CA) need ignore_https_errors on the Playwright context, otherwise
        # the api_sniffer browser path hard-fails on cert verify.
        pw = _make_pw()
        browser = pw.chromium.launch.return_value
        async with open_page(pw, {"skip_ssl": True}):
            pass
        ctx_kwargs = browser.new_context.await_args.kwargs
        assert ctx_kwargs.get("ignore_https_errors") is True

    async def test_skip_ssl_absent_omits_ignore_https_errors(self):
        # Default path: do not loosen TLS verification.
        pw = _make_pw()
        browser = pw.chromium.launch.return_value
        async with open_page(pw, {}):
            pass
        ctx_kwargs = browser.new_context.await_args.kwargs
        assert "ignore_https_errors" not in ctx_kwargs


class TestHeadlessCoercion:
    """Coverage for the headless fallback when the X server is missing (#2431).

    Boards that need Akamai/PerimeterX bypass pass ``headless: false`` and
    rely on the browser-1 container's xvfb entrypoint to provide an X
    server. Two failure modes need coercion:

    1. ``DISPLAY`` is unset — entrypoint didn't run (image predates it, or
       ``docker run --entrypoint=""`` bypass).
    2. ``DISPLAY`` is set but the X server is dead — Xvfb crashed after
       handing off to the crawler (OOM, segfault). Without probing, a bare
       env check would wave the launch through to the same Playwright
       crash #2431 was meant to prevent.

    ``_resolve_headless`` coerces ``headless=False`` → ``True`` with a
    warning in both cases, degrading to bot-manager-blocked rather than
    crashing. Reason label distinguishes ``no_display`` from
    ``display_unresponsive`` for metrics.
    """

    def test_headless_true_passes_through(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        assert _resolve_headless(True) == (True, False)

    def test_headless_false_honoured_when_display_set(self, monkeypatch):
        """DISPLAY set + X server alive → honoured (no coercion)."""
        monkeypatch.setenv("DISPLAY", ":99")
        # Force the probe to report the server alive; the real xdpyinfo
        # is not available in CI so we need to stub it explicitly.
        monkeypatch.setattr("src.shared.browser._x_server_alive", lambda _d: True)
        assert _resolve_headless(False) == (False, False)

    def test_headless_false_coerced_when_display_unset(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        assert _resolve_headless(False) == (True, True)

    def test_headless_false_coerced_when_display_empty(self, monkeypatch):
        # os.environ.get returns "" for empty values, which is falsy —
        # treat that as "no display" for robustness.
        monkeypatch.setenv("DISPLAY", "")
        assert _resolve_headless(False) == (True, True)

    def test_headless_false_coerced_when_x_server_dead(self, monkeypatch):
        """DISPLAY set but X server unresponsive → coerce to headless.

        This is the failure mode the bare-env-check version of #2431
        missed: Xvfb died mid-run, DISPLAY=:99 still set, Playwright
        crashes with "XServer running". Now _resolve_headless probes
        xdpyinfo and coerces on probe failure.
        """
        monkeypatch.setenv("DISPLAY", ":99")
        monkeypatch.setattr("src.shared.browser._x_server_alive", lambda _d: False)
        assert _resolve_headless(False) == (True, True)

    def test_headless_false_honoured_when_x_server_alive(self, monkeypatch):
        """DISPLAY set AND X server responding → honour the headful request."""
        monkeypatch.setenv("DISPLAY", ":99")
        monkeypatch.setattr("src.shared.browser._x_server_alive", lambda _d: True)
        assert _resolve_headless(False) == (False, False)

    def test_x_server_alive_success(self, monkeypatch):
        """Zero exit code → server is alive."""
        completed = MagicMock()
        completed.returncode = 0
        monkeypatch.setattr(
            "src.shared.browser.subprocess.run",
            lambda *_a, **_kw: completed,
        )
        assert _x_server_alive(":99") is True

    def test_x_server_alive_nonzero_exit(self, monkeypatch):
        """Non-zero exit (e.g. connection refused) → server is not alive."""
        completed = MagicMock()
        completed.returncode = 1
        monkeypatch.setattr(
            "src.shared.browser.subprocess.run",
            lambda *_a, **_kw: completed,
        )
        assert _x_server_alive(":99") is False

    def test_x_server_alive_handles_missing_xdpyinfo(self, monkeypatch):
        """xdpyinfo not installed on dev machines → probe returns False.

        The x11-utils package ships xdpyinfo on the browser-1 image; dev
        laptops typically don't have it. FileNotFoundError must not
        crash the probe — it falls through to False (same as a dead X
        server), so the caller coerces to headless in both cases.
        """

        def _raise_fnf(*_args, **_kwargs):
            raise FileNotFoundError("xdpyinfo not found")

        monkeypatch.setattr("src.shared.browser.subprocess.run", _raise_fnf)
        assert _x_server_alive(":99") is False

    def test_x_server_alive_handles_timeout(self, monkeypatch):
        """A hung X server trips the 2s timeout → probe returns False."""

        def _raise_timeout(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd=["xdpyinfo"], timeout=2)

        monkeypatch.setattr("src.shared.browser.subprocess.run", _raise_timeout)
        assert _x_server_alive(":99") is False

    def test_x_server_alive_handles_oserror(self, monkeypatch):
        """Other OS-level failures (EACCES, ENOEXEC) → False, not crash."""

        def _raise_os(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr("src.shared.browser.subprocess.run", _raise_os)
        assert _x_server_alive(":99") is False

    async def test_open_page_coerces_missing_display(self, monkeypatch):
        """Vanilla launch path: ``headless: false`` + no DISPLAY → headless=True."""
        monkeypatch.delenv("DISPLAY", raising=False)
        pw = _make_pw()
        async with open_page(pw, {"headless": False}):
            pass
        kwargs = pw.chromium.launch.await_args.kwargs
        assert kwargs["headless"] is True
        # --headless=new is used so anti-bot systems see the less-detectable
        # headless variant; a plain headless=True would be blocked harder.
        assert "--headless=new" in kwargs.get("args", [])

    async def test_open_page_preserves_headless_false_with_display(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":99")
        monkeypatch.setattr("src.shared.browser._x_server_alive", lambda _d: True)
        pw = _make_pw()
        async with open_page(pw, {"headless": False}):
            pass
        kwargs = pw.chromium.launch.await_args.kwargs
        assert kwargs["headless"] is False
        # No --headless=new since we're running headful.
        assert "--headless=new" not in kwargs.get("args", [])

    async def test_open_page_coerces_when_x_server_dead(self, monkeypatch):
        """DISPLAY=:99 but xdpyinfo fails → vanilla launch coerced to headless."""
        monkeypatch.setenv("DISPLAY", ":99")
        monkeypatch.setattr("src.shared.browser._x_server_alive", lambda _d: False)
        pw = _make_pw()
        async with open_page(pw, {"headless": False}):
            pass
        kwargs = pw.chromium.launch.await_args.kwargs
        assert kwargs["headless"] is True
        assert "--headless=new" in kwargs.get("args", [])

    async def test_open_page_persistent_context_coerces_missing_display(self, monkeypatch):
        """Persistent-context path (tesla/mcdonalds-it) also coerces."""
        monkeypatch.delenv("DISPLAY", raising=False)
        # Need the persistent-context mock shape.
        page = _make_page()
        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        context.close = AsyncMock()
        context.add_cookies = AsyncMock()
        context.set_default_timeout = MagicMock()
        context.pages = [page]
        pw = MagicMock()
        pw.chromium = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=context)
        pw.chromium.launch = AsyncMock()

        async with open_page(
            pw,
            {"headless": False, "persistent_context": True, "channel": "chrome"},
        ):
            pass
        kwargs = pw.chromium.launch_persistent_context.await_args.kwargs
        assert kwargs["headless"] is True
        assert "--headless=new" in kwargs.get("args", [])


class TestOpenPagePersistentContext:
    """Coverage for the ``persistent_context: true`` branch of open_page.

    Added for Akamai-protected boards (Tesla, future WAF'd employers).
    ``launch + new_context`` fails the bot-manager fingerprint check;
    ``launch_persistent_context`` with a user-data-dir passes it.
    """

    @staticmethod
    def _make_persist_pw() -> MagicMock:
        """Playwright mock with launch_persistent_context wired up."""
        page = _make_page()
        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        context.close = AsyncMock()
        context.add_cookies = AsyncMock()
        context.set_default_timeout = MagicMock()
        context.pages = [page]
        pw = MagicMock()
        pw.chromium = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=context)
        pw.chromium.launch = AsyncMock()  # unused in this path — trip if called
        return pw

    async def test_uses_launch_persistent_context_not_launch(self):
        pw = self._make_persist_pw()
        async with open_page(pw, {"persistent_context": True}):
            pass
        pw.chromium.launch_persistent_context.assert_awaited_once()
        pw.chromium.launch.assert_not_called()

    async def test_user_data_dir_cleaned_up(self):
        import os

        pw = self._make_persist_pw()
        async with open_page(pw, {"persistent_context": True}):
            pass
        # First positional arg is the user_data_dir path; must not still
        # exist after the context closes.
        user_data_dir = pw.chromium.launch_persistent_context.await_args.args[0]
        assert not os.path.exists(user_data_dir), (
            f"user_data_dir {user_data_dir} leaked; tmpdirs accumulate "
            "across browser cycles under the worker pool"
        )

    async def test_user_data_dir_cleaned_on_exception(self):
        import os

        pw = self._make_persist_pw()
        with pytest.raises(RuntimeError):
            async with open_page(pw, {"persistent_context": True}):
                raise RuntimeError("boom")
        user_data_dir = pw.chromium.launch_persistent_context.await_args.args[0]
        assert not os.path.exists(user_data_dir)

    async def test_channel_viewport_locale_user_agent_forwarded(self):
        pw = self._make_persist_pw()
        async with open_page(
            pw,
            {
                "persistent_context": True,
                "channel": "chrome",
                "viewport": {"width": 1920, "height": 1080},
                "locale": "en-GB",
                "user_agent": "ua/1.0",
            },
        ):
            pass
        kwargs = pw.chromium.launch_persistent_context.await_args.kwargs
        assert kwargs["channel"] == "chrome"
        assert kwargs["viewport"] == {"width": 1920, "height": 1080}
        assert kwargs["locale"] == "en-GB"
        assert kwargs["user_agent"] == "ua/1.0"

    async def test_automation_controlled_flag_added(self):
        """Akamai reads ``navigator.webdriver`` before any init-script.

        ``--disable-blink-features=AutomationControlled`` is the only
        way to hide that flag from the pre-script JS, so the default
        must add it whenever persistent_context is on.
        """
        pw = self._make_persist_pw()
        async with open_page(pw, {"persistent_context": True}):
            pass
        kwargs = pw.chromium.launch_persistent_context.await_args.kwargs
        assert "--disable-blink-features=AutomationControlled" in kwargs.get("args", [])

    async def test_proxy_forwarded_to_persistent_context(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config.settings, "proxy_provider", "webshare")
        monkeypatch.setattr(config.settings, "webshare_proxy_url", "http://u:p@px.example:7000")
        pw = self._make_persist_pw()
        async with open_page(pw, {"persistent_context": True}, use_proxy=True):
            pass
        kwargs = pw.chromium.launch_persistent_context.await_args.kwargs
        assert kwargs["proxy"] == {
            "server": "http://px.example:7000",
            "username": "u",
            "password": "p",
        }

    async def test_warmup_url_navigated_before_yield(self):
        pw = self._make_persist_pw()
        page = pw.chromium.launch_persistent_context.return_value.pages[0]
        async with open_page(
            pw,
            {"persistent_context": True, "warmup_url": "https://example.com/"},
        ):
            pass
        page.goto.assert_awaited_once()
        args, kwargs = page.goto.await_args
        assert args[0] == "https://example.com/"
        assert kwargs["wait_until"] == "domcontentloaded"

    async def test_skip_ssl_forwarded_to_persistent_context(self):
        # persistent_context takes context-level knobs at launch time, not
        # via a separate new_context() call, so ignore_https_errors must
        # land in launch_persistent_context kwargs instead.
        pw = self._make_persist_pw()
        async with open_page(pw, {"persistent_context": True, "skip_ssl": True}):
            pass
        kwargs = pw.chromium.launch_persistent_context.await_args.kwargs
        assert kwargs.get("ignore_https_errors") is True

    async def test_skip_ssl_absent_omits_ignore_https_errors_on_persistent(self):
        pw = self._make_persist_pw()
        async with open_page(pw, {"persistent_context": True}):
            pass
        kwargs = pw.chromium.launch_persistent_context.await_args.kwargs
        assert "ignore_https_errors" not in kwargs


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

        with patch("playwright.async_api.async_playwright", return_value=mock_async_pw):
            html = await render("https://example.com")
        assert html == "<html><body>rendered</body></html>"

    async def test_passes_config(self):
        mock_page = _make_page()
        mock_pw = _make_pw(mock_page)

        mock_async_pw = MagicMock()
        mock_async_pw.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_async_pw.__aexit__ = AsyncMock(return_value=False)

        config = {"wait": "load", "timeout": 5000}
        with patch("playwright.async_api.async_playwright", return_value=mock_async_pw):
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
        with patch("playwright.async_api.async_playwright", return_value=mock_async_pw):
            await render("https://example.com", config)
        # dismiss_overlays calls page.evaluate
        mock_page.evaluate.assert_awaited_once()

    async def test_none_config(self):
        mock_page = _make_page()
        mock_pw = _make_pw(mock_page)

        mock_async_pw = MagicMock()
        mock_async_pw.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_async_pw.__aexit__ = AsyncMock(return_value=False)

        with patch("playwright.async_api.async_playwright", return_value=mock_async_pw):
            html = await render("https://example.com", None)
        assert html == "<html></html>"


# ---------------------------------------------------------------------------
# TestSafeContent
# ---------------------------------------------------------------------------


class TestSafeContent:
    """Retry behaviour for page.content() when the page is navigating.

    Background: post.ch (issue #2188) reliably hits
    ``Error: Page.content: Unable to retrieve content because the page is
    navigating and changing the content.`` The race is transient — waiting
    for ``domcontentloaded`` and retrying once recovers the HTML. Any other
    error propagates so real failures are not silently swallowed.
    """

    @staticmethod
    def _navigating_error() -> Exception:
        return Exception(
            "Page.content: Unable to retrieve content because the "
            "page is navigating and changing the content."
        )

    async def test_success_first_try(self):
        page = _make_page()
        page.content = AsyncMock(return_value="<html>ok</html>")
        html = await safe_content(page)
        assert html == "<html>ok</html>"
        assert page.content.await_count == 1

    async def test_retries_on_navigation_race_and_recovers(self):
        page = _make_page()
        page.content = AsyncMock(side_effect=[self._navigating_error(), "<html>ok</html>"])
        page.wait_for_load_state = AsyncMock()
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            html = await safe_content(page)
        assert html == "<html>ok</html>"
        assert page.content.await_count == 2
        page.wait_for_load_state.assert_awaited_once()
        call = page.wait_for_load_state.await_args
        assert call.args[0] == "domcontentloaded"
        assert isinstance(call.kwargs.get("timeout"), int)

    async def test_retries_exhausted_reraises(self):
        page = _make_page()
        err = self._navigating_error()
        page.content = AsyncMock(side_effect=[err, err, err])
        page.wait_for_load_state = AsyncMock()
        with (
            patch.object(asyncio, "sleep", new_callable=AsyncMock),
            pytest.raises(Exception) as exc_info,
        ):
            await safe_content(page)
        assert "page is navigating" in str(exc_info.value)
        # 1 initial + 2 retries = 3 attempts
        assert page.content.await_count == 3

    async def test_non_navigation_error_propagates_without_retry(self):
        page = _make_page()
        page.content = AsyncMock(side_effect=RuntimeError("connection closed"))
        page.wait_for_load_state = AsyncMock()
        with pytest.raises(RuntimeError, match="connection closed"):
            await safe_content(page)
        assert page.content.await_count == 1
        page.wait_for_load_state.assert_not_awaited()

    async def test_wait_for_load_state_failure_does_not_block_retry(self):
        """If wait_for_load_state raises, we still retry page.content()."""
        page = _make_page()
        page.content = AsyncMock(side_effect=[self._navigating_error(), "<html>ok</html>"])
        page.wait_for_load_state = AsyncMock(side_effect=PlaywrightTimeoutError("x"))
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            html = await safe_content(page)
        assert html == "<html>ok</html>"
        assert page.content.await_count == 2


# ---------------------------------------------------------------------------
# TestRepeatAction
# ---------------------------------------------------------------------------


class TestRepeatAction:
    async def test_repeat_stops_when_no_new_links(self):
        """Same link count before and after click → stops after 1 iteration."""
        page = _make_page()
        # evaluate returns same count both times (before=10, after=10)
        page.evaluate = AsyncMock(side_effect=[10, 10])
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            await run_actions(page, [{"action": "repeat", "selector": "button.more"}])
        # 2 evaluate calls: before + after
        assert page.evaluate.await_count == 2
        # Click happened once
        page.locator.return_value.first.click.assert_awaited_once()

    async def test_repeat_stops_at_max(self):
        """Stops at max iterations even when new links keep appearing."""
        page = _make_page()
        # Each pair of evaluate calls: before=N, after=N+5 (always new links)
        counts = []
        for i in range(5):
            counts.extend([10 + i * 5, 15 + i * 5])
        page.evaluate = AsyncMock(side_effect=counts)
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            await run_actions(page, [{"action": "repeat", "selector": "button.more", "max": 3}])
        # 3 iterations × 2 evaluate calls = 6
        assert page.evaluate.await_count == 6
        assert page.locator.return_value.first.click.await_count == 3

    async def test_repeat_stops_when_selector_gone(self):
        """Stops when the selector element disappears."""
        page = _make_page()
        page.evaluate = AsyncMock(return_value=10)
        # First iteration: element exists → count() == 0 on second
        page.locator.return_value.first.count = AsyncMock(side_effect=[0])
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            await run_actions(page, [{"action": "repeat", "selector": "button.more"}])
        # Only 1 evaluate (before count), then selector gone → no click
        assert page.evaluate.await_count == 1
        page.locator.return_value.first.click.assert_not_awaited()

    async def test_repeat_default_timeout(self):
        """Repeat actions use _REPEAT_TIMEOUT (300s) by default."""
        assert _REPEAT_TIMEOUT == 300.0

    async def test_repeat_uses_custom_wait_ms(self):
        """wait_ms parameter controls sleep duration between clicks."""
        page = _make_page()
        # 1 iteration: before=10, after=10 (stops)
        page.evaluate = AsyncMock(side_effect=[10, 10])
        with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
            await run_actions(
                page, [{"action": "repeat", "selector": "button.more", "wait_ms": 500}]
            )
            mock_sleep.assert_awaited_once_with(0.5)


# ---------------------------------------------------------------------------
# TestResolvePlaceholders
# ---------------------------------------------------------------------------


class TestResolvePlaceholders:
    def test_uuid_replaced(self):
        cookies = [{"name": "datr", "value": "{uuid}", "domain": ".example.com"}]
        result = _resolve_placeholders(cookies)
        assert result[0]["value"] != "{uuid}"
        assert len(result[0]["value"]) == 32  # hex uuid without dashes

    def test_uuid_unique_per_cookie(self):
        cookies = [
            {"name": "a", "value": "{uuid}"},
            {"name": "b", "value": "{uuid}"},
        ]
        result = _resolve_placeholders(cookies)
        assert result[0]["value"] != result[1]["value"]

    def test_no_placeholder_unchanged(self):
        cookies = [{"name": "x", "value": "static", "domain": ".example.com"}]
        result = _resolve_placeholders(cookies)
        assert result[0]["value"] == "static"

    def test_original_not_mutated(self):
        cookie = {"name": "datr", "value": "{uuid}"}
        _resolve_placeholders([cookie])
        assert cookie["value"] == "{uuid}"

    def test_empty_list(self):
        assert _resolve_placeholders([]) == []

    def test_partial_placeholder(self):
        cookies = [{"name": "x", "value": "prefix-{uuid}-suffix"}]
        result = _resolve_placeholders(cookies)
        assert result[0]["value"].startswith("prefix-")
        assert result[0]["value"].endswith("-suffix")
        assert "{uuid}" not in result[0]["value"]
