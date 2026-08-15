from __future__ import annotations

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types
from src.core.monitors.jobbank104 import (
    _canonical_job_url,
    _token_from_url,
    can_handle,
    discover,
)
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

TOKEN = "auzu36g"
BOARD_URL = f"https://www.104.com.tw/company/{TOKEN}"


def _listing(*job_ids: str, advertised: int | None = None) -> str:
    count = len(job_ids) if advertised is None else advertised
    links = "".join(
        f'<article><a href="/job/{job_id}?jobsource=company_job">Role {job_id}</a></article>'
        for job_id in job_ids
    )
    return f"<html><body><a>工作機會({count})</a>{links}</body></html>"


class TestIdentity:
    def test_provider_is_registered_with_workspace_defaults(self):
        assert "jobbank104" in all_monitor_types()
        assert detect_ats_from_url(BOARD_URL) == "jobbank104"
        assert auto_scraper_type("jobbank104") == ("json-ld", None)

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (BOARD_URL, TOKEN),
            (f"{BOARD_URL}/", TOKEN),
            ("https://www.104.com.tw/company/A3UPRYG", "a3upryg"),
        ],
    )
    def test_extracts_company_token(self, url: str, expected: str):
        assert _token_from_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            f"http://www.104.com.tw/company/{TOKEN}",
            f"https://104.com.tw/company/{TOKEN}",
            f"https://user@www.104.com.tw/company/{TOKEN}",
            f"https://www.104.com.tw:444/company/{TOKEN}",
            f"{BOARD_URL}?page=2",
            f"{BOARD_URL}#jobs",
            f"https://www.104.com.tw/job/{TOKEN}",
            f"https://www.104.com.tw/company/{TOKEN}/jobs",
            f"https://www.104.com.tw.evil.test/company/{TOKEN}",
        ],
    )
    def test_rejects_untrusted_or_scoped_urls(self, url: str):
        assert _token_from_url(url) is None

    def test_canonicalizes_job_urls(self):
        assert (
            _canonical_job_url("https://www.104.com.tw/job/8JLD1?jobsource=company_job")
            == "https://www.104.com.tw/job/8jld1"
        )
        assert _canonical_job_url("https://evil.test/job/8jld1") is None
        assert _canonical_job_url("https://www.104.com.tw/jobs/8jld1") is None


class TestMonitor:
    async def test_discovers_and_canonicalizes_company_jobs(self):
        page = _listing("8jld1", "92nxg").replace(
            "</body>",
            (
                '<a href="https://www.104.com.tw/job/8JLD1#duplicate">Duplicate</a>'
                '<a href="https://evil.test/job/aaaaa">Foreign</a></body>'
            ),
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {
            "https://www.104.com.tw/job/8jld1",
            "https://www.104.com.tw/job/92nxg",
        }

    async def test_metadata_token_override(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_listing(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {
                    "board_url": "https://example.com/jobs",
                    "metadata": {"token": TOKEN},
                },
                client,
            )

        assert result == set()
        assert seen == [BOARD_URL]

    async def test_explicit_empty_listing_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(advertised=0), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == set()

    async def test_count_mismatch_is_safe_truncation(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing("8jld1", advertised=13),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert isinstance(result, MonitorResult)
        assert result.urls == {"https://www.104.com.tw/job/8jld1"}
        assert result.truncated is True

    async def test_non_listing_page_fails_not_empty(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>not a board</html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="non-listing"):
                await discover({"board_url": BOARD_URL}, client)

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_status_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.status_code == status
        assert exc_info.value.url == BOARD_URL

    async def test_challenge_status_is_transient_failure(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(403, text="challenge", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == 403


class TestProbe:
    async def test_direct_url_is_detected_without_network(self):
        assert await can_handle(BOARD_URL) == {"token": TOKEN}

    async def test_reachable_page_adds_verified_count(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing("8jld1", "92nxg"), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) == {"token": TOKEN, "jobs": 2}

    async def test_provider_challenge_retains_deterministic_match(self, monkeypatch):
        async def blocked(_token: str, _client: httpx.AsyncClient) -> str:
            raise PaginationFetchError("blocked", last_status=403)

        monkeypatch.setattr("src.core.monitors.jobbank104._fetch_listing", blocked)
        async with httpx.AsyncClient() as client:
            assert await can_handle(BOARD_URL, client) == {"token": TOKEN}

    async def test_unrelated_url_is_not_detected(self):
        assert await can_handle("https://example.com/company/auzu36g") is None
