from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.api_sniffer import discover as api_sniffer_discover
from src.core.monitors.inline import discover as inline_discover
from src.shared.http_retry import PaginationFetchError

DATA = Path(__file__).resolve().parents[1] / "data"


def _boards() -> list[dict[str, str]]:
    with (DATA / "boards.csv").open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _board(slug: str) -> dict[str, str]:
    return next(row for row in _boards() if row["board_slug"] == slug)


def test_blocked_static_sources_use_bounded_proxy_recovery() -> None:
    barclays = json.loads(_board("barclays-careers")["monitor_config"])
    assert barclays["proxy"] is True
    assert barclays["xml_attempts"] == 5

    bdo = _board("bdo-brazil")
    bdo_monitor = json.loads(bdo["monitor_config"])
    bdo_scraper = json.loads(bdo["scraper_config"])
    assert bdo_monitor["transport_attempts"] == 5
    assert bdo_monitor["pagination"]["transient_403"] is True
    assert bdo_monitor["pagination"]["transport_attempts"] == 5
    assert bdo_scraper == {"proxy": True, "transport_attempts": 5}

    beiersdorf = _board("beiersdorf-brazil-vagas")
    beiersdorf_monitor = json.loads(beiersdorf["monitor_config"])
    beiersdorf_scraper = json.loads(beiersdorf["scraper_config"])
    assert beiersdorf_monitor["transport_attempts"] == 5
    assert beiersdorf_monitor["pagination"]["transient_403"] is True
    assert beiersdorf_monitor["pagination"]["transport_attempts"] == 5
    assert beiersdorf_scraper == {"proxy": True, "transport_attempts": 5}

    mcdonalds = json.loads(_board("mcdonalds-sg")["monitor_config"])
    assert mcdonalds["proxy"] is True
    assert mcdonalds["transient_403"] is True
    assert mcdonalds["transport_attempts"] == 3

    rtx = _board("rtx-careers")
    rtx_monitor = json.loads(rtx["monitor_config"])
    assert rtx_monitor["proxy"] is True
    assert rtx_monitor["sitemap_url"] == "https://careers.rtx.com/global/en/sitemap.xml"
    assert "url" not in rtx_monitor
    assert rtx_monitor["xml_attempts"] == 5
    # Listing discovery rotates blocked proxy exits. Detail scraping remains
    # direct and uses the existing same-session retry first; only an exhausted
    # 403 performs an exact requisition lookup against RTX's proxied Workday
    # tenant, avoiding a retry storm against the blocked Phenom edge.
    assert json.loads(rtx["scraper_config"]) == {
        "workday_fallback": {
            "company": "globalhr",
            "wd_instance": "wd5",
            "site": "REC_RTX_Ext_Gateway",
            "proxy": True,
        }
    }

    walgreens = [row for row in _boards() if row["board_slug"].startswith("walgreens-careers-")]
    assert len(walgreens) == 3
    walgreens_configs = [json.loads(row["monitor_config"]) for row in walgreens]
    assert all(config["proxy"] is True for config in walgreens_configs)
    assert all(config["transient_403"] is True for config in walgreens_configs)
    assert all(config["transport_attempts"] == 5 for config in walgreens_configs)


def test_postmerge_detail_sources_use_bounded_proxy_recovery() -> None:
    exeter = json.loads(_board("beth-israel-lahey-health-exeter")["scraper_config"])
    assert exeter["proxy"] is True
    assert exeter["transport_attempts"] == 5

    biorce = json.loads(_board("biorce-main")["scraper_config"])
    assert biorce == {
        "fields": {
            "title": "title",
            "description": "description",
            "locations": "locations[].name",
        },
        "proxy": True,
        "transport_attempts": 5,
        "channel": "chrome",
        "headless": False,
        "stealth": True,
    }


@pytest.mark.asyncio
async def test_walgreens_403_exhaustion_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr("src.core.monitors.api_sniffer.asyncio.sleep", AsyncMock())
    walgreens = [row for row in _boards() if row["board_slug"].startswith("walgreens-careers-")]

    for row in walgreens:
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(403, text="Access denied", request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await api_sniffer_discover(
                    {
                        "board_url": row["board_url"],
                        "metadata": json.loads(row["monitor_config"]),
                    },
                    client,
                )

        assert attempts == 5
        assert exc_info.value.attempts == 5
        assert exc_info.value.last_status == 403


def test_retired_thailand_source_is_removed_after_moving_to_federal_portal() -> None:
    assert not any(row["board_slug"] == "swiss-confederation-thailand" for row in _boards())


def test_retired_disruptive_industries_teamtailor_source_is_removed() -> None:
    assert not any(row["board_slug"] == "disruptive-industries-teamtailor" for row in _boards())


def test_oac_starts_at_the_stable_search_results_url() -> None:
    row = _board("bright-horizons-only-about-children-australia")
    config = json.loads(row["monitor_config"])

    assert row["board_url"] == (
        "https://careers.oac.edu.au/jobtools/"
        "jncustomsearch.searchResults?in_organid=20676&in_jobDate=All"
    )
    assert [action["action"] for action in config["actions"]] == ["wait_for"]


@pytest.mark.asyncio
async def test_iihf_live_layout_extracts_the_current_vacancy() -> None:
    row = _board("international-ice-hockey-federation-jobs")
    config = json.loads(row["monitor_config"])
    assert config["fetch_urls"][0].startswith("https://canada-central.iihf.com/")
    assert config["empty_requires_no_jobs"] is True

    html = """
    <section class="m-text is-full">
      <div class="s-content">
        <h3 class="s-sub-title">Receptionist / Corporate Services Assistant</h3>
        The International Ice Hockey Federation is hiring for its Zurich office.
        <strong>How to apply</strong>
      </div>
    </section>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        jobs = await inline_discover(
            {"board_url": row["board_url"], "metadata": config},
            client,
        )

    assert len(jobs) == 1
    assert jobs[0].title == "Receptionist / Corporate Services Assistant"
    assert "Zurich office" in (jobs[0].description or "")
    assert jobs[0].locations == ["Zurich, CH"]


@pytest.mark.asyncio
async def test_iihf_retains_authoritative_empty_state() -> None:
    row = _board("international-ice-hockey-federation-jobs")
    config = json.loads(row["monitor_config"])
    html = """
    <div class="s-content">
      Details of all future job opportunities will be advertised here.
    </div>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        jobs = await inline_discover(
            {"board_url": row["board_url"], "metadata": config},
            client,
        )

    assert jobs == []
