from __future__ import annotations

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, hrmos
from src.core.monitors.hrmos import _page_metadata, _tenant_from_url, can_handle, discover
from src.redis_queue import _KNOWN_ATS_DOMAINS
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

TENANT = "a-tech"
BOARD_URL = f"https://hrmos.co/pages/{TENANT}/jobs"
JOB_ID = "0000001"
JOB_URL = f"{BOARD_URL}/{JOB_ID}"


def _listing(
    *job_ids: str,
    total: int | None = None,
    page: int = 1,
    linked_pages: int = 1,
    tenant: str = TENANT,
) -> str:
    total = len(job_ids) if total is None else total
    links = "".join(
        f'<a href="https://hrmos.co/pages/{tenant}/jobs/{job_id}">Role</a>' for job_id in job_ids
    )
    pagination = "".join(
        (
            f'<li class="sg-button current"> {value} </li>'
            if value == page
            else f'<li><a href="https://hrmos.co/pages/{tenant}/jobs?page={value}">{value}</a></li>'
        )
        for value in range(1, linked_pages + 1)
    )
    return (
        '<html><section id="jsi-joblist">'
        f"<ul>{links}</ul>"
        f"<p>全 {total:,} 件中 {len(job_ids)} 件 を表示しています</p>"
        f"<nav>{pagination}</nav>"
        "</section></html>"
    )


def _empty_listing(*, tenant: str = TENANT) -> str:
    return (
        "<html><head>"
        f'<link rel="canonical" href="https://hrmos.co/pages/{tenant}/jobs">'
        "</head><body>"
        '<section class="sg-wrapper sg-unavailable-notifier">'
        f"<h2>公開されている {tenant} の求人はありません。</h2>"
        "</section></body></html>"
    )


def _listing_with_unavailable_template(*job_ids: str, total: int) -> str:
    return _listing(*job_ids, total=total).replace(
        "</html>",
        '<section class="sg-unavailable-notifier" hidden></section></html>',
    )


class TestTenantAndUrls:
    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL,
            f"{BOARD_URL}/",
            JOB_URL,
            "https://hrmos.co/pages/123456789/jobs/job-id_123",
        ],
    )
    def test_extracts_tenant_from_public_urls(self, url: str):
        expected = "123456789" if "123456789" in url else TENANT
        assert _tenant_from_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            f"http://hrmos.co/pages/{TENANT}/jobs",
            f"https://www.hrmos.co/pages/{TENANT}/jobs",
            f"https://user@hrmos.co/pages/{TENANT}/jobs",
            f"https://hrmos.co:444/pages/{TENANT}/jobs",
            "https://hrmos.co/pages",
            f"{BOARD_URL}/groups/engineering",
            f"{BOARD_URL}?page=2",
            f"{BOARD_URL}?category=engineering",
            f"https://hrmos.co.evil.test/pages/{TENANT}/jobs",
        ],
    )
    def test_rejects_untrusted_or_scoped_urls(self, url: str):
        assert _tenant_from_url(url) is None

    def test_page_metadata_parses_counts_and_navigation(self):
        page = _listing("one", "two", total=1_234, page=2, linked_pages=13)
        assert _page_metadata(page) == (1234, 2, 2, 13)


