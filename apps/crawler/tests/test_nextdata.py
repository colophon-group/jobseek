from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.nextdata import (
    _build_url,
    _extract_field,
    _extract_next_data,
    _resolve_path,
    can_handle,
    discover,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NEXT_DATA = {
    "props": {
        "pageProps": {
            "positions": [
                {
                    "id": "abc-123",
                    "text": "Engineer",
                    "locations": [{"name": "London"}, {"name": "Remote"}],
                    "team": "Engineering",
                    "category": {"name": "Tech"},
                },
                {
                    "id": "def-456",
                    "text": "Designer",
                    "locations": [{"name": "Remote"}],
                    "team": "Design",
                    "category": {"name": "Creative"},
                },
            ]
        }
    }
}


def _html_with_next_data(data: dict) -> str:
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


SAMPLE_HTML = _html_with_next_data(NEXT_DATA)

BOARD_RICH = {
    "board_url": "https://example.com/careers",
    "metadata": {
        "path": "props.pageProps.positions",
        "url_template": "https://example.com/careers/{slug}-{id}/",
        "slug_fields": ["text"],
        "fields": {
            "title": "text",
            "locations": "locations[].name",
            "metadata.team": "team",
        },
    },
}

BOARD_URL_ONLY = {
    "board_url": "https://example.com/careers",
    "metadata": {
        "path": "props.pageProps.positions",
        "url_template": "https://example.com/careers/{slug}-{id}/",
        "slug_fields": ["text"],
    },
}


def _mock_transport(html: str, status: int = 200):
    def handler(request):
        return httpx.Response(status, text=html)
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestResolvePath:
    def test_valid_path(self):
        assert _resolve_path({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_list_value(self):
        assert _resolve_path(NEXT_DATA, "props.pageProps.positions") == NEXT_DATA["props"]["pageProps"]["positions"]

    def test_missing_key(self):
        assert _resolve_path({"a": {"b": 1}}, "a.x") is None

    def test_empty_data(self):
        assert _resolve_path({}, "a.b") is None

    def test_single_key(self):
        assert _resolve_path({"a": 1}, "a") == 1

    def test_non_dict_intermediate(self):
        assert _resolve_path({"a": "string"}, "a.b") is None


class TestExtractField:
    def test_simple_key(self):
        item = {"text": "Engineer", "id": "123"}
        assert _extract_field(item, "text") == "Engineer"

    def test_nested_key(self):
        item = {"category": {"name": "Tech"}}
        assert _extract_field(item, "category.name") == "Tech"

    def test_array_unwrap(self):
        item = {"locations": [{"name": "London"}, {"name": "Remote"}]}
        assert _extract_field(item, "locations[].name") == ["London", "Remote"]

    def test_missing_key(self):
        item = {"text": "Engineer"}
        assert _extract_field(item, "missing") is None

    def test_missing_nested(self):
        item = {"category": {"name": "Tech"}}
        assert _extract_field(item, "category.missing") is None

    def test_array_unwrap_missing_array(self):
        item = {"text": "Engineer"}
        assert _extract_field(item, "locations[].name") is None

    def test_numeric_value_converted(self):
        item = {"count": 42}
        assert _extract_field(item, "count") == "42"

    def test_array_unwrap_empty_array(self):
        item = {"locations": []}
        assert _extract_field(item, "locations[].name") is None


class TestBuildUrl:
    def test_basic_substitution(self):
        item = {"id": "abc-123", "text": "Engineer"}
        url = _build_url(item, "https://example.com/{slug}-{id}/", ["text"])
        assert url == "https://example.com/engineer-abc-123/"

    def test_no_slug_fields(self):
        item = {"id": "abc-123"}
        url = _build_url(item, "https://example.com/jobs/{id}", None)
        assert url == "https://example.com/jobs/abc-123"

    def test_missing_variable(self):
        item = {"id": "abc-123"}
        url = _build_url(item, "https://example.com/{slug}-{id}/", ["text"])
        # "text" not in item, so slug won't be set -> KeyError -> None
        assert url is None

    def test_multiple_slug_fields(self):
        item = {"title": "Senior Engineer", "dept": "Backend"}
        url = _build_url(item, "https://example.com/{slug}/", ["title", "dept"])
        assert url == "https://example.com/senior-engineer-backend/"

    def test_integer_values(self):
        item = {"id": 42}
        url = _build_url(item, "https://example.com/jobs/{id}", None)
        assert url == "https://example.com/jobs/42"


class TestExtractNextData:
    def test_valid_html(self):
        data = _extract_next_data(SAMPLE_HTML)
        assert data == NEXT_DATA

    def test_no_script(self):
        assert _extract_next_data("<html><body>No script</body></html>") is None

    def test_invalid_json(self):
        html = '<html><script id="__NEXT_DATA__">{invalid json}</script></html>'
        assert _extract_next_data(html) is None

    def test_multiline_json(self):
        data = {"props": {"test": True}}
        html = f'<script id="__NEXT_DATA__" type="application/json">\n{json.dumps(data)}\n</script>'
        assert _extract_next_data(html) == data


# ---------------------------------------------------------------------------
# Rich mode tests
# ---------------------------------------------------------------------------


class TestDiscoverRichMode:
    async def test_returns_discovered_jobs(self):
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_RICH, client)

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(j, DiscoveredJob) for j in result)

    async def test_job_fields_mapped(self):
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_RICH, client)

        eng = next(j for j in result if j.title == "Engineer")
        assert eng.url == "https://example.com/careers/engineer-abc-123/"
        assert eng.locations == ["London", "Remote"]
        assert eng.metadata == {"team": "Engineering"}

    async def test_partial_fields(self):
        """Items with missing fields still produce DiscoveredJob with None."""
        data = {
            "props": {
                "pageProps": {
                    "positions": [
                        {"id": "x", "text": "PM"},  # no locations, no team
                    ]
                }
            }
        }
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "path": "props.pageProps.positions",
                "url_template": "https://example.com/{slug}-{id}/",
                "slug_fields": ["text"],
                "fields": {"title": "text", "locations": "locations[].name", "metadata.team": "team"},
            },
        }
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)

        assert len(result) == 1
        assert result[0].title == "PM"
        assert result[0].locations is None
        assert result[0].metadata is None

    async def test_locations_array_unwrap(self):
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_RICH, client)

        designer = next(j for j in result if j.title == "Designer")
        assert designer.locations == ["Remote"]


