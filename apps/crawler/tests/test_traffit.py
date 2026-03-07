from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.traffit import (
    _api_url,
    _board_url,
    _get_value,
    _parse_job,
    _parse_location,
    _parse_salary,
    _slug_from_url,
    can_handle,
    discover,
)


class TestSlugFromUrl:
    def test_standard_url(self):
        assert _slug_from_url("https://mycompany.traffit.com") == "mycompany"

    def test_with_path(self):
        assert _slug_from_url("https://acme.traffit.com/career/") == "acme"

    def test_ignored_slugs(self):
        for slug in ("www", "api", "cdn", "app", "help", "knowledge"):
            assert _slug_from_url(f"https://{slug}.traffit.com") is None

    def test_non_traffit_url(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_empty_subdomain(self):
        assert _slug_from_url("https://traffit.com") is None


class TestApiUrl:
    def test_basic(self):
        assert _api_url("acme") == "https://acme.traffit.com/public/job_posts/published"

    def test_with_underscores(self):
        assert _api_url("my_company") == "https://my_company.traffit.com/public/job_posts/published"


class TestBoardUrl:
    def test_basic(self):
        assert _board_url("acme") == "https://acme.traffit.com/career/"


class TestGetValue:
    def test_found(self):
        values = [
            {"field_id": "description", "value": "<p>Hello</p>"},
            {"field_id": "requirements", "value": "<p>Skills</p>"},
        ]
        assert _get_value(values, "description") == "<p>Hello</p>"

    def test_not_found(self):
        values = [{"field_id": "description", "value": "text"}]
        assert _get_value(values, "requirements") is None

    def test_empty_list(self):
        assert _get_value([], "description") is None


class TestParseLocation:
    def test_locality_only(self):
        values = [
            {
                "field_id": "geolocation",
                "value": '{"locality": "Kraków"}',
            }
        ]
        assert _parse_location(values) == ["Kraków"]

    def test_locality_and_country(self):
        values = [
            {
                "field_id": "geolocation",
                "value": '{"locality": "Kraków", "country": "Polska"}',
            }
        ]
        assert _parse_location(values) == ["Kraków, Polska"]

    def test_missing_geolocation(self):
        values = [{"field_id": "description", "value": "text"}]
        assert _parse_location(values) is None

    def test_malformed_json(self):
        values = [{"field_id": "geolocation", "value": "not json"}]
        assert _parse_location(values) is None

    def test_no_locality(self):
        values = [{"field_id": "geolocation", "value": '{"country": "Polska"}'}]
        assert _parse_location(values) is None


class TestParseSalary:
    def test_full_salary(self):
        options = {
            "_Salary_MIN": "6000",
            "_Salary_MAX": "10000",
            "_Salary_Currency": "PLN",
            "_Salary_Rate": "Monthly",
        }
        result = _parse_salary(options)
        assert result == {
            "currency": "PLN",
            "min": 6000.0,
            "max": 10000.0,
            "unit": "month",
        }

    def test_partial_min_only(self):
        options = {
            "_Salary_MIN": "5000",
            "_Salary_Currency": "PLN",
            "_Salary_Rate": "Monthly",
        }
        result = _parse_salary(options)
        assert result is not None
        assert result["min"] == 5000.0
        assert result["max"] is None

    def test_missing(self):
        assert _parse_salary({}) is None

    def test_no_currency(self):
        options = {"_Salary_MIN": "5000", "_Salary_MAX": "10000"}
        assert _parse_salary(options) is None

    def test_unknown_rate(self):
        options = {
            "_Salary_MIN": "100",
            "_Salary_Currency": "EUR",
            "_Salary_Rate": "Weekly",
        }
        result = _parse_salary(options)
        assert result is not None
        assert result["unit"] == "month"  # default fallback

    def test_yearly_rate(self):
        options = {
            "_Salary_MIN": "60000",
            "_Salary_MAX": "80000",
            "_Salary_Currency": "PLN",
            "_Salary_Rate": "Yearly",
        }
        result = _parse_salary(options)
        assert result["unit"] == "year"

    def test_hourly_rate(self):
        options = {
            "_Salary_MIN": "50",
            "_Salary_Currency": "EUR",
            "_Salary_Rate": "Hourly",
        }
        result = _parse_salary(options)
        assert result["unit"] == "hour"


class TestParseJob:
    def _make_job(self, **overrides):
        base = {
            "url": "https://acme.traffit.com/public/an/abc123",
            "id": 1,
            "advert": {
                "values": [
                    {"field_id": "description", "value": "<p>Job description</p>"},
                    {
                        "field_id": "geolocation",
                        "value": '{"locality": "Kraków", "country": "Polska"}',
                    },
                    {"field_id": "requirements", "value": "<p>Requirements</p>"},
                    {"field_id": "responsibilities", "value": "<p>Tasks</p>"},
                    {"field_id": "benefits", "value": "<p>Benefits</p>"},
                ],
                "id": 100,
                "name": "Finance Manager",
                "language": "en",
                "recruitment": {"id": 1, "nr_ref": "1/2/2026/JŁ/718"},
            },
            "valid_start": "2026-03-06 15:01:44",
            "valid_end": None,
            "awarded": False,
            "options": {
                "job_type": ["Full time"],
                "remote": "0",
                "branches": "Administrative",
                "_Salary_MIN": "6000",
                "_Salary_MAX": "10000",
                "_Salary_Currency": "PLN",
                "_Salary_Rate": "Monthly",
                "_work_model": "Hybrid",
            },
        }
        base.update(overrides)
        return base

    def test_basic_all_fields(self):
        result = _parse_job(self._make_job())
        assert result is not None
        assert result.url == "https://acme.traffit.com/public/an/abc123"
        assert result.title == "Finance Manager"
        assert result.description == "<p>Job description</p>"
        assert result.locations == ["Kraków, Polska"]
        assert result.employment_type == "full-time"
        assert result.job_location_type == "hybrid"
        assert result.date_posted == "2026-03-06"
        assert result.base_salary == {
            "currency": "PLN",
            "min": 6000.0,
            "max": 10000.0,
            "unit": "month",
        }
        assert result.language == "en"
        assert result.extras == {
            "requirements": "<p>Requirements</p>",
            "responsibilities": "<p>Tasks</p>",
            "benefits": "<p>Benefits</p>",
        }
        assert result.metadata == {
            "reference": "1/2/2026/JŁ/718",
            "department": "Administrative",
        }

    def test_missing_url(self):
        job = self._make_job()
        del job["url"]
        assert _parse_job(job) is None

    def test_description_extraction(self):
        job = self._make_job()
        job["advert"]["values"] = [
            {"field_id": "description", "value": "<h1>Title</h1><p>Content</p>"}
        ]
        result = _parse_job(job)
        assert result.description == "<h1>Title</h1><p>Content</p>"

    def test_location_parsing(self):
        job = self._make_job()
        job["advert"]["values"] = [{"field_id": "geolocation", "value": '{"locality": "Warsaw"}'}]
        result = _parse_job(job)
        assert result.locations == ["Warsaw"]

    def test_employment_type_mapping(self):
        for raw_type, expected in [
            ("Full time", "full-time"),
            ("Part time", "part-time"),
            ("Contract", "contract"),
            ("Internship", "internship"),
        ]:
            job = self._make_job()
            job["options"]["job_type"] = [raw_type]
            result = _parse_job(job)
            assert result.employment_type == expected, f"Failed for {raw_type}"

    def test_employment_type_unknown(self):
        job = self._make_job()
        job["options"]["job_type"] = ["Freelance"]
        result = _parse_job(job)
        assert result.employment_type is None

    def test_job_location_type_remote(self):
        job = self._make_job()
        job["options"]["remote"] = "1"
        result = _parse_job(job)
        assert result.job_location_type == "remote"

    def test_job_location_type_hybrid(self):
        job = self._make_job()
        job["options"]["remote"] = "0"
        job["options"]["_work_model"] = "Hybrid"
        result = _parse_job(job)
        assert result.job_location_type == "hybrid"

    def test_job_location_type_remote_via_work_model(self):
        job = self._make_job()
        job["options"]["remote"] = "0"
        job["options"]["_work_model"] = "Remote"
        result = _parse_job(job)
        assert result.job_location_type == "remote"

    def test_date_extraction(self):
        job = self._make_job()
        job["valid_start"] = "2025-12-01 09:00:00"
        result = _parse_job(job)
        assert result.date_posted == "2025-12-01"

    def test_salary_assembly(self):
        job = self._make_job()
        job["options"] = {
            "_Salary_MIN": "3000",
            "_Salary_MAX": "5000",
            "_Salary_Currency": "EUR",
            "_Salary_Rate": "Monthly",
        }
        result = _parse_job(job)
        assert result.base_salary == {
            "currency": "EUR",
            "min": 3000.0,
            "max": 5000.0,
            "unit": "month",
        }

    def test_language(self):
        job = self._make_job()
        job["advert"]["language"] = "pl"
        result = _parse_job(job)
        assert result.language == "pl"

    def test_extras_present(self):
        job = self._make_job()
        result = _parse_job(job)
        assert "requirements" in result.extras
        assert "responsibilities" in result.extras
        assert "benefits" in result.extras

    def test_extras_empty_when_no_values(self):
        job = self._make_job()
        job["advert"]["values"] = []
        result = _parse_job(job)
        assert result.extras is None

    def test_metadata_reference(self):
        job = self._make_job()
        result = _parse_job(job)
        assert result.metadata["reference"] == "1/2/2026/JŁ/718"

    def test_metadata_department(self):
        job = self._make_job()
        result = _parse_job(job)
        assert result.metadata["department"] == "Administrative"

    def test_no_metadata(self):
        job = self._make_job()
        job["advert"]["recruitment"] = {}
        del job["options"]["branches"]
        result = _parse_job(job)
        assert result.metadata is None


class TestDiscover:
    def _make_api_job(self, url, title, **kwargs):
        job = {
            "url": url,
            "advert": {
                "values": [{"field_id": "description", "value": "<p>Desc</p>"}],
                "name": title,
                "language": "en",
                "recruitment": {},
            },
            "valid_start": "2026-01-01 00:00:00",
            "awarded": False,
            "options": {},
        }
        job.update(kwargs)
        return job

    async def test_returns_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    self._make_api_job("https://acme.traffit.com/public/an/1", "Job A"),
                    self._make_api_job("https://acme.traffit.com/public/an/2", "Job B"),
                ],
                headers={"x-result-total-pages": "1"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.traffit.com/career/",
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
                "board_url": "https://acme.traffit.com/career/",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive TRAFFIT slug"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "acme" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_slug_from_url(self):
        def handler(request):
            assert "acme" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.traffit.com/career/",
                "metadata": {},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_skips_awarded_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    self._make_api_job(
                        "https://acme.traffit.com/public/an/1",
                        "Awarded",
                        awarded=True,
                    ),
                    self._make_api_job(
                        "https://acme.traffit.com/public/an/2",
                        "Active",
                        awarded=False,
                    ),
                ],
                headers={"x-result-total-pages": "1"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.traffit.com/career/",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Active"

    async def test_pagination(self):
        """Two pages of results."""

        def handler(request):
            page = request.headers.get("X-Request-Current-Page", "1")
            if page == "1":
                return httpx.Response(
                    200,
                    json=[self._make_api_job("https://acme.traffit.com/public/an/1", "Page 1 Job")],
                    headers={"x-result-total-pages": "2"},
                )
            else:
                return httpx.Response(
                    200,
                    json=[self._make_api_job("https://acme.traffit.com/public/an/2", "Page 2 Job")],
                    headers={"x-result-total-pages": "2"},
                )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.traffit.com/career/",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            titles = {j.title for j in jobs}
            assert titles == {"Page 1 Job", "Page 2 Job"}

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.traffit.com/career/",
                "metadata": {"slug": "acme"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)


class TestCanHandle:
    async def test_traffit_url_with_api(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[{"id": 1}],
                headers={"x-result-total-count": "5"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.traffit.com", client)
            assert result is not None
            assert result["slug"] == "acme"
            assert result["jobs"] == 5

    async def test_url_without_client(self):
        result = await can_handle("https://acme.traffit.com")
        assert result == {"slug": "acme"}

    async def test_api_fails_still_returns_slug(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.traffit.com", client)
            assert result == {"slug": "acme"}

    async def test_non_traffit_url(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_html_marker_detection(self):
        def handler(request):
            url = str(request.url)
            if "traffit.com/public/job_posts" in url:
                return httpx.Response(
                    200,
                    json=[{"id": 1}],
                    headers={"x-result-total-count": "3"},
                )
            return httpx.Response(
                200,
                text="<html><body>"
                '<script src="https://cdn3.traffit.com/js/app.js"></script>'
                '<div data-slug="https://myco.traffit.com/career"></div>'
                "</body></html>",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["slug"] == "myco"
            assert result["jobs"] == 3

    async def test_no_match(self):
        def handler(request):
            return httpx.Response(200, text="<html>nothing here</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None

    async def test_ignored_slug_in_html(self):
        def handler(request):
            return httpx.Response(
                200,
                text="<html><body>"
                '<script src="https://cdn3.traffit.com/js/app.js"></script>'
                '<link href="https://cdn.traffit.com/style.css">'
                "</body></html>",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None
