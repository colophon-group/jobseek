# ADR-001: Multi-Language Job Postings

Status: implemented

Date: 2026-03-10

Last updated: 2026-07-06

## Context

Some ATS platforms expose language metadata or multiple localized versions of
the same posting. Personio can fetch XML feeds by language and build localized
variants. Other sources usually provide one rendered language, with occasional
language metadata from APIs such as Greenhouse, AlmaCareer, Jobylon, or Traffit.

The March 2026 implementation plan originally lived in
`docs/multi-language-job-postings.md` as a checklist. That was useful during
the migration, but it became misleading once the schema, R2 storage, Typesense,
and language detector evolved.

## Decision

The crawler treats posting language as metadata on the extracted content, not as
a separate translation workflow.

- Monitor and scraper result objects may provide `language` and
  `localizations`.
- When no language is provided, crawler processing detects it from normalized
  description HTML using `fast-langdetect` / fastText via
  `apps/crawler/src/shared/langdetect.py`.
- `detect_language()` returns a primary ISO 639-1 code when confidence is high
  enough; `detect_all_languages()` chunks longer descriptions and returns
  significant languages that meet the configured coverage threshold.
- Processing derives `job_posting.locales[]` from the primary language,
  localization keys, and significant detected languages.
- Processing derives `job_posting.titles[]` from the primary title plus
  localized titles when a monitor provides them.
- Description HTML is staged through the R2 description pipeline for the
  selected posting locale and tracked by `description_r2_hash`.
- Typesense indexes `locales[]` for language filtering, using `["_none"]` as
  the sentinel when no language is available.

The previous plan proposed a different detector, a narrow language allowlist,
a Postgres JSONB localization column as the primary storage shape, and a
Postgres FTS phase before Typesense. Those are historical, not the implemented
state.

## Consequences

- Detection covers the broad fastText language set instead of a hand-curated
  European subset.
- Runtime throughput matters more than maximum per-language precision because
  language detection runs inside crawler processing.
- Detection is probabilistic: short or low-confidence descriptions return
  `None` / `[]`, and downstream indexing represents missing language with the
  Typesense `_none` sentinel.
- `localizations` remains an input shape for monitors that can provide variants;
  readers should not expect a live localization JSONB column on `job_posting`.
- Future language-search or locale-display changes should be tracked as GitHub
  issues, not resurrected as unchecked boxes in this ADR.

## References

- [`apps/crawler/src/shared/langdetect.py`](../../apps/crawler/src/shared/langdetect.py)
- [`apps/crawler/src/processing/cpu.py`](../../apps/crawler/src/processing/cpu.py)
- [`apps/crawler/src/processing/r2_stage.py`](../../apps/crawler/src/processing/r2_stage.py)
- [Job data fields](../08-job-data-fields.md)
- [Typesense deployment state](../11-typesense.md)
