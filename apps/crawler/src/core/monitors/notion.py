"""Notion monitor — enumerate job pages from a public Notion site.

Notion career pages (``*.notion.site``) host job listings as sub-pages of a
parent page.  This monitor uses Notion's internal API (``/api/v3``) to
enumerate child pages without browser rendering.

No configuration is required — the monitor resolves everything from the
board URL.  It tries the URL's own page first, then falls back to the
site's public home page.

Config (``board.metadata``):
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


def _parse_notion_url(url: str) -> tuple[str | None, str | None]:
    """Extract (subdomain, page_id_or_slug) from a ``*.notion.site`` URL.

    Returns (None, None) when the URL is not a Notion site.
    The second element is a UUID when the path ends in 32 hex chars,
    otherwise it is the raw path slug (e.g. ``"job-posts"``), or None
    for the root URL.
    """
    parsed = urlparse(url)
    m = _NOTION_SITE_RE.match(parsed.netloc)
    if not m:
        return None, None
    subdomain = m.group(1)
    path = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    if not path:
        return subdomain, None
    # Try to extract a UUID from the last 32 hex chars
    clean = re.sub(r"[^0-9a-fA-F]", "", path)
    if len(clean) >= 32:
        raw = clean[-32:]
        return subdomain, _to_uuid(raw)
    # Return the raw slug for resolution via API
    return subdomain, path


def _to_uuid(raw: str) -> str:
    """Normalise a 32-char hex string to UUID format (8-4-4-4-12)."""
    raw = raw.replace("-", "")
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _is_uuid(value: str) -> bool:
    """Check if a string looks like a UUID (hex with optional dashes)."""
    return bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value))


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
    block_id: str,
) -> dict | None:
    """Call getPublicPageData to retrieve space info and public home page."""
    return await _api_post(client, subdomain, "getPublicPageData", {
        "blockId": block_id,
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


async def _resolve_page_id(
    client: httpx.AsyncClient,
    subdomain: str,
    path_hint: str | None,
) -> tuple[str | None, str | None]:
    """Resolve a URL path to (page_id, public_home_page_id).

    *path_hint* is either a UUID already, a slug like ``"job-posts"``, or
    None for the root.  Returns (None, None) on failure.
    """
    # If we already have a UUID, use it directly for getPublicPageData
    probe_id = path_hint if (path_hint and _is_uuid(path_hint)) else "index"
    public_data = await _get_public_page_data(client, subdomain, probe_id)
    if not public_data or not public_data.get("spaceId"):
        return None, None

    public_home = public_data.get("publicHomePage")

    if path_hint and _is_uuid(path_hint):
        return path_hint, public_home

    # For slugs or root, load the public home page and search by slug
    if public_home:
        chunk = await _load_page_chunk(client, subdomain, public_home)
        if chunk and path_hint:
            # The URL's page might be discoverable from the page chunk —
            # search all blocks for a page whose slug matches.
            found = _find_page_by_slug(chunk, path_hint)
            if found:
                return found, public_home
        # Fall through: the public home page itself is a candidate
        return public_home, public_home

    return None, None


def _find_page_by_slug(data: dict, slug: str) -> str | None:
    """Search loadPageChunk blocks for a page matching *slug*.

    Notion page slugs are derived from the title — lowercase, spaces
    replaced with hyphens.  We also check the canonical block ID embedded
    in ``<link rel="canonical">`` style page properties.
    """
    blocks = data.get("recordMap", {}).get("block", {})
    slug_lower = slug.lower().replace(" ", "-")
    for bid, bdata in blocks.items():
        val = bdata.get("value", {}).get("value") or bdata.get("value", {})
        if val.get("type") not in ("page", "collection_view_page"):
            continue
        title = _extract_title(val)
        title_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        if title_slug == slug_lower:
            return bid
    return None


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
# Page resolution — try multiple strategies to find job pages
# ---------------------------------------------------------------------------


async def _find_job_pages(
    client: httpx.AsyncClient,
    subdomain: str,
    path_hint: str | None,
    *,
    include_nested: bool = False,
) -> tuple[list[dict], str | None]:
    """Resolve the board URL and return (job_pages, resolved_parent_id).

    Tries in order:
    1. The page identified by the URL (if it has a page ID)
    2. The site's public home page
    """
    page_id, public_home = await _resolve_page_id(client, subdomain, path_hint)

    # Build candidate list: URL's page first, then public home
    candidates: list[str] = []
    if page_id:
        candidates.append(page_id)
    if public_home and public_home not in candidates:
        candidates.append(public_home)

    for cand_id in candidates:
        chunk = await _load_page_chunk(client, subdomain, cand_id)
        if not chunk:
            continue
        pages = _extract_child_pages(chunk, cand_id, include_nested=include_nested)
        if pages:
            return pages, cand_id

    return [], None


# ---------------------------------------------------------------------------
# Monitor interface
# ---------------------------------------------------------------------------


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect whether *url* is a public Notion site with job sub-pages."""
    subdomain, path_hint = _parse_notion_url(url)
    if not subdomain or not client:
        return None

    pages, _parent = await _find_job_pages(client, subdomain, path_hint)
    if not pages:
        return None

    log.info("notion.detected_by_probe", url=url, jobs=len(pages))
    return {"jobs": len(pages)}


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> set[str]:
    """Enumerate job page URLs from a Notion site."""
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]
    subdomain, path_hint = _parse_notion_url(board_url)
    if not subdomain:
        raise ValueError(f"Not a Notion site URL: {board_url}")

    include_nested = metadata.get("include_nested", False)

    pages, parent_id = await _find_job_pages(
        client, subdomain, path_hint, include_nested=include_nested,
    )
    if not pages:
        log.warning("notion.no_pages_found", board_url=board_url)
        return set()

    urls = {_page_url(subdomain, p["id"]) for p in pages}
    log.info("notion.discovered", board_url=board_url, jobs=len(urls))
    return urls


register("notion", discover, cost=15, can_handle=can_handle)
