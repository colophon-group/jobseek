from __future__ import annotations

import httpx
import pytest

from src.core.monitors.lever import (
    _api_url,
    _build_description,
    _parse_job,
    _parse_salary,
    _token_from_url,
    can_handle,
    discover,
)


class TestBuildDescription:
    def test_description_only(self):
        posting = {"description": "<p>About the role</p>"}
        assert _build_description(posting) == "<p>About the role</p>"

    def test_with_lists(self):
        posting = {
            "description": "<p>Intro</p>",
            "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
        }
        result = _build_description(posting)
        assert "<p>Intro</p>" in result
        assert "<h3>Requirements</h3>" in result
        assert "<li>Python</li>" in result

    def test_with_additional(self):
        posting = {"additional": "<p>Benefits</p>"}
        assert _build_description(posting) == "<p>Benefits</p>"

    def test_all_sections(self):
        posting = {
            "description": "Intro",
            "lists": [{"text": "Reqs", "content": "items"}],
            "additional": "Extra",
        }
        result = _build_description(posting)
        assert "Intro" in result
        assert "Reqs" in result
        assert "Extra" in result

    def test_empty(self):
        assert _build_description({}) is None

    def test_empty_sections(self):
        posting = {"description": "", "lists": [{"text": "", "content": ""}]}
        assert _build_description(posting) is None


class TestParseSalary:
    def test_per_year(self):
        salary_range = {
            "currency": "USD",
            "min": 100000,
            "max": 150000,
            "interval": "per-year-salary",
        }
        result = _parse_salary(salary_range)
        assert result == {"currency": "USD", "min": 100000, "max": 150000, "unit": "year"}

    def test_per_hour(self):
        salary_range = {
            "currency": "USD",
            "min": 50,
            "max": 80,
            "interval": "per-hour-wage",
        }
        result = _parse_salary(salary_range)
        assert result["unit"] == "hour"

    def test_per_month(self):
        salary_range = {
            "currency": "EUR",
            "min": 5000,
            "max": 7000,
            "interval": "per-month-salary",
        }
        result = _parse_salary(salary_range)
        assert result["unit"] == "month"

    def test_unknown_interval(self):
        result = _parse_salary({"currency": "USD", "min": 100, "max": 200, "interval": "custom"})
        assert result["unit"] == "custom"

    def test_none(self):
        assert _parse_salary(None) is None

    def test_no_min_max(self):
        assert _parse_salary({"currency": "USD"}) is None

    def test_only_min(self):
        result = _parse_salary({"currency": "USD", "min": 100000, "interval": "per-year-salary"})
        assert result is not None
        assert result["min"] == 100000
        assert result["max"] is None


class TestParseJob:
    def test_basic(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "text": "Engineer",
            "categories": {"location": "NYC"},
        }
        result = _parse_job(posting)
        assert result is not None
        assert result.url == "https://jobs.lever.co/test/123"
        assert result.title == "Engineer"
        assert result.locations == ["NYC"]

    def test_missing_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"text": "No URL"}) is None

    def test_all_locations(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {"allLocations": ["NYC", "London", "Berlin"]},
        }
        result = _parse_job(posting)
        assert result.locations == ["NYC", "London", "Berlin"]

    def test_single_location_fallback(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {"location": "Remote"},
        }
        result = _parse_job(posting)
        assert result.locations == ["Remote"]

    def test_no_locations(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {},
        }
        result = _parse_job(posting)
        assert result.locations is None

    def test_metadata_team_and_department(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {"team": "Platform", "department": "Engineering"},
            "id": "abc123",
        }
        result = _parse_job(posting)
        assert result.metadata["team"] == "Platform"
        assert result.metadata["department"] == "Engineering"
        assert result.metadata["id"] == "abc123"

    def test_employment_type(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {"commitment": "Full-time"},
        }
        result = _parse_job(posting)
        assert result.employment_type == "Full-time"

    def test_workplace_type(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "workplaceType": "remote",
            "categories": {},
        }
        result = _parse_job(posting)
        assert result.job_location_type == "remote"

    def test_salary(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "salaryRange": {
                "currency": "USD",
                "min": 100000,
                "max": 150000,
                "interval": "per-year-salary",
            },
            "categories": {},
        }
        result = _parse_job(posting)
        assert result.base_salary is not None
        assert result.base_salary["currency"] == "USD"
        assert result.base_salary["min"] == 100000

    def test_no_metadata_when_empty(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {},
        }
        result = _parse_job(posting)
        assert result.metadata is None


class TestTokenFromUrl:
    def test_standard(self):
        assert _token_from_url("https://jobs.lever.co/stripe") == "stripe"

    def test_with_path(self):
        assert _token_from_url("https://jobs.lever.co/stripe/123") == "stripe"

    def test_with_hyphen(self):
        assert _token_from_url("https://jobs.lever.co/my-company") == "my-company"

    def test_no_match(self):
        assert _token_from_url("https://example.com/careers") is None

    def test_ignore_token(self):
        assert _token_from_url("https://jobs.lever.co/v0") is None


class TestApiUrl:
    def test_basic(self):
        assert _api_url("stripe") == "https://api.lever.co/v0/postings/stripe"


class TestDiscover:
    async def test_single_page(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {
                        "hostedUrl": "https://jobs.lever.co/test/1",
                        "text": "Job 1",
                        "categories": {},
                    },
                    {
                        "hostedUrl": "https://jobs.lever.co/test/2",
                        "text": "Job 2",
                        "categories": {},
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            jobs = await discover(board, client)
            assert len(jobs) == 2
            titles = {j.title for j in jobs}
            assert titles == {"Job 1", "Job 2"}

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_token_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Lever token"):
                await discover(board, client)

    async def test_skips_jobs_without_url(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {"text": "No URL", "categories": {}},
                    {
                        "hostedUrl": "https://jobs.lever.co/test/1",
                        "text": "Has URL",
                        "categories": {},
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Has URL"

    async def test_pagination(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            params = dict(request.url.params)
            skip = int(params.get("skip", 0))
            if skip == 0:
                # Full batch of 100
                return httpx.Response(
                    200,
                    json=[
                        {
                            "hostedUrl": f"https://jobs.lever.co/test/{i}",
                            "text": f"Job {i}",
                            "categories": {},
                        }
                        for i in range(100)
                    ],
                )
            else:
                # Partial batch — end of pages
                return httpx.Response(
                    200,
                    json=[
                        {
                            "hostedUrl": f"https://jobs.lever.co/test/{i}",
                            "text": f"Job {i}",
                            "categories": {},
                        }
                        for i in range(100, 110)
                    ],
                )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            jobs = await discover(board, client)
            assert len(jobs) == 110
            assert call_count == 2

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)


class TestCanHandle:
    async def test_lever_url(self):
        result = await can_handle("https://jobs.lever.co/stripe")
        assert result == {"token": "stripe"}

    async def test_non_lever_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_in_page_html(self):
        def handler(request):
            return httpx.Response(
                200,
                text='<html><script src="https://api.lever.co/v0/postings/myco"></script></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result.get("token") == "myco"

    async def test_probe_fallback(self):
        def handler(request):
            url = str(request.url)
            if "api.lever.co" in url:
                return httpx.Response(200, json=[])
            return httpx.Response(200, text="<html>plain page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is not None
            assert result.get("token") == "example"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "api.lever.co" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no lever refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None
