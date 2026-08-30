from __future__ import annotations

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.core.monitors.infor import (
    InforSite,
    bootstrap_session,
    build_job_url,
    can_handle,
    discover,
    parse_candidate_url,
)
from src.core.scrapers.infor import parse_detail, scrape
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

BOARD_URL = (
    "https://tenant.cloud.infor.com:1444/lmghr/CandidateSelfService/lm"
    "?_frommenu=true&context.dataarea=lmghr"
    "&context.session.key.JobBoard=EXTERNAL"
    "&context.session.key.HROrganization=1000"
)
SITE = InforSite(
    origin="https://tenant.cloud.infor.com:1444",
    dataarea="lmghr",
    job_board="EXTERNAL",
    hr_organization="1000",
)


def _bootstrap_response() -> httpx.Response:
    return httpx.Response(
        200,
        text="<html><title>Careers</title></html>",
        headers=[
            ("set-cookie", "JSESSIONID=session; Path=/; Secure"),
            ("set-cookie", "SSO.CSRF=csrf-token; Path=/; Secure"),
        ],
        request=httpx.Request("GET", BOARD_URL),
    )


def _listing_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "JobPostingListWebServices_ListOperationResponseArray": [
                {
                    "JobPostingListWebServices_ListOperationResponse": {
                        "JobRequisition": "3628",
                        "JobPosting": "1",
                        "__Description_translation___": "Staff Psychologist",
                        "LocationOfJob": "US:NY:Oceanside",
                        "PostingDateRange.Begin": "20240711",
                        "WorkType": "B",
                        "Category": "Clinical",
                        "SubCategory": "Behavioral Health",
                    }
                },
                {
                    "JobPostingListWebServices_ListOperationResponse": {
                        "JobRequisition": "4227",
                        "JobPosting": "1",
                        "__Description_translation___": "Chair of Psychiatry",
                        "LocationOfJob": "US:NY:Long Beach",
                        "PostingDateRange.Begin": "20230602",
                        "WorkType": "FT",
                        "Category": "",
                        "SubCategory": "",
                    }
                },
            ]
        },
        request=httpx.Request("GET", "https://tenant.cloud.infor.com/api"),
    )


def _detail_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "Find_PostingDisplay_FormOperationResponse": {
                "JobRequisition": "3628",
                "JobPosting": "1",
                "__Description_translation___": "Staff Psychologist",
                "__PositionDescription_translation___": (
                    "<p><b>Position Summary</b></p><p>Provide patient care.</p>"
                ),
                "__LocationOfJob.Description_translation___": "US:NY:Oceanside",
                "PostingDateRange.Begin": "20240711",
                "Category.Description": "Clinical",
                "RelationshipToOrganization.Description": "EMPLOYEE",
                "JobRequisitionLocation": "SN027",
            }
        },
        request=httpx.Request("GET", "https://tenant.cloud.infor.com/detail"),
    )


def test_parse_candidate_url_and_build_detail_identity() -> None:
    parsed = parse_candidate_url(BOARD_URL)
    assert parsed == (SITE, None, None)

    job_url = build_job_url(SITE, "3628", "1")
    parsed_job = parse_candidate_url(job_url, require_job=True)
    assert parsed_job == (SITE, "3628", "1")
    query = parse_qs(urlparse(job_url).query)
    assert query["JobReq"] == ["3628"]
    assert query["JobPost"] == ["1"]


@pytest.mark.parametrize(
    "url",
    [
        "http://tenant.cloud.infor.com:1444/lmghr/CandidateSelfService/lm"
        "?context.session.key.JobBoard=EXTERNAL"
        "&context.session.key.HROrganization=1000",
        "https://tenant.example.com:1444/lmghr/CandidateSelfService/lm"
        "?context.session.key.JobBoard=EXTERNAL"
        "&context.session.key.HROrganization=1000",
        "https://tenant.cloud.infor.com:1444/lmghr/CandidateSelfService/lm"
        "?context.dataarea=other&context.session.key.JobBoard=EXTERNAL"
        "&context.session.key.HROrganization=1000",
        "https://tenant.cloud.infor.com:1444/lmghr/CandidateSelfService/lm"
        "?context.session.key.JobBoard=EXTERNAL",
    ],
)
def test_parse_candidate_url_rejects_untrusted_or_incomplete_urls(url: str) -> None:
    assert parse_candidate_url(url) is None


