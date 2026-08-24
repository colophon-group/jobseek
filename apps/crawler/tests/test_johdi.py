from __future__ import annotations

import httpx

from src.core.enum_normalize import normalize_employment_type
from src.core.monitors import _REGISTRY as MONITOR_REGISTRY
from src.core.monitors.johdi import _widget_config, can_handle, discover, validated_config
from src.core.scrapers import _REGISTRY as SCRAPER_REGISTRY
from src.core.scrapers.johdi import _offer_id, _parse_detail, scrape
from src.workspace._compat import auto_scraper_type

BOARD_URL = "https://www.example.ch/jobs"
JOB_URL = f"{BOARD_URL}#/offer/4381/ouvrierere-qualifiee-a-100"
COMPANY_KEY = "opaque-company-key-1234567890"
FLOW = "web"
LOCALE = "fr"
CONFIG = {"company_key": COMPANY_KEY, "flow": FLOW, "locale": LOCALE}
LIST_PATH = f"/api/company/{COMPANY_KEY}/publicationFlows/{FLOW}/offers/{LOCALE}"
DETAIL_PATH = f"/api/company/{COMPANY_KEY}/publicationFlows/{FLOW}/offer/4381/{LOCALE}"


def _page() -> str:
    return f"""
    <html><body>
      <div id="ats-offers"
           data-locale="{LOCALE}"
           data-company-hash-key="{COMPANY_KEY}"
           data-flow="{FLOW}"></div>
    </body></html>
    """


def _summary() -> dict:
    return {
        "id": 4381,
        "title": "OUVRIER·ÈRE QUALIFIÉ·E À 100 %",
        "slug": "ouvrierere-qualifiee-a-100",
    }


def _detail() -> dict:
    return {
        **_summary(),
        "introduction": "<p>La Municipalité met au concours un poste.</p>",
        "description": "<h3>Tâches principales</h3><ul><li>Entretenir le domaine public.</li></ul>",
        "contract_type": "CDI",
        "work_place": "Les Avants",
        "city": "Montreux",
        "canton": "Vaud",
        "country_code": "ch",
        "publication_date": "2026-08-11",
        "expiration_date": "2026-08-25T00:00:00Z",
        "activity_from": 100,
        "activity_to": 100,
        "ref": "OUV-4381",
        "sector": "Public administration",
        "apply_link": "https://ats.johdisuite.ch/recruitments/4870/offer/4381/postulation/web",
    }


def _transport(*, offers: list | None = None) -> httpx.MockTransport:
    offers = [_summary()] if offers is None else offers

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == BOARD_URL:
            return httpx.Response(200, text=_page())
        if request.url.path == LIST_PATH:
            return httpx.Response(200, json=offers)
        if request.url.path == DETAIL_PATH:
            return httpx.Response(200, json=_detail())
        raise AssertionError(f"unexpected request: {request.url}")

    return httpx.MockTransport(handler)


def test_widget_config_requires_complete_safe_johdi_mount() -> None:
    assert _widget_config(_page()) == CONFIG
    assert _widget_config('<div id="ats-offers" data-flow="web"></div>') is None
    assert _widget_config('<div id="something-else"></div>') is None
    assert (
        validated_config({"company_key": "../../other-host", "flow": FLOW, "locale": LOCALE})
        is None
    )


async def test_can_handle_verifies_feed_and_accepts_empty_board() -> None:
    async with httpx.AsyncClient(transport=_transport(offers=[])) as client:
        assert await can_handle(BOARD_URL, client) == {**CONFIG, "jobs": 0}


async def test_discover_returns_canonical_offer_urls_without_detail_fetches() -> None:
    async with httpx.AsyncClient(transport=_transport()) as client:
        urls = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    assert urls == {JOB_URL}


def test_parse_detail_maps_complete_content() -> None:
    content = _parse_detail(_detail(), LOCALE)
    assert content.title == "OUVRIER·ÈRE QUALIFIÉ·E À 100 %"
    assert "Tâches principales" in (content.description or "")
    assert content.locations == ["Les Avants, Montreux, Vaud, CH"]
    assert normalize_employment_type(content.employment_type) == "full_time"
    assert content.date_posted == "2026-08-11"
    assert content.language == "fr"
    assert content.metadata == {
        "id": 4381,
        "reference": "OUV-4381",
        "sector": "Public administration",
        "activity_from": 100,
        "activity_to": 100,
        "expiration_date": "2026-08-25T00:00:00Z",
        "apply_url": "https://ats.johdisuite.ch/recruitments/4870/offer/4381/postulation/web",
    }


async def test_scrape_fetches_detail_api() -> None:
    async with httpx.AsyncClient(transport=_transport()) as client:
        content = await scrape(JOB_URL, CONFIG, client)

    assert content.title == "OUVRIER·ÈRE QUALIFIÉ·E À 100 %"
    assert content.locations == ["Les Avants, Montreux, Vaud, CH"]
    assert "Entretenir le domaine public" in (content.description or "")


def test_offer_id_rejects_non_johdi_fragment_routes() -> None:
    assert _offer_id(JOB_URL) == "4381"
    assert _offer_id(f"{BOARD_URL}#/vacancy/4381/example") is None
    assert _offer_id(f"{BOARD_URL}#/offer/not-an-id/example") is None


def test_johdi_monitor_and_scraper_are_registered_and_auto_paired() -> None:
    monitor = next(item for item in MONITOR_REGISTRY if item.name == "johdi")
    assert monitor.rich is False
    assert monitor.cost == 10
    assert "johdi" in SCRAPER_REGISTRY
    assert auto_scraper_type("johdi", CONFIG) == ("johdi", CONFIG)
