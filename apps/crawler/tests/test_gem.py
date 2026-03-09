from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob, slug_guess_mode
from src.core.monitors.gem import (
    _parse_job,
    _parse_locations,
    _slug_from_url,
    can_handle,
    discover,
)


class TestSlugFromUrl:
    def test_standard_url(self):
        assert _slug_from_url("https://jobs.gem.com/caffeine-ai") == "caffeine-ai"

    def test_with_path(self):
        assert _slug_from_url("https://jobs.gem.com/gem/am9icG9zdDpxyz") == "gem"

    def test_ignored_slug_api(self):
        assert _slug_from_url("https://jobs.gem.com/api") is None

    def test_ignored_slug_app(self):
        assert _slug_from_url("https://jobs.gem.com/app") is None

    def test_unrelated_url(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_hyphenated_slug(self):
        assert _slug_from_url("https://jobs.gem.com/my-company") == "my-company"


class TestParseLocations:
    def test_from_offices(self):
        post = {
            "offices": [
                {"name": "Zurich", "location": {"name": "Zürich, Switzerland"}},
            ]
        }
        assert _parse_locations(post) == ["Zürich, Switzerland"]

    def test_office_name_fallback(self):
        post = {"offices": [{"name": "NYC Office"}]}
        assert _parse_locations(post) == ["NYC Office"]

    def test_location_dict_fallback(self):
        post = {"location": {"name": "San Francisco, United States"}}
        assert _parse_locations(post) == ["San Francisco, United States"]

    def test_location_string_fallback(self):
        post = {"location": "Remote"}
        assert _parse_locations(post) == ["Remote"]

    def test_offices_take_priority(self):
        post = {
            "offices": [{"name": "Berlin", "location": {"name": "Berlin, Germany"}}],
            "location": {"name": "Global"},
        }
        assert _parse_locations(post) == ["Berlin, Germany"]

    def test_dedup(self):
        post = {
            "offices": [
                {"name": "A", "location": {"name": "London, UK"}},
                {"name": "B", "location": {"name": "London, UK"}},
            ]
        }
        assert _parse_locations(post) == ["London, UK"]

    def test_multiple_offices(self):
        post = {
            "offices": [
                {"name": "A", "location": {"name": "NYC, US"}},
                {"name": "B", "location": {"name": "London, UK"}},
            ]
        }
        assert _parse_locations(post) == ["NYC, US", "London, UK"]

    def test_empty_offices(self):
        assert _parse_locations({"offices": []}) is None

    def test_no_location(self):
        assert _parse_locations({}) is None


class TestParseJob:
    def test_full_post(self):
        post = {
            "absolute_url": "https://jobs.gem.com/acme/abc123",
            "title": "Software Engineer",
            "content": "<p>About the role</p>",
            "location": {"name": "Zürich, Switzerland"},
            "location_type": "hybrid",
            "employment_type": "full_time",
            "first_published_at": "2025-11-24T14:56:56.569Z",
            "departments": [{"id": "d1", "name": "Engineering"}],
            "offices": [
                {"name": "Zurich", "location": {"name": "Zürich, Switzerland"}},
            ],
        }
        result = _parse_job(post)
        assert result is not None
        assert result.url == "https://jobs.gem.com/acme/abc123"
        assert result.title == "Software Engineer"
        assert result.description == "<p>About the role</p>"
        assert result.locations == ["Zürich, Switzerland"]
        assert result.employment_type == "Full-time"
        assert result.job_location_type == "hybrid"
        assert result.date_posted == "2025-11-24T14:56:56.569Z"
        assert result.metadata == {"department": "Engineering"}

    def test_missing_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"title": "No URL"}) is None

    def test_employment_type_mapping(self):
        for code, expected in [
            ("full_time", "Full-time"),
            ("part_time", "Part-time"),
            ("contract", "Contract"),
            ("internship", "Intern"),
        ]:
            post = {"absolute_url": "https://jobs.gem.com/x/1", "employment_type": code}
            result = _parse_job(post)
            assert result.employment_type == expected, f"Failed for {code}"

    def test_unknown_employment_type_passthrough(self):
        post = {"absolute_url": "https://jobs.gem.com/x/1", "employment_type": "custom"}
        result = _parse_job(post)
        assert result.employment_type == "custom"

    def test_empty_employment_type(self):
        post = {"absolute_url": "https://jobs.gem.com/x/1", "employment_type": ""}
        result = _parse_job(post)
        assert result.employment_type is None

    def test_location_type_mapping(self):
        for code, expected in [
            ("remote", "remote"),
            ("hybrid", "hybrid"),
            ("in_office", "onsite"),
        ]:
            post = {"absolute_url": "https://jobs.gem.com/x/1", "location_type": code}
            result = _parse_job(post)
            assert result.job_location_type == expected, f"Failed for {code}"

    def test_unknown_location_type_returns_none(self):
        post = {"absolute_url": "https://jobs.gem.com/x/1", "location_type": "unknown"}
        result = _parse_job(post)
        assert result.job_location_type is None

    def test_multiple_departments(self):
        post = {
            "absolute_url": "https://jobs.gem.com/x/1",
            "departments": [
                {"id": "1", "name": "Engineering"},
                {"id": "2", "name": "AI"},
            ],
        }
        result = _parse_job(post)
        assert result.metadata == {"department": "Engineering, AI"}

    def test_no_metadata(self):
        post = {"absolute_url": "https://jobs.gem.com/x/1"}
        result = _parse_job(post)
        assert result.metadata is None


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {
                        "absolute_url": "https://jobs.gem.com/acme/1",
                        "title": "Engineer",
                        "content": "<p>Desc</p>",
                    },
                    {
                        "absolute_url": "https://jobs.gem.com/acme/2",
                        "title": "Designer",
                        "content": "<p>Desc 2</p>",
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.gem.com/acme",
                "metadata": {"token": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.gem.com/acme",
                "metadata": {"token": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "not-a-url", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Gem slug"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "myslug" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {"token": "myslug"}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_slug_from_board_url(self):
        def handler(request):
            assert "caffeine-ai" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.gem.com/caffeine-ai", "metadata": {}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_skips_jobs_without_url(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {"title": "No URL"},
                    {"absolute_url": "https://jobs.gem.com/x/1", "title": "Has URL"},
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.gem.com/acme",
                "metadata": {"token": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Has URL"

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.gem.com/acme",
                "metadata": {"token": "acme"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)

    async def test_non_list_response_returns_empty(self):
        def handler(request):
            return httpx.Response(200, json={"error": "not found"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.gem.com/acme",
                "metadata": {"token": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0


class TestCanHandle:
    async def test_gem_url(self):
        result = await can_handle("https://jobs.gem.com/caffeine-ai")
        assert result is not None
        assert result["token"] == "caffeine-ai"

    async def test_non_gem_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_gem_url_with_api_probe(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[{"absolute_url": "https://jobs.gem.com/acme/1", "title": "Job"}],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.gem.com/acme", client)
            assert result is not None
            assert result["token"] == "acme"
            assert result["jobs"] == 1

    async def test_detects_gem_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "api.gem.com" in url:
                return httpx.Response(200, json=[{"absolute_url": "u", "title": "J"}])
            return httpx.Response(
                200,
                text='<html><a href="https://jobs.gem.com/myco/abc123">Apply</a></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is not None
            assert result["token"] == "myco"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "api.gem.com" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no gem refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None

    async def test_slug_guess_mode_enables_probe_fallback(self):
        def handler(request):
            url = str(request.url)
            if "api.gem.com/job_board/v0/example/job_posts" in url:
                return httpx.Response(200, json=[{"absolute_url": "u", "title": "J"}])
            return httpx.Response(200, text="<html>no gem refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with slug_guess_mode(True):
                result = await can_handle("https://www.example.com/careers", client)
            assert result is not None
            assert result["token"] == "example"
