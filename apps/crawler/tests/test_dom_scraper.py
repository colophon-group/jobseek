"""Tests for src.core.scrapers.dom — mock-based, no real browser needed."""

from __future__ import annotations

import json
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
    "enrich": ["description"],
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

    @pytest.mark.parametrize(
        "board_slug",
        ("bajaj-finserv-careers-ps-jobs", "larsen-toubro-careers"),
    )
    def test_peoplestrong_boards_declare_description_enrich(self, board_slug):
        """PeopleStrong listings are rich, so detail scrapes need explicit enrich."""
        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {row["board_slug"]: row for row in rows}
        row = by_slug.get(board_slug)

        assert row is not None, f"{board_slug!r} row missing from boards.csv"
        assert row.get("monitor_type") == "api_sniffer"
        assert row.get("scraper_type") == "dom"

        scraper_config = json.loads(row.get("scraper_config") or "{}")
        assert scraper_config == PEOPLESTRONG_DOM_CONFIG
        metadata = {
            "scraper_type": row.get("scraper_type"),
            "scraper_config": scraper_config,
        }
        assert _board_has_enrich(metadata) == ["description"]


# ---------------------------------------------------------------------------
# gone_url_pattern — issue #2963
#
# L'Oréal's careers site keeps stale URLs in its sitemap that 302-redirect
# to ``/jobs/Error`` once the upstream posting is removed. The dom scraper's
# selectors don't match the error page, so without help the pipeline burns
# three transient backoffs on each (``last_scraped_at`` updates, but
# ``description_r2_hash`` stays NULL) and lands at ``next_scrape_at IS NULL``,
# stranding the row as ``is_active=true`` indefinitely.
#
# ``gone_url_pattern`` checks the FINAL URL after redirects and raises
# ``HTTPStatusError(410)`` so the existing ``_is_permanent_gone`` classifier
# in ``processing/scrape.py`` tombstones on the first failure.
# ---------------------------------------------------------------------------


