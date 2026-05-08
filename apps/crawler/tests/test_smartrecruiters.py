from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.smartrecruiters import (
    _get_page_with_retry,
    _token_from_url,
    can_handle,
    discover,
)
from src.core.scrapers import JobContent
from src.core.scrapers.smartrecruiters import (
    _build_description,
    _build_location,
    _parse_detail,
    _parse_job_url,
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

    async def test_http_error_raises(self, monkeypatch):
        """Persistent 5xx exhausts the retry budget and surfaces as
        ``PaginationFetchError`` (#2749) — semantically the same
        "scrape-level failure" outcome as the prior
        ``HTTPStatusError`` from ``raise_for_status``, but routed
        through the unified pagination-failure path that every other
        paginating monitor uses (workday #2748, lever #2749, dom #2722).
        """
        from src.core.monitors import smartrecruiters as sr_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.smartrecruiters.com/acme",
                "metadata": {"token": "acme"},
            }
            with pytest.raises(PaginationFetchError):
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


class TestParseJobUrl:
    def test_bare_id(self):
        url = "https://jobs.smartrecruiters.com/Nexthink/743999106810286"
        assert _parse_job_url(url) == ("Nexthink", "743999106810286")

    def test_id_with_slug(self):
        url = "https://jobs.smartrecruiters.com/Nexthink/743999106810286-senior-software-engineer"
        assert _parse_job_url(url) == ("Nexthink", "743999106810286-senior-software-engineer")

    def test_careers_subdomain(self):
        url = "https://careers.smartrecruiters.com/AcmeCorp/123456789"
        assert _parse_job_url(url) == ("AcmeCorp", "123456789")

    def test_non_matching(self):
        assert _parse_job_url("https://example.com/job/123") == (None, None)


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
                {},
                client,
            )
            assert result.title == "Senior Engineer"
            assert "<p>Build things</p>" in result.description
            assert result.locations == ["Lausanne, Switzerland"]
            assert result.employment_type == "Full-time"

    async def test_unparseable_url(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape("https://example.com/job/123", {}, client)
            assert result.title is None

    async def test_token_derived_from_url(self):
        """Token is extracted from URL path — no config needed."""
        posting = {"name": "Test Job", "jobAd": {"sections": {}}}
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=posting))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape("https://jobs.smartrecruiters.com/acme/123", {}, client)
            assert result.title == "Test Job"

    async def test_detail_404(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://jobs.smartrecruiters.com/acme/123",
                {},
                client,
            )
            assert result.title is None


_LIST_URL = "https://api.smartrecruiters.com/v1/companies/acme/postings"


