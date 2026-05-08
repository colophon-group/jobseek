from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.workday import (
    PAGE_SIZE,
    _api_base,
    _api_list_url,
    _discover_sites,
    _job_url,
    _paginate_query,
    _parse_components,
    _pick_split_facet,
    _post_page_with_retry,
    can_handle,
    discover,
)
from src.core.scrapers.workday import (
    _detail_url,
    _normalize_workday_location,
    _parse_detail,
    _parse_job_url,
    _parse_location_type,
    scrape,
)
from src.shared.http_retry import PaginationFetchError


class TestParseComponents:
    def test_standard_url(self):
        result = _parse_components("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
        assert result == ("nvidia", "wd5", "NVIDIAExternalCareerSite")

    def test_with_locale_prefix(self):
        result = _parse_components(
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
        )
        assert result == ("nvidia", "wd5", "NVIDIAExternalCareerSite")

    def test_hyphenated_company(self):
        result = _parse_components("https://my-company.wd1.myworkdayjobs.com/External")
        assert result == ("my-company", "wd1", "External")

    def test_non_matching_url(self):
        assert _parse_components("https://example.com/careers") is None

    def test_with_trailing_slash(self):
        result = _parse_components("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/")
        assert result == ("nvidia", "wd5", "NVIDIAExternalCareerSite")


class TestApiBase:
    def test_basic(self):
        result = _api_base("nvidia", "wd5")
        assert result == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia"


class TestApiListUrl:
    def test_basic(self):
        result = _api_list_url("nvidia", "wd5", "ExtSite")
        assert result == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/ExtSite/jobs"


class TestDetailUrl:
    def test_basic(self):
        result = _detail_url("nvidia", "wd5", "ExtSite", "/job/Senior-Engineer/JR001")
        assert (
            result
            == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/ExtSite/job/Senior-Engineer/JR001"
        )


class TestJobUrl:
    def test_basic(self):
        result = _job_url("nvidia", "wd5", "ExtSite", "/Senior-Engineer/JR001")
        assert result == "https://nvidia.wd5.myworkdayjobs.com/ExtSite/Senior-Engineer/JR001"


class TestParseLocationtype:
    def test_remote(self):
        assert _parse_location_type("Remote") == "remote"

    def test_flexible(self):
        assert _parse_location_type("Flexible") == "hybrid"

    def test_hybrid(self):
        assert _parse_location_type("Hybrid") == "hybrid"

    def test_none(self):
        assert _parse_location_type(None) is None

    def test_onsite(self):
        assert _parse_location_type("On-Site") is None

    def test_case_insensitive(self):
        assert _parse_location_type("REMOTE") == "remote"


class TestNormalizeWorkdayLocation:
    """Test _normalize_workday_location for Workday location formats."""

    # Code format: US-STATE-CITY with building/address after ~
    def test_code_format_with_tilde(self):
        assert (
            _normalize_workday_location("US-AR-SPRINGDALE-BLDG 1 ~ 275 E Robinson Ave ~ BLDG 1")
            == "Springdale, AR, US"
        )

    def test_code_format_with_building(self):
        assert (
            _normalize_workday_location("US-MA-TEWKSBURY-TB1 ~ 50 Apple Hill Dr ~ ASSABET BLDG")
            == "Tewksbury, MA, US"
        )

    def test_code_format_remote(self):
        assert _normalize_workday_location("US-CT-REMOTE") == "Remote, CT, US"

    def test_code_format_au(self):
        assert (
            _normalize_workday_location("AU-NSW-NOWRA-039 ~ 39 Wugan St ~ WUGAN Lot 10 Yerriyong")
            == "Nowra, NSW, AU"
        )

    def test_code_format_gb(self):
        assert _normalize_workday_location("GB-LND-LONDON") == "London, LND, GB"

    # Display format: space-separated without commas
    def test_display_format_double_space(self):
        # Citi returns "Sg  Singapore" (double space)
        assert _normalize_workday_location("Sg  Singapore") == "Sg, Singapore"

    def test_display_format_triple_part(self):
        assert _normalize_workday_location("Heredia  Costa Rica") == "Heredia, Costa Rica"

    # Already comma-separated (pass through)
    def test_already_comma_separated(self):
        assert (
            _normalize_workday_location("New York, NY, United States")
            == "New York, NY, United States"
        )

    # Plain city name (unchanged)
    def test_plain_city(self):
        assert _normalize_workday_location("Singapore") == "Singapore"

    def test_empty_string(self):
        assert _normalize_workday_location("") == ""


class TestParseDetail:
    def test_full_detail(self):
        detail = {
            "jobPostingInfo": {
                "title": "Senior Engineer",
                "externalPath": "/Senior-Engineer/JR001",
                "jobDescription": "<p>Build software</p>",
                "location": "Santa Clara, CA",
                "additionalLocations": ["Austin, TX", "Remote"],
                "timeType": "Full-time",
                "remoteType": "Hybrid",
                "startDate": "2024-01-15",
                "jobReqId": "JR001",
            }
        }
        result = _parse_detail(detail)
        assert result.title == "Senior Engineer"
        assert result.description == "<p>Build software</p>"
        assert result.locations == ["Santa Clara, CA", "Austin, TX", "Remote"]
        assert result.employment_type == "Full-time"
        assert result.job_location_type == "hybrid"
        assert result.date_posted == "2024-01-15"
        assert result.metadata == {"jobReqId": "JR001"}

    def test_missing_job_posting_info(self):
        result = _parse_detail({})
        assert result.title is None

    def test_locations_dedup(self):
        detail = {
            "jobPostingInfo": {
                "location": "NYC",
                "additionalLocations": ["NYC", "LA"],
            }
        }
        result = _parse_detail(detail)
        assert result.locations == ["NYC", "LA"]

    def test_no_locations(self):
        detail = {"jobPostingInfo": {}}
        result = _parse_detail(detail)
        assert result.locations is None

    def test_no_metadata(self):
        detail = {"jobPostingInfo": {}}
        result = _parse_detail(detail)
        assert result.metadata is None


class TestPickSplitFacet:
    def test_picks_facet_with_most_values(self):
        facets = [
            {
                "facetParameter": "category",
                "values": [
                    {"id": "cat1", "count": 500},
                    {"id": "cat2", "count": 300},
                    {"id": "cat3", "count": 200},
                ],
            },
            {
                "facetParameter": "location",
                "values": [
                    {"id": "loc1", "count": 900},
                    {"id": "loc2", "count": 100},
                ],
            },
        ]
        result = _pick_split_facet(facets)
        assert result is not None
        param, ids = result
        assert param == "category"
        assert ids == ["cat1", "cat2", "cat3"]

    def test_skips_facet_with_value_at_cap(self):
        facets = [
            {
                "facetParameter": "category",
                "values": [
                    {"id": "cat1", "count": 2000},  # At cap
                    {"id": "cat2", "count": 100},
                ],
            },
            {
                "facetParameter": "location",
                "values": [
                    {"id": "loc1", "count": 900},
                ],
            },
        ]
        result = _pick_split_facet(facets)
        assert result is not None
        param, ids = result
        assert param == "location"

    def test_no_valid_facets(self):
        facets = [
            {
                "facetParameter": "category",
                "values": [{"id": "cat1", "count": 2000}],
            }
        ]
        assert _pick_split_facet(facets) is None

    def test_empty_facets(self):
        assert _pick_split_facet([]) is None

    def test_facet_without_values(self):
        facets = [{"facetParameter": "category", "values": []}]
        assert _pick_split_facet(facets) is None


class TestDiscover:
    async def test_returns_urls(self):
        def handler(request):
            url = str(request.url)
            if request.method == "POST" and "/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "total": 2,
                        "jobPostings": [
                            {"externalPath": "/Engineer/JR001"},
                            {"externalPath": "/Designer/JR002"},
                        ],
                        "facets": [],
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://nvidia.wd5.myworkdayjobs.com/ExtSite",
                "metadata": {
                    "company": "nvidia",
                    "wd_instance": "wd5",
                    "site": "ExtSite",
                },
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert len(urls) == 2
            assert all(isinstance(u, str) for u in urls)
            assert any("JR001" in u for u in urls)
            assert any("JR002" in u for u in urls)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"total": 0, "jobPostings": [], "facets": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/Site",
                "metadata": {
                    "company": "co",
                    "wd_instance": "wd1",
                    "site": "Site",
                },
            }
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_no_components_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot parse Workday"):
                await discover(board, client)

    async def test_components_from_url(self):
        def handler(request):
            url = str(request.url)
            assert "nvidia" in url
            return httpx.Response(
                200,
                json={"total": 0, "jobPostings": [], "facets": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://nvidia.wd5.myworkdayjobs.com/ExtSite",
                "metadata": {},
            }
            urls = await discover(board, client)
            assert len(urls) == 0


class TestCanHandle:
    async def test_workday_url_match(self):
        result = await can_handle("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
        assert result is not None
        assert result["company"] == "nvidia"
        assert result["wd_instance"] == "wd5"
        assert result["site"] == "NVIDIAExternalCareerSite"

    async def test_non_matching_url(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_url_match_with_client(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"total": 42, "jobPostings": [], "facets": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://nvidia.wd5.myworkdayjobs.com/ExtSite", client)
            assert result is not None
            assert result["jobs"] == 42

    async def test_detects_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "myworkdayjobs.com" in url and "wday/cxs" in url:
                return httpx.Response(
                    200,
                    json={"total": 10, "jobPostings": [], "facets": []},
                )
            # Place the Workday URL at the end of the text so the regex's $ anchor works
            return httpx.Response(
                200,
                text="<html>Apply at https://acme.wd1.myworkdayjobs.com/Careers",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.acme.com/careers", client)
            assert result is not None
            assert result["company"] == "acme"
            assert result["site"] == "Careers"

    async def test_no_match_with_client(self):
        def handler(request):
            return httpx.Response(200, text="<html>no workday</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None


class TestDiscoverSites:
    async def test_parses_robots_txt(self):
        robots = (
            "User-agent: *\n"
            "Sitemap: https://co.wd1.myworkdayjobs.com/SiteA/siteMap.xml\n"
            "Sitemap: https://co.wd1.myworkdayjobs.com/SiteB/siteMap.xml\n"
        )

        def handler(request):
            if "/robots.txt" in str(request.url):
                return httpx.Response(200, text=robots)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sites = await _discover_sites("co", "wd1", client)
            assert sites == ["SiteA", "SiteB"]

    async def test_robots_not_found_returns_empty(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            sites = await _discover_sites("co", "wd1", client)
            assert sites == []


class TestMultiSiteDiscover:
    async def test_aggregates_urls_from_all_sites(self):
        robots = (
            "Sitemap: https://co.wd1.myworkdayjobs.com/SiteA/siteMap.xml\n"
            "Sitemap: https://co.wd1.myworkdayjobs.com/SiteB/siteMap.xml\n"
        )

        def handler(request):
            url = str(request.url)
            if "/robots.txt" in url:
                return httpx.Response(200, text=robots)
            if request.method == "POST" and "SiteA/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "total": 1,
                        "jobPostings": [{"externalPath": "/Eng/JR001"}],
                        "facets": [],
                    },
                )
            if request.method == "POST" and "SiteB/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "total": 1,
                        "jobPostings": [{"externalPath": "/Design/JR002"}],
                        "facets": [],
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/SiteA",
                "metadata": {"company": "co", "wd_instance": "wd1", "site": "SiteA"},
            }
            urls = await discover(board, client)
            assert len(urls) == 2
            assert any("SiteA" in u for u in urls)
            assert any("SiteB" in u for u in urls)

    async def test_all_sites_false_uses_single_site(self):
        def handler(request):
            url = str(request.url)
            if request.method == "POST" and "SiteA/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "total": 1,
                        "jobPostings": [{"externalPath": "/Eng/JR001"}],
                        "facets": [],
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/SiteA",
                "metadata": {
                    "company": "co",
                    "wd_instance": "wd1",
                    "site": "SiteA",
                    "all_sites": False,
                },
            }
            urls = await discover(board, client)
            assert len(urls) == 1
            assert any("JR001" in u for u in urls)


class TestParseJobUrl:
    def test_standard_job_url(self):
        result = _parse_job_url(
            "https://nvidia.wd5.myworkdayjobs.com/ExtSite/job/Senior-Engineer/JR001"
        )
        assert result == ("nvidia", "wd5", "ExtSite", "/job/Senior-Engineer/JR001")

    def test_with_locale_prefix(self):
        result = _parse_job_url("https://nvidia.wd5.myworkdayjobs.com/en-US/ExtSite/job/Eng/JR001")
        assert result == ("nvidia", "wd5", "ExtSite", "/job/Eng/JR001")

    def test_non_matching_url(self):
        assert _parse_job_url("https://example.com/careers/123") is None

    def test_board_url_without_job_path(self):
        assert _parse_job_url("https://nvidia.wd5.myworkdayjobs.com/ExtSite") is None


class TestScrape:
    async def test_fetches_detail(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "title": "Engineer",
                        "jobDescription": "<p>Build</p>",
                        "location": "NYC",
                        "timeType": "Full-time",
                        "remoteType": "Remote",
                        "startDate": "2024-06-01",
                        "jobReqId": "JR001",
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://nvidia.wd5.myworkdayjobs.com/ExtSite/job/Eng/JR001",
                {},
                client,
            )
            assert result.title == "Engineer"
            assert result.description == "<p>Build</p>"
            assert result.locations == ["NYC"]
            assert result.employment_type == "Full-time"
            assert result.job_location_type == "remote"

    async def test_unparseable_url_returns_empty(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape("https://example.com/job/123", {}, client)
            assert result.title is None

    async def test_404_returns_empty(self):
        """Posting removed between list + detail fetches — soft-fail."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape(
                "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                {},
                client,
            )
            assert result.title is None

    async def test_403_raises(self):
        """Bare 403 (real WAF block / auth failure) surfaces as error so it's retried."""
        transport = httpx.MockTransport(lambda r: httpx.Response(403))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape(
                    "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                    {},
                    client,
                )

    async def test_403_s22_returns_empty(self):
        """Workday's 'closed requisition' response: 403 + {errorCode: S22}.

        Verified 2026-04-19 against 15 consecutive 403 URLs from Loki —
        0/15 were in the current LIST output. Treat as soft-fail (same as
        the documented 404) so delisted jobs drain from the scrape queue
        without flooding batch.scrape.error.
        """

        def handler(request):
            return httpx.Response(
                403,
                json={
                    "errorCode": "S22",
                    "errorCaseId": "test-case",
                    "httpStatus": 403,
                    "message": "permission denied",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/x/JR001",
                {},
                client,
            )
            assert result.title is None  # empty JobContent, no exception

    async def test_403_other_code_raises(self):
        """403 with a different errorCode shape is NOT treated as gone —
        it could be a real auth failure or rate limit, so let it surface."""

        def handler(request):
            return httpx.Response(403, json={"errorCode": "OTHER", "message": "rate limited"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape(
                    "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                    {},
                    client,
                )

    async def test_403_non_json_body_raises(self):
        """403 with a non-JSON body (HTML WAF page) still raises."""
        transport = httpx.MockTransport(lambda r: httpx.Response(403, text="<html>blocked</html>"))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape(
                    "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                    {},
                    client,
                )

    async def test_500_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape(
                    "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                    {},
                    client,
                )


# ---------------------------------------------------------------------------
# Pagination retry semantics (#2748)
# ---------------------------------------------------------------------------


_LIST_URL = "https://co.wd1.myworkdayjobs.com/wday/cxs/co/Site/jobs"


class TestPostPageWithRetry:
    """``_post_page_with_retry`` mirrors ``fetch_with_retry``'s contract on
    Workday's POST list endpoint: 5xx / 408 / 425 / 429 / network errors
    are retried, non-retryable 4xx fail fast, and persistent failures
    raise :class:`PaginationFetchError` so a single broken pagination
    page doesn't silently truncate the run (#2748).
    """

    async def test_returns_on_success(self):
        def handler(request):
            return httpx.Response(200, json={"total": 0, "jobPostings": [], "facets": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _post_page_with_retry(client, _LIST_URL, {"limit": 20, "offset": 0})
            assert data == {"total": 0, "jobPostings": [], "facets": []}

    async def test_retries_on_429_then_succeeds(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(
                200, json={"total": 1, "jobPostings": [{"externalPath": "/x"}], "facets": []}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _post_page_with_retry(
                client, _LIST_URL, {"limit": 20, "offset": 0}, base_delay=0.001
            )
            assert data["jobPostings"] == [{"externalPath": "/x"}]
            assert calls["n"] == 3

    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        """Issue #2748's load-bearing case: pre-fix, a non-429 retryable
        status (503, etc.) was not retried — ``raise_for_status`` raised
        out and the run was recorded as a scrape-level failure rather
        than retried. Now 503 is retried like every other transient.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503, text="service unavailable")
            return httpx.Response(
                200, json={"total": 1, "jobPostings": [{"externalPath": "/x"}], "facets": []}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _post_page_with_retry(
                client, _LIST_URL, {"limit": 20, "offset": 0}, base_delay=0.001
            )
            assert data["jobPostings"] == [{"externalPath": "/x"}]
            assert calls["n"] == 3

    async def test_retries_on_cloudflare_5xx(self, monkeypatch):
        """Cloudflare origin codes 520-526/530 are retried (parity with
        dom + accenture + PCSX). Pinned for one representative code; the
        full set is exercised by ``test_http_retry``."""
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(520, text="cf origin error")
            return httpx.Response(200, json={"total": 0, "jobPostings": [], "facets": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _post_page_with_retry(
                client, _LIST_URL, {"limit": 20, "offset": 0}, base_delay=0.001
            )
            assert data == {"total": 0, "jobPostings": [], "facets": []}
            assert calls["n"] == 2

    async def test_raises_after_persistent_5xx(self, monkeypatch):
        """Issue #2748 acceptance: persistent 5xx exhausts the retry budget
        and raises ``PaginationFetchError`` — no silent truncation.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(500, text="internal")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _post_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 20, "offset": 0},
                    retries=3,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status == 500
            assert exc_info.value.attempts == 3
            assert calls["n"] == 3

    async def test_raises_on_non_retryable_4xx_immediately(self, monkeypatch):
        """A 401 / 403 / 400 indicates a hard error — no point retrying.
        Raise ``PaginationFetchError`` on the first attempt."""
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(401, text="unauthorized")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _post_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 20, "offset": 0},
                    retries=3,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status == 401
            # Exactly one attempt — no retry on non-retryable 4xx.
            assert calls["n"] == 1

    async def test_raises_after_persistent_network_error(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            raise httpx.ConnectError("conn refused")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _post_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 20, "offset": 0},
                    retries=2,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status is None
            assert exc_info.value.last_error == "ConnectError"

    async def test_raises_on_empty_200_body(self, monkeypatch):
        """Per the issue, a 200 with a body that decodes to ``null`` (or
        any non-dict shape) used to leave ``data is None`` and silently
        ``break`` the pagination loop. Now the helper treats it as a
        transient failure (retry, then raise) so the run surfaces.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            # JSON ``null`` decodes to Python ``None``.
            return httpx.Response(
                200, content=b"null", headers={"content-type": "application/json"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await _post_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 20, "offset": 0},
                    retries=2,
                    base_delay=0.001,
                )


class TestPaginateQueryRetry:
    """Issue #2748 acceptance: the inner pagination loop propagates the
    new retry-then-raise contract end-to-end. Pre-fix, a 5xx on page N>0
    raised ``HTTPStatusError`` straight out of the page fetch — caller
    treated it as a scrape-level failure but ``data is None`` could also
    silently break the loop on any future change. Now both transients
    are retried and persistent failures raise ``PaginationFetchError``.
    """

    async def test_503_then_200_pagination_continues(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        # Total 30 (PAGE_SIZE=20 → two pages). First page succeeds with
        # 20 postings; second page returns 503 once then 200 with 10.
        page2_calls = {"n": 0}

        def handler(request):
            body = request.read().decode()
            offset = 0
            if '"offset": 20' in body or '"offset":20' in body:
                offset = 20
            if offset == 0:
                return httpx.Response(
                    200,
                    json={
                        "total": 30,
                        "jobPostings": [{"externalPath": f"/job/{i}"} for i in range(PAGE_SIZE)],
                        "facets": [],
                    },
                )
            page2_calls["n"] += 1
            if page2_calls["n"] < 2:
                return httpx.Response(503, text="unavailable")
            return httpx.Response(
                200,
                json={
                    "total": 30,
                    "jobPostings": [{"externalPath": f"/job/{20 + i}"} for i in range(10)],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            paths, total, _ = await _paginate_query(_LIST_URL, {}, client)
            assert total == 30
            assert len(paths) == 30
            # Page 2 was retried once before succeeding.
            assert page2_calls["n"] == 2

    async def test_persistent_500_raises_not_silent_break(self, monkeypatch):
        """Pre-fix, ``data is None`` after the retry loop hit a silent
        ``break`` and returned the partial ``paths`` list. Now the helper
        raises ``PaginationFetchError`` instead.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            body = request.read().decode()
            if '"offset": 20' in body or '"offset":20' in body:
                return httpx.Response(500, text="internal")
            return httpx.Response(
                200,
                json={
                    "total": 30,
                    "jobPostings": [{"externalPath": f"/job/{i}"} for i in range(PAGE_SIZE)],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _paginate_query(_LIST_URL, {}, client)
            assert exc_info.value.last_status == 500

    async def test_persistent_connection_error_raises(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            body = request.read().decode()
            if '"offset": 20' in body or '"offset":20' in body:
                raise httpx.ConnectError("conn reset")
            return httpx.Response(
                200,
                json={
                    "total": 30,
                    "jobPostings": [{"externalPath": f"/job/{i}"} for i in range(PAGE_SIZE)],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _paginate_query(_LIST_URL, {}, client)
            assert exc_info.value.last_error == "ConnectError"

    async def test_empty_200_body_raises(self, monkeypatch):
        """Per the issue: ``data is None`` from a 200 with a ``null`` /
        non-dict body must raise rather than silently break the loop.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            body = request.read().decode()
            if '"offset": 20' in body or '"offset":20' in body:
                return httpx.Response(
                    200, content=b"null", headers={"content-type": "application/json"}
                )
            return httpx.Response(
                200,
                json={
                    "total": 30,
                    "jobPostings": [{"externalPath": f"/job/{i}"} for i in range(PAGE_SIZE)],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await _paginate_query(_LIST_URL, {}, client)