class TestDomGoneUrlPattern:
    async def test_render_path_raises_410_on_gone_redirect(self):
        """Render path: when ``page.url`` matches gone_url_pattern,
        scrape() raises ``httpx.HTTPStatusError`` with status 410."""
        from src.core.scrapers.dom import scrape

        page = _make_page("<html></html>")
        page.url = "https://careers.loreal.com/en_US/jobs/Error"
        config = {
            "render": True,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page), pytest.raises(httpx.HTTPStatusError) as exc_info:
            await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        assert exc_info.value.response.status_code == 410

    async def test_render_path_skips_actions_on_gone_redirect(self):
        """When gone is detected, run_actions is skipped — actions can
        run an evaluate() pipeline that itself raises on the error page."""
        from src.core.scrapers.dom import scrape

        page = _make_page("<html></html>")
        page.url = "https://careers.loreal.com/en_US/jobs/Error"
        config = {
            "render": True,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "actions": [{"action": "dismiss_overlays"}],
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page), pytest.raises(httpx.HTTPStatusError):
            await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        page.evaluate.assert_not_called()

    async def test_render_path_no_pattern_extracts_normally(self):
        """No gone_url_pattern config -> existing behaviour preserved."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        # Even on the error URL, with no pattern set we don't classify
        # as gone -- extraction proceeds normally (and would land on the
        # transient path via empty extraction, the legacy behaviour).
        page.url = "https://careers.loreal.com/en_US/jobs/Error"
        config = {
            "render": True,
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            result = await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        assert result.title == "Software Engineer"

    async def test_render_path_pattern_no_match_extracts_normally(self):
        """Pattern set, but final URL doesn't match -> extraction proceeds."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        page.url = "https://careers.loreal.com/en_US/jobs/JobDetail/Foo/123"
        config = {
            "render": True,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            result = await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        assert result.title == "Software Engineer"

    async def test_static_path_raises_410_on_gone_redirect(self):
        """Static HTTP path: when the final URL after follow_redirects
        matches gone_url_pattern, scrape() raises HTTPStatusError(410).

        The redirect chain may end on a 200 (rendered "this posting was
        removed" page), so status alone never reveals gone-ness on these
        hosts -- we must inspect the final URL.
        """
        from src.core.scrapers.dom import scrape

        config = {
            "render": False,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "steps": [{"tag": "h1", "field": "title"}],
        }

        # Patch httpx.AsyncClient.get to return a Response whose .url
        # reports the post-redirect error page. (httpx.MockTransport
        # always reports request.url as response.url, which would defeat
        # the test, so we patch the higher-level client method.)
        async def fake_get(self_client, url, **kwargs):
            final_req = httpx.Request("GET", "https://careers.loreal.com/en_US/jobs/Error")
            return httpx.Response(200, text="error page", request=final_req)

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            client = httpx.AsyncClient()
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await scrape(
                    "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                    config,
                    client,
                )
        assert exc_info.value.response.status_code == 410

    async def test_static_path_no_match_extracts_normally(self):
        """Static path: final URL doesn't match -> 200 response is consumed
        normally and steps run."""
        from src.core.scrapers.dom import scrape

        page_html = "<html><body><h1>Real Job</h1></body></html>"

        def handler(request):
            return httpx.Response(200, text=page_html)

        config = {
            "render": False,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "steps": [{"tag": "h1", "field": "title"}],
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://careers.loreal.com/en_US/jobs/JobDetail/Foo/123",
                config,
                client,
            )
        assert result.title == "Real Job"

    async def test_invalid_regex_logs_and_does_not_raise(self):
        """A malformed gone_url_pattern is logged but does not break
        extraction -- the absent guard is preferable to an outage."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        page.url = "https://careers.loreal.com/en_US/jobs/Error"
        config = {
            "render": True,
            "gone_url_pattern": "[unterminated",
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            result = await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        # Extraction proceeds despite the bad regex.
        assert result.title == "Software Engineer"

    def test_loreal_csv_config_pattern_matches_error_redirect(self):
        """Verify the live boards.csv config pattern actually matches the
        L'Oreal error redirect URL we observed in production probes."""
        import csv
        import json
        import re

        from src.shared.constants import DATA_DIR

        with open(DATA_DIR / "boards.csv") as f:
            for row in csv.DictReader(f):
                if row["board_slug"] == "loreal-careers":
                    cfg = json.loads(row["scraper_config"])
                    pat = cfg.get("gone_url_pattern")
                    assert pat, "loreal-careers must define gone_url_pattern"
                    # Empirically observed redirect chains (Hetzner egress,
                    # 2026-05-09): a removed posting 302s to /en_US/jobs/Error.
                    assert re.search(pat, "https://careers.loreal.com/en_US/jobs/Error")
                    assert re.search(pat, "https://careers.loreal.com/en_US/jobs/Error?x=1")
                    # Must NOT match a real posting URL.
                    assert not re.search(
                        pat,
                        "https://careers.loreal.com/en_US/jobs/JobDetail/Foo/123",
                    )
                    return
        raise AssertionError("loreal-careers row not found in boards.csv")


# ---------------------------------------------------------------------------
# Decathlon talentclue.com — kept as a live mirror of the boards.csv config
# so a future bulk-edit of the row gets caught by the suite.
# ---------------------------------------------------------------------------

DECATHLON_DOM_CONFIG = {
    "render": False,
    "steps": [
        {"tag": "title", "field": "title"},
        {
            "tag": "h2",
            "attr": "class=job-description__title",
            "field": "description",
            "html": True,
            "stop": "tienes perfil en",
            "optional": True,
        },
    ],
}


class TestDecathlonDomScraper:
    """Verify the dom scraper extracts title + description from a captured
    decathlon.talentclue.com (Drupal 7) detail page using the boards.csv
    config (#2952).

    Empirical findings that motivated the fix:

    1. The previous selectors used ``class~=job-page__header-title`` syntax
       — the dom scraper's attr matcher splits on ``=`` once, so it ended
       up looking for an attribute literally named ``class~`` and never
       matched anything (0/2 fields extracted on every posting).
    2. Even with the syntax corrected, the title <h1> sits inside a
       ``<header>`` element which ``flatten()`` filters as NOISE_TAGS,
       and the description container is not a single <div>. The working
       config reads the ``<title>`` tag for the headline and starts the
       description range at the first ``<h2 class="job-description__title">``
       block, stopping before the apply UI ("¿Ya tienes perfil en ?").
    """

    def test_decathlon_extraction_vendor(self):
        """Vendor posting yields title + non-trivial description."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "decathlon_talentclue_jobpage.html").read_text()
        result = parse_html(html, DECATHLON_DOM_CONFIG)

        assert result.title == "VENDEDOR/A DEPORTES DE AGUA Decathlon Albacete"
        assert result.description is not None
        assert len(result.description) > 500
        # Description should include the company intro + actual job copy
        assert "DECATHLON" in result.description
        assert "Requisitos" in result.description
        # The apply-UI fragment must NOT leak into the description range
        assert "Autocompletar" not in result.description
        assert "Inscríbete" not in result.description

    def test_decathlon_extraction_taller(self):
        """A second posting (workshop technician) extracts cleanly too —
        guards against over-fitting the config to one job's structure."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "decathlon_talentclue_jobpage_taller.html").read_text()
        result = parse_html(html, DECATHLON_DOM_CONFIG)

        assert result.title == "TÉCNICO/A DE TALLER Decathlon Lugones"
        assert result.description is not None
        assert len(result.description) > 500
        assert "DECATHLON" in result.description
        assert "Autocompletar" not in result.description

    def test_old_class_tilde_config_was_broken(self, recwarn):
        """Regression guard: the prior ``class~=...`` config extracts
        nothing from decathlon talentclue pages. Ensures we don't revert.
        """
        from src.core.scrapers.dom import parse_html

        old_config = {
            "steps": [
                {
                    "tag": "h1",
                    "attr": "class~=job-page__header-title",
                    "field": "title",
                },
                {
                    "tag": "div",
                    "attr": "class~=job-page__content",
                    "field": "description",
                    "html": True,
                },
            ]
        }
        html = (FIXTURES_DIR / "decathlon_talentclue_jobpage.html").read_text()
        result = parse_html(html, old_config)
        # 0/2 fields extracted — what the live crawler observed for 557
        # active postings before this fix (all has_content=false in
        # Typesense, all next_scrape_at=NULL in Postgres).
        assert result.title is None
        assert result.description is None

    def test_decathlon_config_routes_to_http_queue(self):
        """``render: false`` keeps the scraper on the slim HTTP worker —
        the talentclue page is fully rendered server-side (Drupal 7) so
        Playwright is unnecessary.
        """
        from src.core.scrapers import scraper_needs_browser

        assert scraper_needs_browser("dom", DECATHLON_DOM_CONFIG) is False


# ---------------------------------------------------------------------------
# ayuda-en-accion talentclue.com — sibling cluster of Decathlon (#2962/#2963).
# Same Drupal 7 talentclue page structure; the ``stop`` marker differs
# because ayuda-en-accion has no b4work integration, so the apply CTA
# falls through to the Spanish "Inscríbete" button.
# ---------------------------------------------------------------------------

AYUDA_EN_ACCION_DOM_CONFIG = {
    "render": False,
    "steps": [
        {"tag": "title", "field": "title"},
        {
            "tag": "h2",
            "attr": "class=job-description__title",
            "field": "description",
            "html": True,
            "stop": "Inscríbete",
            "optional": True,
        },
    ],
}


class TestAyudaEnAccionDomScraper:
    """Verify the dom scraper extracts title + description from a captured
    empleoayudaenaccion.talentclue.com (Drupal 7) detail page using the
    boards.csv config (sibling of #2952 / #2962, tracked in #2963).

    Same Drupal 7 talentclue layout as Decathlon: <h1 class="job-page__
    header-title"> sits inside a <header> (filtered as NOISE_TAGS by
    flatten()), and the description is a series of <h2 class=
    "job-description__title"> blocks rather than a single <div>.

    The original ``class~=...`` syntax was a parser bug — the dom scraper's
    attr matcher splits on the first ``=`` only, so it looked for an
    attribute literally named ``class~`` and never matched anything
    (0/2 fields extracted on every posting → 130 active rows with
    has_content=false in Typesense).
    """

    def test_ayuda_en_accion_extraction_consultoria(self):
        """Consultoría posting yields title + non-trivial description."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "ayuda_en_accion_talentclue_jobpage.html").read_text()
        result = parse_html(html, AYUDA_EN_ACCION_DOM_CONFIG)

        assert result.title is not None
        assert "AGROTECH BOLIVIA 2026" in result.title
        assert result.description is not None
        assert len(result.description) > 500
        # Description should include the company intro + the actual job copy
        assert "Ayuda en Acción" in result.description
        # The apply UI / footer must NOT leak into the description range
        assert "Inscríbete" not in result.description
        assert "Mira el resto" not in result.description
        assert "Powered by" not in result.description

    def test_ayuda_en_accion_extraction_coordinador(self):
        """A second posting (Coordinador/a Territorial) extracts cleanly
        too — guards against over-fitting the config to one job's structure.
        """
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "ayuda_en_accion_talentclue_jobpage_coord.html").read_text()
        result = parse_html(html, AYUDA_EN_ACCION_DOM_CONFIG)

        assert result.title == "Coordinador/a Territorial"
        assert result.description is not None
        assert len(result.description) > 500
        assert "Ayuda en Acción" in result.description
        assert "Inscríbete" not in result.description
        assert "Mira el resto" not in result.description

    def test_ayuda_en_accion_old_class_tilde_config_was_broken(self, recwarn):
        """Regression guard: the prior ``class~=...`` config extracts
        nothing from ayuda-en-accion talentclue pages. Ensures we don't
        revert the pre-#2963 boards.csv row.
        """
        from src.core.scrapers.dom import parse_html

        old_config = {
            "steps": [
                {
                    "tag": "h1",
                    "attr": "class~=job-page__header-title",
                    "field": "title",
                },
                {
                    "tag": "div",
                    "attr": "class~=job-page__content",
                    "field": "description",
                    "html": True,
                },
            ]
        }
        html = (FIXTURES_DIR / "ayuda_en_accion_talentclue_jobpage.html").read_text()
        result = parse_html(html, old_config)
        # 0/2 fields extracted — the live state for 130 active postings
        # before this fix (all has_content=false in Typesense).
        assert result.title is None
        assert result.description is None

    def test_ayuda_en_accion_config_routes_to_http_queue(self):
        """``render: false`` keeps the scraper on the slim HTTP worker —
        the talentclue page is fully rendered server-side (Drupal 7) so
        Playwright is unnecessary.
        """
        from src.core.scrapers import scraper_needs_browser

        assert scraper_needs_browser("dom", AYUDA_EN_ACCION_DOM_CONFIG) is False


# ---------------------------------------------------------------------------
# barcelona-activa talentclue.com — sibling cluster of Decathlon (#2962/#2963).
# Same Drupal 7 talentclue page structure; barcelona-activa runs the
# Catalan UI (Inscriu-t'hi) but exposes the b4work integration, so the
# Spanish "¿Ya tienes perfil en ?" line still appears on every page —
# we use ``stop: "tienes perfil en"`` to match Decathlon's anchor.
# ---------------------------------------------------------------------------

BARCELONA_ACTIVA_DOM_CONFIG = {
    "render": False,
    "steps": [
        {"tag": "title", "field": "title"},
        {
            "tag": "h2",
            "attr": "class=job-description__title",
            "field": "description",
            "html": True,
            "stop": "tienes perfil en",
            "optional": True,
        },
    ],
}


class TestBarcelonaActivaDomScraper:
    """Verify the dom scraper extracts title + description from a captured
    barcelonactiva.talentclue.com (Drupal 7) detail page using the
    boards.csv config (sibling of #2952 / #2962, tracked in #2963).

    Same root cause as Decathlon and ayuda-en-accion: the prior
    ``class~=...`` selector was a parser bug. 171 active rows had
    has_content=false in Typesense before this fix.
    """

    def test_barcelona_activa_extraction_monitor(self):
        """Monitor d'Oci Infantil posting yields title + non-trivial
        description (Catalan content)."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "barcelona_activa_talentclue_jobpage.html").read_text()
        result = parse_html(html, BARCELONA_ACTIVA_DOM_CONFIG)

        assert result.title is not None
        assert "Monitor" in result.title
        assert result.description is not None
        assert len(result.description) > 500
        # Description should include the company intro
        assert "Barcelona Activa" in result.description
        # The apply UI / footer must NOT leak into the description range
        assert "tienes perfil en" not in result.description
        assert "Autocompletar" not in result.description
        assert "Powered by" not in result.description

    def test_barcelona_activa_extraction_admin(self):
        """A second posting (Administratiu/iva de recepció) extracts
        cleanly too — guards against over-fitting the config to one
        job's structure.
        """
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "barcelona_activa_talentclue_jobpage_admin.html").read_text()
        result = parse_html(html, BARCELONA_ACTIVA_DOM_CONFIG)

        assert result.title is not None
        assert "Administratiu" in result.title
        assert result.description is not None
        assert len(result.description) > 500
        assert "Barcelona Activa" in result.description
        assert "tienes perfil en" not in result.description
        assert "Autocompletar" not in result.description

    def test_barcelona_activa_old_class_tilde_config_was_broken(self, recwarn):
        """Regression guard: the prior ``class~=...`` config extracts
        nothing from barcelona-activa talentclue pages. Ensures we don't
        revert the pre-#2963 boards.csv row.
        """
        from src.core.scrapers.dom import parse_html

        old_config = {
            "steps": [
                {
                    "tag": "h1",
                    "attr": "class~=job-page__header-title",
                    "field": "title",
                },
                {
                    "tag": "div",
                    "attr": "class~=job-page__content",
                    "field": "description",
                    "html": True,
                },
            ]
        }
        html = (FIXTURES_DIR / "barcelona_activa_talentclue_jobpage.html").read_text()
        result = parse_html(html, old_config)
        # 0/2 fields extracted — the live state for 171 active postings
        # before this fix (all has_content=false in Typesense).
        assert result.title is None
        assert result.description is None

    def test_barcelona_activa_config_routes_to_http_queue(self):
        """``render: false`` keeps the scraper on the slim HTTP worker —
        the talentclue page is fully rendered server-side (Drupal 7) so
        Playwright is unnecessary.
        """
        from src.core.scrapers import scraper_needs_browser

        assert scraper_needs_browser("dom", BARCELONA_ACTIVA_DOM_CONFIG) is False
