from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, api_monitor_types, paycom
from src.core.monitors.paycom import (
    _extract_bootstrap,
    _token_from_url,
    can_handle,
    discover,
)
from src.core.scrapers import _REGISTRY as scraper_registry
from src.core.scrapers.paycom import can_handle as scraper_can_handle
from src.core.scrapers.paycom import scrape as paycom_scrape
from src.redis_queue import _KNOWN_ATS_DOMAINS
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type, auto_skip_crawler_types, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS, _SCRAPER_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS, SCRAPER_CARDS

TOKEN = "0123456789abcdef0123456789abcdef"
PORTAL_URL = f"https://www.paycomonline.net/v4/ats/web.php/portal/{TOKEN}/career-page"
SERVICE_URL = "https://portal-applicant-tracking.us-cent.paycomonline.net"
SEARCH_URL = f"{SERVICE_URL}/api/ats/job-posting-previews/search"


def _bootstrap_page(*, service_url: str = SERVICE_URL, jwt: str = "a.b.c") -> str:
    config = {
        "sessionJWT": jwt,
        "libConfig": json.dumps(
            {
                "atsPortalMantleServiceUrl": service_url,
                "locale": "en-US",
                "translationHighlights": False,
            }
        ),
    }
    return (
        "<html><script>var configsFromHost = "
        + json.dumps(config)
        + "; var Mountable = {};</script></html>"
    )


def _preview(job_id: object, title: str = "Platform Engineer") -> dict:
    return {
        "jobId": job_id,
        "jobTitle": title,
        "locations": "Denver, CO",
        "remoteType": "Hybrid",
        "positionType": "Full Time",
        "postedOn": "2026-08-01",
        "description": "<p>Build reliable systems.</p>",
        "isHotJob": True,
    }


def _search(rows: list[object], *, count: int | None = None) -> dict:
    return {
        "jobPostingPreviews": rows,
        "jobPostingPreviewsCount": len(rows) if count is None else count,
    }


class TestTokenAndBootstrap:
    def test_extracts_portal_token_from_listing_and_job_urls(self):
        assert _token_from_url(PORTAL_URL) == TOKEN
        assert (
            _token_from_url(
                f"https://www.paycomonline.net/v4/ats/web.php/portal/{TOKEN}/jobs/123?jpt=public"
            )
            == TOKEN
        )

    @pytest.mark.parametrize(
        "url",
        [
            f"http://www.paycomonline.net/v4/ats/web.php/portal/{TOKEN}/career-page",
            f"https://evil.example/v4/ats/web.php/portal/{TOKEN}/career-page",
            f"https://www.paycomonline.net.evil.test/v4/ats/web.php/portal/{TOKEN}/career-page",
            "https://www.paycomonline.net/v4/ats/web.php/portal/short/career-page",
            f"https://user@www.paycomonline.net/v4/ats/web.php/portal/{TOKEN}/career-page",
        ],
    )
    def test_rejects_untrusted_or_malformed_urls(self, url: str):
        assert _token_from_url(url) is None

    def test_extracts_and_validates_bootstrap(self):
        bootstrap = _extract_bootstrap(_bootstrap_page(), PORTAL_URL)
        assert bootstrap.service_url == SERVICE_URL
        assert bootstrap.headers == {
            "Accept": "application/json",
            "Authorization": "a.b.c",
            "Locale": "en-US",
            "Translation-Highlights": "false",
            "Portal-Host-Referrer": PORTAL_URL,
        }

    @pytest.mark.parametrize(
        "page",
        [
            "<html>No config</html>",
            "<script>var configsFromHost = nope; var Mountable</script>",
            '<script>var configsFromHost = {"sessionJWT":"bad"}; var Mountable</script>',
        ],
    )
    def test_rejects_malformed_bootstrap(self, page: str):
        with pytest.raises(ValueError):
            _extract_bootstrap(page, PORTAL_URL)

    @pytest.mark.parametrize(
        "service_url",
        [
            "http://portal-applicant-tracking.us-cent.paycomonline.net",
            "https://evil.example",
            "https://paycomonline.net.evil.test",
            "https://user@portal.paycomonline.net",
            "https://portal.paycomonline.net?internal=true",
        ],
    )
    def test_rejects_untrusted_bootstrap_service(self, service_url: str):
        with pytest.raises(ValueError, match="untrusted"):
            _extract_bootstrap(_bootstrap_page(service_url=service_url), PORTAL_URL)


