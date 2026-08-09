from __future__ import annotations

import json

import httpx
import pytest

import src.core.monitors.keka as keka_monitor
from src.core.monitors import BoardGoneError, all_monitor_types, api_monitor_types
from src.core.monitors.keka import can_handle, discover
from src.probe_boards import probe_row
from src.redis_queue import delay_for_domain
from src.shared.http_retry import PaginationFetchError
from src.shared.keka import (
    KekaBoard,
    extract_keka_identifier,
    is_keka_forbidden_redirect,
    keka_board_from_metadata,
    keka_board_from_url,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.help import MONITOR_CARDS

TENANT = "acme"
PORTAL = "default"
IDENTIFIER = "11111111-1111-4111-8111-111111111111"
OTHER_IDENTIFIER = "22222222-2222-4222-8222-222222222222"
LISTING_URL = f"https://{TENANT}.keka.com/careers"
CUSTOM_URL = f"https://{TENANT}.keka.com/careers/engineering"
JOBS_URL = f"{LISTING_URL}/api/embedjobs/default/active/{IDENTIFIER}"


def _bootstrap(identifier: str = IDENTIFIER) -> str:
    return (
        "<html><script>fetch('/ats/documents/"
        f"{identifier}/careerportal/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.html')"
        "</script></html>"
    )


def _job(job_id: int = 123, **overrides: object) -> dict:
    row: dict[str, object] = {
        "id": job_id,
        "title": " Platform Engineer ",
        "description": '<h2 style="color:red">Build</h2><script>bad()</script><p>Systems</p>',
        "departmentIdentifier": "dept-uuid",
        "departmentName": "Engineering",
        "experience": "3 - 5 years",
        "jobNumber": "ENG-123",
        "jobLocations": [
            {
                "id": 1,
                "name": "HQ",
                "city": "Bengaluru",
                "state": "KA",
                "countryCode": "IN",
                "countryName": "India",
            }
        ],
        "jobType": 2,
        "publishedOn": "2026-08-01T12:30:00Z",
        "salaryRange": {
            "minimum": 100000,
            "maximum": 150000,
            "currency": "INR",
            "salaryPeriod": 4,
        },
        "skillNames": ["Python", "Python", "PostgreSQL"],
    }
    row.update(overrides)
    return row


def _config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "tenant": TENANT,
        "portal": PORTAL,
        "identifier": IDENTIFIER,
    }
    config.update(overrides)
    return config


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "company_slug": "acme",
        "board_slug": "acme-keka",
        "board_url": LISTING_URL,
        "monitor_type": "keka",
        "monitor_config": json.dumps(_config()),
        "scraper_type": "skip",
        "scraper_config": "",
    }
    row.update(overrides)
    return row


class TestIdentity:
    @pytest.mark.parametrize(
        "url, expected",
        [
            (LISTING_URL, KekaBoard(TENANT)),
            (f"{LISTING_URL}/", KekaBoard(TENANT)),
            (CUSTOM_URL, KekaBoard(TENANT, "engineering")),
            (f"{LISTING_URL}/jobdetails/123", KekaBoard(TENANT)),
            (f"{CUSTOM_URL}/jobdetails/123?source=linkedin", KekaBoard(TENANT, "engineering")),
        ],
    )
    def test_accepts_public_urls(self, url: str, expected: KekaBoard):
        assert keka_board_from_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            f"http://{TENANT}.keka.com/careers",
            f"https://user@{TENANT}.keka.com/careers",
            f"https://{TENANT}.keka.com:444/careers",
            "https://keka.com/careers",
            "https://www.keka.com/careers",
            f"https://{TENANT}.keka.com.evil.test/careers",
            f"https://{TENANT}.keka.com/careers/api",
            f"https://{TENANT}.keka.com/careers/jobdetails/0",
            f"https://{TENANT}.keka.com/careers?source=",
            f"https://{TENANT}.keka.com/careers?source=a&source=b",
        ],
    )
    def test_rejects_untrusted_or_noncanonical_urls(self, url: str):
        assert keka_board_from_url(url) is None

    def test_metadata_and_routes(self):
        board = keka_board_from_metadata(_config(tenant=TENANT.upper()))
        assert board == KekaBoard(TENANT, PORTAL, IDENTIFIER)
        assert board is not None
        assert board.jobs_url() == JOBS_URL
        assert board.job_url(123) == f"{LISTING_URL}/jobdetails/123"

    def test_extracts_only_valid_bootstrap_identity(self):
        assert extract_keka_identifier(_bootstrap()) == IDENTIFIER
        assert extract_keka_identifier("<script>fetch('/ats/documents/nope')</script>") is None

    def test_recognizes_only_exact_forbidden_redirect(self):
        board = KekaBoard(TENANT)
        assert is_keka_forbidden_redirect(board, "/careers/Content/403.html") is True
        assert (
            is_keka_forbidden_redirect(board, "https://evil.test/careers/Content/403.html") is False
        )


