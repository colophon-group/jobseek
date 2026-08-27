from __future__ import annotations

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, icims
from src.core.monitors.icims import _host_from_url, can_handle, discover
from src.redis_queue import _KNOWN_ATS_DOMAINS
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

HOST = "careers-acme.icims.com"
BOARD_URL = f"https://{HOST}/jobs/search"
LISTING_URL = f"https://{HOST}/jobs/search?ss=1&in_iframe=1"


def _listing(
    *job_ids: int,
    page: int = 1,
    total: int = 1,
    include_page_links: bool = True,
) -> str:
    jobs = "".join(
        f'<a href="/jobs/{job_id}/role-{job_id}/job?in_iframe=1">Role</a>' for job_id in job_ids
    )
    pagination = ""
    if include_page_links and total > 1:
        pagination = "".join(
            f'<a href="/jobs/search?pr={index}&amp;in_iframe=1">{index + 1}</a>'
            for index in range(total)
        )
    return (
        '<html><body class="iCIMS_ListingsPage">'
        f"<span>Page {page} of {total}</span>{jobs}{pagination}</body></html>"
    )


def _card_listing(
    host: str,
    *jobs: tuple[int, str, str, str],
    page: int = 1,
    total: int = 1,
) -> str:
    cards = "".join(
        (
            '<li class="iCIMS_JobCardItem"><div class="row">'
            '<div class="col-xs-6 header left"><span class="sr-only">Region</span>'
            f'<span>{region}</span></div><div class="col-xs-12 title">'
            f'<a class="iCIMS_Anchor" href="https://{host}/jobs/{job_id}/role/job?in_iframe=1">'
            f'<h3>{title}</h3></a></div><div class="iCIMS_JobHeaderTag">'
            f"<dt>Job Type</dt><dd>{job_type}</dd></div></div></li>"
        )
        for job_id, title, region, job_type in jobs
    )
    pagination = "".join(
        f'<a href="/jobs/search?pr={index}&amp;in_iframe=1">{index + 1}</a>'
        for index in range(total)
    )
    return (
        '<html><body class="iCIMS_ListingsPage">'
        f'<span>Page {page} of {total}</span><ul class="iCIMS_JobsTable">{cards}</ul>'
        f"{pagination}</body></html>"
    )


def _job_url(job_id: int) -> str:
    return f"https://{HOST}/jobs/{job_id}/job?in_iframe=1"


class TestHostAndUrls:
    @pytest.mark.parametrize(
        "url",
        [
            f"https://{HOST}",
            f"https://{HOST}/jobs",
            BOARD_URL,
            f"https://{HOST}/jobs/123/software-engineer/job",
            f"https://{HOST}/jobs/123/job?in_iframe=1",
            f"https://{HOST}/jobs/search?ss=1&in_iframe=1",
        ],
    )
    def test_extracts_host_from_public_urls(self, url: str):
        assert _host_from_url(url) == HOST

    @pytest.mark.parametrize(
        "url",
        [
            f"http://{HOST}/jobs/search",
            "https://icims.com/jobs/search",
            "https://www.icims.com/jobs",
            f"https://nested.{HOST}/jobs/search",
            f"https://user@{HOST}/jobs/search",
            f"https://{HOST}:444/jobs/search",
            f"https://{HOST}/admin",
            f"https://{HOST}.evil.test/jobs/search",
            f"https://{HOST}/jobs/search?searchLocation=12781--EMEA",
            f"https://{HOST}/jobs/search?ss=1&ss=1",
            f"https://{HOST}/jobs/search?in_iframe=0",
        ],
    )
    def test_rejects_untrusted_or_non_board_urls(self, url: str):
        assert _host_from_url(url) is None


