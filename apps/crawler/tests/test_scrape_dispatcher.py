from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.scrape import scrape_one
from src.core.scrapers import JobContent

_patch_throttle = patch("src.core.scrape.throttle_domain", new_callable=AsyncMock)


class TestScrapeOne:
    @_patch_throttle
    async def test_delegates_to_jsonld(self, _mock_throttle):
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Data Scientist", "description": "ML work"}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape_one("https://example.com/job", "json-ld", {}, client)
            assert isinstance(result, JobContent)
            assert result.title == "Data Scientist"
            assert result.description == "ML work"

    @_patch_throttle
    async def test_delegates_to_dom_static(self, _mock_throttle):
        page_html = """<html><body>
        <h1>Engineer</h1>
        </body></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape_one(
                "https://example.com/job",
                "dom",
                {"render": False, "steps": [{"tag": "h1", "field": "title"}]},
                client,
            )
            assert isinstance(result, JobContent)
            assert result.title == "Engineer"

    @_patch_throttle
    async def test_none_config_treated_as_empty(self, _mock_throttle):
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Test"}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape_one("https://example.com/job", "json-ld", None, client)
            assert result.title == "Test"

    async def test_unknown_scraper_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="Unknown scraper type"):
                await scrape_one("https://example.com/job", "nonexistent", {}, client)
