"""Tests for the Notion monitor."""

from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitors.notion import (
    _extract_child_pages,
    _extract_title,
    _page_url,
    _parse_notion_url,
    can_handle,
    discover,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SUBDOMAIN = "acme"
SPACE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PAGE_ID = "11111111-2222-3333-4444-555555555555"
HOME_PAGE_ID = "66666666-7777-8888-9999-aaaaaaaaaaaa"

JOB_PAGES = [
    {"id": "aaa11111-1111-1111-1111-111111111111", "type": "page", "title": "Software Engineer"},
    {"id": "bbb22222-2222-2222-2222-222222222222", "type": "page", "title": "Product Manager"},
    {"id": "ccc33333-3333-3333-3333-333333333333", "type": "page", "title": "Data Analyst"},
]


def _make_block(block_id, block_type="page", title="", content=None, alive=True, parent_id=None):
    """Build a Notion block record in the API response format."""
    val = {"type": block_type, "alive": alive}
    if parent_id:
        val["parent_id"] = parent_id
    if content:
        val["content"] = content
    if title:
        val["properties"] = {"title": [[title]]}
    return {block_id: {"value": {"value": val}}}


def _make_chunk_response(parent_id, children):
    """Build a loadPageChunk response with a parent page containing child pages."""
    blocks = {}
    child_ids = []
    for child in children:
        cid = child["id"]
        child_ids.append(cid)
        blocks.update(_make_block(
            cid,
            block_type=child.get("type", "page"),
            title=child.get("title", ""),
            alive=child.get("alive", True),
            parent_id=parent_id,
        ))
    blocks.update(_make_block(parent_id, block_type="page", content=child_ids))
    return {"recordMap": {"block": blocks}}


def _make_public_page_data(space_id=SPACE_ID, public_home=None):
    return {
        "spaceId": space_id,
        "publicHomePage": public_home,
        "publicAccessRole": "reader",
    }


# ---------------------------------------------------------------------------
# Unit tests — URL parsing
# ---------------------------------------------------------------------------


class TestParseNotionUrl:
    def test_standard_url_with_slug(self):
        sub, pid = _parse_notion_url(
            "https://acme.notion.site/Job-Board-11111111222233334444555555555555"
        )
        assert sub == "acme"
        assert pid == "11111111-2222-3333-4444-555555555555"

    def test_url_without_slug(self):
        sub, pid = _parse_notion_url(
            "https://acme.notion.site/11111111222233334444555555555555"
        )
        assert sub == "acme"
        assert pid == "11111111-2222-3333-4444-555555555555"

    def test_url_with_dashed_uuid(self):
        sub, pid = _parse_notion_url(
            "https://acme.notion.site/11111111-2222-3333-4444-555555555555"
        )
        assert sub == "acme"
        # Dashes are stripped then re-formatted
        assert pid == "11111111-2222-3333-4444-555555555555"

    def test_root_url(self):
        sub, pid = _parse_notion_url("https://acme.notion.site/")
        assert sub == "acme"
        assert pid is None

    def test_non_notion_url(self):
        sub, pid = _parse_notion_url("https://example.com/careers")
        assert sub is None
        assert pid is None

    def test_hyphenated_subdomain(self):
        sub, pid = _parse_notion_url(
            "https://my-company.notion.site/aabbccdd11223344aabbccdd11223344"
        )
        assert sub == "my-company"
        assert pid is not None

    def test_path_with_subpath(self):
        sub, pid = _parse_notion_url(
            "https://acme.notion.site/job-posts"
        )
        assert sub == "acme"
        # "job-posts" has no 32 hex chars
        assert pid is None


class TestPageUrl:
    def test_builds_correct_url(self):
        url = _page_url("acme", "11111111-2222-3333-4444-555555555555")
        assert url == "https://acme.notion.site/11111111222233334444555555555555"


class TestExtractTitle:
    def test_simple_title(self):
        val = {"properties": {"title": [["Hello World"]]}}
        assert _extract_title(val) == "Hello World"

    def test_formatted_title(self):
        val = {"properties": {"title": [["Bold text", [["b"]]], [" normal"]]}}
        assert _extract_title(val) == "Bold text normal"

    def test_missing_title(self):
        assert _extract_title({}) == ""
        assert _extract_title({"properties": {}}) == ""


# ---------------------------------------------------------------------------
# Unit tests — child page extraction
# ---------------------------------------------------------------------------


class TestExtractChildPages:
    def test_extracts_child_pages(self):
        data = _make_chunk_response(PAGE_ID, JOB_PAGES)
        pages = _extract_child_pages(data, PAGE_ID)
        assert len(pages) == 3
        titles = {p["title"] for p in pages}
        assert titles == {"Software Engineer", "Product Manager", "Data Analyst"}

    def test_skips_non_page_blocks(self):
        children = [
            *JOB_PAGES,
            {"id": "ddd44444-4444-4444-4444-444444444444", "type": "text", "title": "Not a page"},
        ]
        data = _make_chunk_response(PAGE_ID, children)
        pages = _extract_child_pages(data, PAGE_ID)
        assert len(pages) == 3

    def test_skips_deleted_pages(self):
        children = [
            *JOB_PAGES,
            {"id": "eee55555-5555-5555-5555-555555555555", "type": "page", "title": "Deleted", "alive": False},
        ]
        data = _make_chunk_response(PAGE_ID, children)
        pages = _extract_child_pages(data, PAGE_ID)
        assert len(pages) == 3

    def test_empty_page(self):
        data = _make_chunk_response(PAGE_ID, [])
        pages = _extract_child_pages(data, PAGE_ID)
        assert pages == []

    def test_include_nested(self):
        """Grandchild pages are included when include_nested=True."""
        grandchild_id = "fff66666-6666-6666-6666-666666666666"
        child_id = JOB_PAGES[0]["id"]

        data = _make_chunk_response(PAGE_ID, JOB_PAGES)
        # Add grandchild content to first child
        child_block = data["recordMap"]["block"][child_id]["value"]["value"]
        child_block["content"] = [grandchild_id]
        # Add grandchild block
        data["recordMap"]["block"].update(
            _make_block(grandchild_id, title="Nested Job", parent_id=child_id)
        )

        pages = _extract_child_pages(data, PAGE_ID, include_nested=True)
        assert len(pages) == 4
        assert any(p["title"] == "Nested Job" for p in pages)

    def test_nested_excluded_by_default(self):
        grandchild_id = "fff66666-6666-6666-6666-666666666666"
        child_id = JOB_PAGES[0]["id"]

        data = _make_chunk_response(PAGE_ID, JOB_PAGES)
        child_block = data["recordMap"]["block"][child_id]["value"]["value"]
        child_block["content"] = [grandchild_id]
        data["recordMap"]["block"].update(
            _make_block(grandchild_id, title="Nested Job", parent_id=child_id)
        )

        pages = _extract_child_pages(data, PAGE_ID, include_nested=False)
        assert len(pages) == 3


# ---------------------------------------------------------------------------
# Integration tests — can_handle probe
# ---------------------------------------------------------------------------


class TestCanHandle:
    @pytest.mark.asyncio
    async def test_detects_notion_site_with_jobs(self):
        chunk = _make_chunk_response(PAGE_ID, JOB_PAGES)
        public_data = _make_public_page_data()

        def handler(request):
            url = str(request.url)
            if "getPublicPageData" in url:
                return httpx.Response(200, json=public_data)
            if "loadPageChunk" in url:
                return httpx.Response(200, json=chunk)
            return httpx.Response(404)

        board_url = f"https://{SUBDOMAIN}.notion.site/Job-Board-{PAGE_ID.replace('-', '')}"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(board_url, client)

        assert result is not None
        assert result["page_id"] == PAGE_ID
        assert result["space_id"] == SPACE_ID
        assert result["jobs"] == 3

    @pytest.mark.asyncio
    async def test_falls_back_to_public_home_page(self):
        """When the URL's page has no children, probe checks publicHomePage."""
        empty_chunk = _make_chunk_response(PAGE_ID, [])
        home_chunk = _make_chunk_response(HOME_PAGE_ID, JOB_PAGES)
        public_data = _make_public_page_data(public_home=HOME_PAGE_ID)

        def handler(request):
            url = str(request.url)
            body = json.loads(request.content)
            if "getPublicPageData" in url:
                return httpx.Response(200, json=public_data)
            if "loadPageChunk" in url:
                req_page = body["page"]["id"]
                if req_page == PAGE_ID:
                    return httpx.Response(200, json=empty_chunk)
                if req_page == HOME_PAGE_ID:
                    return httpx.Response(200, json=home_chunk)
            return httpx.Response(404)

        board_url = f"https://{SUBDOMAIN}.notion.site/careers-{PAGE_ID.replace('-', '')}"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(board_url, client)

        assert result is not None
        assert result["page_id"] == HOME_PAGE_ID
        assert result["jobs"] == 3

    @pytest.mark.asyncio
    async def test_returns_none_for_non_notion(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_child_pages(self):
        empty_chunk = _make_chunk_response(PAGE_ID, [])
        public_data = _make_public_page_data()

        def handler(request):
            url = str(request.url)
            if "getPublicPageData" in url:
                return httpx.Response(200, json=public_data)
            if "loadPageChunk" in url:
                return httpx.Response(200, json=empty_chunk)
            return httpx.Response(404)

        board_url = f"https://{SUBDOMAIN}.notion.site/{PAGE_ID.replace('-', '')}"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(board_url, client)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_api_fails(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(f"https://{SUBDOMAIN}.notion.site/abc", client)
        assert result is None


# ---------------------------------------------------------------------------
# Integration tests — discover
# ---------------------------------------------------------------------------


class TestDiscover:
    @pytest.mark.asyncio
    async def test_returns_job_urls(self):
        chunk = _make_chunk_response(PAGE_ID, JOB_PAGES)

        def handler(request):
            return httpx.Response(200, json=chunk)

        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{PAGE_ID.replace('-', '')}",
            "metadata": {"page_id": PAGE_ID},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert len(urls) == 3
        for job in JOB_PAGES:
            expected = _page_url(SUBDOMAIN, job["id"])
            assert expected in urls

    @pytest.mark.asyncio
    async def test_uses_page_id_from_url_when_not_in_config(self):
        chunk = _make_chunk_response(PAGE_ID, JOB_PAGES)
        requested_ids = []

        def handler(request):
            body = json.loads(request.content)
            requested_ids.append(body["page"]["id"])
            return httpx.Response(200, json=chunk)

        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/Careers-{PAGE_ID.replace('-', '')}",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert len(urls) == 3
        assert requested_ids[0] == PAGE_ID

    @pytest.mark.asyncio
    async def test_config_page_id_takes_precedence(self):
        """page_id in config overrides the one parsed from the URL."""
        chunk = _make_chunk_response(HOME_PAGE_ID, JOB_PAGES)
        requested_ids = []

        def handler(request):
            body = json.loads(request.content)
            requested_ids.append(body["page"]["id"])
            return httpx.Response(200, json=chunk)

        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{PAGE_ID.replace('-', '')}",
            "metadata": {"page_id": HOME_PAGE_ID},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await discover(board, client)

        assert requested_ids[0] == HOME_PAGE_ID

    @pytest.mark.asyncio
    async def test_empty_page_returns_empty_set(self):
        chunk = _make_chunk_response(PAGE_ID, [])

        def handler(request):
            return httpx.Response(200, json=chunk)

        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{PAGE_ID.replace('-', '')}",
            "metadata": {"page_id": PAGE_ID},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert urls == set()

    @pytest.mark.asyncio
    async def test_include_nested_config(self):
        grandchild_id = "fff66666-6666-6666-6666-666666666666"
        child_id = JOB_PAGES[0]["id"]

        chunk = _make_chunk_response(PAGE_ID, JOB_PAGES)
        child_block = chunk["recordMap"]["block"][child_id]["value"]["value"]
        child_block["content"] = [grandchild_id]
        chunk["recordMap"]["block"].update(
            _make_block(grandchild_id, title="Nested Job", parent_id=child_id)
        )

        def handler(request):
            return httpx.Response(200, json=chunk)

        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{PAGE_ID.replace('-', '')}",
            "metadata": {"page_id": PAGE_ID, "include_nested": True},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert len(urls) == 4

    @pytest.mark.asyncio
    async def test_raises_for_non_notion_url(self):
        board = {"board_url": "https://example.com/careers", "metadata": {}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
            with pytest.raises(ValueError, match="Not a Notion site"):
                await discover(board, client)

    @pytest.mark.asyncio
    async def test_raises_when_no_page_id(self):
        board = {"board_url": "https://acme.notion.site/", "metadata": {}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
            with pytest.raises(ValueError, match="Cannot determine page_id"):
                await discover(board, client)