class TestMonitor:
    async def test_discovers_all_pages_and_canonicalizes_urls(self):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            page_index = int(request.url.params.get("pr", "0"))
            text = _listing(
                100 + page_index,
                page=page_index + 1,
                total=3,
            )
            if page_index == 0:
                text = text.replace(
                    "</body>",
                    (
                        f'<a href="https://other.icims.com/jobs/999/foreign/job">Other</a>'
                        f'<a href="https://{HOST}/jobs/100/another-title/job?ref=dup">Dup</a>'
                        "</body>"
                    ),
                )
            return httpx.Response(200, text=text, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {_job_url(100), _job_url(101), _job_url(102)}
        assert requests == [
            LISTING_URL,
            (
                f"https://{HOST}/jobs/search?pr=1&in_iframe=1"
                "&searchRelation=keyword_all&schemaId=&o="
            ),
            (
                f"https://{HOST}/jobs/search?pr=2&in_iframe=1"
                "&searchRelation=keyword_all&schemaId=&o="
            ),
        ]

    async def test_metadata_host_override(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_listing(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://example.com/jobs", "metadata": {"host": HOST}},
                client,
            )

        assert result == set()
        assert seen == [LISTING_URL]

    async def test_cross_locale_dedupe_uses_stable_listing_identity_and_title_aliases(self):
        peer_host = "peer-acme.icims.com"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == HOST:
                text = _card_listing(
                    HOST,
                    (100, "Gestionnaire en formation", "CA-QC-Laval", "Regular Full-Time"),
                    (101, "Unique French role", "CA-QC-Laval", "Regular Full-Time"),
                    (102, "Bilingual Representative", "CA-Remote", "Regular Full-Time"),
                )
            else:
                assert request.url.host == peer_host
                text = _card_listing(
                    peer_host,
                    (
                        200,
                        "Management Trainee - Bilingual",
                        "CA-QC-Laval",
                        "Regular Full-Time",
                    ),
                    (201, "Bilingual Representative", "CA-Remote", "Regular Full-Time"),
                )
            return httpx.Response(200, text=text, request=request)

        metadata = {
            "host": HOST,
            "cross_locale_dedupe": {
                "peer_host": peer_host,
                "title_aliases": {
                    "Gestionnaire en formation": "Management Trainee",
                    "Management Trainee - Bilingual": "Management Trainee",
                },
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL, "metadata": metadata}, client)

        assert result == {_job_url(101)}

    async def test_cross_locale_dedupe_consumes_peer_identity_counts(self):
        peer_host = "peer-acme.icims.com"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == HOST:
                text = _card_listing(
                    HOST,
                    (100, "Same role", "CA-QC-Laval", "Regular Full-Time"),
                    (101, "Same role", "CA-QC-Laval", "Regular Full-Time"),
                )
            else:
                text = _card_listing(
                    peer_host,
                    (200, "Same role", "CA-QC-Laval", "Regular Full-Time"),
                )
            return httpx.Response(200, text=text, request=request)

        metadata = {
            "host": HOST,
            "cross_locale_dedupe": {"peer_host": peer_host, "title_aliases": {}},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL, "metadata": metadata}, client)

        assert result == {_job_url(101)}

    async def test_cross_locale_dedupe_preserves_distinct_job_type(self):
        peer_host = "peer-acme.icims.com"

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            job_type = "Regular Part-Time" if host == HOST else "Regular Full-Time"
            return httpx.Response(
                200,
                text=_card_listing(host, (100, "Same title", "CA-QC-Laval", job_type)),
                request=request,
            )

        metadata = {
            "host": HOST,
            "cross_locale_dedupe": {"peer_host": peer_host, "title_aliases": {}},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL, "metadata": metadata}, client)

        assert result == {_job_url(100)}

    async def test_cross_locale_dedupe_fails_closed_when_peer_is_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        peer_host = "peer-acme.icims.com"
        monkeypatch.setattr(icims, "MAX_PAGES", 1)

        def handler(request: httpx.Request) -> httpx.Response:
            total = 1 if request.url.host == HOST else 2
            return httpx.Response(
                200,
                text=_card_listing(
                    request.url.host,
                    (100, "Role", "CA-QC", "Full-Time"),
                    total=total,
                ),
                request=request,
            )

        metadata = {
            "host": HOST,
            "cross_locale_dedupe": {"peer_host": peer_host, "title_aliases": {}},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="peer .* was truncated"):
                await discover({"board_url": BOARD_URL, "metadata": metadata}, client)

    async def test_empty_single_listing_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == set()

    @pytest.mark.parametrize("status", [404, 410])
    async def test_first_page_terminal_status_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": BOARD_URL}, client)

    @pytest.mark.parametrize("status", [204, 302, 400])
    async def test_unexpected_status_is_not_board_gone(self, status: int):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                headers={"location": "https://example.com/jobs"} if status == 302 else {},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == status

    async def test_custom_site_javascript_redirect_fails_not_empty(self):
        text = "<script>window.top.location.href = 'https://careers.example.com/jobs';</script>"
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=text, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="non-listing"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_second_page_failure_aborts_whole_discovery(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("pr") == "1":
                return httpx.Response(404, request=request)
            return httpx.Response(200, text=_listing(100, total=2), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == 404

    async def test_empty_advertised_page_fails_not_partial(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page_index = int(request.url.params.get("pr", "0"))
            ids = (100,) if page_index == 0 else ()
            return httpx.Response(
                200,
                text=_listing(*ids, page=page_index + 1, total=2),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="empty advertised page"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_wrong_page_response_fails_not_partial(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, text=_listing(100, page=1, total=2), request=request
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="page 1 for page index 1"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_duplicate_across_pages_suppresses_tombstoning(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page_index = int(request.url.params.get("pr", "0"))
            return httpx.Response(
                200,
                text=_listing(100, page=page_index + 1, total=2),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {_job_url(100)}

    async def test_page_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(icims, "MAX_PAGES", 1)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(100, total=2), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {_job_url(100)}

    async def test_job_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(icims, "MAX_JOBS", 1)
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, text=_listing(100, 101, total=2), request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {_job_url(100), _job_url(101)}
        assert requests == [LISTING_URL]

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
            return httpx.Response(200, text=_listing(100), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": BOARD_URL}, client) == {_job_url(100)}
        assert calls == 2


class TestDetection:
    async def test_direct_url_detects_without_client(self):
        assert await can_handle(BOARD_URL) == {"host": HOST}

    async def test_filtered_direct_url_is_not_widened_with_or_without_client(self):
        filtered = f"{BOARD_URL}?searchLocation=12781--EMEA"
        assert await can_handle(filtered) is None
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(filtered, client) is None

    async def test_direct_url_verifies_first_page_count(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(100, 101, total=3), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle(BOARD_URL, client)
        assert result == {"host": HOST, "jobs": "2+ (first of 3 pages)"}

    async def test_embedded_link_is_detected_and_verified(self):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.host == "example.com":
                return httpx.Response(
                    200,
                    text=f'<a href="https://{HOST}/jobs/123/role/job">Open roles</a>',
                    request=request,
                )
            return httpx.Response(200, text=_listing(123), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) == {
                "host": HOST,
                "jobs": 1,
            }
        assert requests == ["https://example.com/careers", LISTING_URL]

    async def test_does_not_guess_host_from_company_slug(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="<html>No ATS link</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://acme.example/careers", client) is None
        assert requested == ["https://acme.example/careers"]

    async def test_filtered_embedded_link_is_not_widened(self):
        filtered = f"https://{HOST}/jobs/search?searchLocation=12781--EMEA"
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(
                200,
                text=f'<a href="{filtered}">Regional roles</a>',
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) is None
        assert requested == ["https://example.com/careers"]


def test_runtime_and_workspace_integration():
    assert "icims" in all_monitor_types()
    assert "icims" in _KNOWN_ATS_DOMAINS
    assert detect_ats_from_url(BOARD_URL) == "icims"
    assert detect_ats_from_url(f"{BOARD_URL}?searchLocation=12781--EMEA") is None
    assert detect_ats_from_url("https://www.icims.com/jobs") is None
    assert auto_scraper_type("icims") == ("json-ld", None)
    assert "icims" in MONITOR_CARDS
    assert "icims" in _MONITOR_CONFIG_HINTS


def test_career_discovery_finds_listing_and_detail_links():
    html = (
        f'<a href="https://{HOST}/jobs/search">Jobs</a>'
        f'<a href="https://{HOST}/jobs/123/software-engineer/job">Role</a>'
    )
    candidates = _scan_ats_urls_in_html(html)
    assert [candidate.url for candidate in candidates] == [
        f"https://{HOST}/jobs/search",
        f"https://{HOST}/jobs/123/software-engineer/job",
    ]


def test_career_discovery_rejects_non_board_path():
    assert _scan_ats_urls_in_html(f'<a href="https://{HOST}/admin">Admin</a>') == []


def test_career_discovery_does_not_widen_filtered_listing():
    filtered = f"https://{HOST}/jobs/search?searchLocation=12781--EMEA"
    assert _scan_ats_urls_in_html(f'<a href="{filtered}">Regional jobs</a>') == []
