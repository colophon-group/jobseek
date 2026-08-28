from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock

import httpx
import pytest

import src.core.monitors.rss as rss_monitor
from src.core.monitor import monitor_one
from src.core.monitors import DiscoveredJob
from src.core.monitors.rss import (
    RssFeedNotXml,
    _add_pagination,
    _advertised_rss_feed_url,
    _build_feed_url,
    _g,
    _parse_feed,
    _parse_generic_item,
    _parse_sf_item,
    _parse_tt_item,
    _text,
    _tt,
    _tt_location_string,
    can_handle,
    discover,
    discover_stream,
)
from src.shared.http_retry import PaginationFetchError

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

    def test_location_falls_back_to_labelled_description(self):
        xml = """
        <item>
            <title>Project Manager P4 (Luanda, AO)</title>
            <link>https://jobs.example.com/job/1</link>
            <description><![CDATA[
                <p><strong>Job ID:</strong> 13784<br>
                <strong>Location:</strong> Luanda&nbsp;&nbsp;&nbsp;<br>
                <strong>Contract type:</strong> Short Term</p>
                <p>Full job description.</p>
            ]]></description>
            <pubDate>Tue, 18 Aug 2026 00:00:00 GMT</pubDate>
        </item>
        """

        result = _parse_sf_item(ET.fromstring(xml))

        assert result is not None
        assert result.title == "Project Manager P4"
        assert result.locations == ["Luanda, AO"]
        assert result.date_posted == "Tue, 18 Aug 2026 00:00:00 GMT"

    def test_description_location_does_not_strip_unrelated_title_suffix(self):
        xml = """
        <item>
            <title>Specialist (Research, AI)</title>
            <link>https://jobs.example.com/job/2</link>
            <description><![CDATA[
                <p><strong>Location:</strong> Geneva<br>
                <strong>Contract type:</strong> Fixed Term</p>
            ]]></description>
        </item>
        """

        result = _parse_sf_item(ET.fromstring(xml))

        assert result is not None
        assert result.title == "Specialist (Research, AI)"
        assert result.locations == ["Geneva"]

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

    def test_vendor_location_and_job_id_extensions(self):
        item = _make_item(
            """
            <title />
            <link>https://example.com/?page=advertisement_display&amp;id=458</link>
            <JobID>458</JobID>
            <Location>Lausanne</Location>
            """
        )

        result = _parse_generic_item(item)

        assert result is not None
        assert result.title is None
        assert result.locations == ["Lausanne"]
        assert result.metadata == {"id": "458"}

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


