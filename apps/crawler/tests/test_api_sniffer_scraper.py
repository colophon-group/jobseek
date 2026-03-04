"""Tests for the api_sniffer scraper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.scrapers.api_sniffer import (
    _extract_from_object,
    _extract_heuristic,
    _find_single_job,
    _score_job_object,
    probe_pw,
)
from src.shared.api_sniff import Exchange


def _make_exchange(url="https://example.com/api/job", body=None, phase="load"):
    return Exchange(
        method="GET", url=url,
        request_headers={}, post_data=None,
        status=200, body=body,
        content_type="application/json", phase=phase,
    )


class TestScoreJobObject:
    def test_good_job_object(self):
        obj = {
            "title": "Software Engineer",
            "description": "A " * 30 + "long description",
            "location": "NYC",
            "department": "Engineering",
            "id": "123",
        }
        score = _score_job_object(obj)
        assert score >= 30  # title(10) + description(20) + location(5) + keys(5)

    def test_no_title_returns_zero(self):
        obj = {"description": "Some text", "location": "NYC"}
        assert _score_job_object(obj) == 0

    def test_short_description(self):
        obj = {"title": "Dev", "description": "Short"}
        score = _score_job_object(obj)
        assert score == 10  # Only title, description too short


class TestFindSingleJob:
    def test_finds_top_level(self):
        body = {
            "title": "Developer",
            "description": "A " * 30 + "long description",
            "location": "NYC",
            "id": "123",
            "department": "Eng",
        }
        ex = _make_exchange(body=body)
        result = _find_single_job([ex])
        assert result is not None
        assert result["title"] == "Developer"

    def test_finds_nested(self):
        body = {
            "data": {
                "title": "PM",
                "description": "A " * 30 + "long desc",
                "location": "SF",
                "id": "456",
                "team": "Product",
            }
        }
        ex = _make_exchange(body=body)
        result = _find_single_job([ex])
        assert result is not None
        assert result["title"] == "PM"

    def test_returns_none_no_job(self):
        body = {"config": {"theme": "dark"}}
        ex = _make_exchange(body=body)
        result = _find_single_job([ex])
        assert result is None

    def test_best_score_wins(self):
        body_weak = {"title": "X"}
        body_strong = {
            "title": "Developer",
            "description": "A " * 30 + "rich HTML content here",
            "location": "NYC",
            "id": "1",
            "dept": "Eng",
        }
        ex1 = _make_exchange(url="https://example.com/a", body=body_weak)
        ex2 = _make_exchange(url="https://example.com/b", body=body_strong)
        result = _find_single_job([ex1, ex2])
        assert result is not None
        assert result["title"] == "Developer"


class TestExtractHeuristic:
    def test_all_fields(self):
        obj = {
            "title": "Dev",
            "description": "HTML content",
            "location": "NYC",
            "employmentType": "Full-time",
            "datePosted": "2024-01-15",
            "workplaceType": "remote",
        }
        content = _extract_heuristic(obj)
        assert content.title == "Dev"
        assert content.description == "HTML content"
        assert content.locations == ["NYC"]
        assert content.employment_type == "Full-time"
        assert content.date_posted == "2024-01-15"
        assert content.job_location_type == "remote"

    def test_locations_array_of_strings(self):
        obj = {"title": "Dev", "locations": ["NYC", "SF"]}
        content = _extract_heuristic(obj)
        assert content.locations == ["NYC", "SF"]

    def test_locations_array_of_objects(self):
        obj = {
            "title": "Dev",
            "locations": [{"name": "NYC"}, {"name": "SF"}],
        }
        content = _extract_heuristic(obj)
        assert content.locations == ["NYC", "SF"]

    def test_empty_object(self):
        content = _extract_heuristic({})
        assert content.title is None
        assert content.description is None


class TestExtractFromObject:
    def test_with_explicit_mapping(self):
        obj = {
            "jobTitle": "Engineer",
            "bodyHtml": "<p>Job desc</p>",
            "offices": [{"name": "NYC"}, {"name": "LA"}],
        }
        config = {
            "fields": {
                "title": "jobTitle",
                "description": "bodyHtml",
                "locations": "offices[].name",
            }
        }
        content = _extract_from_object(obj, config)
        assert content.title == "Engineer"
        assert content.description == "<p>Job desc</p>"
        assert content.locations == ["NYC", "LA"]

    def test_without_mapping_uses_heuristic(self):
        obj = {"title": "Dev", "description": "HTML content"}
        content = _extract_from_object(obj, {})
        assert content.title == "Dev"
        assert content.description == "HTML content"

    def test_metadata_fields(self):
        obj = {"title": "Dev", "url": "/jobs/1", "department": "Eng"}
        config = {"fields": {"title": "title", "metadata.team": "department"}}
        content = _extract_from_object(obj, config)
        assert content.title == "Dev"
        assert content.metadata == {"team": "Eng"}


def _make_mock_pw(exchanges_per_url: dict[str, list]):
    """Create a mock Playwright that yields pages with preset exchanges.

    ``exchanges_per_url`` maps URL → list of Exchange objects that
    ``capture_exchanges`` should populate when that URL is navigated to.
    """
    pw = MagicMock()

    class _FakePage:
        """Async-context-manager page that populates exchanges on navigate."""

        def __init__(self):
            self._exchanges: list = []
            self._url: str | None = None

        def on(self, event, callback):
            pass

        async def goto(self, url, **kwargs):
            self._url = url

    # We patch open_page and capture_exchanges + navigate at the module level
    return pw, exchanges_per_url


class TestProbePw:
    async def test_detects_job_data(self):
        """probe_pw detects single-job XHR responses and returns metadata."""
        job_body = {
            "title": "Software Engineer",
            "description": "A " * 30 + "long description here",
            "location": "NYC",
            "department": "Engineering",
            "id": "123",
        }

        exchange = _make_exchange(body=job_body)

        async def fake_capture(page, host):
            return [exchange]

        async def fake_navigate(page, url, opts):
            pass

        # Mock open_page as an async context manager
        mock_page = MagicMock()
        mock_open_page = MagicMock()
        mock_open_page.return_value.__aenter__ = AsyncMock(return_value=mock_page)
        mock_open_page.return_value.__aexit__ = AsyncMock(return_value=False)

        pw = MagicMock()

        with patch("src.shared.browser.open_page", mock_open_page), \
             patch("src.core.scrapers.api_sniffer.capture_exchanges", fake_capture), \
             patch("src.shared.browser.navigate", fake_navigate), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            metadata, comment = await probe_pw(
                ["https://example.com/job/1", "https://example.com/job/2"],
                pw,
            )

        assert metadata is not None
        assert metadata["titles"] == 2
        assert metadata["descriptions"] == 2
        assert metadata["total"] == 2
        assert "config" in metadata
        assert "fields" in metadata["config"]
        assert "titles" in comment

    async def test_no_data_returns_none(self):
        """probe_pw returns None when no XHR job data found."""
        exchange = _make_exchange(body={"config": {"theme": "dark"}})

        async def fake_capture(page, host):
            return [exchange]

        async def fake_navigate(page, url, opts):
            pass

        mock_page = MagicMock()
        mock_open_page = MagicMock()
        mock_open_page.return_value.__aenter__ = AsyncMock(return_value=mock_page)
        mock_open_page.return_value.__aexit__ = AsyncMock(return_value=False)

        pw = MagicMock()

        with patch("src.shared.browser.open_page", mock_open_page), \
             patch("src.core.scrapers.api_sniffer.capture_exchanges", fake_capture), \
             patch("src.shared.browser.navigate", fake_navigate), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            metadata, comment = await probe_pw(
                ["https://example.com/job/1"],
                pw,
            )

        assert metadata is None
        assert "Not detected" in comment

    async def test_below_threshold_returns_none(self):
        """probe_pw returns None when < 50% of pages have job data."""
        job_body = {
            "title": "Engineer",
            "description": "A " * 30 + "long description",
            "location": "NYC",
            "id": "1",
            "dept": "Eng",
        }
        no_job_body = {"settings": {"locale": "en"}}

        call_count = 0

        async def fake_capture(page, host):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [_make_exchange(body=job_body)]
            return [_make_exchange(body=no_job_body)]

        async def fake_navigate(page, url, opts):
            pass

        mock_page = MagicMock()
        mock_open_page = MagicMock()
        mock_open_page.return_value.__aenter__ = AsyncMock(return_value=mock_page)
        mock_open_page.return_value.__aexit__ = AsyncMock(return_value=False)

        pw = MagicMock()

        with patch("src.shared.browser.open_page", mock_open_page), \
             patch("src.core.scrapers.api_sniffer.capture_exchanges", fake_capture), \
             patch("src.shared.browser.navigate", fake_navigate), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            metadata, comment = await probe_pw(
                ["https://example.com/job/1", "https://example.com/job/2", "https://example.com/job/3"],
                pw,
            )

        assert metadata is None
        assert "1/3" in comment
