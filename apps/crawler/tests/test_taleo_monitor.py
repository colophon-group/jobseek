from __future__ import annotations

import html
import json

import httpx
import pytest

import src.core.monitors.taleo as taleo_monitor
from src.core.monitors import BoardGoneError, all_monitor_types
from src.core.monitors.taleo import can_handle, discover
from src.probe_boards import probe_row
from src.redis_queue import delay_for_domain
from src.shared.http_retry import PaginationFetchError
from src.shared.taleo import (
    TaleoBoard,
    taleo_board_from_metadata,
    taleo_board_from_url,
    taleo_inactive_redirect,
    taleo_next_offset_from_html,
    taleo_requisition_id,
    taleo_safe_redirect,
    taleo_total_from_html,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

BOARD = TaleoBoard("phe.tbe.taleo.net", "phe01", "ACME", 1)
LISTING_URL = BOARD.listing_url()
DETAIL_URL = BOARD.job_url(123)
FINAL_BOARD = TaleoBoard("phh.tbe.taleo.net", "phh01", "ACME", 41)
DISPATCHER_URL = (
    "https://phe.tbe.taleo.net/dispatcher/servlet/DispatcherServlet?"
    "org=ACME&act=redirectCws&redirectUrl="
    "https%3A%2F%2Fphe.tbe.taleo.net%2Fphe01%2Fats%2Fcareers%2Fv2%2F"
    "searchResults%3Forg%3DACME%26cws%3D1"
)


def _config(board: TaleoBoard = BOARD) -> dict[str, object]:
    return {
        "host": board.host,
        "partition": board.partition,
        "org": board.org,
        "cws": board.cws,
    }


def _listing(total: int, requisition_ids: list[int], board: TaleoBoard = BOARD) -> str:
    links = "".join(
        f'<a href="{html.escape(board.job_url(requisition_id))}">Job</a>'
        for requisition_id in requisition_ids
    )
    return (
        '<div class="panel oracletaleocwsv2-totals-callout">'
        '<h3 aria-label="Open Positions">Postes ouverts</h3>'
        f'<span tabindex="0" class="oracletaleocwsv2-panel-number">{total}</span>'
        f"</div>{links}"
    )


def _cursor_listing(
    requisition_ids: list[int],
    *,
    row_from: int = 0,
    next_offset: int | None = None,
    board: TaleoBoard = BOARD,
) -> str:
    links = "".join(
        f'<a href="{html.escape(board.job_url(requisition_id))}">Job</a>'
        for requisition_id in requisition_ids
    )
    next_link = ""
    if next_offset is not None:
        next_link = (
            f'<a class="jscroll-next" href="/{board.partition}/ats/careers/v2/'
            f"searchResults?next&amp;rowFrom={next_offset}&amp;act=null&amp;"
            'sortColumn=null&amp;sortOrder=null&amp;currentTime=1785797000000">next</a>'
        )
    return (
        '<section class="oracletaleocwsv2-search-results">'
        f'<div data-row-from="{row_from}">{links}</div>{next_link}</section>'
    )


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "company_slug": "acme",
        "board_slug": "acme-taleo",
        "board_url": LISTING_URL,
        "monitor_type": "taleo",
        "monitor_config": json.dumps(_config()),
        "scraper_type": "json-ld",
        "scraper_config": "",
    }
    row.update(overrides)
    return row


