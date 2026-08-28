from __future__ import annotations

import json

import httpx
import pytest

from src.config import settings
from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, cornerstone, get_stream_fn
from src.core.monitors.cornerstone import can_handle, discover
from src.redis_queue import delay_for_domain
from src.shared.cornerstone import (
    CornerstoneBoard,
    CornerstoneContextMissingError,
    cornerstone_board_from_metadata,
    cornerstone_board_from_url,
    extract_cornerstone_context,
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

TENANT = "aswatsoneurope"
SITE_ID = 16
CORP = "aswatsoneurope"
BOARD = CornerstoneBoard(TENANT, SITE_ID, CORP)
BOARD_URL = BOARD.listing_url()
FED_BOARD = CornerstoneBoard("leidosbiomed", 4, "leidosbiomed", "csodfed.com")
FED_SEARCH_URL = "https://us-il2-hs.api.csodfed.com/rec-job-search/external/jobs"
SEARCH_URL = "https://eu-fra.api.csod.com/rec-job-search/external/jobs"
TOKEN_ONE = f"{'a' * 24}.{'b' * 24}.{'c' * 24}"
TOKEN_TWO = f"{'d' * 24}.{'e' * 24}.{'f' * 24}"


def _context_page(
    *,
    token: str = TOKEN_ONE,
    corp: str = CORP,
    cloud: str = "https://eu-fra.api.csod.com/",
    culture_id: object = 1,
    culture_name: object = "en-US",
) -> str:
    context = {
        "corp": corp,
        "token": token,
        "cultureID": culture_id,
        "cultureName": culture_name,
        "endpoints": {"cloud": cloud},
    }
    return f"<script>if (!csod.context) csod.context={json.dumps(context)};</script>"


def _raw_job(
    requisition_id: object,
    *,
    title: object = "Platform Engineer",
    description: object = "Build reliable systems.",
    date: object = "07/30/2026",
    locations: object = None,
) -> dict:
    return {
        "requisitionId": requisition_id,
        "displayJobTitle": title,
        "externalDescription": description,
        "postingEffectiveDate": date,
        "postingExpirationDate": "-",
        "locations": locations
        if locations is not None
        else [{"city": "Renswoude", "country": "NL"}],
    }


def _search_payload(rows: list[object], *, total: int | None = None, status: str = "Success"):
    return {
        "status": status,
        "data": {
            "totalCount": len(rows) if total is None else total,
            "requisitions": rows,
            "filters": [],
            "customFieldFilters": [],
        },
    }


class TestBoardIdentity:
    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL,
            f"https://{TENANT}.csod.com/ux/ats/careersite/{SITE_ID}/home/"
            f"requisition/273622?c={CORP}",
            f"https://{TENANT.upper()}.csod.com:443/ux/ats/careersite/{SITE_ID}/home"
            f"?c={CORP.upper()}",
        ],
    )
    def test_accepts_canonical_listing_and_detail_urls(self, url: str):
        assert cornerstone_board_from_url(url) == BOARD

    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL.replace("https://", "http://"),
            BOARD_URL.replace("https://", "https://user@"),
            BOARD_URL.replace(".csod.com", ".csod.com:444"),
            BOARD_URL.replace(".csod.com", ".nested.csod.com"),
            BOARD_URL.replace(f"{TENANT}.csod.com", "portal.csod.com"),
            BOARD_URL.replace("/home", "/jobs"),
            BOARD_URL.replace(f"/{SITE_ID}/", "/0/"),
            BOARD_URL.replace("?c=", "?page=2&c="),
            BOARD_URL + "&page=2",
            BOARD_URL + f"&c={CORP}",
            BOARD_URL + "#jobs",
            BOARD_URL.replace(".csod.com", ".csod.com.evil.test"),
            f"https://{TENANT}.csod.com:bad/ux/ats/careersite/{SITE_ID}/home?c={CORP}",
            f"https://{TENANT}.csod.com/ux/ats/careersite/{SITE_ID}/home/requisition/nope?c={CORP}",
        ],
    )
    def test_rejects_untrusted_or_scoped_urls(self, url: str):
        assert cornerstone_board_from_url(url) is None

    def test_metadata_is_normalized(self):
        assert (
            cornerstone_board_from_metadata(
                {"tenant": f" {TENANT.upper()} ", "site_id": str(SITE_ID), "corp": CORP.upper()}
            )
            == BOARD
        )

    def test_accepts_the_explicit_federal_cornerstone_domain(self):
        assert cornerstone_board_from_url(FED_BOARD.listing_url()) == FED_BOARD
        assert (
            cornerstone_board_from_metadata(
                {
                    "tenant": "leidosbiomed",
                    "site_id": 4,
                    "corp": "leidosbiomed",
                    "domain": "csodfed.com",
                }
            )
            == FED_BOARD
        )

    @pytest.mark.parametrize(
        "metadata",
        [
            {"tenant": "portal", "site_id": SITE_ID, "corp": CORP},
            {"tenant": TENANT, "site_id": 0, "corp": CORP},
            {"tenant": TENANT, "site_id": True, "corp": CORP},
            {"tenant": TENANT, "site_id": SITE_ID, "corp": "bad/corp"},
            {"tenant": TENANT, "site_id": SITE_ID, "corp": CORP, "domain": "evil.test"},
        ],
    )
    def test_invalid_metadata_is_rejected(self, metadata: dict):
        assert cornerstone_board_from_metadata(metadata) is None


