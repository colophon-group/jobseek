from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, jobbank104
from src.core.monitors.jobbank104 import (
    _canonical_job_url,
    _token_from_url,
    can_handle,
    discover,
    save_raw,
)
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

TOKEN = "auzu36g"
BOARD_URL = f"https://www.104.com.tw/company/{TOKEN}"
API_URL = f"https://www.104.com.tw/api/companies/{TOKEN}/jobs"


def _job(job_id: str, *, url: str | None = None) -> dict:
    return {
        "jobNo": job_id,
        "jobUrl": url or f"https://www.104.com.tw/job/{job_id}",
        "jobName": f"Role {job_id}",
    }


def _payload(
    jobs: list[dict],
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int | None = None,
) -> dict:
    size = jobbank104.PAGE_SIZE if page_size is None else page_size
    count = len(jobs) if total is None else total
    pages = (count + size - 1) // size if count else 0
    return {
        "data": {
            "totalCount": count,
            "totalPages": pages,
            "page": page,
            "pageSize": size,
            "list": {"topJobs": [], "normalJobs": jobs},
        }
    }


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
        assert detect_ats_from_url(url) != "jobbank104"

    def test_canonicalizes_job_urls(self):
        assert (
            _canonical_job_url("https://www.104.com.tw/job/8JLD1?jobsource=company_job")
            == "https://www.104.com.tw/job/8jld1"
        )
        assert _canonical_job_url("https://evil.test/job/8jld1") is None


class TestMonitor:
    async def test_discovers_authoritative_normal_jobs(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url).startswith(API_URL)
            assert request.headers["accept"] == "application/json"
            assert request.headers["referer"] == BOARD_URL
            return httpx.Response(
                200,
                json=_payload([_job("8jld1"), _job("92nxg")]),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {
            "https://www.104.com.tw/job/8jld1",
            "https://www.104.com.tw/job/92nxg",
        }

    async def test_metadata_token_override(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url).split("?")[0])
            return httpx.Response(200, json=_payload([]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {
                    "board_url": "https://example.com/jobs",
                    "metadata": {"token": TOKEN},
                },
                client,
            )

        assert result == set()
        assert seen == [API_URL]

    async def test_paginates_without_including_promoted_duplicates(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(jobbank104, "PAGE_SIZE", 2)

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            rows = [_job("8jld1"), _job("92nxg")] if page == 1 else [_job("94xx9")]
            payload = _payload(rows, total=3, page=page, page_size=2)
            payload["data"]["list"]["topJobs"] = [_job("8jld1")]
            return httpx.Response(200, json=payload, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {
            "https://www.104.com.tw/job/8jld1",
            "https://www.104.com.tw/job/92nxg",
            "https://www.104.com.tw/job/94xx9",
        }

    async def test_inventory_over_cap_is_safe_truncation(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(jobbank104, "PAGE_SIZE", 2)
        monkeypatch.setattr(jobbank104, "MAX_JOBS", 2)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([_job("8jld1"), _job("92nxg")], total=3, page_size=2),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert isinstance(result, MonitorResult)
        assert len(result.urls) == 2
        assert result.truncated is True

    async def test_incomplete_page_fails_closed(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([_job("8jld1")], total=2),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="returned 1 rows, expected 2"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_invalid_job_identity_fails_closed(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([_job("8jld1", url="https://evil.test/job/8jld1")]),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="invalid job identity"):
                await discover({"board_url": BOARD_URL}, client)

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_status_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.status_code == status
        assert exc_info.value.url == API_URL

    async def test_challenge_status_is_transient_failure(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(403, text="challenge", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": BOARD_URL}, client)
        assert exc_info.value.last_status == 403

    async def test_raw_artifact_uses_proxy_aware_client(self, tmp_path, monkeypatch):
        seen: list[dict] = []
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=_payload([_job("8jld1")]), request=request)
        )

        async with (
            httpx.AsyncClient() as base_client,
            httpx.AsyncClient(transport=transport) as routed_client,
        ):

            @asynccontextmanager
            async def fake_client_for(client, config):
                assert client is base_client
                seen.append(config)
                yield routed_client

            monkeypatch.setattr("src.shared.http.client_for", fake_client_for)
            await save_raw(tmp_path, BOARD_URL, {"token": TOKEN, "proxy": True}, base_client)

        assert seen == [{"token": TOKEN, "proxy": True}]
        saved = json.loads((tmp_path / "jobbank104-listing.json").read_text())
        assert saved["data"]["totalCount"] == 1


class TestProbe:
    async def test_direct_url_is_detected_without_network(self):
        assert await can_handle(BOARD_URL) == {"token": TOKEN}

    async def test_reachable_api_adds_verified_count(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([_job("8jld1"), _job("92nxg")]),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) == {"token": TOKEN, "jobs": 2}

    async def test_provider_challenge_retains_deterministic_match(self, monkeypatch):
        async def blocked(_token: str, _client: httpx.AsyncClient):
            raise PaginationFetchError("blocked", attempts=1, last_status=403)

        monkeypatch.setattr(jobbank104, "_discover_urls", blocked)
        async with httpx.AsyncClient() as client:
            assert await can_handle(BOARD_URL, client) == {"token": TOKEN}

    async def test_unrelated_url_is_not_detected(self):
        assert await can_handle("https://example.com/company/auzu36g") is None
