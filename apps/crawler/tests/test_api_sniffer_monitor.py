"""Tests for the api_sniffer monitor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.monitors.api_sniffer import (
    _discover_live_url,
    _extract_rich,
    _extract_urls_from_template,
    discover,
)


class TestExtractRich:
    def test_basic_fields(self):
        items = [
            {"title": "Dev", "bodyHtml": "<p>Description</p>", "url": "/jobs/1", "location": "NYC"},
            {"title": "PM", "bodyHtml": "<p>PM desc</p>", "url": "/jobs/2", "location": "SF"},
        ]
        fields = {"title": "title", "description": "bodyHtml", "locations": "location"}
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert len(jobs) == 2
        assert jobs[0].title == "Dev"
        assert jobs[0].description == "<p>Description</p>"
        assert jobs[0].locations == ["NYC"]
        assert jobs[0].url == "https://example.com/jobs/1"

    def test_url_template(self):
        items = [
            {"title": "Dev", "id": "123", "slug": "developer"},
        ]
        fields = {"title": "title"}
        jobs = _extract_rich(
            items,
            fields,
            None,
            "https://example.com/jobs/{id}/{slug}",
            "https://example.com",
        )
        assert len(jobs) == 1
        assert jobs[0].url == "https://example.com/jobs/123/developer"

    def test_metadata_fields(self):
        items = [{"title": "Dev", "url": "/jobs/1", "department": "Eng"}]
        fields = {"title": "title", "metadata.team": "department"}
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert jobs[0].metadata == {"team": "Eng"}

    def test_array_locations(self):
        items = [
            {
                "title": "Dev",
                "url": "/jobs/1",
                "offices": [{"name": "NYC"}, {"name": "SF"}],
            }
        ]
        fields = {"title": "title", "locations": "offices[].name"}
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert jobs[0].locations == ["NYC", "SF"]

    def test_no_url_skipped(self):
        items = [{"title": "Dev", "score": 5}]
        fields = {"title": "title"}
        jobs = _extract_rich(items, fields, None, None, "https://example.com")
        assert len(jobs) == 0


class TestExtractUrlsFromTemplate:
    def test_basic(self):
        items = [
            {"id": "1", "slug": "dev"},
            {"id": "2", "slug": "pm"},
        ]
        urls = _extract_urls_from_template(
            items,
            "https://example.com/jobs/{id}/{slug}",
            "https://example.com",
        )
        assert "https://example.com/jobs/1/dev" in urls
        assert "https://example.com/jobs/2/pm" in urls

    def test_missing_key(self):
        items = [{"id": "1"}]
        urls = _extract_urls_from_template(
            items,
            "https://example.com/jobs/{id}/{slug}",
            "https://example.com",
        )
        assert len(urls) == 0  # KeyError → skipped


def _make_mock_pw(mock_page):
    """Create a mock Playwright instance that yields the given page."""
    mock_pw = MagicMock()
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
    return mock_pw


class TestDiscoverReplay:
    """Test discover() in replay mode (api_url in config)."""

    @pytest.mark.asyncio
    async def test_replay_rich_mode(self):
        """When fields are in config, discover should return list[DiscoveredJob]."""
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML"},
            {"title": "PM", "url": "/jobs/2", "desc": "More HTML"},
            {"title": "QA", "url": "/jobs/3", "desc": "QA HTML"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title", "description": "desc"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=json.dumps(api_response))
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"

    @pytest.mark.asyncio
    async def test_replay_url_only_mode(self):
        """When no fields in config, discover should return set[str]."""
        items = [
            {"title": "Dev", "url": "https://example.com/jobs/1"},
            {"title": "PM", "url": "https://example.com/jobs/2"},
            {"title": "QA", "url": "https://example.com/jobs/3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=json.dumps(api_response))
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, set)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_no_playwright_returns_empty(self):
        """Without pw and no api_url, discover should return empty set."""
        board = {"board_url": "https://example.com/careers", "metadata": {}}
        http = AsyncMock()
        result = await discover(board, http, pw=None)
        assert isinstance(result, set)
        assert len(result) == 0


class TestHTTPFallback:
    """Test HTTP fallback behavior when Playwright fails."""

    @pytest.mark.asyncio
    async def test_replay_http_fallback_on_playwright_failure(self):
        """When browser fetch fails, falls back to httpx and returns data."""
        items = [
            {"title": "Dev", "url": "https://example.com/jobs/1"},
            {"title": "PM", "url": "https://example.com/jobs/2"},
            {"title": "QA", "url": "https://example.com/jobs/3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        # Playwright fetch fails
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Browser crashed"))
        mock_pw = _make_mock_pw(mock_page)

        # httpx succeeds — response methods are sync (not awaited)
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"

    @pytest.mark.asyncio
    async def test_browser_true_no_playwright_falls_back_to_http(self):
        """With browser: true but pw=None, falls back to _discover_http."""
        items = [
            {"title": "Dev", "url": "https://example.com/jobs/1"},
            {"title": "PM", "url": "https://example.com/jobs/2"},
        ]
        api_response = {"results": items, "total": 2}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        # httpx response — json() and raise_for_status() are sync
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        # pw=None — should fall back to HTTP instead of returning empty
        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0].title == "Dev"

    @pytest.mark.asyncio
    async def test_replay_both_fail_returns_empty(self):
        """When both browser and HTTP fallback fail, returns empty."""
        config = {
            "api_url": "https://example.com/api/jobs",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Browser crashed"))
        mock_pw = _make_mock_pw(mock_page)

        # httpx also fails
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP failed too")

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 0


def _make_mock_response(url, data=None):
    """Create a mock Playwright Response with url and json()."""
    resp = MagicMock()
    resp.url = url
    if data is not None:
        resp.json = AsyncMock(return_value=data)
    else:
        resp.json = AsyncMock(side_effect=Exception("no body"))
    return resp


class TestLiveUrlDiscovery:
    """Test api_url_match dynamic URL discovery."""

    @pytest.mark.asyncio
    async def test_live_url_replaces_stale_token(self):
        """When a response matches api_url_match with a new token, api_url is updated."""
        stored_url = (
            "https://gateway.example.com/apigw-OLD_TOKEN/v1/api/jobs/search"
            "?pageSize=100&start=1&lang=en"
        )
        live_response_url = (
            "https://gateway.example.com/apigw-NEW_TOKEN/v1/api/jobs/search?pageSize=100&start=1"
        )
        api_url_match = "gateway.example.com/*/v1/api/jobs/search"

        mock_page = AsyncMock()
        captured_handler = None

        def capture_on(event, handler):
            nonlocal captured_handler
            if event == "response":
                captured_handler = handler

        mock_page.on = capture_on

        async def fake_navigate(*args, **kwargs):
            if captured_handler:
                captured_handler(_make_mock_response(live_response_url, {"jobs": []}))

        mock_page.goto = fake_navigate

        url, data = await _discover_live_url(
            mock_page,
            board_url="https://www.example.com/careers",
            api_url=stored_url,
            api_url_match=api_url_match,
            wait="load",
            timeout=20000,
            settle=0,
        )

        assert "apigw-NEW_TOKEN" in url
        assert "apigw-OLD_TOKEN" not in url
        assert "pageSize=100" in url
        assert "lang=en" in url
        assert data == {"jobs": []}

    @pytest.mark.asyncio
    async def test_no_match_keeps_stored_url(self):
        """When no response matches api_url_match, original api_url is returned."""
        stored_url = "https://gateway.example.com/apigw-TOKEN/v1/api/jobs/search?page=1"
        api_url_match = "gateway.example.com/*/v1/api/jobs/search"

        mock_page = AsyncMock()
        mock_page.on = lambda event, handler: None

        url, data = await _discover_live_url(
            mock_page,
            board_url="https://www.example.com/careers",
            api_url=stored_url,
            api_url_match=api_url_match,
            wait="load",
            timeout=20000,
            settle=0,
        )

        assert url == stored_url
        assert data is None

    @pytest.mark.asyncio
    async def test_same_token_keeps_stored_url(self):
        """When live URL has the same base as stored, URL unchanged but data captured."""
        stored_url = "https://gateway.example.com/apigw-SAME/v1/api/jobs/search?page=1&lang=en"
        live_response_url = "https://gateway.example.com/apigw-SAME/v1/api/jobs/search?page=1"
        api_url_match = "gateway.example.com/*/v1/api/jobs/search"

        mock_page = AsyncMock()
        captured_handler = None

        def capture_on(event, handler):
            nonlocal captured_handler
            if event == "response":
                captured_handler = handler

        mock_page.on = capture_on

        async def fake_navigate(*args, **kwargs):
            if captured_handler:
                captured_handler(_make_mock_response(live_response_url, {"jobs": []}))

        mock_page.goto = fake_navigate

        url, data = await _discover_live_url(
            mock_page,
            board_url="https://www.example.com/careers",
            api_url=stored_url,
            api_url_match=api_url_match,
            wait="load",
            timeout=20000,
            settle=0,
        )

        assert url == stored_url
        assert data == {"jobs": []}

    @pytest.mark.asyncio
    async def test_navigation_failure_keeps_stored_url(self):
        """When navigation raises, stored api_url is returned (no crash)."""
        stored_url = "https://gateway.example.com/apigw-TOKEN/v1/api/jobs/search?page=1"
        api_url_match = "gateway.example.com/*/v1/api/jobs/search"

        mock_page = AsyncMock()
        mock_page.on = lambda event, handler: None
        mock_page.goto = AsyncMock(side_effect=Exception("Akamai blocked"))

        url, data = await _discover_live_url(
            mock_page,
            board_url="https://www.example.com/careers",
            api_url=stored_url,
            api_url_match=api_url_match,
            wait="load",
            timeout=20000,
            settle=0,
        )

        assert url == stored_url
        assert data is None

    @pytest.mark.asyncio
    async def test_end_to_end_replay_with_api_url_match_and_route_params(self):
        """With route_params, navigates upfront and captures response directly."""
        items = [
            {"title": "Consultant", "url": "/jobs/1"},
            {"title": "Associate", "url": "/jobs/2"},
            {"title": "Analyst", "url": "/jobs/3"},
        ]
        api_response = {"docs": items, "total": 3}

        stored_url = "https://gateway.example.com/apigw-OLD/v1/api/jobs/search"
        live_url = "https://gateway.example.com/apigw-NEW/v1/api/jobs/search?pageSize=1000"

        config = {
            "api_url": stored_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "browser": True,
            "api_url_match": "gateway.example.com/*/v1/api/jobs/search",
            "route_params": {"pageSize": "1000"},
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handler = None

        def capture_on(event, handler):
            nonlocal captured_handler
            if event == "response":
                captured_handler = handler

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            if captured_handler:
                captured_handler(_make_mock_response(live_url, api_response))

        mock_page.goto = fake_goto
        mock_page.route = AsyncMock()

        mock_pw = _make_mock_pw(mock_page)
        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Consultant"

        # No evaluate call — data came from captured response
        mock_page.evaluate.assert_not_called()
        # page.route was called to set up param overrides
        mock_page.route.assert_called_once()

    @pytest.mark.asyncio
    async def test_end_to_end_replay_api_url_match_no_route_params(self):
        """Without route_params, tries replay first, falls back to live discovery."""
        items = [
            {"title": "Consultant", "url": "/jobs/1"},
            {"title": "Associate", "url": "/jobs/2"},
            {"title": "Analyst", "url": "/jobs/3"},
        ]
        api_response = {"docs": items, "total": 3}

        stored_url = "https://gateway.example.com/apigw-OLD/v1/api/jobs/search"
        live_url = "https://gateway.example.com/apigw-NEW/v1/api/jobs/search?pageSize=100"

        config = {
            "api_url": stored_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "browser": True,
            "api_url_match": "gateway.example.com/*/v1/api/jobs/search",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        # Replay with stale URL fails
        mock_page.evaluate = AsyncMock(side_effect=Exception("stale token"))

        goto_count = 0

        async def fake_goto(*args, **kwargs):
            nonlocal goto_count
            goto_count += 1
            # Second navigation (retry) triggers the response handler
            if goto_count >= 2 and captured_handlers:
                captured_handlers[-1](_make_mock_response(live_url, api_response))

        mock_page.goto = fake_goto

        mock_pw = _make_mock_pw(mock_page)
        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Consultant"

        # Evaluate was called (replay attempt with stale URL)
        assert mock_page.evaluate.call_count >= 1
        # Two navigations: initial (cookies) + retry (live discovery)
        assert goto_count >= 2

    @pytest.mark.asyncio
    async def test_route_params_modifies_outgoing_request(self):
        """route_params sets up page.route() to modify the page's own API request."""
        items = [{"title": f"Job{i}", "url": f"/jobs/{i}"} for i in range(100)]
        api_response = {"docs": items, "numFound": 100}

        config = {
            "api_url": "https://gateway.example.com/apigw-TOKEN/v1/api/jobs/search",
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "browser": True,
            "api_url_match": "gateway.example.com/*/v1/api/jobs/search",
            "route_params": {"pageSize": "1000"},
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handler = None
        routed_pattern = None

        def capture_on(event, handler):
            nonlocal captured_handler
            if event == "response":
                captured_handler = handler

        mock_page.on = capture_on

        async def fake_route(pattern, handler):
            nonlocal routed_pattern
            routed_pattern = pattern

        mock_page.route = fake_route

        async def fake_goto(*args, **kwargs):
            if captured_handler:
                captured_handler(
                    _make_mock_response(
                        "https://gateway.example.com/apigw-TOKEN/v1/api/jobs/search?pageSize=1000",
                        api_response,
                    )
                )

        mock_page.goto = fake_goto
        mock_pw = _make_mock_pw(mock_page)

        result = await discover(board, AsyncMock(), pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 100
        # page.route was called with a matching pattern
        assert routed_pattern is not None
        assert "v1/api/jobs/search" in routed_pattern


class TestRetryWithApiUrlMatch:
    """Test that fetch failure + api_url_match triggers live URL re-discovery."""

    @pytest.mark.asyncio
    async def test_upfront_discovery_misses_then_retry_succeeds(self):
        """When upfront _discover_live_url finds nothing (API hadn't fired yet),
        then the stale fetch fails, the retry re-navigates and captures the response."""
        items = [
            {"title": "Dev", "url": "/jobs/1"},
            {"title": "PM", "url": "/jobs/2"},
            {"title": "QA", "url": "/jobs/3"},
        ]
        api_response = {"docs": items}

        stale_url = "https://gateway.example.com/apigw-OLD/v1/api/jobs/search"
        live_url = "https://gateway.example.com/apigw-NEW/v1/api/jobs/search?p=1"

        config = {
            "api_url": stale_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "browser": True,
            "api_url_match": "gateway.example.com/*/v1/api/jobs/search",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        goto_count = 0
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            nonlocal goto_count
            goto_count += 1
            # First navigation: API hasn't fired yet (slow JS) — no match
            # Second navigation (retry): API fires, handler captures response
            if goto_count >= 2 and captured_handlers:
                captured_handlers[-1](_make_mock_response(live_url, api_response))

        mock_page.goto = fake_goto

        # Browser fetch with stale URL fails
        mock_page.evaluate = AsyncMock(side_effect=Exception("404 Not Found"))

        mock_pw = _make_mock_pw(mock_page)
        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"

        # Two navigations: upfront miss + retry
        assert goto_count >= 2

    @pytest.mark.asyncio
    async def test_no_api_url_match_skips_rediscovery(self):
        """Without api_url_match, fetch failure goes straight to HTTP fallback."""
        config = {
            "api_url": "https://api.example.com/v1/jobs",
            "method": "GET",
            "json_path": "docs",
            "browser": True,
            # No api_url_match — no re-discovery possible
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("API down"))

        mock_pw = _make_mock_pw(mock_page)

        # HTTP fallback also fails
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP failed")
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 0


class TestHttpModeUrlMatchFallback:
    """Test that _discover_http retries with live URL when api_url_match is set."""

    @pytest.mark.asyncio
    async def test_http_stale_url_uses_captured_response(self):
        """HTTP fetch fails → browser captures live response → uses it directly."""
        stale_url = "https://gateway.example.com/apigw-x0old0token/v1/api/jobs"
        live_url = "https://gateway.example.com/apigw-x0new0token/v1/api/jobs?page=1"
        items = [
            {"title": "Dev", "url": "/jobs/1"},
            {"title": "PM", "url": "/jobs/2"},
            {"title": "QA", "url": "/jobs/3"},
        ]
        api_response = {"docs": items}

        config = {
            "api_url": stale_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "api_url_match": "gateway.example.com/*/v1/api/jobs",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            if captured_handlers:
                captured_handlers[-1](_make_mock_response(live_url, api_response))

        mock_page.goto = fake_goto
        mock_pw = _make_mock_pw(mock_page)

        # httpx: stale URL fails
        call_count = 0

        async def http_side_effect(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status.side_effect = Exception("403 Forbidden")
            return resp

        http = AsyncMock()
        http.request = AsyncMock(side_effect=http_side_effect)

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"
        # Only one HTTP call (the initial failure); data came from captured response
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_http_stale_url_retries_when_body_capture_fails(self):
        """HTTP fails → browser finds new URL but body read fails → HTTP retry with new URL."""
        stale_url = "https://gateway.example.com/apigw-x0old0token/v1/api/jobs"
        live_url = "https://gateway.example.com/apigw-x0new0token/v1/api/jobs?page=1"
        items = [
            {"title": "Dev", "url": "/jobs/1"},
            {"title": "PM", "url": "/jobs/2"},
            {"title": "QA", "url": "/jobs/3"},
        ]
        api_response = {"docs": items}

        config = {
            "api_url": stale_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "api_url_match": "gateway.example.com/*/v1/api/jobs",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            if captured_handlers:
                # Response matched but body read will fail
                captured_handlers[-1](
                    _make_mock_response(live_url, None)  # json() raises
                )

        mock_page.goto = fake_goto
        mock_pw = _make_mock_pw(mock_page)

        call_count = 0

        async def http_side_effect(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if "x0old0token" in url:
                resp.raise_for_status.side_effect = Exception("403 Forbidden")
                return resp
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value=api_response)
            return resp

        http = AsyncMock()
        http.request = AsyncMock(side_effect=http_side_effect)

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"
        # Two HTTP calls: stale (fail) + new URL (success)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_http_no_api_url_match_returns_empty(self):
        """Without api_url_match, HTTP failure returns empty immediately."""
        config = {
            "api_url": "https://api.example.com/v1/jobs",
            "method": "GET",
            "json_path": "docs",
            "fields": {"title": "title"},
            # No api_url_match
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("403 Forbidden")
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        mock_pw = _make_mock_pw(AsyncMock())

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_http_url_match_no_playwright_returns_empty(self):
        """With api_url_match but no Playwright, HTTP failure returns empty."""
        config = {
            "api_url": "https://gateway.example.com/apigw-x0old0token/v1/api/jobs",
            "method": "GET",
            "json_path": "docs",
            "fields": {"title": "title"},
            "api_url_match": "gateway.example.com/*/v1/api/jobs",
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("403 Forbidden")
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_http_url_match_same_url_uses_captured_data(self):
        """When URL didn't rotate but response was captured, uses captured data."""
        url = "https://gateway.example.com/apigw-x0same0token/v1/api/jobs"
        items = [
            {"title": "Dev", "url": "/jobs/1"},
            {"title": "PM", "url": "/jobs/2"},
            {"title": "QA", "url": "/jobs/3"},
        ]
        api_response = {"docs": items}

        config = {
            "api_url": url,
            "method": "GET",
            "json_path": "docs",
            "fields": {"title": "title"},
            "api_url_match": "gateway.example.com/*/v1/api/jobs",
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            if captured_handlers:
                captured_handlers[-1](_make_mock_response(url, api_response))

        mock_page.goto = fake_goto
        mock_pw = _make_mock_pw(mock_page)

        call_count = 0

        async def http_side_effect(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status.side_effect = Exception("403 Forbidden")
            return resp

        http = AsyncMock()
        http.request = AsyncMock(side_effect=http_side_effect)

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"
        # Only one HTTP call (the initial failure); data from captured response
        assert call_count == 1
