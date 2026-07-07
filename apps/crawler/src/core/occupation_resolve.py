"""Occupation taxonomy resolver.

Matches free-text occupation strings (from enrichment) to taxonomy entries
in occupations.csv. Called at enrichment collection time via the taxonomy
module, and also used by backfill scripts for historical data.

Usage:
    from src.core.occupation_resolve import match_occupation, load_occupation_ids

    slug = match_occupation("Software Developer")  # -> "software-engineer"
    ids = await load_occupation_ids(pool)           # -> {"software-engineer": 1, ...}
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    import asyncpg

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_OCCUPATION_METADATA_COLUMNS = frozenset({"slug", "parent", "domain", "aliases"})


def occupation_locale_columns(columns: Sequence[str]) -> list[str]:
    """Return CSV columns that carry localized occupation display names."""
    return [column for column in columns if column not in _OCCUPATION_METADATA_COLUMNS]


def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace, strip gender markers."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    # Strip French gender suffixes: Technicien(ne), Superviseur(euse), Technicien/ne
    text = re.sub(r"\((?:ne|e|euse)\)", "", text)
    text = re.sub(r"/(?:ne|e|euse)\b", "", text)
    # Strip (m/f/d), (H/F), (H/F/X), (f/m/d), (w/m/d) etc.
    text = re.sub(r"\([hfmwdx/]+\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@functools.cache
def _load_aliases() -> dict[str, str]:
    """Read occupations.csv and build normalized_alias -> slug dict."""
    path = DATA_DIR / "occupations.csv"
    df = pl.read_csv(path, infer_schema_length=0)

    mapping: dict[str, str] = {}
    locales = occupation_locale_columns(df.columns)

    for row in df.iter_rows(named=True):
        slug = row["slug"]

        # Map slug itself
        mapping[_normalize(slug.replace("-", " "))] = slug

        # Map display names
        for locale in locales:
            name = row.get(locale)
            if name:
                mapping[_normalize(name)] = slug

        # Map aliases
        aliases_raw = row.get("aliases", "")
        if aliases_raw:
            for alias in aliases_raw.split("|"):
                alias = alias.strip()
                if alias:
                    mapping[_normalize(alias)] = slug

    return mapping


_WORD_BOUNDARY_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _word_boundary_match(alias: str, text: str) -> bool:
    """Check if alias appears in text at word boundaries."""
    if alias not in _WORD_BOUNDARY_RE_CACHE:
        _WORD_BOUNDARY_RE_CACHE[alias] = re.compile(
            r"(?:^|[\s,/\-\(])" + re.escape(alias) + r"(?:[\s,:/\-\)]|$)"
        )
    return _WORD_BOUNDARY_RE_CACHE[alias].search(text) is not None


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Minimum number of tokens for bag-of-words fallback matching.
# 3-token aliases produce false positives (e.g. {data, center, manager}
# matching "Health and Safety Manager, Data Center"). 4+ is safe.
_MIN_TOKEN_SET_SIZE = 4


@functools.cache
def _load_token_aliases() -> tuple[tuple[frozenset[str], int, str], ...]:
    """Build token-set aliases for bag-of-words fallback (4+ tokens only)."""
    aliases = _load_aliases()
    result: list[tuple[frozenset[str], int, str]] = []
    for alias, slug in aliases.items():
        tokens = frozenset(_TOKEN_RE.findall(alias))
        if len(tokens) >= _MIN_TOKEN_SET_SIZE:
            result.append((tokens, len(tokens), slug))
    # Sort by token count descending for greedy matching
    result.sort(key=lambda x: -x[1])
    return tuple(result)


def match_occupation(raw: str) -> str | None:
    """Match a raw occupation string to a taxonomy slug.

    Three-stage matching:
    1. Exact match (full normalized title == alias)
    2. Longest word-boundary substring match (alias is contiguous in title)
    3. Token-set containment (all words of a 4+ word alias appear in title)

    Returns the slug or None if no match found.
    """
    if not raw:
        return None

    aliases = _load_aliases()
    normalized = _normalize(raw)

    # Stage 1: exact match
    if normalized in aliases:
        return aliases[normalized]

    # Stage 2: longest word-boundary substring match
    best_slug: str | None = None
    best_len = 0

    for alias, slug in aliases.items():
        if len(alias) > best_len and _word_boundary_match(alias, normalized):
            best_slug = slug
            best_len = len(alias)

    if best_slug:
        return best_slug

    # Stage 3: token-set containment (4+ token aliases only)
    title_tokens = set(_TOKEN_RE.findall(normalized))
    best_token_slug: str | None = None
    best_token_count = 0

    for alias_tokens, token_count, slug in _load_token_aliases():
        if token_count > best_token_count and alias_tokens.issubset(title_tokens):
            best_token_slug = slug
            best_token_count = token_count

    return best_token_slug


async def load_occupation_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Load slug -> id mapping from the occupation table."""
    rows = await pool.fetch("SELECT id, slug FROM occupation")
    return {row["slug"]: row["id"] for row in rows}