class TestBootstrapContext:
    def test_extracts_trusted_context_without_exposing_token_in_repr(self):
        context = extract_cornerstone_context(_context_page(), BOARD)

        assert context.search_url == SEARCH_URL
        assert context.culture_id == 1
        assert context.culture_name == "en-US"
        assert context.headers["Authorization"] == f"Bearer {TOKEN_ONE}"
        assert TOKEN_ONE not in repr(context)

    def test_accepts_the_federal_cornerstone_api_for_a_federal_board(self):
        context = extract_cornerstone_context(
            _context_page(
                corp="leidosbiomed",
                cloud="https://us-il2-hs.api.csodfed.com/",
            ),
            FED_BOARD,
        )

        assert context.search_url == FED_SEARCH_URL

    @pytest.mark.parametrize(
        ("page", "message"),
        [
            ("<html></html>", "omitted csod.context"),
            ("<script>csod.context={bad};</script>", "invalid context JSON"),
            (_context_page(corp="other"), "does not match"),
            (_context_page(token="short.token.value"), "malformed session token"),
            (_context_page(cloud="https://api.csod.com.evil.test/"), "untrusted API origin"),
            (_context_page(cloud="https://api.csodfed.com/"), "untrusted API origin"),
            (_context_page(cloud="http://eu-fra.api.csod.com/"), "untrusted API origin"),
            (_context_page(culture_id=True), "invalid culture ID"),
            (_context_page(culture_name="bad/culture"), "invalid culture name"),
        ],
    )
    def test_rejects_malformed_or_untrusted_context(self, page: str, message: str):
        with pytest.raises(ValueError, match=message):
            extract_cornerstone_context(page, BOARD)


