"""Upload and diff-track job descriptions on Cloudflare R2.

R2 layout per posting:
    job/{posting_id}/{locale}/latest.html
    job/{posting_id}/{locale}/history.json   — description diffs + extras changes

history.json structure:
    {"current_extras": {...}, "versions": [{...}, ...]}

    current_extras: latest structured data snapshot (title, locations, metadata, etc.)
    versions: list of change entries (newest first), each containing:
        {"timestamp": "...", "diff": "...", "extras": {"field": old_value}}
        - "diff": reverse unified diff (only if description changed)
        - "extras": dict of {field: previous_value} for changed fields only
          (null = field was added, absent = unchanged)

Environment variables (shared with image_sync):
    R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
    R2_BUCKET / R2_DOMAIN_URL
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import struct
from datetime import UTC, datetime
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


_http: httpx.AsyncClient | None = None
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
    global _http
    if _http is None:
        _http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=10.0),
        )
    return _http


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


async def get_object(key: str) -> str | None:
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


def _compute_reverse_diff(new_html: str, old_html: str) -> str:
    """Unified diff from new → old (reverse patch)."""
    return "".join(
        difflib.unified_diff(
            new_html.splitlines(keepends=True),
            old_html.splitlines(keepends=True),
            fromfile="new",
            tofile="old",
        )
    )


def _extras_diff(old: dict, new: dict) -> dict:
    """Compute changed fields between old and new extras.

    Returns a dict of {field: old_value} for fields that changed.
    - Field present with a value → it changed (value is the previous value)
    - Field present with null → it was added (no previous value)
    - Field absent → unchanged
    """
    changed: dict = {}
    all_keys = set(old) | set(new)
    for key in all_keys:
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            changed[key] = old_val  # None if key was absent in old
    return changed


async def upload_posting(
    posting_id: str,
    locale: str,
    html: str,
    extras: dict,
) -> None:
    """Upload or update a posting's description + extras on R2.

    Records a history entry with description diff and/or changed extras fields.
    No separate extras.json — latest extras state is reconstructable from history.

    History entry format:
        {"timestamp": "...", "diff": "...", "extras": {"field": old_value}}
    - "diff": reverse unified diff (only if description changed)
    - "extras": dict of {field: previous_value} for changed fields only
      (null = field was added, absent = unchanged)
    """
    prefix = _prefix(posting_id)
    latest_key = f"{prefix}/{locale}/latest.html"
    history_key = f"{prefix}/{locale}/history.json"

    existing_html = await get_object(latest_key)

    # Load previous extras from the first history entry's snapshot
    history_raw = await get_object(history_key)
    history = json.loads(history_raw) if history_raw else {"versions": []}
    existing_extras: dict = history.get("current_extras", {})

    # Carry forward metadata from previous extras when the new upload
    # doesn't provide it.  Monitors produce metadata (e.g. employer,
    # expiration_date) but scrapers typically don't — without this,
    # each scrape would "remove" metadata and each monitor would "add"
    # it back, creating spurious history churn.
    if "metadata" not in extras and "metadata" in existing_extras:
        extras = {**extras, "metadata": existing_extras["metadata"]}

    desc_changed = existing_html is not None and existing_html != html
    extras_changed_fields = _extras_diff(existing_extras, extras)
    is_first = existing_html is None

    if not is_first and not desc_changed and not extras_changed_fields:
        return  # nothing changed

    if is_first:
        # First upload — create history with current extras snapshot
        history = {"versions": [], "current_extras": extras}
        await _put_object(history_key, json.dumps(history), "application/json")
        log.info("description_store.created", posting_id=posting_id, locale=locale)
    else:
        entry: dict = {"timestamp": datetime.now(UTC).isoformat()}
        if desc_changed:
            entry["diff"] = _compute_reverse_diff(html, existing_html)
        if extras_changed_fields:
            entry["extras"] = extras_changed_fields

        history["versions"].insert(0, entry)
        history["current_extras"] = extras
        await _put_object(history_key, json.dumps(history), "application/json")
        log.info(
            "description_store.updated",
            posting_id=posting_id,
            locale=locale,
            desc_changed=desc_changed,
            extras_fields=list(extras_changed_fields.keys()) if extras_changed_fields else [],
        )

    if is_first or desc_changed:
        await _put_object(latest_key, html)


async def upload_description(
    posting_id: str,
    locale: str,
    html: str,
) -> None:
    """Upload or update a description on R2 (localization-only, no extras).

    Used for secondary locale descriptions. Primary locale should use upload_posting().
    """
    prefix = _prefix(posting_id)
    latest_key = f"{prefix}/{locale}/latest.html"
    history_key = f"{prefix}/{locale}/history.json"

    existing = await get_object(latest_key)

    if existing is not None and existing == html:
        return

    if existing is not None:
        diff = _compute_reverse_diff(html, existing)
        history_raw = await get_object(history_key)
        history = json.loads(history_raw) if history_raw else {"versions": []}
        history["versions"].insert(
            0,
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "diff": diff,
            },
        )
        await _put_object(history_key, json.dumps(history), "application/json")
        log.info(
            "description_store.updated",
            posting_id=posting_id,
            locale=locale,
            diff_len=len(diff),
        )
    else:
        await _put_object(history_key, json.dumps({"versions": []}), "application/json")
        log.info("description_store.created", posting_id=posting_id, locale=locale)

    await _put_object(latest_key, html)


async def get_description_html(posting_id: str, locale: str) -> str | None:
    """Fetch the latest HTML description from R2. Returns None if not found."""
    key = f"{_prefix(posting_id)}/{locale}/latest.html"
    return await get_object(key)


def get_description_url(posting_id: str, locale: str) -> str:
    """Return the public CDN URL for a description."""
    domain = os.environ["R2_DOMAIN_URL"].rstrip("/")
    return f"{domain}/{_prefix(posting_id)}/{locale}/latest.html"
