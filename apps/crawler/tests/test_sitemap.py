from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from src.core.monitors.sitemap import (
    _common_nonstandard_candidates,
    _detect_ns,
    _extract_child_sitemaps,
    _extract_urls,
    _is_job_related,
    _is_sitemap_index,
    _parse_robots_sitemaps,
    _strip_utm,
    _try_fetch_xml,
    _walk_up_candidates,
    can_handle,
    discover,
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

    def test_https_namespace_variant(self):
        # Some generators (e.g. TalentsConnect Job Shop) emit https:// instead
        # of http:// in the xmlns declaration — the parser must handle this.
        xml_str = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="https://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/offer/job-one/abc-123</loc></url>
            <url><loc>https://example.com/offer/job-two/def-456</loc></url>
        </urlset>"""
        root = ET.fromstring(xml_str)
        urls = _extract_urls(root)
        assert len(urls) == 2
        assert "https://example.com/offer/job-one/abc-123" in urls
        assert "https://example.com/offer/job-two/def-456" in urls


class TestDetectNs:
    def test_http_namespace(self):
        xml_str = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>"""
        root = ET.fromstring(xml_str)
        assert _detect_ns(root) == "{http://www.sitemaps.org/schemas/sitemap/0.9}"

    def test_https_namespace(self):
        xml_str = """<urlset xmlns="https://www.sitemaps.org/schemas/sitemap/0.9"/>"""
        root = ET.fromstring(xml_str)
        assert _detect_ns(root) == "{https://www.sitemaps.org/schemas/sitemap/0.9}"

    def test_no_namespace(self):
        xml_str = """<urlset/>"""
        root = ET.fromstring(xml_str)
        # Falls back to canonical NS constant
        assert _detect_ns(root) == "{http://www.sitemaps.org/schemas/sitemap/0.9}"


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
            return httpx.Response(
                200,
                text=sitemap_xml,
                headers={"content-type": "application/xml"},
            )

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
                return httpx.Response(
                    200,
                    text=sitemap_xml,
                    headers={"content-type": "application/xml"},
                )
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
                return httpx.Response(
                    200,
                    text=sitemap_xml,
                    headers={"content-type": "application/xml"},
                )
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
                return httpx.Response(
                    200,
                    text=child_xml,
                    headers={"content-type": "application/xml"},
                )
            if "sitemap.xml" in url:
                return httpx.Response(
                    200,
                    text=index_xml,
                    headers={"content-type": "application/xml"},
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {},
            }
            urls, new_sitemap = await discover(board, client)
            assert len(urls) == 1
            assert "https://example.com/jobs/1" in urls

    async def test_resolves_nested_sitemap_index(self):
        index_xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/careers-sitemap.xml</loc></sitemap>
        </sitemapindex>"""

        nested_index_xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/jobs-1.xml</loc></sitemap>
            <sitemap><loc>https://example.com/jobs-2.xml</loc></sitemap>
        </sitemapindex>"""

        child_one_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/1</loc></url>
        </urlset>"""

        child_two_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/2</loc></url>
        </urlset>"""

        def handler(request):
            url = str(request.url)
            if "careers-sitemap.xml" in url:
                return httpx.Response(
                    200,
                    text=nested_index_xml,
                    headers={"content-type": "application/xml"},
                )
            if "jobs-1.xml" in url:
                return httpx.Response(
                    200,
                    text=child_one_xml,
                    headers={"content-type": "application/xml"},
                )
            if "jobs-2.xml" in url:
                return httpx.Response(
                    200,
                    text=child_two_xml,
                    headers={"content-type": "application/xml"},
                )
            if "sitemap.xml" in url:
                return httpx.Response(
                    200,
                    text=index_xml,
                    headers={"content-type": "application/xml"},
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {},
            }
            urls, _ = await discover(board, client)
            assert urls == {
                "https://example.com/jobs/1",
                "https://example.com/jobs/2",
            }


class TestCanHandle:
    async def test_sitemap_found(self):
        sitemap_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/1</loc></url>
        </urlset>"""

        def handler(request):
            url = str(request.url)
            if "sitemap.xml" in url:
                return httpx.Response(
                    200,
                    text=sitemap_xml,
                    headers={"content-type": "application/xml"},
                )
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

    async def test_nested_sitemap_index_counts_leaf_urls(self):
        index_xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/careers-sitemap.xml</loc></sitemap>
        </sitemapindex>"""

        nested_index_xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/jobs-1.xml</loc></sitemap>
            <sitemap><loc>https://example.com/jobs-2.xml</loc></sitemap>
        </sitemapindex>"""

        child_one_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/1</loc></url>
        </urlset>"""

        child_two_xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/jobs/2</loc></url>
        </urlset>"""

        def handler(request):
            url = str(request.url)
            if "careers-sitemap.xml" in url:
                return httpx.Response(
                    200,
                    text=nested_index_xml,
                    headers={"content-type": "application/xml"},
                )
            if "jobs-1.xml" in url:
                return httpx.Response(
                    200,
                    text=child_one_xml,
                    headers={"content-type": "application/xml"},
                )
            if "jobs-2.xml" in url:
                return httpx.Response(
                    200,
                    text=child_two_xml,
                    headers={"content-type": "application/xml"},
                )
            if "sitemap.xml" in url:
                return httpx.Response(
                    200,
                    text=index_xml,
                    headers={"content-type": "application/xml"},
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result == {
                "sitemap_url": "https://example.com/careers/sitemap.xml",
                "urls": 2,
            }
