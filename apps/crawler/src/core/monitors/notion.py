"""Notion monitor — enumerate job pages from a public Notion site.

Notion career pages (``*.notion.site``) host job listings either as
sub-pages of a parent page or as rows in a Notion database (collection).
This monitor uses Notion's internal API (``/api/v3``) to enumerate them
without browser rendering.

No configuration is required — the monitor resolves everything from the
board URL.  It tries the URL's own page first, then falls back to the
site's public home page, searching for both child pages and embedded
database views.

Config (``board.metadata``):
    include_nested    If true, also include grandchild pages (default: false).
    url_filter        Regex or {"include": ..., "exclude": ...} to filter result
                      URLs — same semantics as the dom/sitemap monitors.
    collection_index  Zero-based index of which collection_view to use when a
                      page has multiple databases (default: all).
    title_exclude     Regex pattern — exclude rows whose title matches.
                      Example: "Stay up to date|Coming soon"
    property_filter   Filter collection rows by property values.
                      {"exclude": {"Department": "Archived"}} or
                      {"include": {"Status": "Open"}}
                      Property names are matched case-insensitively.
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
    clean = re.sub(r"[^0-9a-fA-F]", "", path)
    if len(clean) >= 32:
        raw = clean[-32:]
        return subdomain, _to_uuid(raw)
    return subdomain, path


def _to_uuid(raw: str) -> str:
    """Normalise a 32-char hex string to UUID format (8-4-4-4-12)."""
    raw = raw.replace("-", "")
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _is_uuid(value: str) -> bool:
    return bool(re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value,
    ))


def _page_url(subdomain: str, page_id: str) -> str:
    return f"https://{subdomain}.notion.site/{page_id.replace('-', '')}"


def _apply_url_filter(urls: set[str], url_filter) -> set[str]:
    """Apply a url_filter (string regex or {"include": ..., "exclude": ...})."""
    if not url_filter:
        return urls
    if isinstance(url_filter, str):
        pat = re.compile(url_filter)
        return {u for u in urls if pat.search(u)}
    include = url_filter.get("include")
    exclude = url_filter.get("exclude")
    result = urls
    if include:
        pat = re.compile(include)
        result = {u for u in result if pat.search(u)}
    if exclude:
        pat = re.compile(exclude)
        result = {u for u in result if not pat.search(u)}
    return result


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
    block_id: str | None = None,
) -> dict | None:
    """Call getPublicPageData to retrieve space info and public home page.

    When *block_id* is None, omits it from the payload — this resolves the
    site's default space/home page via the subdomain alone.
    """
    payload: dict = {
        "type": "block-space",
        "name": "page",
        "requestedOnPublicDomain": True,
        "showOriginalLink": False,
        "spaceDomain": subdomain,
    }
    if block_id:
        payload["blockId"] = block_id
    return await _api_post(client, subdomain, "getPublicPageData", payload)


async def _load_page_chunk(
    client: httpx.AsyncClient,
    subdomain: str,
    page_id: str,
    limit: int = 200,
) -> dict | None:
    return await _api_post(client, subdomain, "loadPageChunk", {
        "page": {"id": page_id},
        "limit": limit,
        "cursor": {"stack": []},
        "chunkNumber": 0,
        "verticalColumns": False,
    })


async def _query_collection(
    client: httpx.AsyncClient,
    subdomain: str,
    collection_id: str,
    view_id: str,
    space_id: str,
) -> list[dict]:
    """Query a Notion collection and return rows.

    Each row dict has ``id``, ``title``, and ``properties`` (a dict of
    human-readable property name → string value).
    """
    data = await _api_post(client, subdomain, "queryCollection", {
        "source": {
            "type": "collection",
            "id": collection_id,
            "spaceId": space_id,
        },
        "collectionView": {
            "id": view_id,
            "spaceId": space_id,
        },
        "loader": {
            "type": "reducer",
            "reducers": {
                "collection_group_results": {
                    "type": "results",
                    "limit": 300,
                }
            },
            "searchQuery": "",
            "userTimeZone": "UTC",
        },
    })
    if not data:
        return []
    block_ids = (
        data.get("result", {})
        .get("reducerResults", {})
        .get("collection_group_results", {})
        .get("blockIds", [])
    )

    # Build schema map: prop_id -> prop_name
    schema_map: dict[str, str] = {}
    for _cid, cdata in data.get("recordMap", {}).get("collection", {}).items():
        cval = cdata.get("value", {}).get("value") or cdata.get("value", {})
        for prop_id, prop in cval.get("schema", {}).items():
            if prop.get("name") and prop_id != "title":
                schema_map[prop_id] = prop["name"]

    blocks = data.get("recordMap", {}).get("block", {})
    rows: list[dict] = []
    for rid in block_ids:
        b = blocks.get(rid, {})
        val = b.get("value", {}).get("value") or b.get("value", {})
        title = _extract_title(val)
        raw_props = val.get("properties", {})

        # Map internal prop IDs to human-readable names
        named_props: dict[str, str] = {}
        for prop_id, prop_name in schema_map.items():
            raw = raw_props.get(prop_id)
            if raw and isinstance(raw, list):
                value = "".join(
                    seg[0] for seg in raw if isinstance(seg, list) and seg
                )
                if value.strip():
                    named_props[prop_name] = value

        rows.append({"id": rid, "title": title, "properties": named_props})
    return rows


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


async def _resolve_site(
    client: httpx.AsyncClient,
    subdomain: str,
    path_hint: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Resolve a board URL to (page_id, public_home_id, space_id)."""
    if path_hint and _is_uuid(path_hint):
        public_data = await _get_public_page_data(client, subdomain, path_hint)
        if not public_data or not public_data.get("spaceId"):
            return None, None, None
        return (
            path_hint,
            public_data.get("publicHomePage"),
            public_data["spaceId"],
        )

    # Slug or root — resolve via subdomain (no blockId)
    public_data = await _get_public_page_data(client, subdomain)
    if not public_data or not public_data.get("spaceId"):
        return None, None, None

    space_id = public_data["spaceId"]
    public_home = public_data.get("publicHomePage")

    if not public_home:
        return None, None, space_id

    if not path_hint:
        return public_home, public_home, space_id

    # Slug — try to find matching page in the home page's block tree
    chunk = await _load_page_chunk(client, subdomain, public_home)
    if chunk:
        found = _find_page_by_slug(chunk, path_hint)
        if found:
            return found, public_home, space_id

    return public_home, public_home, space_id