class TestAdvertisedRssFeedUrl:
    def test_resolves_relative_feed(self):
        html = '<link rel="alternate" type="application/rss+xml" href="/careers/feed.xml">'

        assert _advertised_rss_feed_url("https://example.com/jobs", html) == (
            "https://example.com/careers/feed.xml"
        )

    def test_upgrades_same_host_legacy_http_feed(self):
        html = '<link rel="alternate" type="application/rss+xml" href="http://example.com/rss.php">'

        assert _advertised_rss_feed_url("https://example.com/jobs", html) == (
            "https://example.com/rss.php"
        )

    def test_rejects_cross_origin_feed(self):
        html = (
            '<link rel="alternate" type="application/rss+xml" '
            'href="https://attacker.example/rss.php">'
        )

        assert _advertised_rss_feed_url("https://example.com/jobs", html) is None

    def test_rejects_same_host_different_port(self):
        html = (
            '<link rel="alternate" type="application/rss+xml" '
            'href="https://example.com:8443/rss.php">'
        )

        assert _advertised_rss_feed_url("https://example.com/jobs", html) is None

    def test_rejects_malformed_port(self):
        html = (
            '<link rel="alternate" type="application/rss+xml" '
            'href="https://example.com:not-a-port/rss.php">'
        )

        assert _advertised_rss_feed_url("https://example.com/jobs", html) is None


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
    async def test_successfactors_job_identity_collapses_locale_and_title_aliases(self):
        feed_xml = _rss_xml("""
            <item>
                <title>Deutscher Titel</title>
                <link>https://jobs.example.com/job/alter-deutscher-titel/1001/</link>
                <guid>1001</guid>
            </item>
            <item>
                <title>Titre français</title>
                <link>https://jobs.example.com/job/ancien-titre-francais/1002/</link>
                <guid>1002</guid>
            </item>
        """)

        def handler(request):
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            locations = {
                "/job/alter-deutscher-titel/1001/": "/job/Neuer-Titel/9580-de_DE/",
                "/job/ancien-titre-francais/1002/": "/job/Nouveau-Titre/9580-fr_FR/",
            }
            return httpx.Response(302, headers={"location": locations[request.url.path]})

        config = {
            "preset": "successfactors",
            "feed_url": "https://jobs.example.com/googlefeed.xml",
            "resolve_job_invite_identity": True,
            "url_allowlist": (
                r"^https://jobs\.example\.com/job/[^/?#]+/"
                r"[1-9]\d{0,11}-[a-z]{2}_[A-Z]{2}/$"
            ),
            "url_transform": {
                "find": (
                    r"^https://jobs\.example\.com/job/[^/?#]+/"
                    r"([1-9]\d{0,11})-[a-z]{2}_[A-Z]{2}/$"
                ),
                "replace": r"https://jobs.example.com/job-invite/\1/",
                "collision_policy": "prefer_source_pattern",
                "collision_preferred_source_patterns": ["-de_DE/$", "-fr_FR/$"],
                "collision_canonical_identity_regex": (
                    r"^https://jobs\.example\.com/job-invite/([1-9]\d{0,11})/$"
                ),
                "collision_identity_metadata_key": "job_invite_id",
                "collision_stream_buffer_limit": 10,
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await monitor_one(
                "https://jobs.example.com/search/",
                "rss",
                config,
                client,
            )

        assert result.urls == {"https://jobs.example.com/job-invite/9580/"}
        job = result.jobs_by_url["https://jobs.example.com/job-invite/9580/"]
        assert job.title == "Deutscher Titel"
        assert job.metadata == {
            "id": "1001",
            "feed_id": "1001",
            "job_invite_id": "9580",
            "job_locale": "de_DE",
        }

    async def test_successfactors_job_identity_rejects_cross_origin_redirect(self):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://jobs.example.com/job/pilot/1001/</link>
            </item>
        """)

        def handler(request):
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            return httpx.Response(
                302,
                headers={"location": "https://evil.example/job/Pilot/9580-de_DE/"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(rss_monitor.SuccessFactorsJobIdentityError):
                await discover(
                    {
                        "board_url": "https://jobs.example.com/search/",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://jobs.example.com/googlefeed.xml",
                            "resolve_job_invite_identity": True,
                        },
                    },
                    client,
                )

    @pytest.mark.parametrize("transient_status", [403, 429, 503])
    async def test_successfactors_job_identity_retries_transient_statuses(
        self,
        transient_status,
        monkeypatch,
    ):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://jobs.example.com/job/pilot/1001/</link>
            </item>
        """)
        attempts = 0

        def handler(request):
            nonlocal attempts
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            attempts += 1
            if attempts == 1:
                return httpx.Response(transient_status)
            return httpx.Response(
                302,
                headers={"location": "/job/Pilot/9580-en_US/"},
            )

        sleep = AsyncMock()
        monkeypatch.setattr(rss_monitor, "_sleep", sleep)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://jobs.example.com/search/",
                    "metadata": {
                        "preset": "successfactors",
                        "feed_url": "https://jobs.example.com/googlefeed.xml",
                        "resolve_job_invite_identity": True,
                    },
                },
                client,
            )

        assert attempts == 2
        sleep.assert_awaited_once()
        assert jobs[0].url == "https://jobs.example.com/job/Pilot/9580-en_US/"

    async def test_successfactors_job_identity_enforces_bounded_feed_cap(self, monkeypatch):
        feed_xml = _rss_xml("""
            <item><link>https://jobs.example.com/job/one/1001/</link></item>
            <item><link>https://jobs.example.com/job/two/1002/</link></item>
        """)
        monkeypatch.setattr(rss_monitor, "_SF_JOB_IDENTITY_MAX_JOBS", 1)

        def handler(request):
            return httpx.Response(200, text=feed_xml)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(rss_monitor.SuccessFactorsJobIdentityError, match="bounded"):
                await discover(
                    {
                        "board_url": "https://jobs.example.com/search/",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://jobs.example.com/googlefeed.xml",
                            "resolve_job_invite_identity": True,
                        },
                    },
                    client,
                )

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

    async def test_successfactors_title_placeholder_is_not_a_description(self):
        feed_xml = _rss_xml(f"""
            <item>
                <title>Claims Specialist</title>
                <link>https://example.com/job/1</link>
                <description><![CDATA[Claims Specialist]]></description>
                <g:location xmlns:g="{_G_NS}">CH</g:location>
            </item>
        """)

        def handler(request):
            return httpx.Response(200, text=feed_xml)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://jobs.example.com/careers",
                    "metadata": {"preset": "successfactors"},
                },
                client,
            )

        assert jobs[0].description is None

    async def test_successfactors_can_enrich_legal_employer(self):
        feed_xml = _rss_xml(f"""
            <item>
                <title>Pilot (Lisbon, PT)</title>
                <link>https://example.com/job/1</link>
                <description>Full description</description>
                <g:location xmlns:g="{_G_NS}">Lisbon, PT</g:location>
            </item>
        """)

        def handler(request):
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            return httpx.Response(
                200,
                text=(
                    '<h1 data-careersite-propertyid="title">Pilot</h1>'
                    '<span data-careersite-propertyid="customfield1">'
                    "Executive Jet Management (Europe) Limited</span>"
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://example.com/careers",
                    "metadata": {
                        "preset": "successfactors",
                        "feed_url": "https://example.com/googlefeed.xml",
                        "fetch_company": True,
                    },
                },
                client,
            )

        assert jobs[0].metadata["company"] == "Executive Jet Management (Europe) Limited"

    async def test_successfactors_can_enrich_required_detail_fields(self):
        feed_xml = _rss_xml(f"""
            <item>
                <title>Juriste (Fribourg, CH)</title>
                <link>https://example.com/job/fr/1001/</link>
                <guid>1001</guid>
                <description>Full description</description>
                <g:location xmlns:g="{_G_NS}">Fribourg, CH</g:location>
            </item>
        """)

        def handler(request):
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            return httpx.Response(
                200,
                text=(
                    '<h1 data-careersite-propertyid="title">Juriste</h1>'
                    '<span data-careersite-propertyid="dept">Service cantonal</span>'
                    '<span data-careersite-propertyid="adcode">10444</span>'
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://example.com/careers",
                    "metadata": {
                        "preset": "successfactors",
                        "feed_url": "https://example.com/googlefeed.xml",
                        "detail_fields": {"service": "dept", "adcode": "adcode"},
                    },
                },
                client,
            )

        assert jobs[0].metadata["service"] == "Service cantonal"
        assert jobs[0].metadata["adcode"] == "10444"

    async def test_successfactors_required_detail_field_fails_closed(self):
        feed_xml = _rss_xml("""
            <item>
                <title>Juriste</title>
                <link>https://example.com/job/fr/1001/</link>
                <guid>1001</guid>
                <description>Full description</description>
            </item>
        """)

        def handler(request):
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            return httpx.Response(
                200,
                text='<h1 data-careersite-propertyid="title">Juriste</h1>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="omitted required property 'adcode'"):
                await discover(
                    {
                        "board_url": "https://example.com/careers",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://example.com/googlefeed.xml",
                            "detail_fields": {"adcode": "adcode"},
                        },
                    },
                    client,
                )

    async def test_successfactors_rejects_unsafe_detail_field_selector(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ) as client:
            with pytest.raises(ValueError, match="Invalid SuccessFactors detail property"):
                await discover(
                    {
                        "board_url": "https://example.com/careers",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://example.com/googlefeed.xml",
                            "detail_fields": {"service": 'dept"] *'},
                        },
                    },
                    client,
                )

    async def test_successfactors_enriches_only_url_filtered_jobs(self):
        feed_xml = _rss_xml(f"""
            <item>
                <title>European Pilot</title>
                <link>https://example.com/europe/job/1</link>
                <g:location xmlns:g="{_G_NS}">Lisbon, PT</g:location>
            </item>
            <item>
                <title>US Pilot</title>
                <link>https://example.com/us/job/2</link>
                <g:location xmlns:g="{_G_NS}">Columbus, OH</g:location>
            </item>
        """)
        requested_paths: list[str] = []

        def handler(request):
            requested_paths.append(request.url.path)
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            return httpx.Response(
                200,
                text=(
                    '<h1 data-careersite-propertyid="title">Pilot</h1>'
                    '<span data-careersite-propertyid="customfield1">NetJets</span>'
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await discover(
                {
                    "board_url": "https://example.com/europe",
                    "metadata": {
                        "preset": "successfactors",
                        "feed_url": "https://example.com/googlefeed.xml",
                        "fetch_company": True,
                        "url_filter": "/europe/job/",
                    },
                },
                client,
            )

        assert requested_paths == ["/googlefeed.xml", "/europe/job/1"]

    async def test_successfactors_company_enrichment_rejects_off_origin_url(self):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://other.example/job/1</link>
            </item>
        """)

        def handler(request):
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            raise AssertionError("off-origin detail URL must not be fetched")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="rejected URL"):
                await discover(
                    {
                        "board_url": "https://example.com/careers",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://example.com/googlefeed.xml",
                            "fetch_company": True,
                        },
                    },
                    client,
                )

    async def test_successfactors_company_enrichment_follows_same_origin_redirect(self):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://example.com/job/1</link>
            </item>
        """)
        requested_paths: list[str] = []

        def handler(request):
            requested_paths.append(request.url.path)
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            if request.url.path == "/job/1":
                return httpx.Response(302, headers={"location": "/job/1/detail"})
            return httpx.Response(
                200,
                text=(
                    '<h1 data-careersite-propertyid="title">Pilot</h1>'
                    '<span data-careersite-propertyid="customfield1">NetJets</span>'
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://example.com/careers",
                    "metadata": {
                        "preset": "successfactors",
                        "feed_url": "https://example.com/googlefeed.xml",
                        "fetch_company": True,
                    },
                },
                client,
            )

        assert jobs[0].metadata["company"] == "NetJets"
        assert requested_paths == ["/googlefeed.xml", "/job/1", "/job/1/detail"]

    async def test_successfactors_company_enrichment_rejects_off_origin_redirect(self):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://example.com/job/1</link>
            </item>
        """)
        requested_hosts: list[str] = []

        def handler(request):
            requested_hosts.append(request.url.host)
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            return httpx.Response(302, headers={"location": "https://other.example/job/1"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="rejected redirect"):
                await discover(
                    {
                        "board_url": "https://example.com/careers",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://example.com/googlefeed.xml",
                            "fetch_company": True,
                        },
                    },
                    client,
                )

        assert requested_hosts == ["example.com", "example.com"]

    async def test_successfactors_company_enrichment_rejects_invalid_html_page(self, monkeypatch):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://example.com/job/1</link>
            </item>
        """)

        detail_requests = 0

        def handler(request):
            nonlocal detail_requests
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            detail_requests += 1
            return httpx.Response(200, text="<html><h1>Access denied</h1></html>")

        async def no_sleep(_delay):
            return None

        monkeypatch.setattr(rss_monitor, "_sleep", no_sleep)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(rss_monitor.SuccessFactorsDetailPageError) as exc_info:
                await discover(
                    {
                        "board_url": "https://example.com/careers",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://example.com/googlefeed.xml",
                            "fetch_company": True,
                        },
                    },
                    client,
                )

        assert exc_info.value.classification == "missing_job_marker"
        assert exc_info.value.attempts == 3
        assert detail_requests == 3

    async def test_successfactors_company_enrichment_retries_transient_status(self, monkeypatch):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://example.com/job/1</link>
            </item>
        """)
        detail_requests = 0

        def handler(request):
            nonlocal detail_requests
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            detail_requests += 1
            if detail_requests == 1:
                return httpx.Response(503)
            return httpx.Response(
                200,
                text=(
                    '<h1 data-careersite-propertyid="title">Pilot</h1>'
                    '<span data-careersite-propertyid="customfield1">NetJets</span>'
                ),
            )

        async def no_sleep(_delay):
            return None

        monkeypatch.setattr(rss_monitor, "_sleep", no_sleep)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://example.com/careers",
                    "metadata": {
                        "preset": "successfactors",
                        "feed_url": "https://example.com/googlefeed.xml",
                        "fetch_company": True,
                    },
                },
                client,
            )

        assert jobs[0].metadata["company"] == "NetJets"
        assert detail_requests == 2

    async def test_successfactors_company_enrichment_fails_closed(self, monkeypatch):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://example.com/job/1</link>
                <description>Full description</description>
            </item>
        """)

        detail_requests = 0

        def handler(request):
            nonlocal detail_requests
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            detail_requests += 1
            return httpx.Response(503)

        async def no_sleep(_delay):
            return None

        monkeypatch.setattr(rss_monitor, "_sleep", no_sleep)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(
                    {
                        "board_url": "https://example.com/careers",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://example.com/googlefeed.xml",
                            "fetch_company": True,
                        },
                    },
                    client,
                )

        assert exc_info.value.last_status == 503
        assert exc_info.value.attempts == 3
        assert detail_requests == 3

    async def test_successfactors_company_enrichment_classifies_challenge(self, monkeypatch):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://example.com/job/1</link>
            </item>
        """)
        detail_requests = 0

        def handler(request):
            nonlocal detail_requests
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            detail_requests += 1
            return httpx.Response(200, text="<html><title>Just a moment...</title></html>")

        async def no_sleep(_delay):
            return None

        monkeypatch.setattr(rss_monitor, "_sleep", no_sleep)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(rss_monitor.SuccessFactorsDetailPageError) as exc_info:
                await discover(
                    {
                        "board_url": "https://example.com/careers",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://example.com/googlefeed.xml",
                            "fetch_company": True,
                        },
                    },
                    client,
                )

        assert exc_info.value.classification == "bot_challenge"
        assert exc_info.value.attempts == 3
        assert detail_requests == 3

    async def test_successfactors_company_enrichment_preserves_permanent_status(self):
        feed_xml = _rss_xml("""
            <item>
                <title>Pilot</title>
                <link>https://example.com/job/1</link>
            </item>
        """)
        detail_requests = 0

        def handler(request):
            nonlocal detail_requests
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed_xml)
            detail_requests += 1
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(
                    {
                        "board_url": "https://example.com/careers",
                        "metadata": {
                            "preset": "successfactors",
                            "feed_url": "https://example.com/googlefeed.xml",
                            "fetch_company": True,
                        },
                    },
                    client,
                )

        assert exc_info.value.last_status == 404
        assert exc_info.value.attempts == 1
        assert detail_requests == 1

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

    async def test_wp_job_manager_preset_uses_numbered_pages(self):
        first_page = _rss_xml(
            "".join(
                f"<item><title>Job {job_id}</title>"
                f"<link>https://example.com/job/{job_id}</link></item>"
                for job_id in range(1, 11)
            )
        )
        second_page = _rss_xml("""
            <item>
                <title>Job 11</title>
                <link>https://example.com/job/11</link>
            </item>
            <item>
                <title>Job 12</title>
                <link>https://example.com/job/12</link>
            </item>
        """)
        requested_pages = []

        def handler(request):
            requested_pages.append(request.url.params.get("paged"))
            body = first_page if request.url.params["paged"] == "1" else second_page
            return httpx.Response(200, text=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://example.com/open-positions/",
                    "metadata": {"preset": "wp_job_manager"},
                },
                client,
            )

        assert len(jobs) == 12
        assert requested_pages == ["1", "2"]

    async def test_wp_job_manager_repeated_full_page_fails_closed(self):
        full_page = _rss_xml(
            "".join(
                f"<item><title>Job {job_id}</title>"
                f"<link>https://example.com/job/{job_id}</link></item>"
                for job_id in range(1, 11)
            )
        )

        def handler(_request):
            return httpx.Response(200, text=full_page)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError, match="RepeatedPaginatedFeedPage"):
                await discover(
                    {
                        "board_url": "https://example.com/open-positions/",
                        "metadata": {"preset": "wp_job_manager"},
                    },
                    client,
                )

    async def test_teamtailor_transient_400_retries_same_page(self, monkeypatch):
        feed_xml = _rss_xml("""
            <item>
                <title>Recovered job</title>
                <link>https://example.com/jobs/1</link>
            </item>
        """)
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(400, text="temporary provider error")
            return httpx.Response(200, text=feed_xml)

        async def no_sleep(_delay):
            return None

        monkeypatch.setattr(rss_monitor, "_sleep", no_sleep)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.teamtailor.com/jobs",
                "metadata": {"preset": "teamtailor"},
            }
            jobs = await discover(board, client)

        assert [job.title for job in jobs] == ["Recovered job"]
        assert attempts == 2

    async def test_generic_transient_408_retries_same_feed(self, monkeypatch):
        feed_xml = _rss_xml("""
            <item>
                <title>Recovered job</title>
                <link>https://example.com/jobs/1</link>
            </item>
        """)
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(408, text="request timeout")
            return httpx.Response(200, text=feed_xml)

        async def no_sleep(_delay):
            return None

        monkeypatch.setattr(rss_monitor, "_sleep", no_sleep)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"preset": "generic", "feed_url": "https://example.com/feed.xml"},
            }
            jobs = await discover(board, client)

        assert [job.title for job in jobs] == ["Recovered job"]
        assert attempts == 2

    async def test_retired_feed_404_is_failure_not_empty_success(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(404, text="retired")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"preset": "generic", "feed_url": "https://example.com/feed.xml"},
            }
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(board, client)

        assert exc_info.value.last_status == 404
        assert attempts == 1

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

    async def test_stream_yields_batch_before_response_finishes(self):
        class TwoChunkStream(httpx.AsyncByteStream):
            finished = False

            async def __aiter__(self):
                item = (
                    "<item><title>Engineer</title>"
                    "<link>https://example.com/jobs/{}</link>"
                    f"<description>{'x' * 500}</description></item>"
                )
                yield (
                    '<?xml version="1.0"?><rss><channel>'
                    + "".join(item.format(i) for i in range(200))
                    + f"<!--{'padding' * 10_000}-->"
                ).encode()
                yield (item.format(200) + "</channel></rss>").encode()
                self.finished = True

        source = TwoChunkStream()

        def handler(request):
            return httpx.Response(200, stream=source)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {
                    "preset": "generic",
                    "feed_url": "https://example.com/feed.xml",
                },
            }
            batches = discover_stream(board, client)
            first = await anext(batches)
            assert len(first) == 200
            assert source.finished is False
            await batches.aclose()

    async def test_stream_marks_max_jobs_as_truncated(self, monkeypatch):
        feed_xml = _rss_xml(
            "".join(f"<item><link>https://example.com/jobs/{i}</link></item>" for i in range(4))
        )

        def handler(request):
            return httpx.Response(200, text=feed_xml)

        monkeypatch.setattr(rss_monitor, "MAX_JOBS", 3)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {
                    "preset": "generic",
                    "feed_url": "https://example.com/feed.xml",
                },
            }
            batches = [batch async for batch in discover_stream(board, client)]

        assert len(batches) == 1
        assert batches[0].truncated is True
        assert len(batches[0].urls) == 3


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

    async def test_wp_job_manager_jobs_feed_precedes_wordpress_post_feed(self):
        rss_xml = _rss_xml("""
            <item>
                <title>Nurse</title>
                <link>https://example.com/job/nurse/</link>
            </item>
        """)

        def handler(request):
            if request.url.params.get("feed") == "job_feed":
                return httpx.Response(200, text=rss_xml)
            if request.url.path == "/feed/":
                pytest.fail("the site-wide WordPress feed must not be probed")
            return httpx.Response(
                200,
                text=(
                    '<html><head><link rel="alternate" type="application/rss+xml" '
                    'href="/feed/"></head><body>'
                    '<script src="/wp-content/plugins/wp-job-manager/assets/job-listings.js">'
                    "</script></body></html>"
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/open-positions/", client)

        assert result == {
            "preset": "wp_job_manager",
            "feed_url": "https://example.com/?feed=job_feed",
            "jobs": 1,
        }

    async def test_detects_advertised_generic_feed(self):
        rss_xml = _rss_xml(
            """
            <item>
                <link>https://example.com/job/1</link>
                <Location>Lausanne</Location>
            </item>
            """
        )

        def handler(request):
            if request.url.path == "/rss.php":
                return httpx.Response(200, text=rss_xml)
            return httpx.Response(
                200,
                text=(
                    '<html><head><link rel="alternate" type="application/rss+xml" '
                    'href="/rss.php"></head></html>'
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)

        assert result == {
            "preset": "generic",
            "feed_url": "https://example.com/rss.php",
            "jobs": 1,
        }

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


# ── _parse_feed (non-XML sniff) ──────────────────────────────────────────


class TestParseFeed:
    def test_accepts_rss(self):
        root = _parse_feed(_rss_xml(""), "https://x/feed")
        assert root.tag == "rss"

    def test_accepts_xml_prolog(self):
        root = _parse_feed(
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>',
            "https://x/feed",
        )
        assert root.tag.endswith("feed")

    def test_rejects_html_landing_page(self):
        # The real Givaudan failure: a redirect target returns a Phenom People
        # HTML page with a huge <script> that ET misreports as a line-17 parse
        # error deep inside JavaScript code.
        html = '<!DOCTYPE html>\n<html lang="en"><head><title>Careers</title></head></html>'
        with pytest.raises(RssFeedNotXml):
            _parse_feed(html, "https://jobs.example.com/googlefeed.xml")

    def test_rejects_plaintext_disabled_message(self):
        # The other Givaudan variant: careers.givaudan.com/googlefeed.xml
        # returns "Message - This feed is disabled" with 200 OK.
        with pytest.raises(RssFeedNotXml):
            _parse_feed("Message - This feed is disabled", "https://x/feed")

    def test_rejects_empty(self):
        with pytest.raises(RssFeedNotXml):
            _parse_feed("", "https://x/feed")


class TestDiscoverRejectsNonXml:
    async def test_html_response_raises_named_error(self):
        """discover() must surface a named error, not a cryptic XML parse
        message, when a feed endpoint starts serving HTML.
        """

        def handler(request):
            return httpx.Response(
                200,
                text="<!DOCTYPE html><html><body>Feed moved</body></html>",
                headers={"content-type": "text/html; charset=utf-8"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.example.com/careers",
                "metadata": {
                    "preset": "successfactors",
                    "feed_url": "https://jobs.example.com/googlefeed.xml",
                },
            }
            with pytest.raises(RssFeedNotXml, match="googlefeed.xml"):
                await discover(board, client)
