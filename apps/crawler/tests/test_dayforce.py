from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx
import pytest

from src.config import settings
from src.core.monitor import MonitorResult
from src.core.monitors import (
    BoardGoneError,
    all_monitor_types,
    dayforce,
    get_stream_fn,
    monitor_needs_browser,
)
from src.core.monitors.dayforce import can_handle, discover
from src.redis_queue import delay_for_domain
from src.shared.dayforce import (
    DayforceBoard,
    dayforce_board_from_metadata,
    dayforce_board_from_url,
    dayforce_listing_culture_from_url,
    extract_dayforce_site,
    resolve_dayforce_listing_redirect,
)
from src.shared.http_retry import PaginationFetchError
from src.sync import _compute_throttle_key
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import (
    CareerPageCandidate,
    _dedup_candidates,
    _scan_ats_urls_in_html,
)
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

TENANT = "allianthcm"
PORTAL = "Alliant"
BOARD = DayforceBoard(TENANT, PORTAL)
BOARD_URL = BOARD.listing_url()
SEARCH_URL = BOARD.search_url()
JOB_ID = 41467
JOB_URL = BOARD.job_url("en-US", JOB_ID)


def _site_page(
    *,
    tenant: str = TENANT,
    portal: str = PORTAL,
    site_portal: str | None = None,
    culture: object = "en-US",
    cultures: object = None,
    job_board_id: object = 7,
    disabled: object = None,
) -> str:
    if cultures is None:
        cultures = ["en-US", "fr-CA"]
    data = {
        "query": {
            "clientNamespace": tenant,
            "careerSiteXRefCode": portal,
        },
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "mutations": [],
                    "queries": [
                        {
                            "queryKey": [
                                "site-info",
                                {
                                    "clientNamespace": tenant,
                                    "careerSiteXRefCode": portal,
                                    "language": culture,
                                },
                            ],
                            "state": {
                                "data": {
                                    "clientNamespace": tenant,
                                    "jobBoardCode": site_portal or portal.lower(),
                                    "cultureCode": culture,
                                    "jobBoardId": job_board_id,
                                    "isoCultureCodes": cultures,
                                    "isDisabled": disabled,
                                }
                            },
                        }
                    ],
                }
            }
        },
    }
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def _raw_job(
    job_id: object,
    *,
    title: object = "Enrollment Center Representative",
    description: object = "Summary&nbsp;\n\nHelp customers.\nExplain benefits.",
    tenant: object = TENANT,
    job_board_id: object = 7,
    virtual: object = False,
    locations: object = None,
) -> dict:
    return {
        "clientNamespace": tenant,
        "jobBoardId": job_board_id,
        "jobPostingId": job_id,
        "jobReqId": 123,
        "jobTitle": title,
        "jobDescription": description,
        "hasVirtualLocation": virtual,
        "postingStartTimestampUTC": "2026-08-03T10:00:00+00:00",
        "postingExpiryTimestampUTC": "2026-09-03T10:00:00+00:00",
        "isEvergreen": False,
        "postingLocations": locations
        if locations is not None
        else [
            {
                "formattedAddress": "San Antonio, TX, USA",
                "locationId": 627,
                "locationType": 2,
            }
        ],
    }


def _search_payload(rows: list[object], *, total: int | None = None, offset: int = 0) -> dict:
    return {
        "maxCount": len(rows) if total is None else total,
        "offset": offset,
        "count": len(rows),
        "jobPostings": rows,
    }


def _bootstrap_client(page: str | None = None, *, status: int = 200):
    page = page if page is not None else _site_page()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == BOARD_URL
        return httpx.Response(status, text=page, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _install_browser(
    monkeypatch: pytest.MonkeyPatch,
    fetch,
    *,
    page_html: str | None = None,
    navigated: list[str] | None = None,
) -> object:
    from src.shared import browser as browser_module

    fake_page = object()

    @asynccontextmanager
    async def fake_open_page(_pw, _config, use_proxy=False, target_url=None):
        _ = use_proxy
        yield fake_page

    async def fake_safe_content(_page):
        return page_html if page_html is not None else _site_page()

    async def fake_navigate_and_capture_headers(_page, board):
        if navigated is not None:
            navigated.append(board.listing_url())
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-csrf-token": "a" * 64,
        }

    monkeypatch.setattr(browser_module, "open_page", fake_open_page)
    monkeypatch.setattr(browser_module, "safe_content", fake_safe_content)
    monkeypatch.setattr(dayforce, "make_browser_fetcher", lambda _page: fetch)
    monkeypatch.setattr(
        dayforce,
        "_navigate_and_capture_headers",
        fake_navigate_and_capture_headers,
    )
    return object()


