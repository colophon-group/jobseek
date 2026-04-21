"""Regression tests for ``scraper_needs_browser``.

The function is the single source of truth for the queue-routing
decision (simple vs browser). Misclassifying a render-aware scraper
as "no browser needed" puts the task on the simple queue, where slim
workers without Chromium claim it and crash with
``BrowserType.launch: Executable doesn't exist``. See issue #2250.

Each render-aware scraper from ``_RENDER_AWARE_SCRAPERS`` (currently
``dom``, ``json-ld``, ``embedded``, ``nextdata``) gets its own check
so dropping one from the set fails the suite loudly. Plus the
``api_sniffer`` capture vs HTTP-mode branches.
"""

from __future__ import annotations

import pytest

from src.core.scrapers import scraper_needs_browser


class TestRenderAwareScrapers:
    """For each render-aware scraper: ``render: true`` => browser."""

    @pytest.mark.parametrize(
        "scraper",
        ["dom", "json-ld", "embedded", "nextdata"],
    )
    def test_render_true_requires_browser(self, scraper: str) -> None:
        assert scraper_needs_browser(scraper, {"render": True}) is True

    @pytest.mark.parametrize(
        "scraper",
        ["dom", "json-ld", "embedded", "nextdata"],
    )
    def test_render_false_no_browser(self, scraper: str) -> None:
        assert scraper_needs_browser(scraper, {"render": False}) is False

    @pytest.mark.parametrize(
        "scraper",
        ["dom", "json-ld", "embedded", "nextdata"],
    )
    def test_render_missing_no_browser(self, scraper: str) -> None:
        # Default is no rendering — only ``render: true`` opts in.
        assert scraper_needs_browser(scraper, {}) is False
        assert scraper_needs_browser(scraper, None) is False

    @pytest.mark.parametrize(
        "scraper",
        ["dom", "json-ld", "embedded", "nextdata"],
    )
    def test_render_true_with_other_keys(self, scraper: str) -> None:
        # Mixed config (e.g. selectors + render) must still flag browser.
        assert scraper_needs_browser(scraper, {"render": True, "selector": ".job"}) is True


class TestApiSniffer:
    """``api_sniffer`` is registered with ``needs_browser=True`` but
    drops down to HTTP when an ``api_url`` is supplied. Both branches
    must be honoured."""

    def test_no_config_needs_browser(self) -> None:
        # No config => capture mode (the Netflix scenario from #2250).
        assert scraper_needs_browser("api_sniffer", None) is True

    def test_empty_config_needs_browser(self) -> None:
        assert scraper_needs_browser("api_sniffer", {}) is True

    def test_api_url_skips_browser(self) -> None:
        assert scraper_needs_browser("api_sniffer", {"api_url": "https://x/y"}) is False

    def test_api_url_empty_string_needs_browser(self) -> None:
        # Falsy ``api_url`` (empty string) is treated as "not set".
        assert scraper_needs_browser("api_sniffer", {"api_url": ""}) is True


class TestUnknownAndPassive:
    def test_unknown_scraper_no_browser(self) -> None:
        # Must not crash on a scraper name that isn't registered.
        assert scraper_needs_browser("definitely-not-a-real-scraper", None) is False

    def test_unknown_with_render_true_no_browser(self) -> None:
        # Even with ``render: true``, a non-render-aware scraper must
        # not claim it needs a browser — that would put a task on the
        # browser queue that no scraper would actually use Chromium on.
        assert scraper_needs_browser("not-render-aware", {"render": True}) is False

    def test_jsonld_static_path(self) -> None:
        # The original "static-only" json-ld extractor.
        assert scraper_needs_browser("json-ld", None) is False


class TestRenderAwareSet:
    """Lock the set membership so a future change can't silently drop
    a render-aware scraper. This is the regression that #2237 fixed."""

    def test_set_contents(self) -> None:
        from src.core.scrapers import _RENDER_AWARE_SCRAPERS

        assert frozenset({"dom", "json-ld", "embedded", "nextdata"}) == _RENDER_AWARE_SCRAPERS
