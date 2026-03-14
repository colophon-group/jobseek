"""Language detection for job posting descriptions.

Uses fasttext (via fast-langdetect) for high-speed detection across 176
languages.  The compressed model (~1 MB) is loaded once on first call.
"""

from __future__ import annotations

import re

from fast_langdetect import detect

_TAG_RE = re.compile(r"<[^>]+>")

_MIN_SCORE = 0.3


def detect_language(description: str) -> str | None:
    """Detect language from job description HTML.

    Returns an ISO 639-1 code (e.g. "en", "de") or None if detection
    is inconclusive.  Strips HTML tags before detection.
    """
    plain = _TAG_RE.sub(" ", description)[:500].strip()
    if not plain:
        return None

    try:
        results = detect(plain, model="lite")
    except Exception:
        return None

    if results and results[0]["score"] >= _MIN_SCORE:
        return results[0]["lang"]
    return None