class TestBoardIdentity:
    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL,
            BOARD_URL + "/",
            JOB_URL,
            JOB_URL + "/",
            f"https://jobs.dayforcehcm.com/en-GB/{TENANT}/{PORTAL}",
            f"https://JOBS.DAYFORCEHCM.COM:443/{TENANT.upper()}/{PORTAL}",
        ],
    )
    def test_accepts_canonical_listing_and_detail_urls(self, url: str):
        assert dayforce_board_from_url(url) == BOARD

    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL.replace("https://", "http://"),
            BOARD_URL.replace("https://", "https://user@"),
            BOARD_URL.replace("dayforcehcm.com", "dayforcehcm.com:444"),
            BOARD_URL + "?page=2",
            BOARD_URL + "#jobs",
            BOARD_URL.replace("dayforcehcm.com", "dayforcehcm.com.evil.test"),
            "https://jobs.dayforcehcm.com/api/CANDIDATEPORTAL",
            "https://jobs.dayforcehcm.com/acme/bad_portal",
            "https://jobs.dayforcehcm.com/acme//CANDIDATEPORTAL",
            "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/jobs/0",
            "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/jobs/nope",
            "https://jobs.dayforcehcm.com:bad/acme/CANDIDATEPORTAL",
        ],
    )
    def test_rejects_untrusted_or_scoped_urls(self, url: str):
        assert dayforce_board_from_url(url) is None

    def test_metadata_preserves_portal_case(self):
        assert dayforce_board_from_metadata({"tenant": TENANT.upper(), "portal": PORTAL}) == BOARD

    @pytest.mark.parametrize(
        "metadata",
        [
            {"tenant": "api", "portal": PORTAL},
            {"tenant": TENANT, "portal": "bad/portal"},
            {"tenant": TENANT, "portal": "bad_portal"},
            {"tenant": TENANT},
        ],
    )
    def test_invalid_metadata_is_rejected(self, metadata: dict):
        assert dayforce_board_from_metadata(metadata) is None

    def test_localized_listing_redirect_is_strictly_scoped(self):
        target = f"https://jobs.dayforcehcm.com/en-GB/{TENANT}/{PORTAL}"
        assert dayforce_listing_culture_from_url(target) == "en-GB"
        assert (
            resolve_dayforce_listing_redirect(BOARD, BOARD_URL, f"/en-GB/{TENANT}/{PORTAL}")
            == target
        )
        assert (
            resolve_dayforce_listing_redirect(
                BOARD,
                BOARD_URL,
                "https://example.com/en-GB/allianthcm/Alliant",
            )
            is None
        )
        assert (
            resolve_dayforce_listing_redirect(
                BOARD,
                BOARD_URL,
                f"/en-GB/{TENANT}/OtherPortal",
            )
            is None
        )


class TestBootstrapSite:
    def test_extracts_case_insensitive_matching_site(self):
        site = extract_dayforce_site(_site_page(), BOARD)
        assert site.job_board_id == 7
        assert site.culture == "en-US"
        assert site.cultures == ("en-US", "fr-CA")
        assert site.disabled is False

    @pytest.mark.parametrize(
        ("page", "message"),
        [
            ("<html></html>", "omitted valid __NEXT_DATA__"),
            (_site_page(tenant="other"), "does not match"),
            (_site_page(site_portal="OTHER"), "unique matching"),
            (_site_page(culture="bad/culture"), "invalid culture"),
            (_site_page(cultures=[]), "supported cultures"),
            (_site_page(cultures=["en-US", "en-us"]), "invalid supported cultures"),
            (_site_page(job_board_id=True), "invalid job-board ID"),
            (_site_page(disabled="yes"), "invalid disabled state"),
            (_site_page(disabled=1), "invalid disabled state"),
        ],
    )
    def test_rejects_malformed_or_foreign_site_info(self, page: str, message: str):
        with pytest.raises(ValueError, match=message):
            extract_dayforce_site(page, BOARD)


