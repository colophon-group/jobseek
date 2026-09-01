from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from src.config import settings
from src.core.monitor import MonitorResult
from src.core.monitors import (
    BoardGoneError,
    _build_comment,
    all_monitor_types,
    api_monitor_types,
    get_stream_fn,
    ukg,
)
from src.core.monitors.ukg import can_handle, discover
from src.core.scrapers.embedded import parse_html
from src.probe_boards import _probe_ukg
from src.processing.board import _throttle_key
from src.redis_queue import delay_for_domain
from src.shared.http_retry import PaginationFetchError
from src.shared.tdm import TDMReservedError
from src.shared.ukg import UKGBoard, ukg_board_from_metadata, ukg_board_from_url
from src.sync import _compute_throttle_key
from src.workspace._compat import auto_scraper_type, auto_skip_crawler_types, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

HOST = "recruiting.ultipro.com"
MODERN_HOST = "baptisthealth.rec.pro.ukg.net"
TENANT = "AMI1003AMIK"
BOARD_ID = "132b6065-9187-4e0f-a292-4f67e675d1d0"
JOB_ID = "2b34c2a1-f814-4355-b347-11d1b89a948c"
BOARD = UKGBoard(HOST, TENANT, BOARD_ID)
BOARD_URL = BOARD.listing_url()


def _row(index: int, **overrides) -> dict:
    opportunity_id = f"00000000-0000-4000-8000-{index:012d}"
    row = {
        "Id": opportunity_id,
        "Title": f"Role {index}",
        "RequisitionNumber": f"REQ-{index}",
        "FullTime": True,
        "JobCategoryName": "Engineering",
        "Locations": [
            {
                "LocalizedName": None,
                "LocalizedDescription": "HQ",
                "Address": {
                    "City": "Patrick",
                    "State": {"Code": "SC", "Name": "South Carolina"},
                    "Country": {"Code": "USA", "Name": "United States"},
                },
            }
        ],
        "PostedDate": "2026-08-02T19:44:33.109Z",
        "BriefDescription": "<p class='intro'>Build safely.</p><script>bad()</script>",
        "JobLocationType": 1,
        "OpportunityType": 0,
    }
    row.update(overrides)
    return row


def _payload(rows: list[object], *, total: int | None = None) -> dict:
    return {
        "locations": [],
        "opportunities": rows,
        "totalCount": len(rows) if total is None else total,
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "url,expected",
    [
        (BOARD_URL, BOARD),
        (BOARD_URL + "/", BOARD),
        (BOARD.job_url(JOB_ID), BOARD),
        (
            BOARD_URL.replace("recruiting.ultipro.com", "recruiting2.ultipro.com"),
            UKGBoard("recruiting2.ultipro.com", TENANT, BOARD_ID),
        ),
        (
            BOARD_URL.replace("recruiting.ultipro.com", "recruiting.ultipro.ca"),
            UKGBoard("recruiting.ultipro.ca", TENANT, BOARD_ID),
        ),
        (
            BOARD_URL.replace("recruiting.ultipro.com", MODERN_HOST),
            UKGBoard(MODERN_HOST, TENANT, BOARD_ID),
        ),
        (
            BOARD_URL.replace(TENANT, TENANT.lower()),
            UKGBoard(HOST, TENANT.lower(), BOARD_ID),
        ),
    ],
)
def test_board_identity_accepts_public_urls(url: str, expected: UKGBoard):
    assert ukg_board_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        BOARD_URL.replace("https://", "http://"),
        BOARD_URL.replace("https://", "https://user@"),
        BOARD_URL.replace(HOST, f"{HOST}:444"),
        BOARD_URL.replace(HOST, f"{HOST}.evil.test"),
        BOARD_URL.replace(HOST, "jobs.ultipro.com"),
        BOARD_URL.replace(HOST, "rec.pro.ukg.net"),
        BOARD_URL.replace(HOST, "a.b.rec.pro.ukg.net"),
        BOARD_URL.replace(HOST, "baptisthealth.rec.pro.ukg.net.evil.test"),
        BOARD_URL + "?location=remote",
        BOARD_URL + "#jobs",
        BOARD_URL.replace("/JobBoard/", "/Other/"),
        BOARD_URL.replace(BOARD_ID, "not-a-uuid"),
        BOARD_URL.replace(BOARD_ID, BOARD_ID.replace("-", "")),
        BOARD.job_url(JOB_ID) + "&page=2",
        BOARD.job_url(JOB_ID).replace("opportunityId", "id"),
        BOARD.job_url(JOB_ID).replace(JOB_ID, "bad"),
        BOARD_URL + "/JobBoardView/LoadSearchResults",
    ],
)
def test_board_identity_rejects_unsafe_or_scoped_urls(url: str):
    assert ukg_board_from_url(url) is None


