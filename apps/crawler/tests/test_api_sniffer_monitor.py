"""Tests for the api_sniffer monitor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.monitors.api_sniffer import _extract_rich, _extract_urls_from_template, discover


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
            items, fields, None,
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
            "fields": {"title": "title", "description": "desc"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        # Mock Playwright
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=json.dumps(api_response))

        mock_pw = MagicMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()

        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

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
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=json.dumps(api_response))

        mock_pw = MagicMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()

        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, set)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_no_playwright_returns_empty(self):
        """Without pw, discover should return empty set."""
        board = {"board_url": "https://example.com/careers", "metadata": {}}
        http = AsyncMock()
        result = await discover(board, http, pw=None)
        assert isinstance(result, set)
        assert len(result) == 0
