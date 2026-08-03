from __future__ import annotations

import html
import json
from pathlib import Path

import httpx
import pytest

from src.config import settings
from src.core.adp import normalize_adp_employment_type
from src.core.monitor import MonitorResult
from src.core.monitors import (
    BoardGoneError,
    adp,
    all_monitor_types,
    api_monitor_types,
    is_rich_monitor,
    monitor_needs_browser,
)
from src.core.monitors.adp import can_handle, discover
from src.probe_boards import probe_row
from src.redis_queue import delay_for_domain
from src.shared.adp import (
    AdpBoard,
    adp_board_from_metadata,
    adp_board_from_url,
    adp_start_from_search_url,
)
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import (
    CareerPageCandidate,
    _dedup_candidates,
    _extract_links,
)

CID = "0b103883-5bcb-4c19-89f9-e2b305fc27b0"
CC_ID = "19000101_000001"
LOCALE = "en_US"
BOARD = AdpBoard(cid=CID, cc_id=CC_ID, locale=LOCALE)
BOARD_URL = BOARD.listing_url()


def _raw_job(index: int, **overrides) -> dict:
    row = {
        "itemID": f"{9_200_000_000_000 + index}_1",
        "clientRequisitionID": f"REQ-{index}",
        "requisitionTitle": f"Engineer {index}",
        "postDate": "2026-08-03T12:09:00.000-04:00",
        "workLevelCode": {"shortName": "Full Time"},
        "requisitionLocations": [
            {"nameCode": {"shortName": " Toronto,  ON, CA "}},
            {"nameCode": {"shortName": "Toronto, ON, CA"}},
        ],
    }
    row.update(overrides)
    return row


def _payload(rows: list[object], *, total: int, start: int) -> dict:
    return {
        "jobRequisitions": rows,
        "meta": {"startSequence": start, "totalNumber": total, "links": []},
    }


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestIdentity:
    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL,
            (
                "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
                f"recruitment.html?cid={CID}&ccId={CC_ID}&lang={LOCALE}"
            ),
            BOARD.job_url("9202920507783_1"),
            BOARD_URL.replace(CID, CID.upper()),
            BOARD_URL.replace("workforcenow.adp.com", "workforcenow.adp.com:443"),
        ],
    )
    def test_accepts_unfiltered_listing_and_detail_urls(self, url: str):
        assert adp_board_from_url(url) == BOARD

    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL.replace("https://", "http://"),
            BOARD_URL.replace("https://", "https://attacker@"),
            BOARD_URL.replace("workforcenow.adp.com", "workforcenow.adp.com.evil.test"),
            BOARD_URL.replace("workforcenow.adp.com", "workforcenow.adp.com:444"),
            BOARD_URL.replace("/mdf/recruitment/recruitment.html", "/jobs"),
            BOARD_URL + "&department=engineering",
            BOARD_URL + "&cid=" + CID,
            BOARD_URL.replace("selectedMenuKey=CareerCenter", "selectedMenuKey=Other"),
            BOARD_URL + "&jobId=bad%2Fid",
            BOARD_URL + "#jobs",
            BOARD_URL.replace("ccId=19000101_000001", "ccId=bad"),
            BOARD_URL.replace("lang=en_US", "lang=english"),
        ],
    )
    def test_rejects_untrusted_or_scoped_variants(self, url: str):
        assert adp_board_from_url(url) is None

    def test_metadata_and_search_identity(self):
        assert (
            adp_board_from_metadata({"cid": CID.upper(), "cc_id": CC_ID, "locale": "EN_us"})
            == BOARD
        )
        assert adp_start_from_search_url(BOARD.search_url(start=21), BOARD) == 21
        assert adp_start_from_search_url(BOARD.search_url(start=21) + "&filter=x", BOARD) is None


