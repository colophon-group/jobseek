"""Tests for src.core.scrapers.embedded — unified embedded data scraper."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx

from src.core.scrapers import JobContent
from src.core.scrapers.embedded import can_handle, parse_html, scrape

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _script_html(script_id: str, data: dict) -> str:
    return (
        f"<html><body>"
        f'<script id="{script_id}" type="application/json">'
        f"{json.dumps(data)}"
        f"</script></body></html>"
    )


def _variable_html(var_name: str, data: dict) -> str:
    return f"<html><body><script>{var_name} = {json.dumps(data)};</script></body></html>"


def _af_callback_html(data: list) -> str:
    return (
        f"<html><body><script>"
        f"AF_initDataCallback({{key: 'ds:1', data: {json.dumps(data)}}});"
        f"</script></body></html>"
    )


JOB_DATA = {
    "title": "Engineer",
    "descriptionHtml": "<p>Build things</p>",
    "locations": [{"name": "London"}, {"name": "Remote"}],
    "department": {"name": "Engineering"},
    "employmentType": "Full-time",
}

SCRIPT_HTML = _script_html("app-data", {"job": JOB_DATA})

VARIABLE_HTML = _variable_html("window.__DATA__", {"job": JOB_DATA})

# Google Wiz style: positional array
AF_DATA = [
    "ignored",
    "Software Engineer",
    "uuid-123",
    "Engineering",
    None,
    None,
    None,
    None,
    None,
    [["id1", None, "New York"], ["id2", None, "Remote"]],
    "<p>Job description here</p>",
]
AF_HTML = _af_callback_html(AF_DATA)


def _mock_transport(html: str, status: int = 200):
    def handler(request):
        return httpx.Response(status, text=html)

    return httpx.MockTransport(handler)


SCRIPT_CONFIG = {
    "script_id": "app-data",
    "path": "job",
    "fields": {
        "title": "title",
        "description": "descriptionHtml",
        "locations": "locations[].name",
    },
}

VARIABLE_CONFIG = {
    "variable": "window.__DATA__",
    "path": "job",
    "fields": {
        "title": "title",
        "description": "descriptionHtml",
        "locations": "locations[].name",
    },
}

AF_CONFIG = {
    "pattern": r"AF_initDataCallback\s*\(\s*\{[^}]*data\s*:\s*",
    "fields": {
        "title": "[1]",
        "description": "[10]",
        "locations": "[9][*][2]",
    },
}


# ---------------------------------------------------------------------------
# parse_html tests
# ---------------------------------------------------------------------------


class TestParseHtml:
    def test_script_id_extraction(self):
        result = parse_html(SCRIPT_HTML, SCRIPT_CONFIG)
        assert result.title == "Engineer"
        assert result.description == "<p>Build things</p>"
        assert result.locations == ["London", "Remote"]

    def test_variable_extraction(self):
        result = parse_html(VARIABLE_HTML, VARIABLE_CONFIG)
        assert result.title == "Engineer"
        assert result.description == "<p>Build things</p>"
        assert result.locations == ["London", "Remote"]

    def test_af_callback_positional(self):
        result = parse_html(AF_HTML, AF_CONFIG)
        assert result.title == "Software Engineer"
        assert result.description == "<p>Job description here</p>"
        assert result.locations == ["New York", "Remote"]

    def test_jmespath_named_fields(self):
        config = {
            "script_id": "app-data",
            "path": "job",
            "fields": {
                "title": "title",
                "metadata.team": "department.name",
                "employment_type": "employmentType",
            },
        }
        result = parse_html(SCRIPT_HTML, config)
        assert result.title == "Engineer"
        assert result.employment_type == "Full-time"
        assert result.metadata == {"team": "Engineering"}

    def test_missing_data_returns_empty(self):
        html = "<html><body>Nothing here</body></html>"
        result = parse_html(html, SCRIPT_CONFIG)
        assert result == JobContent()

    def test_missing_path_returns_empty(self):
        data = {"other": {"stuff": True}}
        html = _script_html("app-data", data)
        result = parse_html(html, SCRIPT_CONFIG)
        assert result == JobContent()

    def test_no_fields_returns_empty(self):
        config = {"script_id": "app-data", "path": "job"}
        result = parse_html(SCRIPT_HTML, config)
        assert result == JobContent()

    def test_no_path_uses_root(self):
        data = {"title": "Root Job", "desc": "At root"}
        html = _script_html("data", data)
        config = {"script_id": "data", "fields": {"title": "title", "description": "desc"}}
        result = parse_html(html, config)
        assert result.title == "Root Job"
        assert result.description == "At root"


# ---------------------------------------------------------------------------
# scrape tests
# ---------------------------------------------------------------------------


class TestScrape:
    async def test_basic_scrape(self):
        async with httpx.AsyncClient(transport=_mock_transport(SCRIPT_HTML)) as client:
            result = await scrape("https://example.com/job/1", SCRIPT_CONFIG, client)
        assert result.title == "Engineer"
        assert result.description == "<p>Build things</p>"
        assert result.locations == ["London", "Remote"]

    async def test_variable_scrape(self):
        async with httpx.AsyncClient(transport=_mock_transport(VARIABLE_HTML)) as client:
            result = await scrape("https://example.com/job/1", VARIABLE_CONFIG, client)
        assert result.title == "Engineer"

    async def test_af_callback_scrape(self):
        async with httpx.AsyncClient(transport=_mock_transport(AF_HTML)) as client:
            result = await scrape("https://example.com/job/1", AF_CONFIG, client)
        assert result.title == "Software Engineer"
        assert result.locations == ["New York", "Remote"]

    async def test_fetch_failure(self):
        async with httpx.AsyncClient(transport=_mock_transport("", status=404)) as client:
            result = await scrape("https://example.com/job/1", SCRIPT_CONFIG, client)
        assert result == JobContent()

    async def test_no_fields_returns_empty(self):
        config = {"script_id": "app-data", "path": "job"}
        async with httpx.AsyncClient(transport=_mock_transport(SCRIPT_HTML)) as client:
            result = await scrape("https://example.com/job/1", config, client)
        assert result == JobContent()

    async def test_render_mode(self):
        config = {**SCRIPT_CONFIG, "render": True}
        with patch(
            "src.shared.browser.render",
            new_callable=AsyncMock,
            return_value=SCRIPT_HTML,
        ):
            async with httpx.AsyncClient(transport=_mock_transport("")) as client:
                result = await scrape("https://example.com/job/1", config, client)
        assert result.title == "Engineer"


# ---------------------------------------------------------------------------
# can_handle tests
# ---------------------------------------------------------------------------


class TestCanHandle:
    def test_script_id_detected(self):
        data = {"title": "Engineer", "description": "<p>Build</p>"}
        html = _script_html("job-data", data)
        result = can_handle([html])
        assert result is not None
        assert result["script_id"] == "job-data"
        assert "fields" in result

    def test_af_callback_detected(self):
        data = [
            1,
            "Engineer",
            "desc",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "<p>Description</p>",
        ]
        html = _af_callback_html(data)
        result = can_handle([html])
        assert result is not None
        assert "pattern" in result

    def test_variable_detected(self):
        data = {"title": "PM", "description": "<p>Lead</p>"}
        html = _variable_html("window.__DATA__", data)
        result = can_handle([html])
        assert result is not None
        assert result["variable"] == "window.__DATA__"
        assert "fields" in result

    def test_plain_html_returns_none(self):
        html = "<html><body><h1>No embedded data</h1></body></html>"
        result = can_handle([html])
        assert result is None

    def test_next_data_excluded(self):
        """__NEXT_DATA__ should NOT be detected by embedded (that's nextdata's domain)."""
        data = {"props": {"pageProps": {"title": "Eng", "description": "Hi"}}}
        html = (
            f"<html><body>"
            f'<script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(data)}"
            f"</script></body></html>"
        )
        result = can_handle([html])
        assert result is None

    def test_majority_threshold(self):
        """At least half the pages must match."""
        data = {"title": "Engineer", "description": "<p>Build</p>"}
        good_html = _script_html("job-data", data)
        bad_html = "<html><body>Nothing</body></html>"
        # 1 of 3 → should not detect
        result = can_handle([good_html, bad_html, bad_html])
        assert result is None
        # 2 of 3 → should detect
        result = can_handle([good_html, good_html, bad_html])
        assert result is not None

    def test_nested_job_object(self):
        data = {"job": {"title": "PM", "description": "<p>Lead</p>"}}
        html = _script_html("app-data", data)
        result = can_handle([html])
        assert result is not None
        assert result.get("path") == "job"
