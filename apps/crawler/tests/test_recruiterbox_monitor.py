from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, recruiterbox
from src.core.monitors.recruiterbox import can_handle, discover
from src.probe_boards import probe_row
from src.redis_queue import delay_for_domain
from src.shared.http_retry import PaginationFetchError
from src.shared.recruiterbox import (
    RecruiterboxBoard,
    recruiterbox_board_from_metadata,
    recruiterbox_board_from_url,
    recruiterbox_inactive_from_html,
    recruiterbox_job_token,
    recruiterbox_total_from_html,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

TENANT = "acme-jobs"
LEGACY_URL = f"https://{TENANT}.recruiterbox.com/"
BOARD_URL = f"https://{TENANT}.hire.trakstar.com/"
JOB_URL = f"https://{TENANT}.hire.trakstar.com/jobs/abc123/"


def _token(index: int) -> str:
    return f"job{index:04d}"


def _listing(*tokens: str, total: int | None = None, tenant: str = TENANT) -> str:
    advertised_total = len(tokens) if total is None else total
    links = "".join(f'<a class="job" href="/jobs/{token}/">Role</a>' for token in tokens)
    return (
        "<html><head><title>Careers | Trakstar Hire</title></head><body>"
        f"<script>RB.init_data({{total_jobs: {advertised_total}}});</script>"
        f"{links}</body></html>"
    )


def _inactive_page() -> str:
    return (
        '<html><link rel="canonical" href="https://recruiterbox.com/inactive-ats">'
        "<h3>Inactive account.</h3>"
        "<p>This employer is no longer using Trakstar Hire to collect applications.</p>"
        "</html>"
    )


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "company_slug": "acme",
        "board_slug": "acme-recruiterbox",
        "board_url": LEGACY_URL,
        "monitor_type": "recruiterbox",
        "monitor_config": "",
        "scraper_type": "json-ld",
        "scraper_config": "",
    }
    row.update(overrides)
    return row


class TestIdentity:
    @pytest.mark.parametrize(
        "url",
        [
            LEGACY_URL,
            f"{LEGACY_URL}jobs",
            f"{LEGACY_URL}?limit=100&p=2",
            BOARD_URL,
            JOB_URL,
            f"{JOB_URL}?source=careers",
        ],
    )
    def test_accepts_public_urls_and_canonicalizes(self, url: str):
        board = recruiterbox_board_from_url(url)
        assert board == RecruiterboxBoard(TENANT)
        assert board is not None
        assert board.listing_url() == BOARD_URL

    @pytest.mark.parametrize(
        "url",
        [
            f"http://{TENANT}.hire.trakstar.com/",
            f"https://user@{TENANT}.hire.trakstar.com/",
            f"https://{TENANT}.hire.trakstar.com:444/",
            f"https://nested.{TENANT}.hire.trakstar.com/",
            "https://hire.trakstar.com/",
            "https://www.hire.trakstar.com/",
            f"{BOARD_URL}admin",
            f"{BOARD_URL}?department=engineering",
            f"{BOARD_URL}?p=2&p=3",
            f"{BOARD_URL}?p=0",
            f"{JOB_URL}?source=",
            f"{JOB_URL}#role",
            f"https://{TENANT}.hire.trakstar.com.evil.test/",
        ],
    )
    def test_rejects_untrusted_or_scoped_urls(self, url: str):
        assert recruiterbox_board_from_url(url) is None

    def test_metadata_and_job_identity(self):
        board = recruiterbox_board_from_metadata({"tenant": f" {TENANT.upper()} "})
        assert board == RecruiterboxBoard(TENANT)
        assert board is not None
        assert board.page_url(2) == f"{BOARD_URL}?limit=100&p=2"
        assert board.job_url("ABC123") == JOB_URL
        assert recruiterbox_job_token(f"{JOB_URL}?source=careers", board) == "abc123"
        assert recruiterbox_job_token("https://other.hire.trakstar.com/jobs/abc123/", board) is None

    def test_total_parser_distinguishes_empty_from_invalid(self):
        assert recruiterbox_total_from_html(_listing(total=132)) == 132
        assert recruiterbox_total_from_html('{"total_jobs": "7"}') == 7
        assert recruiterbox_total_from_html(_inactive_page()) is None
        assert recruiterbox_total_from_html("<html>unrelated page</html>") is None
        assert recruiterbox_inactive_from_html(_inactive_page()) is True
        assert recruiterbox_inactive_from_html(_listing(total=0)) is False