def test_metadata_identity_is_strict_and_listing_url_is_supported():
    assert (
        ukg_board_from_metadata(
            {"host": HOST.upper(), "tenant": TENANT, "board_id": BOARD_ID.upper()}
        )
        == BOARD
    )
    assert ukg_board_from_metadata({"listing_url": BOARD_URL}) == BOARD
    assert (
        ukg_board_from_metadata(
            {"host": "recruiting.ultipro.com.evil.test", "tenant": TENANT, "board_id": BOARD_ID}
        )
        is None
    )
    assert (
        ukg_board_from_metadata({"host": HOST, "tenant": "../tenant", "board_id": BOARD_ID}) is None
    )


def test_parse_job_preserves_rich_fields_and_normalizes_nested_locations():
    job = ukg._parse_job(
        _row(
            1,
            Id=JOB_ID.upper(),
            Locations=[
                {
                    "Address": {
                        "City": " Patrick ",
                        "State": {"Name": "South Carolina"},
                        "Country": {"Name": "United States"},
                    }
                },
                {"LocalizedDescription": "Remote - US", "Address": None},
                {"LocalizedDescription": "remote - us", "Address": {}},
            ],
        ),
        BOARD,
    )

    assert job is not None
    assert job.url == BOARD.job_url(JOB_ID)
    assert job.title == "Role 1"
    assert job.description == "<p>Build safely.</p>"
    assert job.locations == ["Patrick, South Carolina, United States", "Remote - US"]
    assert job.employment_type == "full_time"
    assert job.job_location_type == "onsite"
    assert job.date_posted == "2026-08-02"
    assert job.metadata == {
        "opportunity_id": JOB_ID,
        "requisition_number": "REQ-1",
        "category": "Engineering",
        "opportunity_type": 0,
    }


@pytest.mark.parametrize(
    "raw,employment,location",
    [
        ({"FullTime": False, "JobLocationType": 0}, "part_time", "hybrid"),
        ({"FullTime": True, "JobLocationType": 1}, "full_time", "onsite"),
        ({"FullTime": None, "JobLocationType": 2}, None, "remote"),
        ({"FullTime": "yes", "JobLocationType": True}, None, None),
        ({"FullTime": None, "JobLocationType": 9}, None, None),
    ],
)
def test_parse_job_maps_known_enums_only(raw: dict, employment: str | None, location: str | None):
    job = ukg._parse_job(_row(1, **raw), BOARD)
    assert job is not None
    assert job.employment_type == employment
    assert job.job_location_type == location


def test_parse_job_requires_uuid_and_title_but_tolerates_optional_fields():
    job = ukg._parse_job(
        _row(
            1,
            BriefDescription=None,
            Locations=None,
            PostedDate="not-a-date",
            FullTime=None,
            JobLocationType=None,
        ),
        BOARD,
    )
    assert job is not None
    assert job.description is None
    assert job.locations is None
    assert job.date_posted is None
    assert ukg._parse_job(_row(2, Id="bad"), BOARD) is None
    assert ukg._parse_job(_row(3, Title=" "), BOARD) is None


@pytest.mark.asyncio
async def test_discover_posts_expected_payload_and_maps_one_page():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == BOARD.search_url()
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_payload([_row(1)]), request=request)

    async with _client(handler) as client:
        jobs = await discover({"board_url": BOARD_URL}, client)

    assert isinstance(jobs, list)
    assert len(jobs) == 1
    assert requests == [
        {"opportunitySearch": {"Top": 100, "Skip": 0, "QueryString": "", "Filters": []}}
    ]


