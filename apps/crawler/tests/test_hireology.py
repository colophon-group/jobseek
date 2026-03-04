from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.hireology import (
    _parse_job,
    _parse_locations,
    _slug_from_url,
    can_handle,
    discover,
)


class TestSlugFromUrl:
    def test_new_domain(self):
        assert _slug_from_url("https://acme.hireology.careers") == "acme"

    def test_new_domain_with_path(self):
        assert _slug_from_url("https://acme.hireology.careers/jobs/123") == "acme"

    def test_careers_domain(self):
        assert _slug_from_url("https://careers.hireology.com/acme") == "acme"

    def test_careers_domain_with_subpath(self):
        assert _slug_from_url("https://careers.hireology.com/acme/123/description") == "acme"

    def test_ignored_subdomain_api(self):
        assert _slug_from_url("https://api.hireology.careers") is None

    def test_ignored_subdomain_www(self):
        assert _slug_from_url("https://www.hireology.careers") is None

    def test_unrelated_url(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_no_path_on_careers_domain(self):
        assert _slug_from_url("https://careers.hireology.com/") is None

    def test_hyphenated_slug(self):
        assert _slug_from_url("https://my-company.hireology.careers") == "my-company"


class TestParseLocations:
    def test_dict_array_city_state(self):
        job = {"locations": [{"city": "Chicago", "state": "IL"}]}
        assert _parse_locations(job) == ["Chicago, IL"]

    def test_dict_array_city_only(self):
        job = {"locations": [{"city": "New York"}]}
        assert _parse_locations(job) == ["New York"]

    def test_dict_array_state_only(self):
        job = {"locations": [{"state": "TX"}]}
        assert _parse_locations(job) == ["TX"]

    def test_flat_strings(self):
        job = {"locations": ["Remote", "Austin, TX"]}
        assert _parse_locations(job) == ["Remote", "Austin, TX"]

    def test_mixed_types(self):
        job = {"locations": [{"city": "NYC", "state": "NY"}, "Remote"]}
        assert _parse_locations(job) == ["NYC, NY", "Remote"]

    def test_empty_list(self):
        assert _parse_locations({"locations": []}) is None

    def test_no_locations_key(self):
        assert _parse_locations({}) is None

    def test_empty_dict(self):
        job = {"locations": [{"city": "", "state": ""}]}
        assert _parse_locations(job) is None

    def test_empty_string_in_list(self):
        job = {"locations": [""]}
        assert _parse_locations(job) is None


class TestParseJob:
    def test_full_job(self):
        raw = {
            "career_site_url": "https://acme.hireology.careers/jobs/123",
            "name": "Software Engineer",
            "job_description": "<p>Build things</p>",
            "locations": [{"city": "Chicago", "state": "IL"}],
            "employment_status": "Full-time",
            "created_at": "2024-06-01",
            "organization": {"name": "Acme Corp"},
            "job_family": {"name": "Engineering"},
            "id": "abc-123",
        }
        result = _parse_job(raw)
        assert result is not None
        assert result.url == "https://acme.hireology.careers/jobs/123"
        assert result.title == "Software Engineer"
        assert result.description == "<p>Build things</p>"
        assert result.locations == ["Chicago, IL"]
        assert result.employment_type == "Full-time"
        assert result.date_posted == "2024-06-01"
        assert result.metadata == {
            "organization": "Acme Corp",
            "job_family": "Engineering",
            "id": "abc-123",
        }

    def test_missing_career_site_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"name": "No URL"}) is None

    def test_remote_flag(self):
        raw = {
            "career_site_url": "https://example.com/job",
            "remote": True,
        }
        result = _parse_job(raw)
        assert result.job_location_type == "remote"

    def test_not_remote(self):
        raw = {
            "career_site_url": "https://example.com/job",
            "remote": False,
        }
        result = _parse_job(raw)
        assert result.job_location_type is None

    def test_metadata_organization_nested_dict(self):
        raw = {
            "career_site_url": "https://example.com/job",
            "organization": {"name": "Org Inc"},
        }
        result = _parse_job(raw)
        assert result.metadata == {"organization": "Org Inc"}

    def test_metadata_job_family_nested_dict(self):
        raw = {
            "career_site_url": "https://example.com/job",
            "job_family": {"name": "Sales"},
        }
        result = _parse_job(raw)
        assert result.metadata == {"job_family": "Sales"}

    def test_metadata_empty_org_name(self):
        raw = {
            "career_site_url": "https://example.com/job",
            "organization": {"name": ""},
        }
        result = _parse_job(raw)
        assert result.metadata is None

    def test_metadata_org_not_dict(self):
        raw = {
            "career_site_url": "https://example.com/job",
            "organization": "string-org",
        }
        result = _parse_job(raw)
        assert result.metadata is None

    def test_no_metadata(self):
        raw = {"career_site_url": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.metadata is None

    def test_no_locations(self):
        raw = {"career_site_url": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.locations is None


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "career_site_url": "https://acme.hireology.careers/jobs/1",
                            "name": "Engineer",
                            "job_description": "Desc",
                            "status": "Open",
                        },
                        {
                            "career_site_url": "https://acme.hireology.careers/jobs/2",
                            "name": "Designer",
                            "job_description": "Desc 2",
                            "status": "Open",
                        },
                    ],
                    "count": 2,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.hireology.careers",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json={"data": [], "count": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.hireology.careers",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Hireology slug"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "myslug" in str(request.url)
            return httpx.Response(200, json={"data": [], "count": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {"slug": "myslug"}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_slug_from_board_url(self):
        def handler(request):
            assert "acme" in str(request.url)
            return httpx.Response(200, json={"data": [], "count": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.hireology.careers", "metadata": {}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_filters_non_open_status(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "career_site_url": "https://example.com/1",
                            "name": "Open Job",
                            "status": "Open",
                        },
                        {
                            "career_site_url": "https://example.com/2",
                            "name": "Closed Job",
                            "status": "Closed",
                        },
                        {
                            "career_site_url": "https://example.com/3",
                            "name": "Draft Job",
                            "status": "Draft",
                        },
                    ],
                    "count": 3,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.hireology.careers",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Open Job"

    async def test_pagination(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            page = int(request.url.params.get("page", "1"))
            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "career_site_url": f"https://example.com/{i}",
                                "name": f"Job {i}",
                                "status": "Open",
                            }
                            for i in range(500)
                        ],
                        "count": 600,
                    },
                )
            else:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "career_site_url": f"https://example.com/{i}",
                                "name": f"Job {i}",
                                "status": "Open",
                            }
                            for i in range(500, 600)
                        ],
                        "count": 600,
                    },
                )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.hireology.careers",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 600
            assert call_count == 2

    async def test_skips_jobs_without_url(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"name": "No URL", "status": "Open"},
                        {
                            "career_site_url": "https://example.com/job",
                            "name": "Has URL",
                            "status": "Open",
                        },
                    ],
                    "count": 2,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.hireology.careers",
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
                "board_url": "https://acme.hireology.careers",
                "metadata": {"slug": "acme"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)


class TestCanHandle:
    async def test_matching_url_new_domain(self):
        result = await can_handle("https://acme.hireology.careers")
        assert result == {"slug": "acme"}

    async def test_matching_url_careers_domain(self):
        result = await can_handle("https://careers.hireology.com/acme")
        assert result == {"slug": "acme"}

    async def test_non_matching_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "api.hireology.com" in url:
                return httpx.Response(200, json={"count": 5, "data": [{"id": 1}]})
            return httpx.Response(
                200,
                text='<html><script src="https://careers.hireology.com/myco/embed"></script></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result.get("slug") == "myco"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "api.hireology.com" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no hireology refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None

    async def test_url_match_with_api_probe(self):
        def handler(request):
            return httpx.Response(200, json={"count": 10, "data": [{"id": 1}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.hireology.careers", client)
            assert result is not None
            assert result["slug"] == "acme"
            assert result["jobs"] == 10
