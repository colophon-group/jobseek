from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import (
    all_monitor_types,
    darwinbox,
    get_stream_fn,
    monitor_needs_browser,
)
from src.core.monitors.darwinbox import can_handle, discover
from src.processing.board import _throttle_key
from src.redis_queue import delay_for_domain
from src.shared.darwinbox import (
    DarwinboxBoard,
    darwinbox_board_from_metadata,
    darwinbox_board_from_url,
)
from src.shared.http_retry import PaginationFetchError
from src.shared.tdm import TDMReservedError
from src.sync import _compute_throttle_key
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

HOST = "airtel.darwinbox.in"
BOARD = DarwinboxBoard(HOST)
LEGACY_URL = f"https://{HOST}/ms/candidate/careers"
LISTING_URL = BOARD.listing_url()


def _row(index: int, **overrides) -> dict:
    row = {
        "id": f"job{index:04d}",
        "title": f"Role {index}",
        "jd": f"&lt;p&gt;Description {index}&lt;/p&gt;",
        "tool_tip_locations": ["Pune, India"],
        "posted_on": 1_700_000_000,
        "is_remote": 0,
    }
    row.update(overrides)
    return row


class _Page:
    url = LISTING_URL

    async def goto(self, *_args, **_kwargs):
        return None


class _DocumentResponse:
    url = LISTING_URL

    class Request:
        resource_type = "document"

    request = Request()

    def __init__(self, headers: dict[str, str]):
        self._headers = headers

    async def all_headers(self) -> dict[str, str]:
        return self._headers


class _NavigationPage:
    url = LISTING_URL

    def __init__(self):
        self.listener = None

    def on(self, event: str, listener) -> None:
        assert event == "response"
        self.listener = listener

    def remove_listener(self, event: str, listener) -> None:
        assert event == "response"
        assert self.listener is listener
        self.listener = None


def _patch_browser(monkeypatch, pages: dict[int, dict]) -> list[int]:
    @asynccontextmanager
    async def open_page(_pw, _config, *, use_proxy, target_url=None):
        assert use_proxy is False
        yield _Page()

    async def prepare(_page, board, _config):
        assert board == BOARD

    requested: list[int] = []

    async def fetch(method, url, headers, body):
        assert method == "POST"
        assert url == BOARD.jobs_url()
        assert headers["x-requested-with"] == "XMLHttpRequest"
        payload = json.loads(body)
        assert payload["companyId"] == "main"
        assert payload["limit"] == 100
        page = payload["page"]
        requested.append(page)
        return pages[page]

    monkeypatch.setattr(darwinbox, "_open_darwinbox_page", open_page)
    monkeypatch.setattr(darwinbox, "_prepare_page", prepare)
    monkeypatch.setattr(darwinbox, "make_browser_fetcher", lambda _page: fetch)
    return requested


@pytest.mark.parametrize(
    "url,expected",
    [
        (LEGACY_URL, BOARD),
        (f"{LEGACY_URL}/", BOARD),
        (LISTING_URL, BOARD),
        (f"{LISTING_URL}/jobDetails/65e1eceedb514", BOARD),
        (
            "https://acme.darwinbox.com/ms/candidate/graduate/careers",
            DarwinboxBoard("acme.darwinbox.com", "graduate"),
        ),
    ],
)
def test_board_identity_accepts_public_unscoped_urls(url: str, expected: DarwinboxBoard):
    assert darwinbox_board_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://airtel.darwinbox.in/ms/candidate/careers",
        "https://darwinbox.in/ms/candidate/careers",
        "https://airtel.darwinbox.in.evil.test/ms/candidate/careers",
        "https://foo.airtel.darwinbox.in/ms/candidate/careers",
        "https://airtel.darwinbox.in:444/ms/candidate/careers",
        f"{LISTING_URL}?location=Pune",
        f"{LISTING_URL}#jobs",
        f"https://{HOST}/ms/candidateapi/job/alljobs",
        f"https://{HOST}/ms/candidatev2/main/careers/jobDetails/bad.id",
        f"https://{HOST}/ms/candidatev2/main/other",
    ],
)
def test_board_identity_rejects_unsafe_or_scoped_urls(url: str):
    assert darwinbox_board_from_url(url) is None


def test_metadata_identity_is_strict():
    assert darwinbox_board_from_metadata({"host": HOST}) == BOARD
    assert darwinbox_board_from_metadata({"host": HOST, "company_id": "graduate"}) == (
        DarwinboxBoard(HOST, "graduate")
    )
    assert darwinbox_board_from_metadata({"host": "darwinbox.in"}) is None
    assert darwinbox_board_from_metadata({"host": HOST, "company_id": "../main"}) is None


def test_parse_job_preserves_available_rich_fields():
    job = darwinbox._parse_job(
        _row(
            1,
            id="65e1eceedb514",
            jd="&lt;p class='x'&gt;Hello&lt;/p&gt;&lt;script&gt;bad()&lt;/script&gt;",
            tool_tip_locations=[" Pune, India ", "Pune, India", "Zurich, Switzerland"],
            emp_type_name=" Full Time ",
            is_remote=1,
            internal_job_code="REQ-42",
            department_name_only="Engineering",
            experience="5 years",
        ),
        BOARD,
    )

    assert job is not None
    assert job.url == f"{LISTING_URL}/jobDetails/65e1eceedb514"
    assert job.description == "<p>Hello</p>"
    assert job.locations == ["Pune, India", "Zurich, Switzerland"]
    assert job.employment_type == "Full Time"
    assert job.job_location_type == "remote"
    assert job.date_posted == "2023-11-14"
    assert job.metadata == {
        "id": "65e1eceedb514",
        "job_code": "REQ-42",
        "department": "Engineering",
        "experience": "5 years",
    }