@pytest.mark.asyncio
async def test_streams_multiple_pages_and_persists_board_identity(monkeypatch):
    monkeypatch.setattr(ukg, "PAGE_SIZE", 2)
    monkeypatch.setattr(ukg, "MAX_JOBS", 10)
    monkeypatch.setattr(ukg, "MAX_PAGES", 5)
    skips: list[int] = []

    async def fetch(_board, _client, *, skip, take=2):
        skips.append(skip)
        rows: list[object] = [_row(skip + 1), _row(skip + 2)] if skip == 0 else [_row(3)]
        return _payload(rows, total=3)

    monkeypatch.setattr(ukg, "_fetch_page", fetch)
    batches = []
    async for batch in ukg.stream({"board_url": BOARD_URL}, httpx.AsyncClient()):
        batches.append(batch)

    assert skips == [0, 2]
    assert [len(batch.urls) for batch in batches] == [2, 1]
    assert batches[0].metadata_updates == {
        "host": HOST,
        "tenant": TENANT,
        "board_id": BOARD_ID,
        "listing_url": BOARD_URL,
    }
    assert batches[-1].truncated is False


@pytest.mark.asyncio
async def test_zero_jobs_is_authoritative_and_keeps_metadata():
    async with _client(
        lambda request: httpx.Response(200, json=_payload([], total=0), request=request)
    ) as client:
        batches = [batch async for batch in ukg.stream({"board_url": BOARD_URL}, client)]
    assert len(batches) == 1
    assert batches[0].urls == set()
    assert batches[0].truncated is False
    assert batches[0].metadata_updates["board_id"] == BOARD_ID


@pytest.mark.asyncio
async def test_premature_empty_page_retries_then_fails(monkeypatch):
    calls = 0

    async def fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _payload([], total=3)

    monkeypatch.setattr(ukg, "_fetch_page", fetch)
    with pytest.raises(PaginationFetchError) as exc_info:
        await discover({"board_url": BOARD_URL}, httpx.AsyncClient())
    assert exc_info.value.last_error == "PrematureEmptyUKGPage"
    assert calls == 2


@pytest.mark.asyncio
async def test_premature_partial_page_retries_and_recovers(monkeypatch):
    monkeypatch.setattr(ukg, "PAGE_SIZE", 2)
    monkeypatch.setattr(ukg, "MAX_JOBS", 10)
    monkeypatch.setattr(ukg, "MAX_PAGES", 5)
    calls: dict[int, int] = {}

    async def fetch(_board, _client, *, skip, take=2):
        calls[skip] = calls.get(skip, 0) + 1
        if skip == 0 and calls[skip] == 1:
            return _payload([_row(1)], total=3)
        if skip == 0:
            return _payload([_row(1), _row(2)], total=3)
        return _payload([_row(3)], total=3)

    monkeypatch.setattr(ukg, "_fetch_page", fetch)
    jobs = await discover({"board_url": BOARD_URL}, httpx.AsyncClient())
    assert isinstance(jobs, list)
    assert len(jobs) == 3
    assert calls == {0: 2, 2: 1}


@pytest.mark.asyncio
async def test_premature_partial_page_fails_after_retry(monkeypatch):
    monkeypatch.setattr(ukg, "PAGE_SIZE", 2)

    async def fetch(*_args, **_kwargs):
        return _payload([_row(1)], total=3)

    monkeypatch.setattr(ukg, "_fetch_page", fetch)
    with pytest.raises(PaginationFetchError) as exc_info:
        await discover({"board_url": BOARD_URL}, httpx.AsyncClient())
    assert exc_info.value.last_error == "PrematurePartialUKGPage"


