from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from src.core.monitors import DiscoveredJob
from src.core.monitors.rss import (
    _add_pagination,
    _build_feed_url,
    _g,
    _parse_generic_item,
    _parse_sf_item,
    _parse_tt_item,
    _text,
    _tt,
    _tt_location_string,
    can_handle,
    discover,
)

_G_NS = "http://base.google.com/ns/1.0"
_TT_NS = "https://teamtailor.com/locations"


def _make_item(xml_str: str) -> ET.Element:
    """Wrap an XML string in <item> and return the Element."""
    return ET.fromstring(f"<item>{xml_str}</item>")


# ── _text ────────────────────────────────────────────────────────────────


class TestText:
    def test_child_with_text(self):
        item = _make_item("<title>Engineer</title>")
        assert _text(item, "title") == "Engineer"

    def test_missing_child(self):
        item = _make_item("<link>http://x</link>")
        assert _text(item, "title") is None

    def test_empty_text(self):
        item = _make_item("<title></title>")
        assert _text(item, "title") is None

    def test_whitespace_stripped(self):
        item = _make_item("<title>  Spaced  </title>")
        assert _text(item, "title") == "Spaced"


# ── _g (Google Base namespace) ───────────────────────────────────────────


class TestG:
    def test_basic(self):
        item = _make_item(f'<g:location xmlns:g="{_G_NS}">Berlin</g:location>')
        assert _g(item, "location") == "Berlin"

    def test_missing(self):
        item = _make_item("<title>X</title>")
        assert _g(item, "location") is None

    def test_empty(self):
        item = _make_item(f'<g:location xmlns:g="{_G_NS}"></g:location>')
        assert _g(item, "location") is None


# ── _tt (Teamtailor namespace) ───────────────────────────────────────────


class TestTt:
    def test_basic(self):
        item = _make_item(f'<tt:department xmlns:tt="{_TT_NS}">Sales</tt:department>')
        assert _tt(item, "department") == "Sales"

    def test_missing(self):
        item = _make_item("<title>X</title>")
        assert _tt(item, "department") is None

    def test_empty(self):
        item = _make_item(f'<tt:department xmlns:tt="{_TT_NS}"></tt:department>')
        assert _tt(item, "department") is None


# ── _parse_sf_item (SuccessFactors) ──────────────────────────────────────


class TestParseSfItem:
    def test_basic_item(self):
        xml = f"""
        <item>
            <title>Software Engineer (Berlin, DE)</title>
            <link>https://example.com/job/1</link>
            <description>&lt;p&gt;Great job&lt;/p&gt;</description>
            <guid>JOB-001</guid>
            <g:location xmlns:g="{_G_NS}">Berlin, DE</g:location>
            <g:employer xmlns:g="{_G_NS}">Acme Corp</g:employer>
            <g:job_function xmlns:g="{_G_NS}">Engineering</g:job_function>
            <g:expiration_date xmlns:g="{_G_NS}">2025-12-31</g:expiration_date>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_sf_item(item)
        assert result is not None
        assert result.url == "https://example.com/job/1"
        assert result.title == "Software Engineer"  # Location stripped from title
        assert result.description == "<p>Great job</p>"
        assert result.locations == ["Berlin, DE"]
        assert result.metadata["id"] == "JOB-001"
        assert result.metadata["employer"] == "Acme Corp"
        assert result.metadata["job_function"] == "Engineering"
        assert result.metadata["expiration_date"] == "2025-12-31"

    def test_no_link_returns_none(self):
        xml = "<item><title>No link</title></item>"
        item = ET.fromstring(xml)
        assert _parse_sf_item(item) is None

    def test_location_stripped_from_title(self):
        xml = f"""
        <item>
            <title>Manager (Tempe, AZ, US, 85288)</title>
            <link>https://example.com/job/2</link>
            <g:location xmlns:g="{_G_NS}">Tempe, AZ, US, 85288</g:location>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_sf_item(item)
        assert result.title == "Manager"

    def test_job_function_ats_webform_filtered(self):
        xml = f"""
        <item>
            <link>https://example.com/job/3</link>
            <g:job_function xmlns:g="{_G_NS}">ATS_WEBFORM</g:job_function>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_sf_item(item)
        assert result is not None
        assert result.metadata is None or "job_function" not in (result.metadata or {})

    def test_no_metadata(self):
        xml = """
        <item>
            <link>https://example.com/job/4</link>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_sf_item(item)
        assert result.metadata is None


# ── _tt_location_string ──────────────────────────────────────────────────


