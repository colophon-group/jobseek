"""Helpers for normalizing job-description HTML across monitors and scrapers."""

from __future__ import annotations

import re
from html import unescape

from selectolax.lexbor import LexborHTMLParser

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
    tree = LexborHTMLParser(normalized)
    body = tree.body
    if body is None:
        return None

    # Phase 1: remove entire subtrees for dangerous/non-content tags.
    for tag in _DROP_SUBTREE_TAGS:
        for node in body.css(tag):
            node.decompose()

    # Phase 2: bottom-up walk — strip attributes from allowed tags, unwrap unknown.
    for node in reversed(body.css("*")):
        tag = node.tag
        if tag == "body":
            continue
        if tag in _ALLOWED_TAGS:
            # Strip all attributes (collect keys first to avoid mutation during iteration).
            for attr in list(node.attrs):
                del node.attrs[attr]
        else:
            node.unwrap()

    cleaned = body.inner_html.strip()
    if not cleaned:
        return None
    # Normalize non-breaking spaces to regular spaces to avoid spurious diffs
    # when sources alternate between &nbsp; and regular whitespace.
    cleaned = cleaned.replace("\u00a0", " ").replace("&nbsp;", " ")
    return cleaned or None