class TestIdentity:
    @pytest.mark.parametrize(
        "url",
        [
            LISTING_URL,
            f"{LISTING_URL}&rowFrom=20",
            DETAIL_URL,
            "https://phe.tbe.taleo.net/phe01/ats/careers/v2/searchResults/?cws=1&org=acme",
        ],
    )
    def test_accepts_public_urls(self, url: str):
        assert taleo_board_from_url(url) == BOARD

    @pytest.mark.parametrize(
        "url",
        [
            LISTING_URL.replace("https://", "http://"),
            LISTING_URL.replace("https://", "https://user@"),
            LISTING_URL.replace("taleo.net", "taleo.net:444"),
            LISTING_URL.replace("taleo.net", "taleo.net.evil.test"),
            LISTING_URL.replace("/phe01/", "/phg01/"),
            LISTING_URL + "&org=OTHER",
            LISTING_URL + "&department=engineering",
            LISTING_URL + "&rowFrom=1",
            LISTING_URL.replace("cws=1", "cws=0"),
            LISTING_URL.replace("searchResults", "searchResults/extra"),
            DETAIL_URL + "&rid=456",
        ],
    )
    def test_rejects_untrusted_or_scoped_urls(self, url: str):
        assert taleo_board_from_url(url) is None

    def test_metadata_routes_and_requisition_identity(self):
        assert taleo_board_from_metadata(_config()) == BOARD
        assert BOARD.listing_url(row_from=10) == f"{LISTING_URL}&rowFrom=10"
        assert taleo_requisition_id(DETAIL_URL, BOARD) == 123
        assert taleo_requisition_id(FINAL_BOARD.job_url(123), BOARD) is None

    def test_total_parser_is_label_independent_and_rejects_conflicts(self):
        assert taleo_total_from_html(_listing(17, list(range(1, 11)))) == 17
        assert (
            taleo_total_from_html('<span class="oracletaleocwsv2-panel-number">1,234</span>')
            == 1234
        )
        assert (
            taleo_total_from_html(
                '<span class="oracletaleocwsv2-panel-number">3</span>'
                '<span class="oracletaleocwsv2-panel-number">4</span>'
            )
            is None
        )
        assert taleo_total_from_html("<html>not Taleo</html>") is None

    def test_accepts_only_same_org_official_redirects(self):
        assert taleo_safe_redirect(BOARD, LISTING_URL, FINAL_BOARD.listing_url()) == (
            FINAL_BOARD.listing_url(),
            FINAL_BOARD,
        )
        assert taleo_safe_redirect(BOARD, LISTING_URL, DISPATCHER_URL) == (
            DISPATCHER_URL,
            BOARD,
        )
        assert taleo_safe_redirect(BOARD, LISTING_URL, "https://evil.test/jobs") is None
        assert (
            taleo_safe_redirect(
                BOARD,
                LISTING_URL,
                FINAL_BOARD.listing_url().replace("org=ACME", "org=OTHER"),
            )
            is None
        )
        assert (
            taleo_safe_redirect(
                BOARD,
                LISTING_URL,
                DISPATCHER_URL.replace("org=ACME", "org=OTHER", 1),
            )
            is None
        )

    def test_validates_cursor_links_and_inactive_dispatcher(self):
        page = _cursor_listing(list(range(1, 11)), next_offset=10)
        assert taleo_next_offset_from_html(page, BOARD, current_offset=0) == 10
        assert taleo_next_offset_from_html(_cursor_listing([1]), BOARD, current_offset=0) is None
        assert taleo_inactive_redirect(
            BOARD,
            DISPATCHER_URL,
            "INACTIVEcareers/v2/searchResults?org=ACME&cws=1",
        )
        assert not taleo_inactive_redirect(
            BOARD,
            DISPATCHER_URL,
            "INACTIVEcareers/v2/searchResults?org=OTHER&cws=1",
        )


