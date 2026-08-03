from __future__ import annotations

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, api_monitor_types
from src.core.monitors.bamboohr import (
    _parse_listing,
    _tenant_from_url,
    can_handle,
    discover,
)
from src.core.scrapers import _REGISTRY as scraper_registry
from src.core.scrapers.api_sniffer import scrape as api_sniffer_scrape
from src.redis_queue import _KNOWN_ATS_DOMAINS
from src.workspace._compat import auto_scraper_type, auto_skip_crawler_types, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS, _SCRAPER_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS, SCRAPER_CARDS

BOARD_URL = "https://acme.bamboohr.com/careers"


def _payload(jobs: list[object], *, total: int | None = None) -> dict:
    return {
        "meta": {"totalCount": len(jobs) if total is None else total},
        "result": jobs,
    }


JOBS = [
    {
        "id": "347",
        "jobOpeningName": "Enterprise Account Executive",
        "departmentId": "19099",
        "departmentLabel": "Sales",
        "employmentStatusLabel": "Full-Time",
        "location": {"city": None, "state": None},
        "atsLocation": {
            "country": "Germany",
            "province": "Upper Bavaria",
            "city": "Munich",
        },
        "isRemote": None,
        "locationType": "1",
    },
    {
        "id": 348,
        "jobOpeningName": "Platform Engineer",
        "employmentStatusLabel": "Part-Time",
        "location": {"city": "Zurich", "state": "Zurich", "addressCountry": "Switzerland"},
        "atsLocation": {},
        "isRemote": True,
        "locationType": "2",
    },
]

DETAIL = {
    "meta": {},
    "result": {
        "formFields": [],
        "jobOpening": {
            "jobOpeningName": "Enterprise Account Executive",
            "departmentId": "19099",
            "departmentLabel": "Sales",
            "employmentStatusLabel": "Full-Time",
            "location": {"city": None, "state": None, "addressCountry": None},
            "atsLocation": {"city": "Munich", "state": "Bavaria", "country": "Germany"},
            "description": "<p>Own enterprise sales across the DACH region.</p>",
            "datePosted": "2026-04-08",
            "minimumExperience": "Experienced",
            "locationType": "1",
        },
    },
}


