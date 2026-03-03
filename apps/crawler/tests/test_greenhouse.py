from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.greenhouse import (
    _api_url,
    _parse_job,
    _token_from_url,
    can_handle,
    discover,
)


class TestParseJob:
    def test_basic(self):
        raw = {
            "absolute_url": "https://boards.greenhouse.io/test/jobs/1",
            "title": "Engineer",
            "content": "<p>Great job</p>",
            "first_published": "2024-01-01",
        }
        result = _parse_job(raw)
        assert result is not None
        assert result.url == "https://boards.greenhouse.io/test/jobs/1"
        assert result.title == "Engineer"
        assert result.description == "<p>Great job</p>"
        assert result.date_posted == "2024-01-01"

    def test_missing_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"title": "No URL"}) is None

    def test_locations_from_location_field(self):
        raw = {
            "absolute_url": "https://example.com/job",
            "location": {"name": "New York"},
        }
        result = _parse_job(raw)
        assert result.locations == ["New York"]

    def test_locations_from_offices(self):
        raw = {
            "absolute_url": "https://example.com/job",
            "offices": [{"name": "London"}, {"name": "Berlin"}],
        }
        result = _parse_job(raw)
        assert result.locations == ["London", "Berlin"]

    def test_deduplicates_location_and_office(self):
        raw = {
            "absolute_url": "https://example.com/job",
            "location": {"name": "HQ"},
            "offices": [{"name": "HQ"}, {"name": "London"}],
        }
        result = _parse_job(raw)
        assert result.locations == ["HQ", "London"]

    def test_metadata_departments(self):
        raw = {
            "absolute_url": "https://example.com/job",
            "departments": [{"name": "Engineering"}, {"name": "Platform"}],
        }
        result = _parse_job(raw)
        assert result.metadata == {"departments": ["Engineering", "Platform"]}

    def test_metadata_education(self):
        raw = {
            "absolute_url": "https://example.com/job",
            "education": "Bachelor's",
        }
        result = _parse_job(raw)
        assert result.metadata == {"education": "Bachelor's"}

    def test_metadata_requisition_id(self):
        raw = {
            "absolute_url": "https://example.com/job",
            "requisition_id": "REQ-001",
        }
        result = _parse_job(raw)
        assert result.metadata == {"requisition_id": "REQ-001"}

    def test_no_metadata(self):
        raw = {"absolute_url": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.metadata is None

    def test_no_locations(self):
        raw = {"absolute_url": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.locations is None

    def test_empty_location_name(self):
        raw = {
            "absolute_url": "https://example.com/job",
            "location": {"name": ""},
        }
        result = _parse_job(raw)
        assert result.locations is None

    def test_offices_skip_empty_names(self):
        raw = {
            "absolute_url": "https://example.com/job",
            "offices": [{"name": ""}, {"name": "NYC"}],
        }
        result = _parse_job(raw)
        assert result.locations == ["NYC"]


class TestTokenFromUrl:
    def test_standard_url(self):
        assert _token_from_url("https://boards.greenhouse.io/stripe") == "stripe"

    def test_with_path(self):
        assert _token_from_url("https://boards.greenhouse.io/stripe/jobs/123") == "stripe"

    def test_embed_ignored(self):
        assert _token_from_url("https://boards.greenhouse.io/embed") is None

    def test_no_match(self):
        assert _token_from_url("https://example.com/careers") is None


class TestApiUrl:
    def test_basic(self):
        assert _api_url("stripe") == "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"

    def test_with_slug(self):
        assert (
            _api_url("my-company") == "https://boards-api.greenhouse.io/v1/boards/my-company/jobs"
        )


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "absolute_url": "https://boards.greenhouse.io/test/jobs/1",
                            "title": "Engineer",
                            "content": "Desc",
                        },
                        {
                            "absolute_url": "https://boards.greenhouse.io/test/jobs/2",
                            "title": "Designer",
                            "content": "Desc 2",
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://boards.greenhouse.io/testco",
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
                "board_url": "https://boards.greenhouse.io/testco",
                "metadata": {"token": "testco"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_token_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Greenhouse token"):
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
            board = {"board_url": "https://boards.greenhouse.io/testco", "metadata": {}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_skips_jobs_without_url(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"title": "No URL"},
                        {"absolute_url": "https://example.com/job", "title": "Has URL"},
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://boards.greenhouse.io/testco",
                "metadata": {"token": "testco"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Has URL"

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://boards.greenhouse.io/testco",
                "metadata": {"token": "testco"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)


class TestCanHandle:
    async def test_greenhouse_url(self):
        result = await can_handle("https://boards.greenhouse.io/stripe")
        assert result == {"token": "stripe"}

    async def test_non_greenhouse_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_in_page_html(self):
        def handler(request):
            return httpx.Response(
                200,
                text='<html><script src="https://boards-api.greenhouse.io/v1/boards/myco/embed"></script></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result.get("token") == "myco"

    async def test_probe_fallback(self):
        def handler(request):
            url = str(request.url)
            if "boards-api.greenhouse.io" in url:
                return httpx.Response(200, json={"jobs": []})
            return httpx.Response(200, text="<html>plain careers page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is not None
            assert result.get("token") == "example"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "boards-api.greenhouse.io" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no greenhouse refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None