class TestTtLocationString:
    def test_name_preferred(self):
        xml = f"""
        <tt:location xmlns:tt="{_TT_NS}">
            <tt:name>Downtown Office</tt:name>
            <tt:city>London</tt:city>
            <tt:country>UK</tt:country>
        </tt:location>
        """
        loc_el = ET.fromstring(xml)
        assert _tt_location_string(loc_el) == "Downtown Office"

    def test_city_country_fallback(self):
        xml = f"""
        <tt:location xmlns:tt="{_TT_NS}">
            <tt:city>Stockholm</tt:city>
            <tt:country>Sweden</tt:country>
        </tt:location>
        """
        loc_el = ET.fromstring(xml)
        assert _tt_location_string(loc_el) == "Stockholm, Sweden"

    def test_city_only(self):
        xml = f"""
        <tt:location xmlns:tt="{_TT_NS}">
            <tt:city>Paris</tt:city>
        </tt:location>
        """
        loc_el = ET.fromstring(xml)
        assert _tt_location_string(loc_el) == "Paris"

    def test_country_only(self):
        xml = f"""
        <tt:location xmlns:tt="{_TT_NS}">
            <tt:country>Germany</tt:country>
        </tt:location>
        """
        loc_el = ET.fromstring(xml)
        assert _tt_location_string(loc_el) == "Germany"

    def test_empty_returns_none(self):
        xml = f'<tt:location xmlns:tt="{_TT_NS}"></tt:location>'
        loc_el = ET.fromstring(xml)
        assert _tt_location_string(loc_el) is None


# ── _parse_tt_item (Teamtailor) ──────────────────────────────────────────


class TestParseTtItem:
    def test_full_item(self):
        xml = f"""
        <item>
            <title>Designer</title>
            <link>https://example.com/jobs/1</link>
            <description>&lt;p&gt;Design stuff&lt;/p&gt;</description>
            <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
            <guid>TT-001</guid>
            <remoteStatus>fully</remoteStatus>
            <tt:department xmlns:tt="{_TT_NS}">Design</tt:department>
            <tt:role xmlns:tt="{_TT_NS}">Senior</tt:role>
            <tt:locations xmlns:tt="{_TT_NS}">
                <tt:location>
                    <tt:name>Stockholm HQ</tt:name>
                </tt:location>
                <tt:location>
                    <tt:city>London</tt:city>
                    <tt:country>UK</tt:country>
                </tt:location>
            </tt:locations>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_tt_item(item)
        assert result is not None
        assert result.url == "https://example.com/jobs/1"
        assert result.title == "Designer"
        assert result.description == "<p>Design stuff</p>"
        assert result.job_location_type == "remote"
        assert result.locations == ["Stockholm HQ", "London, UK"]
        assert result.date_posted == "Mon, 01 Jan 2024 00:00:00 +0000"
        assert result.metadata["department"] == "Design"
        assert result.metadata["role"] == "Senior"
        assert result.metadata["id"] == "TT-001"

    def test_remote_status_hybrid(self):
        xml = """
        <item>
            <link>https://example.com/jobs/2</link>
            <remoteStatus>hybrid</remoteStatus>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_tt_item(item)
        assert result.job_location_type == "hybrid"

    def test_remote_status_none_onsite(self):
        xml = """
        <item>
            <link>https://example.com/jobs/3</link>
            <remoteStatus>none</remoteStatus>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_tt_item(item)
        assert result.job_location_type == "onsite"

    def test_remote_status_onsite(self):
        xml = """
        <item>
            <link>https://example.com/jobs/4</link>
            <remoteStatus>onsite</remoteStatus>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_tt_item(item)
        assert result.job_location_type == "onsite"

    def test_no_link_returns_none(self):
        xml = "<item><title>No link</title></item>"
        item = ET.fromstring(xml)
        assert _parse_tt_item(item) is None

    def test_no_locations(self):
        xml = """
        <item>
            <link>https://example.com/jobs/5</link>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_tt_item(item)
        assert result.locations is None


# ── _parse_generic_item ──────────────────────────────────────────────────


class TestParseGenericItem:
    def test_basic_item(self):
        xml = """
        <item>
            <title>Engineer</title>
            <link>https://example.com/jobs/1</link>
            <description>&lt;p&gt;Work here&lt;/p&gt;</description>
            <pubDate>Tue, 15 Jan 2024 12:00:00 GMT</pubDate>
            <guid>G-001</guid>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_generic_item(item)
        assert result is not None
        assert result.url == "https://example.com/jobs/1"
        assert result.title == "Engineer"
        assert result.description == "<p>Work here</p>"
        assert result.date_posted == "Tue, 15 Jan 2024 12:00:00 GMT"
        assert result.metadata == {"id": "G-001"}

    def test_no_link_returns_none(self):
        xml = "<item><title>No link</title></item>"
        item = ET.fromstring(xml)
        assert _parse_generic_item(item) is None

    def test_no_metadata(self):
        xml = """
        <item>
            <link>https://example.com/jobs/2</link>
        </item>
        """
        item = ET.fromstring(xml)
        result = _parse_generic_item(item)
        assert result.metadata is None


# ── _build_feed_url ──────────────────────────────────────────────────────