class TestMonitor:
    async def test_discovers_rich_summaries_with_bounded_pagination(self):
        requests: list[tuple[str, str, dict | None]] = []
        first = [_preview(index) for index in range(1, 101)]
        final = [_preview(101, "Site Reliability Engineer")]

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            requests.append((request.method, str(request.url), body))
            if str(request.url) == PORTAL_URL:
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            assert request.headers["authorization"] == "a.b.c"
            assert request.headers["portal-host-referrer"] == PORTAL_URL
            assert body is not None
            rows = first if body["skip"] == 0 else final
            return httpx.Response(200, json=_search(rows, count=101), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": PORTAL_URL}, client)

        assert isinstance(result, list)
        assert len(result) == 101
        assert requests[0] == ("GET", PORTAL_URL, None)
        assert requests[1][2]["take"] == 100
        assert requests[1][2]["skip"] == 0
        assert requests[2][2]["skip"] == 100
        job = result[-1]
        assert job.url.endswith(f"/{TOKEN}/jobs/101")
        assert job.title == "Site Reliability Engineer"
        assert job.description == "<p>Build reliable systems.</p>"
        assert job.locations == ["Denver, CO"]
        assert job.employment_type == "Full Time"
        assert job.job_location_type == "hybrid"
        assert job.date_posted == "2026-08-01"
        assert job.metadata == {"job_id": 101, "is_hot_job": True}

    async def test_metadata_token_override(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            return httpx.Response(200, json=_search([]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://example.com/jobs", "metadata": {"token": TOKEN}},
                client,
            )

        assert result == []
        assert seen == [PORTAL_URL, SEARCH_URL]

    async def test_empty_board_is_authoritative(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            return httpx.Response(200, json=_search([]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": PORTAL_URL}, client) == []

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_status_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": PORTAL_URL}, client)

    async def test_unavailable_marker_is_board_gone(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="Job board does not exist or is unavailable at this time.",
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": PORTAL_URL}, client)

    async def test_search_retries_transient_status(self):
        search_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal search_calls
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            search_calls += 1
            if search_calls == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(200, json=_search([_preview(1)]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": PORTAL_URL}, client)
        assert isinstance(result, list)
        assert len(result) == 1
        assert search_calls == 2

    async def test_premature_empty_page_gets_one_semantic_retry(self):
        search_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal search_calls
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            search_calls += 1
            rows = [] if search_calls == 1 else [_preview(1)]
            return httpx.Response(200, json=_search(rows, count=1), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": PORTAL_URL}, client)
        assert isinstance(result, list)
        assert len(result) == 1
        assert search_calls == 2

    async def test_persistent_premature_empty_page_fails(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            return httpx.Response(200, json=_search([], count=1), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": PORTAL_URL}, client)
        assert exc_info.value.last_status == 200

    async def test_count_change_fails_without_partial_success(self):
        first = [_preview(index) for index in range(1, 101)]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            body = json.loads(request.content)
            if body["skip"] == 0:
                return httpx.Response(200, json=_search(first, count=101), request=request)
            return httpx.Response(200, json=_search([_preview(101)], count=102), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="count changed"):
                await discover({"board_url": PORTAL_URL}, client)

    @pytest.mark.parametrize(
        "bad_row",
        [None, {"jobId": True}, {"jobId": 1.5}, {"jobId": "not-numeric"}],
    )
    async def test_partially_invalid_rows_suppress_tombstoning(self, bad_row: object):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            return httpx.Response(
                200,
                json=_search([_preview(1), bad_row]),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": PORTAL_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {f"https://www.paycomonline.net/v4/ats/web.php/portal/{TOKEN}/jobs/1"}

    async def test_duplicate_ids_suppress_tombstoning(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            return httpx.Response(
                200,
                json=_search([_preview(1), _preview(1)]),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": PORTAL_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True

    async def test_cap_returns_truncated_result(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(paycom, "MAX_JOBS", 2)
        monkeypatch.setattr(paycom, "MAX_PAGES", 1)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            return httpx.Response(
                200,
                json=_search([_preview(1), _preview(2)], count=3),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": PORTAL_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 2


class TestDetection:
    async def test_direct_url_detects_without_client(self):
        assert await can_handle(PORTAL_URL) == {"token": TOKEN}

    async def test_direct_url_verifies_job_count(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            body = json.loads(request.content)
            assert body["take"] == 1
            return httpx.Response(200, json=_search([_preview(1)], count=45), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle(PORTAL_URL, client) == {"token": TOKEN, "jobs": 45}

    async def test_embedded_link_is_detected_without_guessing(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.host == "example.com":
                return httpx.Response(
                    200,
                    text=f'<a href="{PORTAL_URL}">Open roles</a>',
                    request=request,
                )
            if request.method == "GET":
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            return httpx.Response(200, json=_search([], count=0), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)

        assert result == {"token": TOKEN, "jobs": 0}
        assert requested == ["https://example.com/careers", PORTAL_URL, SEARCH_URL]

    async def test_does_not_blind_probe_company_slug(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="<html>No ATS link</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) is None
        assert requested == ["https://example.com/careers"]


class TestScraper:
    async def test_fetches_full_detail_through_shared_bootstrap(self):
        detail = {
            "jobPosting": {
                "jobId": 466263,
                "clientCode": "0TS15",
                "jobTitle": "Grounds Maintenance Staff",
                "location": "Estes Park, CO",
                "secondaryLocations": ["Granby, CO"],
                "remoteType": "On-site",
                "positionType": "Seasonal Jobs",
                "description": "<p>Maintain landscaped areas.</p>",
                "qualifications": "<ul><li>Driver license.</li></ul>",
                "salaryRange": "$15.16 - $18.50 Hourly",
                "jobCategory": "Buildings and Grounds",
                "jobShift": "Day",
                "isHotJob": True,
                "googleJobJson": json.dumps(
                    {"title": "Grounds Maintenance Staff", "datePosted": "2026-07-20"}
                ),
            }
        }
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.method == "GET" and str(request.url) == PORTAL_URL:
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            assert request.headers["authorization"] == "a.b.c"
            return httpx.Response(200, json=detail, request=request)

        job_url = PORTAL_URL.replace("career-page", "jobs/466263")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await paycom_scrape(job_url, {}, client)

        assert requests == [PORTAL_URL, f"{SERVICE_URL}/api/ats/job-postings/466263"]
        assert result.title == "Grounds Maintenance Staff"
        assert result.description == "<p>Maintain landscaped areas.</p>"
        assert result.locations == ["Estes Park, CO", "Granby, CO"]
        assert result.employment_type == "Seasonal Jobs"
        assert result.job_location_type == "onsite"
        assert result.date_posted == "2026-07-20"
        assert result.base_salary is not None
        assert result.extras == {"qualifications": "<ul><li>Driver license.</li></ul>"}
        assert result.metadata is not None
        assert result.metadata["ats_job_id"] == 466263
        assert result.metadata["department"] == "Buildings and Grounds"

    async def test_closed_detail_returns_empty_content(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == PORTAL_URL:
                return httpx.Response(200, text=_bootstrap_page(), request=request)
            return httpx.Response(404, request=request)

        job_url = PORTAL_URL.replace("career-page", "jobs/466263")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await paycom_scrape(job_url, {}, client)
        assert result.title is None
        assert result.description is None

    async def test_scraper_detection_requires_canonical_job_url(self):
        job_url = PORTAL_URL.replace("career-page", "jobs/466263")
        assert await scraper_can_handle(job_url) == {}
        assert await scraper_can_handle(f"{job_url}/") == {}
        assert await scraper_can_handle(f"{job_url}?jpt=public") == {}
        assert await scraper_can_handle(PORTAL_URL) is None
        assert await scraper_can_handle("https://evil.example/jobs/466263") is None
        assert await scraper_can_handle(f"{PORTAL_URL}?next=/jobs/466263") is None
        assert await scraper_can_handle(f"{PORTAL_URL}/jobs/466263") is None


def test_workspace_and_runtime_integration():
    assert "paycom" in all_monitor_types()
    assert "paycom" in api_monitor_types()
    assert "paycom" not in auto_skip_crawler_types()
    assert "paycom" in scraper_registry
    assert "paycom" in _KNOWN_ATS_DOMAINS
    assert detect_ats_from_url(PORTAL_URL) == "paycom"
    assert detect_ats_from_url("https://www.paycomonline.net/v4/ats/web.php/login") is None

    scraper = auto_scraper_type("paycom")
    assert scraper is not None
    scraper_type, scraper_config = scraper
    assert scraper_type == "paycom"
    assert scraper_config is not None
    assert "description" in scraper_config["enrich"]
    assert "paycom" in MONITOR_CARDS
    assert "paycom" in SCRAPER_CARDS
    assert "paycom" in _MONITOR_CONFIG_HINTS
    assert "paycom" not in _SCRAPER_CONFIG_HINTS


def test_career_discovery_finds_paycom_link():
    html = f'<a href="{PORTAL_URL}">Open roles</a>'
    candidates = _scan_ats_urls_in_html(html)
    assert [candidate.url for candidate in candidates] == [PORTAL_URL]
