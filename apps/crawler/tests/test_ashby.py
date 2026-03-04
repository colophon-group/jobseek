from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.ashby import (
    _api_url,
    _parse_job,
    _parse_locations,
    _token_from_url,
    can_handle,
    discover,
)


class TestParseLocations:
    def test_primary_location(self):
        job = {"location": "New York, NY"}
        assert _parse_locations(job) == ["New York, NY"]

    def test_secondary_locations(self):
        job = {
            "location": "New York",
            "secondaryLocations": [{"location": "London"}, {"location": "Berlin"}],
        }
        assert _parse_locations(job) == ["New York", "London", "Berlin"]

    def test_deduplicates(self):
        job = {
            "location": "HQ",
            "secondaryLocations": [{"location": "HQ"}, {"location": "London"}],
        }
        assert _parse_locations(job) == ["HQ", "London"]

    def test_address_fallback(self):
        job = {"address": {"city": "Berlin", "region": "Berlin", "country": "Germany"}}
        assert _parse_locations(job) == ["Berlin, Berlin, Germany"]

    def test_no_locations(self):
        assert _parse_locations({}) is None

    def test_empty_location_string(self):
        job = {"location": ""}
        assert _parse_locations(job) is None

    def test_secondary_locations_as_strings(self):
        job = {"secondaryLocations": ["London", "Berlin"]}
        assert _parse_locations(job) == ["London", "Berlin"]


