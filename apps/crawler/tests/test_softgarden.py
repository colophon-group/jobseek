from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.softgarden import (
    _board_url,
    _extract_job_ids,
    _extract_locations,
    _extract_salary,
    _job_url,
    _normalize_employment_type,
    _parse_detail,
    _slug_from_url,
    can_handle,
    discover,
)

# ── URL helpers ──────────────────────────────────────────────────────────


class TestSlugFromUrl:
    def test_standard(self):
        assert _slug_from_url("https://hapaglloyd.softgarden.io") == "hapaglloyd"

    def test_with_path(self):
        assert _slug_from_url("https://ctseventim.softgarden.io/job/12345") == "ctseventim"

    def test_ignored_slugs(self):
        assert _slug_from_url("https://www.softgarden.io") is None
        assert _slug_from_url("https://api.softgarden.io") is None
        assert _slug_from_url("https://app.softgarden.io") is None
        assert _slug_from_url("https://static.softgarden.io") is None
        assert _slug_from_url("https://cdn.softgarden.io") is None

    def test_non_softgarden(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_empty(self):
        assert _slug_from_url("") is None


class TestBoardUrl:
    def test_basic(self):
        assert _board_url("hapaglloyd") == "https://hapaglloyd.softgarden.io"


class TestJobUrl:
    def test_default_pattern(self):
        url = _job_url("https://hapaglloyd.softgarden.io", 12345)
        assert url == "https://hapaglloyd.softgarden.io/job/12345?l=en"

    def test_custom_pattern(self):
        url = _job_url(
            "https://hapaglloyd.softgarden.io",
            12345,
            "{base}/job/{id}?l=de",
        )
        assert url == "https://hapaglloyd.softgarden.io/job/12345?l=de"


# ── Listing parsing ─────────────────────────────────────────────────────


class TestExtractJobIds:
    def test_standard(self):
        html = "<script>var complete_job_id_list = [111, 222, 333];</script>"
        assert _extract_job_ids(html) == [111, 222, 333]

    def test_with_jobs_selected(self):
        html = "<script>var complete_job_id_list = jobs_selected = [48677018, 53688446];</script>"
        assert _extract_job_ids(html) == [48677018, 53688446]

    def test_empty_array(self):
        html = "<script>var complete_job_id_list = [];</script>"
        assert _extract_job_ids(html) == []

    def test_no_match(self):
        html = "<html><body>No jobs here</body></html>"
        assert _extract_job_ids(html) == []


# ── Employment type ──────────────────────────────────────────────────────


class TestNormalizeEmploymentType:
    def test_full_time(self):
        assert _normalize_employment_type("FULL_TIME") == "full-time"

    def test_part_time(self):
        assert _normalize_employment_type("PART_TIME") == "part-time"

    def test_temporary(self):
        assert _normalize_employment_type("TEMPORARY") == "temporary"

    def test_contractor(self):
        assert _normalize_employment_type("CONTRACTOR") == "contract"

    def test_intern(self):
        assert _normalize_employment_type("INTERN") == "internship"

    def test_list_input(self):
        assert _normalize_employment_type(["FULL_TIME", "PART_TIME"]) == "full-time"

    def test_unknown(self):
        assert _normalize_employment_type("FREELANCE") is None

    def test_none(self):
        assert _normalize_employment_type(None) is None


# ── Locations ────────────────────────────────────────────────────────────


class TestExtractLocations:
    def test_address_components(self):
        posting = {
            "jobLocation": {
                "address": {
                    "addressLocality": "Hamburg",
                    "addressCountry": "DE",
                }
            }
        }
        assert _extract_locations(posting) == ["Hamburg, DE"]

    def test_full_address(self):
        posting = {
            "jobLocation": {
                "address": {
                    "addressLocality": "Berlin",
                    "addressRegion": "Berlin",
                    "addressCountry": "DE",
                }
            }
        }
        assert _extract_locations(posting) == ["Berlin, Berlin, DE"]

    def test_name_field(self):
        posting = {"jobLocation": {"name": "New York"}}
        assert _extract_locations(posting) == ["New York"]

    def test_multiple_locations(self):
        posting = {
            "jobLocation": [
                {"address": {"addressLocality": "Berlin", "addressCountry": "DE"}},
                {"address": {"addressLocality": "Munich", "addressCountry": "DE"}},
            ]
        }
        result = _extract_locations(posting)
        assert result == ["Berlin, DE", "Munich, DE"]

    def test_none(self):
        assert _extract_locations({}) is None
        assert _extract_locations({"jobLocation": None}) is None


# ── Salary ───────────────────────────────────────────────────────────────


class TestExtractSalary:
    def test_full_salary(self):
        posting = {
            "baseSalary": {
                "currency": "EUR",
                "value": {"minValue": 50000, "maxValue": 80000, "unitText": "YEAR"},
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "EUR", "min": 50000, "max": 80000, "unit": "year"}

    def test_zero_value_skipped(self):
        posting = {
            "baseSalary": {
                "currency": "EUR",
                "value": {"minValue": 0.0, "maxValue": 0.0, "unitText": "YEAR"},
            }
        }
        assert _extract_salary(posting) is None

    def test_missing_salary(self):
        assert _extract_salary({}) is None
        assert _extract_salary({"baseSalary": None}) is None
        assert _extract_salary({"baseSalary": "invalid"}) is None

    def test_hourly(self):
        posting = {
            "baseSalary": {
                "currency": "USD",
                "value": {"minValue": 20, "maxValue": 30, "unitText": "HOUR"},
            }
        }
        result = _extract_salary(posting)
        assert result["unit"] == "hour"

    def test_monthly(self):
        posting = {
            "baseSalary": {
                "currency": "EUR",
                "value": {"minValue": 3000, "maxValue": 5000, "unitText": "MONTH"},
            }
        }
        result = _extract_salary(posting)
        assert result["unit"] == "month"


# ── Detail parsing ───────────────────────────────────────────────────────


class TestParseDetail:
    def test_full_jsonld(self):
        html = """
        <html>
        <head>
        <script type="application/ld+json">
        {
            "@type": "JobPosting",
            "title": "Software Engineer",
            "description": "<p>Build things</p>",
            "datePosted": "2024-06-01",
            "employmentType": "FULL_TIME",
            "jobLocation": {
                "address": {
                    "addressLocality": "Hamburg",
                    "addressCountry": "DE"
                }
            },
            "baseSalary": {
                "currency": "EUR",
                "value": {"minValue": 60000, "maxValue": 90000, "unitText": "YEAR"}
            }
        }
        </script>
        </head>
        <body></body>
        </html>
        """
        result = _parse_detail(html, "https://example.softgarden.io/job/123")
        assert result is not None
        assert result.url == "https://example.softgarden.io/job/123"
        assert result.title == "Software Engineer"
        assert result.description == "<p>Build things</p>"
        assert result.date_posted == "2024-06-01"
        assert result.employment_type == "full-time"
        assert result.locations == ["Hamburg, DE"]
        assert result.base_salary == {
            "currency": "EUR",
            "min": 60000,
            "max": 90000,
            "unit": "year",
        }

    def test_missing_jsonld(self):
        html = "<html><body>No JSON-LD here</body></html>"
        result = _parse_detail(html, "https://example.softgarden.io/job/123")
        assert result is None

    def test_graph_format(self):
        html = """
        <script type="application/ld+json">
        {"@graph": [
            {"@type": "WebPage", "name": "Jobs"},
            {"@type": "JobPosting", "title": "Designer", "description": "Design stuff"}
        ]}
        </script>
        """
        result = _parse_detail(html, "https://example.softgarden.io/job/456")
        assert result is not None
        assert result.title == "Designer"

    def test_name_fallback(self):
        html = """
        <script type="application/ld+json">
        {"@type": "JobPosting", "name": "Manager"}
        </script>
        """
        result = _parse_detail(html, "https://example.softgarden.io/job/789")
        assert result is not None
        assert result.title == "Manager"


# ── Discover ─────────────────────────────────────────────────────────────


class TestDiscover:
    async def test_returns_jobs(self):
        listing_html = "<script>var complete_job_id_list = [111, 222];</script>"
        detail_html = """
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Engineer", "description": "Build"}
        </script>
        """

        def handler(request):
            url = str(request.url)
            if url == "https://acme.softgarden.io":
                return httpx.Response(200, text=listing_html)
            if "/job/" in url:
                return httpx.Response(200, text=detail_html)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.softgarden.io",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)
            assert jobs[0].title == "Engineer"

    async def test_empty_ids(self):
        def handler(request):
            return httpx.Response(200, text="<html>No jobs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.softgarden.io",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Softgarden"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "myslug" in str(request.url)
            return httpx.Response(200, text="<html>No jobs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"slug": "myslug"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_slug_from_url(self):
        def handler(request):
            assert "testco" in str(request.url)
            return httpx.Response(200, text="<html>No jobs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://testco.softgarden.io",
                "metadata": {},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_custom_pattern(self):
        listing_html = "<script>var complete_job_id_list = [999];</script>"
        detail_html = """
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Dev", "description": "Code"}
        </script>
        """

        def handler(request):
            url = str(request.url)
            if url == "https://acme.softgarden.io":
                return httpx.Response(200, text=listing_html)
            if "/job/999?l=de" in url:
                return httpx.Response(200, text=detail_html)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.softgarden.io",
                "metadata": {"slug": "acme", "job_url_pattern": "{base}/job/{id}?l=de"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1

    async def test_failed_detail_skipped(self):
        listing_html = "<script>var complete_job_id_list = [111, 222];</script>"
        detail_html = """
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Good Job", "description": "Works"}
        </script>
        """

        def handler(request):
            url = str(request.url)
            if url == "https://acme.softgarden.io":
                return httpx.Response(200, text=listing_html)
            if "111" in url:
                return httpx.Response(500)
            if "222" in url:
                return httpx.Response(200, text=detail_html)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.softgarden.io",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Good Job"


# ── Can handle ───────────────────────────────────────────────────────────


class TestCanHandle:
    async def test_softgarden_url_without_client(self):
        result = await can_handle("https://hapaglloyd.softgarden.io")
        assert result is not None
        assert result["slug"] == "hapaglloyd"

    async def test_softgarden_url_with_client(self):
        listing_html = "<script>var complete_job_id_list = [1, 2, 3];</script>"

        def handler(request):
            return httpx.Response(200, text=listing_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://hapaglloyd.softgarden.io", client)
            assert result is not None
            assert result["slug"] == "hapaglloyd"
            assert result["jobs"] == 3

    async def test_html_markers(self):
        page_html = '<html><script src="https://acme.softgarden.io/assets/app.js"></script></html>'
        listing_html = "<script>var complete_job_id_list = [10, 20];</script>"

        def handler(request):
            url = str(request.url)
            if "acme.softgarden.io" in url:
                return httpx.Response(200, text=listing_html)
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["slug"] == "acme"
            assert result["jobs"] == 2

    async def test_no_match(self):
        def handler(request):
            return httpx.Response(200, text="<html>plain page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None

    async def test_non_matching_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None
