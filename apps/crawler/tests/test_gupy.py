from __future__ import annotations

import json

import httpx
import pytest

from src.config import settings
from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, gupy
from src.core.monitors.gupy import can_handle, discover
from src.redis_queue import delay_for_domain
from src.shared.gupy import gupy_tenant_from_url
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

TENANT = "afya"
BOARD_URL = f"https://{TENANT}.gupy.io/"
JOB_ID = 11761084
JOB_URL = f"https://{TENANT}.gupy.io/jobs/{JOB_ID}"


def _page(
    *job_ids: int | str,
    tenant: str = TENANT,
    career_page: object = None,
    jobs: object = None,
) -> str:
    if career_page is None:
        career_page = {"name": "Example"}
    if jobs is None:
        jobs = [{"id": job_id, "title": f"Role {job_id}"} for job_id in job_ids]
    data = {
        "props": {
            "pageProps": {
                "subdomain": tenant,
                "careerPage": career_page,
                "jobs": jobs,
            }
        }
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(data)}"
        "</script></html>"
    )


class TestTenantAndUrls:
    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL,
            BOARD_URL.rstrip("/"),
            JOB_URL,
            f"{JOB_URL}?jobBoardSource=gupy_public_page",
        ],
    )
    def test_extracts_tenant_from_public_urls(self, url: str):
        assert gupy_tenant_from_url(url) == TENANT

    @pytest.mark.parametrize(
        "url",
        [
            f"http://{TENANT}.gupy.io/",
            "https://www.gupy.io/",
            "https://portal.gupy.io/",
            f"https://user@{TENANT}.gupy.io/",
            f"https://{TENANT}.gupy.io:444/",
            f"https://nested.{TENANT}.gupy.io/",
            f"https://{TENANT}.gupy.io/about",
            f"{BOARD_URL}?page=2",
            f"{JOB_URL}?jobBoardSource=other",
            f"https://{TENANT}.gupy.io.evil.test/",
        ],
    )
    def test_rejects_untrusted_or_scoped_urls(self, url: str):
        assert gupy_tenant_from_url(url) is None


class TestMonitor:
    async def test_reuses_nextdata_items_to_discover_canonical_urls(self):
        page = _page(JOB_ID, 11663915)
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, text=page, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {JOB_URL, f"https://{TENANT}.gupy.io/jobs/11663915"}
        assert requests == [BOARD_URL]

    async def test_metadata_tenant_override(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_page(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://example.com/jobs", "metadata": {"tenant": TENANT}},
                client,
            )

        assert result == set()
        assert seen == [BOARD_URL]

    async def test_empty_listing_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_page(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == set()

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_status_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": BOARD_URL}, client)

    @pytest.mark.parametrize("status", [204, 302, 400])
    async def test_unexpected_status_is_not_board_gone(self, status: int):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                headers={"location": "https://www.gupy.io/"} if status == 302 else {},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == status

    @pytest.mark.parametrize(
        ("page", "message"),
        [
            ("<html>Gupy service</html>", "NextData page props"),
            (_page(JOB_ID, tenant="other"), "mismatched NextData"),
            (_page(JOB_ID, career_page="invalid"), "career-page metadata"),
            (_page(jobs={"id": JOB_ID}), "jobs array"),
        ],
    )
    async def test_malformed_page_fails_not_empty(self, page: str, message: str):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match=message):
                await discover({"board_url": BOARD_URL}, client)

    async def test_bot_challenge_fails_not_empty(self):
        challenge = (
            "<html><title>Just a moment...</title>/cdn-cgi/challenge-platform/ "
            "enable javascript and cookies</html>"
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=challenge, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(RuntimeError, match="bot challenge"):
                await discover({"board_url": BOARD_URL}, client)

    @pytest.mark.parametrize("first_status", [202, 403, 429, 503])
    async def test_retries_transient_statuses(
        self,
        first_status: int,
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
                first_status if calls == 1 else 200,
                text="" if calls == 1 else _page(JOB_ID),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": BOARD_URL}, client) == {JOB_URL}
        assert calls == 2

    async def test_empty_200_retries_instead_of_tombstoning(
        self,
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
                200,
                text="" if calls == 1 else _page(JOB_ID),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": BOARD_URL}, client) == {JOB_URL}
        assert calls == 2

    @pytest.mark.parametrize(
        "jobs",
        [
            [{"id": JOB_ID}, {"id": JOB_ID}],
            [{"id": JOB_ID}, {"id": "bad/path"}],
            [{"title": "Missing ID"}],
        ],
    )
    async def test_invalid_or_duplicate_items_suppress_tombstoning(self, jobs: list[dict]):
        page = _page(jobs=jobs)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True

    async def test_html_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        page = _page(JOB_ID)
        monkeypatch.setattr(gupy, "MAX_HTML_CHARS", len(page))
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {JOB_URL}

    async def test_job_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(gupy, "MAX_JOBS", 1)
        page = _page(JOB_ID, 11663915)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {JOB_URL, f"https://{TENANT}.gupy.io/jobs/11663915"}


class TestDetection:
    async def test_direct_url_detects_without_client(self):
        assert await can_handle(BOARD_URL) == {"tenant": TENANT}

    async def test_detail_url_with_canonical_source_detects_without_client(self):
        assert await can_handle(f"{JOB_URL}?jobBoardSource=gupy_public_page") == {"tenant": TENANT}

    async def test_query_scoped_direct_url_is_not_widened(self):
        filtered = f"{BOARD_URL}?page=2"
        assert await can_handle(filtered) is None
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(filtered, client) is None

    async def test_direct_url_verifies_job_count(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_page(JOB_ID, 11663915),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) == {"tenant": TENANT, "jobs": 2}

    async def test_embedded_link_is_detected_and_verified(self):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.host == "example.com":
                return httpx.Response(
                    200,
                    text=f'<a href="{JOB_URL}">Open role</a>',
                    request=request,
                )
            return httpx.Response(200, text=_page(JOB_ID), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) == {
                "tenant": TENANT,
                "jobs": 1,
            }
        assert requests == ["https://example.com/careers", BOARD_URL]

    async def test_does_not_guess_tenant_from_company_domain(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="<html>No ATS link</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://afya.example/careers", client) is None
        assert requested == ["https://afya.example/careers"]


def test_runtime_and_workspace_integration():
    assert "gupy" in all_monitor_types()
    assert detect_ats_from_url(BOARD_URL) == "gupy"
    assert detect_ats_from_url(f"{BOARD_URL}?page=2") is None
    assert detect_ats_from_url(f"http://{TENANT}.gupy.io/") is None
    assert detect_ats_from_url("https://portal.gupy.io/") is None
    assert detect_ats_from_url(f"https://{TENANT}.gupy.io:bad/") is None
    assert auto_scraper_type("gupy") == ("json-ld", None)
    assert "gupy" in MONITOR_CARDS
    assert "gupy" in _MONITOR_CONFIG_HINTS
    assert delay_for_domain(f"{TENANT}.gupy.io") == settings.throttle_delay_ats
    assert delay_for_domain("gupy.io.evil.test") == settings.throttle_delay_default


def test_career_discovery_finds_listing_and_detail_links():
    html = f'<a href="{BOARD_URL}">Jobs</a><a href="{JOB_URL}">Role</a>'
    candidates = _scan_ats_urls_in_html(html)
    assert [candidate.url for candidate in candidates] == [BOARD_URL, JOB_URL]


def test_career_discovery_rejects_query_scoped_paths():
    html = f'<a href="{BOARD_URL}?page=2">Scoped</a>'
    assert _scan_ats_urls_in_html(html) == []
