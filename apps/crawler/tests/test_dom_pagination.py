"""Tests for DOM monitor pagination support."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.core.monitors.dom import (
    _MAX_PAGINATION_PAGES,
    _build_url_matcher,
    _extract_links_static,
    _fetch_via_page,
    _paginate_urls,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Patch target: _paginate_urls does `from src.core.monitors import fetch_page_text`
_FETCH_PATCH = "src.core.monitors.fetch_page_text"


def _html_with_links(*urls: str) -> str:
    """Build minimal HTML with anchor tags for the given URLs."""
    links = "".join(f'<a href="{url}">link</a>' for url in urls)
    return f"<html><body>{links}</body></html>"


def _make_fetch(pages: dict[str, str | None]):
    """Return an async function mimicking fetch_page_text with per-URL responses."""

    async def fake_fetch(url, client, **kwargs):
        return pages.get(url)

    return fake_fetch


# ---------------------------------------------------------------------------
# _extract_links_static
# ---------------------------------------------------------------------------


class TestExtractLinksStatic:
    def test_filters_job_keywords(self):
        html = _html_with_links(
            "https://example.com/jobs/123",
            "https://example.com/about",
            "https://example.com/career/456",
        )
        urls = _extract_links_static(html, "https://example.com")
        assert urls == {
            "https://example.com/jobs/123",
            "https://example.com/career/456",
        }

    def test_resolves_relative_urls(self):
        html = _html_with_links("/jobs/42", "/about")
        urls = _extract_links_static(html, "https://example.com/careers/")
        assert "https://example.com/jobs/42" in urls
        assert "https://example.com/about" not in urls

    def test_empty_html(self):
        assert _extract_links_static("", "https://example.com") == set()

    def test_url_matcher_overrides_keywords(self):
        """url_matcher regex replaces the default keyword filter."""
        import re

        html = _html_with_links(
            "https://example.com/emploi/paris/dev/123",
            "https://example.com/about",
            "https://example.com/emploi/lyon/pm/456",
        )
        matcher = re.compile(r"/emploi/")
        urls = _extract_links_static(html, "https://example.com", url_matcher=matcher)
        assert urls == {
            "https://example.com/emploi/paris/dev/123",
            "https://example.com/emploi/lyon/pm/456",
        }

    def test_url_matcher_none_uses_keywords(self):
        """Without url_matcher, default keyword filter applies."""
        html = _html_with_links(
            "https://example.com/emploi/123",
            "https://example.com/jobs/456",
        )
        urls = _extract_links_static(html, "https://example.com", url_matcher=None)
        # /emploi/ doesn't match keywords, /jobs/ does
        assert urls == {"https://example.com/jobs/456"}


class TestBuildUrlMatcher:
    def test_string_filter(self):
        m = _build_url_matcher("/emploi/")
        assert m is not None
        assert m.search("https://example.com/emploi/123")
        assert not m.search("https://example.com/about")

    def test_dict_filter_include(self):
        m = _build_url_matcher({"include": "/jobs/", "exclude": "/blog/"})
        assert m is not None
        assert m.search("https://example.com/jobs/123")

    def test_none_filter(self):
        assert _build_url_matcher(None) is None
        assert _build_url_matcher("") is None


# ---------------------------------------------------------------------------
# _paginate_urls
# ---------------------------------------------------------------------------


class TestPaginateUrls:
    async def test_accumulates_urls(self):
        """Pages with different job links get merged."""
        initial = {"https://example.com/jobs/1"}
        pages = {
            "https://example.com/careers?p=2": _html_with_links("https://example.com/jobs/2"),
            "https://example.com/careers?p=3": _html_with_links("https://example.com/jobs/3"),
        }
        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 3},
                initial,
                MagicMock(),
            )
        assert result == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }

    async def test_stops_on_no_new_links(self):
        """Same links on page 2 as initial -> stops."""
        initial = {"https://example.com/jobs/1"}
        pages = {
            "https://example.com/careers?p=2": _html_with_links(
                "https://example.com/jobs/1"  # duplicate
            ),
        }
        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 10},
                initial,
                MagicMock(),
            )
        assert result == {"https://example.com/jobs/1"}

    async def test_stops_on_fetch_error(self):
        """fetch_page_text returns None -> stops paginating."""
        initial = {"https://example.com/jobs/1"}
        with patch(_FETCH_PATCH, new=_make_fetch({})):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 5},
                initial,
                MagicMock(),
            )
        assert result == {"https://example.com/jobs/1"}

    async def test_respects_max_pages(self):
        """Only fetches up to max_pages."""
        initial = {"https://example.com/jobs/1"}
        call_count = 0
        url_map = {}
        for i in range(2, 20):
            url_map[f"https://example.com/careers?p={i}"] = _html_with_links(
                f"https://example.com/jobs/{i}"
            )

        async def counting_fetch(url, client, **kwargs):
            nonlocal call_count
            call_count += 1
            return url_map.get(url)

        with patch(_FETCH_PATCH, new=counting_fetch):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 4},
                initial,
                MagicMock(),
            )
        # max_pages=4 means pages 2, 3, 4 fetched (page 1 is initial)
        assert call_count == 3
        assert len(result) == 4

    async def test_system_cap(self):
        """max_pages is capped at _MAX_PAGINATION_PAGES."""
        assert _MAX_PAGINATION_PAGES == 200
        initial = set()
        call_count = 0

        async def counting_fetch(url, client, **kwargs):
            nonlocal call_count
            call_count += 1
            return _html_with_links(f"https://example.com/jobs/{call_count}")

        with patch(_FETCH_PATCH, new=counting_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 999},
                initial,
                MagicMock(),
            )
        # Should be capped at 200 - 1 = 199 fetches (pages 2..200)
        assert call_count <= _MAX_PAGINATION_PAGES

    async def test_start_and_increment(self):
        """Custom start and increment produce correct URL params."""
        initial = {"https://example.com/jobs/1"}
        fetched_urls = []

        async def tracking_fetch(url, client, **kwargs):
            fetched_urls.append(url)
            return _html_with_links(f"https://example.com/jobs/{len(fetched_urls) + 1}")

        with patch(_FETCH_PATCH, new=tracking_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "offset", "start": 0, "increment": 20, "max_pages": 3},
                initial,
                MagicMock(),
            )
        # start=0, increment=20 -> first fetch at offset=20, second at offset=40
        assert "offset=20" in fetched_urls[0]
        assert "offset=40" in fetched_urls[1]


# ---------------------------------------------------------------------------
# _fetch_via_page
# ---------------------------------------------------------------------------


class TestFetchViaPage:
    async def test_returns_html(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value="<html>ok</html>")
        result = await _fetch_via_page(page, "https://example.com/page2")
        assert result == "<html>ok</html>"
        page.evaluate.assert_awaited_once()

    async def test_returns_none_on_error(self):
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=Exception("network error"))
        result = await _fetch_via_page(page, "https://example.com/page2")
        assert result is None