@pytest.mark.asyncio
async def test_retry_accepts_concurrent_count_drop_but_marks_run_truncated(monkeypatch):
    monkeypatch.setattr(ukg, "PAGE_SIZE", 2)
    pages = iter([_payload([_row(1)], total=3), _payload([_row(1)], total=1)])

    async def fetch(*_args, **_kwargs):
        return next(pages)

    monkeypatch.setattr(ukg, "_fetch_page", fetch)
    result = await discover({"board_url": BOARD_URL}, httpx.AsyncClient())
    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert result.urls == {BOARD.job_url(_row(1)["Id"])}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pages",
    [
        [_payload([_row(1), "bad"], total=2)],
        [_payload([_row(1), _row(1)], total=2)],
    ],
)
async def test_ambiguous_rows_or_count_drift_suppress_tombstoning(monkeypatch, pages):
    monkeypatch.setattr(ukg, "PAGE_SIZE", 2)
    monkeypatch.setattr(ukg, "MAX_JOBS", 10)
    monkeypatch.setattr(ukg, "MAX_PAGES", 5)
    iterator = iter(pages)

    async def fetch(*_args, **_kwargs):
        return next(iterator)

    monkeypatch.setattr(ukg, "_fetch_page", fetch)
    result = await discover({"board_url": BOARD_URL}, httpx.AsyncClient())
    assert isinstance(result, MonitorResult)
    assert result.truncated is True


@pytest.mark.asyncio
async def test_count_change_across_pages_marks_final_batch_truncated(monkeypatch):
    monkeypatch.setattr(ukg, "PAGE_SIZE", 2)
    pages = iter([_payload([_row(1), _row(2)], total=4), _payload([_row(3)], total=3)])

    async def fetch(*_args, **_kwargs):
        return next(pages)

    monkeypatch.setattr(ukg, "_fetch_page", fetch)
    result = await discover({"board_url": BOARD_URL}, httpx.AsyncClient())
    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert len(result.urls) == 3


@pytest.mark.asyncio
async def test_first_page_404_is_board_gone():
    async with _client(lambda request: httpx.Response(404, request=request)) as client:
        with pytest.raises(BoardGoneError) as exc_info:
            await discover({"board_url": BOARD_URL}, client)
    assert exc_info.value.url == BOARD_URL
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_tdm_reservation_propagates():
    async with _client(
        lambda request: httpx.Response(
            200,
            json=_payload([], total=0),
            headers={"TDM-Reservation": "1"},
            request=request,
        )
    ) as client:
        with pytest.raises(TDMReservedError):
            await discover({"board_url": BOARD_URL}, client)


@pytest.mark.asyncio
async def test_direct_and_explicit_page_detection_require_api_proof():
    homepage = "https://example.com/careers"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == homepage:
            return httpx.Response(
                200,
                text=f'<a href="{BOARD.job_url(JOB_ID)}">Jobs</a>',
                request=request,
            )
        return httpx.Response(200, json=_payload([_row(1)], total=7), request=request)

    async with _client(handler) as client:
        direct = await can_handle(BOARD_URL, client)
        linked = await can_handle(homepage, client)
    expected = {
        "host": HOST,
        "tenant": TENANT,
        "board_id": BOARD_ID,
        "listing_url": BOARD_URL,
        "jobs": 7,
    }
    assert direct == expected
    assert linked == expected


