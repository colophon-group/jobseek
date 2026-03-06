"""Language detection for job posting descriptions.

Uses lingua-py restricted to European languages for high-precision detection.
The detector is initialized once and cached for reuse across calls.
"""

from __future__ import annotations

import re
from functools import lru_cache

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

# Western, Northern + Eastern Europe; expand as scraper audience grows.
_SUPPORTED = (
    Language.ENGLISH,
    Language.GERMAN,
    Language.FRENCH,
    Language.ITALIAN,
    Language.SPANISH,
    Language.DUTCH,
    Language.PORTUGUESE,
    Language.SWEDISH,
    Language.BOKMAL,
    Language.DANISH,
    Language.FINNISH,
    Language.POLISH,
    Language.CZECH,
    Language.SLOVAK,
    Language.HUNGARIAN,
    Language.ROMANIAN,
    Language.BULGARIAN,
    Language.CROATIAN,
)

_CODE_MAP: dict[Language, str] = {
    Language.ENGLISH: "en",
    Language.GERMAN: "de",
    Language.FRENCH: "fr",
    Language.ITALIAN: "it",
    Language.SPANISH: "es",
    Language.DUTCH: "nl",
    Language.PORTUGUESE: "pt",
    Language.SWEDISH: "sv",
    Language.BOKMAL: "no",
    Language.DANISH: "da",
    Language.FINNISH: "fi",
    Language.POLISH: "pl",
    Language.CZECH: "cs",
    Language.SLOVAK: "sk",
    Language.HUNGARIAN: "hu",
    Language.ROMANIAN: "ro",
    Language.BULGARIAN: "bg",
    Language.CROATIAN: "hr",
}

_TAG_RE = re.compile(r"<[^>]+>")


@lru_cache(maxsize=1)
def _get_detector() -> LanguageDetector:
    """Build and cache a language detector restricted to supported languages."""
    return (
        LanguageDetectorBuilder.from_languages(*_SUPPORTED).with_preloaded_language_models().build()
    )


def detect_language(description: str) -> str | None:
    """Detect language from job description HTML.

    Returns an ISO 639-1 code (e.g. "en", "de") or None if detection
    is inconclusive.  Strips HTML tags before detection.
    """
    plain = _TAG_RE.sub(" ", description)[:500].strip()
    if not plain:
        return None

    result = _get_detector().detect_language_of(plain)
    return _CODE_MAP.get(result) if result else None
