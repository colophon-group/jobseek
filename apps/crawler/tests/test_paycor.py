from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import httpx

from src.core.scrapers.paycor import can_handle, parse_html, scrape
from src.workspace._compat import all_scraper_types
from src.workspace.commands.help import SCRAPER_CARDS

DETAIL_HTML = """
<html>
  <h1>JOIN OUR TEAM</h1>
  <table id="gnewtonJobDescription">
    <tr><td id="gnewtonJobPosition">
      <b>Position:</b>&nbsp; Systems Engineer
    </td></tr>
    <tr>
      <td id="gnewtonJobLocation"><b>Location:</b></td>
      <td id="gnewtonJobLocationInfo"> Salt Lake City, UT<br></td>
    </tr>
    <tr><td id="gnewtonJobID"><b>Job Id:</b>&nbsp; 1234 </td></tr>
    <tr><td id="gnewtonJobOpening"><b># of Openings:</b>&nbsp; 2 </td></tr>
    <tr>
      <td id="gnewtonJobDescriptionText">
        <p>Design signalling systems for rail vehicles.</p>
        <h3>Qualifications</h3>
        <ul><li>Engineering degree</li></ul>
      </td>
    </tr>
  </table>
</html>
"""


def test_parse_html_uses_job_fields_instead_of_generic_heading():
    result = parse_html(DETAIL_HTML)

    assert result.title == "Systems Engineer"
    assert result.locations == ["Salt Lake City, UT"]
    assert result.description == (
        "<p>Design signalling systems for rail vehicles.</p>\n"
        "        <h3>Qualifications</h3>\n"
        "        <ul><li>Engineering degree</li></ul>"
    )
    assert result.metadata == {"job_id": "1234", "openings": "2"}


def test_can_handle_requires_newton_job_detail_markup_on_every_sample():
    assert can_handle([DETAIL_HTML, DETAIL_HTML]) == {}
    assert can_handle([]) is None
    assert can_handle([DETAIL_HTML, "<html>not a Newton detail page</html>"]) is None


async def test_scrape_fetches_static_html():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=DETAIL_HTML, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scrape(
            "https://recruitingbypaycor.com/career/JobIntroduction.action?id=abc",
            {},
            client,
        )

    assert result.title == "Systems Engineer"
    assert result.description


async def test_scrape_returns_empty_content_for_failed_detail():
    transport = httpx.MockTransport(lambda request: httpx.Response(404, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await scrape(
            "https://recruitingbypaycor.com/career/JobIntroduction.action?id=gone",
            {},
            client,
        )

    assert result.title is None
    assert result.description is None


def test_workspace_surfaces_include_paycor_scraper():
    assert "paycor" in all_scraper_types()
    assert "paycor" in SCRAPER_CARDS


def test_stadler_boards_partition_central_feed_and_canonicalize_paycor_urls():
    with (Path(__file__).parents[1] / "data" / "boards.csv").open(newline="") as handle:
        boards = {
            row["board_slug"]: json.loads(row["monitor_config"] or "{}")
            for row in csv.DictReader(handle)
            if row["company_slug"] == "stadler-rail"
        }

    assert boards["stadler-rail-corporate-global"]["item_filter"] == {
        "exclude": {
            'attributes."25"': ["Switzerland", "USA"],
        },
        "dedupe_by": ["hk_id", "szas.sza_apply_link"],
    }
    transform = boards["stadler-rail-us-paycor"]["url_transform"]
    source_url = (
        "https://recruitingbypaycor.com/career/JobIntroduction.action"
        "?clientId=client&id=stable&source=Company+Website&lang=en"
    )
    assert re.sub(transform["find"], transform["replace"], source_url) == (
        "https://recruitingbypaycor.com/career/JobIntroduction.action?clientId=client&id=stable"
    )
