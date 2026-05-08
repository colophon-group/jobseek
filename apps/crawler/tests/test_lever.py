from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.lever import (
    _api_url,
    _build_description,
    _get_page_with_retry,
    _parse_job,
    _parse_salary,
    _region_from_url,
    _token_from_url,
    can_handle,
    discover,
)


class TestBuildDescription:
    def test_description_only(self):
        posting = {"description": "<p>About the role</p>"}
        assert _build_description(posting) == "<p>About the role</p>"

    def test_with_lists(self):
        posting = {
            "description": "<p>Intro</p>",
            "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
        }
        result = _build_description(posting)
        assert "<p>Intro</p>" in result
        assert "<h3>Requirements</h3>" in result
        assert "<li>Python</li>" in result

    def test_with_additional(self):
        posting = {"additional": "<p>Benefits</p>"}
        assert _build_description(posting) == "<p>Benefits</p>"

    def test_all_sections(self):
        posting = {
            "description": "Intro",
            "lists": [{"text": "Reqs", "content": "items"}],
            "additional": "Extra",
        }
        result = _build_description(posting)
        assert "Intro" in result
        assert "Reqs" in result
        assert "Extra" in result

    def test_empty(self):
        assert _build_description({}) is None

    def test_empty_sections(self):
        posting = {"description": "", "lists": [{"text": "", "content": ""}]}
        assert _build_description(posting) is None


class TestParseSalary:
    def test_per_year(self):
        salary_range = {
            "currency": "USD",
            "min": 100000,
            "max": 150000,
            "interval": "per-year-salary",
        }
        result = _parse_salary(salary_range)
        assert result == {"currency": "USD", "min": 100000, "max": 150000, "unit": "year"}

    def test_per_hour(self):
        salary_range = {
            "currency": "USD",
            "min": 50,
            "max": 80,
            "interval": "per-hour-wage",
        }
        result = _parse_salary(salary_range)
        assert result["unit"] == "hour"

    def test_per_month(self):
        salary_range = {
            "currency": "EUR",
            "min": 5000,
            "max": 7000,
            "interval": "per-month-salary",
        }
        result = _parse_salary(salary_range)
        assert result["unit"] == "month"

    def test_unknown_interval(self):
        result = _parse_salary({"currency": "USD", "min": 100, "max": 200, "interval": "custom"})
        assert result["unit"] == "custom"

    def test_none(self):
        assert _parse_salary(None) is None

    def test_no_min_max(self):
        assert _parse_salary({"currency": "USD"}) is None

    def test_only_min(self):
        result = _parse_salary({"currency": "USD", "min": 100000, "interval": "per-year-salary"})
        assert result is not None
        assert result["min"] == 100000
        assert result["max"] is None


class TestParseJob:
    def test_basic(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "text": "Engineer",
            "categories": {"location": "NYC"},
        }
        result = _parse_job(posting)
        assert result is not None
        assert result.url == "https://jobs.lever.co/test/123"
        assert result.title == "Engineer"
        assert result.locations == ["NYC"]

    def test_missing_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"text": "No URL"}) is None

    def test_all_locations(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {"allLocations": ["NYC", "London", "Berlin"]},
        }
        result = _parse_job(posting)
        assert result.locations == ["NYC", "London", "Berlin"]

    def test_single_location_fallback(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {"location": "Remote"},
        }
        result = _parse_job(posting)
        assert result.locations == ["Remote"]

    def test_no_locations(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {},
        }
        result = _parse_job(posting)
        assert result.locations is None

    def test_metadata_team_and_department(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {"team": "Platform", "department": "Engineering"},
            "id": "abc123",
        }
        result = _parse_job(posting)
        assert result.metadata["team"] == "Platform"
        assert result.metadata["department"] == "Engineering"
        assert result.metadata["id"] == "abc123"

    def test_employment_type(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {"commitment": "Full-time"},
        }
        result = _parse_job(posting)
        assert result.employment_type == "Full-time"

    def test_workplace_type(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "workplaceType": "remote",
            "categories": {},
        }
        result = _parse_job(posting)
        assert result.job_location_type == "remote"

    def test_salary(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "salaryRange": {
                "currency": "USD",
                "min": 100000,
                "max": 150000,
                "interval": "per-year-salary",
            },
            "categories": {},
        }
        result = _parse_job(posting)
        assert result.base_salary is not None
        assert result.base_salary["currency"] == "USD"
        assert result.base_salary["min"] == 100000

    def test_no_metadata_when_empty(self):
        posting = {
            "hostedUrl": "https://jobs.lever.co/test/123",
            "categories": {},
        }
        result = _parse_job(posting)
        assert result.metadata is None


