from __future__ import annotations

import httpx

from src.core.monitors import DiscoveredJob
from src.core.monitors.inploi import (
    _locations,
    _page_metadata,
    _parse_job,
    _salary,
    can_handle,
    discover,
)
from src.workspace._compat import auto_scraper_type, auto_skip_crawler_types

PAGE = (
    r'<html class="inploi-cms"><script>window.data="'
    r"\"sdk\",\"pk_test1234567890\",\"segment_ids\",\"segment\",\"248\""
    r'"</script></html>'
)


def test_page_metadata_extracts_public_configuration():
    assert _page_metadata(PAGE) == ("pk_test1234567890", "248")


def test_page_metadata_rejects_non_inploi_page():
    assert _page_metadata('<script>"segment_ids","segment","248"</script>') is None


def test_inploi_auto_enriches_description():
    assert auto_scraper_type("inploi", {}) == (
        "json-ld",
        {"enrich": ["description"]},
    )
    assert "inploi" not in auto_skip_crawler_types()


def test_locations_deduplicate_parts():
    assert _locations({"town": "London", "city": "London", "country": "UK"}) == ["London, UK"]
    assert _locations({}) is None


def test_salary_maps_visible_hourly_value():
    assert _salary(
        {
            "pay": "15.50",
            "pay_currency": "GBP",
            "pay_type": "HOURLY",
            "pay_display": True,
            "pay_mask": False,
        }
    ) == {"currency": "GBP", "min": 15.5, "max": 15.5, "unit": "hour"}
    assert _salary({"pay": "15", "pay_mask": True}) is None


def test_parse_job_maps_rich_list_fields():
    raw = {
        "id": 123,
        "title": "Chef",
        "city": "Dublin",
        "country": "Republic of Ireland",
        "employment_type": "FULL_TIME",
        "location_type": "LOCATION",
        "created_at": "2026-08-08T12:00:00Z",
        "external_ref": "abc",
        "company_name": "Compass Group Ireland",
        "category": "Catering",
        "custom_data": {"expiry_date": "2026-09-08"},
    }
    job = _parse_job(raw, "https://jobs.example.com/search")
    assert job == DiscoveredJob(
        url="https://jobs.example.com/job/123",
        title="Chef",
        locations=["Dublin, Republic of Ireland"],
        employment_type="FULL_TIME",
        job_location_type="onsite",
        date_posted="2026-08-08T12:00:00Z",
        metadata={
            "id": 123,
            "external_ref": "abc",
            "company_name": "Compass Group Ireland",
            "category": "Catering",
            "valid_through": "2026-09-08",
        },
    )


def test_parse_job_requires_id_and_title():
    assert _parse_job({"title": "Chef"}, "https://jobs.example.com/search") is None
    assert _parse_job({"id": 1}, "https://jobs.example.com/search") is None


async def test_can_handle_checks_search_page_and_api():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.inploi.com":
            assert request.headers["x-publishable-key"] == "pk_test1234567890"
            assert request.url.params["filters[segment_ids][0]"] == "248"
            return httpx.Response(
                200,
                json={"data": [{}], "pagination": {"total": 42, "last_page": 42}},
            )
        if request.url.path == "/":
            return httpx.Response(200, text="<html>Inploi</html>")
        if request.url.path == "/search":
            return httpx.Response(200, text=PAGE)
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await can_handle("https://jobs.example.com/", client)

    assert result == {
        "api_key": "pk_test1234567890",
        "segment_id": "248",
        "search_url": "https://jobs.example.com/search",
        "jobs": 42,
    }


async def test_discover_paginates_and_maps_jobs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-publishable-key"] == "pk_public"
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": page,
                        "title": f"Job {page}",
                        "city": "London",
                        "country": "UK",
                    }
                ],
                "pagination": {"total": 2, "current_page": page, "last_page": 2},
            },
        )

    board = {
        "board_url": "https://jobs.example.com/search",
        "metadata": {"api_key": "pk_public", "segment_id": "248", "page_size": 1},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(board, client)

    assert [job.url for job in jobs] == [
        "https://jobs.example.com/job/1",
        "https://jobs.example.com/job/2",
    ]
    assert [job.title for job in jobs] == ["Job 1", "Job 2"]
