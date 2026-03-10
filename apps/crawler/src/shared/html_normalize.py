"""Helpers for normalizing job-description HTML across monitors and scrapers."""

from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

_DROP_SUBTREE_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "object",
        "embed",
        "svg",
        "math",
        "canvas",
        "template",
        "head",
    }
)

# Keep a compact set of semantic tags used in job descriptions.
_ALLOWED_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "strong",
        "u",
        "ul",
    }
)

_ESCAPED_HTML_TAG_RE = re.compile(
    r"&lt;\s*/?\s*(?:p|h[1-6]|ul|ol|li|a|strong|em|b|i|u|s|br|blockquote|pre|code)\b",
    re.IGNORECASE,
)


def _maybe_decode_escaped_html(text: str) -> str:
    """Decode HTML entities when the string looks like escaped HTML markup."""
    if _ESCAPED_HTML_TAG_RE.search(text):
        return unescape(text)
    return text


class _DescriptionSanitizer(HTMLParser):
    """Drop attributes and non-whitelisted tags while preserving safe structure."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._open_tags: list[str] = []
        self._skip_depth = 0

    def _append_data(self, data: str) -> None:
        if data:
            # Keep quotes literal; only escape markup delimiters.
            self._parts.append(escape(data, quote=False))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag_l = tag.lower()
        if self._skip_depth > 0:
            if tag_l not in _VOID_TAGS:
                self._skip_depth += 1
            return

        if tag_l in _DROP_SUBTREE_TAGS:
            if tag_l not in _VOID_TAGS:
                self._skip_depth = 1
            return

        if tag_l not in _ALLOWED_TAGS:
            return

        self._parts.append(f"<{tag_l}>")
        if tag_l not in _VOID_TAGS:
            self._open_tags.append(tag_l)

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if self._skip_depth > 0:
            if tag_l not in _VOID_TAGS:
                self._skip_depth -= 1
            return

        if tag_l not in _ALLOWED_TAGS or tag_l in _VOID_TAGS:
            return

        if tag_l not in self._open_tags:
            return

        # Close through the matching tag to keep output well-formed.
        while self._open_tags:
            current = self._open_tags.pop()
            self._parts.append(f"</{current}>")
            if current == tag_l:
                break

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._append_data(data)

    def handle_entityref(self, name: str) -> None:
        # ``convert_charrefs=True`` should cover this, but keep a safe fallback.
        if self._skip_depth == 0:
            self._append_data(unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        # ``convert_charrefs=True`` should cover this, but keep a safe fallback.
        if self._skip_depth == 0:
            self._append_data(unescape(f"&#{name};"))

    def get_html(self) -> str:
        while self._open_tags:
            self._parts.append(f"</{self._open_tags.pop()}>")
        return "".join(self._parts).strip()


def normalize_description_html(description: str | None) -> str | None:
    """Normalize description HTML for storage and rendering.

    - Decodes escaped markup like ``&lt;p&gt;...`` when present.
    - Drops all attributes from allowed tags.
    - Removes unsafe/non-content tags (script/style/iframe/etc.).
    - Unwraps unknown tags while preserving text and allowed children.
    """
    if description is None:
        return None

    raw = description.strip()
    if not raw:
        return None

    normalized = _maybe_decode_escaped_html(raw)
    parser = _DescriptionSanitizer()
    parser.feed(normalized)
    parser.close()
    cleaned = parser.get_html()
    return cleaned or None
