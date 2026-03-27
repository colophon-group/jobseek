"""Technology taxonomy resolver.

Deterministic regex-based technology extraction from job descriptions.
Zero false positives — we'd rather miss a technology than report a wrong one.

Usage:
    from src.core.technology_resolve import match_technologies, load_technology_ids

    slugs = match_technologies("<p>We use Python and React...</p>")  # -> ["python", "react"]
    ids = await load_technology_ids(pool)                             # -> {"python": 1, ...}
"""

from __future__ import annotations

import functools
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    import asyncpg

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Word character regex — used to decide boundary strategy
_WORD_CHAR = re.compile(r"\w")


class _HTMLStripper(HTMLParser):
    """Minimal HTML tag stripper."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    """Strip HTML tags, return plain text."""
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


def _make_boundary_pattern(literal: str) -> str:
    """Escape a plain-text pattern and add smart word boundaries.

    Uses \\b on sides where the pattern starts/ends with a word character,
    and (?<!\\w)/(?!\\w) on sides with non-word characters (e.g. C++, .NET, C#).
    """
    escaped = re.escape(literal)
    # Leading boundary
    escaped = r"\b" + escaped if _WORD_CHAR.match(literal[0]) else r"(?<!\w)" + escaped
    # Trailing boundary
    escaped = escaped + r"\b" if _WORD_CHAR.match(literal[-1]) else escaped + r"(?!\w)"
    return escaped


@functools.cache
def _load_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """Load technologies.csv and compile regex patterns.

    All patterns in the CSV are plain text literals. They are escaped
    and wrapped with smart word boundaries in code.

    Returns list of (slug, compiled_pattern) tuples.
    """
    path = DATA_DIR / "technologies.csv"
    df = pl.read_csv(path, infer_schema_length=0)

    result: list[tuple[str, re.Pattern[str]]] = []
    for row in df.iter_rows(named=True):
        slug = row["slug"]
        raw_patterns = row.get("patterns", "")
        flags_str = row.get("flags", "") or ""

        if not raw_patterns:
            continue

        alternatives = [p.strip() for p in raw_patterns.split("|") if p.strip()]
        if not alternatives:
            continue

        parts = [_make_boundary_pattern(alt) for alt in alternatives]
        combined = "|".join(parts)
        flags = 0 if "cs" in flags_str else re.IGNORECASE
        compiled = re.compile(combined, flags)
        result.append((slug, compiled))

    return result


@functools.cache
def _load_patterns_with_keywords() -> list[tuple[str, re.Pattern[str], tuple[str, ...]]]:
    """Load patterns with lowercase keywords for fast pre-filtering.

    Returns list of (slug, compiled_pattern, keywords) tuples.
    keywords is a tuple of all lowercased literal alternatives.
    If *any* keyword is found via ``in`` on the lowercased text,
    only then is the full regex invoked.
    """
    path = DATA_DIR / "technologies.csv"
    df = pl.read_csv(path, infer_schema_length=0)
    slug_keywords: dict[str, tuple[str, ...]] = {}
    for row in df.iter_rows(named=True):
        raw = row.get("patterns", "")
        if not raw:
            continue
        alts = [p.strip().lower() for p in raw.split("|") if p.strip()]
        if alts:
            slug_keywords[row["slug"]] = tuple(alts)

    return [(slug, pattern, slug_keywords.get(slug, ())) for slug, pattern in _load_patterns()]


def match_technologies(text: str) -> list[str]:
    """Extract technology slugs from text (plain text or HTML).

    Returns a deduplicated list of matched technology slugs.
    Zero false positives by design.
    """
    if not text:
        return []

    # Strip HTML if it looks like HTML
    if "<" in text:
        text = _strip_html(text)

    text_lower = text.lower()
    matched: list[str] = []
    for slug, pattern, keywords in _load_patterns_with_keywords():
        if keywords and not any(kw in text_lower for kw in keywords):
            continue
        if pattern.search(text):
            matched.append(slug)

    return matched


async def load_technology_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Load slug -> id mapping from the technology table."""
    rows = await pool.fetch("SELECT id, slug FROM technology")
    return {row["slug"]: row["id"] for row in rows}
