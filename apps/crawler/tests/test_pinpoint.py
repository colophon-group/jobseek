from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.pinpoint import (
    _build_description,
    _build_location,
    _parse_job,
    _parse_salary,
    _slug_from_url,
    can_handle,
    discover,
)


class TestSlugFromUrl:
    def test_standard_url(self):
        assert _slug_from_url("https://acme.pinpointhq.com") == "acme"

    def test_with_path(self):
        assert _slug_from_url("https://acme.pinpointhq.com/postings/123") == "acme"

    def test_ignored_slug_www(self):
        assert _slug_from_url("https://www.pinpointhq.com") is None

    def test_ignored_slug_api(self):
        assert _slug_from_url("https://api.pinpointhq.com") is None

    def test_unrelated_url(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_hyphenated_slug(self):
        assert _slug_from_url("https://my-company.pinpointhq.com") == "my-company"


class TestBuildDescription:
    def test_all_four_sections(self):
        posting = {
            "description": "<p>About the role</p>",
            "key_responsibilities": "<ul><li>Build stuff</li></ul>",
            "key_responsibilities_header": "Responsibilities",
            "skills_knowledge_expertise": "<ul><li>Python</li></ul>",
            "skills_knowledge_expertise_header": "Skills",
            "benefits": "<ul><li>Health</li></ul>",
            "benefits_header": "Benefits",
        }
        result = _build_description(posting)
        assert result is not None
        assert "<p>About the role</p>" in result
        assert "<h3>Responsibilities</h3>" in result
        assert "<ul><li>Build stuff</li></ul>" in result
        assert "<h3>Skills</h3>" in result
        assert "<h3>Benefits</h3>" in result

    def test_description_only(self):
        posting = {"description": "<p>Just a description</p>"}
        result = _build_description(posting)
        assert result == "<p>Just a description</p>"

    def test_some_sections_missing(self):
        posting = {
            "description": "<p>About</p>",
            "benefits": "<p>Perks</p>",
            "benefits_header": "Perks & Benefits",
        }
        result = _build_description(posting)
        assert result is not None
        assert "<p>About</p>" in result
        assert "<h3>Perks & Benefits</h3>" in result
        assert "<p>Perks</p>" in result

    def test_headers_omitted_when_not_provided(self):
        posting = {
            "key_responsibilities": "<ul><li>Code</li></ul>",
        }
        result = _build_description(posting)
        assert result is not None
        assert "<h3>" not in result
        assert "<ul><li>Code</li></ul>" in result

    def test_empty_posting(self):
        assert _build_description({}) is None

    def test_non_string_values_skipped(self):
        posting = {"description": 123}
        assert _build_description(posting) is None


class TestBuildLocation:
    def test_name_preferred(self):
        posting = {"location": {"name": "London, UK", "city": "London", "province": "England"}}
        assert _build_location(posting) == ["London, UK"]

    def test_city_province_fallback(self):
        posting = {"location": {"city": "Berlin", "province": "Brandenburg"}}
        assert _build_location(posting) == ["Berlin, Brandenburg"]

    def test_city_only(self):
        posting = {"location": {"city": "Paris"}}
        assert _build_location(posting) == ["Paris"]

    def test_province_only(self):
        posting = {"location": {"province": "California"}}
        assert _build_location(posting) == ["California"]

    def test_empty_location_object(self):
        posting = {"location": {}}
        assert _build_location(posting) is None

    def test_no_location_key(self):
        assert _build_location({}) is None

    def test_location_not_dict(self):
        posting = {"location": "string-location"}
        assert _build_location(posting) is None


class TestParseSalary:
    def test_hourly(self):
        posting = {
            "compensation_minimum": 25,
            "compensation_maximum": 40,
            "compensation_currency": "USD",
            "compensation_frequency": "per_hour",
            "compensation_visible": True,
        }
        result = _parse_salary(posting)
        assert result == {"currency": "USD", "min": 25, "max": 40, "unit": "hour"}

    def test_monthly(self):
        posting = {
            "compensation_minimum": 5000,
            "compensation_maximum": 7000,
            "compensation_currency": "EUR",
            "compensation_frequency": "monthly",
        }
        result = _parse_salary(posting)
        assert result == {"currency": "EUR", "min": 5000, "max": 7000, "unit": "month"}

    def test_weekly(self):
        posting = {
            "compensation_minimum": 1000,
            "compensation_maximum": 1500,
            "compensation_currency": "GBP",
            "compensation_frequency": "two_weeks",
        }
        result = _parse_salary(posting)
        assert result == {"currency": "GBP", "min": 1000, "max": 1500, "unit": "week"}

    def test_daily(self):
        posting = {
            "compensation_minimum": 200,
            "compensation_maximum": 300,
            "compensation_currency": "USD",
            "compensation_frequency": "per_day",
        }
        result = _parse_salary(posting)
        assert result == {"currency": "USD", "min": 200, "max": 300, "unit": "day"}

    def test_daily_string_without_day_substring(self):
        # "daily" does not contain the substring "day" (d-a-i-l-y vs d-a-y),
        # so it falls through to the default "year"
        posting = {
            "compensation_minimum": 200,
            "compensation_maximum": 300,
            "compensation_currency": "USD",
            "compensation_frequency": "daily",
        }
        result = _parse_salary(posting)
        assert result == {"currency": "USD", "min": 200, "max": 300, "unit": "year"}

    def test_annual_default(self):
        posting = {
            "compensation_minimum": 60000,
            "compensation_maximum": 80000,
            "compensation_currency": "USD",
            "compensation_frequency": "annually",
        }
        result = _parse_salary(posting)
        assert result == {"currency": "USD", "min": 60000, "max": 80000, "unit": "year"}

    def test_compensation_visible_false(self):
        posting = {
            "compensation_minimum": 50000,
            "compensation_maximum": 70000,
            "compensation_visible": False,
        }
        assert _parse_salary(posting) is None

    def test_both_none(self):
        posting = {"compensation_minimum": None, "compensation_maximum": None}
        assert _parse_salary(posting) is None

    def test_no_compensation_fields(self):
        assert _parse_salary({}) is None

    def test_min_only(self):
        posting = {
            "compensation_minimum": 50000,
            "compensation_maximum": None,
            "compensation_currency": "USD",
            "compensation_frequency": "",
        }
        result = _parse_salary(posting)
        assert result == {"currency": "USD", "min": 50000, "max": None, "unit": "year"}


class TestParseJob:
    def test_full_posting(self):
        posting = {
            "url": "https://acme.pinpointhq.com/postings/1",
            "title": "Software Engineer",
            "description": "<p>About the role</p>",
            "key_responsibilities": "<ul><li>Build</li></ul>",
            "key_responsibilities_header": "Responsibilities",
            "location": {"name": "London"},
            "employment_type": "full_time",
            "workplace_type": "hybrid",
            "deadline_at": "2024-12-31",
            "compensation_minimum": 50000,
            "compensation_maximum": 70000,
            "compensation_currency": "GBP",
            "compensation_frequency": "annually",
            "compensation_visible": True,
            "job": {
                "department": {"name": "Engineering"},
                "division": {"name": "Product"},
                "requisition_id": "REQ-001",
            },
        }
        result = _parse_job(posting)
        assert result is not None
        assert result.url == "https://acme.pinpointhq.com/postings/1"
        assert result.title == "Software Engineer"
        assert result.description is not None
        assert "<p>About the role</p>" in result.description
        assert result.locations == ["London"]
        assert result.employment_type == "Full-time"
        assert result.job_location_type == "hybrid"
        assert result.date_posted == "2024-12-31"
        assert result.base_salary == {"currency": "GBP", "min": 50000, "max": 70000, "unit": "year"}
        assert result.metadata == {
            "department": "Engineering",
            "division": "Product",
            "requisition_id": "REQ-001",
        }

    def test_missing_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"title": "No URL"}) is None

    def test_employment_type_mapping(self):
        for raw, expected in [
            ("full_time", "Full-time"),
            ("permanent_full_time", "Full-time"),
            ("part_time", "Part-time"),
            ("contract_temp", "Contract"),
            ("internship", "Intern"),
            ("temporary", "Temporary"),
            ("volunteer", "Volunteer"),
        ]:
            posting = {"url": "https://example.com/job", "employment_type": raw}
            result = _parse_job(posting)
            assert result.employment_type == expected, f"Failed for {raw}"

    def test_employment_type_fallback_to_text(self):
        posting = {
            "url": "https://example.com/job",
            "employment_type": "unknown_code",
            "employment_type_text": "Seasonal",
        }
        result = _parse_job(posting)
        assert result.employment_type == "Seasonal"

    def test_employment_type_no_text_fallback(self):
        posting = {
            "url": "https://example.com/job",
            "employment_type": "",
        }
        result = _parse_job(posting)
        assert result.employment_type is None

    def test_workplace_type_remote(self):
        posting = {"url": "https://example.com/job", "workplace_type": "remote"}
        result = _parse_job(posting)
        assert result.job_location_type == "remote"

    def test_workplace_type_onsite(self):
        posting = {"url": "https://example.com/job", "workplace_type": "onsite"}
        result = _parse_job(posting)
        assert result.job_location_type == "onsite"

    def test_workplace_type_unknown(self):
        posting = {"url": "https://example.com/job", "workplace_type": "unknown"}
        result = _parse_job(posting)
        assert result.job_location_type is None

    def test_nested_job_metadata(self):
        posting = {
            "url": "https://example.com/job",
            "job": {
                "department": {"name": "Sales"},
                "division": {"name": "EMEA"},
                "requisition_id": "R-42",
            },
        }
        result = _parse_job(posting)
        assert result.metadata == {
            "department": "Sales",
            "division": "EMEA",
            "requisition_id": "R-42",
        }

    def test_no_metadata(self):
        posting = {"url": "https://example.com/job"}
        result = _parse_job(posting)
        assert result.metadata is None

    def test_job_metadata_empty_dept(self):
        posting = {
            "url": "https://example.com/job",
            "job": {"department": {"name": ""}, "division": None},
        }
        result = _parse_job(posting)
        assert result.metadata is None


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "url": "https://acme.pinpointhq.com/postings/1",
                            "title": "Engineer",
                            "description": "Desc",
                        },
                        {
                            "url": "https://acme.pinpointhq.com/postings/2",
                            "title": "Designer",
                            "description": "Desc 2",
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.pinpointhq.com",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json={"data": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.pinpointhq.com",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Pinpoint slug"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "myslug" in str(request.url)
            return httpx.Response(200, json={"data": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {"slug": "myslug"}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_slug_from_board_url(self):
        def handler(request):
            assert "acme" in str(request.url)
            return httpx.Response(200, json={"data": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.pinpointhq.com", "metadata": {}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_skips_jobs_without_url(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"title": "No URL"},
                        {
                            "url": "https://acme.pinpointhq.com/postings/1",
                            "title": "Has URL",
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.pinpointhq.com",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Has URL"

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.pinpointhq.com",
                "metadata": {"slug": "acme"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)


class TestCanHandle:
    async def test_pinpoint_url(self):
        result = await can_handle("https://acme.pinpointhq.com")
        assert result == {"slug": "acme"}

    async def test_non_pinpoint_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "pinpointhq.com/postings.json" in url:
                return httpx.Response(200, json={"data": [{"id": 1}]})
            return httpx.Response(
                200,
                text='<html><a href="https://myco.pinpointhq.com/postings/1">Apply</a></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result.get("slug") == "myco"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "pinpointhq.com/postings.json" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no pinpoint refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None

    async def test_url_match_with_api_probe(self):
        def handler(request):
            return httpx.Response(200, json={"data": [{"id": 1}, {"id": 2}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.pinpointhq.com", client)
            assert result is not None
            assert result["slug"] == "acme"
            assert result["jobs"] == 2
