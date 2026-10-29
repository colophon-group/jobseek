from __future__ import annotations

import httpx
import pytest

from src.core.monitors.api_sniffer import _discover_http, _refresh_request_headers
from src.shared.http_retry import PaginationFetchError
from src.shared.tdm import TDMReservedError


@pytest.mark.asyncio
async def test_public_context_is_fresh_per_discovery_and_never_sent_to_bootstrap():
    board = "https://fixture.invalid/careers"
    source = "https://fixture.invalid/public/context"
    endpoint = "https://api.fixture.invalid/jobs"
    config = {
        "api_url": endpoint,
        "json_path": "jobs",
        "url_field": "url",
        "request_headers": {"rolecode": "manager"},
        "request_header_refresh": {
            "source_url": source,
            "fields": {"myJobsToken": "context.value"},
        },
    }
    calls = []
    token = "first-public-context"

    def respond(request):
        calls.append(str(request.url))
        if str(request.url) in {board, source}:
            assert "myJobsToken" not in request.headers
            if str(request.url) == board:
                return httpx.Response(200, text="<html>Public careers</html>")
            return httpx.Response(200, json={"context": {"value": token}})
        assert str(request.url) == endpoint
        assert request.headers["myJobsToken"] == token
        assert request.headers["rolecode"] == "manager"
        return httpx.Response(200, json={"jobs": [{"url": board + "/job"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        for fresh_value in ("first-public-context", "second-public-context"):
            token = fresh_value
            jobs = await _discover_http({"board_url": board}, client, config)
            assert jobs == {board + "/job"}
            assert config["request_headers"] == {"rolecode": "manager"}
            assert "myJobsToken" not in client.headers
    assert calls == [board, source, endpoint] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reserved_at", ["/careers", "/public/context"])
async def test_public_context_preserves_policy_before_inventory(reserved_at):
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == reserved_at:
            return httpx.Response(200, headers={"TDM-Reservation": "1"}, text="reserved")
        return httpx.Response(200, text="<html>Public careers</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(TDMReservedError):
            await _refresh_request_headers(
                client,
                "https://fixture.invalid/careers",
                "https://api.fixture.invalid/jobs",
                {},
                {
                    "source_url": "https://fixture.invalid/public/context",
                    "fields": {"myJobsToken": "context"},
                },
            )
    assert requests[-1] == reserved_at


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "bad\r\nheader", "x" * 8193, True, {}])
async def test_invalid_public_context_never_becomes_an_api_header(value):
    def respond(request):
        if request.url.path == "/careers":
            return httpx.Response(200, text="<html>Public careers</html>")
        return httpx.Response(200, json={"context": value})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match="invalid public header refresh value"):
            await _refresh_request_headers(
                client,
                "https://fixture.invalid/careers",
                "https://api.fixture.invalid/jobs",
                {},
                {
                    "source_url": "https://fixture.invalid/public/context",
                    "fields": {"myJobsToken": "context"},
                },
            )


@pytest.mark.asyncio
async def test_refreshed_context_cannot_follow_a_redirect_on_a_shared_client():
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if request.url.path == "/careers":
            return httpx.Response(200, text="<html>Public careers</html>")
        if request.url.path == "/context":
            return httpx.Response(200, json={"context": "fresh-public-context"})
        assert request.headers["myJobsToken"] == "fresh-public-context"
        return httpx.Response(302, headers={"Location": "https://outside.fixture.invalid/"})

    config = {
        "api_url": "https://api.fixture.invalid/jobs",
        "json_path": "jobs",
        "url_field": "url",
        "request_header_refresh": {
            "source_url": "https://fixture.invalid/context",
            "fields": {"myJobsToken": "context"},
        },
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        with pytest.raises(PaginationFetchError):
            await _discover_http({"board_url": "https://fixture.invalid/careers"}, client, config)
    assert (
        requests
        == [
            "https://fixture.invalid/careers",
            "https://fixture.invalid/context",
        ]
        + ["https://api.fixture.invalid/jobs"] * 3
    )
