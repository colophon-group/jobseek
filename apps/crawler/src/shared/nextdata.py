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


def extract_field(item: dict, spec: str | list | dict) -> str | list[str] | None:
    """Extract a value from *item* using a field spec.

    **String** — jmespath expression (unchanged behavior).

    **List** — concatenation of specs (joined with ``\\n``).
      - Entries prefixed with ``=`` are literal constants.
      - jmespath entries returning None are skipped, along with
        any preceding constants (prevents orphaned headings).
      - Dict entries ``{"each": "<path>", "wrap": "<template>"}`` iterate
        an array of objects, replacing ``{field}`` placeholders.
      - Array results are flattened (each element becomes a part).

    **Dict with "concat" + optional "separator"** — like a list spec
      but with a custom separator (default ``\\n``)::

          {"concat": ["city_info.en_name", "city_info.parent.parent.en_name"],
           "separator": ", "}
          → "Warsaw, Poland"

    **Dict with "path" + "map"** — value mapping.
      Resolves the jmespath ``path``, stringifies the result, and looks
      it up in ``map``.  Returns the mapped value or ``None``::

          {"path": "homeOffice", "map": {"True": "remote"}}
    """
    if isinstance(spec, list):
        return _extract_concat(item, spec)

    if isinstance(spec, dict) and "concat" in spec:
        return _extract_concat(item, spec["concat"], separator=spec.get("separator", "\n"))

    if isinstance(spec, dict) and "path" in spec:
        if "map" in spec:
            return _extract_mapped(item, spec)
        return extract_field(item, spec["path"])

    # Constant string (=prefix) — return literal value
    if isinstance(spec, str) and spec.startswith("="):
        return spec[1:]

    result = jmespath.search(spec, item)
    if result is None:
        return None
    if isinstance(result, list):
        values = [str(v) for v in result if v is not None]
        return values or None
    return str(result)


def _extract_mapped(item: dict, spec: dict) -> str | list[str] | None:
    """Resolve a path and map the value through a lookup dict."""
    result = jmespath.search(spec["path"], item)
    if result is None:
        return None
    value_map = spec["map"]
    if isinstance(result, list):
        mapped = [str(value_map[str(v)]) for v in result if str(v) in value_map]
        return mapped or None
    key = str(result)
    mapped = value_map.get(key)
    return str(mapped) if mapped is not None else None


_HTML_TAG_RE = re.compile(r"<[a-zA-Z/]")


def _plain_to_html(text: str) -> str:
    """Convert plain text with newlines to HTML.

    - Lines starting with ``- `` become ``<li>`` items grouped in ``<ul>``.
    - Blank lines become paragraph breaks.
    - Other newlines become ``<br>``.

    Already-HTML content (contains ``<`` followed by a tag char) is returned
    unchanged.  Single-line text without newlines is returned as-is.
    """
    if _HTML_TAG_RE.search(text):
        return text
    if "\n" not in text:
        return text

    lines = text.split("\n")
    out: list[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        is_bullet = stripped.startswith("- ")

        if is_bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{stripped[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            if not stripped:
                out.append("<br>")
            else:
                out.append(stripped + "<br>")

    if in_list:
        out.append("</ul>")

    # Strip trailing <br> tags
    while out and out[-1] == "<br>":
        out.pop()
    if out and out[-1].endswith("<br>"):
        out[-1] = out[-1][:-4]

    return "\n".join(out)


def _extract_concat(item: dict, specs: list, *, separator: str = "\n") -> str | None:
    """Resolve a list of specs and concatenate results."""
    parts: list[str] = []
    pending_constants: list[str] = []
    had_data_expr = False  # Track whether any non-constant spec was seen

    for s in specs:
        # Constant string (=prefix)
        if isinstance(s, str) and s.startswith("="):
            pending_constants.append(s[1:])
            continue

        # Template dict: {"each": "path[*]", "wrap": "<h3>{text}</h3>\n{content}"}
        if isinstance(s, dict):
            had_data_expr = True
            each_path = s.get("each", "")
            wrap_tpl = s.get("wrap", "")
            arr = jmespath.search(each_path, item)
            if not arr or not isinstance(arr, list):
                pending_constants.clear()
                continue
            parts.extend(pending_constants)
            pending_constants.clear()
            for obj in arr:
                if not isinstance(obj, dict):
                    parts.append(str(obj))
                    continue
                rendered = wrap_tpl
                for key, val in obj.items():
                    rendered = rendered.replace(f"{{{key}}}", str(val) if val is not None else "")
                parts.append(rendered)
            continue

        # Regular jmespath expression
        had_data_expr = True
        result = jmespath.search(s, item)
        if result is None:
            pending_constants.clear()
            continue

        parts.extend(pending_constants)
        pending_constants.clear()

        if isinstance(result, list):
            parts.extend(_plain_to_html(str(v)) for v in result if v is not None)
        else:
            parts.append(_plain_to_html(str(result)))

    # Trailing constants: include if we have data or if the spec is constants-only.
    # Skip when data expressions were attempted but all resolved to None.
    if parts or not had_data_expr:
        parts.extend(pending_constants)

    return separator.join(parts) if parts else None


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
