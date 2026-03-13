from __future__ import annotations

import httpx
import pytest

from src.core.monitors.smartrecruiters import (
    _token_from_url,
    can_handle,
    discover,
)
from src.core.scrapers import JobContent
from src.core.scrapers.smartrecruiters import (
    _build_description,
    _build_location,
    _extract_posting_id,
    _parse_detail,
    _parse_salary,
    scrape,
)

# ── Monitor tests ────────────────────────────────────────────────────────


class TestTokenFromUrl:
    def test_api_url(self):
        assert (
            _token_from_url("https://api.smartrecruiters.com/v1/companies/acme/postings") == "acme"
        )

    def test_jobs_subdomain(self):
        assert _token_from_url("https://jobs.smartrecruiters.com/acme") == "acme"

    def test_careers_subdomain(self):
        assert _token_from_url("https://careers.smartrecruiters.com/acme-corp") == "acme-corp"

    def test_with_path(self):
        assert _token_from_url("https://careers.smartrecruiters.com/acme/job/123") == "acme"

    def test_ignored_token(self):
        assert _token_from_url("https://api.smartrecruiters.com/v1/companies/api/x") is None
        assert _token_from_url("https://jobs.smartrecruiters.com/postings") is None

    def test_non_matching_url(self):
        assert _token_from_url("https://example.com/careers") is None


class TestDiscover:
    async def test_returns_urls(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"id": "post1"},
                        {"id": "post2"},
                    ],
                    "totalFound": 2,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.smartrecruiters.com/acme",
                "metadata": {"token": "acme"},
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert len(urls) == 2
            assert "https://jobs.smartrecruiters.com/acme/post1" in urls
            assert "https://jobs.smartrecruiters.com/acme/post2" in urls

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"content": [], "totalFound": 0},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.smartrecruiters.com/acme",
                "metadata": {"token": "acme"},
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert len(urls) == 0

    async def test_no_token_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive SmartRecruiters"):
                await discover(board, client)

    async def test_token_from_metadata(self):
        def handler(request):
            assert "mytoken" in str(request.url)
            return httpx.Response(
                200,
                json={"content": [], "totalFound": 0},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"token": "mytoken"},
            }
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_token_from_board_url(self):
        def handler(request):
            assert "testco" in str(request.url)
            return httpx.Response(
                200,
                json={"content": [], "totalFound": 0},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.smartrecruiters.com/testco",
                "metadata": {},
            }
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_pagination(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            url = str(request.url)
            if "offset=0" in url or "offset" not in url:
                call_count += 1
                return httpx.Response(
                    200,
                    json={
                        "content": [{"id": f"p{i}"} for i in range(100)],
                        "totalFound": 150,
                    },
                )
            else:
                call_count += 1
                return httpx.Response(
                    200,
                    json={
                        "content": [{"id": f"p{100 + i}"} for i in range(50)],
                        "totalFound": 150,
                    },
                )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.smartrecruiters.com/acme",
                "metadata": {"token": "acme"},
            }
            urls = await discover(board, client)
            assert len(urls) == 150
            assert call_count == 2  # Two pages

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.smartrecruiters.com/acme",
                "metadata": {"token": "acme"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)


