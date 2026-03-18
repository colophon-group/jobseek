"""Analyze dual-language job descriptions and evaluate splitting strategies.

Reads cached descriptions from the local SQLite DB (built by desc_cache.py),
detects dual-language content, attempts splitting, and reports quality metrics.

Prerequisites:
  uv run python scripts/desc_cache.py  # populate the cache first

Usage:
  uv run python scripts/analyze_dual_lang.py                    # Full analysis
  uv run python scripts/analyze_dual_lang.py --detect-only       # Language detection only
  uv run python scripts/analyze_dual_lang.py --company roche     # Filter by company
  uv run python scripts/analyze_dual_lang.py --examples 5        # Show N example splits
  uv run python scripts/analyze_dual_lang.py --dump-failures 10  # Show N failed splits
  uv run python scripts/analyze_dual_lang.py --export results.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import textwrap
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from fast_langdetect import detect as _ft_detect
from selectolax.lexbor import LexborHTMLParser

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "desc_cache.db"

# ── Language detection helpers ──

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MIN_SEGMENT_CHARS = 30
_MIN_DETECT_SCORE = 0.35


def _strip_html(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _detect_lang(text: str) -> tuple[str | None, float]:
    """Detect language of plain text. Returns (lang, score) or (None, 0)."""
    text = text.strip()
    if len(text) < _MIN_SEGMENT_CHARS:
        return None, 0.0
    try:
        results = _ft_detect(text, model="lite")
    except Exception:
        return None, 0.0
    if results and results[0]["score"] >= _MIN_DETECT_SCORE:
        return results[0]["lang"], results[0]["score"]
    return None, 0.0


# ── HTML segment extraction ──

_BLOCK_TAGS = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "div"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


@dataclass
class Segment:
    """A block of text extracted from the HTML with its position."""

    text: str
    tag: str
    lang: str | None = None
    score: float = 0.0
    char_offset: int = 0  # position in the serialized HTML
    node_index: int = 0  # sequential index of the source node


def _extract_segments(html: str) -> list[Segment]:
    """Extract block-level text segments from HTML."""
    tree = LexborHTMLParser(html)
    body = tree.body
    if body is None:
        # Try without body wrapper
        tree = LexborHTMLParser(f"<body>{html}</body>")
        body = tree.body
        if body is None:
            return []

    segments: list[Segment] = []
    seen_offsets: set[int] = set()

    for i, node in enumerate(body.css("*")):
        tag = node.tag
        if tag not in _BLOCK_TAGS:
            continue

        # Skip if this node is a child of another block we already captured
        text = node.text(separator=" ").strip() if node.text() else ""
        if not text or len(text) < 10:
            continue

        # Deduplicate by checking if this text is a substring of a previous segment
        offset = html.find(text[:40]) if len(text) >= 40 else -1
        if offset >= 0 and offset in seen_offsets:
            continue
        if offset >= 0:
            seen_offsets.add(offset)

        seg = Segment(text=text, tag=tag, char_offset=max(offset, 0), node_index=i)
        seg.lang, seg.score = _detect_lang(text)
        segments.append(seg)

    return segments


# ── Dual-language detection ──


@dataclass
class Detection:
    classification: str  # 'mono', 'dual', 'multi', 'ambiguous', 'short'
    languages: dict[str, float]  # lang -> fraction of total chars
    segment_count: int
    total_chars: int
    primary_lang: str | None = None
    secondary_lang: str | None = None


def _detect_dual_language(segments: list[Segment]) -> Detection:
    """Classify a description's language composition."""
    if not segments:
        return Detection("short", {}, 0, 0)

    lang_chars: Counter[str] = Counter()
    total = 0
    for seg in segments:
        n = len(seg.text)
        total += n
        if seg.lang:
            lang_chars[seg.lang] += n

    if total < 50:
        return Detection("short", {}, len(segments), total)

    detected_total = sum(lang_chars.values())
    if detected_total == 0:
        return Detection("ambiguous", {}, len(segments), total)

    fractions = {lang: chars / detected_total for lang, chars in lang_chars.items()}

    # Filter out noise languages (< 5% of detected text)
    significant = {lang: frac for lang, frac in fractions.items() if frac >= 0.05}

    if len(significant) <= 1:
        primary = lang_chars.most_common(1)[0][0] if lang_chars else None
        return Detection("mono", fractions, len(segments), total, primary_lang=primary)

    if len(significant) == 2:
        langs = sorted(significant, key=lambda k: -significant[k])
        # Both must have at least 15% of text
        if significant[langs[1]] >= 0.15:
            return Detection(
                "dual",
                fractions,
                len(segments),
                total,
                primary_lang=langs[0],
                secondary_lang=langs[1],
            )
        # Otherwise it's mono with some foreign terms
        return Detection("mono", fractions, len(segments), total, primary_lang=langs[0])

    if len(significant) >= 3:
        langs = sorted(significant, key=lambda k: -significant[k])
        return Detection(
            "multi",
            fractions,
            len(segments),
            total,
            primary_lang=langs[0],
            secondary_lang=langs[1],
        )

    return Detection("ambiguous", fractions, len(segments), total)