class TestParseJob:
    def test_basic(self):
        raw = {
            "jobUrl": "https://jobs.ashbyhq.com/test/123",
            "title": "Engineer",
            "descriptionHtml": "<p>Great job</p>",
            "publishedAt": "2024-01-01T00:00:00Z",
            "location": "Remote",
        }
        result = _parse_job(raw)
        assert result is not None
        assert result.url == "https://jobs.ashbyhq.com/test/123"
        assert result.title == "Engineer"
        assert result.description == "<p>Great job</p>"
        assert result.date_posted == "2024-01-01T00:00:00Z"
        assert result.locations == ["Remote"]

    def test_missing_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"title": "No URL"}) is None

    def test_employment_type_mapping(self):
        raw = {"jobUrl": "https://example.com/job", "employmentType": "FullTime"}
        result = _parse_job(raw)
        assert result.employment_type == "Full-time"

    def test_workplace_type_mapping(self):
        raw = {"jobUrl": "https://example.com/job", "workplaceType": "Remote"}
        result = _parse_job(raw)
        assert result.job_location_type == "remote"

    def test_hybrid_workplace(self):
        raw = {"jobUrl": "https://example.com/job", "workplaceType": "Hybrid"}
        result = _parse_job(raw)
        assert result.job_location_type == "hybrid"

    def test_onsite_workplace(self):
        raw = {"jobUrl": "https://example.com/job", "workplaceType": "OnSite"}
        result = _parse_job(raw)
        assert result.job_location_type == "onsite"

    def test_metadata_department(self):
        raw = {"jobUrl": "https://example.com/job", "department": "Engineering"}
        result = _parse_job(raw)
        assert result.metadata == {"department": "Engineering"}

    def test_metadata_team(self):
        raw = {"jobUrl": "https://example.com/job", "team": "Platform"}
        result = _parse_job(raw)
        assert result.metadata == {"team": "Platform"}

    def test_metadata_id(self):
        raw = {"jobUrl": "https://example.com/job", "id": "abc-123"}
        result = _parse_job(raw)
        assert result.metadata["id"] == "abc-123"

    def test_no_metadata(self):
        raw = {"jobUrl": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.metadata is None

    def test_plain_description_fallback(self):
        raw = {"jobUrl": "https://example.com/job", "descriptionPlain": "Plain text"}
        result = _parse_job(raw)
        assert result.description == "Plain text"

    def test_html_description_preferred(self):
        raw = {
            "jobUrl": "https://example.com/job",
            "descriptionHtml": "<p>HTML</p>",
            "descriptionPlain": "Plain",
        }
        result = _parse_job(raw)
        assert result.description == "<p>HTML</p>"

    def test_no_employment_type(self):
        raw = {"jobUrl": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.employment_type is None

    def test_no_workplace_type(self):
        raw = {"jobUrl": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.job_location_type is None


class TestTokenFromUrl:
    def test_standard_url(self):
        assert _token_from_url("https://jobs.ashbyhq.com/stripe") == "stripe"

    def test_with_path(self):
        assert _token_from_url("https://jobs.ashbyhq.com/stripe/abc-123") == "stripe"

    def test_with_hyphen(self):
        assert _token_from_url("https://jobs.ashbyhq.com/my-company") == "my-company"

    def test_no_match(self):
        assert _token_from_url("https://example.com/careers") is None

    def test_ignore_token(self):
        assert _token_from_url("https://jobs.ashbyhq.com/api") is None


class TestApiUrl:
    def test_basic(self):
        assert _api_url("stripe") == "https://api.ashbyhq.com/posting-api/job-board/stripe"

    def test_with_hyphen(self):
        assert (
            _api_url("my-company")
            == "https://api.ashbyhq.com/posting-api/job-board/my-company"
        )


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "jobUrl": "https://jobs.ashbyhq.com/test/1",
                            "title": "Engineer",
                            "descriptionHtml": "<p>Desc</p>",
                            "isListed": True,
                        },
                        {
                            "jobUrl": "https://jobs.ashbyhq.com/test/2",
                            "title": "Designer",
                            "descriptionHtml": "<p>Desc 2</p>",
                            "isListed": True,
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.ashbyhq.com/testco",
                "metadata": {"token": "testco"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)
            assert jobs[0].title in ("Engineer", "Designer")

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json={"jobs": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.ashbyhq.com/testco",
                "metadata": {"token": "testco"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_token_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Ashby token"):
                await discover(board, client)

    async def test_token_from_metadata(self):
        def handler(request):
            assert "mytoken" in str(request.url)
            return httpx.Response(200, json={"jobs": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {"token": "mytoken"}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_token_from_board_url(self):
        def handler(request):
            assert "testco" in str(request.url)
            return httpx.Response(200, json={"jobs": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.ashbyhq.com/testco", "metadata": {}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_skips_jobs_without_url(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"title": "No URL"},
                        {"jobUrl": "https://example.com/job", "title": "Has URL"},
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.ashbyhq.com/testco",
                "metadata": {"token": "testco"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Has URL"

    async def test_skips_unlisted_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "jobUrl": "https://example.com/1",
                            "title": "Listed",
                            "isListed": True,
                        },
                        {
                            "jobUrl": "https://example.com/2",
                            "title": "Unlisted",
                            "isListed": False,
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.ashbyhq.com/testco",
                "metadata": {"token": "testco"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Listed"

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.ashbyhq.com/testco",
                "metadata": {"token": "testco"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)

    async def test_includes_compensation_param(self):
        def handler(request):
            assert "includeCompensation=true" in str(request.url)
            return httpx.Response(200, json={"jobs": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.ashbyhq.com/testco",
                "metadata": {"token": "testco"},
            }
            await discover(board, client)


class TestCanHandle:
    async def test_ashby_url(self):
        result = await can_handle("https://jobs.ashbyhq.com/stripe")
        assert result == {"token": "stripe"}

    async def test_non_ashby_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_in_page_html(self):
        def handler(request):
            return httpx.Response(
                200,
                text='<html><script src="https://api.ashbyhq.com/posting-api/job-board/myco"></script></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result.get("token") == "myco"

    async def test_detects_jobs_subdomain_in_page(self):
        def handler(request):
            return httpx.Response(
                200,
                text='<html><a href="https://jobs.ashbyhq.com/myco/abc-123">Apply</a></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result.get("token") == "myco"

    async def test_probe_fallback(self):
        def handler(request):
            url = str(request.url)
            if "api.ashbyhq.com" in url:
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(200, text="<html>plain careers page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is not None
            assert result.get("token") == "example"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "api.ashbyhq.com" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no ashby refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None

    async def test_ashby_url_with_client_returns_count(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"jobUrl": "https://example.com/1", "title": "A"},
                        {"jobUrl": "https://example.com/2", "title": "B"},
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.ashbyhq.com/testco", client)
            assert result == {"token": "testco", "jobs": 2}
