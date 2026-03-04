from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.recruitee import (
    _api_base_from_url,
    _parse_job,
    _parse_job_location_type,
    _parse_locations,
    _parse_salary,
    _slug_from_url,
    can_handle,
    discover,
)


class TestSlugFromUrl:
    def test_standard_url(self):
        assert _slug_from_url("https://acme.recruitee.com") == "acme"

    def test_with_path(self):
        assert _slug_from_url("https://acme.recruitee.com/o/software-engineer") == "acme"

    def test_ignored_slug_www(self):
        assert _slug_from_url("https://www.recruitee.com") is None

    def test_ignored_slug_api(self):
        assert _slug_from_url("https://api.recruitee.com") is None

    def test_ignored_slug_app(self):
        assert _slug_from_url("https://app.recruitee.com") is None

    def test_unrelated_url(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_hyphenated_slug(self):
        assert _slug_from_url("https://my-company.recruitee.com") == "my-company"


class TestApiBaseFromUrl:
    def test_standard_recruitee(self):
        assert _api_base_from_url("https://acme.recruitee.com") == "https://acme.recruitee.com"

    def test_custom_domain(self):
        assert _api_base_from_url("https://jobs.acme.com/careers") == "https://jobs.acme.com"

    def test_http_scheme(self):
        assert _api_base_from_url("http://acme.recruitee.com") == "http://acme.recruitee.com"

    def test_no_host(self):
        assert _api_base_from_url("not-a-url") is None


class TestParseLocations:
    def test_structured_city_country(self):
        offer = {"locations": [{"city": "Berlin", "country": "Germany"}]}
        assert _parse_locations(offer) == ["Berlin, Germany"]

    def test_structured_city_only(self):
        offer = {"locations": [{"city": "Paris"}]}
        assert _parse_locations(offer) == ["Paris"]

    def test_structured_country_only(self):
        offer = {"locations": [{"country": "Netherlands"}]}
        assert _parse_locations(offer) == ["Netherlands"]

    def test_structured_dedup(self):
        offer = {
            "locations": [
                {"city": "London", "country": "UK"},
                {"city": "London", "country": "UK"},
            ]
        }
        assert _parse_locations(offer) == ["London, UK"]

    def test_flat_string_fallback(self):
        offer = {"location": "Remote"}
        assert _parse_locations(offer) == ["Remote"]

    def test_structured_takes_priority_over_flat(self):
        offer = {
            "locations": [{"city": "Berlin", "country": "Germany"}],
            "location": "Global",
        }
        result = _parse_locations(offer)
        assert result == ["Berlin, Germany"]

    def test_flat_string_only_when_structured_empty(self):
        offer = {"locations": [], "location": "Anywhere"}
        assert _parse_locations(offer) == ["Anywhere"]

    def test_empty_locations(self):
        assert _parse_locations({"locations": []}) is None

    def test_no_locations(self):
        assert _parse_locations({}) is None

    def test_multiple_structured(self):
        offer = {
            "locations": [
                {"city": "NYC", "country": "US"},
                {"city": "London", "country": "UK"},
            ]
        }
        assert _parse_locations(offer) == ["NYC, US", "London, UK"]


class TestParseJobLocationType:
    def test_remote(self):
        assert _parse_job_location_type({"remote": True}) == "remote"

    def test_hybrid(self):
        assert _parse_job_location_type({"hybrid": True}) == "hybrid"

    def test_on_site(self):
        assert _parse_job_location_type({"on_site": True}) == "onsite"

    def test_none(self):
        assert _parse_job_location_type({}) is None

    def test_remote_takes_priority(self):
        assert _parse_job_location_type({"remote": True, "hybrid": True}) == "remote"

    def test_false_values(self):
        result = _parse_job_location_type(
            {"remote": False, "hybrid": False, "on_site": False}
        )
        assert result is None


class TestParseSalary:
    def test_full_salary(self):
        offer = {
            "salary": {
                "min": 50000,
                "max": 70000,
                "currency": "EUR",
                "period": "yearly",
            }
        }
        result = _parse_salary(offer)
        assert result == {"currency": "EUR", "min": 50000, "max": 70000, "unit": "year"}

    def test_hourly(self):
        offer = {
            "salary": {"min": 20, "max": 30, "currency": "USD", "period": "hourly"}
        }
        result = _parse_salary(offer)
        assert result["unit"] == "hour"

    def test_monthly(self):
        offer = {
            "salary": {"min": 4000, "max": 6000, "currency": "GBP", "period": "monthly"}
        }
        result = _parse_salary(offer)
        assert result["unit"] == "month"

    def test_weekly(self):
        offer = {
            "salary": {"min": 1000, "max": 1500, "currency": "USD", "period": "weekly"}
        }
        result = _parse_salary(offer)
        assert result["unit"] == "week"

    def test_all_none_returns_none(self):
        offer = {"salary": {"min": None, "max": None}}
        assert _parse_salary(offer) is None

    def test_no_salary_key(self):
        assert _parse_salary({}) is None

    def test_salary_not_dict(self):
        assert _parse_salary({"salary": "50000"}) is None

    def test_min_only(self):
        offer = {
            "salary": {"min": 40000, "max": None, "currency": "CHF", "period": ""}
        }
        result = _parse_salary(offer)
        assert result == {"currency": "CHF", "min": 40000, "max": None, "unit": "year"}


class TestParseJob:
    def test_full_offer(self):
        offer = {
            "careers_url": "https://acme.recruitee.com/o/engineer",
            "title": "Software Engineer",
            "description": "<p>About the role</p>",
            "requirements": "<p>What we need</p>",
            "locations": [{"city": "Berlin", "country": "Germany"}],
            "employment_type_code": "fulltime",
            "remote": True,
            "published_at": "2024-06-01",
            "salary": {"min": 50000, "max": 70000, "currency": "EUR", "period": "yearly"},
            "department": "Engineering",
            "tags": ["python", "async"],
            "category_code": "tech",
            "id": 12345,
        }
        result = _parse_job(offer)
        assert result is not None
        assert result.url == "https://acme.recruitee.com/o/engineer"
        assert result.title == "Software Engineer"
        assert "<p>About the role</p>" in result.description
        assert "<p>What we need</p>" in result.description
        assert result.locations == ["Berlin, Germany"]
        assert result.employment_type == "Full-time"
        assert result.job_location_type == "remote"
        assert result.date_posted == "2024-06-01"
        assert result.base_salary == {"currency": "EUR", "min": 50000, "max": 70000, "unit": "year"}
        assert result.metadata == {
            "department": "Engineering",
            "tags": ["python", "async"],
            "category": "tech",
            "id": 12345,
        }

    def test_missing_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"title": "No URL"}) is None

    def test_combined_description_and_requirements(self):
        offer = {
            "careers_url": "https://example.com/job",
            "description": "<p>Desc</p>",
            "requirements": "<p>Reqs</p>",
        }
        result = _parse_job(offer)
        assert result.description is not None
        assert "<p>Desc</p>" in result.description
        assert "<p>Reqs</p>" in result.description

    def test_description_only(self):
        offer = {
            "careers_url": "https://example.com/job",
            "description": "<p>Desc only</p>",
        }
        result = _parse_job(offer)
        assert result.description == "<p>Desc only</p>"

    def test_no_description(self):
        offer = {"careers_url": "https://example.com/job"}
        result = _parse_job(offer)
        assert result.description is None

    def test_employment_type_code_mapping(self):
        for code, expected in [
            ("fulltime", "Full-time"),
            ("fulltime_permanent", "Full-time"),
            ("parttime", "Part-time"),
            ("freelance", "Contract"),
            ("internship", "Intern"),
            ("traineeship", "Intern"),
            ("volunteer", "Volunteer"),
        ]:
            offer = {"careers_url": "https://example.com/job", "employment_type_code": code}
            result = _parse_job(offer)
            assert result.employment_type == expected, f"Failed for {code}"

    def test_unknown_employment_type_code_passthrough(self):
        offer = {
            "careers_url": "https://example.com/job",
            "employment_type_code": "custom_type",
        }
        result = _parse_job(offer)
        assert result.employment_type == "custom_type"

    def test_empty_employment_type_code(self):
        offer = {
            "careers_url": "https://example.com/job",
            "employment_type_code": "",
        }
        result = _parse_job(offer)
        assert result.employment_type is None

    def test_metadata_tags(self):
        offer = {
            "careers_url": "https://example.com/job",
            "tags": ["remote", "senior"],
        }
        result = _parse_job(offer)
        assert result.metadata == {"tags": ["remote", "senior"]}

    def test_no_metadata(self):
        offer = {"careers_url": "https://example.com/job"}
        result = _parse_job(offer)
        assert result.metadata is None


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "offers": [
                        {
                            "careers_url": "https://acme.recruitee.com/o/1",
                            "title": "Engineer",
                            "description": "Desc",
                            "status": "published",
                        },
                        {
                            "careers_url": "https://acme.recruitee.com/o/2",
                            "title": "Designer",
                            "description": "Desc 2",
                            "status": "published",
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.recruitee.com",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json={"offers": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.recruitee.com",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_slug_or_api_base_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "not-a-url", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Recruitee API base"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "myslug" in str(request.url)
            return httpx.Response(200, json={"offers": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {"slug": "myslug"}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_slug_from_board_url(self):
        def handler(request):
            assert "acme.recruitee.com" in str(request.url)
            return httpx.Response(200, json={"offers": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.recruitee.com", "metadata": {}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_filters_non_published_status(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "offers": [
                        {
                            "careers_url": "https://example.com/1",
                            "title": "Published",
                            "status": "published",
                        },
                        {
                            "careers_url": "https://example.com/2",
                            "title": "Draft",
                            "status": "draft",
                        },
                        {
                            "careers_url": "https://example.com/3",
                            "title": "Closed",
                            "status": "closed",
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.recruitee.com",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Published"

    async def test_skips_jobs_without_url(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "offers": [
                        {"title": "No URL", "status": "published"},
                        {
                            "careers_url": "https://example.com/job",
                            "title": "Has URL",
                            "status": "published",
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.recruitee.com",
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
                "board_url": "https://acme.recruitee.com",
                "metadata": {"slug": "acme"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)

    async def test_custom_domain_via_api_base_metadata(self):
        def handler(request):
            assert "jobs.acme.com" in str(request.url)
            return httpx.Response(200, json={"offers": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.acme.com/careers",
                "metadata": {"api_base": "https://jobs.acme.com"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0


class TestCanHandle:
    async def test_recruitee_url(self):
        result = await can_handle("https://acme.recruitee.com")
        assert result is not None
        assert result["slug"] == "acme"
        assert result["api_base"] == "https://acme.recruitee.com"

    async def test_non_recruitee_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_slug_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "/api/offers" in url:
                return httpx.Response(200, json={"offers": [{"id": 1}]})
            return httpx.Response(
                200,
                text='<html><script src="https://myco.recruitee.com/widget.js"></script></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.example.com/careers", client)
            assert result is not None
            assert result.get("slug") == "myco"

    async def test_detects_recruiteecdn_marker(self):
        def handler(request):
            url = str(request.url)
            if "/api/offers" in url:
                return httpx.Response(200, json={"offers": [{"id": 1}]})
            return httpx.Response(
                200,
                text='<html><link href="https://d26l0rr0k0zzsr.recruiteecdn.com/style.css"></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.example.com/careers", client)
            assert result is not None
            assert "api_base" in result

    async def test_detects_window_recruitee_marker(self):
        def handler(request):
            url = str(request.url)
            if "/api/offers" in url:
                return httpx.Response(200, json={"offers": [{"id": 1}]})
            return httpx.Response(
                200,
                text="<html><script>window.recruitee = {company_id: 123}</script></html>",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.example.com/careers", client)
            assert result is not None
            assert "api_base" in result

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "/api/offers" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no recruitee refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None

    async def test_url_match_with_api_probe(self):
        def handler(request):
            return httpx.Response(200, json={"offers": [{"id": 1}, {"id": 2}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.recruitee.com", client)
            assert result is not None
            assert result["slug"] == "acme"
            assert result["jobs"] == 2
            assert result["api_base"] == "https://acme.recruitee.com"
