"""Safe HTTP-validator fingerprints for mutable document URLs.

The monitor turns a mutable source URL into a versioned job identity.  The
downstream scraper must independently verify the same validators before it
accepts the response body, otherwise content can change between discovery and
scraping while retaining the stale identity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import httpx

from src.shared.tdm import check_response as check_tdm_response

RESPONSE_FINGERPRINT_QUERY_PARAM = "_jobseek_fp"
MAX_RESPONSE_FINGERPRINT_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_FINGERPRINT_URL_CHARS = 8_192
MAX_RESPONSE_FINGERPRINT_REDIRECTS = 5
_MAX_QUERY_FIELDS = 100
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{24}\Z")
_REDIRECT_STATUSES = frozenset({301, 302, 307, 308})


@dataclass(frozen=True)
class ResponseFingerprintValidators:
    """Canonical response metadata used to identify one representation."""

    etag: str
    last_modified: str
    content_length: int
    content_type: str


def _url_origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"Response fingerprint found an invalid URL: {url}")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"Response fingerprint URL must not contain credentials: {url}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"Response fingerprint found an invalid URL: {url}") from exc
    if port is None:
        port = 443 if parts.scheme.casefold() == "https" else 80
    return parts.scheme.casefold(), parts.hostname.casefold(), port


def normalize_response_fingerprint_source_url(url: str) -> str:
    """Validate a source URL and remove its non-HTTP fragment."""
    if not isinstance(url, str) or not url or len(url) > MAX_RESPONSE_FINGERPRINT_URL_CHARS:
        raise ValueError("Response fingerprint URL exceeds the supported length")
    _url_origin(url)
    parts = urlsplit(url)
    try:
        pairs = parse_qsl(
            parts.query,
            keep_blank_values=True,
            max_num_fields=_MAX_QUERY_FIELDS,
        )
    except ValueError as exc:
        raise ValueError(f"Response fingerprint URL has an invalid query: {url}") from exc
    if any(key == RESPONSE_FINGERPRINT_QUERY_PARAM for key, _ in pairs):
        raise ValueError(
            f"Response fingerprint source URL contains reserved query parameter: {url}"
        )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def response_fingerprint_validators(
    response: httpx.Response,
    *,
    expected_content_type: str,
    source_url: str,
) -> ResponseFingerprintValidators:
    """Validate and canonicalize the response metadata used by a fingerprint."""
    check_tdm_response(response)

    content_encoding = response.headers.get("content-encoding")
    if content_encoding is not None and content_encoding.strip().casefold() != "identity":
        raise ValueError(
            f"Response fingerprint does not support encoded response bodies: {source_url}"
        )

    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    content_type = content_type.strip().casefold()
    if content_type != expected_content_type.strip().casefold():
        raise ValueError(
            f"Response fingerprint received unexpected Content-Type {content_type!r}: {source_url}"
        )

    etag = response.headers.get("etag")
    if etag is None or etag.startswith("W/"):
        raise ValueError(f"Response fingerprint requires a strong ETag: {source_url}")
    if (
        len(etag) < 10
        or len(etag) > 512
        or not etag.startswith('"')
        or not etag.endswith('"')
        or '"' in etag[1:-1]
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in etag)
    ):
        raise ValueError(f"Response fingerprint received an invalid strong ETag: {source_url}")

    raw_last_modified = response.headers.get("last-modified")
    if raw_last_modified is None:
        raise ValueError(f"Response fingerprint requires Last-Modified: {source_url}")
    try:
        parsed_last_modified = parsedate_to_datetime(raw_last_modified)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"Response fingerprint received invalid Last-Modified: {source_url}"
        ) from exc
    if parsed_last_modified.tzinfo is None:
        raise ValueError(f"Response fingerprint received invalid Last-Modified: {source_url}")
    last_modified = parsed_last_modified.astimezone(UTC).isoformat()

    raw_content_length = response.headers.get("content-length")
    try:
        content_length = int(raw_content_length) if raw_content_length is not None else 0
    except ValueError as exc:
        raise ValueError(
            f"Response fingerprint received invalid Content-Length: {source_url}"
        ) from exc
    if not 0 < content_length <= MAX_RESPONSE_FINGERPRINT_BYTES:
        raise ValueError(f"Response fingerprint received invalid Content-Length: {source_url}")

    return ResponseFingerprintValidators(
        etag=etag,
        last_modified=last_modified,
        content_length=content_length,
        content_type=content_type,
    )


def response_fingerprint_token(
    source_url: str,
    validators: ResponseFingerprintValidators,
) -> str:
    """Compute the stable token for a normalized source and its validators."""
    normalized_url = normalize_response_fingerprint_source_url(source_url)
    payload = "\n".join(
        (
            normalized_url,
            validators.etag,
            validators.last_modified,
            str(validators.content_length),
            validators.content_type,
        )
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def build_response_fingerprint_url(
    source_url: str,
    validators: ResponseFingerprintValidators,
) -> str:
    """Append a deterministic private token while preserving the source query."""
    normalized_url = normalize_response_fingerprint_source_url(source_url)
    parts = urlsplit(normalized_url)
    token = response_fingerprint_token(normalized_url, validators)
    query = f"{parts.query}&" if parts.query else ""
    query += f"{RESPONSE_FINGERPRINT_QUERY_PARAM}={token}"
    result = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    if len(result) > MAX_RESPONSE_FINGERPRINT_URL_CHARS:
        raise ValueError("Response fingerprint URL exceeds the supported length")
    return result


def extract_response_fingerprint_url(url: str) -> tuple[str, str] | None:
    """Return the original source URL and token from a synthetic identity URL."""
    if not isinstance(url, str) or not url or len(url) > MAX_RESPONSE_FINGERPRINT_URL_CHARS:
        raise ValueError("Response fingerprint URL exceeds the supported length")
    _url_origin(url)
    parts = urlsplit(url)
    fields = parts.query.split("&") if parts.query else []
    marker = f"{RESPONSE_FINGERPRINT_QUERY_PARAM}="
    matching_indexes = [index for index, field in enumerate(fields) if field.startswith(marker)]
    if not matching_indexes:
        normalize_response_fingerprint_source_url(url)
        return None
    if matching_indexes != [len(fields) - 1]:
        raise ValueError("Response fingerprint URL contains a misplaced reserved parameter")
    token = fields[-1][len(marker) :]
    if _FINGERPRINT_RE.fullmatch(token) is None:
        raise ValueError("Response fingerprint URL contains an invalid token")
    base_query = "&".join(fields[:-1])
    source_url = urlunsplit((parts.scheme, parts.netloc, parts.path, base_query, ""))
    normalized_url = normalize_response_fingerprint_source_url(source_url)
    return normalized_url, token


async def same_origin_response(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    stream: bool = False,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Issue a request with bounded redirects validated before every hop."""
    initial_url = urlunsplit((*urlsplit(url)[:4], ""))
    if len(initial_url) > MAX_RESPONSE_FINGERPRINT_URL_CHARS:
        raise ValueError("Response fingerprint URL exceeds the supported length")
    origin = _url_origin(initial_url)
    current_url = initial_url

    for redirect_count in range(MAX_RESPONSE_FINGERPRINT_REDIRECTS + 1):
        request = client.build_request(method, current_url, headers=headers)
        if headers:
            # Configured public headers must never inherit credentials from a
            # shared client, including on the first same-origin request.
            from src.shared.public_request_headers import strip_private_request_headers

            strip_private_request_headers(request)
        response = await client.send(request, stream=stream, follow_redirects=False)
        try:
            check_tdm_response(response)
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise ValueError(
                        f"Response fingerprint redirect has no Location: {current_url}"
                    )
                if redirect_count >= MAX_RESPONSE_FINGERPRINT_REDIRECTS:
                    raise ValueError("Response fingerprint exceeded the redirect limit")
                next_url = urljoin(str(response.url), location)
                next_url = urlunsplit((*urlsplit(next_url)[:4], ""))
                if len(next_url) > MAX_RESPONSE_FINGERPRINT_URL_CHARS:
                    raise ValueError("Response fingerprint redirect URL is too long")
                if _url_origin(next_url) != origin:
                    raise ValueError(
                        f"Response fingerprint refused a cross-origin redirect: {current_url}"
                    )
                await response.aclose()
                current_url = next_url
                continue
            if 300 <= response.status_code < 400:
                raise ValueError(
                    f"Response fingerprint received unsupported redirect {response.status_code}"
                )
            response.raise_for_status()
            return response
        except BaseException:
            await response.aclose()
            raise

    raise AssertionError("unreachable")
