from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import pytest

from src.core.monitors.sitemap import (
    _strip_utm,
    _extract_urls,
    _is_sitemap_index,
    _extract_child_sitemaps,
    _is_job_related,
    _walk_up_candidates,
    _common_nonstandard_candidates,
    _try_fetch_xml,
    _parse_robots_sitemaps,
    SitemapDiscoveryError,
    SitemapParseError,
    discover,
    can_handle,
)


class TestStripUtm:
    def test_no_params(self):
        assert _strip_utm("https://example.com/job") == "https://example.com/job"

    def test_removes_utm_params(self):
        result = _strip_utm("https://example.com/job?utm_source=google&utm_medium=cpc")
        assert result == "https://example.com/job"

    def test_keeps_non_utm_params(self):
        result = _strip_utm("https://example.com/job?id=1&utm_source=google")
        assert "id=1" in result
        assert "utm_source" not in result

    def test_only_utm_params(self):
        result = _strip_utm("https://example.com/job?utm_campaign=test")
        assert result == "https://example.com/job"


class TestExtractUrls:
    def test_standard_sitemap(self):
        xml_str = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/job1</loc></url>
            <url><loc>https://example.com/job2</loc></url>
        </urlset>"""
        root = ET.fromstring(xml_str)
        urls = _extract_urls(root)
        assert urls == ["https://example.com/job1", "https://example.com/job2"]

    def test_strips_utm_from_urls(self):
        xml_str = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/job1?utm_source=test</loc></url>
        </urlset>"""
        root = ET.fromstring(xml_str)
        urls = _extract_urls(root)
        assert urls == ["https://example.com/job1"]

    def test_no_namespace_fallback(self):
        xml_str = """<?xml version="1.0"?>
        <urlset>
            <url><loc>https://example.com/job1</loc></url>
        </urlset>"""
        root = ET.fromstring(xml_str)
        urls = _extract_urls(root)
        assert urls == ["https://example.com/job1"]

    def test_empty_sitemap(self):
        xml_str = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        </urlset>"""
        root = ET.fromstring(xml_str)
        urls = _extract_urls(root)
        assert urls == []

    def test_strips_whitespace(self):
        xml_str = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>  https://example.com/job1  </loc></url>
        </urlset>"""
        root = ET.fromstring(xml_str)
        urls = _extract_urls(root)
        assert urls == ["https://example.com/job1"]


class TestIsSitemapIndex:
    def test_is_index(self):
        xml_str = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
        </sitemapindex>"""
        root = ET.fromstring(xml_str)
        assert _is_sitemap_index(root) is True

    def test_is_not_index(self):
        xml_str = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/job1</loc></url>
        </urlset>"""
        root = ET.fromstring(xml_str)
        assert _is_sitemap_index(root) is False


class TestExtractChildSitemaps:
    def test_extracts_children(self):
        xml_str = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
            <sitemap><loc>https://example.com/sitemap-2.xml</loc></sitemap>
        </sitemapindex>"""
        root = ET.fromstring(xml_str)
        children = _extract_child_sitemaps(root)
        assert len(children) == 2
        assert "https://example.com/sitemap-1.xml" in children

    def test_no_children(self):
        xml_str = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        </sitemapindex>"""
        root = ET.fromstring(xml_str)
        children = _extract_child_sitemaps(root)
        assert children == []

    def test_no_namespace_fallback(self):
        xml_str = """<?xml version="1.0"?>
        <sitemapindex>
            <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
        </sitemapindex>"""
        root = ET.fromstring(xml_str)
        children = _extract_child_sitemaps(root)
        assert children == ["https://example.com/sitemap-1.xml"]


class TestIsJobRelated:
    def test_job_url(self):
        assert _is_job_related("https://example.com/jobs/123") is True

    def test_career_url(self):
        assert _is_job_related("https://example.com/careers/swe") is True

    def test_position_url(self):
        assert _is_job_related("https://example.com/positions/dev") is True

    def test_posting_url(self):
        assert _is_job_related("https://example.com/postings/1") is True

    def test_non_job_url(self):
        assert _is_job_related("https://example.com/about") is False

    def test_non_job_url_blog(self):
        assert _is_job_related("https://example.com/blog/post") is False


class TestWalkUpCandidates:
    def test_generates_candidates(self):
        candidates = _walk_up_candidates("https://example.com/careers/engineering")
        assert candidates[0] == "https://example.com/careers/engineering/sitemap.xml"
        assert "https://example.com/careers/sitemap.xml" in candidates
        assert "https://example.com/sitemap.xml" in candidates

    def test_root_url(self):
        candidates = _walk_up_candidates("https://example.com")
        assert "https://example.com/sitemap.xml" in candidates

    def test_no_duplicates(self):
        candidates = _walk_up_candidates("https://example.com/")
        assert len(candidates) == len(set(candidates))


