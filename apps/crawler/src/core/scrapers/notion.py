"""Notion scraper — extract job details from a public Notion page.

Uses Notion's internal API (``/api/v3/loadPageChunk``) to fetch page
blocks and convert them to structured HTML.  No browser rendering needed.

When the page is a row in a Notion database (collection), the scraper also
extracts structured properties (location, department, employment type) by
reading the collection schema and mapping property names to job fields.

Config (``scraper_config``):
    property_map    Map collection property names to job fields.
                    Keys: Notion property names (case-insensitive).
                    Values: job field names (location, employment_type,
                    job_location_type, metadata.team, etc.)
                    Default auto-maps common names.
"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import urlparse

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Notion URL parsing
# ---------------------------------------------------------------------------

_NOTION_SITE_RE = re.compile(r"^([\w-]+)\.notion\.site$")


def _parse_notion_url(url: str) -> tuple[str | None, str | None]:
    """Extract (subdomain, page_id) from a notion.site URL."""
    parsed = urlparse(url)
    m = _NOTION_SITE_RE.match(parsed.netloc)
    if not m:
        return None, None
    subdomain = m.group(1)
    path = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else ""
    clean = re.sub(r"[^0-9a-fA-F]", "", path)
    if len(clean) >= 32:
        raw = clean[-32:]
        page_id = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
        return subdomain, page_id
    return subdomain, None


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

_API_TIMEOUT = 15.0


async def _load_page_chunk(
    client: httpx.AsyncClient,
    subdomain: str,
    page_id: str,
) -> dict | None:
    url = f"https://{subdomain}.notion.site/api/v3/loadPageChunk"
    try:
        resp = await client.post(
            url,
            json={
                "page": {"id": page_id},
                "limit": 200,
                "cursor": {"stack": []},
                "chunkNumber": 0,
                "verticalColumns": False,
            },
            timeout=_API_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Collection property extraction
# ---------------------------------------------------------------------------

# Default mapping: Notion property name (lowercase) -> JobContent field
_DEFAULT_PROPERTY_MAP: dict[str, str] = {
    "location": "locations",
    "locations": "locations",
    "city": "locations",
    "office": "locations",
    "department": "metadata.team",
    "team": "metadata.team",
    "employment type": "employment_type",
    "type": "employment_type",
    "contract type": "employment_type",
    "remote": "job_location_type",
    "work model": "job_location_type",
}


def _build_schema_map(data: dict) -> dict[str, tuple[str, str]]:
    """Build a map from property_id -> (property_name, property_type) from collection schema."""
    collections = data.get("recordMap", {}).get("collection", {})
    result: dict[str, tuple[str, str]] = {}
    for _cid, cdata in collections.items():
        val = cdata.get("value", {}).get("value") or cdata.get("value", {})
        schema = val.get("schema", {})
        for prop_id, prop in schema.items():
            name = prop.get("name", "")
            ptype = prop.get("type", "")
            if name:
                result[prop_id] = (name, ptype)
    return result


def _extract_property_value(raw: list) -> str:
    """Extract a plain string from a Notion property value array."""
    if not isinstance(raw, list):
        return ""
    parts = []
    for segment in raw:
        if isinstance(segment, list) and segment:
            parts.append(str(segment[0]))
    return "".join(parts)


def _extract_collection_properties(
    data: dict,
    page_id: str,
    property_map: dict[str, str] | None = None,
) -> dict[str, str | list[str]]:
    """Extract mapped properties from a collection row.

    Returns a dict of job field name -> value.
    """
    schema_map = _build_schema_map(data)
    if not schema_map:
        return {}

    # Merge default + custom property map
    pmap: dict[str, str] = dict(_DEFAULT_PROPERTY_MAP)
    if property_map:
        pmap.update({k.lower(): v for k, v in property_map.items()})

    blocks = data.get("recordMap", {}).get("block", {})
    page_block = blocks.get(page_id, {})
    page_val = page_block.get("value", {}).get("value") or page_block.get("value", {})
    properties = page_val.get("properties", {})

    result: dict[str, str | list[str]] = {}
    for prop_id, (prop_name, prop_type) in schema_map.items():
        if prop_id == "title":
            continue
        field_name = pmap.get(prop_name.lower())
        if not field_name:
            continue
        raw = properties.get(prop_id)
        if not raw:
            continue
        value = _extract_property_value(raw)
        if not value.strip():
            continue

        # Split comma-separated values for multi_select
        if prop_type == "multi_select" and field_name == "locations":
            result[field_name] = [v.strip() for v in value.split(",") if v.strip()]
        else:
            result[field_name] = value

    return result


# ---------------------------------------------------------------------------
# Block → HTML conversion
# ---------------------------------------------------------------------------

_BLOCK_RENDERERS: dict[str, str] = {
    "header": "h1",
    "sub_header": "h2",
    "sub_sub_header": "h3",
    "text": "p",
    "quote": "blockquote",
    "callout": "div",
}


def _render_rich_text(title_parts: list) -> str:
    """Convert Notion rich text array to HTML with inline formatting."""
    if not isinstance(title_parts, list):
        return ""
    parts: list[str] = []
    for segment in title_parts:
        if not isinstance(segment, list) or not segment:
            continue
        text = escape(segment[0])
        if len(segment) > 1 and isinstance(segment[1], list):
            for anno in segment[1]:
                if not isinstance(anno, list) or not anno:
                    continue
                code = anno[0]
                if code == "b":
                    text = f"<strong>{text}</strong>"
                elif code == "i":
                    text = f"<em>{text}</em>"
                elif code == "a" and len(anno) > 1:
                    href = escape(anno[1])
                    text = f'<a href="{href}">{text}</a>'
        parts.append(text)
    return "".join(parts)


def _blocks_to_html(data: dict, page_id: str) -> str:
    """Convert page content blocks to an HTML fragment."""
    blocks = data.get("recordMap", {}).get("block", {})
    page_block = blocks.get(page_id, {})
    page_val = page_block.get("value", {}).get("value") or page_block.get("value", {})
    content_ids: list[str] = page_val.get("content", [])

    html_parts: list[str] = []
    in_list = False

    for cid in content_ids:
        block = blocks.get(cid, {})
        val = block.get("value", {}).get("value") or block.get("value", {})
        btype = val.get("type", "")
        props = val.get("properties", {})
        title_raw = props.get("title", [])
        text = _render_rich_text(title_raw)

        if btype not in ("bulleted_list", "numbered_list") and in_list:
            html_parts.append("</ul>")
            in_list = False

        if btype in _BLOCK_RENDERERS:
            tag = _BLOCK_RENDERERS[btype]
            if text.strip():
                html_parts.append(f"<{tag}>{text}</{tag}>")

        elif btype in ("bulleted_list", "numbered_list"):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{text}</li>")

        elif btype == "divider":
            html_parts.append("<hr>")

        elif btype == "toggle":
            if text.strip():
                html_parts.append(f"<h3>{text}</h3>")
            for child_id in val.get("content", []):
                child = blocks.get(child_id, {})
                child_val = child.get("value", {}).get("value") or child.get("value", {})
                child_type = child_val.get("type", "")
                child_text = _render_rich_text(child_val.get("properties", {}).get("title", []))
                if child_type in ("bulleted_list", "numbered_list") and child_text.strip():
                    if not in_list:
                        html_parts.append("<ul>")
                        in_list = True
                    html_parts.append(f"<li>{child_text}</li>")
                elif child_text.strip():
                    tag = _BLOCK_RENDERERS.get(child_type, "p")
                    html_parts.append(f"<{tag}>{child_text}</{tag}>")

    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def _extract_title(data: dict, page_id: str) -> str:
    blocks = data.get("recordMap", {}).get("block", {})
    page_block = blocks.get(page_id, {})
    page_val = page_block.get("value", {}).get("value") or page_block.get("value", {})
    title_parts = page_val.get("properties", {}).get("title", [])
    if isinstance(title_parts, list):
        return "".join(part[0] for part in title_parts if isinstance(part, list) and part)
    return ""


# ---------------------------------------------------------------------------
# Scraper interface
# ---------------------------------------------------------------------------


def can_handle(htmls: list[str]) -> dict | None:
    """Notion pages are detected by URL pattern, not HTML content."""
    return None


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    pw=None,
    **kwargs,
) -> JobContent:
    """Scrape a single Notion job page via the internal API."""
    subdomain, page_id = _parse_notion_url(url)
    if not subdomain or not page_id:
        log.warning("notion_scraper.invalid_url", url=url)
        return JobContent()

    data = await _load_page_chunk(http, subdomain, page_id)
    if not data:
        log.warning("notion_scraper.load_failed", url=url)
        return JobContent()

    title = _extract_title(data, page_id)
    description = _blocks_to_html(data, page_id)

    # Extract collection properties if this page is a database row
    property_map = config.get("property_map")
    props = _extract_collection_properties(data, page_id, property_map)

    locations = props.get("locations") if isinstance(props.get("locations"), list) else None
    employment_type = props.get("employment_type")
    job_location_type = props.get("job_location_type")
    metadata = {}
    if props.get("metadata.team"):
        metadata["team"] = props["metadata.team"]

    log.info(
        "notion_scraper.scraped",
        url=url,
        title=title[:60] if title else "(empty)",
        description_len=len(description),
        locations=locations,
        employment_type=employment_type,
    )

    return JobContent(
        title=title or None,
        description=description or None,
        locations=locations,
        employment_type=employment_type,
        job_location_type=job_location_type,
        metadata=metadata or None,
    )


register("notion", scrape)
