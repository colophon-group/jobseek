from __future__ import annotations

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, herp
from src.core.monitors.herp import _slug_from_url, can_handle, discover
from src.redis_queue import _KNOWN_ATS_DOMAINS
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

SLUG = "a244"
BOARD_URL = f"https://herp.careers/v1/{SLUG}"
JOB_ID = "Q8BB49VqptS0"
JOB_URL = f"{BOARD_URL}/{JOB_ID}"


def _listing(*job_ids: str) -> str:
    links = "".join(f'<a href="/v1/{SLUG}/{job_id}">Role</a>' for job_id in job_ids)
    return f'<html><div class="requisition-list">{links}</div></html>'


class TestSlugAndUrls:
    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL,
            f"{BOARD_URL}/",
            JOB_URL,
            "https://herp.careers/v1/company_slug/job-id_123",
        ],
    )
    def test_extracts_slug_from_public_urls(self, url: str):
        expected = "company_slug" if "company_slug" in url else SLUG
        assert _slug_from_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            f"http://herp.careers/v1/{SLUG}",
            f"https://www.herp.careers/v1/{SLUG}",
            f"https://user@herp.careers/v1/{SLUG}",
            f"https://herp.careers:444/v1/{SLUG}",
            "https://herp.careers/careers",
            f"{BOARD_URL}/requisition-groups/group-id",
            f"{BOARD_URL}/short",
            f"{BOARD_URL}?group=engineering",
            f"https://herp.careers.evil.test/v1/{SLUG}",
        ],
    )
    def test_rejects_untrusted_or_non_board_urls(self, url: str):
        assert _slug_from_url(url) is None


class TestMonitor:
    async def test_discovers_canonical_urls_via_shared_dom_extractor(self):
        page = _listing(JOB_ID, "vzmK31n19VfH").replace(
            "</div>",
            (
                f'<a href="{JOB_URL}?ref=duplicate">Duplicate</a>'
                '<a href="/v1/a244/requisition-groups/group-id">Group</a>'
                '<a href="/v1/other/Foreign123">Foreign</a></div>'
            ),
        )
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, text=page, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {JOB_URL, f"{BOARD_URL}/vzmK31n19VfH"}
        assert requests == [BOARD_URL]

    async def test_metadata_slug_override(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_listing(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://example.com/jobs", "metadata": {"slug": SLUG}},
                client,
            )

        assert result == set()
        assert seen == [BOARD_URL]

    async def test_empty_listing_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(), request=request)
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
                headers={"location": "https://herp.co.jp/"} if status == 302 else {},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == status

    async def test_malformed_page_fails_not_empty(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>HERP service</html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="non-listing"):
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
            if calls == 1:
                return httpx.Response(first_status, request=request)
            return httpx.Response(200, text=_listing(JOB_ID), request=request)

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
            text = "" if calls == 1 else _listing(JOB_ID)
            return httpx.Response(200, text=text, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": BOARD_URL}, client) == {JOB_URL}
        assert calls == 2

    async def test_html_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        page = _listing(JOB_ID)
        monkeypatch.setattr(herp, "MAX_HTML_CHARS", len(page))
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {JOB_URL}

    async def test_job_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(herp, "MAX_JOBS", 1)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing(JOB_ID, "vzmK31n19VfH"),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 1


class TestDetection:
    async def test_direct_url_detects_without_client(self):
        assert await can_handle(BOARD_URL) == {"slug": SLUG}

    async def test_query_scoped_direct_url_is_not_widened(self):
        filtered = f"{BOARD_URL}?group=engineering"
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
                text=_listing(JOB_ID, "vzmK31n19VfH"),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) == {"slug": SLUG, "jobs": 2}

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
            return httpx.Response(200, text=_listing(JOB_ID), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) == {
                "slug": SLUG,
                "jobs": 1,
            }
        assert requests == ["https://example.com/careers", BOARD_URL]

    async def test_does_not_guess_slug_from_company_domain(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="<html>No ATS link</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://a244.example/careers", client) is None
        assert requested == ["https://a244.example/careers"]


def test_runtime_and_workspace_integration():
    assert "herp" in all_monitor_types()
    assert "herp.careers" in _KNOWN_ATS_DOMAINS
    assert detect_ats_from_url(BOARD_URL) == "herp"
    assert detect_ats_from_url(f"{BOARD_URL}?group=engineering") is None
    assert auto_scraper_type("herp") == ("json-ld", None)
    assert "herp" in MONITOR_CARDS
    assert "herp" in _MONITOR_CONFIG_HINTS


def test_career_discovery_finds_listing_and_detail_links():
    html = f'<a href="{BOARD_URL}">Jobs</a><a href="{JOB_URL}">Role</a>'
    candidates = _scan_ats_urls_in_html(html)
    assert [candidate.url for candidate in candidates] == [BOARD_URL, JOB_URL]


def test_career_discovery_rejects_group_and_query_scoped_paths():
    html = (
        f'<a href="{BOARD_URL}/requisition-groups/group-id">Group</a>'
        f'<a href="{BOARD_URL}?group=engineering">Scoped</a>'
    )
    assert _scan_ats_urls_in_html(html) == []
