from __future__ import annotations

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import all_monitor_types, api_monitor_types
from src.core.monitors.paynet import API_URL, can_handle, discover
from src.redis_queue import _KNOWN_ATS_DOMAINS
from src.shared.paynet import paynet_company_from_url
from src.workspace._compat import auto_scraper_type, auto_skip_crawler_types, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

COMPANY_ID = "TMS7300"
BOARD_URL = "https://www.pay-netonline.com/PayNet/Applicant/Postings.aspx?co=" + COMPANY_ID
POSTING_ID = "7b276a9c-6221-4637-9c0d-87d8eb281651"


def _row(
    *,
    posting_id: object = POSTING_ID,
    title: object = "Registered Nurse",
) -> dict:
    return {
        "ID": posting_id,
        "Company": {"Name": "Sunset Manor Inc"},
        "Position": {
            "Title": title,
            "Description": "<p>Provide resident-centered nursing care.</p>",
            "Text": "Provide resident-centered nursing care.",
            "Requirements": "<ul><li>Active nursing license</li></ul>",
            "Location": "Irene, SD",
            "StartDate": "7/1/2025",
            "EndDate": "10/31/2026",
        },
    }


class TestPayNetUrls:
    def test_extracts_company_from_exact_unfiltered_board(self):
        assert paynet_company_from_url(BOARD_URL) == COMPANY_ID
        assert paynet_company_from_url(BOARD_URL.replace("www.", "")) == COMPANY_ID

    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL.replace("https://", "http://"),
            BOARD_URL.replace("www.pay-netonline.com", "pay-netonline.com.evil.test"),
            BOARD_URL.replace("www.pay-netonline.com", "user@www.pay-netonline.com"),
            BOARD_URL + "&location=Irene",
            BOARD_URL + "#jobs",
            "https://www.pay-netonline.com/PayNet/Applicant/Posting.aspx?co=TMS7300",
            "https://www.pay-netonline.com/PayNet/Applicant/Postings.aspx?co=",
        ],
    )
    def test_rejects_untrusted_filtered_or_malformed_urls(self, url: str):
        assert paynet_company_from_url(url) is None


class TestPayNetMonitor:
    async def test_discovers_complete_rich_jobs_from_201_response(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, json=[_row()], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover({"board_url": BOARD_URL}, client)

        assert len(jobs) == 1
        job = jobs[0]
        assert str(seen[0].url) == f"{API_URL}?company_id={COMPANY_ID}"
        assert seen[0].headers["accept"] == "application/json"
        assert job.url == (
            f"https://www.pay-netonline.com/PayNet/Applicant/Posting.aspx?JobPostingID={POSTING_ID}"
        )
        assert job.title == "Registered Nurse"
        assert "resident-centered nursing care" in (job.description or "")
        assert "<h3>Requirements</h3>" in (job.description or "")
        assert job.locations == ["Irene, SD"]
        assert job.date_posted == "2025-07-01"
        assert job.extras == {"qualifications": "<ul><li>Active nursing license</li></ul>"}
        assert job.metadata == {
            "posting_id": POSTING_ID,
            "company_name": "Sunset Manor Inc",
            "valid_through": "2026-10-31",
        }
        assert job.source_identity == f"paynet:tms7300:{POSTING_ID}"

    async def test_empty_board_is_valid(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(201, json=[], request=request)
            )
        ) as client:
            assert await discover({"board_url": BOARD_URL}, client) == []

    async def test_invalid_rows_suppress_gone_detection(self):
        payload = [_row(), _row(posting_id="not-a-uuid")]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(201, json=payload, request=request)
            )
        ) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {
            f"https://www.pay-netonline.com/PayNet/Applicant/Posting.aspx?JobPostingID={POSTING_ID}"
        }

    async def test_probe_validates_live_payload_and_reports_count(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(201, json=[_row(), _row()], request=request)
            )
        ) as client:
            assert await can_handle(BOARD_URL, client) == {
                "company_id": COMPANY_ID,
                "jobs": 2,
            }


def test_workspace_and_runtime_integration():
    assert "paynet" in all_monitor_types()
    assert "paynet" in api_monitor_types()
    assert "paynet" in auto_skip_crawler_types()
    assert "paynet" in _KNOWN_ATS_DOMAINS
    assert detect_ats_from_url(BOARD_URL) == "paynet"
    assert auto_scraper_type("paynet") == ("skip", None)
    assert "paynet" in MONITOR_CARDS
    assert "paynet" in _MONITOR_CONFIG_HINTS


def test_career_discovery_finds_paynet_link():
    candidates = _scan_ats_urls_in_html(f'<a href="{BOARD_URL}">Open roles</a>')
    assert [candidate.url for candidate in candidates] == [BOARD_URL]