# ── Splitting strategies ──


@dataclass
class SplitResult:
    strategy: str  # 'hr', 'heading', 'paragraph', 'failed'
    lang_a: str
    lang_b: str
    html_a: str
    html_b: str
    confidence: float  # 0-1
    split_index: int  # segment index where the split occurs
    failure_reason: str | None = None


def _try_hr_split(html: str, segments: list[Segment], det: Detection) -> SplitResult | None:
    """Strategy 1: Split on <hr> tag."""
    hr_positions = [m.start() for m in re.finditer(r"<hr\s*/?\s*>", html, re.IGNORECASE)]
    if len(hr_positions) != 1:
        # Multiple or zero HRs — not a clean signal
        return None

    hr_pos = hr_positions[0]
    html_a = html[:hr_pos].strip()
    html_b = re.sub(r"^\s*<hr\s*/?\s*>\s*", "", html[hr_pos:], flags=re.IGNORECASE).strip()

    if not html_a or not html_b:
        return None

    text_a = _strip_html(html_a)
    text_b = _strip_html(html_b)

    if len(text_a) < 50 or len(text_b) < 50:
        return None

    lang_a, score_a = _detect_lang(text_a)
    lang_b, score_b = _detect_lang(text_b)

    if not lang_a or not lang_b or lang_a == lang_b:
        return None

    # Check that these match the detected dual-language pair
    expected = {det.primary_lang, det.secondary_lang}
    actual = {lang_a, lang_b}
    if actual != expected:
        return None

    confidence = min(score_a, score_b)
    return SplitResult(
        strategy="hr",
        lang_a=lang_a,
        lang_b=lang_b,
        html_a=html_a,
        html_b=html_b,
        confidence=confidence,
        split_index=-1,
    )


