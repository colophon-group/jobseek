"""Generalized extraction of structured JSON data embedded in HTML.

Handles multiple embedding patterns:
- ``<script id="...">`` blocks with JSON content
- Regex patterns followed by JSON (e.g. ``AF_initDataCallback``)
- Variable assignments (``window.__DATA__ = {...}``)

Pure functions, no I/O.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser


def find_json_extent(text: str, start: int) -> int | None:
    """Find the end index of a JSON object or array starting at *start*.

    Uses bracket-counting with string awareness. Returns the index
    one past the closing bracket, or None if no valid extent found.
    """
    if start >= len(text):
        return None
    opener = text[start]
    if opener == "{":
        closer = "}"
    elif opener == "[":
        closer = "]"
    else:
        return None

    depth = 0
    in_string = False
    escape = False
    i = start

    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            if in_string:
                escape = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            i += 1
            continue
        if in_string:
            i += 1
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1

    return None


def _try_parse_json(text: str) -> object | None:
    """Try to parse *text* as JSON, with light cleanup on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # Light cleanup: trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None


class _ScriptExtractor(HTMLParser):
    """HTMLParser that extracts content of ``<script id="...">``."""

    def __init__(self, target_id: str) -> None:
        super().__init__()
        self.target_id = target_id
        self.result: str | None = None
        self._capturing = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            attr_dict = dict(attrs)
            if attr_dict.get("id") == self.target_id:
                self._capturing = True

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self.result = data

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capturing:
            self._capturing = False


def extract_script_by_id(html: str, script_id: str) -> str | None:
    """Extract the text content of ``<script id="script_id">`` from HTML."""
    parser = _ScriptExtractor(script_id)
    try:
        parser.feed(html)
    except Exception:
        return None
    return parser.result


def extract_by_pattern(html: str, pattern: str) -> object | None:
    """Match *pattern* in *html*, then extract JSON starting after the match.

    The pattern should match up to (but not including) the start of the JSON.
    Uses bracket-counting to find the extent of the JSON object/array.
    """
    match = re.search(pattern, html)
    if not match:
        return None

    # Find the first { or [ after the match
    rest = html[match.end():]
    for i, ch in enumerate(rest):
        if ch in "{[":
            end = find_json_extent(rest, i)
            if end is not None:
                return _try_parse_json(rest[i:end])
            break

    return None


def extract_by_variable(html: str, variable: str) -> object | None:
    """Extract JSON from a variable assignment like ``window.__DATA__ = {...}``.

    Builds a regex pattern from the variable name and delegates to
    ``extract_by_pattern``.
    """
    # Escape the variable for regex, then build assignment pattern
    escaped = re.escape(variable)
    # Match: var/let/const/window.X = or just X =
    pattern = rf"(?:var|let|const)\s+{escaped}\s*=\s*|{escaped}\s*=\s*"
    return extract_by_pattern(html, pattern)


def parse_embedded(html: str, config: dict) -> object | None:
    """Top-level dispatcher: extract embedded JSON from *html* using *config*.

    Config keys (checked in priority order):
    - ``script_id``: extract from ``<script id="...">``
    - ``pattern``: regex pattern, extract JSON after match
    - ``variable``: variable assignment (``window.__DATA__``, etc.)

    Returns parsed JSON or None.
    """
    script_id = config.get("script_id")
    if script_id:
        content = extract_script_by_id(html, script_id)
        if content is not None:
            return _try_parse_json(content.strip())
        return None

    pattern = config.get("pattern")
    if pattern:
        return extract_by_pattern(html, pattern)

    variable = config.get("variable")
    if variable:
        return extract_by_variable(html, variable)

    return None