def _find_page_by_slug(data: dict, slug: str) -> str | None:
    """Search all blocks for a page whose title matches *slug*."""
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


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def _extract_child_pages(
    data: dict,
    parent_id: str,
    *,
    include_nested: bool = False,
) -> list[dict]:
    """Return child page blocks of *parent_id*."""
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

        if include_nested:
            for gcid in val.get("content", []):
                gc_block = blocks.get(gcid, {})
                gc_val = gc_block.get("value", {}).get("value") or gc_block.get("value", {})
                if gc_val.get("type") == "page" and gc_val.get("alive", True):
                    pages.append({"id": gcid, "title": _extract_title(gc_val)})

    return pages


def _find_all_collection_views(data: dict) -> list[dict]:
    """Find ALL collection_view blocks anywhere in the page chunk.

    Searches every block, not just direct children — handles deeply nested
    collection views inside columns, toggles, callouts, etc.
    """
    blocks = data.get("recordMap", {}).get("block", {})
    results: list[dict] = []

    for _bid, bdata in blocks.items():
        val = bdata.get("value", {}).get("value") or bdata.get("value", {})
        if val.get("type") not in ("collection_view", "collection_view_page"):
            continue
        collection_id = val.get("collection_id")
        view_ids = val.get("view_ids", [])
        if collection_id and view_ids:
            results.append({
                "collection_id": collection_id,
                "view_id": view_ids[0],
            })

    return results


def _extract_title(val: dict) -> str:
    props = val.get("properties", {})
    title_parts = props.get("title", [])
    if isinstance(title_parts, list):
        return "".join(part[0] for part in title_parts if isinstance(part, list) and part)
    return ""


# ---------------------------------------------------------------------------
# Main logic — find job pages via multiple strategies
# ---------------------------------------------------------------------------


def _apply_row_filters(
    rows: list[dict],
    *,
    title_exclude: str | None = None,
    property_filter: dict | None = None,
) -> list[dict]:
    """Filter collection rows by title pattern and/or property values."""
    result = rows

    if title_exclude:
        pat = re.compile(title_exclude, re.IGNORECASE)
        before = len(result)
        result = [r for r in result if not pat.search(r["title"])]
        log.info("notion.title_exclude", pattern=title_exclude, before=before, after=len(result))

    if property_filter:
        exclude_props = property_filter.get("exclude", {})
        include_props = property_filter.get("include", {})

        if exclude_props:
            before = len(result)
            filtered = []
            for r in result:
                props = r.get("properties", {})
                skip = False
                for prop_name, prop_val in exclude_props.items():
                    actual = props.get(prop_name, "")
                    if actual.lower() == prop_val.lower():
                        skip = True
                        break
                if not skip:
                    filtered.append(r)
            result = filtered
            log.info(
                "notion.property_exclude",
                rules=exclude_props, before=before, after=len(result),
            )

        if include_props:
            before = len(result)
            filtered = []
            for r in result:
                props = r.get("properties", {})
                match = all(
                    props.get(pn, "").lower() == pv.lower()
                    for pn, pv in include_props.items()
                )
                if match:
                    filtered.append(r)
            result = filtered
            log.info(
                "notion.property_include",
                rules=include_props, before=before, after=len(result),
            )

    return result