class TestCommonNonstandardCandidates:
    def test_returns_three_candidates(self):
        candidates = _common_nonstandard_candidates("https://example.com/careers")
        assert len(candidates) == 3
        assert all("example.com" in c for c in candidates)
        assert "https://example.com/sitemaps/sitemapIndex" in candidates


class TestTryFetchXml:
    async def test_success(self):
        xml_str = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/job1</loc></url>
        </urlset>"""

        def handler(request):
            return httpx.Response(200, text=xml_str, headers={"content-type": "application/xml"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            root = await _try_fetch_xml("https://example.com/sitemap.xml", client)
            assert root is not None

    async def test_404(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            root = await _try_fetch_xml("https://example.com/sitemap.xml", client)
            assert root is None

    async def test_non_xml_content_type(self):
        def handler(request):
            return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            root = await _try_fetch_xml("https://example.com/sitemap.xml", client)
            assert root is None


class TestParseRobotsSitemaps:
    async def test_parses_sitemaps(self):
        robots_txt = "User-agent: *\nDisallow: /admin\nSitemap: https://example.com/sitemap.xml\n"

        def handler(request):
            return httpx.Response(200, text=robots_txt, headers={"content-type": "text/plain"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sitemaps = await _parse_robots_sitemaps("https://example.com/careers", client)
            assert sitemaps == ["https://example.com/sitemap.xml"]

    async def test_404_returns_empty(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sitemaps = await _parse_robots_sitemaps("https://example.com/careers", client)
            assert sitemaps == []

    async def test_html_response_returns_empty(self):
        def handler(request):
            return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sitemaps = await _parse_robots_sitemaps("https://example.com/careers", client)
            assert sitemaps == []


class TestDiscover:
    async def test_with_cached_sitemap(self):
        sitemap_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/1</loc></url>
            <url><loc>https://example.com/jobs/2</loc></url>
        </urlset>"""

        def handler(request):
            return httpx.Response(200, text=sitemap_xml, headers={"content-type": "application/xml"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"sitemap_url": "https://example.com/sitemap.xml"},
            }
            urls, new_sitemap = await discover(board, client)
            assert len(urls) == 2
            assert "https://example.com/jobs/1" in urls
            assert new_sitemap is None  # cached, not new

    async def test_discovers_new_sitemap(self):
        sitemap_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/1</loc></url>
        </urlset>"""

        def handler(request):
            url = str(request.url)
            if "sitemap.xml" in url:
                return httpx.Response(200, text=sitemap_xml, headers={"content-type": "application/xml"})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {},
            }
            urls, new_sitemap = await discover(board, client)
            assert len(urls) == 1
            assert new_sitemap is not None

    async def test_cached_miss_rediscovers(self):
        sitemap_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/1</loc></url>
        </urlset>"""

        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            url = str(request.url)
            # Cached URL fails
            if "old-sitemap.xml" in url:
                return httpx.Response(404)
            # Discovery succeeds
            if "sitemap.xml" in url:
                return httpx.Response(200, text=sitemap_xml, headers={"content-type": "application/xml"})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"sitemap_url": "https://example.com/old-sitemap.xml"},
            }
            urls, new_sitemap = await discover(board, client)
            assert len(urls) == 1
            assert new_sitemap is not None  # rediscovered

    async def test_resolves_sitemap_index(self):
        index_xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/sitemap-jobs.xml</loc></sitemap>
        </sitemapindex>"""

        child_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/1</loc></url>
        </urlset>"""

        def handler(request):
            url = str(request.url)
            if "sitemap-jobs.xml" in url:
                return httpx.Response(200, text=child_xml, headers={"content-type": "application/xml"})
            if "sitemap.xml" in url:
                return httpx.Response(200, text=index_xml, headers={"content-type": "application/xml"})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {},
            }
            urls, new_sitemap = await discover(board, client)
            assert len(urls) == 1
            assert "https://example.com/jobs/1" in urls


class TestCanHandle:
    async def test_sitemap_found(self):
        sitemap_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/1</loc></url>
        </urlset>"""

        def handler(request):
            url = str(request.url)
            if "sitemap.xml" in url:
                return httpx.Response(200, text=sitemap_xml, headers={"content-type": "application/xml"})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert "sitemap_url" in result

    async def test_no_sitemap(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None
