"""Tests for the api_sniffer monitor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.monitors.api_sniffer import (
    ApiSnifferFallbackError,
    _discover_live_url,
    _extract_rich,
    _extract_urls_from_template,
    discover,
)


def _http_status_error_resp(status: int) -> MagicMock:
    """Build a mock httpx.Response whose ``raise_for_status()`` raises a real
    :class:`httpx.HTTPStatusError`. The api_sniffer retry classifier
    (#2733) reads ``exc.response.status_code`` to decide retryable vs.
    fail-fast, so generic ``Exception("403 Forbidden")`` no longer
    suffices — it would be caught as a transient and burn the full
    retry budget. Use this helper for tests simulating an HTTP error.
    """
    resp = MagicMock()
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(status, request=request)
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"HTTP {status}", request=request, response=response
    )
    return resp


@pytest.fixture(autouse=True)
def _zero_settle():
    """Eliminate 3-second settle sleeps in tests."""
    with patch("src.core.monitors.api_sniffer._DEFAULT_SETTLE", 0):
        yield


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

    def test_bad_url_template_falls_back_to_url_field(self):
        items = [
            {"title": "Dev", "id": "123", "url": "/jobs/123"},
        ]
        fields = {"title": "title"}
        jobs = _extract_rich(
            items,
            fields,
            "url",
            "https://example.com/jobs/{id}/{missing}",
            "https://example.com",
        )
        assert len(jobs) == 1
        assert jobs[0].url == "https://example.com/jobs/123"

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

    def test_multi_field_concat(self):
        items = [
            {
                "title": "Engineer",
                "url": "/jobs/1",
                "intro": "Join our team.",
                "tasks": "<ul><li>Build things</li></ul>",
                "reqs": "<ul><li>5 years exp</li></ul>",
            },
        ]
        fields = {
            "title": "title",
            "description": ["intro", "tasks", "reqs"],
        }
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert len(jobs) == 1
        assert jobs[0].description == (
            "Join our team.\n\n<ul><li>Build things</li></ul>\n\n<ul><li>5 years exp</li></ul>"
        )

    def test_multi_field_concat_partial(self):
        """Missing fields are skipped, present ones still concatenated."""
        items = [
            {
                "title": "PM",
                "url": "/jobs/2",
                "tasks": "Manage projects",
            },
        ]
        fields = {
            "title": "title",
            "description": ["intro", "tasks", "reqs"],
        }
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert len(jobs) == 1
        assert jobs[0].description == "Manage projects"

    def test_multi_field_concat_all_missing(self):
        """When all paths in a list resolve to None, the field is absent."""
        items = [{"title": "QA", "url": "/jobs/3"}]
        fields = {
            "title": "title",
            "description": ["intro", "tasks"],
        }
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert len(jobs) == 1
        assert jobs[0].description is None


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
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
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
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
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


class TestJsonPathValues:
    """Tests for the ``json_path_values`` flag that coerces a dict-of-items
    at ``json_path`` to ``list(dict.values())``.

    This supports APIs like TalentClue that return
    ``{"jobs": {"<id>": {...}, ...}}`` instead of an array.
    """

    @pytest.mark.asyncio
    async def test_http_dict_values_mode_yields_items(self):
        """POST returns {"jobs": {"1": {...}, "2": {...}}}.

        With ``json_path_values: true, json_path: "jobs"``, both items
        should surface.
        """
        api_response = {
            "jobs": {
                "101": {"title": "Dev", "url": "/jobs/101"},
                "102": {"title": "PM", "url": "/jobs/102"},
            }
        }

        config = {
            "api_url": "https://api.example.com/jobs",
            "method": "POST",
            "headers": {"Accept": "application/json"},
            "json_path": "jobs",
            "json_path_values": True,
            "url_field": "url",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=api_response)
        http.request = AsyncMock(return_value=resp)

        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 2
        titles = sorted(job.title for job in result)
        assert titles == ["Dev", "PM"]

    @pytest.mark.asyncio
    async def test_http_json_path_values_no_op_on_non_dict(self):
        """When resolved content is not a dict, ``json_path_values`` is a no-op."""
        api_response = {
            "jobs": [
                {"title": "Dev", "url": "/jobs/1"},
                {"title": "PM", "url": "/jobs/2"},
            ]
        }

        config = {
            "api_url": "https://api.example.com/jobs",
            "method": "GET",
            "json_path": "jobs",
            "json_path_values": True,  # flag present but content is already a list
            "url_field": "url",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=api_response)
        http.request = AsyncMock(return_value=resp)

        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_http_without_flag_dict_response_yields_nothing(self):
        """Without the flag, a dict at ``json_path`` is not an item list —
        preserves existing behavior (no items surfaced, empty result).
        """
        api_response = {
            "jobs": {
                "101": {"title": "Dev", "url": "/jobs/101"},
                "102": {"title": "PM", "url": "/jobs/102"},
            }
        }

        config = {
            "api_url": "https://api.example.com/jobs",
            "method": "POST",
            "json_path": "jobs",
            # no json_path_values
            "url_field": "url",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=api_response)
        http.request = AsyncMock(return_value=resp)

        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_replay_dict_values_mode_yields_items(self):
        """Replay path: in-browser fetch returns dict-of-items; flag coerces
        to values list and items surface as DiscoveredJob."""
        api_response = {
            "jobs": {
                "101": {"title": "Dev", "url": "/jobs/101"},
                "102": {"title": "PM", "url": "/jobs/102"},
                "103": {"title": "QA", "url": "/jobs/103"},
            }
        }

        config = {
            "api_url": "https://api.example.com/jobs/{CLIENT_ID}/{BASE64}",
            "method": "POST",
            "json_path": "jobs",
            "json_path_values": True,
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        titles = sorted(job.title for job in result)
        assert titles == ["Dev", "PM", "QA"]


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
    async def test_replay_both_fail_raises(self):
        """When both browser and HTTP fallback fail, raises ApiSnifferFallbackError.

        The raised exception propagates up to the board processor, which records
        a failure (incrementing ``consecutive_failures``) so the auto-disable at
        5 kicks in for persistently-broken boards.  Previously this returned an
        empty list, causing the counter to bounce between success and empty and
        never trip the disable threshold.
        """
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

        with pytest.raises(ApiSnifferFallbackError) as exc_info:
            await discover(board, http, pw=mock_pw)
        assert exc_info.value.api_url == "https://example.com/api/jobs"
        assert exc_info.value.board_url == "https://example.com/careers"
        # Chained from the underlying httpx failure
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_replay_both_fail_logs_at_warning_not_error(self):
        """http_fallback_failed must be logged at WARNING, not ERROR.

        These events are expected ends-of-fallback-chain; logging them at ERROR
        muddies the error budget.  The exception raised propagates the failure
        through the normal board-failure pipeline instead.
        """
        config = {
            "api_url": "https://example.com/api/jobs",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Browser crashed"))
        mock_pw = _make_mock_pw(mock_page)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP failed too")
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        # Patch the module-level structlog BoundLogger to intercept calls.
        with (
            patch("src.core.monitors.api_sniffer.log") as mock_log,
            pytest.raises(ApiSnifferFallbackError),
        ):
            await discover(board, http, pw=mock_pw)

        warning_events = [c.args[0] for c in mock_log.warning.call_args_list]
        error_events = [c.args[0] for c in mock_log.error.call_args_list]

        assert "api_sniffer.http_fallback_failed" in warning_events, (
            f"expected http_fallback_failed at WARNING, got warnings={warning_events}"
        )
        assert "api_sniffer.http_fallback_failed" not in error_events, (
            f"http_fallback_failed must not be logged at ERROR, got errors={error_events}"
        )

    @pytest.mark.asyncio
    async def test_replay_fallback_raise_chains_original_exception(self):
        """The raised ApiSnifferFallbackError preserves the underlying cause.

        Operators reading the failure in Loki need to know which HTTP status
        or network error caused the fallback to exhaust; the chain carries
        that context via ``__cause__``.
        """
        config = {
            "api_url": "https://us.api.csod.com/rec-job-search/external/jobs",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {
            "board_url": "https://bradesco.csod.com/ux/ats/careersite/1/home",
            "metadata": config,
        }

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Browser crashed"))
        mock_pw = _make_mock_pw(mock_page)

        original_error = RuntimeError("401 Unauthorized")
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = original_error
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        with pytest.raises(ApiSnifferFallbackError) as exc_info:
            await discover(board, http, pw=mock_pw)
        assert exc_info.value.__cause__ is original_error
        assert "401 Unauthorized" in str(exc_info.value)


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
        """Without api_url_match, fetch failure goes straight to HTTP fallback.

        When the HTTP fallback also fails, ApiSnifferFallbackError is raised so
        the board processor advances the consecutive-failure counter rather
        than recording an empty check (which would reset it).
        """
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

        with pytest.raises(ApiSnifferFallbackError):
            await discover(board, http, pw=mock_pw)


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
            return _http_status_error_resp(403)

        http = AsyncMock()
        http.request = AsyncMock(side_effect=http_side_effect)

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"
        # Only one HTTP call (the initial 403 — non-retryable, no retry);
        # data came from the captured browser response.
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
            if "x0old0token" in url:
                return _http_status_error_resp(403)
            resp = MagicMock()
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

        http = AsyncMock()
        http.request = AsyncMock(return_value=_http_status_error_resp(403))

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

        http = AsyncMock()
        http.request = AsyncMock(return_value=_http_status_error_resp(403))

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
            return _http_status_error_resp(403)

        http = AsyncMock()
        http.request = AsyncMock(side_effect=http_side_effect)

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"
        # Only one HTTP call (the initial 403 — non-retryable); data from
        # captured browser response.
        assert call_count == 1


# ---------------------------------------------------------------------------
# Pagination retry semantics (#2733)
# ---------------------------------------------------------------------------


class TestHttpFetchWithRetry:
    """``http_fetch_with_retry`` mirrors ``fetch_with_retry``'s contract on
    api_sniffer's httpx surface: retryable statuses (5xx, 408/425/429)
    retry-then-raise, 404/410 return None (legitimate end-of-pagination),
    other non-retryable 4xx return None with a warning, and arbitrary
    network exceptions retry-then-raise. Pinned for #2733.
    """

    @pytest.mark.asyncio
    async def test_returns_json_on_200(self):
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"items": []})
        client = AsyncMock()
        client.request = AsyncMock(return_value=resp)
        out = await http_fetch_with_retry(client, "GET", "https://x/api")
        assert out == {"items": []}
        assert client.request.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self):
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        client = AsyncMock()
        client.request = AsyncMock(return_value=_http_status_error_resp(404))
        out = await http_fetch_with_retry(client, "GET", "https://x/api")
        assert out is None
        assert client.request.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_403(self):
        """Other non-retryable 4xx — lenient stop with a warning."""
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        client = AsyncMock()
        client.request = AsyncMock(return_value=_http_status_error_resp(403))
        out = await http_fetch_with_retry(client, "GET", "https://x/api")
        assert out is None
        assert client.request.await_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json = MagicMock(return_value={"items": [1]})

        client = AsyncMock()
        client.request = AsyncMock(
            side_effect=[
                _http_status_error_resp(503),
                _http_status_error_resp(503),
                ok_resp,
            ]
        )
        out = await http_fetch_with_retry(client, "GET", "https://x/api", base_delay=0.001)
        assert out == {"items": [1]}
        assert client.request.await_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_persistent_5xx(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch_with_retry
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        client = AsyncMock()
        client.request = AsyncMock(return_value=_http_status_error_resp(503))
        with pytest.raises(PaginationFetchError) as exc_info:
            await http_fetch_with_retry(client, "GET", "https://x/api", retries=3, base_delay=0.001)
        assert exc_info.value.last_status == 503
        assert exc_info.value.attempts == 3
        assert client.request.await_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_cloudflare_5xx(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json = MagicMock(return_value={"items": [1]})
        for status in (520, 525, 530):
            client = AsyncMock()
            client.request = AsyncMock(side_effect=[_http_status_error_resp(status), ok_resp])
            out = await http_fetch_with_retry(client, "GET", "https://x/api", base_delay=0.001)
            assert out == {"items": [1]}, f"status {status} should retry then succeed"
            assert client.request.await_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_persistent_network_error(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch_with_retry
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        client = AsyncMock()
        client.request = AsyncMock(side_effect=httpx.ConnectError("conn refused"))
        with pytest.raises(PaginationFetchError) as exc_info:
            await http_fetch_with_retry(client, "GET", "https://x/api", retries=2, base_delay=0.001)
        assert exc_info.value.last_error == "ConnectError"
        assert exc_info.value.last_status is None
        assert client.request.await_count == 2

    @pytest.mark.asyncio
    async def test_lenient_http_fetch_returns_none_on_persistent_5xx(self, monkeypatch):
        """The legacy ``http_fetch`` wrapper preserves the "any failure → None"
        contract by catching ``PaginationFetchError`` from
        ``http_fetch_with_retry``. Used by the api_sniffer scraper which
        treats None as "no content found"."""
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        client = AsyncMock()
        client.request = AsyncMock(return_value=_http_status_error_resp(503))
        out = await http_fetch(client, "GET", "https://x/api")
        assert out is None
        # 3 attempts (default), retries exhausted, exception caught + None returned.
        assert client.request.await_count == 3


class TestMaxItemsTruncation:
    """Regression tests for the MAX_ITEMS truncation guard (#3216 / #3267).

    The two silent-truncation sites in ``api_sniffer`` (HTTP-only mode at
    ``_discover_http`` and replay/browser mode at ``_discover_replay``)
    used to slice ``items[:MAX_ITEMS]`` and return a plain
    ``list[DiscoveredJob]`` / ``set[str]``. That dropped every URL
    beyond the cap and looked like a clean cycle to the board
    processor — so ``_MARK_GONE_BY_TIMESTAMP`` would tombstone the
    unseen tail on the next pass (the same silent-data-loss shape
    fixed by #2722 for fetch-failure-driven truncation).

    The fix matches the pattern used by the 29 monitors migrated in
    #3266: drop the slice, keep every collected item, and wrap the
    result via :mod:`src.shared.truncation` helpers so
    ``MonitorResult.truncated`` is ``True``. The board processor sees
    the flag, marks the cycle partial, and skips gone-detection.
    """

    @pytest.mark.asyncio
    async def test_http_mode_rich_returns_truncated_monitor_result(self):
        """``_discover_http`` rich path: > ``max_items`` -> truncated rich result.

        Uses ``max_items`` override in config (cheaper than 10k items)
        so the slice triggers at 2; the test still pins the contract.
        Returns a :class:`MonitorResult` with ``truncated=True`` and
        all URLs preserved (no slicing).
        """
        from src.core.monitor import MonitorResult

        # 3 items, max_items=2 → truncated.
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "max_items": 2,
            "fields": {"title": "title", "description": "desc"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=None)

        assert isinstance(result, MonitorResult), (
            "HTTP truncation must return a MonitorResult (not a plain "
            "list[DiscoveredJob]) so the board processor sees "
            "truncated=True and skips _MARK_GONE_BY_TIMESTAMP."
        )
        assert result.truncated is True
        # All 3 URLs preserved — the cap is a safety stop, not a slice.
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is not None
        assert set(result.jobs_by_url) == result.urls

    @pytest.mark.asyncio
    async def test_http_mode_url_only_returns_truncated_monitor_result(self):
        """``_discover_http`` URL-only path: > ``max_items`` -> truncated URL result."""
        from src.core.monitor import MonitorResult

        items = [
            {"id": "1", "url": "https://example.com/jobs/1"},
            {"id": "2", "url": "https://example.com/jobs/2"},
            {"id": "3", "url": "https://example.com/jobs/3"},
        ]
        api_response = {"results": items}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "max_items": 2,
            # No fields → URL-only mode.
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=None)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        # All 3 URLs preserved — no slicing.
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        # URL-only mode keeps jobs_by_url as None.
        assert result.jobs_by_url is None

    @pytest.mark.asyncio
    async def test_http_mode_under_cap_returns_plain_list(self):
        """Below ``max_items``: behaviour unchanged — plain list returned.

        Verifies the helper only fires when ``len(items) > max_items``;
        clean cycles must continue to return ``list[DiscoveredJob]`` /
        ``set[str]`` so unrelated callers (and the gone-detection path)
        keep working.

        Uses 3 items because ``find_arrays`` (in ``src/shared/api_sniff.py``)
        only surfaces arrays of 3+ dicts.
        """
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "max_items": 10,  # 3 items, cap=10 → not truncated
            "fields": {"title": "title", "description": "desc"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=None)

        # Below-cap path returns the plain list (not a MonitorResult).
        assert isinstance(result, list)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_replay_mode_rich_returns_truncated_monitor_result(self, monkeypatch):
        """``_discover_replay`` rich path: > ``MAX_ITEMS`` -> truncated rich result.

        ``_discover_replay`` doesn't honour ``max_items`` (only ``_discover_http``
        does), so the test patches the module-level ``MAX_ITEMS`` constant
        instead of crafting 10k items. Same pattern as the silent-slice
        regression suite in ``tests/test_truncation.py``.
        """
        from src.core.monitor import MonitorResult
        from src.core.monitors import api_sniffer as api_sniffer_module

        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 2)

        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
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
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, MonitorResult), (
            "Replay truncation must return a MonitorResult so the board "
            "processor skips _MARK_GONE_BY_TIMESTAMP and the unseen tail "
            "beyond the cap is not tombstoned."
        )
        assert result.truncated is True
        # All 3 URLs preserved — no slicing.
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is not None
        assert set(result.jobs_by_url) == result.urls

    @pytest.mark.asyncio
    async def test_replay_mode_url_only_returns_truncated_monitor_result(self, monkeypatch):
        """``_discover_replay`` URL-only path: > ``MAX_ITEMS`` -> truncated URL result."""
        from src.core.monitor import MonitorResult
        from src.core.monitors import api_sniffer as api_sniffer_module

        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 2)

        items = [
            {"id": "1", "url": "https://example.com/jobs/1"},
            {"id": "2", "url": "https://example.com/jobs/2"},
            {"id": "3", "url": "https://example.com/jobs/3"},
        ]
        api_response = {"results": items}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            # No fields → URL-only.
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is None

    @pytest.mark.asyncio
    async def test_replay_mode_under_cap_returns_plain_list(self, monkeypatch):
        """Below ``MAX_ITEMS``: behaviour unchanged — plain list returned.

        Uses 3 items because ``find_arrays`` (in ``src/shared/api_sniff.py``)
        only surfaces arrays of 3+ dicts.
        """
        from src.core.monitors import api_sniffer as api_sniffer_module

        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 10)

        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
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
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)

        # Below-cap path returns the plain list (not a MonitorResult).
        assert isinstance(result, list)
        assert len(result) == 3


class TestDiscoverAutoTruncation:
    """Regression tests for the MAX_ITEMS truncation guard inside
    ``_discover_auto`` (#3336).

    ``_discover_auto`` is the auto-discover entry point (no ``api_url``
    in metadata) — full capture + detect + paginate pipeline. The third
    silent-slice site in ``api_sniffer.py`` used to slice
    ``items[:MAX_ITEMS]`` and return a plain list/set; same mass-delisting
    risk as #3216 / #3267 because the board processor never saw a
    truncation flag and ``_MARK_GONE_BY_TIMESTAMP`` would tombstone every
    URL beyond the cap.

    Stubs the module-level helpers (``capture_exchanges``,
    ``trigger_interactions``, ``detect_job_list``, ``infer_pagination``,
    ``paginate_all``, ``extract_urls_via_dom_crossref``) plus the
    locally-imported ``src.shared.browser`` symbols so the test exercises
    only the truncation branch.
    """

    @staticmethod
    def _patch_auto_pipeline(monkeypatch, items, *, url_field="url"):
        """Stub the auto-discover pipeline so ``paginate_all`` returns *items*.

        Returns the patched module so the caller can `setattr` ``MAX_ITEMS``.
        """
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.shared import browser as browser_module
        from src.shared.api_sniff import ArrayCandidate, Exchange, JobListResult

        exchange = Exchange(
            method="GET",
            url="https://example.com/api/jobs",
            request_headers={},
            post_data=None,
            status=200,
            body={"results": items},
            content_type="application/json",
            phase="load",
        )
        candidate = ArrayCandidate(exchange=exchange, json_path="results", items=items)
        job_list_result = JobListResult(
            candidate=candidate,
            url_field=url_field,
            total_count=len(items),
            pagination=None,
        )

        # Stubs on the api_sniffer module (where the names are bound).
        async def _fake_capture_exchanges(_page, _host):
            return [exchange]

        async def _fake_trigger_interactions(_page, _exchanges):
            return None

        def _fake_detect_job_list(_exchanges, _board_url):
            return job_list_result

        def _fake_infer_pagination(_exchanges, _url, _page_size):
            return None

        async def _fake_paginate_all(_fetcher, _result, _max_pages):
            return list(items)

        async def _fake_extract_urls_via_dom_crossref(_page, _items, _board_url):
            return []

        def _fake_make_browser_fetcher(_page):
            return None

        monkeypatch.setattr(api_sniffer_module, "capture_exchanges", _fake_capture_exchanges)
        monkeypatch.setattr(api_sniffer_module, "trigger_interactions", _fake_trigger_interactions)
        monkeypatch.setattr(api_sniffer_module, "detect_job_list", _fake_detect_job_list)
        monkeypatch.setattr(api_sniffer_module, "infer_pagination", _fake_infer_pagination)
        monkeypatch.setattr(api_sniffer_module, "paginate_all", _fake_paginate_all)
        monkeypatch.setattr(
            api_sniffer_module,
            "extract_urls_via_dom_crossref",
            _fake_extract_urls_via_dom_crossref,
        )
        monkeypatch.setattr(api_sniffer_module, "make_browser_fetcher", _fake_make_browser_fetcher)

        # Stub the locally-imported browser helpers. ``open_page`` is an
        # ``@asynccontextmanager`` so swap it for one that yields a mock page.
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_open_page(_pw, _config, use_proxy=False):
            yield AsyncMock()

        async def _fake_navigate(_page, _url, _opts):
            return None

        async def _fake_dismiss_overlays(_page):
            return None

        monkeypatch.setattr(browser_module, "open_page", _fake_open_page)
        monkeypatch.setattr(browser_module, "navigate", _fake_navigate)
        monkeypatch.setattr(browser_module, "dismiss_overlays", _fake_dismiss_overlays)

        return api_sniffer_module

    @pytest.mark.asyncio
    async def test_auto_rich_returns_truncated_monitor_result(self, monkeypatch):
        """``_discover_auto`` rich path: > ``MAX_ITEMS`` -> truncated rich result.

        Patches ``MAX_ITEMS = 2`` and feeds 3 items so the cycle trips the
        truncation guard. All 3 URLs must be preserved in the result.
        """
        from src.core.monitor import MonitorResult

        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_sniffer_module = self._patch_auto_pipeline(monkeypatch, items)
        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 2)
        # _DEFAULT_SETTLE is autouse-patched to 0 elsewhere, keep symmetry.

        config = {
            # No api_url → auto-discover branch.
            "fields": {"title": "title", "description": "desc"},
            "settle": 0,
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        mock_pw = _make_mock_pw(AsyncMock())

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, MonitorResult), (
            "Auto-discover truncation must return a MonitorResult so the "
            "board processor skips _MARK_GONE_BY_TIMESTAMP and the unseen "
            "tail beyond the cap is not tombstoned."
        )
        assert result.truncated is True
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is not None
        assert set(result.jobs_by_url) == result.urls

    @pytest.mark.asyncio
    async def test_auto_url_only_returns_truncated_monitor_result(self, monkeypatch):
        """``_discover_auto`` URL-only path: > ``MAX_ITEMS`` -> truncated URL result."""
        from src.core.monitor import MonitorResult

        items = [
            {"id": "1", "url": "https://example.com/jobs/1"},
            {"id": "2", "url": "https://example.com/jobs/2"},
            {"id": "3", "url": "https://example.com/jobs/3"},
        ]
        api_sniffer_module = self._patch_auto_pipeline(monkeypatch, items)
        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 2)

        config = {
            # No api_url, no fields → URL-only auto-discover branch.
            "settle": 0,
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        mock_pw = _make_mock_pw(AsyncMock())

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is None

    @pytest.mark.asyncio
    async def test_auto_under_cap_returns_plain_collection(self, monkeypatch):
        """Below ``MAX_ITEMS``: behaviour unchanged — plain list/set returned.

        Verifies the helper only fires when ``len(items) > MAX_ITEMS``;
        an under-cap auto-discover must keep returning the original
        ``list[DiscoveredJob]`` shape (regression: no MonitorResult wrap).
        """
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_sniffer_module = self._patch_auto_pipeline(monkeypatch, items)
        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 10)

        config = {
            "fields": {"title": "title", "description": "desc"},
            "settle": 0,
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        mock_pw = _make_mock_pw(AsyncMock())

        result = await discover(board, http, pw=mock_pw)

        # Below-cap path returns the plain list (not a MonitorResult).
        assert isinstance(result, list)
        assert len(result) == 3