class TestMonitor:
    def test_browser_readiness_requires_valid_public_csrf_token(self):
        assert dayforce._csrf_headers({"X-CSRF-Token": "a" * 64}) == {
            "accept": "application/json",
            "content-type": "application/json",
            "x-csrf-token": "a" * 64,
        }

    @pytest.mark.parametrize("headers", [{}, {"x-csrf-token": "short"}])
    def test_browser_readiness_rejects_missing_or_malformed_csrf_token(self, headers: dict):
        with pytest.raises(ValueError, match="valid CSRF token"):
            dayforce._csrf_headers(headers)

    async def test_maps_complete_job_and_exact_search_request(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls: list[tuple[str, str, dict, str | None]] = []

        async def fetch(method, url, headers, body):
            calls.append((method, url, headers, body))
            return _search_payload(
                [
                    _raw_job(
                        JOB_ID,
                        virtual=True,
                        locations=[
                            {"formattedAddress": "San Antonio, TX, USA"},
                            {"formattedAddress": "San Antonio, TX, USA"},
                            {"formattedAddress": "Toronto, ON, Canada"},
                        ],
                    )
                ]
            )

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            jobs = await discover({"board_url": BOARD_URL}, client, pw=pw)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.url == JOB_URL
        assert job.title == "Enrollment Center Representative"
        assert job.description == "<p>Summary</p>\n<p>Help customers.<br>\nExplain benefits.</p>"
        assert job.locations == ["Virtual", "San Antonio, TX, USA", "Toronto, ON, Canada"]
        assert job.job_location_type == "remote"
        assert job.date_posted == "2026-08-03"
        assert job.language == "en"
        assert job.metadata == {
            "job_posting_id": JOB_ID,
            "job_req_id": 123,
            "evergreen": False,
            "expires_at": "2026-09-03T10:00:00+00:00",
        }
        method, url, headers, body = calls[0]
        assert method == "POST"
        assert url == SEARCH_URL
        assert headers == {
            "accept": "application/json",
            "content-type": "application/json",
            "x-csrf-token": "a" * 64,
        }
        assert json.loads(body or "") == {
            "clientNamespace": TENANT,
            "jobBoardCode": PORTAL,
            "cultureCode": "en-US",
            "distanceUnit": 0,
            "paginationStart": 0,
        }

    async def test_streams_pages_before_fetching_the_next(self, monkeypatch: pytest.MonkeyPatch):
        offsets: list[int] = []

        async def fetch(_method, _url, _headers, body):
            offset = json.loads(body)["paginationStart"]
            offsets.append(offset)
            rows = [_raw_job(i) for i in range(1, 26)] if offset == 0 else [_raw_job(26)]
            return _search_payload(rows, total=26, offset=offset)

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            iterator = dayforce.stream({"board_url": BOARD_URL}, client, pw=pw)
            first = await anext(iterator)
            assert len(first.urls) == 25
            assert offsets == [0]
            second = await anext(iterator)
            assert len(second.urls) == 1
            assert second.truncated is False
            with pytest.raises(StopAsyncIteration):
                await anext(iterator)
        assert offsets == [0, 25]

    async def test_configured_offset_overlap_recovers_unstable_page_boundaries(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        offsets: list[int] = []

        async def fetch(_method, _url, _headers, body):
            offset = json.loads(body)["paginationStart"]
            offsets.append(offset)
            rows = (
                [_raw_job(i) for i in range(1, 26)]
                if offset == 0
                else [
                    _raw_job(25),
                    _raw_job(26),
                ]
            )
            return _search_payload(rows, total=26, offset=offset)

        pw = _install_browser(monkeypatch, fetch)
        board = {
            "board_url": BOARD_URL,
            "metadata": {"offset_overlap": 1},
        }
        async with _bootstrap_client() as client:
            jobs = await discover(board, client, pw=pw)

        assert not isinstance(jobs, MonitorResult)
        assert len(jobs) == 26
        assert offsets == [0, 24]

    async def test_offset_overlap_stays_truncated_when_unique_union_is_incomplete(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        async def fetch(_method, _url, _headers, body):
            offset = json.loads(body)["paginationStart"]
            rows = (
                [_raw_job(i) for i in range(1, 26)] if offset == 0 else [_raw_job(24), _raw_job(25)]
            )
            return _search_payload(rows, total=26, offset=offset)

        pw = _install_browser(monkeypatch, fetch)
        board = {
            "board_url": BOARD_URL,
            "metadata": {"offset_overlap": 1},
        }
        async with _bootstrap_client() as client:
            result = await discover(board, client, pw=pw)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 25

    @pytest.mark.parametrize("offset_overlap", [-1, True, 25, "1"])
    async def test_invalid_offset_overlap_fails_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        offset_overlap: object,
    ):
        async def fetch(_method, _url, _headers, _body):
            raise AssertionError("search should not run")

        pw = _install_browser(monkeypatch, fetch)
        board = {
            "board_url": BOARD_URL,
            "metadata": {"offset_overlap": offset_overlap},
        }
        async with _bootstrap_client() as client:
            with pytest.raises(ValueError, match="offset_overlap"):
                await discover(board, client, pw=pw)

    @pytest.mark.parametrize("status", [429, 503])
    async def test_retries_transient_search_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
    ):
        attempts = 0

        async def fetch(_method, url, _headers, _body):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                request = httpx.Request("POST", url)
                response = httpx.Response(status, request=request, json={"error": "transient"})
                raise httpx.HTTPStatusError(
                    "transient",
                    request=request,
                    response=response,
                )
            return _search_payload([])

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("src.shared.api_sniff.asyncio.sleep", no_sleep)
        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            jobs = await discover({"board_url": BOARD_URL}, client, pw=pw)

        assert jobs == []
        assert attempts == 3

    async def test_does_not_retry_non_transient_search_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        attempts = 0

        async def fetch(_method, url, _headers, _body):
            nonlocal attempts
            attempts += 1
            request = httpx.Request("POST", url)
            response = httpx.Response(400, request=request, json={"error": "invalid"})
            raise httpx.HTTPStatusError("invalid", request=request, response=response)

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client, pw=pw)

        assert attempts == 1
        assert exc_info.value.last_status == 400

    async def test_valid_empty_board(self, monkeypatch: pytest.MonkeyPatch):
        async def fetch(_method, _url, _headers, _body):
            return _search_payload([])

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            assert await discover({"board_url": BOARD_URL}, client, pw=pw) == []

    async def test_trusted_localized_listing_redirect_is_followed_once(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        localized_page = _site_page(culture="en-GB", cultures=["en-GB"])
        requested: list[str] = []
        search_bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if str(request.url) == BOARD_URL:
                return httpx.Response(
                    307,
                    headers={"location": f"/en-GB/{TENANT}/{PORTAL}"},
                    request=request,
                )
            return httpx.Response(200, text=localized_page, request=request)

        async def fetch(_method, _url, _headers, body):
            search_bodies.append(json.loads(body))
            return _search_payload([])

        pw = _install_browser(monkeypatch, fetch, page_html=localized_page)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": BOARD_URL}, client, pw=pw) == []
        assert requested == [BOARD_URL, f"https://jobs.dayforcehcm.com/en-GB/{TENANT}/{PORTAL}"]
        assert search_bodies[0]["cultureCode"] == "en-GB"

    async def test_untrusted_listing_redirect_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                307,
                headers={"location": "https://example.com/en-GB/allianthcm/Alliant"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await discover({"board_url": BOARD_URL}, client, pw=object())

    async def test_requires_browser_before_network(self):
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(RuntimeError, match="requires a Playwright browser"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_metadata_override_supports_noncanonical_stored_url(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        navigated: list[str] = []

        async def fetch(_method, _url, _headers, _body):
            return _search_payload([])

        pw = _install_browser(monkeypatch, fetch, navigated=navigated)
        board = {
            "board_url": "https://careers.example/jobs",
            "metadata": {"tenant": TENANT, "portal": PORTAL},
        }
        async with _bootstrap_client() as client:
            assert await discover(board, client, pw=pw) == []
        assert navigated == [BOARD_URL]

    async def test_missing_board_identity_raises_before_fetch(self):
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="Cannot derive Dayforce"):
                await discover({"board_url": "https://example.com/jobs"}, client, pw=object())

    @pytest.mark.parametrize("status", [404, 410])
    async def test_listing_gone_status_is_authoritative(self, status: int):
        async with _bootstrap_client(status=status) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": BOARD_URL}, client, pw=object())

    async def test_disabled_listing_is_authoritatively_gone(self):
        async with _bootstrap_client(_site_page(disabled=True)) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": BOARD_URL}, client, pw=object())

    async def test_browser_bootstrap_mismatch_fails(self, monkeypatch: pytest.MonkeyPatch):
        async def fetch(_method, _url, _headers, _body):
            raise AssertionError("search should not run")

        pw = _install_browser(monkeypatch, fetch, page_html=_site_page(job_board_id=8))
        async with _bootstrap_client() as client:
            with pytest.raises(ValueError, match="bootstrap changed"):
                await discover({"board_url": BOARD_URL}, client, pw=pw)

    async def test_count_drift_suppresses_tombstoning_instead_of_failing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        async def fetch(_method, _url, _headers, body):
            offset = json.loads(body)["paginationStart"]
            rows = (
                [_raw_job(i) for i in range(1, 26)] if offset == 0 else [_raw_job(26), _raw_job(27)]
            )
            return _search_payload(rows, total=26 if offset == 0 else 27, offset=offset)

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            result = await discover({"board_url": BOARD_URL}, client, pw=pw)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 27

    async def test_count_shrink_beyond_current_offset_suppresses_tombstoning(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        async def fetch(_method, _url, _headers, body):
            offset = json.loads(body)["paginationStart"]
            rows = [_raw_job(i) for i in range(1, 26)] if offset == 0 else []
            return _search_payload(rows, total=26 if offset == 0 else 24, offset=offset)

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            result = await discover({"board_url": BOARD_URL}, client, pw=pw)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 25

    async def test_premature_empty_page_retries_then_fails(self, monkeypatch: pytest.MonkeyPatch):
        calls = 0

        async def fetch(_method, _url, _headers, _body):
            nonlocal calls
            calls += 1
            return _search_payload([], total=2)

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            with pytest.raises(PaginationFetchError) as error:
                await discover({"board_url": BOARD_URL}, client, pw=pw)
        assert error.value.last_error == "PrematureEmptyDayforcePage"
        assert calls == 2

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {"maxCount": True, "offset": 0, "count": 0, "jobPostings": []},
            {"maxCount": 0, "offset": 1, "count": 0, "jobPostings": []},
            {"maxCount": 0, "offset": 0, "count": 0, "jobPostings": {}},
            {"maxCount": 1, "offset": 0, "count": 0, "jobPostings": [_raw_job(1)]},
        ],
    )
    async def test_malformed_search_response_fails(
        self, monkeypatch: pytest.MonkeyPatch, payload: object
    ):
        async def fetch(_method, _url, _headers, _body):
            return payload

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            with pytest.raises(ValueError):
                await discover({"board_url": BOARD_URL}, client, pw=pw)

    @pytest.mark.parametrize(
        "rows",
        [
            [_raw_job(1), _raw_job(1)],
            [_raw_job(1), _raw_job("bad")],
            [_raw_job(1), _raw_job(2, title=" ")],
        ],
    )
    async def test_invalid_or_duplicate_rows_suppress_tombstoning(
        self, monkeypatch: pytest.MonkeyPatch, rows: list[dict]
    ):
        async def fetch(_method, _url, _headers, _body):
            return _search_payload(rows)

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            result = await discover({"board_url": BOARD_URL}, client, pw=pw)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 1

    async def test_foreign_tenant_or_board_record_fails(self, monkeypatch: pytest.MonkeyPatch):
        async def fetch(_method, _url, _headers, _body):
            return _search_payload([_raw_job(1, tenant="other")])

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            with pytest.raises(ValueError, match="foreign tenant"):
                await discover({"board_url": BOARD_URL}, client, pw=pw)

    async def test_cap_preserves_the_complete_collected_page(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(dayforce, "MAX_JOBS", 1)

        async def fetch(_method, _url, _headers, _body):
            return _search_payload([_raw_job(1), _raw_job(2)])

        pw = _install_browser(monkeypatch, fetch)
        async with _bootstrap_client() as client:
            result = await discover({"board_url": BOARD_URL}, client, pw=pw)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {BOARD.job_url("en-US", 1), BOARD.job_url("en-US", 2)}

    async def test_raw_artifact_contains_listing_bootstrap(self, tmp_path):
        page = _site_page()
        async with _bootstrap_client(page) as client:
            await dayforce.save_raw(tmp_path, BOARD_URL, {}, client)
        assert (tmp_path / "dayforce-listing.html").read_text() == page


class TestDetectionAndIntegration:
    async def test_direct_listing_and_detail_detect_without_client(self):
        expected = {"tenant": TENANT, "portal": PORTAL}
        assert await can_handle(BOARD_URL) == expected
        assert await can_handle(f"https://jobs.dayforcehcm.com/en-GB/{TENANT}/{PORTAL}") == expected
        assert await can_handle(JOB_URL) == expected

    async def test_direct_url_is_site_verified_when_client_is_available(self):
        async with _bootstrap_client() as client:
            assert await can_handle(BOARD_URL, client) == {"tenant": TENANT, "portal": PORTAL}

    async def test_query_scoped_direct_url_is_not_widened(self):
        scoped = BOARD_URL + "?department=sales"
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(scoped, client) is None

    async def test_explicit_link_on_company_page_is_detected(self):
        homepage = "https://example.com/careers"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == homepage:
                return httpx.Response(200, text=f'<a href="{JOB_URL}">Apply</a>', request=request)
            assert str(request.url) == BOARD_URL
            return httpx.Response(200, text=_site_page(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(homepage, client)
        assert result == {"tenant": TENANT, "portal": PORTAL}

    async def test_does_not_blindly_guess_company_slug(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>No ATS here</html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle("https://example.com/careers", client) is None

    def test_workspace_scanner_finds_listing_and_detail_urls(self):
        html = f'<a href="{BOARD_URL}">Jobs</a><a href="{JOB_URL}">Role</a>'
        found = _scan_ats_urls_in_html(html)
        assert {candidate.url for candidate in found} == {BOARD_URL, JOB_URL}

    def test_workspace_dedup_uses_tenant_and_portal(self):
        other = DayforceBoard(TENANT, "OtherPortal")
        candidates = [
            CareerPageCandidate(
                url=BOARD_URL,
                source="ats_embed",
                monitor_type="dayforce",
                monitor_config={"tenant": TENANT, "portal": PORTAL},
                score=0.95,
            ),
            CareerPageCandidate(
                url=JOB_URL,
                source="ats_embed",
                monitor_type="dayforce",
                monitor_config={"tenant": TENANT, "portal": PORTAL},
                score=0.90,
            ),
            CareerPageCandidate(
                url=other.listing_url(),
                source="ats_embed",
                monitor_type="dayforce",
                monitor_config={"tenant": TENANT, "portal": other.portal},
                score=0.90,
            ),
        ]
        result = _dedup_candidates(candidates)
        assert {candidate.monitor_config["portal"] for candidate in result} == {
            PORTAL,
            other.portal,
        }

    def test_registry_workspace_browser_and_throttle_integration(self):
        assert "dayforce" in all_monitor_types()
        assert get_stream_fn("dayforce") is dayforce.stream
        assert monitor_needs_browser("dayforce") is True
        assert detect_ats_from_url(BOARD_URL) == "dayforce"
        assert detect_ats_from_url(JOB_URL) == "dayforce"
        assert detect_ats_from_url(BOARD_URL + "?page=2") is None
        assert auto_scraper_type("dayforce") == ("skip", None)
        assert "dayforce" in MONITOR_CARDS
        assert "dayforce" in _MONITOR_CONFIG_HINTS
        throttle_key = _compute_throttle_key("dayforce", BOARD_URL)
        assert throttle_key == "dayforce"
        assert delay_for_domain(throttle_key) == settings.throttle_delay_ats
        assert delay_for_domain("jobs.dayforcehcm.com") == settings.throttle_delay_ats
        assert delay_for_domain("dayforcehcm.com.evil.test") == settings.throttle_delay_default
