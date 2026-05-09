"""Tests for src.core.scrapers.dom — mock-based, no real browser needed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.scrapers import JobContent

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Helpers (same pattern as test_browser_shared.py)
# ---------------------------------------------------------------------------


def _make_page(html: str = "<html></html>") -> MagicMock:
    page = MagicMock()
    page.goto = AsyncMock()
    page.evaluate = AsyncMock()
    page.content = AsyncMock(return_value=html)

    locator_first = MagicMock()
    locator_first.count = AsyncMock(return_value=1)
    locator_first.click = AsyncMock()
    locator = MagicMock()
    locator.first = locator_first
    page.locator = MagicMock(return_value=locator)
    return page


def _make_pw(page: MagicMock | None = None) -> MagicMock:
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


def _patch_playwright(page: MagicMock):
    """Return a patch context for async_playwright that yields our mock."""
    mock_pw = _make_pw(page)
    mock_async_pw = MagicMock()
    mock_async_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_async_pw.__aexit__ = AsyncMock(return_value=False)
    return patch("playwright.async_api.async_playwright", return_value=mock_async_pw)


FIXTURE_HTML = """
<html><body>
<h1>Software Engineer</h1>
<div class="location">
<h2>Location</h2>
<p>London, UK</p>
</div>
<div class="about">
<h2>About the role</h2>
<p>Build amazing things.</p>
<p>Work with great people.</p>
<h2>Requirements</h2>
<ul>
<li>Python</li>
<li>JavaScript</li>
</ul>
</div>
<div class="meta">
<h3>Team</h3>
<p>Platform</p>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDomScraper:
    async def test_missing_steps_returns_empty(self):
        """No 'steps' key → empty JobContent."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", {}, httpx.AsyncClient())
        assert result == JobContent()

    async def test_title_extraction(self):
        """Step with tag: h1 extracts the title."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {"render": True, "steps": [{"tag": "h1", "field": "title"}]}
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.title == "Software Engineer"

    async def test_description_html(self):
        """html: true step produces an HTML fragment."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "steps": [
                {
                    "text": "About the role",
                    "field": "description",
                    "stop": "Requirements",
                    "html": True,
                },
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.description is not None
        assert "<" in result.description  # contains HTML tags

    async def test_location_single(self):
        """Singular 'location' field gets wrapped into locations list."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "steps": [
                {"text": "Location", "offset": 1, "field": "location"},
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.locations is not None
        assert isinstance(result.locations, list)
        assert "London, UK" in result.locations[0]

    async def test_locations_split(self):
        """split step produces a list."""
        from src.core.scrapers.dom import scrape

        html = "<html><body><h2>Locations</h2><p>London | Berlin | Remote</p></body></html>"
        page = _make_page(html)
        config = {
            "render": True,
            "steps": [
                {"text": "Locations", "offset": 1, "field": "locations", "split": " | "},
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.locations == ["London", "Berlin", "Remote"]

    async def test_metadata_fields(self):
        """metadata.team goes to JobContent.metadata."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "steps": [
                {"text": "Team", "offset": 1, "field": "metadata.team"},
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.metadata is not None
        assert result.metadata["team"] == "Platform"

    async def test_qualifications_list(self):
        """List field extraction."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "steps": [
                {"text": "Requirements", "field": "qualifications", "stop_count": 3, "split": "\n"},
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.extras is not None
        assert isinstance(result.extras.get("qualifications"), list)

    async def test_browser_config_passed(self):
        """wait/timeout forwarded to navigate."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "wait": "load",
            "timeout": 5000,
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        page.goto.assert_awaited_once_with(
            "https://example.com/job/1", wait_until="load", timeout=5000
        )

    async def test_actions_executed(self):
        """Action pipeline runs before extraction."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "actions": [{"action": "dismiss_overlays"}],
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        # dismiss_overlays calls page.evaluate
        page.evaluate.assert_awaited_once()

    async def test_static_fetch_title(self):
        """render: false uses HTTP instead of Playwright."""
        from src.core.scrapers.dom import scrape

        page_html = "<html><body><h1>Static Title</h1></body></html>"

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job/1",
                {"render": False, "steps": [{"tag": "h1", "field": "title"}]},
                client,
            )
        assert result.title == "Static Title"

    async def test_static_fetch_multiple_fields(self):
        """render: false extracts multiple fields from static HTML."""
        from src.core.scrapers.dom import scrape

        page_html = """<html><body>
        <h1>Data Engineer</h1>
        <div class="location">
        <h2>Location</h2>
        <p>Berlin, Germany</p>
        </div>
        <div class="desc">
        <h2>About</h2>
        <p>Build data pipelines.</p>
        </div>
        </body></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        config = {
            "render": False,
            "steps": [
                {"tag": "h1", "field": "title"},
                {"text": "Location", "offset": 1, "field": "location"},
                {"text": "About", "offset": 1, "field": "description"},
            ],
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job/1", config, client)
        assert result.title == "Data Engineer"
        assert result.locations is not None
        assert "Berlin" in result.locations[0]
        assert result.description is not None
        assert "pipelines" in result.description

    async def test_static_fetch_no_steps_returns_empty(self):
        """render: false with no steps returns empty JobContent."""
        from src.core.scrapers.dom import scrape

        result = await scrape(
            "https://example.com/job/1",
            {"render": False},
            httpx.AsyncClient(),
        )
        assert result == JobContent()

    async def test_actions_override_render_false(self):
        """actions + render: false overrides to render: true with warning."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": False,
            "actions": [{"action": "dismiss_overlays"}],
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.title == "Software Engineer"
        # Confirms Playwright was used (page.evaluate called by dismiss_overlays)
        page.evaluate.assert_awaited_once()

    async def test_playwright_import_error(self):
        """Raises RuntimeError when playwright is not installed."""
        from src.core.scrapers.dom import scrape

        config = {"render": True, "steps": [{"tag": "h1", "field": "title"}]}
        http = httpx.AsyncClient()

        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def fake_import(name, *args, **kwargs):
            if name == "playwright.async_api":
                raise ImportError("no playwright")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(RuntimeError, match="playwright is required"),
        ):
            await scrape("https://example.com/job/1", config, http)


