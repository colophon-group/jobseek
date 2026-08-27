from __future__ import annotations

import html

import httpx
import pytest

from src.core.monitors import all_monitor_types
from src.core.monitors.computrabajo import (
    _parse_listing,
    _profile_from_url,
    can_handle,
    discover,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

BOARD_URL = (
    "https://hn.computrabajo.com/empresas/ofertas-de-trabajo-de-cintas-de-honduras-B44E90FE4D8AE312"
)


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


class TestIdentity:
    def test_registered_and_auto_configured(self) -> None:
        assert "computrabajo" in all_monitor_types()
        assert detect_ats_from_url(BOARD_URL) == "computrabajo"
        assert auto_scraper_type("computrabajo") == ("json-ld", None)

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
