"""Shared helpers for parsing embedded JSON blobs from HTML pages.

Supports multiple sources:
- ``nextdata`` — Next.js ``<script id="__NEXT_DATA__">`` (default)
- ``reactrouter`` — React Router ``window.__staticRouterHydrationData``
- ``rsc`` — Next.js App Router RSC flight payload (``self.__next_f.push``)

Used by both the nextdata monitor and the nextdata scraper.
"""

from __future__ import annotations

import json
import re

import jmespath

NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

REACT_ROUTER_RE = re.compile(
    r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\(\"(.+?)\"\);",
)

RSC_PUSH_RE = re.compile(
    r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)',
)


def resolve_path(data: dict, path: str) -> object:
    """Walk a path through nested dicts/lists using jmespath.

    Supports dot-separated paths (``a.b.c``), array indexing (``a[0].b``),
    and array wildcards (``a[].b``).

    >>> resolve_path({"a": {"b": [1, 2]}}, "a.b")
    [1, 2]
    """
    if not path:
        return data
    return jmespath.search(path, data)


def extract_field(item: dict, spec: str) -> str | list[str] | None:
    """Extract a value from *item* using a jmespath expression.

    - Simple key: ``"text"`` -> ``item["text"]``
    - Nested key: ``"category.name"`` -> ``item["category"]["name"]``
    - Array unwrap: ``"locations[].name"`` -> ``[loc["name"] for loc in item["locations"]]``
    - Array index: ``"[1]"`` -> positional access
    """
    result = jmespath.search(spec, item)
    if result is None:
        return None
    if isinstance(result, list):
        values = [str(v) for v in result if v is not None]
        return values or None
    return str(result)


def extract_next_data(html: str) -> dict | None:
    """Extract and parse the ``__NEXT_DATA__`` JSON from *html*."""
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def extract_react_router_data(html: str) -> dict | None:
    """Extract and parse React Router ``__staticRouterHydrationData``.

    The data is double-encoded: ``JSON.parse("...")`` wraps an escaped
    JSON string, so we decode the string literal first, then parse.
    """
    match = REACT_ROUTER_RE.search(html)
    if not match:
        return None
    try:
        # The captured group is a JSON-escaped string (inner quotes escaped).
        # Wrap it back in quotes to let json.loads unescape it, then parse.
        unescaped = json.loads('"' + match.group(1) + '"')
        return json.loads(unescaped)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_rsc_data(html: str) -> dict | None:
    """Extract structured data from Next.js RSC flight payload.

    RSC flight payloads are delivered via ``self.__next_f.push()`` calls::

        <script>self.__next_f.push([1,"7:[\"$\",\"$L10\",null,{...}]\\n"])</script>

    The string argument is JSON-escaped.  Inside, each line has the format
    ``<id>:<payload>`` where *payload* is a JSON array
    ``["$","$L...",null,{...actual data...}]``.

    Returns a merged dict of all extracted data objects, or ``None``.
    """
    chunks = RSC_PUSH_RE.findall(html)
    if not chunks:
        return None

    merged: dict = {}
    for raw in chunks:
        try:
            unescaped = json.loads('"' + raw + '"')
        except (json.JSONDecodeError, ValueError):
            continue

        for line in unescaped.split("\n"):
            line = line.strip()
            if not line:
                continue
            # RSC line format: <hex-id>:<payload>
            colon = line.find(":")
            if colon < 1:
                continue
            payload = line[colon + 1 :]
            if not payload or payload[0] not in "{[":
                continue
            try:
                parsed = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue
            # RSC arrays: ["$","$L...",null,{...}] — data dict at index 3
            if isinstance(parsed, list) and len(parsed) >= 4 and isinstance(parsed[3], dict):
                merged.update(parsed[3])
            elif isinstance(parsed, dict):
                merged.update(parsed)

    return merged or None


def extract_embedded_json(html: str, source: str = "nextdata") -> dict | None:
    """Dispatch to the right extractor based on *source*.

    Supported values: ``"nextdata"`` (default), ``"reactrouter"``, ``"rsc"``.
    """
    if source == "reactrouter":
        return extract_react_router_data(html)
    if source == "rsc":
        return extract_rsc_data(html)
    return extract_next_data(html)
