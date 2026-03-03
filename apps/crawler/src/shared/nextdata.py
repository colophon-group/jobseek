"""Shared helpers for parsing Next.js ``__NEXT_DATA__`` JSON blobs.

Used by both the nextdata monitor and the nextdata scraper.
"""

from __future__ import annotations

import json
import re

NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


def resolve_path(data: dict, path: str) -> object:
    """Walk a dot-separated *path* through nested dicts.

    >>> resolve_path({"a": {"b": [1, 2]}}, "a.b")
    [1, 2]
    """
    current: object = data
    for key in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def extract_field(item: dict, spec: str) -> str | list[str] | None:
    """Extract a value from *item* using a field spec.

    - Simple key: ``"text"`` -> ``item["text"]``
    - Nested key: ``"category.name"`` -> ``item["category"]["name"]``
    - Array unwrap: ``"locations[].name"`` -> ``[loc["name"] for loc in item["locations"]]``
    """
    if "[]." in spec:
        array_key, rest = spec.split("[].", 1)
        arr = resolve_path(item, array_key) if "." in array_key else item.get(array_key)
        if not isinstance(arr, list):
            return None
        values = []
        for entry in arr:
            val = resolve_path(entry, rest) if "." in rest else (entry.get(rest) if isinstance(entry, dict) else None)
            if val is not None:
                values.append(str(val))
        return values or None
    # Simple or nested key
    val = resolve_path(item, spec) if "." in spec else item.get(spec)
    if val is None:
        return None
    if isinstance(val, list):
        return [str(v) for v in val]
    return str(val)


def extract_next_data(html: str) -> dict | None:
    """Extract and parse the ``__NEXT_DATA__`` JSON from *html*."""
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None
