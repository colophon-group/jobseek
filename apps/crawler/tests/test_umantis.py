from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.umantis import (
    _base_url,
    _extract_table_nr,
    _get_page_with_retry,
    _parse_host,
    _parse_jobs_from_html,
    can_handle,
    discover,
)
from src.shared.http_retry import PaginationFetchError

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


# ---------------------------------------------------------------------------
# Pagination retry semantics (#2747)
# ---------------------------------------------------------------------------


_PAGE_URL = "https://recruitingapp-2698.umantis.com/Jobs/All?tc999=p2"

_PAGE1_HTML = """\
<html><body>
<a href="/Vacancies/1/Description/1" class="HSTableLinkSubTitle">Job A</a>
<table-navigation initial-data-string='{"TableNr":"999"}'>
</table-navigation>
</body></html>"""

_PAGE2_HTML = """\
<html><body>
<a href="/Vacancies/2/Description/1" class="HSTableLinkSubTitle">Job B</a>
</body></html>"""


class TestGetPageWithRetry:
    """``_get_page_with_retry`` mirrors ``fetch_with_retry``'s contract on
    Umantis's GET pagination endpoint: 5xx / 408 / 425 / 429 / network
    errors are retried, non-retryable 4xx fail fast, and persistent
    failures raise :class:`PaginationFetchError` so a single broken
    pagination page doesn't silently truncate the run (#2747).
    """

    async def test_returns_on_success(self):
        def handler(request):
            return httpx.Response(200, text=_PAGE2_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            text = await _get_page_with_retry(client, _PAGE_URL)
            assert text == _PAGE2_HTML

    async def test_returns_none_on_404_end_of_pagination(self):
        def handler(request):
            return httpx.Response(404, text="not found")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            text = await _get_page_with_retry(client, _PAGE_URL)
            assert text is None

    async def test_retries_on_429_then_succeeds(self, monkeypatch):
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, text=_PAGE2_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            text = await _get_page_with_retry(client, _PAGE_URL, base_delay=0.001)
            assert text == _PAGE2_HTML
            assert calls["n"] == 3

    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        """Issue #2747's load-bearing case: pre-fix, a non-200 response
        (e.g. 503) hit the lenient ``if resp.status_code != 200: break``
        and silently truncated pagination — every URL on unfetched pages
        was then tombstoned by ``_MARK_GONE_BY_TIMESTAMP``. Now 503 is
        retried like every other transient.
        """
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503, text="service unavailable")
            return httpx.Response(200, text=_PAGE2_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            text = await _get_page_with_retry(client, _PAGE_URL, base_delay=0.001)
            assert text == _PAGE2_HTML
            assert calls["n"] == 3

    async def test_raises_after_persistent_5xx(self, monkeypatch):
        """Issue #2747 acceptance: persistent 5xx exhausts the retry budget
        and raises ``PaginationFetchError`` — no silent truncation.
        """
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(500, text="internal")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _get_page_with_retry(
                    client,
                    _PAGE_URL,
                    retries=3,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status == 500
            assert exc_info.value.attempts == 3
            assert calls["n"] == 3

    async def test_raises_on_non_retryable_4xx_immediately(self, monkeypatch):
        """A 401 / 403 / 400 indicates a hard error — no point retrying.
        Raise ``PaginationFetchError`` on the first attempt."""
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(401, text="unauthorized")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _get_page_with_retry(
                    client,
                    _PAGE_URL,
                    retries=3,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status == 401
            # Exactly one attempt — no retry on non-retryable 4xx.
            assert calls["n"] == 1

    async def test_raises_after_persistent_connection_error(self, monkeypatch):
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            raise httpx.ConnectError("conn refused")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _get_page_with_retry(
                    client,
                    _PAGE_URL,
                    retries=2,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status is None
            assert exc_info.value.last_error == "ConnectError"


class TestDiscoverPaginationRetry:
    """Issue #2747 acceptance: the discover() pagination loop propagates
    the new retry-then-raise contract end-to-end. Pre-fix, a transient
    5xx / 429 / network error mid-pagination silently truncated the URL
    set, then ``_MARK_GONE_BY_TIMESTAMP`` tombstoned every URL on
    unfetched pages. Now both transients are retried and persistent
    failures raise ``PaginationFetchError``.
    """

    async def test_503_then_200_pagination_continues(self, monkeypatch):
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())
        page2_calls = {"n": 0}

        def handler(request):
            url = str(request.url)
            if "tc999=p2" in url:
                page2_calls["n"] += 1
                if page2_calls["n"] < 2:
                    return httpx.Response(503, text="unavailable")
                return httpx.Response(200, text=_PAGE2_HTML)
            if "tc999=p3" in url:
                return httpx.Response(200, text="<html></html>")
            return httpx.Response(200, text=_PAGE1_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {"customer_id": "2698"},
            }
            urls = await discover(board, client)
            assert len(urls) == 2
            # Page 2 was retried once before succeeding.
            assert page2_calls["n"] == 2

    async def test_429_then_200_pagination_continues(self, monkeypatch):
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())
        page2_calls = {"n": 0}

        def handler(request):
            url = str(request.url)
            if "tc999=p2" in url:
                page2_calls["n"] += 1
                if page2_calls["n"] < 2:
                    return httpx.Response(429, text="rate limited")
                return httpx.Response(200, text=_PAGE2_HTML)
            if "tc999=p3" in url:
                return httpx.Response(200, text="<html></html>")
            return httpx.Response(200, text=_PAGE1_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {"customer_id": "2698"},
            }
            urls = await discover(board, client)
            assert len(urls) == 2
            assert page2_calls["n"] == 2

    async def test_persistent_500_raises_not_silent_break(self, monkeypatch):
        """Pre-fix, ``if resp.status_code != 200: break`` silently
        truncated the URL set on a persistent 500. Now the helper raises
        ``PaginationFetchError`` instead — caller propagates so the run
        is recorded as a failure.
        """
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            url = str(request.url)
            if "tc999=p2" in url:
                return httpx.Response(500, text="internal")
            return httpx.Response(200, text=_PAGE1_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {"customer_id": "2698"},
            }
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(board, client)
            assert exc_info.value.last_status == 500

    async def test_persistent_connection_error_raises(self, monkeypatch):
        """Pre-fix, ``except Exception: break`` silently truncated on
        connection errors. Now the helper raises ``PaginationFetchError``.
        """
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            url = str(request.url)
            if "tc999=p2" in url:
                raise httpx.ConnectError("conn reset")
            return httpx.Response(200, text=_PAGE1_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {"customer_id": "2698"},
            }
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(board, client)
            assert exc_info.value.last_error == "ConnectError"

    async def test_empty_page_terminates_as_success(self):
        """Legitimate end-of-pagination: a 200 with no jobs on page N
        terminates the loop as success — the existing pagination test
        relies on this, repeated here to pin the behaviour now that the
        retry helper is in place.
        """

        def handler(request):
            url = str(request.url)
            if "tc999=p2" in url:
                # Empty page — no <a> tags with HSTableLinkSubTitle.
                return httpx.Response(200, text="<html><body></body></html>")
            return httpx.Response(200, text=_PAGE1_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {"customer_id": "2698"},
            }
            urls = await discover(board, client)
            # Only page 1's job — empty page 2 terminated the loop.
            assert len(urls) == 1
