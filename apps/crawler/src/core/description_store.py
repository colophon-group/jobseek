"""Upload job descriptions to Cloudflare R2.

R2 layout per posting:
    job/{posting_id}/{locale}/latest.html

Environment variables (shared with image_sync):
    R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
    R2_BUCKET / R2_DOMAIN_URL
"""

from __future__ import annotations

import hashlib
import os
import struct
from urllib.parse import quote

import httpx
import structlog
from botocore.auth import S3SigV4Auth
from botocore.credentials import Credentials

log = structlog.get_logger()


def content_hash(data: str) -> int:
    """Compute a signed int64 hash for Postgres bigint storage."""
    digest = hashlib.sha256(data.encode("utf-8")).digest()
    return struct.unpack(">q", digest[:8])[0]


_http_client: httpx.AsyncClient | None = None
_signer: S3SigV4Auth | None = None


def _get_signer() -> S3SigV4Auth:
    global _signer
    if _signer is None:
        creds = Credentials(
            access_key=os.environ["R2_ACCESS_KEY_ID"],
            secret_key=os.environ["R2_SECRET_ACCESS_KEY"],
        )
        _signer = S3SigV4Auth(creds, "s3", "auto")
    return _signer


def _get_http() -> httpx.AsyncClient:
    """Return the shared httpx client (lazy-initialized)."""
    global _http_client
    if _http_client is None:
        from src.config import settings

        max_conns = max(settings.r2_max_connections, 10)
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0),
            limits=httpx.Limits(
                max_connections=max_conns,
                max_keepalive_connections=max_conns,
            ),
        )
    return _http_client


def _endpoint() -> str:
    return os.environ["R2_ENDPOINT_URL"].rstrip("/")


def _bucket() -> str:
    return os.environ["R2_BUCKET"]


def _prefix(posting_id: str) -> str:
    """R2 key prefix — deterministic from posting ID, no DB column needed."""
    return f"job/{posting_id}"


def _object_url(key: str) -> str:
    return f"{_endpoint()}/{_bucket()}/{quote(key, safe='/')}"


def _sign(method: str, url: str, headers: dict, data: bytes = b"") -> dict:
    """Sign a request using S3 SigV4 and return the signed headers."""
    from botocore.awsrequest import AWSRequest

    req = AWSRequest(method=method, url=url, headers=headers, data=data)
    _get_signer().add_auth(req)
    return dict(req.headers)


async def _get_object(key: str) -> str | None:
    """Download an object as UTF-8 text. Returns None if not found."""
    url = _object_url(key)
    headers = _sign("GET", url, {})
    resp = await _get_http().get(url, headers=headers)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text


async def _put_object(key: str, body: str, content_type: str = "text/html") -> None:
    url = _object_url(key)
    data = body.encode("utf-8")
    headers = _sign(
        "PUT",
        url,
        {
            "Content-Type": content_type,
            "Cache-Control": "public, max-age=86400",
        },
        data,
    )
    resp = await _get_http().put(url, headers=headers, content=data)
    resp.raise_for_status()


async def put_description(posting_id: str, locale: str, html: str) -> None:
    """Upload a description to R2. Overwrites any existing version."""
    key = f"job/{posting_id}/{locale}/latest.html"
    await _put_object(key, html)


async def get_description_html(posting_id: str, locale: str) -> str | None:
    """Fetch the latest HTML description from R2. Returns None if not found."""
    key = f"{_prefix(posting_id)}/{locale}/latest.html"
    return await _get_object(key)


def get_description_url(posting_id: str, locale: str) -> str:
    """Return the public CDN URL for a description."""
    domain = os.environ["R2_DOMAIN_URL"].rstrip("/")
    return f"{domain}/{_prefix(posting_id)}/{locale}/latest.html"
