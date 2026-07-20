from __future__ import annotations

import httpx
import pytest

from src.core.monitors import BoardGoneError
from src.core.monitors.paylocity import (
    _extract_page_data,
    _is_paylocity_url,
    can_handle,
    discover,
)
from src.core.scrapers.paylocity import can_handle as scraper_can_handle
from src.core.scrapers.paylocity import parse_html, scrape
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

BOARD_URL = (
    "https://recruiting.paylocity.com/Recruiting/Jobs/All/"
    "8759d8d9-b6f5-49b5-b817-f3c4f69a25ed/ADC-Therapeutics-America-Inc"
)


def _listing_html(jobs: list[dict]) -> str:
    import json

    return f"<script>window.pageData = {json.dumps({'Jobs': jobs})};</script>"


DETAIL_HTML = """
<html>
  <script>window.ATSPublicBaseUrl = '/Recruiting/';</script>
  <div class="job-preview-header">
    <span class="job-preview-title left"><span>Senior Scientist</span></span>
    <div class="preview-location">
      Hybrid Remote <span>&bull;</span> New Providence, New Jersey
      <span>&bull;</span> Research
    </div>
  </div>
  <div class="job-preview-details">
    <div class="vertical-padding">
      <div class="job-listing-header">Job Type</div>
      <div>Full-time</div>
    </div>
    <div class="job-listing-header">Description</div>
    <div><p>Develop antibody-drug conjugates.</p><ul><li>Lead studies</li></ul></div>
  </div>
</html>
"""


class TestListingParser:
    def test_extracts_page_data_with_brace_semicolon_in_text(self):
        html = _listing_html([{"JobId": 1, "Description": "text }; still JSON"}])
        assert _extract_page_data(html) == {
            "Jobs": [{"JobId": 1, "Description": "text }; still JSON"}]
        }

    def test_missing_page_data(self):
        assert _extract_page_data("<html></html>") is None

    def test_url_detection(self):
        assert _is_paylocity_url(BOARD_URL)
        assert _is_paylocity_url("https://2000recruiting.paylocity.com/Recruiting/Jobs/All/abc")
        assert not _is_paylocity_url("https://www.paylocity.com/company/careers")


class TestMonitor:
    async def test_discovers_rich_summaries(self):
        jobs = [
            {
                "JobId": 123,
                "JobTitle": "Senior Scientist",
                "LocationName": "Hybrid - New Providence, NJ",
                "PublishedDate": "2026-07-10T10:00:00-04:00",
                "HiringDepartment": "Research",
                "IsRemote": False,
            },
            {
                "JobId": 456,
                "JobTitle": "Field Specialist",
                "LocationName": "Remote Worker - US",
                "PublishedDate": "2026-07-09T10:00:00-04:00",
                "IsRemote": True,
            },
        ]

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing_html(jobs), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert [job.title for job in result] == ["Senior Scientist", "Field Specialist"]
        assert result[0].url.endswith("/Recruiting/Jobs/Details/123")
        assert result[0].locations == ["Hybrid - New Providence, NJ"]
        assert result[0].job_location_type == "hybrid"
        assert result[0].date_posted == "2026-07-10T10:00:00-04:00"
        assert result[0].metadata == {"job_id": 123, "department": "Research"}
        assert result[1].job_location_type == "remote"

    async def test_empty_board_is_valid(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing_html([]), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == []
            assert await can_handle(BOARD_URL, client) == {"jobs": 0}

    async def test_missing_jobs_array_raises(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="<script>window.pageData = {}; </script>",
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="pageData.Jobs is not a list"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_invalid_page_is_not_detected(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html></html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) is None

    async def test_missing_board_is_gone_and_not_detected(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(404, text="missing", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": BOARD_URL}, client)
            assert await can_handle(BOARD_URL, client) is None

    async def test_direct_url_detects_without_client(self):
        assert await can_handle(BOARD_URL) == {}
        assert await can_handle("https://example.com/jobs") is None


class TestScraper:
    def test_parses_detail_page(self):
        result = parse_html(DETAIL_HTML)
        assert result.title == "Senior Scientist"
        assert result.description == (
            "<p>Develop antibody-drug conjugates.</p><ul><li>Lead studies</li></ul>"
        )
        assert result.locations == ["New Providence, New Jersey"]
        assert result.employment_type == "Full-time"
        assert result.job_location_type == "hybrid"

    def test_parses_map_location_as_onsite(self):
        start = DETAIL_HTML.index('<div class="preview-location">')
        end = DETAIL_HTML.index("</div>", start) + len("</div>")
        replacement = (
            '<div class="preview-location"><a href="https://maps.google.com/maps?q=Lausanne">'
            "Lausanne, Switzerland</a></div>"
        )
        html = DETAIL_HTML[:start] + replacement + DETAIL_HTML[end:]
        result = parse_html(html)
        assert result.locations == ["Lausanne, Switzerland"]
        assert result.job_location_type == "onsite"

    def test_scraper_detection(self):
        assert scraper_can_handle([DETAIL_HTML]) == {}
        assert scraper_can_handle(["<html>not Paylocity</html>"]) is None

    async def test_scrape_fetches_static_html(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=DETAIL_HTML, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape(
                "https://recruiting.paylocity.com/Recruiting/Jobs/Details/123",
                {},
                client,
            )
        assert result.title == "Senior Scientist"
        assert result.description


def test_workspace_auto_configuration():
    assert detect_ats_from_url(BOARD_URL) == "paylocity"
    assert auto_scraper_type("paylocity") == (
        "paylocity",
        {"enrich": ["description", "employment_type", "job_location_type"]},
    )
