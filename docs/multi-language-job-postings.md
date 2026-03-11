# Multi-Language Job Postings

## Context

Some ATS platforms serve job postings in multiple languages. The same job has different titles, descriptions, and locations depending on the language. Since scraping hasn't started yet, we can redesign the schema and crawler to support this from day one.

### What ATS platforms provide

| Monitor | Language support | Mechanism |
|---------|----------------|-----------|
| **Personio** | Full | XML feed accepts `?language={lang}`. Per-job `available_languages`. Titles + descriptions differ by language. |
| **Workday** | Implicit | URL locale prefix (`/en-US/`, `/de-DE/`). Could iterate locales. |
| **Rippling** | Implicit | URL locale prefix (`/en-US/`, `/fr-FR/`). Same as Workday. |
| **API Sniffer** | Flexible | Captures/replays `lang`/`locale` params from underlying API. |
| **Greenhouse** | Metadata only | API returns `language` field per job but doesn't accept a language parameter. |
| Lever, Ashby, Workable, SmartRecruiters, Pinpoint, Recruitee, Hireology, RSS | None | Single-language API. Company picks language in ATS dashboard. |
| Sitemap, DOM, NextData | None | URL-only discovery. Language depends on the page. |
| **All scrapers** | None | Return content in whatever language the page is rendered in. |

**Personio is the only monitor that actively fetches multiple languages today.** Workday and Rippling are future candidates.

## Design

### Data model

A job posting has N language versions, all equal. One is denormalized to top-level columns for default display and filtering. The rest live in a `localizations` JSONB keyed by locale.

```
job_posting
├── title            TEXT          ← display version (best available language)
├── description      TEXT          ← display version
├── locations        TEXT[]        ← display version
├── language         TEXT          ← language of display version (detected or known)
├── localizations    JSONB         ← ALL versions keyed by locale (including display)
│   {
│     "en": {"title": "...", "description": "...", "locations": [...]},
│     "de": {"title": "...", "description": "...", "locations": [...]}
│   }
├── source_url       TEXT UNIQUE   ← one URL = one row
├── ...structured fields
└── ...lifecycle fields
```

- Default queries work unchanged: `SELECT title, description FROM job_posting`
- `language` says what the top-level fields are in
- `localizations` has every version — frontend picks user's locale, falls back to top-level
- No concept of "primary vs translation" — just N equal versions, one materialized for convenience
- Top-level is always English when available (consistent baseline). The frontend selects the user's locale from `localizations` at display time

### Schema refactor

Since no job data exists yet, we clean up dead columns and integrate language support in one pass.

```typescript
export const jobPosting = pgTable(
  "job_posting",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    companyId: uuid("company_id")
      .notNull()
      .references(() => company.id, { onDelete: "cascade" }),
    boardId: uuid("board_id").references(() => jobBoard.id, {
      onDelete: "set null",
    }),

    // ── Content (display version — best available language) ──
    title: text("title"),
    description: text("description"),
    locations: text("locations").array(),
    employmentType: text("employment_type"),
    jobLocationType: text("job_location_type"),
    baseSalary: jsonb("base_salary"),
    datePosted: timestamp("date_posted", { withTimezone: true }),

    // ── Language ──
    language: text("language"),
    localizations: jsonb("localizations"),

    // ── Extended fields (populated when available) ──
    extras: jsonb("extras"),
    // { skills, responsibilities, qualifications, validThrough, departments, ... }

    // ── Identity & lifecycle ──
    sourceUrl: text("source_url").unique().notNull(),
    status: text("status", { enum: ["active", "delisted"] })
      .default("active").notNull(),
    metadata: jsonb("metadata").default({}),
    firstSeenAt: timestamp("first_seen_at", { withTimezone: true })
      .defaultNow().notNull(),
    lastSeenAt: timestamp("last_seen_at", { withTimezone: true }),
    delistedAt: timestamp("delisted_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow().notNull(),
  },
  (table) => [
    index("idx_jp_company").on(table.companyId),
    index("idx_jp_board").on(table.boardId),
    index("idx_jp_employment_type").on(table.employmentType),
    index("idx_jp_language").on(table.language),
    index("idx_jp_status_active").on(table.status).where(sql`status = 'active'`),
    index("idx_jp_last_seen_active").on(table.lastSeenAt).where(sql`status = 'active'`),
    index("idx_jp_locations").using("gin", table.locations),
  ],
);
```

**What changed vs. current schema:**