class TestTenantDetection:
    def test_extracts_direct_tenant(self):
        assert _tenant_from_url(BOARD_URL) == "acme"
        assert _tenant_from_url("https://acme.bamboohr.com/jobs/embed2.php") == "acme"

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.bamboohr.com/careers",
            "https://app.bamboohr.com/careers",
            "https://bamboohr.com/careers",
            "https://example.com/careers",
            "https://acme.bamboohr.com.evil.test/careers",
            "https://acme.bamboohr.com/login.php",
            "https://acme.bamboohr.com/careers/347",
        ],
    )
    def test_rejects_non_tenant_hosts(self, url: str):
        assert _tenant_from_url(url) is None

    async def test_direct_url_detects_without_client(self):
        assert await can_handle(BOARD_URL) == {"tenant": "acme"}

    async def test_empty_direct_board_is_detected(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=_payload([]), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) == {"tenant": "acme", "jobs": 0}

    async def test_embedded_board_link_is_detected_without_slug_guessing(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.host == "example.com":
                return httpx.Response(
                    200,
                    text='<a href="https://acme.bamboohr.com/careers">Jobs</a>',
                    request=request,
                )
            return httpx.Response(200, json=_payload(JOBS), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/work-with-us", client)

        assert result == {"tenant": "acme", "jobs": 2}
        assert requested == [
            "https://example.com/work-with-us",
            "https://acme.bamboohr.com/careers/list",
        ]

    async def test_does_not_blind_probe_company_slug(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="<html>No ATS link</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) is None

        assert requested == ["https://example.com/careers"]


class TestMonitor:
    async def test_discovers_rich_summaries_in_one_request(self):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, json=_payload(JOBS), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert isinstance(result, list)
        assert requests == ["https://acme.bamboohr.com/careers/list"]
        assert [job.url for job in result] == [
            "https://acme.bamboohr.com/careers/347",
            "https://acme.bamboohr.com/careers/348",
        ]
        assert result[0].title == "Enterprise Account Executive"
        assert result[0].locations == ["Munich, Upper Bavaria, Germany"]
        assert result[0].employment_type == "Full-Time"
        assert result[0].job_location_type == "remote"
        assert result[0].metadata == {
            "job_id": "347",
            "department": "Sales",
            "department_id": "19099",
        }
        assert result[1].locations == ["Zurich, Switzerland"]
        assert result[1].job_location_type == "hybrid"

    async def test_metadata_tenant_override(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json=_payload([]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://example.com/jobs", "metadata": {"tenant": "acme"}},
                client,
            )

        assert result == []
        assert seen == ["https://acme.bamboohr.com/careers/list"]

    async def test_empty_board_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=_payload([]), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == []

    @pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 404, 410])
    async def test_terminal_board_is_gone(self, status: int):
        def handler(request: httpx.Request) -> httpx.Response:
            headers = {"location": "https://www.bamboohr.com/"} if status < 400 else {}
            return httpx.Response(status, json={}, headers=headers, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError, match="no longer exists"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_transient_failure_propagates(self, monkeypatch: pytest.MonkeyPatch):
        from src.core.monitors import bamboohr
        from src.shared.http_retry import PaginationFetchError

        async def fail(_tenant: str, _client: httpx.AsyncClient):
            raise PaginationFetchError(
                "https://acme.bamboohr.com/careers/list",
                3,
                last_status=503,
            )

        monkeypatch.setattr(bamboohr, "_fetch_listing", fail)
        async with httpx.AsyncClient() as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == 503

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"result": {}},
            {"result": []},
            {"meta": {"totalCount": True}, "result": []},
            {"result": [None, "bad"]},
        ],
    )
    async def test_malformed_payload_fails_loud(self, payload: dict):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await discover({"board_url": BOARD_URL}, client)

    def test_declared_partial_result_is_truncated(self):
        jobs, truncated = _parse_listing(_payload(JOBS[:1], total=20), "acme")
        assert len(jobs) == 1
        assert truncated is True

    async def test_declared_partial_result_preserves_no_tombstone_signal(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=_payload(JOBS[:1], total=20), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.jobs_by_url is not None
        assert len(result.jobs_by_url) == 1

    def test_deduplicates_stable_job_urls(self):
        jobs, truncated = _parse_listing(_payload([JOBS[0], JOBS[0]]), "acme")
        assert truncated is True
        assert len(jobs) == 1

    async def test_duplicate_ids_suppress_tombstoning(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([JOBS[0], JOBS[0]]),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {"https://acme.bamboohr.com/careers/347"}

    async def test_partially_invalid_listing_suppresses_tombstoning(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([JOBS[0], {"jobOpeningName": "Missing ID"}]),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {"https://acme.bamboohr.com/careers/347"}


class TestScraper:
    async def test_reuses_api_scraper_for_full_detail(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, json=DETAIL, request=request)

        scraper = auto_scraper_type("bamboohr")
        assert scraper is not None
        scraper_type, config = scraper
        assert scraper_type == "api_sniffer"
        assert config is not None
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await api_sniffer_scrape(
                "https://acme.bamboohr.com/careers/347",
                config,
                client,
            )

        assert requested == ["https://acme.bamboohr.com/careers/347/detail"]
        assert result.title == "Enterprise Account Executive"
        assert result.description == "<p>Own enterprise sales across the DACH region.</p>"
        assert result.locations == ["Munich, Bavaria, Germany"]
        assert result.employment_type == "Full-Time"
        assert result.job_location_type == "remote"
        assert result.date_posted == "2026-04-08"
        assert result.metadata == {
            "department": "Sales",
            "department_id": "19099",
            "minimum_experience": "Experienced",
        }


def test_workspace_and_runtime_integration():
    assert "bamboohr" in all_monitor_types()
    assert "bamboohr" in api_monitor_types()
    assert "bamboohr" not in auto_skip_crawler_types()
    assert "bamboohr" not in scraper_registry
    assert "bamboohr" in _KNOWN_ATS_DOMAINS
    assert detect_ats_from_url(BOARD_URL) == "bamboohr"
    assert detect_ats_from_url("https://www.bamboohr.com/careers") is None
    assert detect_ats_from_url("https://acme.bamboohr.com/login.php") is None
    scraper = auto_scraper_type("bamboohr")
    assert scraper is not None
    scraper_type, scraper_config = scraper
    assert scraper_type == "api_sniffer"
    assert scraper_config is not None
    assert scraper_config["api_url"] == "https://{tenant}.bamboohr.com/careers/{id}/detail"
    assert scraper_config["json_path"] == "result.jobOpening"
    assert scraper_config["fields"]["description"] == "description"
    assert "description" in scraper_config["enrich"]
    assert "bamboohr" in MONITOR_CARDS
    assert "bamboohr" not in SCRAPER_CARDS
    assert "bamboohr" in _MONITOR_CONFIG_HINTS
    assert "bamboohr" not in _SCRAPER_CONFIG_HINTS


def test_career_discovery_finds_bamboohr_link():
    html = '<a href="https://acme.bamboohr.com/careers">Open roles</a>'
    candidates = _scan_ats_urls_in_html(html)
    assert [candidate.url for candidate in candidates] == [BOARD_URL]