async def test_bootstrap_reuses_existing_session_cookies() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "JSESSIONID=existing-session" in request.headers["Cookie"]
        assert "SSO.CSRF=existing-csrf" in request.headers["Cookie"]
        return httpx.Response(200, text="<html><title>Careers</title></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set(
            "JSESSIONID", "existing-session", domain="tenant.cloud.infor.com", path="/"
        )
        client.cookies.set("SSO.CSRF", "existing-csrf", domain="tenant.cloud.infor.com", path="/")

        headers = await bootstrap_session(BOARD_URL, client)

    assert "JSESSIONID=existing-session" in headers["Cookie"]
    assert headers["SSO.CSRF"] == "existing-csrf"


async def test_can_handle_bootstraps_session_and_detects_job_count() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=[_bootstrap_response(), _listing_response()])

    result = await can_handle(BOARD_URL, client)

    assert result == {
        "origin": SITE.origin,
        "dataarea": "lmghr",
        "job_board": "EXTERNAL",
        "hr_organization": "1000",
        "jobs_count": 2,
    }
    bootstrap_call, api_call = client.get.await_args_list
    assert bootstrap_call.kwargs["headers"]["Cookie"] == ""
    assert api_call.kwargs["headers"]["SSO.CSRF"] == "csrf-token"
    assert "JSESSIONID=session" in api_call.kwargs["headers"]["Cookie"]
    assert api_call.kwargs["params"]["_limit"] == "-1"


async def test_discover_returns_rich_summaries_and_stable_job_urls() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=[_bootstrap_response(), _listing_response()])

    jobs = await discover(
        {
            "board_url": BOARD_URL,
            "metadata": {
                "origin": SITE.origin,
                "dataarea": SITE.dataarea,
                "job_board": SITE.job_board,
                "hr_organization": SITE.hr_organization,
            },
        },
        client,
    )

    assert len(jobs) == 2
    assert jobs[0].title == "Staff Psychologist"
    assert jobs[0].locations == ["Oceanside, NY, US"]
    assert jobs[0].date_posted == "2024-07-11"
    assert jobs[0].language == "en"
    assert jobs[0].metadata == {
        "job_requisition": "3628",
        "job_posting": "1",
        "work_type": "B",
        "category": "Clinical",
        "subcategory": "Behavioral Health",
    }
    assert parse_candidate_url(jobs[0].url, require_job=True) == (SITE, "3628", "1")


async def test_scrape_fetches_native_detail_response() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=[_bootstrap_response(), _detail_response()])
    job_url = build_job_url(SITE, "3628", "1")

    content = await scrape(job_url, {"enrich": ["description"]}, client)

    assert content.title == "Staff Psychologist"
    assert content.description == ("<p><b>Position Summary</b></p><p>Provide patient care.</p>")
    assert content.locations == ["Oceanside, NY, US"]
    assert content.date_posted == "2024-07-11"
    assert content.metadata == {
        "job_requisition": "3628",
        "job_posting": "1",
        "category": "Clinical",
        "relationship": "EMPLOYEE",
        "job_requisition_location": "SN027",
    }
    detail_call = client.get.await_args_list[1]
    assert detail_call.kwargs["params"]["JobRequisition"] == "3628"
    assert detail_call.kwargs["params"]["JobPosting"] == "1"
    assert detail_call.kwargs["headers"]["SSO.CSRF"] == "csrf-token"


def test_parse_detail_falls_back_to_untranslated_fields() -> None:
    content = parse_detail(
        {
            "Description": "Fallback title",
            "PositionDescription": "<p>Fallback description</p>",
            "LocationOfJob.Description": "US:NY:Hicksville",
            "PostingDateRange.Begin": "20260203",
        }
    )

    assert content.title == "Fallback title"
    assert content.description == "<p>Fallback description</p>"
    assert content.locations == ["Hicksville, NY, US"]
    assert content.date_posted == "2026-02-03"


def test_workspace_compatibility_auto_configures_infor_enrichment() -> None:
    assert detect_ats_from_url(BOARD_URL) == "infor"
    assert auto_scraper_type("infor") == ("infor", {"enrich": ["description"]})