class TestMonitor:
    async def test_paginates_with_exact_page_completeness(self):
        first_ids = list(range(1, 11))
        second_ids = [11, 12]
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if str(request.url) == LISTING_URL:
                return httpx.Response(200, text=_listing(12, first_ids), request=request)
            assert str(request.url) == BOARD.listing_url(row_from=10)
            return httpx.Response(200, text=_listing(12, second_ids), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(
                {"board_url": LISTING_URL, "metadata": _config()},
                client,
            )

        assert urls == {BOARD.job_url(requisition_id) for requisition_id in range(1, 13)}
        assert requested == [LISTING_URL, BOARD.listing_url(row_from=10)]

    async def test_zero_job_board_is_valid(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(0, []), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": LISTING_URL}, client) == set()

    async def test_cursor_theme_paginates_without_a_total_callout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LISTING_URL:
                body = _cursor_listing(list(range(1, 11)), next_offset=10)
            else:
                assert str(request.url) == BOARD.listing_url(row_from=10)
                body = _cursor_listing([11, 12, 13], row_from=10)
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover({"board_url": LISTING_URL}, client)
        assert urls == {BOARD.job_url(requisition_id) for requisition_id in range(1, 14)}

    async def test_cursor_theme_empty_board_is_valid(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_cursor_listing([]),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": LISTING_URL}, client) == set()

    async def test_cursor_theme_empty_child_fails_whole_run(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = (
                _cursor_listing(list(range(1, 11)), next_offset=10)
                if str(request.url) == LISTING_URL
                else _cursor_listing([], row_from=10)
            )
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="empty cursor child"):
                await discover({"board_url": LISTING_URL}, client)

    async def test_unconfigured_discovery_follows_validated_migration(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if str(request.url) == LISTING_URL:
                return httpx.Response(
                    302,
                    headers={"location": DISPATCHER_URL},
                    request=request,
                )
            if str(request.url) == DISPATCHER_URL:
                return httpx.Response(
                    302,
                    headers={"location": FINAL_BOARD.listing_url()},
                    request=request,
                )
            assert str(request.url) == FINAL_BOARD.listing_url()
            return httpx.Response(200, text=_listing(1, [99], FINAL_BOARD), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover({"board_url": LISTING_URL}, client)
        assert urls == {FINAL_BOARD.job_url(99)}
        assert requested == [LISTING_URL, DISPATCHER_URL, FINAL_BOARD.listing_url()]

    async def test_configured_identity_fails_closed_on_redirect(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"location": FINAL_BOARD.listing_url()},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="redirected unexpectedly"):
                await discover(
                    {"board_url": LISTING_URL, "metadata": _config()},
                    client,
                )

    async def test_untrusted_redirect_fails_whole_run(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"location": "https://evil.test/jobs"},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="untrusted redirect"):
                await discover({"board_url": LISTING_URL}, client)

    async def test_dispatcher_inactive_signal_is_board_gone(self):
        def handler(request: httpx.Request) -> httpx.Response:
            location = (
                DISPATCHER_URL
                if str(request.url) == LISTING_URL
                else "INACTIVEcareers/v2/searchResults?org=ACME&cws=1"
            )
            return httpx.Response(302, headers={"location": location}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BoardGoneError, match="inactive"):
                await discover({"board_url": LISTING_URL}, client)

    async def test_root_not_found_is_board_gone(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(404, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": LISTING_URL}, client)

    async def test_child_not_found_is_transient_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LISTING_URL:
                return httpx.Response(
                    200,
                    text=_listing(11, list(range(1, 11))),
                    request=request,
                )
            return httpx.Response(404, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as raised:
                await discover({"board_url": LISTING_URL}, client)
        assert raised.value.last_status == 404

    async def test_retryable_child_failure_recovers(self):
        child_requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal child_requests
            if str(request.url) == LISTING_URL:
                return httpx.Response(
                    200,
                    text=_listing(11, list(range(1, 11))),
                    request=request,
                )
            child_requests += 1
            if child_requests == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(200, text=_listing(11, [11]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover({"board_url": LISTING_URL}, client)
        assert len(urls) == 11
        assert child_requests == 2

    @pytest.mark.parametrize(
        ("first", "second", "message"),
        [
            (_listing(2, [1]), None, "advertised 2 jobs"),
            (_listing(11, list(range(1, 11))), _listing(12, [11, 12]), "changed total"),
            (
                _listing(11, list(range(1, 11))),
                _listing(11, [10]),
                "repeated requisitions",
            ),
            ("<html>no total</html>", None, "omitted a valid listing marker"),
        ],
    )
    async def test_incomplete_or_inconsistent_pages_fail_whole_run(
        self,
        first: str,
        second: str | None,
        message: str,
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            body = first if str(request.url) == LISTING_URL else second
            assert body is not None
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match=message):
                await discover({"board_url": LISTING_URL}, client)

    async def test_job_cap_fails_before_pagination(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(taleo_monitor, "MAX_JOBS", 10)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing(11, list(range(1, 11))),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="safety cap"):
                await discover({"board_url": LISTING_URL}, client)

    @pytest.mark.parametrize(
        "page",
        [
            _cursor_listing(list(range(1, 10)), next_offset=10),
            _cursor_listing(list(range(1, 11)), next_offset=20),
            _cursor_listing(list(range(1, 11)), next_offset=10).replace(
                f"/{BOARD.partition}/",
                "/phg01/",
            ),
        ],
    )
    async def test_malformed_cursor_pages_fail_whole_run(self, page: str):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await discover({"board_url": LISTING_URL}, client)


class TestDetectionAndIntegration:
    async def test_direct_probe_resolves_migration(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == LISTING_URL:
                return httpx.Response(
                    302,
                    headers={"location": FINAL_BOARD.listing_url()},
                    request=request,
                )
            return httpx.Response(200, text=_listing(1, [7], FINAL_BOARD), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(LISTING_URL, client)
        assert result == {**_config(FINAL_BOARD), "jobs": 1}

    async def test_linked_board_detection_has_no_slug_guessing(self):
        career_url = "https://acme.example/careers"
        escaped_listing = html.escape(LISTING_URL)

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == career_url:
                return httpx.Response(
                    200,
                    text=f'<a href="{escaped_listing}">Jobs</a>',
                    request=request,
                )
            assert str(request.url) == LISTING_URL
            return httpx.Response(200, text=_listing(1, [1]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle(career_url, client) == {**_config(), "jobs": 1}

        calls = 0

        def no_board(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, text="<html>No ATS links</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(no_board)) as client:
            assert await can_handle(career_url, client) is None
        assert calls == 1

    async def test_static_integrations(self):
        assert await can_handle(LISTING_URL, None) == _config()
        assert "taleo" in all_monitor_types()
        assert detect_ats_from_url(LISTING_URL) == "taleo"
        assert detect_ats_from_url(DETAIL_URL) == "taleo"
        assert auto_scraper_type("taleo") == ("json-ld", None)
        assert "taleo" in MONITOR_CARDS
        assert "taleo" in _MONITOR_CONFIG_HINTS
        assert delay_for_domain("phe.tbe.taleo.net") < delay_for_domain("example.com")
        assert [link.url for link in _scan_ats_urls_in_html(f'<a href="{LISTING_URL}">')] == [
            LISTING_URL
        ]

    async def test_native_probe_validates_total(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(3, [1, 2, 3]), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(_row(), client)
        assert result.status == "ok"
        assert result.message == "200 (3 jobs)"

    async def test_native_probe_accepts_cursor_theme(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_cursor_listing(list(range(1, 11)), next_offset=10),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(_row(), client)
        assert result.status == "ok"
        assert result.message == "200 (cursor listing verified)"
