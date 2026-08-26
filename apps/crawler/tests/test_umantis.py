from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.umantis import (
    _base_url,
    _extract_navigation,
    _extract_table_nr,
    _get_page_with_retry,
    _pagination_url,
    _parse_discovered_jobs_from_html,
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


def _navigation(
    *,
    total: int,
    first: int,
    last: int,
    page: int,
    next_url: str | None = None,
    table_nr: str = "999",
) -> str:
    payload: dict = {
        "TableNr": table_nr,
        "TableTotalLines": str(total),
        "TableFrom": first,
        "TableTo": str(last),
        "TableCurrentPage": page,
    }
    if next_url is not None:
        payload["NextLink"] = {
            "EnhancedUrl": next_url,
            "FieldIsActive": 1,
        }
    return f"<table-navigation initial-data-string='{json.dumps(payload)}'></table-navigation>"


def _owned_row(vacancy_id: int, language_id: int, title: str) -> str:
    return f"""
<tr><td>
<a href="/Vacancies/{vacancy_id}/Description/{language_id}"
   class="HSTableLinkSubTitle">{title}</a>
<span class="column-value" id="column_value_1184173">Université de Neuchâtel</span>
</td></tr>
"""


def _strict_board() -> dict:
    return {
        "board_url": (
            "https://recruitingapp-3040.umantis.com/Jobs/3"
            "?lang=fre&CompanyID=32&Reset=G&DesignID=10012"
        ),
        "metadata": {
            "customer_id": "3040",
            "listing_path": "/Jobs/3?lang=fre&CompanyID=32&Reset=G&DesignID=10012",
            "strict_listing_contract": True,
            "expected_employer": "Université de Neuchâtel",
            "employer_field_id": "column_value_1184173",
            "empty_state_text": "Aucune entrée n’a été trouvée.",
        },
    }


class TestParseJobsFromHtml:
    def test_extracts_jobs(self):
        jobs = _parse_jobs_from_html(_LISTING_HTML, "https://recruitingapp-2698.umantis.com")
        assert len(jobs) == 2
        assert jobs[0] == (
            "https://recruitingapp-2698.umantis.com/Vacancies/100/Description",
            "Software Engineer (m/f/d)",
        )
        assert jobs[1] == (
            "https://recruitingapp-2698.umantis.com/Vacancies/200/Description",
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

    def test_rejects_cross_origin_vacancy_link(self):
        html = (
            '<a href="https://evil.example/Vacancies/6500/Description/3" '
            'class="HSTableLinkSubTitle">Injected role</a>'
        )
        with pytest.raises(ValueError, match="crossed the configured origin"):
            _parse_discovered_jobs_from_html(
                html,
                "https://recruitingapp-3040.umantis.com",
            )

    def test_rejects_non_numeric_vacancy_identity(self):
        html = (
            '<a href="/Vacancies/not-an-id/Description/3" '
            'class="HSTableLinkSubTitle">Injected role</a>'
        )
        with pytest.raises(ValueError, match="numeric canonical path"):
            _parse_discovered_jobs_from_html(
                html,
                "https://recruitingapp-3040.umantis.com",
            )

    def test_strips_query_params(self):
        html = '<a href="/Vacancies/1/Description/1?lang=ger" class="HSTableLinkSubTitle">Test</a>'
        jobs = _parse_jobs_from_html(html, "https://x.com")
        assert jobs[0][0] == "https://x.com/Vacancies/1/Description"

    def test_extracts_listing_fields(self):
        html = """\
<table><tr><td><ul>
<li><a href="/Vacancies/1/Description/1" class="HSTableLinkSubTitle">Engineer</a></li>
<li><span class="visually-hidden">Art</span><i class="icon icon-jobtype"></i>
<span class="column-value">Vollzeit</span></li>
<li><span class="visually-hidden">Anstellungsort</span><i class="icon icon-department"></i>
<span class="column-value">D&uuml;bendorf</span></li>
</ul></td></tr></table>
"""
        jobs = _parse_discovered_jobs_from_html(html, "https://recruiting.example.com")
        assert len(jobs) == 1
        assert jobs[0].title == "Engineer"
        assert jobs[0].locations == ["Dübendorf"]
        assert jobs[0].employment_type == "Vollzeit"

    def test_translated_location_label_fallback(self):
        html = """\
<table><tr><td><ul>
<li><a href="/Vacancies/2/Description/2" class="HSTableLinkSubTitle">Engineer</a></li>
<li><span class="visually-hidden">Location</span>
<span class="column-value">Geneva</span></li>
</ul></td></tr></table>
"""
        jobs = _parse_discovered_jobs_from_html(html, "https://recruiting.example.com")
        assert jobs[0].locations == ["Geneva"]


class TestExtractTableNr:
    def test_from_json(self):
        assert _extract_table_nr('"TableNr":"1152481"') == "1152481"

    def test_from_pagination_url(self):
        assert _extract_table_nr("?tc9876543=p2") == "9876543"

    def test_none(self):
        assert _extract_table_nr("<html>no pagination</html>") is None

    def test_html_escaped_full_navigation(self):
        html = (
            '<table-navigation initial-data-string="{&quot;TableTotalLines&quot;:&quot;2&quot;,'
            "&quot;TableTo&quot;:&quot;2&quot;,&quot;TableNr&quot;:&quot;66856&quot;,"
            '&quot;TableCurrentPage&quot;:1,&quot;TableFrom&quot;:1}"></table-navigation>'
        )
        navigation = _extract_navigation(html)
        assert navigation is not None
        assert navigation.table_nr == "66856"
        assert navigation.total == 2


class TestPaginationUrl:
    def test_preserves_listing_filters_for_legacy_boards(self):
        url = _pagination_url(
            "https://recruitingapp-3040.umantis.com/Jobs/3?lang=fre&CompanyID=32&Reset=G",
            "66856",
            2,
        )
        assert url == (
            "https://recruitingapp-3040.umantis.com/Jobs/3?lang=fre&CompanyID=32&tc66856=p2"
        )


# ── Discover ─────────────────────────────────────────────────────────────


class TestDiscover:
    async def test_returns_rich_jobs_for_enrichment(self):
        def handler(request):
            return httpx.Response(200, text=_LISTING_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {
                    "customer_id": "2698",
                    "scraper_config": {"enrich": ["description"]},
                },
            }
            jobs = await discover(board, client)
            assert isinstance(jobs, list)
            assert len(jobs) == 2
            assert all("/Vacancies/" in job.url for job in jobs)

    async def test_returns_urls_without_enrichment(self):
        def handler(request):
            return httpx.Response(200, text=_LISTING_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://recruitingapp-2698.umantis.com/Jobs/All",
                "metadata": {"customer_id": "2698"},
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert urls == {
                "https://recruitingapp-2698.umantis.com/Vacancies/100/Description",
                "https://recruitingapp-2698.umantis.com/Vacancies/200/Description",
            }

    @pytest.mark.parametrize("languages", [(3, 1), (1, 3)])
    async def test_locale_aliases_dedupe_stably_by_numeric_vacancy_id(self, languages):
        listing = "".join(
            f'<a href="/Vacancies/6500/Description/{language}" '
            f'class="HSTableLinkSubTitle">Role {language}</a>'
            for language in languages
        )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=listing, request=request)
            )
        ) as client:
            result = await discover(
                {
                    "board_url": "https://recruitingapp-3040.umantis.com/Jobs/All",
                    "metadata": {"customer_id": "3040"},
                },
                client,
            )

        assert result == {"https://recruitingapp-3040.umantis.com/Vacancies/6500/Description"}

    async def test_de_fr_it_en_variants_do_not_churn_identity_across_cycles(self):
        results = []
        for language in (1, 2, 3, 4):
            listing = (
                f'<a href="/Vacancies/6500/Description/{language}" '
                f'class="HSTableLinkSubTitle">Role {language}</a>'
            )
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request, listing=listing: httpx.Response(
                        200,
                        text=listing,
                        request=request,
                    )
                )
            ) as client:
                results.append(
                    await discover(
                        {
                            "board_url": "https://recruitingapp-3040.umantis.com/Jobs/All",
                            "metadata": {"customer_id": "3040"},
                        },
                        client,
                    )
                )

        assert results == [
            {"https://recruitingapp-3040.umantis.com/Vacancies/6500/Description"},
            {"https://recruitingapp-3040.umantis.com/Vacancies/6500/Description"},
            {"https://recruitingapp-3040.umantis.com/Vacancies/6500/Description"},
            {"https://recruitingapp-3040.umantis.com/Vacancies/6500/Description"},
        ]

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
            assert all("mycompany.umantis.com/Vacancies/" in url for url in urls)

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


