"""Notion monitor — enumerate job pages from a public Notion site.

Notion career pages (``*.notion.site``) host job listings as sub-pages of a
parent page.  This monitor uses Notion's internal API (``/api/v3``) to
enumerate child pages without browser rendering.

Config (``board.metadata``):
    page_id         UUID of the parent page whose children are job posts.
                    Auto-detected during probing when omitted.
    space_id        Notion space UUID.  Auto-detected from the board URL.
    include_nested  If true, also include grandchild pages (default: false).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import register

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOTION_SITE_RE = re.compile(r"^([\w-]+)\.notion\.site$")
_HEX32_RE = re.compile(r"[0-9a-f]{32}")


def _parse_notion_url(url: str) -> tuple[str | None, str | None]:
    """Extract (subdomain, page_id) from a ``*.notion.site`` URL.

    Returns (None, None) when the URL is not a Notion site.
    """
    parsed = urlparse(url)
    m = _NOTION_SITE_RE.match(parsed.netloc)
    if not m:
        return None, None
    subdomain = m.group(1)
    # Page ID is the last 32 hex chars in the path (with or without title slug)
    path = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    # Remove query/fragment, take last 32 hex chars
    clean = re.sub(r"[^0-9a-fA-F]", "", path)
    page_id: str | None = None
    if len(clean) >= 32:
        raw = clean[-32:]
        page_id = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return subdomain, page_id


def _to_uuid(raw: str) -> str:
    """Normalise a 32-char hex string to UUID format (8-4-4-4-12)."""
    raw = raw.replace("-", "")
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _page_url(subdomain: str, page_id: str) -> str:
    """Build a public Notion page URL from subdomain and UUID page ID."""
    return f"https://{subdomain}.notion.site/{page_id.replace('-', '')}"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

_API_TIMEOUT = 15.0


async def _api_post(
    client: httpx.AsyncClient,
    subdomain: str,
    endpoint: str,
    payload: dict,
) -> dict | None:
    """POST to a Notion internal API endpoint, return parsed JSON or None."""
    url = f"https://{subdomain}.notion.site/api/v3/{endpoint}"
    try:
        resp = await client.post(url, json=payload, timeout=_API_TIMEOUT)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


async def _get_public_page_data(
    client: httpx.AsyncClient,
    subdomain: str,
    page_id: str,
) -> dict | None:
    """Call getPublicPageData to retrieve space info and public home page."""
    return await _api_post(client, subdomain, "getPublicPageData", {
        "blockId": page_id,
        "type": "block-space",
        "name": "page",
        "requestedOnPublicDomain": True,
        "showOriginalLink": False,
        "spaceDomain": subdomain,
    })


async def _load_page_chunk(
    client: httpx.AsyncClient,
    subdomain: str,
    page_id: str,
    limit: int = 100,
) -> dict | None:
    """Call loadPageChunk to retrieve page blocks."""
    return await _api_post(client, subdomain, "loadPageChunk", {
        "page": {"id": page_id},
        "limit": limit,
        "cursor": {"stack": []},
        "chunkNumber": 0,
        "verticalColumns": False,
    })


def _extract_child_pages(
    data: dict,
    parent_id: str,
    *,
    include_nested: bool = False,
) -> list[dict]:
    """Return child page blocks of *parent_id* from a loadPageChunk response.

    Each returned dict has ``id`` and ``title`` keys.
    """
    blocks = data.get("recordMap", {}).get("block", {})
    parent_block = blocks.get(parent_id, {})
    parent_val = parent_block.get("value", {}).get("value") or parent_block.get("value", {})
    content_ids: list[str] = parent_val.get("content", [])

    pages: list[dict] = []
    for cid in content_ids:
        block = blocks.get(cid, {})
        val = block.get("value", {}).get("value") or block.get("value", {})
        if val.get("type") != "page":
            continue
        if not val.get("alive", True):
            continue
        title = _extract_title(val)
        pages.append({"id": cid, "title": title})

        # Optionally include grandchildren
        if include_nested:
            for gcid in val.get("content", []):
                gc_block = blocks.get(gcid, {})
                gc_val = gc_block.get("value", {}).get("value") or gc_block.get("value", {})
                if gc_val.get("type") == "page" and gc_val.get("alive", True):
                    pages.append({"id": gcid, "title": _extract_title(gc_val)})

    return pages


def _extract_title(val: dict) -> str:
    """Extract plain-text title from a Notion block value."""
    props = val.get("properties", {})
    title_parts = props.get("title", [])
    if isinstance(title_parts, list):
        return "".join(part[0] for part in title_parts if isinstance(part, list) and part)
    return ""


# ---------------------------------------------------------------------------
# Monitor interface
# ---------------------------------------------------------------------------


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect whether *url* is a public Notion site with job sub-pages."""
    subdomain, page_id = _parse_notion_url(url)
    if not subdomain or not client:
        return None

    # Get space info
    probe_page_id = page_id or "index"
    public_data = await _get_public_page_data(client, subdomain, probe_page_id)
    if not public_data or not public_data.get("spaceId"):
        return None

    space_id = public_data["spaceId"]
    public_home = public_data.get("publicHomePage")

    # Try the URL's page first, then the public home page
    candidates = []
    if page_id:
        candidates.append(page_id)
    if public_home and public_home not in candidates:
        candidates.append(public_home)

    for cand_id in candidates:
        chunk = await _load_page_chunk(client, subdomain, cand_id)
        if not chunk:
            continue
        pages = _extract_child_pages(chunk, cand_id)
        if pages:
            log.info(
                "notion.detected_by_probe",
                url=url,
                page_id=cand_id,
                jobs=len(pages),
            )
            return {
                "page_id": cand_id,
                "space_id": space_id,
                "jobs": len(pages),
            }

    return None


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> set[str]:
    """Enumerate job page URLs from a Notion site."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]
    subdomain, url_page_id = _parse_notion_url(board_url)
    if not subdomain:
        raise ValueError(f"Not a Notion site URL: {board_url}")

    page_id = metadata.get("page_id") or url_page_id
    if not page_id:
        raise ValueError(
            "Cannot determine page_id. Provide it in config: "
            '{"page_id": "<uuid>"}'
        )

    include_nested = metadata.get("include_nested", False)

    chunk = await _load_page_chunk(client, subdomain, page_id)
    if not chunk:
        log.warning("notion.load_failed", page_id=page_id)
        return set()

    pages = _extract_child_pages(chunk, page_id, include_nested=include_nested)
    urls = {_page_url(subdomain, p["id"]) for p in pages}

    log.info("notion.discovered", page_id=page_id, jobs=len(urls))
    return urls


register("notion", discover, cost=15, can_handle=can_handle)
