from __future__ import annotations

import httpx
import pytest

from src.core.monitors import (
    _REGISTRY as monitor_registry,
)
from src.core.monitors import (
    detect_monitor_type,
    fetch_page_text,
    get_discoverer,
    slugs_from_url,
)
from src.core.scrapers import _REGISTRY as scraper_registry
from src.core.scrapers import get_scraper


class TestMonitorRegistry:
    def test_greenhouse_registered(self):
        names = [m.name for m in monitor_registry]
        assert "greenhouse" in names

    def test_lever_registered(self):
        names = [m.name for m in monitor_registry]
        assert "lever" in names

    def test_sitemap_registered(self):
        names = [m.name for m in monitor_registry]
        assert "sitemap" in names

    def test_sorted_by_cost(self):
        costs = [m.cost for m in monitor_registry]
        assert costs == sorted(costs)

    def test_get_discoverer_greenhouse(self):
        fn = get_discoverer("greenhouse")
        assert callable(fn)

    def test_get_discoverer_lever(self):
        fn = get_discoverer("lever")
        assert callable(fn)

    def test_get_discoverer_sitemap(self):
        fn = get_discoverer("sitemap")
        assert callable(fn)

    def test_get_discoverer_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown monitor type"):
            get_discoverer("nonexistent")


class TestScraperRegistry:
    def test_jsonld_registered(self):
        assert "json-ld" in scraper_registry

    def test_html_registered(self):
        assert "html" in scraper_registry

    def test_browser_registered(self):
        assert "browser" in scraper_registry

    def test_get_scraper_jsonld(self):
        fn = get_scraper("json-ld")
        assert callable(fn)

    def test_get_scraper_html(self):
        fn = get_scraper("html")
        assert callable(fn)

    def test_get_scraper_browser(self):
        fn = get_scraper("browser")
        assert callable(fn)

    def test_get_scraper_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown scraper type"):
            get_scraper("nonexistent")


class TestSlugsFromUrl:
    def test_standard_domain(self):
        assert slugs_from_url("https://www.stripe.com/careers") == ["stripe"]

    def test_subdomain(self):
        assert slugs_from_url("https://jobs.example.com/listings") == ["example"]

    def test_no_www(self):
        assert slugs_from_url("https://stripe.com") == ["stripe"]

    def test_deep_path(self):
        assert slugs_from_url("https://www.isomorphiclabs.com/job-openings") == ["isomorphiclabs"]


class TestFetchPageText:
    async def test_success(self):
        def handler(request):
            return httpx.Response(200, text="Hello World")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await fetch_page_text("https://example.com", client)
            assert result == "Hello World"

    async def test_404_returns_none(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await fetch_page_text("https://example.com", client)
            assert result is None

    async def test_truncation(self):
        def handler(request):
            return httpx.Response(200, text="x" * 1000)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await fetch_page_text("https://example.com", client, max_chars=100)
            assert len(result) == 100


class TestDetectMonitorType:
    async def test_detects_greenhouse_url(self):
        result = await detect_monitor_type("https://boards.greenhouse.io/stripe", None)
        assert result is not None
        assert result[0] == "greenhouse"

    async def test_detects_lever_url(self):
        result = await detect_monitor_type("https://jobs.lever.co/stripe", None)
        assert result is not None
        assert result[0] == "lever"

    async def test_no_match_returns_none(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await detect_monitor_type("https://example.com/careers", client)
            assert result is None