class TestStrictDiscover:
    @staticmethod
    def _detail_response(request):
        return httpx.Response(
            200,
            text=(
                '<head><meta name="description" '
                'content="Université de Neuchâtel - Suisse."></head>'
                "<main><p>Université de Neuchâtel</p></main>"
            ),
            request=request,
        )

    async def test_locale_aliases_collapse_to_numeric_id_and_fixed_locale(self):
        listing = (
            _owned_row(6500, 1, "Administrative employee")
            + _owned_row(6500, 3, "Collaborateur-trice administratif-ive")
            + _navigation(total=1, first=1, last=1, page=1)
        )

        def handler(request):
            if "/Vacancies/" in request.url.path:
                return self._detail_response(request)
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(_strict_board(), client)

        assert result == {"https://recruitingapp-3040.umantis.com/Vacancies/6500/Description"}

    async def test_nested_owner_field_captures_the_complete_dedicated_value(self):
        listing = (
            '<tr><td><a href="/Vacancies/6500/Description/3" '
            'class="HSTableLinkSubTitle">Role</a>'
            '<span class="column-value" id="column_value_1184173">'
            "<span>Université de</span> <strong>Neuchâtel</strong>"
            "</span></td></tr>" + _navigation(total=1, first=1, last=1, page=1)
        )

        def handler(request):
            if "/Vacancies/" in request.url.path:
                return self._detail_response(request)
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover(_strict_board(), client) == {
                "https://recruitingapp-3040.umantis.com/Vacancies/6500/Description"
            }

    async def test_nested_owner_field_cannot_hide_a_suffix_after_an_exact_inner_span(self):
        listing = (
            '<tr><td><a href="/Vacancies/6500/Description/3" '
            'class="HSTableLinkSubTitle">Role</a>'
            '<span class="column-value" id="column_value_1184173">'
            "<span>Université de Neuchâtel</span> Research Partner"
            "</span></td></tr>" + _navigation(total=1, first=1, last=1, page=1)
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=listing, request=request)
            )
        ) as client:
            with pytest.raises(ValueError, match="exact configured employer field"):
                await discover(_strict_board(), client)

    async def test_rejects_conflicting_rows_for_same_vacancy_locale(self):
        listing = (
            _owned_row(6500, 3, "First title")
            + _owned_row(6500, 3, "Conflicting title")
            + _navigation(total=1, first=1, last=1, page=1)
        )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=listing, request=request)
            )
        ) as client:
            with pytest.raises(ValueError, match="conflicting rows for one vacancy locale"):
                await discover(_strict_board(), client)

    async def test_rejects_wrong_employer_listing_row(self):
        listing = (
            '<tr><td><a href="/Vacancies/6500/Description/3" '
            'class="HSTableLinkSubTitle">Role at Université de Neuchâtel</a>'
            '<span class="column-value" id="column_value_1184173">Different employer</span>'
            '<span class="column-value">Université de Neuchâtel</span></td></tr>'
            + _navigation(total=1, first=1, last=1, page=1)
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=listing, request=request)
            )
        ) as client:
            with pytest.raises(ValueError, match="exact configured employer field"):
                await discover(_strict_board(), client)

    async def test_rejects_employer_field_that_only_contains_expected_name(self):
        listing = (
            '<tr><td><a href="/Vacancies/6500/Description/3" '
            'class="HSTableLinkSubTitle">Role</a>'
            '<span class="column-value" id="column_value_1184173">'
            "Université de Neuchâtel Research Partner</span></td></tr>"
            + _navigation(total=1, first=1, last=1, page=1)
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=listing, request=request)
            )
        ) as client:
            with pytest.raises(ValueError, match="exact configured employer field"):
                await discover(_strict_board(), client)

    async def test_rejects_wrong_employer_detail(self):
        listing = _owned_row(6500, 3, "Role") + _navigation(
            total=1,
            first=1,
            last=1,
            page=1,
        )

        def handler(request):
            if "/Vacancies/" in request.url.path:
                return httpx.Response(
                    200,
                    text=(
                        '<meta name="description" content="Different employer - Suisse.">'
                        "<h1>Université de Neuchâtel research role</h1>"
                        "<main>Work with Université de Neuchâtel.</main>"
                    ),
                    request=request,
                )
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="detail metadata did not identify"):
                await discover(_strict_board(), client)

    async def test_rejects_body_forged_description_meta(self):
        listing = _owned_row(6500, 3, "Role") + _navigation(
            total=1,
            first=1,
            last=1,
            page=1,
        )

        def handler(request):
            if "/Vacancies/" in request.url.path:
                return httpx.Response(
                    200,
                    text=(
                        "<html><head><title>Role</title></head><body>"
                        '<meta name="description" '
                        'content="Université de Neuchâtel - Suisse.">'
                        "</body></html>"
                    ),
                    request=request,
                )
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="detail metadata did not identify"):
                await discover(_strict_board(), client)

    @pytest.mark.parametrize(
        "detail",
        [
            (
                '<head><meta name="description" '
                'content="Université de Neuchâtel - Suisse.">'
                '<meta name="description" '
                'content="Université de Neuchâtel - duplicate."></head>'
            ),
            ('<head><meta name="description" content="Université de Neuchâtel - Suisse.">'),
        ],
    )
    async def test_rejects_ambiguous_or_unclosed_description_head(self, detail):
        listing = _owned_row(6500, 3, "Role") + _navigation(
            total=1,
            first=1,
            last=1,
            page=1,
        )

        def handler(request):
            if "/Vacancies/" in request.url.path:
                return httpx.Response(200, text=detail, request=request)
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="detail metadata did not identify"):
                await discover(_strict_board(), client)

    async def test_rejects_cross_origin_detail_redirect(self):
        listing = _owned_row(6500, 3, "Role") + _navigation(
            total=1,
            first=1,
            last=1,
            page=1,
        )

        def handler(request):
            if "/Vacancies/" in request.url.path:
                return httpx.Response(
                    302,
                    headers={"location": "https://evil.example/detail"},
                    request=request,
                )
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await discover(_strict_board(), client)

    async def test_suffix_free_detail_follows_same_origin_locale_redirect(self):
        listing = _owned_row(6500, 3, "Role") + _navigation(
            total=1,
            first=1,
            last=1,
            page=1,
        )
        requested: list[str] = []

        def handler(request):
            requested.append(str(request.url))
            if request.url.path.endswith("/Description"):
                return httpx.Response(
                    302,
                    headers={"location": "/Vacancies/6500/Description/3"},
                    request=request,
                )
            if request.url.path.endswith("/Description/3"):
                return self._detail_response(request)
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(_strict_board(), client)

        stable_url = "https://recruitingapp-3040.umantis.com/Vacancies/6500/Description"
        assert result == {stable_url}
        assert stable_url in requested
        assert f"{stable_url}/3" in requested

    async def test_rejects_suffix_free_detail_redirect_without_location(self):
        listing = _owned_row(6500, 3, "Role") + _navigation(
            total=1,
            first=1,
            last=1,
            page=1,
        )

        def handler(request):
            if request.url.path.endswith("/Description"):
                return httpx.Response(302, request=request)
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(_strict_board(), client)
        assert exc_info.value.last_status == 302

    async def test_rejects_suffix_free_detail_redirect_loop(self, monkeypatch):
        from src.core.monitors import umantis as umantis_module

        monkeypatch.setattr(umantis_module.asyncio, "sleep", AsyncMock())
        listing = _owned_row(6500, 3, "Role") + _navigation(
            total=1,
            first=1,
            last=1,
            page=1,
        )

        def handler(request):
            if request.url.path.endswith("/Description"):
                return httpx.Response(
                    302,
                    headers={"location": "/Vacancies/6500/Description/3"},
                    request=request,
                )
            if request.url.path.endswith("/Description/3"):
                return httpx.Response(
                    302,
                    headers={"location": "/Vacancies/6500/Description"},
                    request=request,
                )
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(_strict_board(), client)
        assert exc_info.value.last_error == "TooManyRedirects"

    async def test_rejects_advertised_total_mismatch(self):
        listing = _owned_row(6500, 3, "Role") + _navigation(
            total=2,
            first=1,
            last=2,
            page=1,
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=listing, request=request)
            )
        ) as client:
            with pytest.raises(ValueError, match="advertised navigation range"):
                await discover(_strict_board(), client)

    async def test_accepts_only_explicit_visible_zero(self):
        listing = "<main><p>Aucune entrée n’a été trouvée.</p></main>" + _navigation(
            total=0, first=0, last=0, page=1
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=listing, request=request)
            )
        ) as client:
            assert await discover(_strict_board(), client) == set()

    @pytest.mark.parametrize(
        "hidden_marker",
        [
            '<script>const copy = "Aucune entrée n’a été trouvée.";</script>',
            "<p hidden>Aucune entrée n’a été trouvée.</p>",
            '<p aria-hidden="true">Aucune entrée n’a été trouvée.</p>',
            '<p aria-hidden="  TRUE  ">Aucune entrée n’a été trouvée.</p>',
            '<p style="display: none">Aucune entrée n’a été trouvée.</p>',
            '<p class="visually-hidden">Aucune entrée n’a été trouvée.</p>',
            "<head>Aucune entrée n’a été trouvée.</head>",
            "<title>Aucune entrée n’a été trouvée.</title>",
        ],
    )
    async def test_rejects_zero_without_visible_marker(self, hidden_marker):
        listing = hidden_marker + _navigation(total=0, first=0, last=0, page=1)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=listing, request=request)
            )
        ) as client:
            with pytest.raises(ValueError, match="explicit visible empty state"):
                await discover(_strict_board(), client)

    async def test_follows_exact_tokenized_pagination_and_proves_ranges(self):
        requested: list[str] = []
        page1 = _owned_row(6481, 3, "First") + _navigation(
            total=2,
            first=1,
            last=1,
            page=1,
            next_url="?tc999=p2&amp;_search_token999=12345#connectortable_999",
        )
        page2 = _owned_row(6500, 3, "Second") + _navigation(
            total=2,
            first=2,
            last=2,
            page=2,
        )

        def handler(request):
            requested.append(str(request.url))
            if "/Vacancies/" in request.url.path:
                return self._detail_response(request)
            if request.url.params.get("tc999") == "p2":
                return httpx.Response(200, text=page2, request=request)
            return httpx.Response(200, text=page1, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(_strict_board(), client)

        assert len(result) == 2
        assert any("tc999=p2&_search_token999=12345" in url for url in requested)

    @pytest.mark.parametrize(
        "next_url,error",
        [
            ("?tc999=p2", "search token"),
            (
                "https://evil.example/Jobs/3?tc999=p2&_search_token999=12345",
                "crossed its configured listing origin",
            ),
        ],
    )
    async def test_rejects_unsafe_next_link(self, next_url, error):
        listing = _owned_row(6481, 3, "First") + _navigation(
            total=2,
            first=1,
            last=1,
            page=1,
            next_url=next_url,
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=listing, request=request)
            )
        ) as client:
            with pytest.raises(ValueError, match=error):
                await discover(_strict_board(), client)

    async def test_rejects_cross_origin_pagination_redirect(self):
        listing = _owned_row(6481, 3, "First") + _navigation(
            total=2,
            first=1,
            last=1,
            page=1,
            next_url="?tc999=p2&amp;_search_token999=12345",
        )

        def handler(request):
            if request.url.params.get("tc999") == "p2":
                return httpx.Response(
                    302,
                    headers={"location": "https://evil.example/Jobs/3"},
                    request=request,
                )
            return httpx.Response(200, text=listing, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await discover(_strict_board(), client)

    async def test_rejects_repeated_provider_id_on_next_range(self):
        page1 = _owned_row(6481, 3, "First") + _navigation(
            total=2,
            first=1,
            last=1,
            page=1,
            next_url="?tc999=p2&amp;_search_token999=12345",
        )
        page2 = _owned_row(6481, 3, "First") + _navigation(
            total=2,
            first=2,
            last=2,
            page=2,
        )

        def handler(request):
            if request.url.params.get("tc999") == "p2":
                return httpx.Response(200, text=page2, request=request)
            return httpx.Response(200, text=page1, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="repeated provider vacancy IDs"):
                await discover(_strict_board(), client)

    async def test_uses_isolated_cookie_jar_and_keeps_scoped_cookie(self):
        listing_cookies: list[str | None] = []
        detail_cookies: list[str | None] = []
        listing = _owned_row(6500, 3, "Role") + _navigation(
            total=1,
            first=1,
            last=1,
            page=1,
        )

        def handler(request):
            if "/Vacancies/" in request.url.path:
                detail_cookies.append(request.headers.get("cookie"))
                return self._detail_response(request)
            listing_cookies.append(request.headers.get("cookie"))
            return httpx.Response(
                200,
                text=listing,
                headers={"set-cookie": "scope=unine; Path=/"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            client.cookies.set(
                "scope",
                "another-employer",
                domain="recruitingapp-3040.umantis.com",
                path="/",
            )
            await discover(_strict_board(), client)

        assert listing_cookies == [None]
        assert detail_cookies == ["scope=unine"]


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

    async def test_filtered_url_probe_preserves_listing_path(self):
        requested_urls: list[str] = []

        def handler(request):
            requested_urls.append(str(request.url))
            return httpx.Response(200, text=_LISTING_HTML_V2, request=request)

        url = (
            "https://recruitingapp-3040.umantis.com/Jobs/3"
            "?lang=fre&CompanyID=32&Reset=G&DesignID=10012"
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(url, client)

        assert result == {
            "customer_id": "3040",
            "region": "",
            "listing_path": "/Jobs/3?lang=fre&CompanyID=32&Reset=G&DesignID=10012",
            "jobs": 1,
        }
        assert requested_urls == [url]

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

    async def test_html_marker_preserves_embedded_filtered_listing(self):
        listing_url = (
            "https://recruitingapp-3040.umantis.com/Jobs/3"
            "?lang=fre&amp;CompanyID=32&amp;Reset=G&amp;DesignID=10012"
        )
        page_html = f'<iframe src="{listing_url}"></iframe>'
        requested_urls: list[str] = []

        def handler(request):
            requested_urls.append(str(request.url))
            if "recruitingapp-3040" in str(request.url):
                return httpx.Response(200, text=_LISTING_HTML_V2, request=request)
            return httpx.Response(200, text=page_html, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)

        assert result is not None
        assert result["customer_id"] == "3040"
        assert result["jobs"] == 1
        assert result["listing_path"] == ("/Jobs/3?lang=fre&CompanyID=32&Reset=G&DesignID=10012")
        assert "CompanyID=32" in requested_urls[-1]

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