def _try_heading_split(html: str, segments: list[Segment], det: Detection) -> SplitResult | None:
    """Strategy 2: Split at a heading where language changes."""
    if len(segments) < 4:
        return None

    # Find heading segments that are in the secondary language
    # and appear after a block of primary language text
    primary = det.primary_lang
    secondary = det.secondary_lang
    if not primary or not secondary:
        return None

    # Walk through looking for a heading in the secondary language
    # preceded by at least 2 segments in the primary language
    primary_run = 0
    best_split: int | None = None

    for i, seg in enumerate(segments):
        if seg.lang == primary:
            primary_run += 1
        elif seg.lang == secondary and seg.tag in _HEADING_TAGS and primary_run >= 2:
            # Check that remaining segments are mostly in the secondary language
            remaining = segments[i:]
            secondary_chars = sum(len(s.text) for s in remaining if s.lang == secondary)
            total_chars = sum(len(s.text) for s in remaining)
            if total_chars > 0 and secondary_chars / total_chars >= 0.70:
                best_split = i
                break

    if best_split is None:
        return None

    # Reconstruct HTML halves by finding the heading's position in the HTML
    split_seg = segments[best_split]
    # Find the heading tag in the HTML near the expected position
    heading_pattern = re.compile(
        rf"<{split_seg.tag}[^>]*>\s*{re.escape(split_seg.text[:30])}",
        re.IGNORECASE,
    )
    match = heading_pattern.search(html)
    if not match:
        return None

    html_a = html[: match.start()].strip()
    html_b = html[match.start() :].strip()

    if not html_a or not html_b:
        return None

    text_a = _strip_html(html_a)
    text_b = _strip_html(html_b)
    if len(text_a) < 50 or len(text_b) < 50:
        return None

    lang_a, score_a = _detect_lang(text_a)
    lang_b, score_b = _detect_lang(text_b)

    if not lang_a or not lang_b or lang_a == lang_b:
        return None
    if {lang_a, lang_b} != {primary, secondary}:
        return None

    confidence = min(score_a, score_b) * 0.9  # Slightly lower confidence than HR
    return SplitResult(
        strategy="heading",
        lang_a=lang_a,
        lang_b=lang_b,
        html_a=html_a,
        html_b=html_b,
        confidence=confidence,
        split_index=best_split,
    )


def _try_paragraph_split(html: str, segments: list[Segment], det: Detection) -> SplitResult | None:
    """Strategy 3: Split at the paragraph-level language transition point."""
    if len(segments) < 4:
        return None

    primary = det.primary_lang
    secondary = det.secondary_lang
    if not primary or not secondary:
        return None

    # Find transition: primary segments followed by a run of secondary segments
    best_split: int | None = None
    best_secondary_run = 0

    for i in range(1, len(segments)):
        seg = segments[i]
        if seg.lang != secondary:
            continue

        # Count how many consecutive secondary-language segments follow
        run = 0
        for j in range(i, len(segments)):
            if segments[j].lang == secondary:
                run += 1
            elif segments[j].lang == primary:
                break  # switched back — not a clean transition
            # else: None/other — continue counting

        # Check that segments before i are mostly primary
        before = segments[:i]
        primary_before = sum(1 for s in before if s.lang == primary)
        if len(before) > 0 and primary_before / len(before) < 0.6:
            continue

        # We want the transition that gives the longest clean secondary run
        if run >= 3 and run > best_secondary_run:
            # Verify the secondary run goes to (near) the end
            remaining_after_run = segments[i + run :]
            primary_after = sum(1 for s in remaining_after_run if s.lang == primary)
            if primary_after <= 1:  # Allow at most 1 stray primary segment after
                best_split = i
                best_secondary_run = run

    if best_split is None:
        return None

    # Find the HTML position of the split segment
    split_seg = segments[best_split]
    # Search for the segment's text in the HTML
    escaped = re.escape(split_seg.text[:40])
    pattern = re.compile(rf"<[^>]*>\s*{escaped}", re.IGNORECASE)
    match = pattern.search(html)
    if not match:
        # Try finding just the text
        text_match = html.find(split_seg.text[:40])
        if text_match < 0:
            return None
        # Walk back to find the enclosing tag
        tag_start = html.rfind("<", 0, text_match)
        if tag_start < 0:
            return None
        split_pos = tag_start
    else:
        split_pos = match.start()

    html_a = html[:split_pos].strip()
    html_b = html[split_pos:].strip()

    if not html_a or not html_b:
        return None

    text_a = _strip_html(html_a)
    text_b = _strip_html(html_b)
    if len(text_a) < 50 or len(text_b) < 50:
        return None

    lang_a, score_a = _detect_lang(text_a)
    lang_b, score_b = _detect_lang(text_b)

    if not lang_a or not lang_b or lang_a == lang_b:
        return None
    if {lang_a, lang_b} != {primary, secondary}:
        return None

    confidence = min(score_a, score_b) * 0.8  # Lowest confidence strategy
    return SplitResult(
        strategy="paragraph",
        lang_a=lang_a,
        lang_b=lang_b,
        html_a=html_a,
        html_b=html_b,
        confidence=confidence,
        split_index=best_split,
    )


