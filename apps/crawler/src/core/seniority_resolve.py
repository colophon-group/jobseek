"""Seniority taxonomy resolver.

Best-effort seniority detection from job titles with zero false positives.
Uses prefix/suffix patterns and keyword matching against seniority.csv.

Usage:
    from src.core.seniority_resolve import match_seniority, load_seniority_ids

    slug = match_seniority("Senior Software Engineer")  # -> "senior"
    ids = await load_seniority_ids(pool)                 # -> {"senior": 1, ...}
"""

from __future__ import annotations

import functools
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    import asyncpg

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace, strip gender markers."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"\((?:ne|e|euse)\)", "", text)
    text = re.sub(r"/(?:ne|e|euse)\b", "", text)
    text = re.sub(r"\([hfmwdx/]+\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Prefix patterns ──────────────────────────────────────────────────
# These match at the START of a title. Order matters for disambiguation
# (e.g., "Sr." before general "Senior").

_PREFIX_RULES: list[tuple[re.Pattern[str], str]] = [
    # Senior / Sr
    (re.compile(r"^(?:senior|sr\.?)\s", re.IGNORECASE), "senior"),
    # Junior / Jr
    (re.compile(r"^(?:junior|jr\.?)\s", re.IGNORECASE), "entry"),
    # Principal
    (re.compile(r"^principal\s", re.IGNORECASE), "principal"),
    # Distinguished
    (re.compile(r"^distinguished\s", re.IGNORECASE), "principal"),
    # Staff (safe — almost exclusively tech roles)
    (re.compile(r"^staff\s", re.IGNORECASE), "staff"),
    # Lead — exclude "Lead Generation", "Lead Qualification"
    (re.compile(r"^lead\s(?!generat|qualif)", re.IGNORECASE), "lead"),
    # Tech Lead / Team Lead (anywhere, but as prefix phrase)
    (re.compile(r"^(?:tech|team)\s+lead\b", re.IGNORECASE), "lead"),
]

# ── "Head of" pattern ────────────────────────────────────────────────
# "Head of Engineering", "Head of Product" -> director
# But NOT "Overhead Crane Operator" etc.
_HEAD_OF_RE = re.compile(r"^head\s+of\s", re.IGNORECASE)

# ── Director / VP patterns ──────────────────────────────────────────
# Match at start. Exclude "Art Director" and "Creative Director" (mid-level roles).
_DIRECTOR_RE = re.compile(
    r"^(?:director|directeur|directrice|direktor|direttore)(?:\s+of\s+|[\s,]+)",
    re.IGNORECASE,
)
_DIRECTOR_EXCLUDE_RE = re.compile(
    r"^(?:art|creative|funeral)\s+director",
    re.IGNORECASE,
)
_VP_RE = re.compile(r"^(?:vice\s+president|vp)\s", re.IGNORECASE)

# ── Executive C-suite ────────────────────────────────────────────────
# Match C-suite titles, but exclude advisory practice names like "CIO Advisory".
_CSUITE_RE = re.compile(
    r"^(?:ceo|cto|cfo|coo|cio|ciso|cmo|cpo)\b",
    re.IGNORECASE,
)
_CSUITE_EXCLUDE_RE = re.compile(
    r"^(?:ceo|cto|cfo|coo|cio|ciso|cmo|cpo)\s+advisor",
    re.IGNORECASE,
)
_MANAGING_DIRECTOR_RE = re.compile(
    r"^(?:managing\s+director|geschaftsfuhrer|geschaftsfuhrerin|geschaeftsfuehrer)\b",
    re.IGNORECASE,
)

# ── Intern keywords ──────────────────────────────────────────────────
# These can appear anywhere in the title. Very safe — no false positives.
_INTERN_KEYWORDS_RE = re.compile(
    r"\b(?:"
    r"internship|intern\b|praktikum|praktikant|praktikantin"
    r"|werkstudent|werkstudentin|working\s+student"
    r"|stagiaire|stage\b|alternance|alternant"
    r"|apprendistato|apprentice|apprenticeship"
    r"|duales\s+studium|ausbildung|lehre|lehrling"
    r"|trainee"
    r")\b",
    re.IGNORECASE,
)
# Exclude false positives: "Google Stage 2" is not an internship
_INTERN_EXCLUDE_RE = re.compile(
    r"stage\s+\d",
    re.IGNORECASE,
)

# ── Graduate Program ─────────────────────────────────────────────────
_GRADUATE_RE = re.compile(
    r"\b(?:graduate\s+program|graduate\s+scheme|new\s+grad)\b",
    re.IGNORECASE,
)


def match_seniority(raw: str) -> str | None:
    """Detect seniority level from a job title string.

    Returns slug or None. Designed for zero false positives — we'd rather
    return None than an incorrect match.
    """
    if not raw:
        return None

    normalized = _normalize(raw)

    # 1. Prefix-based rules (most reliable)
    for pattern, slug in _PREFIX_RULES:
        if pattern.search(normalized):
            return slug

    # 2. Head of -> director
    if _HEAD_OF_RE.search(normalized):
        return "director"

    # 3. Director (exclude Art Director, Creative Director, Funeral Director)
    if _DIRECTOR_RE.search(normalized) and not _DIRECTOR_EXCLUDE_RE.search(normalized):
        return "director"

    # 4. VP
    if _VP_RE.search(normalized):
        return "director"

    # 5. C-suite executive
    if _CSUITE_RE.search(normalized) and not _CSUITE_EXCLUDE_RE.search(normalized):
        return "executive"

    # 6. Managing Director / Geschäftsführer
    if _MANAGING_DIRECTOR_RE.search(normalized):
        return "executive"

    # 7. Intern keywords (anywhere in title)
    if _INTERN_KEYWORDS_RE.search(normalized) and not _INTERN_EXCLUDE_RE.search(normalized):
        return "intern"

    # 8. Graduate program
    if _GRADUATE_RE.search(normalized):
        return "entry"

    return None


@functools.cache
def _load_seniority_slugs() -> list[str]:
    """Load seniority slugs from CSV."""
    path = DATA_DIR / "seniority.csv"
    df = pl.read_csv(path, infer_schema_length=0)
    return df["slug"].to_list()


async def load_seniority_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Load slug -> id mapping from the seniority table."""
    rows = await pool.fetch("SELECT id, slug FROM seniority")
    return {row["slug"]: row["id"] for row in rows}