class TestMonitor:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Regular Full Time", "full_time"),
            ("36 Non-Exempt Full Time", "full_time"),
            ("20 Non Exempt Part Time", "part_time"),
            ("Per Diem Direct Traveler", "part_time"),
            ("Summer Intern", "internship"),
        ],
    )
    def test_normalizes_custom_worker_categories(self, raw: str, expected: str):
        assert normalize_adp_employment_type(raw) == expected

    async def test_maps_and_paginates_all_jobs(self):
        starts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.headers["x-requested-with"] == "XMLHttpRequest"
            assert request.headers["x-forwarded-host"] == "workforcenow.adp.com"
            start = int(request.url.params["$skip"])
            starts.append(start)
            if start == 1:
                rows = [_raw_job(index) for index in range(1, 21)]
            elif start == 21:
                rows = [_raw_job(index) for index in range(21, 26)]
            else:
                raise AssertionError(f"unexpected page {start}")
            return httpx.Response(200, json=_payload(rows, total=25, start=start), request=request)

        async with _client(handler) as client:
            jobs = await discover({"board_url": BOARD_URL}, client)

        assert starts == [1, 21]
        assert len(jobs) == 25
        first = jobs[0]
        assert first.url == BOARD.job_url("9200000000001_1")
        assert first.title == "Engineer 1"
        assert first.locations == ["Toronto, ON, CA"]
        assert first.employment_type == "full_time"
        assert first.date_posted == "2026-08-03T12:09:00.000-04:00"
        assert first.language == "en"
        assert first.metadata == {"item_id": "9200000000001_1", "requisition_id": "REQ-1"}

    async def test_valid_empty_board_uses_one_request(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=_payload([], total=0, start=1), request=request)

        async with _client(handler) as client:
            jobs = await discover({"board_url": BOARD_URL}, client)

        assert jobs == []
        assert calls == 1

    @pytest.mark.parametrize("status", [429, 503])
    async def test_retries_transient_first_page(
        self,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
    ):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                return httpx.Response(status, json={"error": "transient"}, request=request)
            return httpx.Response(200, json=_payload([], total=0, start=1), request=request)

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("src.shared.api_sniff.asyncio.sleep", no_sleep)
        async with _client(handler) as client:
            assert await discover({"board_url": BOARD_URL}, client) == []
        assert calls == 3

    async def test_authoritative_gone_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, request=request)

        async with _client(handler) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": BOARD_URL}, client)

    async def test_non_transient_status_fails_without_retry(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(400, json={"error": "invalid"}, request=request)

        async with _client(handler) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)

        assert calls == 1
        assert exc_info.value.last_status == 400

    async def test_malformed_response_exhausts_retry(self, monkeypatch: pytest.MonkeyPatch):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"jobRequisitions": []}, request=request)

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("src.shared.api_sniff.asyncio.sleep", no_sleep)
        async with _client(handler) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)

        assert calls == 3
        assert exc_info.value.last_error == "ValueError"

    async def test_count_drift_returns_truncated_result(self):
        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params["$skip"])
            total = 25 if start == 1 else 26
            rows = (
                [_raw_job(index) for index in range(1, 21)]
                if start == 1
                else [_raw_job(index) for index in range(21, 27)]
            )
            return httpx.Response(
                200,
                json=_payload(rows, total=total, start=start),
                request=request,
            )

        async with _client(handler) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 26

    async def test_duplicate_or_invalid_row_returns_truncated_result(self):
        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params["$skip"])
            rows = (
                [_raw_job(index) for index in range(1, 21)]
                if start == 1
                else [_raw_job(20, requisitionTitle="Duplicate")]
            )
            return httpx.Response(200, json=_payload(rows, total=21, start=start), request=request)

        async with _client(handler) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 20

    async def test_job_cap_returns_truncated_result_without_fetching_past_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        starts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params["$skip"])
            starts.append(start)
            rows = [_raw_job(index) for index in range(1, 21)]
            return httpx.Response(
                200,
                json=_payload(rows, total=21, start=start),
                request=request,
            )

        monkeypatch.setattr(adp, "MAX_JOBS", 20)
        monkeypatch.setattr(adp, "MAX_PAGES", 1)
        async with _client(handler) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert starts == [1]
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 20