def _try_split(html: str, segments: list[Segment], det: Detection) -> SplitResult:
    """Try all splitting strategies in order of reliability."""
    for strategy_fn in [_try_hr_split, _try_heading_split, _try_paragraph_split]:
        result = strategy_fn(html, segments, det)
        if result is not None:
            return result

    return SplitResult(
        strategy="failed",
        lang_a="",
        lang_b="",
        html_a="",
        html_b="",
        confidence=0.0,
        split_index=-1,
        failure_reason="no strategy succeeded",
    )


# ── Quality evaluation ──


@dataclass
class QualityMetrics:
    purity_a: float = 0.0  # fraction of half-A segments in lang_a
    purity_b: float = 0.0
    size_ratio: float = 0.0  # len(larger) / len(smaller)
    structural_ok: bool = True  # both halves start with reasonable tag


def _evaluate_split(split: SplitResult) -> QualityMetrics:
    """Evaluate the quality of a split result."""
    if split.strategy == "failed":
        return QualityMetrics()

    segs_a = _extract_segments(split.html_a)
    segs_b = _extract_segments(split.html_b)

    # Purity: fraction of segments in the expected language
    def purity(segs: list[Segment], expected_lang: str) -> float:
        if not segs:
            return 0.0
        chars_expected = sum(len(s.text) for s in segs if s.lang == expected_lang)
        chars_total = sum(len(s.text) for s in segs if s.lang is not None)
        return chars_expected / chars_total if chars_total > 0 else 0.0

    p_a = purity(segs_a, split.lang_a)
    p_b = purity(segs_b, split.lang_b)

    # Size ratio
    len_a = len(_strip_html(split.html_a))
    len_b = len(_strip_html(split.html_b))
    size_ratio = max(len_a, len_b) / min(len_a, len_b) if min(len_a, len_b) > 0 else float("inf")

    # Structural check: neither half should start with a dangling list item
    structural = True
    for h in [split.html_a, split.html_b]:
        stripped = h.lstrip()
        if stripped.startswith("<li"):
            structural = False

    return QualityMetrics(
        purity_a=p_a,
        purity_b=p_b,
        size_ratio=size_ratio,
        structural_ok=structural,
    )


# ── Title analysis ──


def _analyze_title(titles_json: str) -> dict:
    """Check if titles array contains multiple languages."""
    titles = json.loads(titles_json)
    if len(titles) <= 1:
        return {"dual_title": False, "title_langs": []}

    langs = []
    for t in titles:
        lang, _ = _detect_lang(t)
        langs.append(lang)

    unique = {x for x in langs if x is not None}
    return {"dual_title": len(unique) > 1, "title_langs": langs}


# ── Main analysis ──


@dataclass
class PostingAnalysis:
    posting_id: str
    company_slug: str
    detection: Detection
    split: SplitResult | None = None
    quality: QualityMetrics | None = None
    title_info: dict = field(default_factory=dict)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze dual-language job descriptions")
    p.add_argument("--company", action="append", default=[], help="Filter by company slug(s)")
    p.add_argument("--limit", type=int, default=0, help="Max descriptions to analyze")
    p.add_argument("--detect-only", action="store_true", help="Only run detection, skip splitting")
    p.add_argument("--examples", type=int, default=3, help="Number of example splits to show")
    p.add_argument("--dump-failures", type=int, default=0, help="Show N failed split cases")
    p.add_argument("--export", type=str, default=None, help="Export results to JSON file")
    p.add_argument(
        "--min-confidence", type=float, default=0.0, help="Min split confidence to report"
    )
    return p.parse_args()