@pytest.mark.asyncio
async def test_branded_host_direct_and_page_detection_require_api_proof():
    homepage = "https://example.com/careers"
    board = UKGBoard(MODERN_HOST, TENANT, BOARD_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == homepage:
            return httpx.Response(
                200,
                text=f'<a href="{board.listing_url()}">Jobs</a>',
                request=request,
            )
        return httpx.Response(200, json=_payload([_row(1)], total=145), request=request)

    async with _client(handler) as client:
        direct = await can_handle(board.listing_url(), client)
        linked = await can_handle(homepage, client)
    expected = {
        "host": MODERN_HOST,
        "tenant": TENANT,
        "board_id": BOARD_ID,
        "listing_url": board.listing_url(),
        "jobs": 145,
    }
    assert direct == expected
    assert linked == expected


@pytest.mark.asyncio
async def test_does_not_guess_company_slug_or_accept_unverified_direct_board():
    async with _client(
        lambda request: httpx.Response(200, text="<html>No ATS here</html>", request=request)
    ) as client:
        assert await can_handle("https://example.com/careers", client) is None
        assert await can_handle(BOARD_URL, client) is None
    assert await can_handle("https://example.com/careers") is None
    assert await can_handle(BOARD_URL) is not None


def test_embedded_detail_enrichment_extracts_full_description():
    scraper = auto_scraper_type("ukg")
    assert scraper is not None
    scraper_type, config = scraper
    assert scraper_type == "embedded"
    assert config is not None
    detail = """
      <script>
      var opportunity = new US.Opportunity.CandidateOpportunityDetail({
        "Id":"2b34c2a1-f814-4355-b347-11d1b89a948c",
        "Title":"Teacher",
        "Description":"<h2>Role</h2><p>Teach &amp; mentor.</p>"
      });
      </script>
    """
    content = parse_html(detail, config)
    assert content.title == "Teacher"
    assert content.description == "<h2>Role</h2><p>Teach &amp; mentor.</p>"
    assert config["enrich"] == ["description"]


def test_workspace_registry_discovery_and_throttle_integration():
    assert "ukg" in all_monitor_types()
    assert "ukg" in api_monitor_types()
    assert "ukg" not in auto_skip_crawler_types()
    assert get_stream_fn("ukg") is ukg.stream
    assert detect_ats_from_url(BOARD_URL) == "ukg"
    assert detect_ats_from_url(BOARD.job_url(JOB_ID)) == "ukg"
    assert "ukg" in MONITOR_CARDS
    assert "ukg" in _MONITOR_CONFIG_HINTS
    board = MagicMock()
    values = {"crawler_type": "ukg", "board_url": BOARD_URL, "metadata": {}}
    board.__getitem__ = lambda _self, key: values[key]
    assert _throttle_key(board) == "ukg"
    assert _compute_throttle_key("ukg", BOARD_URL) == "ukg"
    assert delay_for_domain("ukg") == settings.throttle_delay_ats
    assert delay_for_domain(HOST) == settings.throttle_delay_ats
    assert delay_for_domain("recruiting2.ultipro.com") == settings.throttle_delay_ats
    assert delay_for_domain("recruiting.ultipro.ca") == settings.throttle_delay_ats
    assert delay_for_domain(MODERN_HOST) == settings.throttle_delay_ats
    assert delay_for_domain("ultipro.com.evil.test") == settings.throttle_delay_default
    assert (
        _build_comment("ukg", {"tenant": TENANT, "board_id": BOARD_ID, "jobs": 17})
        == f"UKG Pro API \u2014 tenant: {TENANT}, board: {BOARD_ID}, 17 jobs"
    )


def test_workspace_scanner_finds_listing_and_canonicalizes_detail():
    found = _scan_ats_urls_in_html(
        f'<a href="{BOARD_URL}">Jobs</a><a href="{BOARD.job_url(JOB_ID)}">Role</a>'
    )
    assert {candidate.url for candidate in found} == {BOARD_URL, BOARD.job_url(JOB_ID)}


def test_workspace_scanner_finds_branded_host_listing_and_detail():
    board = UKGBoard(MODERN_HOST, TENANT, BOARD_ID)
    found = _scan_ats_urls_in_html(
        f'<a href="{board.listing_url()}">Jobs</a>'
        f'<a href="{board.job_url(JOB_ID)}">Role</a>'
    )
    assert {candidate.url for candidate in found} == {
        board.listing_url(),
        board.job_url(JOB_ID),
    }


@pytest.mark.asyncio
async def test_lightweight_board_probe_posts_top_one_and_validates_shape():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_payload([_row(1)], total=17), request=request)

    row = {
        "board_slug": "example-careers",
        "board_url": BOARD_URL,
        "monitor_config": json.dumps({"host": HOST, "tenant": TENANT, "board_id": BOARD_ID}),
    }
    async with _client(handler) as client:
        result = await _probe_ukg(row, client)
    assert result.status == "ok"
    assert result.message == "200 (17 jobs)"
    assert captured["opportunitySearch"]["Top"] == 1


@pytest.mark.asyncio
async def test_lightweight_board_probe_rejects_identity_mismatch():
    other = UKGBoard(HOST, TENANT, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    row = {
        "board_slug": "example-careers",
        "board_url": BOARD_URL,
        "monitor_config": json.dumps(
            {"host": other.host, "tenant": other.tenant, "board_id": other.board_id}
        ),
    }
    async with httpx.AsyncClient() as client:
        result = await _probe_ukg(row, client)
    assert result.status == "fail"
    assert "does not match" in result.message
