from __future__ import annotations

import html

import httpx
import pytest

from src.core.monitors import all_monitor_types
from src.core.monitors.computrabajo import (
    _pandape_from_url,
    _parse_listing,
    _parse_pandape_listing,
    _profile_from_url,
    can_handle,
    discover,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

BOARD_URL = (
    "https://hn.computrabajo.com/empresas/ofertas-de-trabajo-de-cintas-de-honduras-B44E90FE4D8AE312"
)
PANDAPE_URL = "https://acme.pandape.infojobs.com.br/Vacancies"
PANDAPE_PROXY_URL = "https://acme.pandape.computrabajo.com/Vacancies"


def _job_url(index: int) -> str:
    return (
        "https://hn.computrabajo.com/ofertas-de-trabajo/"
        f"oferta-de-trabajo-de-role-{index}-en-la-ceiba-{index:032X}"
    )


def _listing(total: int, indexes: range | list[int], *, canonical: str = BOARD_URL) -> str:
    links = "".join(
        f'<article data-offers-grid-offer-item-container><a class="js-o-link fc_base" '
        f'href="{html.escape(_job_url(index), quote=True)}#lc=CompanyListOffers-Score-{index}">'
        f"Role {index}</a></article>"
        for index in indexes
    )
    return f"""
        <html><head>
          <meta name="title" content="{total} Ofertas de trabajo en Employer" />
          <link rel="canonical" href="{canonical}" />
        </head><body>{links}</body></html>
    """


def _pandape_listing(
    total: int,
    indexes: range | list[int],
    *,
    page: int = 1,
    board_url: str = PANDAPE_URL,
) -> str:
    links = "".join(
        f'<a class="card card-vacancy" href="/Detail/{index}">Role {index}</a>' for index in indexes
    )
    total_text = f"{total:,}".replace(",", ".")
    is_last = str(page * 20 >= total)
    return f"""
        <html><body>
          <section id="VacancySection">
            <div class="color-title font-3xl"><span>{total_text}</span>
              <span>Vagas de Emprego</span></div>
            {links}
          </section>
          <input id="hdn_isLast" value="{is_last}" />
          <input id="hdn_PageSize" value="20" />
          <input id="hdn_PageNumber" value="{page}" />
        </body></html>
    """


class TestIdentity:
    def test_registered_and_auto_configured(self) -> None:
        assert "computrabajo" in all_monitor_types()
        assert detect_ats_from_url(BOARD_URL) == "computrabajo"
        assert auto_scraper_type("computrabajo") == ("json-ld", None)
        assert auto_scraper_type("computrabajo", {"proxy": True}) == (
            "json-ld",
            {"proxy": True},
        )

    @pytest.mark.parametrize("url", [PANDAPE_URL, PANDAPE_URL.lower(), PANDAPE_PROXY_URL])
    def test_accepts_exact_pandape_listing_urls(self, url: str) -> None:
        assert _pandape_from_url(url) is not None
        assert detect_ats_from_url(url) == "computrabajo"

    @pytest.mark.parametrize(
        "url",
        [
            PANDAPE_URL.replace("https://", "http://"),
            PANDAPE_URL.replace("/Vacancies", "/"),
            PANDAPE_URL + "?pageNumber=2",
            PANDAPE_URL + "#jobs",
            PANDAPE_URL.replace(".infojobs.com.br", ".infojobs.com.br.evil.test"),
            "https://pandape.infojobs.com.br/Vacancies",
        ],
    )
    def test_rejects_untrusted_or_filtered_pandape_urls(self, url: str) -> None:
        assert _pandape_from_url(url) is None
        assert detect_ats_from_url(url) != "computrabajo"

    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL,
            BOARD_URL.lower(),
            BOARD_URL + "/",
            (
                "https://co.computrabajo.com/empresas/"
                "ofertas-de-trabajo-de-example--0123456789abcdef"
            ),
        ],
    )
    def test_accepts_exact_country_employer_profiles(self, url: str) -> None:
        assert _profile_from_url(url) is not None
        assert detect_ats_from_url(url) == "computrabajo"

    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL.replace("https://", "http://"),
            BOARD_URL + "?p=2",
            BOARD_URL + "#jobs",
            BOARD_URL.replace("hn.computrabajo.com", "computrabajo.com"),
            BOARD_URL.replace("hn.computrabajo.com", "hn.computrabajo.com.evil.test"),
            BOARD_URL.replace("B44E90FE4D8AE312", "short"),
            "https://hn.computrabajo.com/ofertas-de-trabajo/",
        ],
    )
    def test_rejects_filtered_or_untrusted_urls(self, url: str) -> None:
        assert _profile_from_url(url) is None
        assert detect_ats_from_url(url) != "computrabajo"


