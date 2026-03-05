from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.rippling import (
    _parse_employment_type,
    _parse_job,
    _parse_job_location_type,
    _parse_salary,
    _slug_from_url,
    can_handle,
    discover,
)


class TestSlugFromUrl:
    def test_standard_url(self):
        assert _slug_from_url("https://ats.rippling.com/acme-corp/jobs") == "acme-corp"

    def test_with_locale_prefix(self):
        assert _slug_from_url("https://ats.rippling.com/en-US/acme-corp/jobs") == "acme-corp"

    def test_us1_subdomain(self):
        assert _slug_from_url("https://ats.us1.rippling.com/acme-corp/jobs") == "acme-corp"

    def test_us1_with_locale(self):
        assert _slug_from_url("https://ats.us1.rippling.com/fr-FR/acme-corp/jobs") == "acme-corp"

    def test_ignored_slug(self):
        assert _slug_from_url("https://ats.rippling.com/api/jobs") is None
        assert _slug_from_url("https://ats.rippling.com/static/jobs") is None

    def test_non_matching_url(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_with_trailing_path(self):
        assert _slug_from_url("https://ats.rippling.com/acme/jobs/some-job-id") == "acme"


class TestParseSalary:
    def test_basic_salary(self):
        pay_ranges = [
            {"rangeStart": 50000, "rangeEnd": 80000, "currency": "USD", "frequency": "ANNUAL"}
        ]
        result = _parse_salary(pay_ranges)
        assert result == {"currency": "USD", "min": 50000, "max": 80000, "unit": "year"}

    def test_hourly_frequency(self):
        pay_ranges = [{"rangeStart": 25, "rangeEnd": 50, "currency": "USD", "frequency": "HOURLY"}]
        result = _parse_salary(pay_ranges)
        assert result["unit"] == "hour"

    def test_monthly_frequency(self):
        pay_ranges = [
            {"rangeStart": 4000, "rangeEnd": 6000, "currency": "EUR", "frequency": "MONTHLY"}
        ]
        result = _parse_salary(pay_ranges)
        assert result["unit"] == "month"

    def test_both_none_returns_none(self):
        pay_ranges = [{"rangeStart": None, "rangeEnd": None, "currency": "USD", "frequency": ""}]
        assert _parse_salary(pay_ranges) is None

    def test_empty_list_returns_none(self):
        assert _parse_salary([]) is None

    def test_none_returns_none(self):
        assert _parse_salary(None) is None

    def test_uses_first_element(self):
        pay_ranges = [
            {"rangeStart": 100, "rangeEnd": 200, "currency": "USD", "frequency": "HOURLY"},
            {"rangeStart": 999, "rangeEnd": 999, "currency": "GBP", "frequency": "ANNUAL"},
        ]
        result = _parse_salary(pay_ranges)
        assert result["min"] == 100
        assert result["currency"] == "USD"


class TestParseJobLocationType:
    def test_remote_in_list(self):
        assert _parse_job_location_type(["Remote"]) == "remote"

    def test_remote_case_insensitive(self):
        assert _parse_job_location_type(["New York", "remote"]) == "remote"

    def test_no_remote(self):
        assert _parse_job_location_type(["San Francisco", "London"]) is None

    def test_empty_list(self):
        assert _parse_job_location_type([]) is None

    def test_none(self):
        assert _parse_job_location_type(None) is None


class TestParseEmploymentType:
    def test_salaried_ft(self):
        assert _parse_employment_type({"label": "SALARIED_FT"}) == "Full-time"

    def test_intern(self):
        assert _parse_employment_type({"label": "INTERN"}) == "Intern"

    def test_fallback_to_id(self):
        assert _parse_employment_type({"label": "UNKNOWN_TYPE", "id": "Freelance"}) == "Freelance"

    def test_none_emp(self):
        assert _parse_employment_type(None) is None

    def test_empty_label_with_id(self):
        assert _parse_employment_type({"label": "", "id": "Custom"}) == "Custom"


class TestParseJob:
    def test_full_detail(self):
        detail = {
            "url": "https://ats.rippling.com/acme/jobs/123",
            "name": "Software Engineer",
            "description": {"company": "<p>About Acme</p>", "role": "<p>Build stuff</p>"},
            "workLocations": ["San Francisco", "Remote"],
            "department": {"name": "Engineering", "base_department": "Tech"},
            "companyName": "Acme Corp",
            "employmentType": {"label": "SALARIED_FT"},
            "createdOn": "2024-06-01",
            "payRangeDetails": [
                {"rangeStart": 100000, "rangeEnd": 150000, "currency": "USD", "frequency": "ANNUAL"}
            ],
        }
        result = _parse_job(detail)
        assert result is not None
        assert result.url == "https://ats.rippling.com/acme/jobs/123"
        assert result.title == "Software Engineer"
        assert "<p>About Acme</p>" in result.description
        assert "<p>Build stuff</p>" in result.description
        assert result.locations == ["San Francisco", "Remote"]
        assert result.job_location_type == "remote"
        assert result.employment_type == "Full-time"
        assert result.date_posted == "2024-06-01"
        assert result.base_salary is not None
        assert result.metadata["department"] == "Engineering"
        assert result.metadata["base_department"] == "Tech"
        assert result.metadata["company"] == "Acme Corp"

    def test_missing_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"name": "No URL"}) is None

    def test_dual_description(self):
        detail = {
            "url": "https://example.com/job",
            "description": {"company": "Company info", "role": "Role info"},
        }
        result = _parse_job(detail)
        assert result.description == "Company info\nRole info"

    def test_only_role_description(self):
        detail = {
            "url": "https://example.com/job",
            "description": {"role": "Role only"},
        }
        result = _parse_job(detail)
        assert result.description == "Role only"

    def test_no_description(self):
        detail = {"url": "https://example.com/job"}
        result = _parse_job(detail)
        assert result.description is None

    def test_work_locations(self):
        detail = {
            "url": "https://example.com/job",
            "workLocations": ["NYC", "LA"],
        }
        result = _parse_job(detail)
        assert result.locations == ["NYC", "LA"]

    def test_empty_work_locations(self):
        detail = {
            "url": "https://example.com/job",
            "workLocations": [],
        }
        result = _parse_job(detail)
        assert result.locations is None

    def test_metadata_department_dict(self):
        detail = {
            "url": "https://example.com/job",
            "department": {"name": "Sales", "base_department": "Sales"},
        }
        result = _parse_job(detail)
        # base_department == name, so only department is set
        assert result.metadata == {"department": "Sales"}

    def test_no_metadata(self):
        detail = {"url": "https://example.com/job"}
        result = _parse_job(detail)
        assert result.metadata is None


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            url = str(request.url)
            if url.endswith("/jobs"):
                # List endpoint returns UUIDs
                return httpx.Response(
                    200,
                    json=[
                        {"uuid": "aaa"},
                        {"uuid": "bbb"},
                    ],
                )
            if "/jobs/aaa" in url:
                return httpx.Response(
                    200,
                    json={
                        "url": "https://ats.rippling.com/acme/jobs/aaa",
                        "name": "Engineer",
                        "description": {"role": "Build things"},
                    },
                )
            if "/jobs/bbb" in url:
                return httpx.Response(
                    200,
                    json={
                        "url": "https://ats.rippling.com/acme/jobs/bbb",
                        "name": "Designer",
                        "description": {"role": "Design things"},
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://ats.rippling.com/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://ats.rippling.com/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Rippling"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "myslug" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {"slug": "myslug"}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_slug_from_board_url(self):
        def handler(request):
            assert "testco" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://ats.rippling.com/testco/jobs",
                "metadata": {},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_skips_jobs_without_url(self):
        def handler(request):
            url = str(request.url)
            if url.endswith("/jobs"):
                return httpx.Response(200, json=[{"uuid": "x"}])
            # Detail has no url field
            return httpx.Response(200, json={"name": "No URL"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://ats.rippling.com/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://ats.rippling.com/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)


class TestCanHandle:
    async def test_rippling_url_match(self):
        result = await can_handle("https://ats.rippling.com/acme/jobs")
        assert result is not None
        assert result["slug"] == "acme"

    async def test_non_matching_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_url_match_with_client_probe(self):
        def handler(request):
            return httpx.Response(200, json=[{"uuid": "1"}, {"uuid": "2"}])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://ats.rippling.com/acme/jobs", client)
            assert result is not None
            assert result["slug"] == "acme"
            assert result["jobs"] == 2

    async def test_detects_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "api.rippling.com" in url:
                return httpx.Response(200, json=[{"uuid": "1"}])
            return httpx.Response(
                200,
                text='<html><script src="https://ats.rippling.com/myco/jobs"></script></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["slug"] == "myco"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "api.rippling.com" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no rippling refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None