# ---------------------------------------------------------------------------
# PeopleStrong (Larsen & Toubro / Bajaj Finserv) — issue #2952
#
# The peoplestrong career portal renders job detail pages as a single-page
# Angular app behind Incapsula. Static HTML is just the empty ``<app-root>``
# shell, so the dom config MUST set ``render: true`` for Playwright to
# execute the JS. The rendered DOM uses ``<h2 data-testid=...>`` for the job
# title (NOT ``<h1>``) — early dom configs that keyed off ``h1`` matched
# nothing and produced 0 descriptions across thousands of postings.
#
# These tests exercise the SHARED config now used by both larsen-toubro and
# bajaj-finserv against captured-from-prod fixtures.
# ---------------------------------------------------------------------------


# Shared dom config used in boards.csv for both peoplestrong companies.
# Kept here so any change to the live config is mirrored by the tests.
PEOPLESTRONG_DOM_CONFIG = {
    "render": True,
    "wait": "networkidle",
    "steps": [
        {
            "tag": "h2",
            "attr": "data-testid=job-detail-top-h2-page-1",
            "field": "title",
        },
        {
            "text": "Job Description",
            "offset": 1,
            "field": "description",
            "stop": "expand_less",
            "html": True,
            "optional": True,
        },
    ],
}


class TestPeopleStrongDomScraper:
    """Verify the dom scraper extracts title + description from a captured
    rendered peoplestrong detail page using the boards.csv config.
    """

    def test_larsen_toubro_extraction(self):
        """L&T detail page yields title + non-trivial HTML description."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "peoplestrong_larsen_toubro.html").read_text()
        result = parse_html(html, PEOPLESTRONG_DOM_CONFIG)

        assert result.title == "Assistant Manager - Strategic Sourcing"
        assert result.description is not None
        # Description should be non-trivial HTML with the expected structure
        assert len(result.description) > 200
        assert "<ul>" in result.description
        assert "Strategic Sourcing" in result.description
        # The trailing 'expand_less' Material icon must NOT leak in
        assert "expand_less" not in result.description

    def test_bajaj_finserv_extraction(self):
        """Bajaj detail page yields title + non-trivial description.

        Bajaj's 'JOB DESCRIPTION' heading is uppercase; the matcher in
        walk_steps is case-insensitive so the same step config matches.
        """
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "peoplestrong_bajaj_finserv.html").read_text()
        result = parse_html(html, PEOPLESTRONG_DOM_CONFIG)

        assert result.title == "Manager - Professional Loans"
        assert result.description is not None
        assert len(result.description) > 200
        assert "expand_less" not in result.description

    def test_old_h1_config_was_broken(self, recwarn):
        """Regression guard: the prior <h1>-based config extracts nothing
        from peoplestrong pages. Ensures we don't accidentally revert.
        """
        from src.core.scrapers.dom import parse_html

        old_config = {
            "steps": [
                {"tag": "h1", "field": "title"},
                {
                    "text": "Location",
                    "offset": 1,
                    "field": "location",
                    "optional": True,
                },
                {
                    "tag": "h1",
                    "offset": 1,
                    "field": "description",
                    "stop": "Apply",
                    "html": True,
                    "optional": True,
                },
            ]
        }
        html = (FIXTURES_DIR / "peoplestrong_larsen_toubro.html").read_text()
        result = parse_html(html, old_config)
        # Old config yields no title and no description — what the live
        # crawler observed before this fix. ``recwarn`` swallows the
        # expected ``step ... not found`` UserWarning.
        assert result.title is None
        assert result.description is None

    def test_peoplestrong_config_routes_to_browser_queue(self):
        """The dom config sets render: true, so workers must dispatch it
        to the browser queue (slim HTTP workers can't load Chromium).
        """
        from src.core.scrapers import scraper_needs_browser

        assert scraper_needs_browser("dom", PEOPLESTRONG_DOM_CONFIG) is True
