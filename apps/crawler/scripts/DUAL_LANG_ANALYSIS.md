# Dual-Language Job Description Analysis

**Date**: 2026-03-18
**Dataset**: 15,002 descriptions from 16 companies (focus: Swiss/European multinationals)

## Executive Summary

**Splitting dual-language descriptions is not viable for production.** Only 25 out of 805
dual-language descriptions (3.1%) can be cleanly split. The vast majority follow a
"boilerplate wrapper" pattern where English corporate text surrounds local-language job
content — not two complete translations of the same job.

## Detection Results

```
Total descriptions analyzed: 15,002

  Monolingual:      13,184 (87.9%)
  Dual-language:       805 ( 5.4%)
  Multi-language:       32 ( 0.2%)
  Ambiguous:             6 ( 0.0%)
  Short (<50 chars):   975 ( 6.5%)
```

## Structural Patterns

Of the 1,509 multi-language descriptions (detected by 5-chunk position analysis):

| Pattern | Count | % | Description |
|---------|------:|--:|-------------|
| **Sandwich** (en→X→en) | 792 | 52.5% | English boilerplate wrapping local content |
| **Intro-only** (en→X→X) | ~500 | ~33% | English intro, then local content to the end |
| **True dual** (X→Y clean) | ~125 | ~8% | Full content in two languages, sequentially |
| **Interleaved** | 92 | 6.1% | Languages mixed throughout |

### Pattern 1: English Sandwich (52.5%) — NOT splittable

Dominant in: **Roche** (168), **PwC** (66), **Novartis**

Structure:
```
[English company boilerplate ~450 chars]
[Heading: "The Position" / "Die Stelle"]
[LOCAL LANGUAGE job content — 70-80% of description]
[English corporate footer ~500 chars]
```

Splitting produces one half that's just boilerplate and one that's the actual job.
This is useless — you'd lose the job content from one "half" and the other would be
meaningless platitudes.

### Pattern 2: English Intro + Local Content (33%) — NOT splittable

Same as sandwich but without the English outro. Roche alone has 243 of these.
The transition happens at ~20% of the text — a short English intro followed by the
full job description in the local language.

### Pattern 3: True Dual (8%) — Splittable but rare

Found almost exclusively in **KPMG Italy** postings. Full Italian description followed
by full English description, sometimes with a heading delimiter.

The 25 successful splits all came from this pattern:
- **Avg purity**: 0.980 (each half is 98% in one language)
- **Avg size ratio**: 1.57x (halves are reasonably balanced)
- **Structural integrity**: 92%

### Pattern 4: Interleaved (6.1%) — NOT splittable

**Julius Baer** pattern: English headings ("YOUR CHALLENGE", "YOUR PROFILE") with
German body text. No clean split point exists.

## Companies Most Affected

| Company | Dual-lang | Total | Rate | Dominant Pattern |
|---------|----------:|------:|-----:|------------------|
| Roche | 426 | 1,341 | 31.8% | Sandwich / Intro-only |
| KPMG | 151 | 3,800 | 4.0% | True dual (IT boards) |
| PwC | 137 | 6,606 | 2.1% | Sandwich |
| Novartis | 55 | 1,031 | 5.3% | Sandwich |
| Swisscom | 17 | 107 | 15.9% | Intro-only |
| Julius Baer | 12 | 528 | 2.3% | Interleaved |
| Logitech | 7 | 220 | 3.2% | Mixed |

## Language Pairs

| Pair | Count | % of dual | Primary source |
|------|------:|----------:|----------------|
| en+it | 229 | 28.4% | KPMG Italy, PwC Italy |
| de+en | 198 | 24.6% | Roche DE, KPMG CH/DE |
| en+zh | 136 | 16.9% | Roche China |
| en+es | 87 | 10.8% | Roche Spain/LATAM |
| en+fr | 69 | 8.6% | Roche France, PwC |
| en+sl | 34 | 4.2% | Novartis Slovenia |

## Splitting Quality (25 successful splits)

| Strategy | N | Avg Purity | Avg Ratio | Avg Confidence |
|----------|--:|----------:|----------:|---------------:|
| Heading | 2 | 0.996 | 1.88x | 0.833 |
| Paragraph | 23 | 0.979 | 1.54x | 0.642 |
| `<hr>` | 0 | — | — | — |

No descriptions used `<hr>` as a language separator (it's stripped by HTML normalization
or not used by ATS platforms).

## Failure Mode Analysis

**Why 780 out of 805 splits failed:**

1. **Boilerplate sandwich** (~600): English content at both start and end means
   the paragraph-level transition detector can't find a clean switch point —
   the language "switches back" to English at the end.

2. **English headings in local content** (~80): Structural headings in English
   (e.g., "YOUR CHALLENGE") within a German description create false
   language-transition signals.

3. **Too few segments** (~50): Short descriptions don't have enough paragraphs
   for reliable segment-level detection.

4. **Mixed technical terminology** (~50): English tech terms embedded in local
   language text cause individual segments to be misdetected.

## Dual-Language Titles

**0 out of 805** dual-language descriptions had dual-language titles. All titles
are single-language. This was initially hypothesized as a signal but is not present
in the data.

## Recommendations

### Do NOT implement automatic splitting

The 3.1% success rate makes it impractical. The 96.9% failure rate would require
a confidence threshold so high that almost nothing would be split.

### Instead: improve locale tagging

The real problem is that descriptions tagged `locales: ["en"]` actually contain
significant local-language content. Two better approaches:

1. **Multi-locale tagging**: Detect all languages present (even in boilerplate
   pattern) and set `locales: ["de", "en"]` so language filters don't miss these
   postings. This is a change to `detect_language()` + `_build_locales()`.

2. **Content-language detection**: Strip known boilerplate patterns (Roche/Workday
   intros/outros are templated) and detect the language of the actual job content.
   Tag with the content language as primary.

3. **Per-monitor language config**: Like Personio's `backfill_languages`, configure
   Workday boards with `language` param to fetch descriptions in the correct locale
   from the API, avoiding the boilerplate issue entirely.

## Tooling

Two scripts were created for this analysis:

### `scripts/desc_cache.py` — Local description cache

Downloads posting metadata from Postgres and description HTML from R2 into a local
SQLite database for offline analysis.

```bash
uv run python scripts/desc_cache.py                            # Index all + download
uv run python scripts/desc_cache.py --company roche             # Specific company
uv run python scripts/desc_cache.py --download-only             # Skip Postgres
uv run python scripts/desc_cache.py --stats                     # Show cache stats
uv run python scripts/desc_cache.py --active-only --limit 5000  # Active only, capped
```

SQLite DB location: `data/desc_cache.db` (gitignored).

Tables:
- `posting` — metadata (id, company_slug, locales, titles, source_url, board_slug)
- `description` — cached HTML (posting_id, locale, html, fetched_at)

### `scripts/analyze_dual_lang.py` — Language analysis

Detects dual-language content, attempts splitting, reports quality metrics.

```bash
uv run python scripts/analyze_dual_lang.py                      # Full analysis
uv run python scripts/analyze_dual_lang.py --detect-only         # Detection only
uv run python scripts/analyze_dual_lang.py --company roche       # Single company
uv run python scripts/analyze_dual_lang.py --examples 10         # More examples
uv run python scripts/analyze_dual_lang.py --dump-failures 10    # Failure details
uv run python scripts/analyze_dual_lang.py --export results.json # JSON export
```

Requires `desc_cache.py` to be run first to populate the SQLite cache.
