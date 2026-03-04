"""Pure HTML → structured-data extraction engine.

Flattens HTML into a list of contentful leaf elements, then walks
extraction steps to pull out named fields.  No I/O, no dependencies
beyond stdlib.
"""

from __future__ import annotations

import re
import warnings
from html import escape
from html.parser import HTMLParser

# Tags that never contain visible job content
SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "svg",
        "path",
        "meta",
        "link",
        "iframe",
        "object",
        "embed",
        "head",
        "template",
    }
)

# Tags that are structural noise (nav, footer, etc.)
NOISE_TAGS = frozenset(
    {
        "nav",
        "footer",
        "header",
    }
)

# Inline tags that don't constitute their own "block" — we fold their
# text into the parent block element.
INLINE_TAGS = frozenset(
    {
        "a",
        "abbr",
        "acronym",
        "b",
        "bdo",
        "big",
        "br",
        "button",
        "cite",
        "code",
        "dfn",
        "em",
        "i",
        "img",
        "input",
        "kbd",
        "label",
        "map",
        "mark",
        "q",
        "ruby",
        "s",
        "samp",
        "select",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "textarea",
        "time",
        "tt",
        "u",
        "var",
        "wbr",
    }
)

# Void elements that have no closing tag
VOID_TAGS = frozenset(
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


class FlattenParser(HTMLParser):
    """Parse HTML and emit flat contentful elements."""

    def __init__(self):
        super().__init__()
        self.elements: list[dict] = []
        # Stack of (tag, attrs_dict, is_skipped)
        self._stack: list[tuple[str, dict, bool]] = []
        self._skip_depth = 0  # > 0 means we're inside a skipped subtree
        self._current_text: list[str] = []
        self._current_block_tag: str | None = None
        self._current_block_attrs: dict = {}
        self._block_depth = 0

    def _flush_text(self):
        """Flush accumulated text as an element."""
        text = " ".join(self._current_text).strip()
        # Collapse whitespace
        text = " ".join(text.split())
        if text and len(text) > 0:
            self.elements.append(
                {
                    "tag": self._current_block_tag or "?",
                    "attrs": self._current_block_attrs,
                    "text": text,
                }
            )
        self._current_text = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_dict = {k: v or "" for k, v in attrs}

        # Track skip depth
        if self._skip_depth > 0:
            if tag not in VOID_TAGS:
                self._stack.append((tag, attr_dict, True))
                self._skip_depth += 1
            return

        if tag in SKIP_TAGS or tag in NOISE_TAGS:
            if tag not in VOID_TAGS:
                self._stack.append((tag, attr_dict, True))
                self._skip_depth = 1
            return

        # Check for aria-hidden or hidden attribute
        if attr_dict.get("aria-hidden") == "true" or "hidden" in attr_dict:
            if tag not in VOID_TAGS:
                self._stack.append((tag, attr_dict, True))
                self._skip_depth = 1
            return

        if tag not in VOID_TAGS:
            self._stack.append((tag, attr_dict, False))

        # If this is a block-level element, flush previous block and start new one
        if tag not in INLINE_TAGS and tag not in VOID_TAGS:
            self._flush_text()
            self._current_block_tag = tag
            self._current_block_attrs = attr_dict
            self._block_depth = len(self._stack)

    def handle_endtag(self, tag: str):
        if tag in VOID_TAGS:
            return

        # Pop stack
        if self._stack and self._stack[-1][0] == tag:
            _, _, was_skipped = self._stack.pop()
            if was_skipped:
                self._skip_depth = max(0, self._skip_depth - 1)
                return
        elif self._stack:
            # Mismatched tag — try to find it
            for i in range(len(self._stack) - 1, -1, -1):
                if self._stack[i][0] == tag:
                    while len(self._stack) > i:
                        self._stack.pop()
                    break

        if self._skip_depth > 0:
            return

        # If closing a block-level element, flush
        if tag not in INLINE_TAGS:
            self._flush_text()
            # Restore parent block context
            for s_tag, s_attrs, s_skip in reversed(self._stack):
                if not s_skip and s_tag not in INLINE_TAGS:
                    self._current_block_tag = s_tag
                    self._current_block_attrs = s_attrs
                    break
            else:
                self._current_block_tag = None
                self._current_block_attrs = {}

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        stripped = data.strip()
        if stripped:
            self._current_text.append(stripped)

    def finish(self):
        self._flush_text()


def flatten(html: str) -> list[dict]:
    """Return flat list of contentful elements from HTML."""
    parser = FlattenParser()
    parser.feed(html)
    parser.finish()
    return parser.elements


_UNICODE_PUNCT = str.maketrans(
    {
        "\u2018": "'",  # left single quote
        "\u2019": "'",  # right single quote
        "\u201c": '"',  # left double quote
        "\u201d": '"',  # right double quote
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u00a0": " ",  # non-breaking space
        "\u200b": "",  # zero-width space
        "\u200c": "",  # zero-width non-joiner
        "\u200d": "",  # zero-width joiner
        "\ufeff": "",  # BOM / zero-width no-break space
    }
)


def _norm(s: str) -> str:
    """Normalize Unicode punctuation to ASCII for matching."""
    return s.translate(_UNICODE_PUNCT).lower()


def _join_html(collected: list[dict]) -> str:
    """Join collected elements into an HTML string, preserving tag structure.

    Consecutive ``li`` elements are grouped inside ``<ul>`` tags.
    """
    parts: list[str] = []
    in_list = False
    for el in collected:
        tag = el["tag"]
        text = escape(el["text"])
        if tag == "li":
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{text}</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<{tag}>{text}</{tag}>")
    if in_list:
        parts.append("</ul>")
    return "".join(parts)


def walk_steps(
    elements: list[dict],
    steps: list[dict],
) -> dict[str, str | list[str] | None]:
    """Walk flat elements according to extraction steps, returning extracted fields.

    Every step with a ``field`` key is guaranteed present in the result: the
    extracted value when found, ``None`` when not found.

    Supported step keys:
        tag        — match by element tag name
        text       — match by substring in element text
        attr       — match by HTML attribute ("key=substring" or "key")
        field      — output field name (omit for anchor-only steps)
        offset     — skip N elements after match before extracting (default 0)
        stop       — stop collecting when element text contains this string
        stop_tag   — stop collecting when element tag matches
        stop_count — max elements to collect in a range
        optional   — if true, suppress warning when step not found
        regex      — regex with capture group; applied to extracted text
        split      — split extracted text into a list on this delimiter
        html       — if true, preserve tag structure in range output as HTML
        from       — override seek start position (e.g. 0 to search from beginning)
    """
    result: dict[str, str | list[str] | None] = {}
    cursor = 0

    for step in steps:
        tag = step.get("tag")
        text = step.get("text")
        field = step.get("field")
        stop = step.get("stop")
        stop_tag = step.get("stop_tag")
        stop_count = step.get("stop_count")
        optional = step.get("optional", False)
        attr = step.get("attr")
        regex = step.get("regex")
        split = step.get("split")
        seek_from = step.get("from")
        offset = step.get("offset", 0)
        html = step.get("html", False)

        # Ensure every field appears in the result
        if field and field not in result:
            result[field] = None

        # Determine seek start
        start = seek_from if seek_from is not None else cursor

        # Seek forward from start to matching element
        match_idx = None
        for i in range(start, len(elements)):
            el = elements[i]
            tag_match = tag is None or el["tag"] == tag
            text_match = text is None or _norm(text) in _norm(el["text"])
            attr_match = True
            if attr:
                if "=" in attr:
                    a_key, a_val = attr.split("=", 1)
                    attr_match = a_key in el["attrs"] and a_val in el["attrs"][a_key]
                else:
                    attr_match = attr in el["attrs"]
            if tag_match and text_match and attr_match:
                match_idx = i
                break

        if match_idx is None:
            if not optional:
                warnings.warn(
                    f"step {step} not found from cursor={start}",
                    stacklevel=2,
                )
            continue

        # Apply offset — skip N elements after the match
        match_idx = min(match_idx + offset, len(elements) - 1)

        is_range = stop or stop_tag or stop_count

        if field and is_range:
            # Collect elements from match, stopping on stop text / stop tag / stop count
            collected_els: list[dict] = []
            stop_idx = None
            for collected, i in enumerate(range(match_idx, len(elements))):
                # Stop-text and stop-tag checks skip the matched element itself
                if i != match_idx:
                    if stop and _norm(stop) in _norm(elements[i]["text"]):
                        stop_idx = i
                        break
                    if stop_tag and elements[i]["tag"] == stop_tag:
                        stop_idx = i
                        break
                if stop_count and collected >= stop_count:
                    stop_idx = i
                    break
                collected_els.append(elements[i])

            if html:
                value = _join_html(collected_els)
            else:
                value = "\n".join(el["text"] for el in collected_els)

            # Post-process: regex
            if regex:
                m = re.search(regex, value, re.DOTALL)
                if m:
                    value = m.group(1).strip()

            # Post-process: split
            if split:
                result[field] = [p for p in value.split(split) if p.strip()]
            else:
                result[field] = value

            cursor = stop_idx if stop_idx is not None else match_idx + len(collected_els)
        elif field:
            value = elements[match_idx]["text"]

            # Post-process: regex
            if regex:
                m = re.search(regex, value, re.DOTALL)
                if m:
                    value = m.group(1).strip()

            # Post-process: split
            if split:
                result[field] = [p for p in value.split(split) if p.strip()]
            else:
                result[field] = value

            cursor = match_idx + 1
        else:
            # Anchor step — just advance cursor
            cursor = match_idx + 1

    return result


def extract_sections(html: str, steps: list[dict]) -> dict[str, str | list[str] | None]:
    """Flatten HTML and walk extraction steps in one call."""
    return walk_steps(flatten(html), steps)