class TestListingParser:
    def test_accepts_explicit_empty_board(self) -> None:
        urls, total = _parse_listing(_listing(0, []), board_url=BOARD_URL, requested_page=1)
        assert urls == set()
        assert total == 0

    def test_canonicalizes_job_links_and_removes_tracking_fragment(self) -> None:
        urls, total = _parse_listing(
            _listing(1, [1]),
            board_url=BOARD_URL,
            requested_page=1,
        )
        assert urls == {_job_url(1)}
        assert total == 1

    def test_percent_encodes_provider_nonbreaking_spaces_in_job_slugs(self) -> None:
        url = _job_url(1).replace("role-1", "consultor\u00a0de\u00a0cumplimiento")
        body = _listing(1, []).replace(
            "</body>", f'<a class="js-o-link" href="{url}">Role</a></body>'
        )

        urls, _total = _parse_listing(body, board_url=BOARD_URL, requested_page=1)

        assert urls == {url.replace("\u00a0", "%C2%A0")}

    @pytest.mark.parametrize(
        "body",
        [
            "<html><title>JavaScript is disabled</title></html>",
            _listing(1, []),
            _listing(0, [1]),
            _listing(0, [], canonical="https://hn.computrabajo.com/company/other"),
        ],
    )
    def test_rejects_challenges_count_mismatches_and_wrong_identity(self, body: str) -> None:
        with pytest.raises(ValueError):
            _parse_listing(body, board_url=BOARD_URL, requested_page=1)

    def test_parses_pandape_localised_total_and_job_links(self) -> None:
        urls, total = _parse_pandape_listing(
            _pandape_listing(1_234, range(1, 21)),
            board_url=PANDAPE_URL,
            requested_page=1,
        )

        assert len(urls) == 20
        assert "https://acme.pandape.infojobs.com.br/Detail/1" in urls
        assert total == 1_234

    @pytest.mark.parametrize(
        "body",
        [
            "<html><title>JavaScript is disabled</title></html>",
            _pandape_listing(1, []),
            _pandape_listing(0, [1]),
            _pandape_listing(21, range(1, 21)).replace('value="1"', 'value="2"'),
            _pandape_listing(21, range(1, 21)).replace('value="False"', 'value="True"'),
        ],
    )
    def test_rejects_invalid_pandape_contracts(self, body: str) -> None:
        with pytest.raises(ValueError):
            _parse_pandape_listing(body, board_url=PANDAPE_URL, requested_page=1)


class TestMonitor:
    async def test_paginates_complete_inventory(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("p", "1"))
            if page == 1:
                body = _listing(21, range(1, 21))
            elif page == 2:
                body = _listing(21, [21])
            else:  # pragma: no cover - proves the page bound
                raise AssertionError(f"unexpected page {page}")
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {_job_url(index) for index in range(1, 22)}

    async def test_probe_reports_authoritative_zero(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_listing(0, []), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(BOARD_URL, client)

        assert result == {
            "host": "hn.computrabajo.com",
            "company_id": "b44e90fe4d8ae312",
            "jobs": 0,
        }

    async def test_paginates_complete_pandape_inventory(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("pageNumber", "1"))
            if page == 1:
                body = _pandape_listing(21, range(1, 21), page=1)
            elif page == 2:
                body = _pandape_listing(21, [21], page=2)
            else:  # pragma: no cover - proves the page bound
                raise AssertionError(f"unexpected page {page}")
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": PANDAPE_URL}, client)

        assert result == {
            f"https://acme.pandape.infojobs.com.br/Detail/{index}" for index in range(1, 22)
        }

    async def test_pandape_probe_reports_jobs_and_proxy_requirement(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = _pandape_listing(1, [1], board_url=PANDAPE_PROXY_URL)
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(PANDAPE_PROXY_URL, client)

        assert result == {
            "host": "acme.pandape.computrabajo.com",
            "variant": "pandape",
            "proxy": True,
            "jobs": 1,
        }