| Change | Reason |
|--------|--------|
| Drop `latestVersionId` | Circular reference to version table. Crawler never writes it. |
| Drop `fetchMethod` | Crawler never writes it. Belongs on version table only. |
| Drop `job_posting_version` table | Crawler doesn't use it. Change tracking can be added later — YAGNI. |
| Collapse `skills`, `responsibilities`, `qualifications`, `validThrough` → `extras` JSONB | Rarely populated by monitors. Most rows would be NULL. Still queryable via `extras->'skills'` if needed. |
| Add `language` | Detected or monitor-provided language of the display content. |
| Add `localizations` | All language versions keyed by locale. |
| Enum `status` | Was free text with only 2 possible values. |
| `sourceUrl` NOT NULL | Every job must have a source URL. Was effectively required but not enforced. |
| Add `idx_jp_company`, `idx_jp_board` | Missing — needed for company/board detail pages. |
| Drop `idx_jp_skills`, `idx_jp_valid_through` | Fields moved to `extras` JSONB. |

### Language detection

Most monitors don't know what language their content is in. We detect automatically using [lingua-py](https://github.com/pemistahl/lingua-py) — highest precision, Rust-compiled bindings, deterministic, no model files.

Restricting to 18 European languages maximizes precision:

```python
# src/shared/langdetect.py
from __future__ import annotations
from functools import lru_cache
from lingua import Language, LanguageDetector, LanguageDetectorBuilder

# Western, Northern + Eastern Europe; expand as scraper audience grows.
_SUPPORTED = (
    Language.ENGLISH, Language.GERMAN, Language.FRENCH,
    Language.ITALIAN, Language.SPANISH, Language.DUTCH,
    Language.PORTUGUESE,
    Language.SWEDISH, Language.BOKMAL, Language.DANISH,
    Language.FINNISH,
    Language.POLISH, Language.CZECH, Language.SLOVAK,
    Language.HUNGARIAN, Language.ROMANIAN, Language.BULGARIAN,
    Language.CROATIAN,
)

_CODE_MAP: dict[Language, str] = {
    Language.ENGLISH: "en", Language.GERMAN: "de",
    Language.FRENCH: "fr", Language.ITALIAN: "it",
    Language.SPANISH: "es", Language.DUTCH: "nl",
    Language.PORTUGUESE: "pt", Language.SWEDISH: "sv",
    Language.BOKMAL: "no", Language.DANISH: "da",
    Language.FINNISH: "fi", Language.POLISH: "pl",
    Language.CZECH: "cs", Language.SLOVAK: "sk",
    Language.HUNGARIAN: "hu", Language.ROMANIAN: "ro",
    Language.BULGARIAN: "bg", Language.CROATIAN: "hr",
}

@lru_cache(maxsize=1)
def _get_detector() -> LanguageDetector:
    return (
        LanguageDetectorBuilder
        .from_languages(*_SUPPORTED)
        .with_preloaded_language_models()
        .build()
    )

def detect_language(description: str) -> str | None:
    """Detect language from job description HTML. Returns ISO 639-1 code or None."""
    import re
    plain = re.sub(r"<[^>]+>", " ", description)[:500].strip()
    if not plain:
        return None
    result = _get_detector().detect_language_of(plain)
    return _CODE_MAP.get(result) if result else None
```

Since we always have descriptions, detection runs on description text — long enough for near-perfect accuracy. ~2ms per call, negligible vs. HTTP time.

**Integration:** detection runs in the batch processor — the single funnel before INSERT/UPDATE. Monitors that already know the language (Personio from config, Greenhouse from API) skip detection.

### Multi-language search

Search is a separate layer on top of storage. Two phases:

**Phase 1 (MVP):** Postgres tsvector combining display + all localization text, rebuilt by trigger. Plus a generated `available_languages TEXT[]` column for language filtering.

```sql
-- tsvector for full-text search across all languages
ALTER TABLE job_posting ADD COLUMN search_tsv tsvector;
CREATE INDEX idx_jp_search ON job_posting USING GIN(search_tsv);
-- trigger rebuilds on INSERT/UPDATE of title, description, localizations

-- language filter
ALTER TABLE job_posting ADD COLUMN available_languages TEXT[]
    GENERATED ALWAYS AS (
        ARRAY[coalesce(language, 'en')]
        || coalesce(ARRAY(SELECT jsonb_object_keys(localizations)), ARRAY[]::text[])
    ) STORED;
CREATE INDEX idx_jp_available_langs ON job_posting USING GIN(available_languages);
```

**Phase 2 (scale):** Typesense or Meilisearch for typo tolerance, fuzzy matching, faceted filtering. Sync pipeline reads from `job_posting`, flattens `localizations` into per-language fields. The storage model supports both phases equally.

---

## Action Plan: Backend (schema + crawler)

### Step 1: Schema refactor

**Files:** `apps/web/src/db/schema.ts`, new Drizzle migration

- [x] Rewrite `jobPosting` table definition per schema above
- [x] Drop `job_posting_version` table
- [x] Drop stale GIN indexes (`idx_jp_skills`, `idx_jp_valid_through`)
- [x] Add `idx_jp_company`, `idx_jp_board`
- [ ] Run `pnpm db:generate` + `pnpm db:migrate`