class TestGetPageWithRetry:
    """``_get_page_with_retry`` mirrors ``fetch_with_retry``'s contract on
    SmartRecruiters' GET list endpoint: 5xx / 408 / 425 / 429 / network
    errors are retried, non-retryable 4xx fail fast, and persistent
    failures raise :class:`PaginationFetchError` so a single broken
    pagination page doesn't silently truncate the run (#2749).
    """

    async def test_returns_on_success(self):
        def handler(request):
            return httpx.Response(200, json={"content": [], "totalFound": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(client, _LIST_URL, {"limit": 100, "offset": 0})
            assert data == {"content": [], "totalFound": 0}

    async def test_retries_on_429_then_succeeds(self, monkeypatch):
        from src.core.monitors import smartrecruiters as sr_module

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json={"content": [{"id": "p1"}], "totalFound": 1})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(
                client, _LIST_URL, {"limit": 100, "offset": 0}, base_delay=0.001
            )
            assert data["content"] == [{"id": "p1"}]
            assert calls["n"] == 3

    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        """Pre-fix, a 5xx anywhere in the loop made ``raise_for_status``
        throw and the run was recorded as a scrape-level failure.
        Now 503 is retried like every other transient.
        """
        from src.core.monitors import smartrecruiters as sr_module

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503, text="service unavailable")
            return httpx.Response(200, json={"content": [], "totalFound": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(
                client, _LIST_URL, {"limit": 100, "offset": 0}, base_delay=0.001
            )
            assert data == {"content": [], "totalFound": 0}
            assert calls["n"] == 3

    async def test_retries_on_cloudflare_5xx(self, monkeypatch):
        """Cloudflare origin codes 520-526/530 are retried (parity with
        dom + workday + lever + accenture + PCSX)."""
        from src.core.monitors import smartrecruiters as sr_module

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(520, text="cf origin error")
            return httpx.Response(200, json={"content": [], "totalFound": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(
                client, _LIST_URL, {"limit": 100, "offset": 0}, base_delay=0.001
            )
            assert data == {"content": [], "totalFound": 0}
            assert calls["n"] == 2

    async def test_raises_after_persistent_5xx(self, monkeypatch):
        """Issue #2749 acceptance: persistent 5xx exhausts the retry
        budget and raises ``PaginationFetchError`` — no silent
        truncation.
        """
        from src.core.monitors import smartrecruiters as sr_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(500, text="internal")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "offset": 0},
                    retries=3,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status == 500
            assert exc_info.value.attempts == 3
            assert calls["n"] == 3

    async def test_raises_on_non_retryable_4xx_immediately(self, monkeypatch):
        """A 401 / 403 / 404 indicates a hard error — no point
        retrying. Raise ``PaginationFetchError`` on the first attempt.
        """
        from src.core.monitors import smartrecruiters as sr_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(401, text="unauthorized")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "offset": 0},
                    retries=3,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status == 401
            assert calls["n"] == 1

    async def test_raises_after_persistent_network_error(self, monkeypatch):
        from src.core.monitors import smartrecruiters as sr_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            raise httpx.ConnectError("conn refused")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "offset": 0},
                    retries=2,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status is None
            assert exc_info.value.last_error == "ConnectError"

    async def test_raises_on_empty_200_body(self, monkeypatch):
        """Per the issue (#2749), a 200 with a body that decodes to
        ``null`` (or any non-dict shape) used to leave
        ``totalFound`` defaulting to 0 and ``content`` defaulting to
        ``[]`` — silently breaking the loop on
        ``offset >= total_found``. Now the helper treats it as a
        transient failure (retry, then raise) so the run surfaces.
        """
        from src.core.monitors import smartrecruiters as sr_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            # JSON ``null`` decodes to Python ``None`` — non-dict.
            return httpx.Response(
                200, content=b"null", headers={"content-type": "application/json"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "offset": 0},
                    retries=2,
                    base_delay=0.001,
                )

    async def test_raises_on_empty_array_200_body(self, monkeypatch):
        """A 200 with body ``[]`` (empty list — wrong shape for
        SmartRecruiters which returns dicts) is non-dict and must
        raise. Distinguishes empty-200 from a legitimate
        ``{"content": [], "totalFound": 0}`` end-of-results.
        """
        from src.core.monitors import smartrecruiters as sr_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "offset": 0},
                    retries=2,
                    base_delay=0.001,
                )

    async def test_legitimate_empty_dict_returns(self):
        """A 200 with body ``{"content": [], "totalFound": 0}`` is
        the canonical end-of-results signal for an empty board.
        Must return cleanly so the discover loop's
        ``offset >= total_found`` end-check fires correctly.
        """

        def handler(request):
            return httpx.Response(200, json={"content": [], "totalFound": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(
                client, _LIST_URL, {"limit": 100, "offset": 0}, base_delay=0.001
            )
            assert data == {"content": [], "totalFound": 0}


class TestDiscoverPaginationRetry:
    """Issue #2749 acceptance: the discover loop propagates the new
    retry-then-raise contract end-to-end. Pre-fix, a 5xx mid-pagination
    raised ``HTTPStatusError`` straight out and a 200-with-``{}``
    silently broke the loop. Now both transients are retried and
    persistent failures raise ``PaginationFetchError``.
    """

    async def test_503_then_200_pagination_continues(self, monkeypatch):
        from src.core.monitors import smartrecruiters as sr_module

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())

        # Total 150 (PAGE_SIZE=100 → two pages). First page succeeds with
        # 100 postings; second page returns 503 once then 200 with 50.
        page2_calls = {"n": 0}

        def handler(request):
            params = dict(request.url.params)
            offset = int(params.get("offset", 0))
            if offset == 0:
                return httpx.Response(
                    200,
                    json={
                        "content": [{"id": f"p{i}"} for i in range(100)],
                        "totalFound": 150,
                    },
                )
            page2_calls["n"] += 1
            if page2_calls["n"] < 2:
                return httpx.Response(503, text="unavailable")
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
            assert page2_calls["n"] == 2

    async def test_persistent_500_mid_pagination_raises(self, monkeypatch):
        """Pre-fix, a 5xx on page N>0 raised ``HTTPStatusError`` straight
        out — recorded as scrape-level failure but bypassed the
        unified retry contract. Now the helper raises
        ``PaginationFetchError`` cleanly via the same path every other
        paginating monitor uses.
        """
        from src.core.monitors import smartrecruiters as sr_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            params = dict(request.url.params)
            offset = int(params.get("offset", 0))
            if offset > 0:
                return httpx.Response(500, text="internal")
            return httpx.Response(
                200,
                json={
                    "content": [{"id": f"p{i}"} for i in range(100)],
                    "totalFound": 150,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.smartrecruiters.com/acme",
                "metadata": {"token": "acme"},
            }
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(board, client)
            assert exc_info.value.last_status == 500

    async def test_empty_200_mid_pagination_raises(self, monkeypatch):
        """The load-bearing test for #2749: a 200 with a non-dict body
        (e.g., a CDN dropping the body and returning ``[]``, or an
        anti-bot challenge served as 200) on page N>0 MUST raise, not
        silently truncate. The pre-fix code path would have broken
        on the missing-totalFound default and returned the partial
        100-URL set as success, feeding ``_MARK_GONE_BY_TIMESTAMP``
        for the unfetched 50 URLs.
        """
        from src.core.monitors import smartrecruiters as sr_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            params = dict(request.url.params)
            offset = int(params.get("offset", 0))
            if offset > 0:
                # CDN/anti-bot envelope served as 200 — the vulnerable case.
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json={
                    "content": [{"id": f"p{i}"} for i in range(100)],
                    "totalFound": 150,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.smartrecruiters.com/acme",
                "metadata": {"token": "acme"},
            }
            with pytest.raises(PaginationFetchError):
                await discover(board, client)

    async def test_legitimate_empty_first_page_returns_empty(self, monkeypatch):
        """An empty board (legitimate ``{"content": [], "totalFound": 0}``
        on the first page) must return an empty set cleanly — the
        end-signal logic ``offset >= total_found`` fires correctly
        without any retry.
        """
        from src.core.monitors import smartrecruiters as sr_module

        monkeypatch.setattr(sr_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"content": [], "totalFound": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.smartrecruiters.com/acme",
                "metadata": {"token": "acme"},
            }
            urls = await discover(board, client)
            assert urls == set()
            assert calls["n"] == 1  # No retries — empty body is structurally valid.
