from __future__ import annotations

import httpx
import pytest

from src.core.monitors.umantis import (
    _base_url,
    _extract_table_nr,
    _parse_host,
    _parse_jobs_from_html,
    can_handle,
    discover,
)

# ── URL helpers ──────────────────────────────────────────────────────────


class TestParseHost:
    def test_standard(self):
        assert _parse_host("https://recruitingapp-2698.umantis.com/Jobs/All") == (
            "2698",
            "",
        )

    def test_de_region(self):
        assert _parse_host("https://recruitingapp-5181.de.umantis.com/Jobs/All") == ("5181", "de")

    def test_ch_region(self):
        assert _parse_host("https://recruitingapp-1234.ch.umantis.com/Jobs/All") == ("1234", "ch")

    def test_non_umantis(self):
        assert _parse_host("https://example.com/careers") == (None, None)

    def test_custom_cname(self):
        # Custom CNAME is NOT matched by _parse_host (only recruitingapp-{ID})
        assert _parse_host("https://jsafrasarasin.umantis.com/Jobs/All") == (
            None,
            None,
        )


class TestBaseUrl:
    def test_no_region(self):
        assert _base_url("2698") == "https://recruitingapp-2698.umantis.com"

    def test_with_region(self):
        assert _base_url("5181", "de") == "https://recruitingapp-5181.de.umantis.com"

    def test_empty_region(self):
        assert _base_url("2698", "") == "https://recruitingapp-2698.umantis.com"


# ── Listing parsing ─────────────────────────────────────────────────────


_LISTING_HTML = """\
<html><body>
<table class="tableaslist">
<tr class="tableaslist_contentrow1">
<td><span class="tableaslist_subtitle tableaslist_element_1152488">
<a href="/Vacancies/100/Description/1" class="HSTableLinkSubTitle"
   aria-label="Software Engineer (m/f/d)">Software Engineer (m/f/d)</a>
</span></td></tr>
<tr class="tableaslist_contentrow2">
<td><span class="tableaslist_subtitle tableaslist_element_1152488">
<a href="/Vacancies/200/Description/2" class="HSTableLinkSubTitle"
   aria-label="Product Manager">Product Manager</a>
</span></td></tr>
</table>
<table-navigation initial-data-string='{"TableNr":"1152481","TableTo":10}'>
</table-navigation>
</body></html>
"""

_LISTING_HTML_V2 = """\
<html><body>
<table class="c-box c-table table-as-list">
<tr class="table-as-list__contentrow1">
<td><span class="table-as-list__subtitle tableaslist_element_1152488">
<a href="/Vacancies/300/Description/1" class="HSTableLinkSubTitle">Data Scientist</a>
</span></td></tr>
</table>
</body></html>
"""


class TestParseJobsFromHtml:
    def test_extracts_jobs(self):
        jobs = _parse_jobs_from_html(_LISTING_HTML, "https://recruitingapp-2698.umantis.com")
        assert len(jobs) == 2
        assert jobs[0] == (
            "https://recruitingapp-2698.umantis.com/Vacancies/100/Description/1",
            "Software Engineer (m/f/d)",
        )
        assert jobs[1] == (
            "https://recruitingapp-2698.umantis.com/Vacancies/200/Description/2",
            "Product Manager",
        )

    def test_v2_template(self):
        jobs = _parse_jobs_from_html(_LISTING_HTML_V2, "https://recruitingapp-5181.de.umantis.com")
        assert len(jobs) == 1
        assert jobs[0][1] == "Data Scientist"

    def test_empty_html(self):
        jobs = _parse_jobs_from_html("<html><body></body></html>", "https://x.com")
        assert jobs == []

    def test_non_vacancy_links_skipped(self):
        html = '<a href="/other" class="HSTableLinkSubTitle">Not a job</a>'
        jobs = _parse_jobs_from_html(html, "https://x.com")
        assert jobs == []

    def test_strips_query_params(self):
        html = '<a href="/Vacancies/1/Description/1?lang=ger" class="HSTableLinkSubTitle">Test</a>'
        jobs = _parse_jobs_from_html(html, "https://x.com")
        assert jobs[0][0] == "https://x.com/Vacancies/1/Description/1"


class TestExtractTableNr:
    def test_from_json(self):
        assert _extract_table_nr('"TableNr":"1152481"') == "1152481"

    def test_from_pagination_url(self):
        assert _extract_table_nr("?tc9876543=p2") == "9876543"

    def test_none(self):
        assert _extract_table_nr("<html>no pagination</html>") is None


# ── Discover ─────────────────────────────────────────────────────────────