class TestBuildFeedUrl:
    def test_basic(self):
        result = _build_feed_url("https://jobs.example.com/careers", "/googlefeed.xml")
        assert result == "https://jobs.example.com/googlefeed.xml"

    def test_with_path(self):
        result = _build_feed_url("https://example.com/careers/page", "/jobs.rss")
        assert result == "https://example.com/jobs.rss"

    def test_preserves_scheme(self):
        result = _build_feed_url("http://example.com/jobs", "/feed.xml")
        assert result == "http://example.com/feed.xml"


# ── _add_pagination ──────────────────────────────────────────────────────


class TestAddPagination:
    def test_adds_params(self):
        result = _add_pagination("https://example.com/jobs.rss", 0, 100)
        assert "offset=0" in result
        assert "per_page=100" in result

    def test_with_offset(self):
        result = _add_pagination("https://example.com/jobs.rss", 200, 100)
        assert "offset=200" in result
        assert "per_page=100" in result

    def test_preserves_existing_params(self):
        result = _add_pagination("https://example.com/jobs.rss?lang=en", 0, 50)
        assert "lang=en" in result
        assert "offset=0" in result
        assert "per_page=50" in result


# ── discover ─────────────────────────────────────────────────────────────


def _rss_xml(items_xml: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>Jobs</title>
            {items_xml}
        </channel>
    </rss>"""


class TestDiscover:
    async def test_successfactors_preset(self):
        feed_xml = _rss_xml(f"""
            <item>
                <title>Engineer (Berlin, DE)</title>
                <link>https://example.com/job/1</link>
                <description>Desc</description>
                <g:location xmlns:g="{_G_NS}">Berlin, DE</g:location>
            </item>
        """)

        def handler(request):
            return httpx.Response(200, text=feed_xml)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.example.com/careers",
                "metadata": {"preset": "successfactors"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert isinstance(jobs[0], DiscoveredJob)
            assert jobs[0].url == "https://example.com/job/1"

    async def test_teamtailor_preset_paginated(self):
        page1_xml = _rss_xml("""
            <item>
                <title>Job 1</title>
                <link>https://example.com/jobs/1</link>
            </item>
        """)

        def handler(request):
            return httpx.Response(200, text=page1_xml)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.teamtailor.com/jobs",
                "metadata": {"preset": "teamtailor"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1

    async def test_generic_preset(self):
        feed_xml = _rss_xml("""
            <item>
                <title>Job A</title>
                <link>https://example.com/jobs/a</link>
                <description>Desc A</description>
            </item>
            <item>
                <title>Job B</title>
                <link>https://example.com/jobs/b</link>
                <description>Desc B</description>
            </item>
        """)

        def handler(request):
            return httpx.Response(200, text=feed_xml)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/feed.xml",
                "metadata": {"preset": "generic", "feed_url": "https://example.com/feed.xml"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2

    async def test_explicit_feed_url(self):
        feed_xml = _rss_xml("""
            <item>
                <link>https://example.com/jobs/1</link>
            </item>
        """)

        def handler(request):
            assert "custom-feed" in str(request.url)
            return httpx.Response(200, text=feed_xml)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {
                    "preset": "successfactors",
                    "feed_url": "https://example.com/custom-feed.xml",
                },
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1

    async def test_empty_feed(self):
        feed_xml = _rss_xml("")

        def handler(request):
            return httpx.Response(200, text=feed_xml)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"preset": "generic", "feed_url": "https://example.com/feed.xml"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0


# ── can_handle ───────────────────────────────────────────────────────────


class TestCanHandle:
    async def test_returns_none_without_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_successfactors_in_page(self):
        rss_xml = _rss_xml("""
            <item>
                <link>https://example.com/job/1</link>
            </item>
        """)

        def handler(request):
            url = str(request.url)
            if "googlefeed.xml" in url:
                return httpx.Response(200, text=rss_xml)
            return httpx.Response(
                200,
                text='<html><script src="https://rmkcdn.successfactors.com/x.js"></script></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["preset"] == "successfactors"

    async def test_detects_teamtailor_in_page(self):
        rss_xml = _rss_xml("""
            <item>
                <link>https://example.com/job/1</link>
            </item>
        """)

        def handler(request):
            url = str(request.url)
            if "jobs.rss" in url:
                return httpx.Response(200, text=rss_xml)
            return httpx.Response(
                200,
                text='<html><link href="https://cdn.teamtailor-cdn.com/style.css"></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["preset"] == "teamtailor"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "googlefeed.xml" in url or "jobs.rss" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>plain page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None

    async def test_blind_probe_fallback(self):
        """Feed exists at known path even though no patterns found in HTML."""
        rss_xml = _rss_xml("""
            <item>
                <link>https://example.com/job/1</link>
            </item>
        """)

        def handler(request):
            url = str(request.url)
            if "googlefeed.xml" in url:
                return httpx.Response(200, text=rss_xml)
            return httpx.Response(200, text="<html>no ats markers</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["preset"] == "successfactors"
