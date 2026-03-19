"""Tests for the Notion monitor."""

from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitors.notion import (
    _extract_child_pages,
    _extract_title,
    _find_all_collection_views,
    _find_page_by_slug,
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
# These IDs are internal to the mock API — the user never configures them.
_URL_PAGE_ID = "11111111-2222-3333-4444-555555555555"
_HOME_PAGE_ID = "66666666-7777-8888-9999-aaaaaaaaaaaa"

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


def _make_handler(
    *,
    public_data=None,
    chunks: dict | None = None,
    default_chunk=None,
):
    """Build an httpx mock handler for Notion API calls.

    Args:
        public_data: Response for getPublicPageData.
        chunks: Mapping of page_id -> chunk response for loadPageChunk.
        default_chunk: Fallback chunk response for any loadPageChunk call.
    """
    def handler(request):
        url = str(request.url)
        body = json.loads(request.content) if request.content else {}
        if "getPublicPageData" in url:
            return httpx.Response(200, json=public_data or {})
        if "loadPageChunk" in url:
            req_page = body.get("page", {}).get("id", "")
            if chunks and req_page in chunks:
                return httpx.Response(200, json=chunks[req_page])
            if default_chunk:
                return httpx.Response(200, json=default_chunk)
        return httpx.Response(404)
    return handler


# ---------------------------------------------------------------------------
# Unit tests — URL parsing
# ---------------------------------------------------------------------------


class TestParseNotionUrl:
    def test_standard_url_with_slug(self):
        sub, hint = _parse_notion_url(
            "https://acme.notion.site/Job-Board-11111111222233334444555555555555"
        )
        assert sub == "acme"
        assert hint == "11111111-2222-3333-4444-555555555555"

    def test_url_without_title_slug(self):
        sub, hint = _parse_notion_url(
            "https://acme.notion.site/11111111222233334444555555555555"
        )
        assert sub == "acme"
        assert hint == "11111111-2222-3333-4444-555555555555"

    def test_root_url(self):
        sub, hint = _parse_notion_url("https://acme.notion.site/")
        assert sub == "acme"
        assert hint is None

    def test_non_notion_url(self):
        sub, hint = _parse_notion_url("https://example.com/careers")
        assert sub is None
        assert hint is None

    def test_hyphenated_subdomain(self):
        sub, hint = _parse_notion_url(
            "https://my-company.notion.site/aabbccdd11223344aabbccdd11223344"
        )
        assert sub == "my-company"
        assert hint is not None

    def test_slug_without_hex_id(self):
        """A path like /job-posts has no 32 hex chars — returns raw slug."""
        sub, hint = _parse_notion_url("https://acme.notion.site/job-posts")
        assert sub == "acme"
        assert hint == "job-posts"

    def test_slug_with_subpath(self):
        sub, hint = _parse_notion_url("https://acme.notion.site/careers/openings")
        assert sub == "acme"
        assert hint == "openings"


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


class TestFindPageBySlug:
    def test_finds_matching_page(self):
        chunk = _make_chunk_response("root", [
            {"id": "aaa", "type": "page", "title": "Job Posts"},
            {"id": "bbb", "type": "page", "title": "About Us"},
        ])
        assert _find_page_by_slug(chunk, "job-posts") == "aaa"

    def test_returns_none_when_no_match(self):
        chunk = _make_chunk_response("root", [
            {"id": "aaa", "type": "page", "title": "About Us"},
        ])
        assert _find_page_by_slug(chunk, "careers") is None

    def test_matches_collection_view_page(self):
        chunk = _make_chunk_response("root", [])
        chunk["recordMap"]["block"].update(
            _make_block("cvp1", block_type="collection_view_page", title="Job Posts")
        )
        assert _find_page_by_slug(chunk, "job-posts") == "cvp1"


# ---------------------------------------------------------------------------
# Unit tests — child page extraction
# ---------------------------------------------------------------------------


class TestExtractChildPages:
    def test_extracts_child_pages(self):
        data = _make_chunk_response(_URL_PAGE_ID, JOB_PAGES)
        pages = _extract_child_pages(data, _URL_PAGE_ID)
        assert len(pages) == 3
        titles = {p["title"] for p in pages}
        assert titles == {"Software Engineer", "Product Manager", "Data Analyst"}

    def test_skips_non_page_blocks(self):
        children = [
            *JOB_PAGES,
            {"id": "ddd44444-4444-4444-4444-444444444444", "type": "text", "title": "Not a page"},
        ]
        data = _make_chunk_response(_URL_PAGE_ID, children)
        pages = _extract_child_pages(data, _URL_PAGE_ID)
        assert len(pages) == 3

    def test_skips_deleted_pages(self):
        children = [
            *JOB_PAGES,
            {"id": "eee55555-5555-5555-5555-555555555555", "type": "page", "title": "Deleted", "alive": False},
        ]
        data = _make_chunk_response(_URL_PAGE_ID, children)
        pages = _extract_child_pages(data, _URL_PAGE_ID)
        assert len(pages) == 3

    def test_empty_page(self):
        data = _make_chunk_response(_URL_PAGE_ID, [])
        pages = _extract_child_pages(data, _URL_PAGE_ID)
        assert pages == []

    def test_include_nested(self):
        grandchild_id = "fff66666-6666-6666-6666-666666666666"
        child_id = JOB_PAGES[0]["id"]

        data = _make_chunk_response(_URL_PAGE_ID, JOB_PAGES)
        child_block = data["recordMap"]["block"][child_id]["value"]["value"]
        child_block["content"] = [grandchild_id]
        data["recordMap"]["block"].update(
            _make_block(grandchild_id, title="Nested Job", parent_id=child_id)
        )

        pages = _extract_child_pages(data, _URL_PAGE_ID, include_nested=True)
        assert len(pages) == 4
        assert any(p["title"] == "Nested Job" for p in pages)

    def test_nested_excluded_by_default(self):
        grandchild_id = "fff66666-6666-6666-6666-666666666666"
        child_id = JOB_PAGES[0]["id"]

        data = _make_chunk_response(_URL_PAGE_ID, JOB_PAGES)
        child_block = data["recordMap"]["block"][child_id]["value"]["value"]
        child_block["content"] = [grandchild_id]
        data["recordMap"]["block"].update(
            _make_block(grandchild_id, title="Nested Job", parent_id=child_id)
        )

        pages = _extract_child_pages(data, _URL_PAGE_ID, include_nested=False)
        assert len(pages) == 3


class TestFindAllCollectionViews:
    def test_finds_direct_collection_view(self):
        """collection_view as direct child of page."""
        cv_id = "cv111111-1111-1111-1111-111111111111"
        data = _make_chunk_response(_URL_PAGE_ID, [{"id": cv_id, "type": "collection_view"}])
        data["recordMap"]["block"][cv_id] = {"value": {"value": {
            "type": "collection_view",
            "collection_id": "col11111-1111-1111-1111-111111111111",
            "view_ids": ["view1111-1111-1111-1111-111111111111"],
        }}}
        cvs = _find_all_collection_views(data)
        assert len(cvs) == 1
        assert cvs[0]["collection_id"] == "col11111-1111-1111-1111-111111111111"

    def test_finds_deeply_nested_collection_view(self):
        """collection_view inside a column inside the page (e.g. Entalpic)."""
        col_list_id = "collist1-1111-1111-1111-111111111111"
        col_id = "column11-1111-1111-1111-111111111111"
        cv_id = "cv222222-2222-2222-2222-222222222222"
        data = _make_chunk_response(_URL_PAGE_ID, [{"id": col_list_id, "type": "column_list"}])
        data["recordMap"]["block"][col_list_id] = {"value": {"value": {
            "type": "column_list", "content": [col_id],
        }}}
        data["recordMap"]["block"][col_id] = {"value": {"value": {
            "type": "column", "content": [cv_id],
        }}}
        data["recordMap"]["block"][cv_id] = {"value": {"value": {
            "type": "collection_view",
            "collection_id": "col22222-2222-2222-2222-222222222222",
            "view_ids": ["view2222-2222-2222-2222-222222222222"],
        }}}
        cvs = _find_all_collection_views(data)
        assert len(cvs) == 1

    def test_finds_multiple_collection_views(self):
        """Multiple databases on one page (e.g. Mbrella)."""
        data = _make_chunk_response(_URL_PAGE_ID, [])
        for i in range(3):
            cv_id = f"cv{i}00000-0000-0000-0000-000000000000"
            data["recordMap"]["block"][cv_id] = {"value": {"value": {
                "type": "collection_view",
                "collection_id": f"col{i}0000-0000-0000-0000-000000000000",
                "view_ids": [f"view{i}000-0000-0000-0000-000000000000"],
            }}}
        cvs = _find_all_collection_views(data)
        assert len(cvs) == 3


class TestDiscoverWithCollection:
    """Test discovery via queryCollection (database pattern)."""

    @pytest.mark.asyncio
    async def test_discovers_jobs_from_database(self):
        COLL_ID = "col11111-1111-1111-1111-111111111111"
        VIEW_ID = "view1111-1111-1111-1111-111111111111"
        ROW_IDS = ["row11111-1111-1111-1111-111111111111", "row22222-2222-2222-2222-222222222222"]

        # Build chunk with a collection_view block (no child pages)
        chunk = _make_chunk_response(_URL_PAGE_ID, [])
        cv_id = "cv111111-1111-1111-1111-111111111111"
        chunk["recordMap"]["block"][cv_id] = {"value": {"value": {
            "type": "collection_view",
            "collection_id": COLL_ID,
            "view_ids": [VIEW_ID],
        }}}

        query_response = {
            "result": {
                "type": "reducer",
                "reducerResults": {
                    "collection_group_results": {
                        "type": "results",
                        "blockIds": ROW_IDS,
                    }
                },
            },
            "recordMap": {"block": {}},
        }

        def handler(request):
            url = str(request.url)
            if "getPublicPageData" in url:
                return httpx.Response(200, json=_make_public_page_data())
            if "loadPageChunk" in url:
                return httpx.Response(200, json=chunk)
            if "queryCollection" in url:
                return httpx.Response(200, json=query_response)
            return httpx.Response(404)

        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{_URL_PAGE_ID.replace('-', '')}",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert len(urls) == 2
        for rid in ROW_IDS:
            assert _page_url(SUBDOMAIN, rid) in urls

    @pytest.mark.asyncio
    async def test_aggregates_multiple_collections(self):
        """Multiple databases on one page — all rows are aggregated."""
        chunk = _make_chunk_response(_URL_PAGE_ID, [])
        for i in range(2):
            cv_id = f"cv{i}00000-0000-0000-0000-000000000000"
            chunk["recordMap"]["block"][cv_id] = {"value": {"value": {
                "type": "collection_view",
                "collection_id": f"col{i}0000-0000-0000-0000-000000000000",
                "view_ids": [f"view{i}000-0000-0000-0000-000000000000"],
            }}}

        call_count = [0]

        def handler(request):
            url = str(request.url)
            if "getPublicPageData" in url:
                return httpx.Response(200, json=_make_public_page_data())
            if "loadPageChunk" in url:
                return httpx.Response(200, json=chunk)
            if "queryCollection" in url:
                call_count[0] += 1
                rows = [f"row{call_count[0]}a000-0000-0000-0000-000000000000",
                        f"row{call_count[0]}b000-0000-0000-0000-000000000000"]
                return httpx.Response(200, json={
                    "result": {"type": "reducer", "reducerResults": {
                        "collection_group_results": {"type": "results", "blockIds": rows}}},
                    "recordMap": {"block": {}},
                })
            return httpx.Response(404)

        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{_URL_PAGE_ID.replace('-', '')}",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert len(urls) == 4  # 2 rows from each of 2 collections


# ---------------------------------------------------------------------------
# Integration tests — can_handle probe
# ---------------------------------------------------------------------------


class TestCanHandle:
    @pytest.mark.asyncio
    async def test_detects_notion_site_with_jobs(self):
        """Board URL with page ID that directly has job sub-pages."""
        handler = _make_handler(
            public_data=_make_public_page_data(),
            default_chunk=_make_chunk_response(_URL_PAGE_ID, JOB_PAGES),
        )
        board_url = f"https://{SUBDOMAIN}.notion.site/Careers-{_URL_PAGE_ID.replace('-', '')}"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(board_url, client)

        assert result is not None
        assert result["jobs"] == 3

    @pytest.mark.asyncio
    async def test_falls_back_to_public_home_page(self):
        """URL page has no children — probe finds them via publicHomePage."""
        handler = _make_handler(
            public_data=_make_public_page_data(public_home=_HOME_PAGE_ID),
            chunks={
                _URL_PAGE_ID: _make_chunk_response(_URL_PAGE_ID, []),
                _HOME_PAGE_ID: _make_chunk_response(_HOME_PAGE_ID, JOB_PAGES),
            },
        )
        board_url = f"https://{SUBDOMAIN}.notion.site/careers-{_URL_PAGE_ID.replace('-', '')}"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(board_url, client)

        assert result is not None
        assert result["jobs"] == 3

    @pytest.mark.asyncio
    async def test_slug_url_resolves_via_home_page(self):
        """A URL like /job-posts (no hex ID) resolves through publicHomePage."""
        handler = _make_handler(
            public_data=_make_public_page_data(public_home=_HOME_PAGE_ID),
            default_chunk=_make_chunk_response(_HOME_PAGE_ID, JOB_PAGES),
        )
        board_url = f"https://{SUBDOMAIN}.notion.site/job-posts"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(board_url, client)

        assert result is not None
        assert result["jobs"] == 3

    @pytest.mark.asyncio
    async def test_returns_none_for_non_notion(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_child_pages(self):
        handler = _make_handler(
            public_data=_make_public_page_data(),
            default_chunk=_make_chunk_response(_URL_PAGE_ID, []),
        )
        board_url = f"https://{SUBDOMAIN}.notion.site/{_URL_PAGE_ID.replace('-', '')}"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(board_url, client)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_api_fails(self):
        handler = lambda r: httpx.Response(500)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(f"https://{SUBDOMAIN}.notion.site/abc", client)
        assert result is None


# ---------------------------------------------------------------------------
# Integration tests — discover
# ---------------------------------------------------------------------------


class TestDiscover:
    @pytest.mark.asyncio
    async def test_returns_job_urls_from_url_page(self):
        """Jobs found directly under the URL's page."""
        handler = _make_handler(
            public_data=_make_public_page_data(),
            default_chunk=_make_chunk_response(_URL_PAGE_ID, JOB_PAGES),
        )
        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{_URL_PAGE_ID.replace('-', '')}",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert len(urls) == 3
        for job in JOB_PAGES:
            expected = _page_url(SUBDOMAIN, job["id"])
            assert expected in urls

    @pytest.mark.asyncio
    async def test_falls_back_to_home_page(self):
        """URL page is empty — discovers jobs via publicHomePage."""
        handler = _make_handler(
            public_data=_make_public_page_data(public_home=_HOME_PAGE_ID),
            chunks={
                _URL_PAGE_ID: _make_chunk_response(_URL_PAGE_ID, []),
                _HOME_PAGE_ID: _make_chunk_response(_HOME_PAGE_ID, JOB_PAGES),
            },
        )
        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{_URL_PAGE_ID.replace('-', '')}",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert len(urls) == 3

    @pytest.mark.asyncio
    async def test_slug_url_discovers_jobs(self):
        """A slug URL like /job-posts resolves and finds jobs."""
        handler = _make_handler(
            public_data=_make_public_page_data(public_home=_HOME_PAGE_ID),
            default_chunk=_make_chunk_response(_HOME_PAGE_ID, JOB_PAGES),
        )
        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/job-posts",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert len(urls) == 3

    @pytest.mark.asyncio
    async def test_empty_page_returns_empty_set(self):
        handler = _make_handler(
            public_data=_make_public_page_data(),
            default_chunk=_make_chunk_response(_URL_PAGE_ID, []),
        )
        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{_URL_PAGE_ID.replace('-', '')}",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert urls == set()

    @pytest.mark.asyncio
    async def test_include_nested_config(self):
        grandchild_id = "fff66666-6666-6666-6666-666666666666"
        child_id = JOB_PAGES[0]["id"]

        chunk = _make_chunk_response(_URL_PAGE_ID, JOB_PAGES)
        child_block = chunk["recordMap"]["block"][child_id]["value"]["value"]
        child_block["content"] = [grandchild_id]
        chunk["recordMap"]["block"].update(
            _make_block(grandchild_id, title="Nested Job", parent_id=child_id)
        )

        handler = _make_handler(
            public_data=_make_public_page_data(),
            default_chunk=chunk,
        )
        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/{_URL_PAGE_ID.replace('-', '')}",
            "metadata": {"include_nested": True},
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
    async def test_no_config_needed(self):
        """The only required input is the board URL — no metadata config."""
        handler = _make_handler(
            public_data=_make_public_page_data(public_home=_HOME_PAGE_ID),
            default_chunk=_make_chunk_response(_HOME_PAGE_ID, JOB_PAGES),
        )
        board = {
            "board_url": f"https://{SUBDOMAIN}.notion.site/job-posts",
            # No metadata at all
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(board, client)

        assert len(urls) == 3