# ---------------------------------------------------------------------------
# URL-only mode tests
# ---------------------------------------------------------------------------


class TestDiscoverUrlOnlyMode:
    async def test_returns_set_of_urls(self):
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_URL_ONLY, client)

        assert isinstance(result, set)
        assert len(result) == 2
        assert "https://example.com/careers/engineer-abc-123/" in result
        assert "https://example.com/careers/designer-def-456/" in result


# ---------------------------------------------------------------------------
# Fetch method tests
# ---------------------------------------------------------------------------


class TestFetchMethods:
    async def test_httpx_fetch(self):
        """Default (render=False) uses httpx."""
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_RICH, client)
        assert len(result) == 2

    async def test_render_uses_playwright(self):
        """render=True delegates to shared.browser.render."""
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                **BOARD_RICH["metadata"],
                "render": True,
            },
        }
        with patch("src.core.monitors.nextdata.fetch_page_text") as mock_fetch:
            mock_fetch.return_value = None  # should NOT be called
            with patch("src.core.monitors.nextdata._fetch_html", new_callable=AsyncMock) as mock_fh:
                mock_fh.return_value = SAMPLE_HTML
                result = await discover(board, httpx.AsyncClient())

        assert isinstance(result, list)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# can_handle tests
# ---------------------------------------------------------------------------


class TestCanHandle:
    async def test_nextjs_page_with_jobs(self):
        # can_handle requires >=5 items to consider the array plausible
        data = {
            "props": {
                "pageProps": {
                    "positions": [{"id": str(i), "text": f"Job {i}"} for i in range(6)]
                }
            }
        }
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://example.com/careers", client)

        assert result is not None
        assert result["path"] == "props.pageProps.positions"

    async def test_non_nextjs_page(self):
        html = "<html><body>Regular page</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    async def test_nextjs_no_jobs_array(self):
        """__NEXT_DATA__ exists but no recognized jobs path."""
        data = {"props": {"pageProps": {"somethingElse": "data"}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    async def test_nextjs_too_few_items(self):
        """Array exists but has <5 items — not plausible."""
        data = {"props": {"pageProps": {"positions": [{"id": 1}, {"id": 2}]}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    async def test_fetch_failure(self):
        async with httpx.AsyncClient(transport=_mock_transport("", status=500)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    async def test_render_fallback(self):
        """When static HTTP has no __NEXT_DATA__, falls back to Playwright."""
        data = {
            "props": {
                "pageProps": {
                    "positions": [{"id": str(i), "text": f"Job {i}"} for i in range(6)]
                }
            }
        }
        rendered_html = _html_with_next_data(data)
        # Static HTML has no __NEXT_DATA__
        plain_html = "<html><body>Regular page</body></html>"

        with patch("src.shared.browser.render", new_callable=AsyncMock) as mock_render:
            mock_render.return_value = rendered_html
            async with httpx.AsyncClient(transport=_mock_transport(plain_html)) as client:
                result = await can_handle("https://example.com/careers", client)

        assert result is not None
        assert result["path"] == "props.pageProps.positions"
        assert result["render"] is True
        mock_render.assert_awaited_once()

    async def test_render_fallback_not_used_when_static_works(self):
        """Playwright is not invoked when static HTTP finds __NEXT_DATA__."""
        data = {
            "props": {
                "pageProps": {
                    "positions": [{"id": str(i), "text": f"Job {i}"} for i in range(6)]
                }
            }
        }
        html = _html_with_next_data(data)

        with patch("src.shared.browser.render", new_callable=AsyncMock) as mock_render:
            async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
                result = await can_handle("https://example.com/careers", client)

        assert result is not None
        assert "render" not in result
        mock_render.assert_not_awaited()


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_missing_next_data(self):
        html = "<html><body>No Next.js here</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RICH, client)
        assert result == []

    async def test_missing_next_data_url_mode(self):
        html = "<html><body>No Next.js here</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_URL_ONLY, client)
        assert result == set()

    async def test_invalid_json(self):
        html = '<html><script id="__NEXT_DATA__">{bad json</script></html>'
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RICH, client)
        assert result == []

    async def test_path_not_found(self):
        data = {"props": {"pageProps": {"other": []}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RICH, client)
        assert result == []

    async def test_max_urls_cap(self):
        items = [{"id": str(i), "text": f"Job {i}"} for i in range(10_500)]
        data = {"props": {"pageProps": {"positions": items}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_URL_ONLY, client)
        assert len(result) <= 10_000

    async def test_missing_path_config(self):
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {"url_template": "https://example.com/{id}"},
        }
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(board, client)
        assert result == set()

    async def test_missing_url_template_config(self):
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {"path": "props.pageProps.positions"},
        }
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(board, client)
        assert result == set()

    async def test_non_dict_items_skipped(self):
        data = {"props": {"pageProps": {"positions": ["string1", "string2", {"id": "1", "text": "Job"}]}}}
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "path": "props.pageProps.positions",
                "url_template": "https://example.com/{id}",
            },
        }
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)
        assert isinstance(result, set)
        assert len(result) == 1