class TestTokenFromUrl:
    def test_standard(self):
        assert _token_from_url("https://jobs.lever.co/stripe") == "stripe"

    def test_with_path(self):
        assert _token_from_url("https://jobs.lever.co/stripe/123") == "stripe"

    def test_with_hyphen(self):
        assert _token_from_url("https://jobs.lever.co/my-company") == "my-company"

    def test_no_match(self):
        assert _token_from_url("https://example.com/careers") is None

    def test_ignore_token(self):
        assert _token_from_url("https://jobs.lever.co/v0") is None

    def test_eu_domain(self):
        assert _token_from_url("https://jobs.eu.lever.co/xm") == "xm"

    def test_eu_domain_with_path(self):
        assert _token_from_url("https://jobs.eu.lever.co/xm/some-job-id") == "xm"


class TestRegionFromUrl:
    def test_standard_lever(self):
        assert _region_from_url("https://jobs.lever.co/stripe") is None

    def test_eu_jobs_domain(self):
        assert _region_from_url("https://jobs.eu.lever.co/xm") == "eu"

    def test_eu_api_domain(self):
        assert _region_from_url("https://api.eu.lever.co/v0/postings/xm") == "eu"

    def test_non_lever(self):
        assert _region_from_url("https://example.com/careers") is None


class TestApiUrl:
    def test_basic(self):
        assert _api_url("stripe") == "https://api.lever.co/v0/postings/stripe"

    def test_eu_region(self):
        assert _api_url("xm", region="eu") == "https://api.eu.lever.co/v0/postings/xm"

    def test_none_region(self):
        assert _api_url("stripe", region=None) == "https://api.lever.co/v0/postings/stripe"