### Step 2: Crawler data model

**Files:** `src/core/monitors/__init__.py`, `src/core/scrapers/__init__.py`

- [x] Add `language: str | None = None` and `localizations: dict | None = None` to `DiscoveredJob`
- [x] Add `language: str | None = None` to `JobContent`
- [x] Collapse `skills`, `responsibilities`, `qualifications` into an `extras: dict | None = None` field on both dataclasses
- [x] Remove `valid_through` from `JobContent` (move to extras)

### Step 3: Language detection module

**Files:** new `src/shared/langdetect.py`, `pyproject.toml`

- [x] Add `lingua-language-detector>=2.1` to dependencies
- [x] Create `src/shared/langdetect.py` with `detect_language(description) -> str | None`
- [ ] Write tests for detection across supported languages

### Step 4: Batch processor

**Files:** `src/batch.py`

- [x] Update `_INSERT_RICH_JOB` — add `language`, `localizations`, `extras` params; remove `skills`, `responsibilities`, `qualifications`
- [x] Update `_UPDATE_RELISTED_CONTENT` and `_UPDATE_JOB_CONTENT` — same column changes
- [x] Call `detect_language()` in `_process_one_board()` for rich jobs where `language is None`
- [x] Call `detect_language()` in `_process_one_scrape()` after scraping content
- [x] Serialize `localizations` and `extras` via existing `_jsonb()` helper
- [x] Call `enrich_description()` to append extras (responsibilities/qualifications/skills) to description with dedup check

### Step 5: Personio monitor — produce localizations

**Files:** `src/core/monitors/personio.py`

- [x] Refactor `_backfill_descriptions()` — instead of merging into primary job, produce `localizations` dict
- [x] Each language fetch populates `localizations[lang] = {"title": ..., "description": ..., "locations": [...]}`
- [x] Set `language = "en"` for top-level when English is available, else fall back to best-coverage language
- [x] Denormalize the English version (or best fallback) to top-level `title`/`description`/`locations`

### Step 6: Greenhouse monitor — pass through language

**Files:** `src/core/monitors/greenhouse.py`

- [x] Read `language` field from API response, set `DiscoveredJob.language`
- [x] No multi-language fetching needed

### Step 7: Update all monitors/scrapers for extras

**Files:** all monitor/scraper files that populate `skills`, `responsibilities`, `qualifications`

- [x] JSON-LD scraper: write `skills`, `responsibilities`, `qualifications`, `valid_through` to `extras` dict
- [x] api_sniffer monitor/scraper: same
- [x] Pinpoint monitor: already concatenates these into `description` HTML — verified no separate fields
- [x] SmartRecruiters: `qualifications` already folded into description — verified
- [x] d.vinci: builds description from sections (tasks/profile/weOffer) — verified compatible
- [x] Update all other scrapers (dom, embedded, nextdata) to write to `extras`

### Step 8: Update workspace commands

**Files:** `src/workspace/commands/crawl.py`, `src/workspace/_compat.py`

- [x] Update quality checks to account for `extras` instead of separate fields
- [ ] Update `ws run monitor` / `ws run scraper` output to show `language` and `localizations` summary

### Step 9: Tests

- [ ] Update `tests/test_personio.py` — verify localizations dict output
- [ ] Update `tests/test_compat.py` if monitor classification changed
- [ ] Add tests for `detect_language()` across supported languages
- [x] Update tests for batch processor with language/localizations/extras fields
- [x] Update any tests that reference `skills`, `responsibilities`, `qualifications` as top-level fields

---

## Action Plan: Frontend + Search (not yet implemented)

### Frontend: locale-aware display

**Files:** `apps/web/`

- [ ] Job detail page reads `localizations` JSONB
- [ ] Language switcher when `localizations` has multiple keys
- [ ] Auto-select based on user's Lingui locale, fall back to top-level (English)
- [ ] Job list cards: show localized title if user's locale is available

### Search: phase 1 (Postgres)

- [ ] `available_languages` generated `TEXT[]` column + GIN index for language filtering
- [ ] tsvector column + trigger combining top-level + all localization text
- [ ] GIN index on tsvector
- [ ] Search API endpoint with `plainto_tsquery` + `ts_rank`

### Search: phase 2 (external engine)

- [ ] Evaluate Typesense vs Meilisearch
- [ ] Sync pipeline: read from `job_posting`, flatten `localizations` into per-language fields
- [ ] Incremental sync via `updated_at > last_sync_at` in scheduler loop
- [ ] Query-time language boosting (user's locale fields weighted higher)
- [ ] Faceted filtering: location, company, language, employment type