async def _find_job_pages(
    client: httpx.AsyncClient,
    subdomain: str,
    path_hint: str | None,
    *,
    include_nested: bool = False,
    collection_index: int | None = None,
    title_exclude: str | None = None,
    property_filter: dict | None = None,
) -> list[dict]:
    """Resolve the board URL and return job pages.

    For each candidate page, tries:
    1. Direct child pages (sub-page pattern)
    2. All collection_view blocks on the page → queryCollection (database pattern)
    3. Falls back to the site's public home page
    """
    page_id, public_home, space_id = await _resolve_site(
        client, subdomain, path_hint,
    )

    candidates: list[str] = []
    if page_id:
        candidates.append(page_id)
    if public_home and public_home not in candidates:
        candidates.append(public_home)

    for cand_id in candidates:
        chunk = await _load_page_chunk(client, subdomain, cand_id)
        if not chunk:
            continue

        # Strategy 1: direct child pages
        pages = _extract_child_pages(chunk, cand_id, include_nested=include_nested)
        if pages:
            log.info(
                "notion.strategy",
                strategy="subpages",
                page_id=cand_id,
                count=len(pages),
            )
            return pages

        # Strategy 2: collection databases (search ALL blocks)
        if space_id:
            cvs = _find_all_collection_views(chunk)
            if cvs:
                # Log discovered collections for observability
                for i, cv in enumerate(cvs):
                    log.info(
                        "notion.collection_found",
                        index=i,
                        collection_id=cv["collection_id"],
                        view_id=cv["view_id"],
                    )

                # Apply collection_index filter if configured
                if collection_index is not None:
                    if 0 <= collection_index < len(cvs):
                        cvs = [cvs[collection_index]]
                        log.info("notion.collection_index", selected=collection_index)
                    else:
                        log.warning(
                            "notion.collection_index_out_of_range",
                            index=collection_index,
                            total=len(cvs),
                        )

                all_rows: list[dict] = []
                for i, cv in enumerate(cvs):
                    rows = await _query_collection(
                        client, subdomain,
                        cv["collection_id"], cv["view_id"], space_id,
                    )
                    log.info(
                        "notion.collection_query",
                        index=i,
                        collection_id=cv["collection_id"],
                        rows=len(rows),
                        titles=[r["title"] for r in rows],
                    )
                    # Filter out rows with empty titles (placeholders/garbage)
                    rows = [r for r in rows if r["title"].strip()]
                    all_rows.extend(rows)

                if all_rows:
                    # Apply title/property filters
                    all_rows = _apply_row_filters(
                        all_rows,
                        title_exclude=title_exclude,
                        property_filter=property_filter,
                    )
                    log.info(
                        "notion.strategy",
                        strategy="collection",
                        page_id=cand_id,
                        collections=len(cvs),
                        total_rows=len(all_rows),
                    )
                    return all_rows

    return []


# ---------------------------------------------------------------------------
# Monitor interface
# ---------------------------------------------------------------------------


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    subdomain, path_hint = _parse_notion_url(url)
    if not subdomain or not client:
        return None

    pages = await _find_job_pages(client, subdomain, path_hint)
    if not pages:
        return None

    log.info("notion.detected_by_probe", url=url, jobs=len(pages))
    return {"jobs": len(pages)}


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> set[str]:
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]
    subdomain, path_hint = _parse_notion_url(board_url)
    if not subdomain:
        raise ValueError(f"Not a Notion site URL: {board_url}")

    include_nested = metadata.get("include_nested", False)
    collection_index = metadata.get("collection_index")
    url_filter = metadata.get("url_filter")
    title_exclude = metadata.get("title_exclude")
    property_filter = metadata.get("property_filter")

    pages = await _find_job_pages(
        client, subdomain, path_hint,
        include_nested=include_nested,
        collection_index=collection_index,
        title_exclude=title_exclude,
        property_filter=property_filter,
    )
    if not pages:
        log.warning("notion.no_pages_found", board_url=board_url)
        return set()

    urls = {_page_url(subdomain, p["id"]) for p in pages}

    if url_filter:
        before = len(urls)
        urls = _apply_url_filter(urls, url_filter)
        log.info("notion.url_filter_applied", before=before, after=len(urls))

    log.info("notion.discovered", board_url=board_url, jobs=len(urls))
    return urls


register("notion", discover, cost=15, can_handle=can_handle)
