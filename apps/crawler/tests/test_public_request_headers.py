from __future__ import annotations

import httpx
import pytest

from src.shared.public_request_headers import public_get, validated_public_request_headers
from src.shared.response_fingerprint import same_origin_response


def test_configured_public_headers_allow_representation_selection():
    assert validated_public_request_headers({"X-Return-Format": "html"}, owner="test") == {
        "X-Return-Format": "html"
    }


@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "Cookie",
        "Proxy-Authorization",
        "Connection",
        "Transfer-Encoding",
        "X-Api-Key",
        "Host",
    ],
)
def test_configured_public_headers_reject_secrets_and_transport_headers(header):
    with pytest.raises(ValueError, match="unsafe header"):
        validated_public_request_headers({header: "value"}, owner="test")


@pytest.mark.parametrize(
    "headers",
    [
        {"Accept": "text/html", "accept": "application/pdf"},
        {"User-Agent": "crawler\r\nAuthorization: secret"},
        {"User-Agent": "crawler\x01secret"},
        {"User-Agent": "crawler\x7fsecret"},
        {"User-Agent": "crawler-é"},
        {"Accept": ""},
    ],
)
def test_configured_public_headers_reject_ambiguous_or_injected_values(headers):
    with pytest.raises(ValueError, match="request_headers"):
        validated_public_request_headers(headers, owner="test")


@pytest.mark.asyncio
async def test_public_get_refuses_cross_origin_redirect_before_forwarding_headers():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host != "jobs.example.com":
            raise AssertionError("cross-origin redirect must not be requested")
        return httpx.Response(
            302,
            headers={"Location": "https://attacker.example/collect"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="cross-origin redirect"):
            await public_get(
                client,
                "https://jobs.example.com/careers",
                headers={"User-Agent": "jobseek-crawler"},
            )

    assert [str(request.url) for request in requests] == ["https://jobs.example.com/careers"]


@pytest.mark.asyncio
async def test_public_get_preserves_safe_headers_on_same_origin_redirect_only():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert request.headers["user-agent"] == "jobseek-crawler"
        if request.url.path == "/careers":
            return httpx.Response(302, headers={"Location": "/openings"}, request=request)
        return httpx.Response(200, text="ok", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer client-default", "Cookie": "session=secret"},
    ) as client:
        response = await public_get(
            client,
            "https://jobs.example.com/careers",
            headers={"User-Agent": "jobseek-crawler"},
        )

    assert response.text == "ok"
    assert [request.url.path for request in requests] == ["/careers", "/openings"]


@pytest.mark.asyncio
async def test_public_fingerprint_request_strips_shared_client_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert request.headers["user-agent"] == "jobseek-crawler"
        return httpx.Response(200, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer client-default", "Cookie": "session=secret"},
    ) as client:
        response = await same_origin_response(
            client,
            "HEAD",
            "https://jobs.example.com/vacancy.pdf",
            headers={"User-Agent": "jobseek-crawler"},
        )

    assert response.status_code == 200
