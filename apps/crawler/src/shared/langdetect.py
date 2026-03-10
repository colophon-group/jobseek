"""Language detection for job posting descriptions.

Uses lingua-py for high-precision detection. The detector is initialized
once and cached for reuse across calls.
"""

from __future__ import annotations

import re
from functools import lru_cache

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

_SUPPORTED = (
    # Western, Northern + Eastern Europe
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
    Language.GREEK,
    Language.TURKISH,
    # CJK + Asian
    Language.JAPANESE,
    Language.CHINESE,
    Language.KOREAN,
    Language.VIETNAMESE,
    Language.THAI,
    Language.INDONESIAN,
    Language.MALAY,
    # Middle East
    Language.ARABIC,
    Language.HEBREW,
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
    Language.GREEK: "el",
    Language.TURKISH: "tr",
    Language.JAPANESE: "ja",
    Language.CHINESE: "zh",
    Language.KOREAN: "ko",
    Language.VIETNAMESE: "vi",
    Language.THAI: "th",
    Language.INDONESIAN: "id",
    Language.MALAY: "ms",
    Language.ARABIC: "ar",
    Language.HEBREW: "he",
}

_TAG_RE = re.compile(r"<[^>]+>")


@lru_cache(maxsize=1)
def _get_detector() -> LanguageDetector:
    """Build and cache a language detector restricted to supported languages."""
    return LanguageDetectorBuilder.from_languages(*_SUPPORTED).with_low_accuracy_mode().build()


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