class TestMonitor:
    async def test_paginates_at_one_hundred_and_canonicalizes_legacy(self):
        first_tokens = tuple(_token(index) for index in range(100))
        second_tokens = tuple(_token(index) for index in range(100, 132))
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            page = request.url.params.get("p")
            tokens = first_tokens if page == "1" else second_tokens
            return httpx.Response(
                200,
                text=_listing(*tokens, total=132),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": LEGACY_URL}, client)

        assert isinstance(result, set)
        assert len(result) == 132
        assert all(url.startswith(BOARD_URL) for url in result)
        assert requests == [
            f"{BOARD_URL}?limit=100&p=1",
            f"{BOARD_URL}?limit=100&p=2",
        ]

    async def test_metadata_tenant_overrides_unrelated_board_url(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_listing(total=0), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://example.com/jobs", "metadata": {"tenant": TENANT}},
                client,
            )

        assert result == set()
        assert seen == [f"{BOARD_URL}?limit=100&p=1"]

    async def test_active_empty_listing_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(total=0), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == set()

    async def test_inactive_account_is_board_gone(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_inactive_page(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError, match="inactive"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_nonzero_total_without_links_fails_not_empty(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(total=1), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="exposed no valid detail links"):
                await discover({"board_url": BOARD_URL}, client)

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_first_page_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": BOARD_URL}, client)

    async def test_nonterminal_4xx_fails_instead_of_marking_board_gone(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(400, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == 400

    async def test_redirect_is_not_followed(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(
                302,
                headers={"location": "https://other.hire.trakstar.com/"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == 302
        assert requested == [f"{BOARD_URL}?limit=100&p=1"]

    @pytest.mark.parametrize("status", [202, 401, 403, 429, 503])
    async def test_retries_transient_statuses(
        self,
        status: int,
        monkeypatch: pytest.MonkeyPatch,
    ):
        calls = 0

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", no_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                status if calls == 1 else 200,
                text="" if calls == 1 else _listing("abc123"),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": BOARD_URL}, client) == {JOB_URL}
        assert calls == 2

    async def test_malformed_page_fails_not_empty(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>not a listing</html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="omitted a valid job total"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_missing_later_page_suppresses_removals(self):
        first_tokens = tuple(_token(index) for index in range(100))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("p") == "2":
                return httpx.Response(404, request=request)
            return httpx.Response(200, text=_listing(*first_tokens, total=101), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 100

    async def test_count_drift_suppresses_removals(self):
        first_tokens = tuple(_token(index) for index in range(100))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("p") == "2":
                return httpx.Response(200, text=_listing(_token(100), total=102), request=request)
            return httpx.Response(200, text=_listing(*first_tokens, total=101), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 101

    async def test_duplicate_page_suppresses_removals(self):
        first_tokens = tuple(_token(index) for index in range(100))

        def handler(request: httpx.Request) -> httpx.Response:
            tokens = (_token(0),) if request.url.params.get("p") == "2" else first_tokens
            return httpx.Response(200, text=_listing(*tokens, total=101), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 100

    async def test_job_cap_suppresses_removals(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(recruiterbox, "MAX_JOBS", 100)
        monkeypatch.setattr(recruiterbox, "MAX_PAGES", 1)
        tokens = tuple(_token(index) for index in range(100))
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(*tokens, total=101), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 100

    async def test_html_cap_suppresses_removals(self, monkeypatch: pytest.MonkeyPatch):
        page = _listing("abc123") + "padding"
        monkeypatch.setattr(recruiterbox, "MAX_HTML_CHARS", len(page) - 1)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {JOB_URL}

    async def test_empty_html_cap_fails_instead_of_reaching_empty_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        page = _listing(total=0) + "padding"
        monkeypatch.setattr(recruiterbox, "MAX_HTML_CHARS", len(page) - 1)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="incomplete empty listing"):
                await discover({"board_url": BOARD_URL}, client)


class TestDetection:
    async def test_direct_url_detects_without_network(self):
        assert await can_handle(LEGACY_URL) == {"tenant": TENANT}
        assert await can_handle(JOB_URL) == {"tenant": TENANT}

    async def test_direct_url_is_verified_and_counted(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing("abc123", total=12), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(LEGACY_URL, client) == {
                "tenant": TENANT,
                "jobs": 12,
            }

    async def test_explicit_embedded_link_is_detected(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.host == "example.com":
                return httpx.Response(
                    200,
                    text=f'<a href="{JOB_URL}">Open role</a>',
                    request=request,
                )
            return httpx.Response(200, text=_listing("abc123"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) == {
                "tenant": TENANT,
                "jobs": 1,
            }
        assert requested == [
            "https://example.com/careers",
            f"{BOARD_URL}?limit=100&p=1",
        ]

    async def test_does_not_blindly_guess_tenant(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="<html>No ATS link</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://acme.example/careers", client) is None
        assert requested == ["https://acme.example/careers"]


class TestIntegration:
    def test_runtime_workspace_and_throttle_registration(self):
        assert "recruiterbox" in all_monitor_types()
        assert detect_ats_from_url(LEGACY_URL) == "recruiterbox"
        assert detect_ats_from_url(JOB_URL) == "recruiterbox"
        assert auto_scraper_type("recruiterbox") == ("recruiterbox", None)
        assert "recruiterbox" in MONITOR_CARDS
        assert "recruiterbox" in _MONITOR_CONFIG_HINTS
        assert delay_for_domain(f"{TENANT}.recruiterbox.com") == delay_for_domain(
            f"{TENANT}.hire.trakstar.com"
        )
        assert delay_for_domain("hire.trakstar.com.evil.test") != delay_for_domain(
            f"{TENANT}.hire.trakstar.com"
        )

    def test_career_discovery_finds_listing_and_detail_links(self):
        html = f'<a href="{LEGACY_URL}">Jobs</a><a href="{JOB_URL}">Role</a>'
        candidates = _scan_ats_urls_in_html(html)
        assert [candidate.url for candidate in candidates] == [LEGACY_URL, JOB_URL]

    async def test_board_probe_uses_canonical_listing_and_total(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text=_listing(total=0), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await probe_row(_row(), client)
        assert result.status == "ok"
        assert result.message == "200 (0 jobs)"
        assert requested == [f"{BOARD_URL}?limit=100&p=1"]

    async def test_board_probe_accepts_metadata_tenant(self):
        row = _row(
            board_url="https://example.com/jobs",
            monitor_config=json.dumps({"tenant": TENANT}),
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(total=0), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(row, client)
        assert result.status == "ok"

    async def test_board_probe_warns_on_malformed_200(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="not a listing", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(_row(), client)
        assert result.status == "warn"
        assert "marker" in result.message

    async def test_board_probe_fails_inactive_account(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_inactive_page(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(_row(), client)
        assert result.status == "fail"
        assert "inactive" in result.message
