"""Shared helpers for parsing Next.js ``__NEXT_DATA__`` JSON blobs.

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