def test_parse_job_accepts_missing_description_and_rejects_missing_identity():
    job = darwinbox._parse_job(_row(1, jd=""), BOARD)
    assert job is not None
    assert job.description is None
    assert darwinbox._parse_job(_row(2, id="bad.id"), BOARD) is None
    assert darwinbox._parse_job(_row(3, title=""), BOARD) is None


@pytest.mark.asyncio
async def test_navigation_honors_header_only_tdm_reservation(monkeypatch):
    from src.shared import browser

    page = _NavigationPage()

    async def navigate(target, url, config):
        assert target is page
        assert url == LISTING_URL
        assert config["wait"] == "domcontentloaded"
        assert page.listener is not None
        page.listener(_DocumentResponse({"tdm-reservation": "1"}))

    async def safe_content(target):
        assert target is page
        return "<html><body>Jobs</body></html>"

    monkeypatch.setattr(browser, "navigate", navigate)
    monkeypatch.setattr(browser, "safe_content", safe_content)

    with pytest.raises(TDMReservedError) as error:
        await darwinbox._prepare_page(page, BOARD, {})
    assert error.value.source == "header"
    assert page.listener is None


@pytest.mark.asyncio
async def test_streams_all_pages_without_materializing(monkeypatch):
    rows = [_row(index) for index in range(205)]
    pages = {
        1: {"status": "success", "data": rows[:100], "job_counts": 205},
        2: {"status": "success", "data": rows[100:200], "job_counts": 205},
        3: {"status": "success", "data": rows[200:], "job_counts": 205},
    }
    requested = _patch_browser(monkeypatch, pages)

    results = [
        result
        async for result in darwinbox.stream(
            {"board_url": LEGACY_URL, "metadata": {"host": HOST, "company_id": "main"}},
            MagicMock(),
            pw=object(),
        )
    ]

    assert requested == [1, 2, 3]
    assert [len(result.urls) for result in results] == [100, 100, 5]
    assert all(result.truncated is False for result in results)
    assert len(set().union(*(result.urls for result in results))) == 205


@pytest.mark.asyncio
async def test_zero_jobs_is_authoritative(monkeypatch):
    _patch_browser(
        monkeypatch,
        {1: {"status": "success", "data": [], "job_counts": 0}},
    )
    results = [
        result
        async for result in darwinbox.stream(
            {"board_url": LISTING_URL, "metadata": {}}, MagicMock(), pw=object()
        )
    ]
    assert len(results) == 1
    assert results[0].urls == set()
    assert results[0].truncated is False


@pytest.mark.asyncio
async def test_premature_partial_page_fails_instead_of_tombstoning(monkeypatch):
    _patch_browser(
        monkeypatch,
        {1: {"status": "success", "data": [_row(1)], "job_counts": 200}},
    )
    with pytest.raises(PaginationFetchError) as error:
        await discover({"board_url": LISTING_URL, "metadata": {}}, MagicMock(), pw=object())
    assert error.value.last_error == "PrematurePartialDarwinboxPage"


@pytest.mark.asyncio
async def test_duplicates_mark_run_truncated(monkeypatch):
    duplicate = _row(1)
    _patch_browser(
        monkeypatch,
        {1: {"status": "success", "data": [duplicate, duplicate], "job_counts": 2}},
    )
    result = await discover({"board_url": LISTING_URL, "metadata": {}}, MagicMock(), pw=object())
    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert result.jobs_by_url is not None
    assert len(result.jobs_by_url) == 1


@pytest.mark.asyncio
async def test_first_page_terminal_status_is_board_gone(monkeypatch):
    _patch_browser(monkeypatch, {})

    async def gone(*_args, **_kwargs):
        raise PaginationFetchError(BOARD.jobs_url(), attempts=1, last_status=404)

    monkeypatch.setattr(darwinbox, "_fetch_jobs_page", gone)
    with pytest.raises(darwinbox.BoardGoneError) as error:
        await discover({"board_url": LISTING_URL, "metadata": {}}, MagicMock(), pw=object())
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_monitor_requires_browser():
    with pytest.raises(RuntimeError, match="requires a Playwright browser"):
        await discover({"board_url": LISTING_URL, "metadata": {}}, MagicMock())


@pytest.mark.asyncio
async def test_can_handle_direct_and_explicitly_linked_urls():
    assert await can_handle(LEGACY_URL) == {"host": HOST, "company_id": "main"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=f'<a href="{LISTING_URL}">Jobs</a>',
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await can_handle("https://example.com/careers", client) == {
            "host": HOST,
            "company_id": "main",
        }


def test_runtime_and_workspace_registries_are_wired():
    assert "darwinbox" in all_monitor_types()
    assert get_stream_fn("darwinbox") is darwinbox.stream
    assert monitor_needs_browser("darwinbox") is True
    assert detect_ats_from_url(LEGACY_URL) == "darwinbox"
    assert detect_ats_from_url(f"{HOST}.evil.test/ms/candidate/careers") is None
    assert auto_scraper_type("darwinbox") == ("skip", None)
    assert "darwinbox" in MONITOR_CARDS
    assert "darwinbox" in _MONITOR_CONFIG_HINTS


def test_darwinbox_uses_host_level_pacing():
    metadata = {"host": HOST, "company_id": "main"}
    assert _compute_throttle_key("darwinbox", LEGACY_URL, metadata) == HOST

    board = MagicMock()
    values = {"crawler_type": "darwinbox", "board_url": LEGACY_URL, "metadata": metadata}
    board.__getitem__ = lambda _self, key: values[key]
    assert _throttle_key(board) == HOST
    assert delay_for_domain(HOST) < delay_for_domain("darwinbox.in.evil.test")