def _run_analysis(args: argparse.Namespace) -> list[PostingAnalysis]:
    if not DB_PATH.exists():
        print(f"Cache not found: {DB_PATH}")
        print("Run: uv run python scripts/desc_cache.py")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))

    company_filter = ""
    if args.company:
        slugs = ", ".join(f"'{c}'" for c in args.company)
        company_filter = f"AND p.company_slug IN ({slugs})"

    limit_clause = f"LIMIT {args.limit}" if args.limit > 0 else ""

    rows = conn.execute(
        f"""SELECT p.id, p.company_slug, p.locales, p.titles, d.html
            FROM posting p
            JOIN description d ON d.posting_id = p.id
            WHERE 1=1 {company_filter}
            ORDER BY p.company_slug
            {limit_clause}"""
    ).fetchall()
    conn.close()

    if not rows:
        print("No descriptions found in cache.")
        sys.exit(1)

    print(f"Analyzing {len(rows):,} descriptions...\n")

    results: list[PostingAnalysis] = []
    t0 = time.monotonic()

    for i, (pid, company, _locales_json, titles_json, html) in enumerate(rows):
        if (i + 1) % 1000 == 0:
            elapsed = time.monotonic() - t0
            rate = (i + 1) / elapsed
            print(f"  {i + 1:,}/{len(rows):,}  ({rate:.0f}/s)")

        segments = _extract_segments(html)
        detection = _detect_dual_language(segments)
        title_info = _analyze_title(titles_json)

        analysis = PostingAnalysis(
            posting_id=pid,
            company_slug=company,
            detection=detection,
            title_info=title_info,
        )

        if not args.detect_only and detection.classification == "dual":
            split = _try_split(html, segments, detection)
            analysis.split = split
            if split.strategy != "failed":
                analysis.quality = _evaluate_split(split)

        results.append(analysis)

    return results