class TestDiscover:
    async def test_single_page(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {
                        "hostedUrl": "https://jobs.lever.co/test/1",
                        "text": "Job 1",
                        "categories": {},
                    },
                    {
                        "hostedUrl": "https://jobs.lever.co/test/2",
                        "text": "Job 2",
                        "categories": {},
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            jobs = await discover(board, client)
            assert len(jobs) == 2
            titles = {j.title for j in jobs}
            assert titles == {"Job 1", "Job 2"}

    async def test_sends_application_json_accept_header(self):
        """Lever returns an HTML widget on a browser Accept header. The monitor
        must force ``Accept: application/json`` on every API call so the shared
        HTTP client's browser Accept default doesn't flip Lever into HTML mode."""
        seen_accepts: list[str] = []

        def handler(request):
            seen_accepts.append(request.headers.get("accept", ""))
            return httpx.Response(200, json=[])

        browser_accept = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Accept": browser_accept},
        ) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            await discover(board, client)
            assert seen_accepts, "no request was issued"
            for accept in seen_accepts:
                assert accept == "application/json", (
                    f"expected Lever request to force application/json, got {accept!r}"
                )

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_token_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Lever token"):
                await discover(board, client)

    async def test_skips_jobs_without_url(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {"text": "No URL", "categories": {}},
                    {
                        "hostedUrl": "https://jobs.lever.co/test/1",
                        "text": "Has URL",
                        "categories": {},
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Has URL"

    async def test_pagination(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            params = dict(request.url.params)
            skip = int(params.get("skip", 0))
            if skip == 0:
                # Full batch of 100
                return httpx.Response(
                    200,
                    json=[
                        {
                            "hostedUrl": f"https://jobs.lever.co/test/{i}",
                            "text": f"Job {i}",
                            "categories": {},
                        }
                        for i in range(100)
                    ],
                )
            else:
                # Partial batch — end of pages
                return httpx.Response(
                    200,
                    json=[
                        {
                            "hostedUrl": f"https://jobs.lever.co/test/{i}",
                            "text": f"Job {i}",
                            "categories": {},
                        }
                        for i in range(100, 110)
                    ],
                )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            jobs = await discover(board, client)
            assert len(jobs) == 110
            assert call_count == 2

    async def test_http_error_raises(self, monkeypatch):
        """Persistent 5xx exhausts the retry budget and surfaces as
        ``PaginationFetchError`` (#2749) — semantically the same
        "scrape-level failure" outcome as the prior
        ``HTTPStatusError`` from ``raise_for_status``, but routed
        through the unified pagination-failure path that every other
        paginating monitor uses (workday #2748, dom #2722, etc.).
        """
        from src.core.monitors import lever as lever_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            with pytest.raises(PaginationFetchError):
                await discover(board, client)

    async def test_eu_region_from_url(self):
        def handler(request):
            assert "api.eu.lever.co" in str(request.url)
            return httpx.Response(
                200,
                json=[
                    {
                        "hostedUrl": "https://jobs.eu.lever.co/xm/1",
                        "text": "EU Job",
                        "categories": {},
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.eu.lever.co/xm", "metadata": {"token": "xm"}}
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "EU Job"

    async def test_eu_region_from_metadata(self):
        def handler(request):
            assert "api.eu.lever.co" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"token": "xm", "region": "eu"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0


class TestCanHandle:
    async def test_lever_url(self):
        result = await can_handle("https://jobs.lever.co/stripe")
        assert result == {"token": "stripe"}

    async def test_non_lever_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_in_page_html(self):
        def handler(request):
            return httpx.Response(
                200,
                text='<html><script src="https://api.lever.co/v0/postings/myco"></script></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result.get("token") == "myco"

    async def test_probe_fallback(self):
        def handler(request):
            url = str(request.url)
            if "api.lever.co" in url:
                return httpx.Response(200, json=[])
            return httpx.Response(200, text="<html>plain page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is not None
            assert result.get("token") == "example"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "api.lever.co" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no lever refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None

    async def test_eu_lever_url(self):
        def handler(request):
            assert "api.eu.lever.co" in str(request.url)
            return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.eu.lever.co/xm", client)
            assert result is not None
            assert result["token"] == "xm"
            assert result["region"] == "eu"
            assert result["jobs"] == 2

    async def test_eu_lever_url_no_client(self):
        result = await can_handle("https://jobs.eu.lever.co/xm")
        assert result == {"token": "xm", "region": "eu"}


_LIST_URL = "https://api.lever.co/v0/postings/testco"


class TestGetPageWithRetry:
    """``_get_page_with_retry`` mirrors ``fetch_with_retry``'s contract on
    Lever's GET list endpoint: 5xx / 408 / 425 / 429 / network errors
    are retried, non-retryable 4xx fail fast (with a sentinel for
    first-page-404 → ``BoardGoneError``), and persistent failures
    raise :class:`PaginationFetchError` so a single broken pagination
    page doesn't silently truncate the run (#2749).
    """

    async def test_returns_on_success(self):
        def handler(request):
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(client, _LIST_URL, {"limit": 100, "skip": 0})
            assert data == []

    async def test_retries_on_429_then_succeeds(self, monkeypatch):
        from src.core.monitors import lever as lever_module

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json=[{"hostedUrl": "https://x"}])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(
                client, _LIST_URL, {"limit": 100, "skip": 0}, base_delay=0.001
            )
            assert data == [{"hostedUrl": "https://x"}]
            assert calls["n"] == 3

    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        """Pre-fix, a 5xx anywhere in the loop made ``raise_for_status``
        throw and the run was recorded as a scrape-level failure.
        Now 503 is retried like every other transient.
        """
        from src.core.monitors import lever as lever_module

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503, text="service unavailable")
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(
                client, _LIST_URL, {"limit": 100, "skip": 0}, base_delay=0.001
            )
            assert data == []
            assert calls["n"] == 3

    async def test_retries_on_cloudflare_5xx(self, monkeypatch):
        """Cloudflare origin codes 520-526/530 are retried (parity with
        dom + workday + accenture + PCSX)."""
        from src.core.monitors import lever as lever_module

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(520, text="cf origin error")
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(
                client, _LIST_URL, {"limit": 100, "skip": 0}, base_delay=0.001
            )
            assert data == []
            assert calls["n"] == 2

    async def test_raises_after_persistent_5xx(self, monkeypatch):
        """Issue #2749 acceptance: persistent 5xx exhausts the retry
        budget and raises ``PaginationFetchError`` — no silent
        truncation.
        """
        from src.core.monitors import lever as lever_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(500, text="internal")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "skip": 0},
                    retries=3,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status == 500
            assert exc_info.value.attempts == 3
            assert calls["n"] == 3

    async def test_raises_on_non_retryable_4xx_immediately(self, monkeypatch):
        """A 401 / 403 indicates a hard error — no point retrying.
        Raise ``PaginationFetchError`` on the first attempt."""
        from src.core.monitors import lever as lever_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(401, text="unauthorized")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "skip": 0},
                    retries=3,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status == 401
            assert calls["n"] == 1

    async def test_raises_after_persistent_network_error(self, monkeypatch):
        from src.core.monitors import lever as lever_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            raise httpx.ConnectError("conn refused")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "skip": 0},
                    retries=2,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status is None
            assert exc_info.value.last_error == "ConnectError"

    async def test_raises_on_empty_200_body(self, monkeypatch):
        """Per the issue (#2749), a 200 with a body that decodes to
        ``null`` (or any non-list shape — Lever returns a JSON array)
        used to leave ``len(batch) < BATCH_SIZE`` true and silently
        ``break`` the pagination loop. Now the helper treats it as a
        transient failure (retry, then raise) so the run surfaces.
        """
        from src.core.monitors import lever as lever_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            # JSON ``null`` decodes to Python ``None`` — non-list.
            return httpx.Response(
                200, content=b"null", headers={"content-type": "application/json"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "skip": 0},
                    retries=2,
                    base_delay=0.001,
                )

    async def test_raises_on_empty_dict_200_body(self, monkeypatch):
        """A 200 with body ``{}`` (empty dict — common CDN/anti-bot
        envelope) is also non-list and must raise rather than silently
        break the loop. Distinguishes empty-200 from a legitimate
        empty array ``[]`` end-of-results.
        """
        from src.core.monitors import lever as lever_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await _get_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 100, "skip": 0},
                    retries=2,
                    base_delay=0.001,
                )

    async def test_legitimate_empty_array_returns(self):
        """An HTTP 200 with body ``[]`` is the canonical end-of-results
        signal — distinguishable from empty-body-200 above. Must
        return cleanly so the discover loop's ``len(batch) < BATCH_SIZE``
        end-check fires correctly.
        """

        def handler(request):
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _get_page_with_retry(
                client, _LIST_URL, {"limit": 100, "skip": 0}, base_delay=0.001
            )
            assert data == []


class TestDiscoverPaginationRetry:
    """Issue #2749 acceptance: the discover loop propagates the new
    retry-then-raise contract end-to-end. Pre-fix, a 5xx mid-pagination
    raised ``HTTPStatusError`` straight out and an empty-200 silently
    broke the loop. Now both transients are retried and persistent
    failures raise ``PaginationFetchError``.
    """

    async def test_503_then_200_pagination_continues(self, monkeypatch):
        from src.core.monitors import lever as lever_module

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())

        # Total 110 (BATCH_SIZE=100 → two pages). First page succeeds with
        # 100 postings; second page returns 503 once then 200 with 10.
        page2_calls = {"n": 0}

        def handler(request):
            params = dict(request.url.params)
            skip = int(params.get("skip", 0))
            if skip == 0:
                return httpx.Response(
                    200,
                    json=[
                        {"hostedUrl": f"https://x/{i}", "text": f"Job {i}", "categories": {}}
                        for i in range(100)
                    ],
                )
            page2_calls["n"] += 1
            if page2_calls["n"] < 2:
                return httpx.Response(503, text="unavailable")
            return httpx.Response(
                200,
                json=[
                    {
                        "hostedUrl": f"https://x/{100 + i}",
                        "text": f"Job {100 + i}",
                        "categories": {},
                    }
                    for i in range(10)
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            jobs = await discover(board, client)
            assert len(jobs) == 110
            # Page 2 was retried once before succeeding.
            assert page2_calls["n"] == 2

    async def test_persistent_500_mid_pagination_raises_not_silent_break(self, monkeypatch):
        """Pre-fix, a 5xx on page N>0 raised ``HTTPStatusError`` straight
        out — recorded as scrape-level failure, but the same shape of
        bug (``len(batch) < BATCH_SIZE`` fires on a non-list batch in
        the unguarded path). Now the helper raises
        ``PaginationFetchError`` cleanly.
        """
        from src.core.monitors import lever as lever_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            params = dict(request.url.params)
            skip = int(params.get("skip", 0))
            if skip > 0:
                return httpx.Response(500, text="internal")
            return httpx.Response(
                200,
                json=[
                    {"hostedUrl": f"https://x/{i}", "text": f"Job {i}", "categories": {}}
                    for i in range(100)
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(board, client)
            assert exc_info.value.last_status == 500

    async def test_empty_200_mid_pagination_raises(self, monkeypatch):
        """The load-bearing test for #2749: a 200 with a non-list body
        on page N>0 (e.g., a CDN dropping the body and returning an
        error envelope) MUST raise, not silently truncate the
        discovery to whatever pages succeeded. The pre-fix code path
        would have broken on ``len(batch) < BATCH_SIZE`` and returned
        the partial 100-job list as success.
        """
        from src.core.monitors import lever as lever_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            params = dict(request.url.params)
            skip = int(params.get("skip", 0))
            if skip > 0:
                # Empty/error envelope served as 200 — vulnerable case.
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                json=[
                    {"hostedUrl": f"https://x/{i}", "text": f"Job {i}", "categories": {}}
                    for i in range(100)
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            with pytest.raises(PaginationFetchError):
                await discover(board, client)

    async def test_first_page_404_still_raises_board_gone(self, monkeypatch):
        """Preserves the #2215 BoardGoneError contract: a 404 on the
        first page is a structural "board removed" signal, not a
        transient failure — must surface as ``BoardGoneError`` so the
        board processor disables in one shot.
        """
        from src.core.monitors import BoardGoneError
        from src.core.monitors import lever as lever_module

        monkeypatch.setattr(lever_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://jobs.lever.co/testco", "metadata": {"token": "testco"}}
            with pytest.raises(BoardGoneError):
                await discover(board, client)
