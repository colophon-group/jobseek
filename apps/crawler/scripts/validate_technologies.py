"""Validate technology extraction against all cached descriptions.

Reads all HTML files from data/descriptions_cache/ and runs
match_technologies against each one. Reports per-technology match
counts and sample contexts for manual review.

Usage:
    uv run python scripts/validate_technologies.py
    uv run python scripts/validate_technologies.py --show-context 5
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.technology_resolve import match_technologies  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "descriptions_cache"


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


_CONTEXT_RE_CACHE: dict[str, re.Pattern[str]] = {}


def extract_context(text: str, slug: str, window: int = 60) -> str:
    """Extract a snippet around the first match of a technology."""
    from src.core.technology_resolve import _load_patterns

    for s, pat in _load_patterns():
        if s == slug:
            m = pat.search(text)
            if m:
                start = max(0, m.start() - window)
                end = min(len(text), m.end() + window)
                snippet = text[start:end].replace("\n", " ").strip()
                return f"...{snippet}..."
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate technology extraction")
    parser.add_argument(
        "--show-context",
        type=int,
        default=3,
        help="Number of sample contexts per technology (default: 3)",
    )
    args = parser.parse_args()

    if not CACHE_DIR.exists():
        print(f"Cache directory not found: {CACHE_DIR}")
        sys.exit(1)

    files = sorted(CACHE_DIR.glob("*.html"))
    total = len(files)
    print(f"Scanning {total} descriptions from {CACHE_DIR}\n")

    counts: Counter[str] = Counter()
    contexts: dict[str, list[str]] = {}
    matched_files = 0

    for i, f in enumerate(files):
        if (i + 1) % 5000 == 0:
            print(f"  ... {i + 1}/{total}")

        html = f.read_text(encoding="utf-8", errors="replace")
        slugs = match_technologies(html)

        if slugs:
            matched_files += 1

        plain = strip_html(html) if slugs else ""
        for slug in slugs:
            counts[slug] += 1
            if slug not in contexts:
                contexts[slug] = []
            if len(contexts[slug]) < args.show_context:
                ctx = extract_context(plain, slug)
                if ctx:
                    contexts[slug].append(ctx)

    print(f"\n{'=' * 70}")
    print(f"Total files: {total}")
    print(f"Files with >= 1 match: {matched_files} ({100 * matched_files / total:.1f}%)")
    print(f"Unique technologies found: {len(counts)}")
    print(f"{'=' * 70}\n")

    # Sort by count descending
    for slug, count in counts.most_common():
        pct = 100 * count / total
        print(f"  {slug:<30s} {count:>6d}  ({pct:5.1f}%)")
        for ctx in contexts.get(slug, []):
            print(f"    > {ctx[:120]}")
        print()


if __name__ == "__main__":
    main()