class TestMonitor:
    async def test_discovers_all_advertised_pages(self):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            requests.append(url)
            page = (
                _listing("job-one", "job-two", total=3, page=1, linked_pages=2)
                if url == BOARD_URL
                else _listing("job-three", total=3, page=2, linked_pages=2)
            )
            return httpx.Response(200, text=page, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {
            f"{BOARD_URL}/job-one",
            f"{BOARD_URL}/job-two",
            f"{BOARD_URL}/job-three",
        }
        assert requests == [BOARD_URL, f"{BOARD_URL}?page=2"]

    async def test_canonicalizes_and_filters_links(self):
        page = _listing(JOB_ID).replace(
            "</ul>",
            (
                f'<a href="{JOB_URL}?ref=duplicate">Duplicate</a>'
                f'<a href="https://hrmos.co/pages/other/jobs/{JOB_ID}">Foreign</a>'
                '<a href="https://hrmos.co/terms/">Terms</a></ul>'
            ),
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == {JOB_URL}

    async def test_metadata_tenant_override(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_listing(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://example.com/jobs", "metadata": {"tenant": TENANT}},
                client,
            )

        assert result == set()
        assert seen == [BOARD_URL]

    async def test_empty_listing_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(total=0), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == set()

    async def test_explicit_unavailable_page_is_authoritative_empty(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_empty_listing(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == set()

    async def test_hidden_unavailable_template_does_not_override_jobs(self):
        page = _listing_with_unavailable_template(JOB_ID, total=1)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == {JOB_URL}

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_first_page_status_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.status_code == status
        assert exc_info.value.url == BOARD_URL

    async def test_terminal_later_page_status_is_transient_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.query:
                return httpx.Response(404, request=request)
            return httpx.Response(
                200,
                text=_listing("job-one", total=2, linked_pages=2),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == 404

    @pytest.mark.parametrize("status", [204, 302, 400])
    async def test_unexpected_status_is_not_board_gone(self, status: int):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                headers={"location": "https://hrmos.co/"} if status == 302 else {},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == status

    async def test_malformed_page_fails_not_empty(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>HRMOS service</html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="non-listing"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_missing_count_fails_not_empty(self):
        page = '<html><section id="jsi-joblist"></section></html>'
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="count marker"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_bot_challenge_fails_not_empty(self):
        challenge = (
            '<html><section id="jsi-joblist"></section><title>Just a moment...</title>'
            "/cdn-cgi/challenge-platform/ enable javascript and cookies</html>"
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
            text = _listing(JOB_ID) if calls > 1 else ""
            return httpx.Response(first_status if calls == 1 else 200, text=text, request=request)

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
                text="" if calls == 1 else _listing(JOB_ID),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": BOARD_URL}, client) == {JOB_URL}
        assert calls == 2

    async def test_wrong_page_number_fails(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=(
                    _listing("job-one", total=2, page=1, linked_pages=2)
                    if not request.url.query
                    else _listing("job-two", total=2, page=3, linked_pages=3)
                ),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="returned page 3 for page 2"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_empty_advertised_page_fails(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=(
                    _listing("job-one", total=2, linked_pages=2)
                    if not request.url.query
                    else _listing(total=2, page=2, linked_pages=2)
                ),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="empty advertised page 2"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_count_drift_suppresses_tombstoning(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page = (
                _listing("job-one", total=2, linked_pages=2)
                if not request.url.query
                else _listing("job-two", total=3, page=2, linked_pages=2)
            )
            return httpx.Response(200, text=page, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {f"{BOARD_URL}/job-one", f"{BOARD_URL}/job-two"}

    async def test_duplicate_page_suppresses_tombstoning(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing(
                    "job-one",
                    total=2,
                    page=2 if request.url.query else 1,
                    linked_pages=2,
                ),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {f"{BOARD_URL}/job-one"}

    async def test_html_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        page = _listing(JOB_ID)
        monkeypatch.setattr(hrmos, "MAX_HTML_CHARS", len(page))
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {JOB_URL}

    async def test_page_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(hrmos, "MAX_PAGES", 1)
        page = _listing("job-one", total=2, linked_pages=2)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {f"{BOARD_URL}/job-one"}


class TestDetection:
    async def test_direct_url_detects_without_client(self):
        assert await can_handle(BOARD_URL) == {"tenant": TENANT}

    async def test_query_scoped_direct_url_is_not_widened(self):
        filtered = f"{BOARD_URL}?category=engineering"
        assert await can_handle(filtered) is None
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(filtered, client) is None

    async def test_direct_url_verifies_total_job_count(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing("job-one", "job-two", total=123, linked_pages=2),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) == {"tenant": TENANT, "jobs": 123}

    async def test_direct_url_verifies_explicit_empty_listing(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_empty_listing(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) == {"tenant": TENANT, "jobs": 0}

    async def test_direct_url_prefers_listing_over_unavailable_template(self):
        page = _listing_with_unavailable_template(JOB_ID, total=1)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) == {"tenant": TENANT, "jobs": 1}

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
            assert await can_handle("https://a-tech.example/careers", client) is None
        assert requested == ["https://a-tech.example/careers"]


def test_runtime_and_workspace_integration():
    assert "hrmos" in all_monitor_types()
    assert "hrmos.co" in _KNOWN_ATS_DOMAINS
    assert detect_ats_from_url(BOARD_URL) == "hrmos"
    assert detect_ats_from_url(f"{BOARD_URL}?page=2") is None
    assert auto_scraper_type("hrmos") == ("json-ld", None)
    assert "hrmos" in MONITOR_CARDS
    assert "hrmos" in _MONITOR_CONFIG_HINTS


def test_career_discovery_finds_listing_and_detail_links():
    html = f'<a href="{BOARD_URL}">Jobs</a><a href="{JOB_URL}">Role</a>'
    candidates = _scan_ats_urls_in_html(html)
    assert [candidate.url for candidate in candidates] == [BOARD_URL, JOB_URL]


def test_career_discovery_rejects_query_scoped_paths():
    html = f'<a href="{BOARD_URL}?category=engineering">Scoped</a>'
    assert _scan_ats_urls_in_html(html) == []
