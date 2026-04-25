"""Split normalized HTML into a numbered list of top-level blocks.

Blocks are the addressable unit for section labelling — a subagent picks
``block_ids`` rather than emitting character spans. Only top-level block
elements (``p``, ``ul``, ``ol``, ``h2``-``h4``, ``blockquote``) produce
blocks; inline formatting (``strong``, ``em``, ``a``, ``br``) lives inside.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from bs4 import BeautifulSoup, Tag

from .normalize import BLOCK_TAGS


@dataclass(frozen=True)
class Block:
    id: int
    tag: str
    html: str
    text: str


def extract_blocks(normalized_html: str) -> list[Block]:
    """Segment normalized HTML into blocks.

    Iterates the soup's top-level children. Bare inline content should
    already be ``<p>``-wrapped by the normalizer; anything unexpected at
    top level is ignored (not raised, not included).
    """
    if not normalized_html or not normalized_html.strip():
        return []

    soup = BeautifulSoup(normalized_html, "html.parser")
    blocks: list[Block] = []
    idx = 0
    for child in soup.children:
        if not isinstance(child, Tag):
            continue
        if child.name not in BLOCK_TAGS:
            continue
        # For list blocks, iterate top-level <li> children explicitly so
        # bullet boundaries survive as newlines while inline formatting
        # inside a bullet (<strong>, <em>, <br>) joins with spaces — the
        # naïve ``get_text(separator="\n")`` fragments mid-bullet whenever
        # a list item contains inline markup.
        if child.name in ("ul", "ol"):
            lines = [
                li.get_text(separator=" ", strip=True)
                for li in child.find_all("li", recursive=False)
            ]
            text = "\n".join(line for line in lines if line)
        else:
            text = child.get_text(separator=" ", strip=True)
        if not text:
            continue
        blocks.append(
            Block(
                id=idx,
                tag=child.name,
                html=str(child).strip(),
                text=text,
            )
        )
        idx += 1
    return blocks


def blocks_to_json(blocks: list[Block]) -> list[dict]:
    """Serialize a block list to the JSON form stored in input.json."""
    return [asdict(b) for b in blocks]