class TestCanHandle:
    async def test_smartrecruiters_url_match(self):
        result = await can_handle("https://careers.smartrecruiters.com/acme")
        assert result is not None
        assert result["token"] == "acme"

    async def test_non_matching_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_url_match_with_client(self):
        def handler(request):
            return httpx.Response(200, json={"totalFound": 42, "content": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://careers.smartrecruiters.com/acme", client)
            assert result is not None
            assert result["token"] == "acme"
            assert result["jobs"] == 42

    async def test_detects_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "api.smartrecruiters.com" in url:
                return httpx.Response(200, json={"totalFound": 5, "content": []})
            return httpx.Response(
                200,
                text='<html><script src="https://careers.smartrecruiters.com/myco/widget"></script></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is not None
            assert result["token"] == "myco"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "api.smartrecruiters.com" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no smartrecruiters</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None

    async def test_redirect_to_generic_smartrecruiters_page_rejected(self):
        def handler(request):
            host = (request.url.host or "").lower()
            if host == "careers.smartrecruiters.com":
                return httpx.Response(
                    302,
                    headers={"Location": "https://www.smartrecruiters.com/careers/"},
                )
            if host == "www.smartrecruiters.com":
                return httpx.Response(200, text="<html>SmartRecruiters careers landing</html>")
            if host == "api.smartrecruiters.com":
                return httpx.Response(404)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://careers.smartrecruiters.com/acme", client)
            assert result is None

    async def test_no_blind_slug_probe_without_smartrecruiters_signal(self):
        def handler(request):
            host = (request.url.host or "").lower()
            path = request.url.path
            if host == "api.smartrecruiters.com" and "/companies/example/postings" in path:
                # A valid token exists, but input page has no SR signal.
                return httpx.Response(200, json={"totalFound": 7, "content": []})
            return httpx.Response(200, text="<html>plain careers page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None


# ── Scraper tests ────────────────────────────────────────────────────────


class TestExtractPostingId:
    def test_bare_id(self):
        url = "https://jobs.smartrecruiters.com/Nexthink/743999106810286"
        assert _extract_posting_id(url) == "743999106810286"

    def test_id_with_slug(self):
        url = "https://jobs.smartrecruiters.com/Nexthink/743999106810286-senior-software-engineer"
        assert _extract_posting_id(url) == "743999106810286-senior-software-engineer"

    def test_careers_subdomain(self):
        url = "https://careers.smartrecruiters.com/AcmeCorp/123456789"
        assert _extract_posting_id(url) == "123456789"

    def test_non_matching(self):
        assert _extract_posting_id("https://example.com/job/123") is None


class TestBuildDescription:
    def test_all_sections(self):
        job_ad = {
            "sections": {
                "companyDescription": {"title": "About Us", "text": "<p>Company</p>"},
                "jobDescription": {"title": "Role", "text": "<p>Job desc</p>"},
                "qualifications": {"title": "Qualifications", "text": "<p>Quals</p>"},
                "additionalInformation": {"title": "Additional", "text": "<p>Info</p>"},
            }
        }
        result = _build_description(job_ad)
        assert "<h3>About Us</h3>" in result
        assert "<p>Company</p>" in result
        assert "<h3>Role</h3>" in result
        assert "<p>Job desc</p>" in result
        assert "<h3>Qualifications</h3>" in result
        assert "<p>Quals</p>" in result
        assert "<h3>Additional</h3>" in result
        assert "<p>Info</p>" in result

    def test_section_without_title(self):
        job_ad = {
            "sections": {
                "jobDescription": {"text": "<p>Just text</p>"},
            }
        }
        result = _build_description(job_ad)
        assert result == "<p>Just text</p>"
        assert "<h3>" not in result

    def test_empty_sections(self):
        assert _build_description({"sections": {}}) is None

    def test_empty_job_ad(self):
        assert _build_description({}) is None

    def test_none_job_ad(self):
        assert _build_description(None) is None

    def test_section_with_empty_text(self):
        job_ad = {
            "sections": {
                "jobDescription": {"title": "Role", "text": ""},
            }
        }
        assert _build_description(job_ad) is None


class TestBuildLocation:
    def test_full_location_preferred(self):
        loc = {"fullLocation": "New York, NY, US", "city": "New York", "country": "US"}
        assert _build_location(loc) == "New York, NY, US"

    def test_city_region_country_fallback(self):
        loc = {"city": "Berlin", "region": "Berlin", "country": "Germany"}
        assert _build_location(loc) == "Berlin, Berlin, Germany"

    def test_city_country_only(self):
        loc = {"city": "London", "country": "UK"}
        assert _build_location(loc) == "London, UK"

    def test_city_only(self):
        loc = {"city": "Paris"}
        assert _build_location(loc) == "Paris"

    def test_empty_dict(self):
        assert _build_location({}) is None

    def test_none(self):
        assert _build_location(None) is None


class TestParseSalary:
    def test_basic_salary(self):
        posting = {
            "compensation": {
                "salary": {
                    "min": 50000,
                    "max": 80000,
                    "currency": "USD",
                    "period": "yearly",
                }
            }
        }
        result = _parse_salary(posting)
        assert result == {"currency": "USD", "min": 50000, "max": 80000, "unit": "year"}

    def test_hourly_period(self):
        posting = {
            "compensation": {
                "salary": {"min": 20, "max": 40, "currency": "USD", "period": "hourly"}
            }
        }
        result = _parse_salary(posting)
        assert result["unit"] == "hour"

    def test_monthly_period(self):
        posting = {
            "compensation": {
                "salary": {"min": 3000, "max": 5000, "currency": "EUR", "period": "monthly"}
            }
        }
        result = _parse_salary(posting)
        assert result["unit"] == "month"

    def test_both_none_returns_none(self):
        posting = {
            "compensation": {"salary": {"min": None, "max": None, "currency": "USD", "period": ""}}
        }
        assert _parse_salary(posting) is None

    def test_no_compensation(self):
        assert _parse_salary({}) is None

    def test_no_salary(self):
        assert _parse_salary({"compensation": {}}) is None

    def test_none_compensation(self):
        assert _parse_salary({"compensation": None}) is None


class TestParseDetail:
    def test_full_posting(self):
        posting = {
            "name": "Software Engineer",
            "jobAd": {
                "sections": {
                    "jobDescription": {"title": "Description", "text": "<p>Build</p>"},
                }
            },
            "location": {"fullLocation": "NYC, NY, US", "remote": False},
            "typeOfEmployment": {"label": "Full-time"},
            "department": {"label": "Engineering"},
            "function": {"label": "Software Development"},
            "experienceLevel": {"label": "Mid-Senior"},
            "releasedDate": "2024-01-15",
            "compensation": {
                "salary": {"min": 100000, "max": 150000, "currency": "USD", "period": "yearly"}
            },
        }
        result = _parse_detail(posting)
        assert isinstance(result, JobContent)
        assert result.title == "Software Engineer"
        assert "<p>Build</p>" in result.description
        assert result.locations == ["NYC, NY, US"]
        assert result.job_location_type is None
        assert result.employment_type == "Full-time"
        assert result.date_posted == "2024-01-15"
        assert result.base_salary is not None
        assert result.metadata["department"] == "Engineering"
        assert result.metadata["function"] == "Software Development"
        assert result.metadata["experienceLevel"] == "Mid-Senior"

    def test_remote_location(self):
        posting = {
            "name": "Remote Job",
            "location": {"remote": True},
        }
        result = _parse_detail(posting)
        assert result.job_location_type == "remote"

    def test_hybrid_location(self):
        posting = {
            "name": "Hybrid Job",
            "location": {"hybrid": True},
        }
        result = _parse_detail(posting)
        assert result.job_location_type == "hybrid"

    def test_employment_type_label(self):
        posting = {
            "name": "Part-time",
            "typeOfEmployment": {"label": "Part-time"},
        }
        result = _parse_detail(posting)
        assert result.employment_type == "Part-time"

    def test_no_employment_type(self):
        posting = {"name": "Job"}
        result = _parse_detail(posting)
        assert result.employment_type is None

    def test_metadata_dicts(self):
        posting = {
            "name": "Job",
            "department": {"label": "Sales"},
            "function": {"label": "Account Management"},
            "experienceLevel": {"label": "Junior"},
        }
        result = _parse_detail(posting)
        assert result.metadata == {
            "department": "Sales",
            "function": "Account Management",
            "experienceLevel": "Junior",
        }

    def test_no_metadata(self):
        posting = {"name": "Job"}
        result = _parse_detail(posting)
        assert result.metadata is None

    def test_metadata_with_empty_labels(self):
        posting = {
            "name": "Job",
            "department": {"label": ""},
            "function": {"label": ""},
        }
        result = _parse_detail(posting)
        assert result.metadata is None


class TestScrape:
    async def test_full_scrape(self):
        def handler(request):
            url = str(request.url)
            if "/postings/743999106810286" in url:
                return httpx.Response(
                    200,
                    json={
                        "name": "Senior Engineer",
                        "jobAd": {
                            "sections": {
                                "jobDescription": {"title": "Role", "text": "<p>Build things</p>"},
                            }
                        },
                        "location": {"fullLocation": "Lausanne, Switzerland"},
                        "typeOfEmployment": {"label": "Full-time"},
                        "releasedDate": "2024-06-01",
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://jobs.smartrecruiters.com/Nexthink/743999106810286",
                {"token": "Nexthink"},
                client,
            )
            assert result.title == "Senior Engineer"
            assert "<p>Build things</p>" in result.description
            assert result.locations == ["Lausanne, Switzerland"]
            assert result.employment_type == "Full-time"

    async def test_unparseable_url(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape("https://example.com/job/123", {"token": "acme"}, client)
            assert result.title is None

    async def test_no_token(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape("https://jobs.smartrecruiters.com/acme/123", {}, client)
            assert result.title is None

    async def test_detail_404(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://jobs.smartrecruiters.com/acme/123",
                {"token": "acme"},
                client,
            )
            assert result.title is None
