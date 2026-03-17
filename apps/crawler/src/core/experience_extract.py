"""Heuristic extraction of years-of-experience requirements from job HTML.

Design goal: **zero false positives**.  We only return experience data when
the text unambiguously states a number of years in the context of a
professional requirement.

Returns the minimum years required.  When multiple requirements appear
(e.g. "5+ years of software development" and "3+ years of design"), we
return the *maximum* across all stated minimums — this represents the
most senior requirement the candidate must meet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Result type ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExperienceRequirement:
    min_years: int
    max_years: int | None  # None when "5+ years" (open-ended)


# ── HTML → plain text ────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def _html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


# ── Main pattern ─────────────────────────────────────────────────────
#
# Matches:
#   "5+ years of experience"
#   "3-5 years of software development experience"
#   "5 years of experience"
#   "1+ years' experience"
#   "10+ years of relevant experience"
#   "7+ years of engineering experience"
#
# The pattern requires an experience-confirming suffix to avoid matching
# things like "15 years, Amazon has been..."

# Pattern A: "N+ years of ... experience" (English and multilingual)
_EXPERIENCE_RE = re.compile(
    r"(?:at\s+least\s+|minimum\s+|mindestens\s+|au\s+moins\s+|minimo\s+)?"
    r"(\d{1,2})"  # min years
    r"\s*"
    r"(?:"
    r"[+＋]"  # "5+"
    r"|\s*[-–—]\s*(\d{1,2})"  # "3-5" → captures max
    r")?"
    r"\s*"
    r"(?:years?|Jahre?|ans?|anni?|años?|jaar|år)"  # multilingual "year(s)"
    r"[''s]*"  # "years'" or "years's"
    r"\s+"
    r"("  # capture experience context
    r"(?:of\s+|d['']\s*|di\s+|de\s+|an\s+|van\s+)?"
    r"(?:relevant\s+|professional\s+|hands[- ]on\s+|direct\s+|equivalent\s+|"
    r"progressive\s+|practical\s+|demonstrated?\s+|proven\s+|"
    r"non-internship\s+|full[- ]time\s+|industry\s+|related\s+|"
    r"minimum\s+|total\s+|combined\s+|solid\s+|"
    r"[\w]+\s+){0,3}"  # up to 3 qualifying adjectives / domain words
    r"(?:"
    # Must end with one of these experience-confirming words
    r"experience"
    r"|Erfahrung"
    r"|expérience"
    r"|esperienza"
    r"|experiencia"
    r"|ervaring"  # Dutch
    r"|erfarenhet"  # Swedish
    r"|erfaring"  # Danish/Norwegian
    r"|management"
    r"|(?:software |product |project |program )?(?:development|engineering)"
    r"|leadership"
    r"|consulting"
    r"|work(?:ing)?\s+(?:experience|in)"
    r"|Berufserfahrung"  # German compound
    r")"
    r")",
    re.IGNORECASE,
)

# Pattern B: Reversed word order (German/French/Italian)
#   "Erfahrung von mindestens 5 Jahren"
#   "expérience de 3 ans minimum"
#   "esperienza di almeno 5 anni"
_EXPERIENCE_REVERSED_RE = re.compile(
    r"(?:Erfahrung|Berufserfahrung|expérience|esperienza|experiencia|ervaring)"
    r"\s+(?:von\s+)?(?:mindestens\s+|d['']\s*(?:au\s+moins\s+)?|"
    r"di\s+(?:almeno\s+)?|de\s+(?:al\s+menos\s+)?|van\s+(?:minimaal\s+)?)?"
    r"(\d{1,2})"  # min years
    r"\s*(?:[+＋]|\s*[-–—]\s*(\d{1,2}))?"  # optional + or range
    r"\s*(?:Jahren?|ans?|anni?|años?|jaar|år)",
    re.IGNORECASE,
)

# Patterns that should NOT be matched — company history, unrelated context
_FALSE_POSITIVE_RE = re.compile(
    r"(?:has been|have been|founded|established|since\s+\d|for over|operating|"
    r"history of|track record of|we.ve been|"
    r"supports? our|in the industry|on the market|"
    r"sentence|imprisonment|prison|warranty|guarantee)",
    re.IGNORECASE,
)


def _check_match(
    text: str,
    m: re.Match,
    min_years: int,
    max_years: int | None,
) -> bool:
    """Return True if the match looks like a genuine experience requirement."""
    if min_years > 30:
        return False
    if max_years is not None and max_years > 30:
        return False
    if max_years is not None and max_years < min_years:
        return False

    # Check for false positive context in the text preceding the match
    start = max(0, m.start() - 60)
    surrounding = text[start : m.start()]
    return not _FALSE_POSITIVE_RE.search(surrounding)


def extract_experience(html: str) -> ExperienceRequirement | None:
    """Extract years-of-experience requirement from job description HTML.

    Returns the highest stated minimum across all experience mentions,
    or None if no unambiguous requirement is found.
    """
    text = _html_to_text(html)

    best_min: int | None = None
    best_max: int | None = None

    # Pattern A: "N years of ... experience"
    for m in _EXPERIENCE_RE.finditer(text):
        min_years = int(m.group(1))
        max_years = int(m.group(2)) if m.group(2) else None

        if not _check_match(text, m, min_years, max_years):
            continue

        if best_min is None or min_years > best_min:
            best_min = min_years
            best_max = max_years

    # Pattern B: reversed word order ("Erfahrung von 5 Jahren")
    for m in _EXPERIENCE_REVERSED_RE.finditer(text):
        min_years = int(m.group(1))
        max_years = int(m.group(2)) if m.group(2) else None

        if not _check_match(text, m, min_years, max_years):
            continue

        if best_min is None or min_years > best_min:
            best_min = min_years
            best_max = max_years

    if best_min is None:
        return None

    return ExperienceRequirement(min_years=best_min, max_years=best_max)