class TestMonitor:
    async def test_returns_complete_rich_records(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            assert str(request.url) == JOBS_URL
            assert request.headers["accept"] == "application/json"
            return httpx.Response(200, json=[_job()], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {"board_url": LISTING_URL, "metadata": _config()},
                client,
            )

        assert len(jobs) == 1
        job = jobs[0]
        assert job.url == f"{LISTING_URL}/jobdetails/123"
        assert job.title == "Platform Engineer"
        assert job.description == "<h2>Build</h2><p>Systems</p>"
        assert job.locations == ["Bengaluru, KA, India"]
        assert job.employment_type == "Full Time"
        assert job.date_posted == "2026-08-01"
        assert job.base_salary == {
            "currency": "INR",
            "min": 100000,
            "max": 150000,
            "unit": "year",
        }
        assert job.metadata == {
            "id": 123,
            "department_id": "dept-uuid",
            "department": "Engineering",
            "experience": "3 - 5 years",
            "job_number": "ENG-123",
            "job_type": 2,
        }
        assert job.extras == {"skills": ["Python", "PostgreSQL"]}
        assert requested == [LISTING_URL, JOBS_URL]

    def test_location_uses_office_label_only_without_structured_geography(self):
        assert keka_monitor._locations(
            [
                {"name": "Head Office", "city": "Paris", "state": "Paris", "countryName": "France"},
                {"name": "Remote Hub"},
            ]
        ) == ["Paris, France", "Remote Hub"]

    async def test_custom_portal_uses_named_routes(self):
        custom_jobs_url = f"{LISTING_URL}/api/embedjobs/engineering/active/{IDENTIFIER}"
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            body: str | list[dict]
            body = _bootstrap() if str(request.url) == CUSTOM_URL else [_job()]
            return httpx.Response(
                200,
                text=body if isinstance(body, str) else None,
                json=body if isinstance(body, list) else None,
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": CUSTOM_URL,
                    "metadata": _config(portal="engineering"),
                },
                client,
            )
        assert jobs[0].url == f"{CUSTOM_URL}/jobdetails/123"
        assert requested == [CUSTOM_URL, custom_jobs_url]

    async def test_empty_active_board_is_valid(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            return httpx.Response(200, json=[], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": LISTING_URL}, client) == []

    async def test_empty_jobs_response_is_retried(self):
        jobs_requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal jobs_requests
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            jobs_requests += 1
            if jobs_requests == 1:
                return httpx.Response(200, text="", request=request)
            return httpx.Response(200, json=[_job()], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover({"board_url": LISTING_URL}, client)
        assert len(jobs) == 1
        assert jobs_requests == 2

    async def test_jobs_payload_cap_fails_whole_run(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(keka_monitor, "MAX_JSON_CHARS", 5)

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            return httpx.Response(200, text="[12345]", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="payload exceeded"):
                await discover({"board_url": LISTING_URL}, client)

    @pytest.mark.parametrize("bad", [None, "bad", {}, {"id": 1}, {"id": True, "title": "x"}])
    async def test_malformed_record_fails_whole_run(self, bad: object):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            return httpx.Response(200, json=[_job(), bad], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="job"):
                await discover({"board_url": LISTING_URL}, client)

    async def test_duplicate_ids_fail_whole_run(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            return httpx.Response(200, json=[_job(), _job(title="Other")], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="duplicate"):
                await discover({"board_url": LISTING_URL}, client)

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_listing_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": LISTING_URL}, client)

    async def test_exact_forbidden_redirect_is_board_gone(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"location": "/careers/Content/403.html"},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": LISTING_URL}, client)

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_jobs_endpoint_is_not_board_gone(self, status: int):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            return httpx.Response(status, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": LISTING_URL}, client)
        assert exc_info.value.last_status == status

    async def test_configured_identity_must_match_live_portal(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_bootstrap(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="live portal identity changed"):
                await discover(
                    {"board_url": LISTING_URL, "metadata": _config(identifier=OTHER_IDENTIFIER)},
                    client,
                )

    async def test_configured_portal_must_match_url_without_request(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected request to {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="does not match"):
                await discover(
                    {"board_url": CUSTOM_URL, "metadata": _config()},
                    client,
                )


class TestWsAndOps:
    async def test_direct_detection_without_client(self):
        assert await can_handle(CUSTOM_URL) == {"tenant": TENANT, "portal": "engineering"}

    async def test_verified_direct_detection_returns_identity_and_count(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            return httpx.Response(200, json=[_job(), _job(456)], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(LISTING_URL, client)
        assert result == {
            "tenant": TENANT,
            "portal": PORTAL,
            "identifier": IDENTIFIER,
            "jobs": 2,
        }

    async def test_linked_detection_does_not_guess_tenants(self):
        career_url = "https://example.com/careers"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == career_url:
                return httpx.Response(
                    200, text=f'<a href="{LISTING_URL}">Jobs</a>', request=request
                )
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            return httpx.Response(200, json=[_job()], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(career_url, client)
        assert result is not None and result["tenant"] == TENANT and result["jobs"] == 1

    async def test_probe_boards_verifies_bootstrap_identity(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_bootstrap(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(_row(), client)
        assert result.status == "ok"
        assert result.message == "200 (identity verified)"

    async def test_probe_boards_rejects_identity_change(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_bootstrap(OTHER_IDENTIFIER), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(_row(), client)
        assert result.status == "fail"
        assert result.message == "Keka live portal identity changed"

    def test_registration_detection_discovery_and_throttling(self):
        assert "keka" in all_monitor_types()
        assert "keka" in api_monitor_types()
        assert detect_ats_from_url(CUSTOM_URL) == "keka"
        assert auto_scraper_type("keka", _config()) == ("skip", None)
        found = _scan_ats_urls_in_html(f'<a href="{CUSTOM_URL}">Careers</a>')
        assert any(link.url == CUSTOM_URL for link in found)
        assert delay_for_domain(f"{TENANT}.keka.com") == delay_for_domain("greenhouse")
        assert "keka" in MONITOR_CARDS
