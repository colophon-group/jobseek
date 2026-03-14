from __future__ import annotations

import httpx
import pytest

from src.core.monitors.rippling import (
    _slug_from_url,
    can_handle,
    discover,
)
from src.core.scrapers.rippling import (
    JobContent,
    _extract_job_params,
    _parse_detail,
    _parse_employment_type,
    _parse_job_location_type,
    _parse_salary,
    scrape,
)

# ── Monitor tests ────────────────────────────────────────────────────────


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


class TestDiscover:
    async def test_returns_urls(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {"uuid": "aaa"},
                    {"uuid": "bbb"},
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://ats.rippling.com/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert len(urls) == 2
            assert "https://ats.rippling.com/acme/jobs/aaa" in urls
            assert "https://ats.rippling.com/acme/jobs/bbb" in urls

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://ats.rippling.com/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            urls = await discover(board, client)
            assert len(urls) == 0

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
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_slug_from_board_url(self):
        def handler(request):
            assert "testco" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://ats.rippling.com/testco/jobs",
                "metadata": {},
            }
            urls = await discover(board, client)
            assert len(urls) == 0

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


# ── Scraper tests ────────────────────────────────────────────────────────


class TestExtractJobParams:
    def test_standard_url(self):
        result = _extract_job_params("https://ats.rippling.com/acme-corp/jobs/abc-123")
        assert result == ("acme-corp", "abc-123")

    def test_us1_subdomain(self):
        result = _extract_job_params("https://ats.us1.rippling.com/acme/jobs/xyz-456")
        assert result == ("acme", "xyz-456")

    def test_with_locale_prefix(self):
        result = _extract_job_params("https://ats.rippling.com/en-US/acme/jobs/def-789")
        assert result == ("acme", "def-789")

    def test_non_matching_url(self):
        assert _extract_job_params("https://example.com/jobs/123") is None


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


class TestParseDetail:
    def test_full_detail(self):
        detail = {
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
        result = _parse_detail(detail)
        assert isinstance(result, JobContent)
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

    def test_dual_description(self):
        detail = {
            "description": {"company": "Company info", "role": "Role info"},
        }
        result = _parse_detail(detail)
        assert result.description == "Company info\nRole info"

    def test_only_role_description(self):
        detail = {
            "description": {"role": "Role only"},
        }
        result = _parse_detail(detail)
        assert result.description == "Role only"

    def test_no_description(self):
        result = _parse_detail({})
        assert result.description is None

    def test_work_locations(self):
        detail = {
            "workLocations": ["NYC", "LA"],
        }
        result = _parse_detail(detail)
        assert result.locations == ["NYC", "LA"]

    def test_empty_work_locations(self):
        detail = {
            "workLocations": [],
        }
        result = _parse_detail(detail)
        assert result.locations is None

    def test_metadata_department_dict(self):
        detail = {
            "department": {"name": "Sales", "base_department": "Sales"},
        }
        result = _parse_detail(detail)
        # base_department == name, so only department is set
        assert result.metadata == {"department": "Sales"}

    def test_no_metadata(self):
        result = _parse_detail({})
        assert result.metadata is None


class TestScrape:
    async def test_full_scrape(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "url": "https://ats.rippling.com/acme/jobs/aaa",
                    "name": "Engineer",
                    "description": {"role": "<p>Build things</p>"},
                    "employmentType": {"label": "SALARIED_FT"},
                    "createdOn": "2024-06-01",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://ats.rippling.com/acme/jobs/aaa",
                {"slug": "acme"},
                client,
            )
            assert result.title == "Engineer"
            assert result.description == "<p>Build things</p>"
            assert result.employment_type == "Full-time"

    async def test_unparseable_url(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape("https://example.com/bad", {}, client)
            assert result.title is None

    async def test_detail_failed(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://ats.rippling.com/acme/jobs/aaa",
                {"slug": "acme"},
                client,
            )
            assert result.title is None

    async def test_config_slug_overrides_url_slug(self):
        """Config slug should be preferred over slug extracted from URL."""
        captured_urls: list[str] = []

        def handler(request):
            captured_urls.append(str(request.url))
            return httpx.Response(
                200,
                json={"name": "Test", "description": {"role": "test"}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await scrape(
                "https://ats.rippling.com/url-slug/jobs/aaa",
                {"slug": "config-slug"},
                client,
            )
            assert "config-slug" in captured_urls[0]
            assert "url-slug" not in captured_urls[0]
