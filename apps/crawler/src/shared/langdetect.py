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


_CHUNK_SIZE = 500
_MIN_CHUNK_LEN = 80
_MIN_LANG_RATIO = 0.15


def _split_chunks(text: str, size: int = _CHUNK_SIZE) -> list[str]:
    """Split *text* into chunks of roughly *size* chars at whitespace."""
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = start + size
        if end >= length:
            chunks.append(text[start:])
            break
        # Find the last whitespace before the boundary
        ws = text.rfind(" ", start, end)
        if ws <= start:
            ws = end  # no whitespace found — hard cut
        chunks.append(text[start:ws])
        start = ws + 1
    return chunks


def detect_all_languages(description: str) -> list[str]:
    """Detect all significant languages in a job description.

    Strips HTML, splits the plain text into ~500-char chunks, runs fasttext
    on each chunk, and returns every language that covers at least 15% of
    valid chunks.  Returns ``[]`` for short or inconclusive text.
    """
    plain = _TAG_RE.sub(" ", description).strip()
    if not plain:
        return []

    chunks = _split_chunks(plain)
    valid_chunks = [c for c in chunks if len(c.strip()) >= _MIN_CHUNK_LEN]
    if not valid_chunks:
        return []

    lang_counts: dict[str, int] = {}
    for chunk in valid_chunks:
        try:
            results = detect(chunk.strip(), model="lite")
        except Exception:
            continue
        if results and results[0]["score"] >= _MIN_SCORE:
            lang = results[0]["lang"]
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

    total = len(valid_chunks)
    return [lang for lang, count in lang_counts.items() if count / total >= _MIN_LANG_RATIO]