def _print_report(results: list[PostingAnalysis], args: argparse.Namespace) -> None:
    total = len(results)
    class_counts = Counter(r.detection.classification for r in results)

    print(f"\n{'=' * 70}")
    print("DUAL-LANGUAGE DESCRIPTION ANALYSIS")
    print(f"{'=' * 70}")
    print(f"\nTotal descriptions analyzed: {total:,}\n")
    for cls in ["mono", "dual", "multi", "ambiguous", "short"]:
        n = class_counts.get(cls, 0)
        pct = n / total * 100 if total else 0
        print(f"  {cls:<12s} {n:>7,} ({pct:5.1f}%)")

    # ── Dual-language breakdown ──
    dual = [r for r in results if r.detection.classification == "dual"]
    if not dual:
        print("\nNo dual-language descriptions found.")
        return

    print(f"\n{'─' * 70}")
    print(f"DUAL-LANGUAGE BREAKDOWN ({len(dual):,} postings)")
    print(f"{'─' * 70}")

    # Language pairs
    pair_counts: Counter[str] = Counter()
    for r in dual:
        d = r.detection
        pair = "+".join(sorted([d.primary_lang or "?", d.secondary_lang or "?"]))
        pair_counts[pair] += 1

    print("\nLanguage pairs:")
    for pair, count in pair_counts.most_common(15):
        pct = count / len(dual) * 100
        print(f"  {pair:<12s} {count:>6,} ({pct:5.1f}%)")

    # Companies with most dual-lang
    company_dual: Counter[str] = Counter(r.company_slug for r in dual)
    print("\nCompanies with most dual-language postings:")
    for slug, count in company_dual.most_common(20):
        total_for_company = sum(1 for r in results if r.company_slug == slug)
        pct = count / total_for_company * 100 if total_for_company else 0
        print(f"  {slug:<30s} {count:>5,} / {total_for_company:>5,} ({pct:5.1f}%)")

    # Dual titles
    dual_titles = sum(1 for r in dual if r.title_info.get("dual_title"))
    print(f"\nDual-language titles: {dual_titles:,} / {len(dual):,}")

    # ── Splitting results ──
    if args.detect_only:
        return

    splits = [r for r in dual if r.split is not None]
    if not splits:
        return

    print(f"\n{'─' * 70}")
    print(f"SPLITTING ANALYSIS ({len(splits):,} attempts)")
    print(f"{'─' * 70}")

    strat_counts = Counter(r.split.strategy for r in splits)
    for strat in ["hr", "heading", "paragraph", "failed"]:
        n = strat_counts.get(strat, 0)
        pct = n / len(splits) * 100 if splits else 0
        print(f"  {strat:<12s} {n:>6,} ({pct:5.1f}%)")

    successful = [r for r in splits if r.split.strategy != "failed"]
    if not successful:
        print("\nNo successful splits.")
        return

    # Quality metrics
    purities_a = [r.quality.purity_a for r in successful if r.quality]
    purities_b = [r.quality.purity_b for r in successful if r.quality]
    ratios = [r.quality.size_ratio for r in successful if r.quality and r.quality.size_ratio < 100]
    structural = sum(1 for r in successful if r.quality and r.quality.structural_ok)
    confidences = [r.split.confidence for r in successful]

    print(f"\nQuality metrics (N={len(successful):,} successful splits):")
    if purities_a:
        avg_purity = (sum(purities_a) + sum(purities_b)) / (len(purities_a) + len(purities_b))
        print(f"  Avg language purity:    {avg_purity:.3f}")
        low_purity = sum(1 for p in purities_a + purities_b if p < 0.85)
        low_pct = low_purity / (2 * len(successful)) * 100
        print(f"  Low purity (<0.85):     {low_purity} halves ({low_pct:.1f}%)")
    if ratios:
        avg_ratio = sum(ratios) / len(ratios)
        print(f"  Avg size ratio:         {avg_ratio:.2f}x")
        lopsided = sum(1 for r in ratios if r > 3.0)
        print(f"  Lopsided (>3x):         {lopsided} ({lopsided / len(ratios) * 100:.1f}%)")
    struct_pct = structural / len(successful) * 100
    print(f"  Structural integrity:   {structural}/{len(successful)} ({struct_pct:.1f}%)")
    if confidences:
        avg_conf = sum(confidences) / len(confidences)
        print(f"  Avg confidence:         {avg_conf:.3f}")

    # ── Per-strategy quality ──
    print("\nPer-strategy quality:")
    for strat in ["hr", "heading", "paragraph"]:
        strat_results = [r for r in successful if r.split.strategy == strat and r.quality]
        if not strat_results:
            continue
        pa = [r.quality.purity_a for r in strat_results]
        pb = [r.quality.purity_b for r in strat_results]
        avg_p = (sum(pa) + sum(pb)) / (len(pa) + len(pb))
        sr = [r.quality.size_ratio for r in strat_results if r.quality.size_ratio < 100]
        avg_r = sum(sr) / len(sr) if sr else 0
        avg_c = sum(r.split.confidence for r in strat_results) / len(strat_results)
        print(
            f"  {strat:<12s}  N={len(strat_results):>5,}  "
            f"purity={avg_p:.3f}  ratio={avg_r:.2f}x  conf={avg_c:.3f}"
        )

    # ── Failure mode analysis ──
    failed = [r for r in splits if r.split.strategy == "failed"]
    if failed:
        print(f"\n{'─' * 70}")
        print(f"FAILURE MODES ({len(failed):,} unsplittable dual-language descriptions)")
        print(f"{'─' * 70}")

        # Analyze why splitting failed
        failure_companies: Counter[str] = Counter(r.company_slug for r in failed)
        print("\nCompanies with most split failures:")
        for slug, count in failure_companies.most_common(10):
            print(f"  {slug:<30s} {count:>5,}")

        failure_pairs: Counter[str] = Counter()
        for r in failed:
            d = r.detection
            pair = "+".join(sorted([d.primary_lang or "?", d.secondary_lang or "?"]))
            failure_pairs[pair] += 1
        print("\nFailed language pairs:")
        for pair, count in failure_pairs.most_common(10):
            print(f"  {pair:<12s} {count:>5,}")

    # ── Example splits ──
    if args.examples > 0 and successful:
        print(f"\n{'─' * 70}")
        print(f"EXAMPLE SPLITS (showing {min(args.examples, len(successful))})")
        print(f"{'─' * 70}")

        # Show diverse examples: one per strategy if possible
        shown = set()
        for strat in ["hr", "heading", "paragraph"]:
            for r in successful:
                if r.split.strategy == strat and r.posting_id not in shown:
                    _print_example(r)
                    shown.add(r.posting_id)
                    if len(shown) >= args.examples:
                        break
            if len(shown) >= args.examples:
                break

        # Fill remaining with highest confidence
        for r in sorted(successful, key=lambda x: -x.split.confidence):
            if r.posting_id not in shown and len(shown) < args.examples:
                _print_example(r)
                shown.add(r.posting_id)

    # ── Dump failures ──
    if args.dump_failures > 0 and failed:
        print(f"\n{'─' * 70}")
        print(f"FAILED SPLIT EXAMPLES (showing {min(args.dump_failures, len(failed))})")
        print(f"{'─' * 70}")

        for r in failed[: args.dump_failures]:
            _print_failure(r)


