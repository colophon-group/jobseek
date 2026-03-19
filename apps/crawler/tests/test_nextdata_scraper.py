"""Tests for src.core.scrapers.nextdata."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx

from src.core.scrapers import JobContent
from src.core.scrapers.nextdata import scrape

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

JOB_DATA = {
    "props": {
        "pageProps": {
            "jobData": {
                "title": "Engineer",
                "descriptionHtml": "<p>Build things</p>",
                "locations": [{"name": "London"}, {"name": "Remote"}],
                "department": {"name": "Engineering"},
                "employmentType": "Full-time",
            }
        }
    }
}


def _html_with_next_data(data: dict) -> str:
    return (
        f"<html><body>"
        f'<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(data)}"
        f"</script></body></html>"
    )


SAMPLE_HTML = _html_with_next_data(JOB_DATA)


def _mock_transport(html: str, status: int = 200):
    def handler(request):
        return httpx.Response(status, text=html)

    return httpx.MockTransport(handler)


BASE_CONFIG = {
    "path": "props.pageProps.jobData",
    "fields": {
        "title": "title",
        "description": "descriptionHtml",
        "locations": "locations[].name",
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNextdataScraper:
    async def test_basic_extraction(self):
        """Title, description, locations mapped correctly."""
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await scrape("https://example.com/job/1", BASE_CONFIG, client)
        assert result.title == "Engineer"
        assert result.description == "<p>Build things</p>"
        assert result.locations == ["London", "Remote"]

    async def test_nested_field(self):
        """department.name extracted via dot path."""
        config = {
            **BASE_CONFIG,
            "fields": {"title": "title", "metadata.team": "department.name"},
        }
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await scrape("https://example.com/job/1", config, client)
        assert result.title == "Engineer"
        assert result.metadata is not None
        assert result.metadata["team"] == "Engineering"

    async def test_array_unwrap(self):
        """locations[].name → list of strings."""
        config = {
            "path": "props.pageProps.jobData",
            "fields": {"locations": "locations[].name"},
        }
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await scrape("https://example.com/job/1", config, client)
        assert result.locations == ["London", "Remote"]

    async def test_metadata_field(self):
        """metadata.team populates JobContent.metadata."""
        config = {
            "path": "props.pageProps.jobData",
            "fields": {"metadata.team": "department.name"},
        }
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await scrape("https://example.com/job/1", config, client)
        assert result.metadata == {"team": "Engineering"}

    async def test_missing_field(self):
        """Unknown spec → None (field not set)."""
        config = {
            "path": "props.pageProps.jobData",
            "fields": {"title": "nonexistent"},
        }
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await scrape("https://example.com/job/1", config, client)
        assert result.title is None

    async def test_no_path_uses_root(self):
        """Omit 'path' → use entire __NEXT_DATA__ as item."""
        data = {"title": "Root Job", "desc": "At root level"}
        html = _html_with_next_data(data)
        config = {"fields": {"title": "title", "description": "desc"}}
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await scrape("https://example.com/job/1", config, client)
        assert result.title == "Root Job"
        assert result.description == "At root level"

    async def test_render_mode(self):
        """render: true delegates to shared.browser.render."""
        config = {**BASE_CONFIG, "render": True}
        with (
            patch("src.core.scrapers.nextdata.browser_render", create=True),
            patch(
                "src.shared.browser.render",
                new_callable=AsyncMock,
                return_value=SAMPLE_HTML,
            ),
            patch(
                "src.core.scrapers.nextdata.browser_render",
                new_callable=AsyncMock,
                return_value=SAMPLE_HTML,
            ),
        ):
            # Actually patch the lazy import
            async with httpx.AsyncClient(transport=_mock_transport("")) as client:
                with patch(
                    "src.shared.browser.render",
                    new_callable=AsyncMock,
                    return_value=SAMPLE_HTML,
                ):
                    result = await scrape("https://example.com/job/1", config, client)
        assert result.title == "Engineer"

    async def test_no_next_data(self):
        """Page without __NEXT_DATA__ → empty JobContent."""
        html = "<html><body>No Next.js here</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await scrape("https://example.com/job/1", BASE_CONFIG, client)
        assert result == JobContent()

    async def test_path_not_found(self):
        """Path resolves to None → empty JobContent."""
        data = {"props": {"pageProps": {"other": {}}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await scrape("https://example.com/job/1", BASE_CONFIG, client)
        assert result == JobContent()

    async def test_empty_fields(self):
        """No 'fields' in config → empty JobContent."""
        config = {"path": "props.pageProps.jobData"}
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await scrape("https://example.com/job/1", config, client)
        assert result == JobContent()


# ---------------------------------------------------------------------------
# RSC flight payload scraper tests
# ---------------------------------------------------------------------------


def _html_with_rsc_data(data: dict) -> str:
    """Build HTML with RSC flight payload."""
    rsc_array = ["$", "$L10", None, data]
    rsc_json = json.dumps(rsc_array)
    rsc_line = f"7:{rsc_json}\n"
    escaped = rsc_line.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'<html><body><script>self.__next_f.push([1,"{escaped}"])</script></body></html>'


RSC_JOB = {
    "job": {
        "title": "Software Engineer",
        "aboutRole": "Build our platform.",
        "location": "Zurich",
        "type": "Full-time",
    }
}

RSC_HTML = _html_with_rsc_data(RSC_JOB)

RSC_CONFIG = {
    "source": "rsc",
    "path": "job",
    "fields": {
        "title": "title",
        "description": "aboutRole",
        "locations": "location",
        "employment_type": "type",
    },
}


class TestRscScraper:
    async def test_basic_extraction(self):
        """RSC flight payload data extracted correctly."""
        async with httpx.AsyncClient(transport=_mock_transport(RSC_HTML)) as client:
            result = await scrape("https://example.com/jobs/swe", RSC_CONFIG, client)
        assert result.title == "Software Engineer"
        assert result.description == "Build our platform."
        assert result.locations == ["Zurich"]
        assert result.employment_type == "Full-time"

    async def test_no_rsc_data(self):
        """Page without RSC payload → empty JobContent."""
        html = "<html><body>No RSC here</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await scrape("https://example.com/jobs/swe", RSC_CONFIG, client)
        assert result == JobContent()

    async def test_inject_script_id_skipped(self):
        """source: rsc config must NOT get script_id injected."""
        from src.core.scrapers.nextdata import _inject_script_id

        result = _inject_script_id(RSC_CONFIG)
        assert "script_id" not in result
        assert result.get("source") == "rsc"
