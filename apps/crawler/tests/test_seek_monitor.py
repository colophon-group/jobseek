from __future__ import annotations

import httpx
import pytest

from src.core.monitors import all_monitor_types, seek
from src.core.monitors.seek import _identity_from_url, can_handle, discover
from src.core.scrapers import JobContent, all_scraper_types
from src.core.scrapers.seek import scrape
from src.shared.http_retry import ResponseBodyTooLargeError
from src.shared.tdm import TDMReservedError
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

ADVERTISER_ID = "9094357"
NZ_ADVERTISER_ID = "22265755"
AU_BOARD_URL = f"https://au.seek.com/jobs?advertiserid={ADVERTISER_ID}"
NZ_BOARD_URL = f"https://nz.seek.com/jobs?advertiserid={NZ_ADVERTISER_ID}"


def _row(job_id: str, *, advertiser_id: str = ADVERTISER_ID) -> dict:
    return {
        "id": job_id,
        "title": f"Role {job_id}",
        "advertiser": {"id": advertiser_id, "description": "United Rentals Australia"},
    }


def _payload(
    rows: list[dict],
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int | None = None,
    advertiser_id: str = ADVERTISER_ID,
) -> dict:
    count = len(rows) if total is None else total
    size = seek.PAGE_SIZE if page_size is None else page_size
    return {
        "data": rows,
        "totalCount": count,
        "solMetadata": {
            "advertiser": advertiser_id,
            "pageNumber": page,
            "pageSize": size,
            "totalJobCount": count,
        },
    }


def _detail_payload(
    job_id: str,
    *,
    advertiser_id: str = ADVERTISER_ID,
    expired: bool = False,
    status: str = "Active",
) -> dict:
    return {
        "data": {
            "jobDetails": {
                "job": {
                    "id": job_id,
                    "title": "Forklift Operator",
                    "content": "<p>Operate equipment safely.</p>",
                    "abstract": "Operate equipment.",
                    "location": {"label": "Bassendean, Perth WA"},
                    "advertiser": {
                        "id": advertiser_id,
                        "name": "United Rentals Australia Pty Ltd",
                    },
                    "workTypes": {"label": "Full time"},
                    "createdAt": {"dateTimeUtc": "2026-08-28T05:39:42Z"},
                    "expiresAt": {"dateTimeUtc": "2026-09-27T13:59:59Z"},
                    "isExpired": expired,
                    "status": status,
                }
            }
        }
    }


class TestIdentity:
    def test_provider_is_registered_and_detected(self):
        assert "seek" in all_monitor_types()
        assert "seek" in all_scraper_types()
        assert detect_ats_from_url(AU_BOARD_URL) == "seek"
        assert detect_ats_from_url(NZ_BOARD_URL) == "seek"
        assert auto_scraper_type("seek") == ("seek", None)

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (AU_BOARD_URL, ("au.seek.com", ADVERTISER_ID)),
            (
                f"https://www.seek.com.au/jobs/?advertiserid={ADVERTISER_ID}",
                ("www.seek.com.au", ADVERTISER_ID),
            ),
            (NZ_BOARD_URL, ("nz.seek.com", "22265755")),
            (
                "https://www.seek.co.nz/jobs?advertiserid=22265755",
                ("www.seek.co.nz", "22265755"),
            ),
        ],
    )
    def test_extracts_advertiser_identity(self, url: str, expected: tuple[str, str]):
        assert _identity_from_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "http://au.seek.com/jobs?advertiserid=9094357",
            "https://user@au.seek.com/jobs?advertiserid=9094357",
            "https://au.seek.com:444/jobs?advertiserid=9094357",
            "https://au.seek.com/jobs",
            "https://au.seek.com/jobs?advertiserid=not-numeric",
            "https://au.seek.com/jobs?advertiserid=9094357&page=2",
            "https://au.seek.com/United-Rentals-jobs/at-this-company",
            "https://au.seek.com.evil.test/jobs?advertiserid=9094357",
        ],
    )
    def test_rejects_untrusted_or_filtered_urls(self, url: str):
        assert _identity_from_url(url) is None
        assert detect_ats_from_url(url) != "seek"


