"""Provider presets for server-rendered BigRedSky job boards."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.core.monitor import monitor_one
from src.core.monitors.dom import (
    _bigredsky_probe_config,
    _extract_rich_rows_static,
    _validated_rich_rows,
    can_handle,
)
from src.core.scrapers.dom import parse_html
from src.workspace._compat import auto_scraper_type

BOARD_URL = "https://bunge.bigredsky.com/page.php"
LISTING_HTML = """
<html><body>
  <table id="brs_report_table_16"><tbody><tr><td><table><tbody>
    <tr class="oddrow">
      <td>20/09/2026</td>
      <td><a href="page.php?pageID=160&amp;windowUID=0&amp;AdvertID=925914">
        Maintenance Officer - Electrical
      </a></td>
      <td>Grain - Maintenance &amp; Engineering</td>
      <td>Permanent - Full Time</td>
      <td>Port Adelaide</td>
    </tr>
    <tr class="evenrow">
      <td>09/10/2026</td>
      <td><a href="page.php?pageID=160&amp;windowUID=0&amp;AdvertID=918201">
        Harvest Employment - 2026/2027
      </a></td>
      <td>Operations</td>
      <td>Casual</td>
      <td>Eyre Peninsula SA<br>Eastern SA/Vic<br>Central SA</td>
    </tr>
  </tbody></table></td></tr></tbody></table>
  <div id="brs-logo"><a href="https://www.bigredsky.com">
    BigRedSky e-Recruitment
  </a></div>
</body></html>
"""

DETAIL_HTML = """
<html><body>
  <div class="tempborder container">
    <div class="templatetext"></div>
    <h1 class="mobile-jobtitle">Maintenance Officer - Electrical</h1>
    <div id="responsive-template-subheading">
      <div id="responsive-template-subheading1">Port Adelaide</div>
      <div id="responsive-template-subheading2">Closing date: 20/09/2026</div>
    </div>
    <div class="templatetext">
      <p><strong>About the role</strong></p>
      <p>Maintain terminal equipment safely and reliably.</p>
      <h2>Duties</h2>
      <ul><li>Perform preventive maintenance.</li></ul>
    </div>
  </div>
</body></html>
"""


def test_bigredsky_probe_preserves_authoritative_listing_locations():
    result = _bigredsky_probe_config(LISTING_HTML, BOARD_URL)

    assert result is not None
    assert result["urls"] == 2
    assert result["bunge_bigredsky_board"] is True
    assert result["rich_rows"] == {
        "row_selector": "#brs_report_table_16 tr.oddrow, #brs_report_table_16 tr.evenrow",
        "link_selector": "a[href*='pageID=160'][href*='AdvertID=']",
        "location_selectors": ["td:nth-of-type(5)"],
        "location_separator": ";",
    }
    assert result["pagination"] == {
        "param_name": "reload_data[firstRow]",
        "start": 0,
        "increment": 20,
        "max_pages": 50,
        "transient_403": True,
    }
    assert result["empty_states"] == [
        {
            "selector": "#brs_jbcontent",
            "contains_text": "There are no positions available currently.",
            "forbidden_link_selector": (
                "#brs_report_table_16 a[href*='pageID=160'][href*='AdvertID=']"
            ),
        }
    ]
    config = _validated_rich_rows(result["rich_rows"])
    assert config is not None
    jobs = _extract_rich_rows_static(
        LISTING_HTML,
        BOARD_URL,
        config,
        re.compile(result["url_filter"]),
    )
    assert [(job.title, job.locations) for job in jobs] == [
        ("Maintenance Officer - Electrical", ["Port Adelaide"]),
        (
            "Harvest Employment - 2026/2027",
            ["Eyre Peninsula SA", "Eastern SA/Vic", "Central SA"],
        ),
    ]


def test_bigredsky_auto_scraper_enriches_only_description():
    monitor_config = _bigredsky_probe_config(LISTING_HTML, BOARD_URL)
    assert monitor_config is not None

    auto = auto_scraper_type("dom", monitor_config)
    assert auto is not None
    scraper_type, scraper_config = auto
    assert scraper_type == "dom"
    assert scraper_config is not None
    assert scraper_config["enrich"] == ["description"]

    content = parse_html(DETAIL_HTML, scraper_config)
    assert content.title == "Maintenance Officer - Electrical"
    assert content.description is not None
    assert "Maintain terminal equipment safely" in content.description
    assert "Perform preventive maintenance" in content.description


async def test_bigredsky_can_handle_returns_provider_preset():
    with patch(
        "src.core.monitors.fetch_page_text",
        new=AsyncMock(return_value=LISTING_HTML),
    ):
        result = await can_handle(BOARD_URL, MagicMock())

    assert result == _bigredsky_probe_config(LISTING_HTML, BOARD_URL)


def test_bigredsky_probe_rejects_unrelated_hosts():
    assert _bigredsky_probe_config(LISTING_HTML, "https://example.com/page.php") is None
    assert _bigredsky_probe_config(LISTING_HTML, "https://doh.bigredsky.com/page.php") is None


async def test_bunge_bigredsky_explicit_empty_state_skips_pagination():
    detected = _bigredsky_probe_config(LISTING_HTML, BOARD_URL)
    assert detected is not None
    config = {key: value for key, value in detected.items() if key != "urls"}
    empty_html = """
    <html><body>
      <div id="brs_jbcontent">There are no positions available currently.</div>
      <div id="brs-logo"><a href="https://www.bigredsky.com">
        BigRedSky e-Recruitment
      </a></div>
    </body></html>
    """
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=empty_html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await monitor_one(BOARD_URL, "dom", config, client)

    assert result.jobs_by_url == {}
    assert len(requests) == 1