class TestMonitor:
    async def test_maps_complete_job_and_search_request(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                assert str(request.url) == BOARD_URL
                return httpx.Response(200, text=_context_page(), request=request)
            captured["headers"] = request.headers
            captured["payload"] = json.loads(request.content)
            rows = [
                _raw_job(
                    273622,
                    locations=[
                        {"city": "Renswoude", "country": "NL"},
                        {"city": "Renswoude", "country": "NL"},
                        {"city": "Paris", "state": "IDF", "country": "FR"},
                    ],
                )
            ]
            return httpx.Response(200, json=_search_payload(rows), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover({"board_url": BOARD_URL}, client)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.url == BOARD.job_url(273622)
        assert job.title == "Platform Engineer"
        assert job.description == "Build reliable systems."
        assert job.locations == ["Renswoude, NL", "Paris, IDF, FR"]
        assert job.date_posted == "2026-07-30"
        assert job.language == "en"
        assert job.metadata == {"requisition_id": 273622}
        assert captured["headers"]["authorization"] == f"Bearer {TOKEN_ONE}"
        assert captured["headers"]["csod-accept-language"] == "en-US"
        assert captured["payload"]["careerSiteId"] == SITE_ID
        assert captured["payload"]["careerSitePageId"] == SITE_ID
        assert captured["payload"]["pageNumber"] == 1
        assert captured["payload"]["pageSize"] == 100
        assert captured["payload"]["searchText"] == ""

    async def test_retries_one_accepted_page_that_omits_bootstrap_context(self, monkeypatch):
        bootstrap_requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal bootstrap_requests
            if request.method == "GET":
                bootstrap_requests += 1
                page = (
                    "<html><script>window.transient='do-not-log'</script></html>"
                    if bootstrap_requests == 1
                    else _context_page()
                )
                return httpx.Response(200, text=page, request=request)
            return httpx.Response(
                200,
                json=_search_payload([_raw_job(273622)]),
                request=request,
            )

        monkeypatch.setattr(cornerstone, "_BOOTSTRAP_CONTEXT_RETRY_DELAY", 0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover({"board_url": BOARD_URL}, client)

        assert len(jobs) == 1
        assert bootstrap_requests == 2

    async def test_persistent_missing_bootstrap_context_fails_after_bounded_retry(
        self, monkeypatch
    ):
        bootstrap_requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal bootstrap_requests
            bootstrap_requests += 1
            return httpx.Response(200, text="<html><script></script></html>", request=request)

        monkeypatch.setattr(cornerstone, "_BOOTSTRAP_CONTEXT_RETRY_DELAY", 0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(CornerstoneContextMissingError, match="omitted csod.context"):
                await discover({"board_url": BOARD_URL}, client)

        assert bootstrap_requests == 2

    async def test_bootstrap_owner_mismatch_is_not_retried(self):
        bootstrap_requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal bootstrap_requests
            bootstrap_requests += 1
            return httpx.Response(200, text=_context_page(corp="other"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="does not match"):
                await discover({"board_url": BOARD_URL}, client)

        assert bootstrap_requests == 1

    async def test_localized_day_first_date_is_normalized(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text=_context_page(culture_id=13, culture_name="fr-FR"),
                    request=request,
                )
            return httpx.Response(
                200,
                json=_search_payload([_raw_job(1, date="27/07/2026")]),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover({"board_url": BOARD_URL}, client)
        assert jobs[0].date_posted == "2026-07-27"
        assert jobs[0].language == "fr"

    async def test_streams_every_page_without_materializing_first(self):
        page_numbers: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            page_number = json.loads(request.content)["pageNumber"]
            page_numbers.append(page_number)
            start = 1 if page_number == 1 else 101
            size = 100 if page_number == 1 else 1
            rows = [_raw_job(i) for i in range(start, start + size)]
            return httpx.Response(200, json=_search_payload(rows, total=101), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            iterator = cornerstone.stream({"board_url": BOARD_URL}, client)
            first = await anext(iterator)
            assert len(first.urls) == 100
            assert page_numbers == [1]
            second = await anext(iterator)
            assert len(second.urls) == 1
            assert second.truncated is False
            with pytest.raises(StopAsyncIteration):
                await anext(iterator)

        assert page_numbers == [1, 2]

    async def test_valid_empty_board(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            return httpx.Response(200, json=_search_payload([]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": BOARD_URL}, client) == []

    async def test_metadata_override_supports_noncanonical_stored_url(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            return httpx.Response(200, json=_search_payload([]), request=request)

        board = {
            "board_url": "https://careers.example/jobs",
            "metadata": {"tenant": TENANT, "site_id": SITE_ID, "corp": CORP},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover(board, client) == []
        assert seen[0] == BOARD_URL

    async def test_missing_board_identity_raises_before_fetch(self):
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="Cannot derive Cornerstone"):
                await discover({"board_url": "https://example.com/jobs"}, client)

    @pytest.mark.parametrize("status", [404, 410])
    async def test_listing_gone_status_is_authoritative(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": BOARD_URL}, client)

    async def test_unexpected_redirect_is_a_failure(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"location": "https://www.csod.com/"},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError):
                await discover({"board_url": BOARD_URL}, client)

    async def test_expired_search_token_refreshes_bootstrap_once(self):
        home_calls = 0
        auth_headers: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal home_calls
            if request.method == "GET":
                home_calls += 1
                token = TOKEN_ONE if home_calls == 1 else TOKEN_TWO
                return httpx.Response(200, text=_context_page(token=token), request=request)
            auth = request.headers["authorization"]
            auth_headers.append(auth)
            if auth == f"Bearer {TOKEN_ONE}":
                return httpx.Response(401, request=request)
            return httpx.Response(200, json=_search_payload([]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": BOARD_URL}, client) == []

        assert home_calls == 2
        assert auth_headers == [f"Bearer {TOKEN_ONE}", f"Bearer {TOKEN_TWO}"]

    async def test_non_auth_search_failure_propagates_without_refresh(self):
        home_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal home_calls
            if request.method == "GET":
                home_calls += 1
                return httpx.Response(200, text=_context_page(), request=request)
            return httpx.Response(400, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await discover({"board_url": BOARD_URL}, client)
        assert home_calls == 1

    async def test_count_drift_fails_instead_of_returning_partial_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            page = json.loads(request.content)["pageNumber"]
            rows = [_raw_job(i) for i in range(1, 101)] if page == 1 else [_raw_job(101)]
            total = 101 if page == 1 else 100
            return httpx.Response(200, json=_search_payload(rows, total=total), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="count changed"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_premature_empty_page_retries_then_fails(self):
        search_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal search_calls
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            search_calls += 1
            return httpx.Response(200, json=_search_payload([], total=2), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as error:
                await discover({"board_url": BOARD_URL}, client)
        assert error.value.last_error == "PrematureEmptyCornerstonePage"
        assert search_calls == 2

    @pytest.mark.parametrize(
        "payload",
        [
            {"status": "Error", "data": {}},
            {"status": "Success", "data": []},
            {"status": "Success", "data": {"totalCount": True, "requisitions": []}},
            {"status": "Success", "data": {"totalCount": 0, "requisitions": {}}},
        ],
    )
    async def test_malformed_search_response_fails(self, payload: dict):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            return httpx.Response(200, json=payload, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                await discover({"board_url": BOARD_URL}, client)

    @pytest.mark.parametrize(
        "rows",
        [
            [_raw_job(1), _raw_job(1)],
            [_raw_job(1), _raw_job("bad")],
            [_raw_job(1), _raw_job(2, title=" ")],
        ],
    )
    async def test_invalid_or_duplicate_rows_suppress_tombstoning(self, rows: list[dict]):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            return httpx.Response(200, json=_search_payload(rows), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 1

    async def test_cap_preserves_the_complete_collected_page(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(cornerstone, "MAX_JOBS", 1)
        rows = [_raw_job(1), _raw_job(2)]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            return httpx.Response(200, json=_search_payload(rows), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {BOARD.job_url(1), BOARD.job_url(2)}

    async def test_raw_artifact_contains_search_data_but_not_session_token(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            return httpx.Response(
                200,
                json=_search_payload([_raw_job(1)]),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await cornerstone.save_raw(tmp_path, BOARD_URL, {}, client)

        raw = (tmp_path / "cornerstone-search.json").read_text()
        assert TOKEN_ONE not in raw
        assert json.loads(raw)["data"]["totalCount"] == 1


class TestDetectionAndIntegration:
    async def test_direct_listing_and_detail_detect_without_client(self):
        expected = {"tenant": TENANT, "site_id": SITE_ID, "corp": CORP}
        assert await can_handle(BOARD_URL) == expected
        assert await can_handle(BOARD.job_url(273622)) == expected

    async def test_direct_federal_listing_preserves_its_domain(self):
        assert await can_handle(FED_BOARD.listing_url()) == {
            "tenant": "leidosbiomed",
            "site_id": 4,
            "corp": "leidosbiomed",
            "domain": "csodfed.com",
        }
        assert detect_ats_from_url(FED_BOARD.listing_url()) == "cornerstone"

    async def test_direct_url_is_api_verified_when_client_is_available(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            return httpx.Response(200, json=_search_payload([], total=42), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(BOARD_URL, client)
        assert result == {"tenant": TENANT, "site_id": SITE_ID, "corp": CORP, "jobs": 42}

    async def test_query_scoped_direct_url_is_not_widened(self):
        scoped = BOARD_URL + "&page=2"
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(scoped, client) is None

    async def test_explicit_link_on_company_page_is_detected(self):
        homepage = "https://example.com/careers"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == homepage:
                return httpx.Response(
                    200,
                    text=f'<a href="{BOARD.job_url(273622)}">Apply</a>',
                    request=request,
                )
            if request.method == "GET":
                return httpx.Response(200, text=_context_page(), request=request)
            return httpx.Response(200, json=_search_payload([], total=7), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(homepage, client)
        assert result == {"tenant": TENANT, "site_id": SITE_ID, "corp": CORP, "jobs": 7}

    async def test_does_not_blindly_guess_company_slug(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>No ATS here</html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle("https://example.com/careers", client) is None

    def test_workspace_scanner_finds_cornerstone_url(self):
        found = _scan_ats_urls_in_html(f'<a href="{BOARD_URL}">Jobs</a>')
        assert any(candidate.url == BOARD_URL for candidate in found)

    def test_workspace_dedup_uses_full_board_identity(self):
        candidates = [
            CareerPageCandidate(
                url=BOARD_URL,
                source="ats_embed",
                monitor_type="cornerstone",
                monitor_config={"tenant": TENANT, "site_id": SITE_ID, "corp": CORP},
                score=0.95,
                comment="listing",
            ),
            CareerPageCandidate(
                url=BOARD.job_url(273622),
                source="ats_embed",
                monitor_type="cornerstone",
                monitor_config={"tenant": TENANT, "site_id": SITE_ID, "corp": CORP},
                score=0.90,
                comment="detail",
            ),
            CareerPageCandidate(
                url=CornerstoneBoard(TENANT, 18, CORP).listing_url(),
                source="ats_embed",
                monitor_type="cornerstone",
                monitor_config={"tenant": TENANT, "site_id": 18, "corp": CORP},
                score=0.90,
                comment="other site",
            ),
            CareerPageCandidate(
                url=CornerstoneBoard(TENANT, SITE_ID, "othercorp").listing_url(),
                source="ats_embed",
                monitor_type="cornerstone",
                monitor_config={"tenant": TENANT, "site_id": SITE_ID, "corp": "othercorp"},
                score=0.90,
                comment="other corporation",
            ),
        ]
        result = _dedup_candidates(candidates)
        assert {
            (candidate.monitor_config["site_id"], candidate.monitor_config["corp"])
            for candidate in result
        } == {(SITE_ID, CORP), (18, CORP), (SITE_ID, "othercorp")}
        assert len(result) == 3

    def test_registry_workspace_and_throttle_integration(self):
        assert "cornerstone" in all_monitor_types()
        assert get_stream_fn("cornerstone") is cornerstone.stream
        assert detect_ats_from_url(BOARD_URL) == "cornerstone"
        assert detect_ats_from_url(BOARD_URL + "&page=2") is None
        assert auto_scraper_type("cornerstone") == ("skip", None)
        assert "cornerstone" in MONITOR_CARDS
        assert "cornerstone" in _MONITOR_CONFIG_HINTS
        throttle_key = _compute_throttle_key("cornerstone", BOARD_URL)
        assert throttle_key == "cornerstone"
        assert delay_for_domain(throttle_key) == settings.throttle_delay_ats
        assert delay_for_domain(f"{TENANT}.csod.com") == settings.throttle_delay_ats
        assert delay_for_domain("eu-fra.api.csod.com") == settings.throttle_delay_ats
        assert delay_for_domain("leidosbiomed.csodfed.com") == settings.throttle_delay_ats
        assert delay_for_domain("us-il2-hs.api.csodfed.com") == settings.throttle_delay_ats
        assert delay_for_domain("csod.com.evil.test") == settings.throttle_delay_default