class TestMonitor:
    async def test_discovers_canonical_job_urls(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "www.seek.com.au"
            assert request.url.path == "/api/jobsearch/v5/search"
            assert request.url.params["advertiserid"] == ADVERTISER_ID
            assert request.url.params["siteKey"] == "AU-Main"
            assert request.url.params["pagesize"] == str(seek.PAGE_SIZE)
            return httpx.Response(
                200,
                json=_payload([_row("94267983"), _row("94267984")]),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": AU_BOARD_URL}, client)

        assert result == {
            "https://au.seek.com/job/94267983",
            "https://au.seek.com/job/94267984",
        }

    async def test_uses_new_zealand_market(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "nz.seek.com"
            assert request.url.params["siteKey"] == "NZ-Main"
            return httpx.Response(
                200,
                json=_payload(
                    [_row("94300044", advertiser_id=NZ_ADVERTISER_ID)],
                    advertiser_id=NZ_ADVERTISER_ID,
                ),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": NZ_BOARD_URL}, client) == {
                "https://nz.seek.com/job/94300044"
            }

    async def test_accepts_authoritative_zero_result(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([], total=0),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": AU_BOARD_URL}, client) == set()

    @pytest.mark.parametrize(
        ("rows", "reported_advertiser_id"),
        [
            ([], None),
            ([], "999"),
            ([_row("94267983")], None),
            ([_row("94267983")], "999"),
        ],
        ids=["zero-missing", "zero-wrong", "nonzero-missing", "nonzero-wrong"],
    )
    async def test_rejects_unbound_search_response_advertiser(
        self,
        rows: list[dict],
        reported_advertiser_id: str | None,
    ):
        payload = _payload(rows)
        if reported_advertiser_id is None:
            payload["solMetadata"].pop("advertiser")
        else:
            payload["solMetadata"]["advertiser"] = reported_advertiser_id
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="advertiser does not match"):
                await discover({"board_url": AU_BOARD_URL}, client)

    async def test_rejects_invalid_or_mismatched_configured_identity(self):
        def unexpected_request(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected request to {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
            with pytest.raises(ValueError, match="Cannot derive"):
                await discover(
                    {
                        "board_url": "https://careers.example/seek",
                        "metadata": {"host": "au.seek.com", "advertiser_id": ADVERTISER_ID},
                    },
                    client,
                )
            with pytest.raises(ValueError, match="does not match"):
                await discover(
                    {
                        "board_url": AU_BOARD_URL,
                        "metadata": {"host": "au.seek.com", "advertiser_id": "999"},
                    },
                    client,
                )

    async def test_caps_search_response_size(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(seek, "MAX_JSON_BYTES", 16)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"{" + (b"x" * 64), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ResponseBodyTooLargeError):
                await discover({"board_url": AU_BOARD_URL}, client)

    async def test_paginates_and_rejects_cross_advertiser_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(seek, "PAGE_SIZE", 2)

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            rows = [_row("94267983"), _row("94267984")] if page == 1 else [_row("94267985")]
            return httpx.Response(
                200,
                json=_payload(rows, total=3, page=page, page_size=2),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": AU_BOARD_URL}, client)
        assert result == {
            "https://au.seek.com/job/94267983",
            "https://au.seek.com/job/94267984",
            "https://au.seek.com/job/94267985",
        }

        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([_row("94267983", advertiser_id="999")]),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="invalid job identity"):
                await discover({"board_url": AU_BOARD_URL}, client)

    async def test_rejects_pagination_drift(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(seek, "PAGE_SIZE", 1)

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            total = 2 if page == 1 else 3
            return httpx.Response(
                200,
                json=_payload(
                    [_row(str(94267982 + page))],
                    total=total,
                    page=page,
                    page_size=1,
                ),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="total changed"):
                await discover({"board_url": AU_BOARD_URL}, client)


class TestProbe:
    async def test_direct_url_is_detected_without_network(self):
        assert await can_handle(AU_BOARD_URL) == {
            "host": "au.seek.com",
            "advertiser_id": ADVERTISER_ID,
        }

    async def test_reachable_api_adds_verified_count(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([_row("94267983"), _row("94267984")]),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(AU_BOARD_URL, client) == {
                "host": "au.seek.com",
                "advertiser_id": ADVERTISER_ID,
                "jobs": 2,
            }

    async def test_tdm_reservation_propagates(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_payload([]),
                headers={"tdm-reservation": "1"},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(TDMReservedError):
                await can_handle(AU_BOARD_URL, client)


class TestScraper:
    async def test_hydrates_detail_without_browser_navigation(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://au.seek.com/graphql"
            return httpx.Response(
                200,
                json=_detail_payload("94267983"),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://au.seek.com/job/94267983", {}, client)

        assert result.title == "Forklift Operator"
        assert result.description == "<p>Operate equipment safely.</p>"
        assert result.locations == ["Bassendean, Perth WA"]
        assert result.employment_type == "Full time"
        assert result.date_posted == "2026-08-28T05:39:42Z"
        assert result.metadata == {
            "seek_id": "94267983",
            "advertiser": "United Rentals Australia Pty Ltd",
            "advertiser_id": ADVERTISER_ID,
            "abstract": "Operate equipment.",
            "status": "Active",
            "is_expired": "False",
        }

    async def test_hydrates_new_zealand_detail_with_authoritative_advertiser(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://nz.seek.com/graphql"
            return httpx.Response(
                200,
                json=_detail_payload("94300044", advertiser_id=NZ_ADVERTISER_ID),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://nz.seek.com/job/94300044",
                {"advertiser_id": NZ_ADVERTISER_ID},
                client,
            )

        assert result.title == "Forklift Operator"
        assert result.metadata and result.metadata["seek_id"] == "94300044"
        assert result.metadata["advertiser_id"] == NZ_ADVERTISER_ID

    @pytest.mark.parametrize("response_id", [None, "99999999"], ids=["missing", "wrong"])
    async def test_rejects_missing_or_mismatched_detail_id(self, response_id: str | None):
        payload = _detail_payload("94267983")
        job = payload["data"]["jobDetails"]["job"]
        if response_id is None:
            job.pop("id")
        else:
            job["id"] = response_id
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )

        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="ID does not match 94267983"):
                await scrape("https://au.seek.com/job/94267983", {}, client)

    @pytest.mark.parametrize(
        "response_advertiser_id",
        [None, "123456"],
        ids=["missing", "wrong"],
    )
    async def test_rejects_missing_or_mismatched_detail_advertiser(
        self,
        response_advertiser_id: str | None,
    ):
        payload = _detail_payload("94267983")
        advertiser = payload["data"]["jobDetails"]["job"]["advertiser"]
        if response_advertiser_id is None:
            advertiser.pop("id")
        else:
            advertiser["id"] = response_advertiser_id
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )

        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="advertiser"):
                await scrape(
                    "https://au.seek.com/job/94267983",
                    {"advertiser_id": ADVERTISER_ID},
                    client,
                )

    @pytest.mark.parametrize(
        ("expired", "status"),
        [(True, "Active"), (False, "Expired")],
    )
    async def test_inactive_detail_returns_empty_content(self, expired: bool, status: str):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_detail_payload("94267983", expired=expired, status=status),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await scrape("https://au.seek.com/job/94267983", {}, client) == JobContent()

    async def test_rejects_malformed_active_detail(self):
        payload = _detail_payload("94267983")
        payload["data"]["jobDetails"]["job"].pop("status")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="invalid status fields"):
                await scrape("https://au.seek.com/job/94267983", {}, client)

    async def test_probe_propagates_tdm_reservation(self, monkeypatch: pytest.MonkeyPatch):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={},
                headers={"tdm-reservation": "1"},
                request=request,
            )
        )
        client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr("src.shared.http.create_http_client", lambda: client)

        from src.core.scrapers.seek import probe_pw

        with pytest.raises(TDMReservedError):
            await probe_pw(["https://au.seek.com/job/94267983"], None)