def _print_example(r: PostingAnalysis) -> None:
    s = r.split
    q = r.quality
    print(f"\n  [{s.strategy}] {r.company_slug} — {r.posting_id[:8]}...")
    print(
        f"  Languages: {s.lang_a} + {s.lang_b}  "
        f"Confidence: {s.confidence:.3f}  "
        f"Purity: {q.purity_a:.2f}/{q.purity_b:.2f}  "
        f"Ratio: {q.size_ratio:.1f}x"
    )

    text_a = _strip_html(s.html_a)
    text_b = _strip_html(s.html_b)
    print(f"  Half A ({s.lang_a}, {len(text_a)} chars):")
    print(textwrap.indent(textwrap.shorten(text_a, 200, placeholder="..."), "    "))
    print(f"  Half B ({s.lang_b}, {len(text_b)} chars):")
    print(textwrap.indent(textwrap.shorten(text_b, 200, placeholder="..."), "    "))


def _print_failure(r: PostingAnalysis) -> None:
    d = r.detection
    print(f"\n  {r.company_slug} — {r.posting_id[:8]}...")
    print(
        f"  Languages: {d.primary_lang}({d.languages.get(d.primary_lang, 0):.0%}) + "
        f"{d.secondary_lang}({d.languages.get(d.secondary_lang, 0):.0%})  "
        f"Segments: {d.segment_count}"
    )

    # Show segment-level language map
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT html FROM description WHERE posting_id = ?", (r.posting_id,)
    ).fetchone()
    conn.close()
    if not row:
        return

    segments = _extract_segments(row[0])
    lang_sequence = []
    for seg in segments[:20]:
        lang = seg.lang or "?"
        tag = seg.tag
        preview = seg.text[:40].replace("\n", " ")
        lang_sequence.append(f"    {lang:>3s} [{tag:>3s}] {preview}...")

    print("  Segment languages:")
    print("\n".join(lang_sequence))
    if len(segments) > 20:
        print(f"    ... and {len(segments) - 20} more segments")


def _export_results(results: list[PostingAnalysis], path: str) -> None:
    data = []
    for r in results:
        entry = {
            "posting_id": r.posting_id,
            "company_slug": r.company_slug,
            "classification": r.detection.classification,
            "languages": r.detection.languages,
            "segment_count": r.detection.segment_count,
            "total_chars": r.detection.total_chars,
            "dual_title": r.title_info.get("dual_title", False),
        }
        if r.split:
            entry["split"] = {
                "strategy": r.split.strategy,
                "lang_a": r.split.lang_a,
                "lang_b": r.split.lang_b,
                "confidence": r.split.confidence,
            }
        if r.quality:
            entry["quality"] = {
                "purity_a": r.quality.purity_a,
                "purity_b": r.quality.purity_b,
                "size_ratio": r.quality.size_ratio,
                "structural_ok": r.quality.structural_ok,
            }
        data.append(entry)

    Path(path).write_text(json.dumps(data, indent=2, default=str))
    print(f"\nExported {len(data):,} results to {path}")


def main() -> None:
    args = _parse_args()
    results = _run_analysis(args)
    _print_report(results, args)

    if args.export:
        _export_results(results, args.export)


if __name__ == "__main__":
    main()
