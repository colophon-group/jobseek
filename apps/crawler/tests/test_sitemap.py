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

    async def test_sends_bot_user_agent(self):
        # Sitemaps are canonically bot-facing. The shared HTTP client uses a
        # Chrome UA to evade bot detection on HTML, but Meta (metacareers.com)
        # inverts the gate and returns 400 to browser UAs. The sitemap monitor
        # must override with a self-identifying crawler UA.
        seen_headers: dict[str, str] = {}

        def handler(request):
            seen_headers.update(request.headers)
            return httpx.Response(
                200, text="<urlset/>", headers={"content-type": "application/xml"}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": "Mozilla/5.0 Chrome/131.0.0.0"},  # sim shared client default
        ) as client:
            await _try_fetch_xml("https://example.com/sitemap.xml", client)

        assert "jobseek-crawler" in seen_headers["user-agent"].lower()
        assert "chrome" not in seen_headers["user-agent"].lower()


class TestFetchChildXml:
    """Strict fetch for child sitemaps inside an index — raises on
    persistent transient errors so silent shard truncation (#2722)
    can't tombstone URLs."""

    async def test_success(self):
        from src.core.monitors.sitemap import _fetch_child_xml

        xml_str = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/j1</loc></url></urlset>'

        def handler(request):
            return httpx.Response(200, text=xml_str)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            root = await _fetch_child_xml("https://example.com/sitemap-1.xml", client)
            assert root is not None

    async def test_404_returns_none(self):
        """Genuinely-missing shard — return None so the caller skips
        without flagging the run as a failure."""
        from src.core.monitors.sitemap import _fetch_child_xml

        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            root = await _fetch_child_xml("https://example.com/sitemap-deleted.xml", client)
            assert root is None

    async def test_503_raises_after_retries(self, monkeypatch):
        """Transient 503 exhausts retries → propagate
        ``PaginationFetchError`` rather than silently returning None
        (the 2026-04-26 NHS spike root cause). ``asyncio.sleep`` is
        patched to a no-op so the test runs in milliseconds rather
        than waiting on real backoff jitter (~2s with defaults).
        """
        import asyncio

        import pytest

        from src.core.monitors.sitemap import _fetch_child_xml
        from src.shared.http_retry import PaginationFetchError

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc:
                await _fetch_child_xml("https://example.com/sitemap-flaky.xml", client)
            assert exc.value.last_status == 503
            assert attempts == 3

    async def test_non_xml_body_returns_none(self):
        """200 OK with HTML body (e.g., CDN serving a not-found page) —
        return None as a benign skip rather than raising."""
        from src.core.monitors.sitemap import _fetch_child_xml

        def handler(request):
            return httpx.Response(200, text="<html>not a sitemap</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            root = await _fetch_child_xml("https://example.com/sitemap-html.xml", client)
            assert root is None

    async def test_403_raises_after_retries(self, monkeypatch):
        """WAF/anti-bot 403 on a child shard exhausts retries →
        propagate ``PaginationFetchError`` (#2994). mchire's awselb/2.0
        was 403'ing 18-44%% of phenom child shards per cycle from the
        production Webshare egress while the index returned 200; the
        original ``transient_403=False`` default would have returned
        ``None`` and let ``_collect_urls`` silently drop the shard's
        URLs, tombstoning thousands of postings via
        ``_MARK_GONE_BY_TIMESTAMP`` — same shape as the 5xx leg of
        #2722 / #2974, just on the 4xx side of the WAF response.
        """
        import asyncio

        import pytest

        from src.core.monitors.sitemap import _fetch_child_xml
        from src.shared.http_retry import PaginationFetchError

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        attempts = 0

        # awselb/2.0 403 body shape — generic forbidden HTML, no
        # WWW-Authenticate header. Verifies we don't accidentally
        # gate on body inspection.
        forbidden_html = (
            "<html><head><title>403 Forbidden</title></head>"
            "<body><center><h1>403 Forbidden</h1></center></body></html>"
        )

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                403,
                text=forbidden_html,
                headers={"server": "awselb/2.0"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc:
                await _fetch_child_xml("https://example.com/sitemap-waf-blocked.xml", client)
            assert exc.value.last_status == 403
            assert attempts == 3

    async def test_403_recovers_within_budget(self, monkeypatch):
        """Transient 403 that clears within the retry budget returns
        the parsed shard — shows the retry path actually delivers
        recovery, not just hard-failure escalation. WAF blocks tied
        to per-IP burst limits often clear after a back-off interval.
        """
        import asyncio

        from src.core.monitors.sitemap import _fetch_child_xml

        async def _no_sleep(_):
            return None

        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        xml_str = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/j1</loc></url></urlset>'
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                return httpx.Response(403)
            return httpx.Response(200, text=xml_str)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            root = await _fetch_child_xml("https://example.com/sitemap-flaky.xml", client)
            assert root is not None
            assert attempts == 2


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