class TestDiscover:
    async def test_returns_urls(self):
        def handler(request):
            return httpx.Response(200, text=_LISTING_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {"customer_id": "2698"},
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert len(urls) == 2
            assert all("/Vacancies/" in u for u in urls)

    async def test_empty_listing(self):
        def handler(request):
            return httpx.Response(200, text="<html><body></body></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com",
                "metadata": {"customer_id": "2698"},
            }
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_customer_id_from_url(self):
        def handler(request):
            return httpx.Response(200, text=_LISTING_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {},
            }
            urls = await discover(board, client)
            assert len(urls) == 2

    async def test_no_customer_id_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {},
            }
            with pytest.raises(ValueError, match="customer_id"):
                await discover(board, client)

    async def test_cname_board(self):
        """CNAME board uses the board URL as the base directly."""
        cname_html = """\
<html><body>
<a href="/Vacancies/100/Description/1" class="HSTableLinkSubTitle">Job A</a>
<a href="/Vacancies/200/Description/2" class="HSTableLinkSubTitle">Job B</a>
</body></html>"""

        def handler(request):
            return httpx.Response(200, text=cname_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://mycompany.umantis.com/Jobs/All",
                "metadata": {"cname": "mycompany.umantis.com"},
            }
            urls = await discover(board, client)
            assert len(urls) == 2
            assert all("mycompany.umantis.com/Vacancies/" in u for u in urls)

    async def test_pagination(self):
        page1_html = """\
<html><body>
<a href="/Vacancies/1/Description/1" class="HSTableLinkSubTitle">Job A</a>
<table-navigation initial-data-string='{"TableNr":"999"}'>
</table-navigation>
</body></html>"""

        page2_html = """\
<html><body>
<a href="/Vacancies/2/Description/1" class="HSTableLinkSubTitle">Job B</a>
</body></html>"""

        call_count = {"n": 0}

        def handler(request):
            url = str(request.url)
            call_count["n"] += 1
            if "tc999=p2" in url:
                return httpx.Response(200, text=page2_html)
            if "tc999=p3" in url:
                return httpx.Response(200, text="<html></html>")
            return httpx.Response(200, text=page1_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {"customer_id": "2698"},
            }
            urls = await discover(board, client)
            assert len(urls) == 2

    async def test_pagination_duplicate_stops(self):
        """Pagination stops when new page returns same jobs."""
        html = """\
<html><body>
<a href="/Vacancies/1/Description/1" class="HSTableLinkSubTitle">Job A</a>
<table-navigation initial-data-string='{"TableNr":"999"}'>
</table-navigation>
</body></html>"""

        def handler(request):
            return httpx.Response(200, text=html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com",
                "metadata": {"customer_id": "2698"},
            }
            urls = await discover(board, client)
            assert len(urls) == 1  # No infinite loop


# ── Can handle ───────────────────────────────────────────────────────────


class TestCanHandle:
    async def test_no_client(self):
        result = await can_handle("https://recruitingapp-2698.umantis.com")
        assert result is not None
        assert result["customer_id"] == "2698"

    async def test_url_match_with_probe(self):
        def handler(request):
            return httpx.Response(200, text=_LISTING_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://recruitingapp-2698.umantis.com/Jobs/All", client)
            assert result is not None
            assert result["customer_id"] == "2698"
            assert result["jobs"] == 2

    async def test_de_region(self):
        result = await can_handle("https://recruitingapp-5181.de.umantis.com/Jobs/All")
        assert result is not None
        assert result["customer_id"] == "5181"
        assert result["region"] == "de"

    async def test_non_umantis(self):
        def handler(request):
            return httpx.Response(200, text="<html>plain page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None

    async def test_html_marker_detection(self):
        page_html = '<html><body><iframe src="https://recruitingapp-2698.umantis.com/Jobs/All"></iframe></body></html>'

        def handler(request):
            url = str(request.url)
            if "recruitingapp-2698" in url and "/Jobs/" in url:
                return httpx.Response(200, text=_LISTING_HTML)
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["customer_id"] == "2698"

    async def test_cname_with_recruitingapp_ref(self):
        """CNAME that references recruitingapp-{ID} in page source."""
        cname_html = '<html><body><script>window.location="https://recruitingapp-2698.umantis.com/Jobs/All"</script></body></html>'

        def handler(request):
            url = str(request.url)
            if "recruitingapp-2698" in url:
                return httpx.Response(200, text=_LISTING_HTML)
            return httpx.Response(200, text=cname_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://careers.umantis.com/Jobs/All", client)
            assert result is not None
            assert result["customer_id"] == "2698"

    async def test_cname_direct_listing(self):
        """CNAME that serves the listing page directly (no recruitingapp ref)."""
        # A CNAME page with globalUmantisParams and HSTableLinkSubTitle
        cname_listing = """\
<html><body>
<script>globalUmantisParams = {PageName: "Overview"}</script>
<a href="/Vacancies/100/Description/1" class="HSTableLinkSubTitle">Engineer</a>
<a href="/Vacancies/200/Description/2" class="HSTableLinkSubTitle">Designer</a>
</body></html>"""

        def handler(request):
            return httpx.Response(200, text=cname_listing)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://mycompany.umantis.com/Jobs/All", client)
            assert result is not None
            assert result["cname"] == "mycompany.umantis.com"
            assert result["jobs"] == 2

    async def test_cname_no_client(self):
        """CNAME without client cannot be detected."""
        result = await can_handle("https://mycompany.umantis.com/Jobs/All")
        assert result is None

    async def test_cname_ignored_subdomain(self):
        """Ignored subdomains (www, api, etc.) should not match."""

        def handler(request):
            return httpx.Response(200, text="<html></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.umantis.com/", client)
            assert result is None
