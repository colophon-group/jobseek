"""Deterministic HTML normalization.

Takes the raw posting HTML (as stored in the ``descriptions`` table) and
produces a clean HTML subset plus a plaintext projection. Never paraphrases
or reorders — only restructures, strips non-content, and balances tags.

The normalizer is the gatekeeper for block-ID-based section labelling: its
output must be stable enough that block boundaries don't drift between runs
of the same input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from bs4 import BeautifulSoup, NavigableString, Tag

NORMALIZER_VERSION: Final = "v0.1.0"

ALLOWED_TAGS: Final = frozenset(
    {"p", "ul", "ol", "li", "h2", "h3", "h4", "strong", "em", "a", "br", "blockquote"}
)

BLOCK_TAGS: Final = frozenset({"p", "ul", "ol", "h2", "h3", "h4", "blockquote"})

INLINE_TAGS: Final = frozenset({"strong", "em", "a", "br"})

DROP_TAGS: Final = frozenset(
    {"script", "style", "noscript", "head", "meta", "link", "svg", "iframe", "object", "embed"}
)


@dataclass(frozen=True)
class Normalized:
    html: str
    text: str
    version: str


def normalize_html(raw_html: str) -> Normalized:
    """Normalize raw posting HTML to the allowed-tag subset.

    Preserves text content char-for-char modulo whitespace collapse +
    entity decoding. Disallowed tags are unwrapped (content preserved).
    """
    if not raw_html or not raw_html.strip():
        return Normalized(html="", text="", version=NORMALIZER_VERSION)

    soup = BeautifulSoup(raw_html, "html.parser")

    # 1. Drop non-content tags entirely (including their text)
    for tag in soup.find_all(list(DROP_TAGS)):
        tag.decompose()

    # 2. <h1> demoted to <h2> (postings rarely have multiple h1s meaningfully)
    for tag in soup.find_all("h1"):
        tag.name = "h2"
    # <h5>/<h6> lifted to h4 (our deepest allowed heading)
    for tag in soup.find_all(["h5", "h6"]):
        tag.name = "h4"

    # 3. Semantic normalization: <b> -> <strong>, <i> -> <em>
    for tag in soup.find_all("b"):
        tag.name = "strong"
    for tag in soup.find_all("i"):
        tag.name = "em"

    # 4. Unwrap disallowed tags (keep text). Iterate until stable — unwrapping
    # can expose previously-nested disallowed tags.
    for _ in range(20):
        disallowed = [t for t in soup.find_all(True) if t.name not in ALLOWED_TAGS]
        if not disallowed:
            break
        for tag in disallowed:
            tag.unwrap()

    # 4b. Plaintext fallback. Some postings (notably Workday exports) deliver
    # the description as raw text with newlines and no HTML block tags at all.
    # After unwrapping, the whole body becomes one naked NavigableString. Rather
    # than wrapping that into a single giant <p> (which produces a one-block
    # posting the splitter can't split), we treat double-newlines (and then
    # single-newlines as a fallback) as paragraph boundaries and emit one <p>
    # per paragraph.
    if not any(soup.find(name=bt) for bt in BLOCK_TAGS):
        flat_text = soup.get_text()
        if flat_text and flat_text.strip():
            parts = [p.strip() for p in re.split(r"\n\s*\n+", flat_text) if p.strip()]
            if len(parts) <= 1:
                # Fallback: single-newline split, filtering out very short fragments
                # so a one-word line doesn't get its own paragraph.
                parts = [p.strip() for p in flat_text.split("\n") if p.strip()]
            if len(parts) > 1:
                soup.clear()
                for part in parts:
                    p_tag = soup.new_tag("p")
                    p_tag.string = part
                    soup.append(p_tag)

    # 5. Strip attributes (keep href on <a> only)
    for tag in soup.find_all(True):
        if tag.name == "a":
            tag.attrs = {k: v for k, v in tag.attrs.items() if k == "href"}
        else:
            tag.attrs = {}

    # 6. Drop empty containers (<p></p>, <li></li>, <ul></ul> with no content)
    for _ in range(5):
        empties = [t for t in soup.find_all(BLOCK_TAGS) if not t.get_text(strip=True)]
        if not empties:
            break
        for tag in empties:
            tag.decompose()

    # 7. Wrap root-level naked text or bare inline content in <p>
    _wrap_naked_text(soup)

    # 8. Serialize + whitespace cleanup
    html = str(soup)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n\s*\n", "\n", html)
    # Add newline between block elements for readability
    for block_tag in ("p", "ul", "ol", "h2", "h3", "h4", "blockquote", "li"):
        html = html.replace(f"</{block_tag}>", f"</{block_tag}>\n")
    html = re.sub(r"\n\s*\n", "\n", html).strip()

    # 9. Plaintext projection — strip tags, preserve line breaks between blocks
    text = BeautifulSoup(html, "html.parser").get_text(separator="\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()

    return Normalized(html=html, text=text, version=NORMALIZER_VERSION)


def _wrap_naked_text(soup: BeautifulSoup) -> None:
    """Wrap contiguous root-level text / inline content in a <p> tag.

    Without this, unwrapping disallowed wrappers can leave naked
    ``NavigableString`` siblings at the root which aren't block-addressable.
    """
    buffer: list[NavigableString | Tag] = []

    def flush() -> None:
        if not buffer:
            return
        p = soup.new_tag("p")
        first = buffer[0]
        first.insert_before(p)
        for item in buffer:
            p.append(item.extract())
        buffer.clear()

    for child in list(soup.children):
        if isinstance(child, NavigableString):
            if child.strip():
                buffer.append(child)
        elif isinstance(child, Tag) and child.name in INLINE_TAGS:
            buffer.append(child)
        else:
            flush()
    flush()


def text_coverage_ratio(raw_html: str, normalized_text: str) -> float:
    """Return the ratio of normalized-text length to raw-stripped-text length.

    Invariant the caller should enforce: ratio >= 0.7. If lower, the
    normalizer likely dropped meaningful content — reject the posting
    from today's sample.
    """
    if not raw_html or not raw_html.strip():
        return 1.0
    raw_text = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ", strip=True)
    raw_len = len(re.sub(r"\s+", " ", raw_text))
    norm_len = len(re.sub(r"\s+", " ", normalized_text))
    if raw_len == 0:
        return 1.0
    return norm_len / raw_len