class TestDetectionAndIntegration:
    async def test_direct_detection_returns_complete_config_and_count(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_payload([_raw_job(1)], total=1, start=1),
                request=request,
            )

        async with _client(handler) as client:
            result = await can_handle(BOARD.job_url("9200000000001_1"), client)

        assert result == {"cid": CID, "cc_id": CC_ID, "locale": LOCALE, "jobs": 1}

    async def test_explicitly_linked_board_is_detected_without_slug_guessing(self):
        homepage = "https://example.com/careers"
        linked = html.escape(BOARD_URL, quote=True)

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == homepage:
                return httpx.Response(
                    200,
                    text=f'<a href="{linked}">Jobs</a>',
                    request=request,
                )
            return httpx.Response(200, json=_payload([], total=0, start=1), request=request)

        async with _client(handler) as client:
            result = await can_handle(homepage, client)

        assert result == {"cid": CID, "cc_id": CC_ID, "locale": LOCALE, "jobs": 0}

    async def test_unrelated_page_does_not_trigger_blind_probe(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, text="<p>No ATS link</p>", request=request)

        async with _client(handler) as client:
            assert await can_handle("https://example.com/careers", client) is None

        assert calls == ["https://example.com/careers"]

    async def test_raw_artifact_saves_validated_first_page(self, tmp_path: Path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_payload([_raw_job(1)], total=1, start=1),
                request=request,
            )

        async with _client(handler) as client:
            await adp.save_raw(tmp_path, BOARD_URL, {}, client)

        payload = json.loads((tmp_path / "adp-listing.json").read_text())
        assert payload["meta"]["totalNumber"] == 1

    async def test_board_probe_uses_native_identity_and_reports_count(self):
        row = {
            "board_slug": "acme-adp",
            "board_url": BOARD_URL,
            "monitor_type": "adp",
            "monitor_config": json.dumps({"cid": CID, "cc_id": CC_ID, "locale": LOCALE}),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert adp_start_from_search_url(str(request.url), BOARD) == 1
            return httpx.Response(
                200,
                json=_payload([_raw_job(1)], total=1, start=1),
                request=request,
            )

        async with _client(handler) as client:
            result = await probe_row(row, client)

        assert result.status == "ok"
        assert result.message == "200, 1 jobs"

    def test_career_scanner_and_board_identity_dedup(self):
        links = _extract_links(
            f'<a href="{html.escape(BOARD_URL, quote=True)}">Careers</a>',
            "https://example.com",
        )
        assert any(link.url == BOARD_URL and link.source == "ats_embed" for link in links)

        config = {"cid": CID, "cc_id": CC_ID, "locale": LOCALE}
        candidates = [
            CareerPageCandidate(
                url=BOARD_URL,
                source="ats_embed",
                monitor_type="adp",
                monitor_config=config,
                score=0.9,
            ),
            CareerPageCandidate(
                url=BOARD.job_url("9200000000001_1"),
                source="homepage_link",
                monitor_type="adp",
                monitor_config=config,
                score=0.7,
            ),
        ]
        assert _dedup_candidates(candidates) == [candidates[0]]

    def test_registry_ws_scraper_detection_and_throttle(self):
        assert "adp" in all_monitor_types()
        assert "adp" in api_monitor_types()
        assert is_rich_monitor("adp") is True
        assert monitor_needs_browser("adp") is False
        assert detect_ats_from_url(BOARD_URL) == "adp"
        scraper, config = auto_scraper_type("adp", {}) or (None, None)
        assert scraper == "adp"
        assert config == {
            "enrich": [
                "title",
                "description",
                "locations",
                "employment_type",
                "date_posted",
                "base_salary",
            ]
        }
        assert delay_for_domain("adp") == settings.throttle_delay_ats
        assert delay_for_domain("workforcenow.adp.com") == settings.throttle_delay_ats
