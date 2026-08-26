"""Safe transport for non-secret headers configured on public job boards.

Board-scoped headers follow links and pagination controlled by remote HTML.
Keep the configurable surface limited to public content negotiation and crawler
identification, and validate redirects before sending another request so a
remote origin cannot turn those headers into an SSRF or credential-forwarding
primitive.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

_PUBLIC_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-language",
        "cache-control",
        "pragma",
        "user-agent",
    }
)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5
_MAX_URL_CHARS = 8_192
_SENSITIVE_DEFAULT_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})


def strip_private_request_headers(request: httpx.Request) -> None:
    """Remove credentials a shared client may have added to a public request."""
    for header in _SENSITIVE_DEFAULT_HEADERS:
        request.headers.pop(header, None)


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"public board request found an invalid URL: {url}")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"public board request URL must not contain credentials: {url}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"public board request found an invalid URL: {url}") from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, parts.hostname.casefold(), port


def same_origin(left: str, right: str) -> bool:
    """Whether two validated public HTTP(S) URLs share an origin."""
    return _origin(left) == _origin(right)


def validated_public_request_headers(value: object, *, owner: str) -> dict[str, str]:
    """Return bounded public headers or reject the configuration fail-closed."""
    if value is None:
        return {}
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(header_value, str)
        for key, header_value in value.items()
    ):
        raise ValueError(f"{owner} request_headers must map strings to strings")

    headers: dict[str, str] = {}
    seen: set[str] = set()
    for key, header_value in value.items():
        normalized = key.strip().casefold()
        if normalized not in _PUBLIC_REQUEST_HEADERS:
            raise ValueError(f"{owner} request_headers contains unsafe header {key!r}")
        if normalized in seen:
            raise ValueError(f"{owner} request_headers contains duplicate header {key!r}")
        if (
            not header_value.strip()
            or len(key) > 64
            or len(header_value) > 1_024
            or any(
                character != "\t" and not 0x20 <= ord(character) <= 0x7E
                for character in header_value
            )
        ):
            raise ValueError(f"{owner} request_headers contains an invalid header")
        seen.add(normalized)
        headers[key.strip()] = header_value.strip()
    return headers


async def public_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float | None = None,
    stream: bool = False,
) -> httpx.Response:
    """GET with bounded redirects that may never change the request origin."""
    if not isinstance(url, str) or not url or len(url) > _MAX_URL_CHARS:
        raise ValueError("public board request URL exceeds the supported length")
    initial_url = urlunsplit((*urlsplit(url)[:4], ""))
    origin = _origin(initial_url)
    current_url = initial_url

    for redirect_count in range(_MAX_REDIRECTS + 1):
        request = client.build_request("GET", current_url, headers=headers, timeout=timeout)
        strip_private_request_headers(request)
        response = await client.send(request, stream=stream, follow_redirects=False)
        if response.status_code not in _REDIRECT_STATUSES:
            if 300 <= response.status_code < 400:
                await response.aclose()
                raise ValueError(
                    f"public board request received unsupported redirect "
                    f"{response.status_code}: {current_url}"
                )
            return response

        try:
            location = response.headers.get("location")
            if not location:
                raise ValueError(f"public board redirect has no Location: {current_url}")
            if redirect_count >= _MAX_REDIRECTS:
                raise ValueError("public board request exceeded the redirect limit")
            next_url = urlunsplit((*urlsplit(urljoin(str(response.url), location))[:4], ""))
            if len(next_url) > _MAX_URL_CHARS:
                raise ValueError("public board redirect URL exceeds the supported length")
            if _origin(next_url) != origin:
                raise ValueError(f"public board request refused a cross-origin redirect: {url}")
        finally:
            await response.aclose()
        current_url = next_url

    raise AssertionError("unreachable")
