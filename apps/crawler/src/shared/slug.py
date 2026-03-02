"""Slugify utility for company names."""

from __future__ import annotations

import re
import unicodedata


def slugify(name: str) -> str:
    """Convert a company name to a URL-safe slug.

    >>> slugify("McKinsey & Company")
    'mckinsey-and-company'
    """
    text = unicodedata.normalize("NFKD", name)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")
